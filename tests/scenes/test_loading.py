"""MJCF 资源路径、注册资产检索和只读几何测量。"""

import tempfile
import unittest
from pathlib import Path

import mujoco
import numpy as np

from lastmile_dataflow.scenes.loading.mjcf import load_spec, save_spec


class LoadingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def assert_model_equal(self, path):
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        native = mujoco.MjSpec.from_file(str(path)).compile()
        loaded = load_spec(path).compile()
        for field in (
            "names",
            "body_pos",
            "body_quat",
            "body_mass",
            "geom_pos",
            "geom_size",
            "geom_rgba",
            "mesh_vert",
            "mesh_face",
            "jnt_type",
            "qpos0",
        ):
            np.testing.assert_array_equal(getattr(native, field), getattr(loaded, field), err_msg=field)
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_relative_mesh_and_model_directory(self):
        (self.root / "tetra.obj").write_text(
            "v 0 0 0\nv 1 0 0\nv 0 1 0\nv 0 0 1\nf 1 2 3\nf 1 4 2\nf 1 3 4\nf 2 4 3\n"
        )
        p = self.root / "model.xml"
        p.write_text(
            '<mujoco><asset><mesh file="tetra.obj"/></asset><worldbody><body><freejoint/><geom type="mesh" mesh="tetra"/></body></worldbody></mujoco>'
        )
        self.assert_model_equal(p)

    def test_attached_specs_export_resources_from_both_original_directories(self):
        specs = []
        for identity, scale in [('scene', 1), ('robot', 2)]:
            folder = self.root / identity
            folder.mkdir()
            (folder / 'mesh.obj').write_text(
                f'v 0 0 0\nv {scale} 0 0\nv 0 {scale} 0\nv 0 0 {scale}\n'
                'f 1 2 3\nf 1 4 2\nf 1 3 4\nf 2 4 3\n')
            xml = folder / 'model.xml'
            xml.write_text(f'<mujoco><asset><mesh name="{identity}" file="mesh.obj"/></asset>'
                f'<worldbody><body name="{identity}"><geom type="mesh" mesh="{identity}"/></body></worldbody></mujoco>')
            specs.append(load_spec(xml))
        specs[0].attach(specs[1], prefix='', frame=specs[0].worldbody.add_frame())
        original = specs[0].compile()
        exported = self.root / 'exported.xml'
        save_spec(specs[0], exported)
        restored = load_spec(exported).compile()
        np.testing.assert_array_equal(restored.mesh_vert, original.mesh_vert)
        np.testing.assert_array_equal(restored.body_mass, original.body_mass)
        self.assertIn(str(self.root / 'scene/mesh.obj'), exported.read_text())
        self.assertIn(str(self.root / 'robot/mesh.obj'), exported.read_text())

    def test_same_basename_includes_and_nested_resource_paths(self):
        for name, offset in [("a", 0), ("b", 2)]:
            folder = self.root / name
            folder.mkdir()
            (folder / (name + ".obj")).write_text(
                "v 0 0 0\nv 1 0 0\nv 0 1 0\nv 0 0 1\nf 1 2 3\nf 1 4 2\nf 1 3 4\nf 2 4 3\n"
            )
            (folder / "assets.xml").write_text(f'<mujoco><mesh file="{name}.obj"/></mujoco>')
        p = self.root / "model.xml"
        p.write_text(
            '<mujoco><asset><include file="a/assets.xml"/><include file="b/assets.xml"/></asset><worldbody><body><freejoint/><geom type="mesh" mesh="a"/><geom type="mesh" mesh="b" pos="2 0 0"/></body></worldbody></mujoco>'
        )
        self.assert_model_equal(p)

    def test_cycles_rejected_and_directory_override_preserved(self):
        p = self.root / "model.xml"
        p.write_text('<mujoco><include file="model.xml"/></mujoco>')
        with self.assertRaises(ValueError):
            load_spec(p)
        p.write_text(
            '<mujoco><compiler meshdir="."/><include file="extra.xml"/><worldbody><geom type="sphere" size="1"/></worldbody></mujoco>'
        )
        (self.root / "extra.xml").write_text('<mujoco><option timestep=".003"/></mujoco>')
        self.assert_model_equal(p)
