"""Read-only MJCF parsing without eager native filesystem probes per asset.

Parse text first and bind the original model directory afterwards. Included XML
is provided through a flat virtual filesystem with unique names; its resource
paths retain their original directories. Geometry and source files are unchanged.
"""

import os
from pathlib import Path
import xml.etree.ElementTree as ET

import mujoco


# Preserve native directory/strippath semantics for uncommon mixed overrides.
DIRECTORY_KEYS = {"assetdir", "meshdir", "texturedir"}
RESOURCE_KEYS = ("file", "fileleft", "fileright", "fileup", "filedown", "filefront", "fileback")


def _needs_native(element):
    return any(
        e.tag == "compiler" and (DIRECTORY_KEYS & e.attrib.keys() or e.get("strippath") == "true")
        for e in element.iter()
    )


def _expand(element, current, root_path, includes, seen):
    """Inline includes as flat virtual files; False when an included file needs native loading."""
    for child in element:
        if child.tag == "include":
            filename = child.get("file")
            if not filename or len(child):
                raise ValueError("include requires a file and no children")
            # Native include lookup first tries the root, then the including
            # directory. Resource paths within that XML use its own directory.
            candidate = Path(os.path.abspath(root_path.parent / filename))
            if not candidate.is_file():
                candidate = Path(os.path.abspath(current.parent / filename))
            info = candidate.stat()
            identity = (info.st_dev, info.st_ino)
            if identity in seen:
                raise ValueError("repeated or cyclic MJCF include: " + str(candidate))
            seen.add(identity)
            nested = ET.fromstring(candidate.read_text())
            if _needs_native(nested) or not _expand(nested, candidate, root_path, includes, seen):
                return False
            key = f"include_{len(includes):04d}.xml"
            includes[key] = ET.tostring(nested)
            child.set("file", key)
            continue
        if current != root_path:
            for key, filename in list(child.attrib.items()):
                if key in RESOURCE_KEYS and not os.path.isabs(filename):
                    child.set(key, os.path.abspath(current.parent / filename))
        if not _expand(child, current, root_path, includes, seen):
            return False
    return True


def load_spec(path):
    path = Path(os.path.abspath(path))  # Do not move a symlinked XML's resource root.
    xml = path.read_text()
    root = ET.fromstring(xml)
    includes = {}
    if any(e.tag == "include" for e in root.iter()):
        root_stat = path.stat()
        seen = {(root_stat.st_dev, root_stat.st_ino)}
        if _needs_native(root) or not _expand(root, path, path, includes, seen):
            return absolute_assets(mujoco.MjSpec.from_file(str(path)), path.parent)
        xml = ET.tostring(root, encoding="unicode")
    spec = mujoco.MjSpec.from_string(xml, include=includes or None)
    spec.modelfiledir = str(path.parent)
    return absolute_assets(spec, path.parent)


def absolute_assets(spec, root):
    """Keep each source's resource directory when specs are attached/exported."""

    def resolve(filename, directory=""):
        if not filename:
            return filename
        if spec.strippath:
            filename = os.path.basename(filename)
        return os.path.abspath(Path(root) / directory / filename)

    for mesh in spec.meshes:
        mesh.file = resolve(mesh.file, spec.compiler.meshdir)
    for texture in spec.textures:
        texture.file = resolve(texture.file, spec.compiler.texturedir)
        for index, filename in enumerate(texture.cubefiles):
            texture.cubefiles[index] = resolve(filename, spec.compiler.texturedir)
    for hfield in spec.hfields:
        hfield.file = resolve(hfield.file)
    spec.strippath = False
    return spec


def save_spec(spec, path):
    """Export reloadable MuJoCo 3.5 XML after attaching scene/robot specs."""
    xml = ET.fromstring(spec.copy().to_xml())
    for texture in xml.findall("./asset/texture"):
        if "gridlayout" in texture.attrib:
            rows, columns = map(int, texture.get("gridsize", "1 1").split())
            texture.set("gridlayout", texture.get("gridlayout")[: rows * columns])
    defaults = xml.find("default")
    if defaults is not None:
        for child in list(defaults):
            if child.tag == "default" and not child.get("class"):
                defaults.remove(child)
                defaults.extend(list(child))
    Path(path).write_text(ET.tostring(xml, encoding="unicode"))
