"""Registered THOR retrieval opens sampled assets, not every remote directory."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lastmile_dataflow.io import write_json
from lastmile_dataflow.scenes.loading.asset_library import ThorLibrary


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
            write_json(
                root / "objects/asset_id_to_object_type.json", {"Chair_1": "Chair"}
            )
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


if __name__ == "__main__":
    unittest.main()
