"""MJCF 资源路径、注册资产检索和只读几何测量。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import mujoco
import numpy as np

from lastmile_dataflow.utils.io import write_json
from lastmile_dataflow.scenes.loading.asset_library import ThorLibrary
from lastmile_dataflow.scenes.loading.mjcf import load_spec
from tests.helpers import table_scene


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


class AssetRetrievalTests(unittest.TestCase):
    def test_registered_names_do_not_scan_remote_prefabs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefabs = root / "objects/Group/Chair/Prefabs"
            source = prefabs / "Chair_1/Chair_1.xml"
            source.parent.mkdir(parents=True)
            source.write_text(
                '<mujoco><worldbody><body name="chair">'
                '<geom type="box" size=".1 .2 .3"/></body></worldbody></mujoco>'
            )
            write_json(root / "objects/asset_id_to_object_type.json", {"Chair_1": "Chair"})
            library = ThorLibrary(root / "objects")
            original = Path.iterdir

            def bounded(directory):
                if directory == prefabs:
                    raise AssertionError("do not scan all remote prefab entries")
                return original(directory)

            with patch.object(Path, "iterdir", bounded):
                catalog = library.retrieve(
                    [{"categories": ["Chair"], "size_constraints_m": {}}],
                    root / "derived",
                    limit=1,
                    seed=17,
                    registered_only=True,
                )
            self.assertEqual(list(catalog.assets), ["Chair_1"])
            self.assertTrue((root / "derived/Chair_1/asset.xml").is_file())


class AssetMeasurementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source, self.robot = table_scene(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_asset_category_retrieval_measured_z_up_without_source_changes(self):
        root = self.root / "thor"
        folder = root / "Kitchen Objects/Cup/Prefabs/Cup_fixture"
        folder.mkdir(parents=True)
        source = folder / "Cup_fixture.xml"
        source.write_text(
            '<mujoco><worldbody><body name="cup"><freejoint/><geom type="box" size=".1 .2 .15" mass="1"/></body></worldbody></mujoco>'
        )
        before = source.read_bytes()
        library = ThorLibrary(root)
        self.assertEqual(library.categories(), ["Cup"])
        assets = library.retrieve(
            [{"categories": ["Cup"], "intended_role": "obstacle", "size_constraints_m": {}}],
            self.root / "retrieval",
        )
        description = assets.describe()
        self.assertEqual(len(description), 1, description)
        self.assertAlmostEqual(description[0]["size"]["height"], 0.4)
        self.assertAlmostEqual(description[0]["size"]["depth"], 0.3)
        self.assertEqual(description[0]["root_motion"], "free")
        self.assertEqual(source.read_bytes(), before)
