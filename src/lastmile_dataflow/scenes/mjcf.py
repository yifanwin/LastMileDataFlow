"""Read-only MJCF parsing without eager native filesystem probes per asset.

Parse text first and bind the original model directory afterwards. Included XML
is provided through a flat virtual filesystem with unique names; its resource
paths retain their original directories. Geometry and source files are unchanged.
"""
import hashlib
import os
from pathlib import Path
import xml.etree.ElementTree as ET

import mujoco


def load_spec(path):
    path = Path(os.path.abspath(path))  # Do not move a symlinked XML's resource root.
    xml = path.read_text()
    root = ET.fromstring(xml)
    includes = {}
    root_stat = path.stat()
    seen = {(root_stat.st_dev, root_stat.st_ino)}
    # Preserve native directory/strippath semantics for uncommon mixed overrides.
    directory_keys = {'assetdir', 'meshdir', 'texturedir'}
    class NativeRequired(Exception):
        pass

    def expand(element, current):
        for child in element:
            if child.tag == 'compiler' and (directory_keys & child.attrib.keys()
                                            or child.get('strippath') == 'true'):
                raise NativeRequired()
            if child.tag == 'include':
                filename = child.get('file')
                if not filename or len(child):
                    raise ValueError('include requires a file and no children')
                # Native include lookup first tries the root, then the including
                # directory. Resource paths within that XML use its own directory.
                candidate = Path(os.path.abspath(path.parent/filename))
                if not candidate.is_file():
                    candidate = Path(os.path.abspath(current.parent/filename))
                info = candidate.stat()
                identity = (info.st_dev, info.st_ino)
                if identity in seen:
                    raise ValueError('repeated or cyclic MJCF include: ' + str(candidate))
                seen.add(identity)
                nested = ET.fromstring(candidate.read_text())
                expand(nested, candidate)
                key = 'include_' + hashlib.sha256(str(candidate).encode()).hexdigest() + '.xml'
                includes[key] = ET.tostring(nested)
                child.set('file', key)
            else:
                if current != path:
                    for key, filename in list(child.attrib.items()):
                        if key in ('file', 'fileleft', 'fileright', 'fileup', 'filedown', 'filefront', 'fileback'):
                            if not os.path.isabs(filename):
                                child.set(key, os.path.abspath(current.parent/filename))
                expand(child, current)

    if any(e.tag == 'include' for e in root.iter()):
        try:
            expand(root, path)
        except NativeRequired:
            return mujoco.MjSpec.from_file(str(path))
        xml = ET.tostring(root, encoding='unicode')
    spec = mujoco.MjSpec.from_string(xml, include=includes or None)
    spec.modelfiledir = str(path.parent)
    return spec
