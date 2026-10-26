"""Contact-sheet layout and pairing; synthetic fixtures are not task evidence."""
import tempfile
from contextlib import closing
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from lastmile_dataflow.data.visualization import scene_preview as preview
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.utils.io import append_row
from tests.helpers import fixtures


class ScenePreviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        source, robot = fixtures(self.root)
        with closing(Simulation.from_source(source, robot, target='target')) as sim:
            sim.freeze(self.root / 'before')
        tree = ET.parse(source.xml_path)
        body = ET.SubElement(tree.getroot().find('worldbody'), 'body',
                             name='procedural_obstacle_0', pos='2.3 0 .2')
        ET.SubElement(body, 'geom', type='box', size='.1 .1 .2')
        tree.write(source.xml_path)
        with closing(Simulation.from_source(source, robot, target='target')) as sim:
            sim.freeze(self.root / 'after')
        self.record = {
            'scene_id': 'fixture', 'status': 'accepted', 'scene': 'after',
            'proposal_intent': 'reachability',
            'task': {'target_body': 'target', 'anchor_world': [2, 0, .15],
                     'category': 'Cup', 'operation': 'pick', 'instruction': '拿起杯子'},
            'placements': [], 'stations': [{'station_id': 'S0001', 'xy': [1, 0], 'base': [1, 0, 0]}],
        }
        self.calibration = {
            'T_world_camera_optical': np.eye(4).tolist(), 'near_m': .01,
            'projection_type': 'pinhole',
            'intrinsics': {'K': [[10, 0, 320], [0, 10, 240], [0, 0, 1]]},
            'target_visible_pixels': 100,
        }

    def observer(self, *args):
        return Image.new('RGB', (preview.WIDTH, preview.HEIGHT), '#888888'), self.calibration

    def test_projection_uses_actual_intrinsics_and_ignores_behind_camera(self):
        projected = preview.project([[2, 0, 2], [0, 0, -1]], self.calibration)
        np.testing.assert_allclose(projected, [[330, 240]])

    def test_v2_edits_pair_against_task_baseline(self):
        record = {
            'scene': 'scene-files/val0-task-arm_path_collision-v000',
            'proposal_intent': 'arm_path_collision',
            'edits': {'placements': [{}], 'target_moves': []},
        }
        self.assertEqual(preview.edit_summary(record), ([{}], []))
        self.assertEqual(preview.before_scene_location(record),
                         'scene-files/val0-task/baseline')
        self.assertIsNone(preview.before_scene_location(
            {**record, 'edits': {}, 'scene': 'scene-files/val0-task/baseline'}))
        moves = {**record, 'edits': {'placements': [], 'target_moves': [{}]}}
        self.assertEqual(preview.edit_summary(moves), ([], [{}]))

    def test_single_row_and_paired_rows_reuse_camera_rigs(self):
        for edited in (False, True):
            with self.subTest(edited=edited):
                row = {**self.record}
                if edited:
                    row.update(placements=[{}], before_scene='before', before_stations=self.record['stations'])
                path = self.root / f'{edited}.png'
                with patch.object(preview, 'observer', side_effect=self.observer) as render:
                    preview.render_scene_preview(self.root, row, path)
                with Image.open(path) as image:
                    self.assertEqual(image.size, (1280, 1228 if edited else 706))
                if edited:
                    calls = render.call_args_list[-4:]
                    self.assertEqual(calls[0].args[1], calls[2].args[1])
                    self.assertEqual(calls[1].args[1], calls[3].args[1])
                    self.assertNotEqual(calls[0].args[0], calls[2].args[0])

    def test_existing_missing_baseline_is_not_reconstructed(self):
        row = {**self.record, 'placements': [{}]}
        with patch.object(preview, 'observer', side_effect=self.observer), \
             patch.object(preview.Simulation, 'from_snapshot', wraps=Simulation.from_snapshot) as load:
            preview.render_scene_preview(self.root, row, self.root / 'legacy.png')
        self.assertEqual(load.call_count, 1)
        self.assertEqual(load.call_args.args[0], self.root / 'after')

    def test_index_links_image_reports_rejection_and_keeps_records_untouched(self):
        record_file = self.root / 'scenes.jsonl'
        append_row(record_file, self.record)
        append_row(record_file, {'scene_id': 'rejected', 'status': 'rejected', 'reason': 'budget < limit'})
        append_row(record_file, {'task_id': 'skipped-task', 'status': 'skipped',
                                 'reason': 'mode_not_applicable', 'proposal_intent': 'articulated_interaction'})
        original = record_file.read_bytes()
        with patch.object(preview, 'render_scene_preview') as render:
            index = preview.export_scene_previews(self.root)
        self.assertEqual(render.call_count, 1)
        page = index.read_text()
        self.assertIn('previews/fixture.png', page)
        self.assertIn('接受意图 reachability', page)
        self.assertIn('budget &lt; limit', page)
        self.assertIn('rejected · 意图 unknown', page)
        self.assertIn('skipped-task · skipped · 意图 articulated_interaction', page)
        self.assertEqual(record_file.read_bytes(), original)

    def test_render_failure_does_not_change_build_status(self):
        append_row(self.root / 'scenes.jsonl', self.record)
        with patch.object(preview, 'render_scene_preview', side_effect=RuntimeError('GL unavailable')), \
             self.assertLogs(preview.logger, level='ERROR'):
            index = preview.export_scene_previews(self.root)
        self.assertIn('accepted', index.read_text())
        self.assertIn('GL unavailable', index.read_text())
