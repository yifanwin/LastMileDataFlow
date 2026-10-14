"""Software-only benchmark contracts; fixtures are not real VLA evidence."""
import tempfile
import unittest
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np

from helpers import make_sim
from lastmile_dataflow.data.benchmark import select_starts, summarize
from lastmile_dataflow.runtime.benchmark_scene import native_scene
from lastmile_dataflow.runtime.simulation import Simulation


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.stations = [{'station_id':'low','geometry':'valid','xy':[0.,0.]},
                         {'station_id':'high','geometry':'valid','xy':[.4,0.]},
                         {'station_id':'far','geometry':'valid','xy':[4.,0.]}]
        self.binding = {'sample_id':'sample','robot_sensor_config_id':'native'}
        self.calibration = {'schema_version':'lastmilebench-station-rates-v1','backend':'curobo',
            **self.binding,'stations':[{'station_id':'low','trials':5,'successes':0},
            {'station_id':'high','trials':5,'successes':5},{'station_id':'far','trials':5,'successes':5}]}

    def test_low_high_nearby_without_path_validator(self):
        pairs = select_starts(self.stations,self.calibration,self.binding)
        self.assertEqual([s['station_id'] for s,_ in pairs],['low'])
        self.assertEqual(pairs[0][1]['s_plus'],['high'])
        self.assertEqual(pairs[0][1]['path_validation'],'not_required_v0')

    def test_wrong_scene_or_profile_rejected(self):
        for k in self.binding:
            with self.subTest(key=k), self.assertRaisesRegex(ValueError,'binding mismatch'):
                select_starts(self.stations,{**self.calibration,k:'foreign'},self.binding)

    def test_invalid_or_duplicate_counts_rejected(self):
        for rows in ([{'station_id':'low','trials':0,'successes':0}],
                     self.calibration['stations']*2,
                     [{'station_id':'low','trials':5,'successes':0,'success_rate':1.}]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                select_starts(self.stations,{**self.calibration,'stations':rows},self.binding)

    def test_no_nearby_solution_not_an_episode(self):
        self.calibration['stations'] = [self.calibration['stations'][0],self.calibration['stations'][2]]
        self.assertEqual(select_starts(self.stations,self.calibration,self.binding),[])

    def test_smoke_is_explicitly_uncalibrated(self):
        self.assertEqual(select_starts(self.stations,None,self.binding)[0][1]['status'],'uncalibrated_smoke')

    def test_summary_full_denominator_timeout_and_unknown(self):
        episodes = [{'episode_id':str(i)} for i in range(3)]
        results = [{'episode_id':'0','status':'success'},{'episode_id':'1','status':'failure'},
                   {'episode_id':'2','status':'timeout'}]
        self.assertEqual(summarize(episodes,results)['task_success_rate'],1/3)
        results[2]['status']='infrastructure_error'
        value = summarize(episodes,results)
        self.assertIsNone(value['task_success_rate'])
        self.assertEqual(value['observed_task_success_rate'],.5)
        self.assertFalse(value['complete'])
        self.assertIsNone(summarize(episodes,[])['task_success_rate'])

    def test_duplicate_or_foreign_results_rejected(self):
        episodes = [{'episode_id':'x'}]
        for results in ([{'episode_id':'y','status':'success'}],
                        [{'episode_id':'x','status':'success'}]*2):
            with self.assertRaises(ValueError): summarize(episodes,results)


    def test_frozen_station_preserves_nonrobot_velocity(self):
        from lastmile_dataflow.scenes.sampling import place_frozen_station
        from dataclasses import asdict
        with tempfile.TemporaryDirectory() as directory:
            sim, robot = make_sim(directory)
            start = int(sim.model.jnt_dofadr[sim.model.joint('target_free').id])
            sim.data.qvel[start:start+6] = np.arange(6)*.01
            old = sim.data.qvel.copy()
            try:
                place_frozen_station(sim, {'initial':asdict(robot)['initial']})
                np.testing.assert_array_equal(sim.data.qvel[start:start+6],old[start:start+6])
                sim.begin()
                with self.assertRaises(RuntimeError):
                    place_frozen_station(sim, {'initial':asdict(robot)['initial']})
            finally:
                sim.close()

    def test_native_conversion_preserves_geometry_state_and_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            sim, robot = make_sim(directory)
            xml = ET.parse(robot.model_path)
            for element in xml.getroot().find('actuator').findall('motor'):
                element.set('ctrllimited','true'); element.set('ctrlrange','-100 100')
            xml.write(robot.model_path)
            sim.freeze(Path(directory)/'parent')
            qpos,qvel = sim.data.qpos.copy(),sim.data.qvel.copy()
            bias = sim.model.actuator_biasprm.copy()
            native = native_scene(sim)
            try:
                np.testing.assert_array_equal(native.data.qpos,qpos)
                np.testing.assert_array_equal(native.data.qvel,qvel)
                np.testing.assert_array_equal(sim.model.actuator_biasprm,bias)
                for key in ('body_pos','body_quat','body_mass','geom_pos','geom_size','jnt_range','cam_pos','cam_quat'):
                    np.testing.assert_array_equal(getattr(native.model,key),getattr(sim.model,key))
                for side in ('left','right'):
                    act = native.model.actuator('robot_0/'+side+'_finger_act')
                    np.testing.assert_array_equal(act.ctrlrange,[-100,100])
                    self.assertEqual(int(act.biastype[0]),0)
                native.freeze(Path(directory)/'native')
                restored = Simulation.from_snapshot(Path(directory)/'native',native.robot.config,target='target')
                restored.close()
                sim.begin()
                with self.assertRaises(RuntimeError): native_scene(sim)
            finally:
                native.close(); sim.close()


if __name__ == '__main__': unittest.main()
