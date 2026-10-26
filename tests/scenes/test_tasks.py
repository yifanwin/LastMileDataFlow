"""任务发现与抓取候选生成。"""

import tempfile
import unittest
from pathlib import Path

import numpy as np

from lastmile_dataflow.runtime.execution_config import ExecutionConfig
from lastmile_dataflow.scenes.tasks.discovery import discover_tasks, grasp_candidates
from tests.helpers import make_sim


class TaskTests(unittest.TestCase):
    def test_task_identity_survives_body_renumbering(self):
        import xml.etree.ElementTree as ET
        from lastmile_dataflow.runtime.simulation import Simulation
        from tests.helpers import fixtures

        with tempfile.TemporaryDirectory() as directory:
            source, robot = fixtures(directory)
            tree = ET.parse(source.xml_path)
            cabinet = ET.SubElement(tree.getroot().find('worldbody'), 'body', name='cabinet', pos='3 1 .5')
            drawer = ET.SubElement(cabinet, 'body', name='drawer')
            ET.SubElement(drawer, 'joint', name='drawer/slide', type='slide', axis='1 0 0', range='0 .4')
            ET.SubElement(drawer, 'geom', type='box', size='.2 .2 .05')
            handle = ET.SubElement(drawer, 'body', name='drawer_handle', pos='.2 0 0')
            ET.SubElement(handle, 'geom', type='box', size='.02 .05 .02')
            tree.write(source.xml_path)
            metadata = {'objects': {'cabinet': {'name_map': {'joints': {'drawer/slide': 'drawer'}}}}}
            identities, body_ids = [], []
            for edited in (False, True):
                if edited:
                    tree = ET.parse(source.xml_path)
                    tree.getroot().find('worldbody').insert(0, ET.Element('body', name='added_obstacle'))
                    tree.write(source.xml_path)
                sim = Simulation.from_source(source, robot, target='target')
                try:
                    entry = next(row for row in sim.catalog if row['instance_id'] == 'target')
                    entry.update(asset_id='fixture', category='Cup', room_id=1)
                    entry = next(row for row in sim.catalog if row['instance_id'] == 'cabinet')
                    entry.update(asset_id='drawer_fixture', category='Drawer', room_id=1)
                    tasks, _ = discover_tasks(sim, metadata, ExecutionConfig(gripper_width_m=.21))
                    identities.append(sorted(t['task_id'] for t in tasks))
                    body_ids.append(sim.model.body('target').id)
                finally:
                    sim.close()
            self.assertNotEqual(body_ids[0], body_ids[1])
            self.assertEqual(identities[0], ['open-cabinet-drawer%2Fslide', 'pick-target'])
            self.assertEqual(identities[0], identities[1])

    def test_actual_free_joint_and_dimensions_not_category_alone(self):
        with tempfile.TemporaryDirectory() as d:
            sim, robot = make_sim(d)
            sim.catalog[0].update(asset_id="fixture", category="Cup", room_id=1)
            tasks, skipped = discover_tasks(sim, {}, ExecutionConfig(gripper_width_m=0.21))
            self.assertTrue(any(t["operation"] == "pick" for t in tasks))
            sim.close()

    def test_procedural_grasps_are_proper_rotations(self):
        task = {
            "asset_id": "fixture",
            "operation": "pick",
            "target_body": "target",
            "local_bounds": [[-0.01, -0.02, -0.03], [0.01, 0.02, 0.03]],
        }
        with tempfile.TemporaryDirectory() as d:
            candidates = grasp_candidates(task, d)
            self.assertTrue(candidates)
            for row in candidates:
                self.assertAlmostEqual(np.linalg.det(row["pose_local"][:3, :3]), 1.0)


class GraspFallbackTests(unittest.TestCase):
    def test_empty_asset_grasp_array_is_explicit_procedural_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "grasps" / "droid" / "fixture"
            p.mkdir(parents=True)
            np.savez(p / "fixture_grasps_filtered.npz", transforms=np.array([]))
            task = {
                "asset_id": "fixture",
                "operation": "pick",
                "target_body": "target",
                "local_bounds": [[-0.01, -0.02, -0.03], [0.01, 0.02, 0.03]],
            }
            rows = grasp_candidates(task, d)
            self.assertTrue(rows)
            self.assertEqual(rows[0]["source"], "procedural_bbox_pinch_v1")
            self.assertEqual(len(rows[0]["empty_asset_grasp_sources"]), 1)
