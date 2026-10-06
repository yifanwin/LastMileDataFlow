import os
import tempfile
import unittest
from pathlib import Path
import numpy as np
import imageio.v2 as imageio

from case_edit_helpers import table_scene, template, proposal
from lastmile_dataflow.runtime.preparation import prepare_scene
from lastmile_dataflow.runtime.case_edit_session import CaseEditSession
from lastmile_dataflow.construction.compiler import compile_sample
from lastmile_dataflow.recording.edit_views import CameraRig, ViewConfig, render_edit_pair


class ViewTests(unittest.TestCase):
    def test_union_fit_and_rad_adapter(self):
        rig = CameraRig.fit([[0, 0, 0], [10, 0, 1]], ViewConfig(width=320, height=240))
        self.assertEqual(rig.lookat, [5., 0., .5])
        self.assertGreater(rig.distance_m, 10)
        self.assertAlmostEqual(rig.camera(1).azimuth, 45)
        self.assertLess(rig.camera(0).elevation, -89)
        with self.assertRaises(ValueError):
            CameraRig.fit([[float('nan'), 0, 0]])

    @unittest.skipUnless(os.environ.get('CASE_EDIT_RENDER_TESTS') == '1', 'opt-in actual OpenGL rendering')
    def test_real_paired_rgb_identity_and_frustum(self):
        with tempfile.TemporaryDirectory() as root:
            source, robot = table_scene(root)
            with prepare_scene(source, robot, base=[0, 0, 0]) as prepared, CaseEditSession(prepared) as session:
                p = proposal()
                exe = compile_sample(p, template(), prepared.graph)
                trial = session.execute(template(), p, exe)
                original_fovy = float(prepared.sim.model.vis.global_.fovy)
                packet = render_edit_pair(prepared.sim, session.sim, prepared.graph, trial.after_graph,
                    {'target'}, Path(root)/'rgb', sample_id=exe.sample_id,
                    config=ViewConfig(width=320, height=240, fovy_rad=1.))
                self.assertEqual(len(packet['images']), 6)
                self.assertEqual(prepared.sim.model.vis.global_.fovy, original_fovy)
                images = [imageio.imread(i['path']) for i in packet['images']]
                self.assertTrue(all(im.shape == (240, 320, 3) and np.std(im) > 1 for im in images))
                self.assertTrue(any(np.any(a != b) for a, b in zip(images[:3], images[3:])))
                self.assertEqual({i['pair_id'] for i in packet['images']}, {packet['pair_id']})
                context = render_edit_pair(prepared.sim, session.sim, prepared.graph, trial.after_graph,
                    {'target'}, Path(root)/'rgb_context', sample_id=exe.sample_id,
                    config=ViewConfig(width=320, height=240), context_nodes={'station_start', 'table'},
                    view_rotation_rad=np.pi/2)
                self.assertGreater(context['view_rigs']['top']['distance_m'],
                                   context['view_rigs']['oblique_a']['distance_m'])
                self.assertAlmostEqual(context['view_rigs']['top']['angles'][0][0], np.pi/2)
