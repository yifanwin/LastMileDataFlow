"""Mocked revalidation contract only; GPU/native acceptance recorded separately."""
from dataclasses import asdict, replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np

from lastmile_dataflow.config import RobotConfig
from lastmile_dataflow.io import read_json,write_json
from lastmile_dataflow.stations.no_edit_config import NoEditConfig
from lastmile_dataflow.stations.no_edit_sampling import disk_samples
from lastmile_dataflow.workflows.native_station_input import revalidate_native_stations


class NativeStationInputTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.parent=self.root/'parent';self.variant=self.parent/'variants/v000'
        self.native=self.root/'native';self.native.mkdir();self.variant.mkdir(parents=True)
        self.config=NoEditConfig(operation_backend='molmobot',policy_config='policy.json',
                 operation_base_mode='policy_relative',width=1024,height=576,radius_m=.8,spacing_m=.11)
        self.robot=replace(RobotConfig(str(self.root/'robot.xml')),control_profile='molmobot-native-v1',control_hz=10.)
        task={'anchor_world':[0,0,1],'task_id':'pick-test','target_body':'cup','asset_id':'Cup_30'}
        self.candidates=disk_samples(task['anchor_world'],.8,.11)
        write_json(self.parent/'baseline/task.json',task);write_json(self.parent/'baseline/stations.json',self.candidates)
        aug={'baseline_config':'old.json','dataset_dir':'dataset','asset_library':'assets','target_instance':'cup','construction':{'case':'Case1'}}
        write_json(self.parent/'frozen_config.json',{'baseline':{'radius_m':.8,'spacing_m':.11},'augmentation':aug})
        write_json(self.variant/'checks.json',{'valid':True})
        write_json(self.variant/'edits.json',[{'asset_id':'Chair_1','pose':{'position':[0,0,0],'quaternion_wxyz':[1,0,0,0]}}])
        source={'scene_id':'procthor-10k-val/val_103'}
        write_json(self.variant/'scene/version.json',{'source':source})
        write_json(self.native/'version.json',{'source':source,'robot':asdict(self.robot)})
        write_json(self.native/'rebuild_provenance.json',{'variant':str(self.variant),'placements':read_json(self.variant/'edits.json')})
        class Sim:
            def state_vector(self):return np.zeros(3)
            def close(self):pass
        self.sim=Sim()
        self.rows=[{**s,'geometry':'valid' if i<30 else 'geometry_filtered'} for i,s in enumerate(self.candidates)]

    def execute(self):
        with patch('lastmile_dataflow.workflows.native_station_input.Simulation.from_snapshot',return_value=self.sim), \
             patch('lastmile_dataflow.workflows.native_station_input.collision_pairs',return_value={'valid':True}), \
             patch('lastmile_dataflow.workflows.native_station_input.load_support',return_value={'valid':True}), \
             patch('lastmile_dataflow.workflows.native_station_input.case_construction_check',return_value={'valid':True}), \
             patch('lastmile_dataflow.workflows.native_station_input.evaluate_stations',return_value=self.rows) as evaluate:
            out=revalidate_native_stations(self.variant,self.native,self.config,self.root/'result')
            return out,evaluate

    def test_all_original_points_revalidated_not_old_twenty(self):
        out,evaluate=self.execute()
        self.assertEqual(evaluate.call_args.kwargs['frozen_stations'],self.candidates)
        self.assertGreater(len(self.candidates),20)
        self.assertEqual(evaluate.call_args.args[2].width,1024)
        self.assertEqual(read_json(out/'navigation/comparison.json')['stations_after'],self.rows)
        final=read_json(out/'stations_final.json');self.assertEqual(len(final),20)
        self.assertTrue(all(s['geometry']=='valid' for s in final))
        self.assertTrue(read_json(out/'native_revalidation.json')['source_scene_state_unchanged'])

    def test_wrong_profile_and_changed_lattice_refused(self):
        version=read_json(self.native/'version.json');version['robot']['control_profile']='position-v1'
        write_json(self.native/'version.json',version)
        with self.assertRaisesRegex(ValueError,'native MolmoBot'):self.execute()
        with self.assertRaisesRegex(ValueError,'dense geometry'):
            revalidate_native_stations(self.variant,self.native,replace(self.config,spacing_m=.22),self.root/'other')

    def test_wrong_transplant_layout_refused(self):
        prov=read_json(self.native/'rebuild_provenance.json');prov['placements'][0]['asset_id']='WrongChair'
        write_json(self.native/'rebuild_provenance.json',prov)
        with self.assertRaisesRegex(ValueError,'placements mismatch'):self.execute()

    def test_failed_native_checks_cannot_produce_final_set(self):
        with patch('lastmile_dataflow.workflows.native_station_input.Simulation.from_snapshot',return_value=self.sim), \
             patch('lastmile_dataflow.workflows.native_station_input.collision_pairs',return_value={'valid':False}), \
             patch('lastmile_dataflow.workflows.native_station_input.load_support',return_value={'valid':True}), \
             patch('lastmile_dataflow.workflows.native_station_input.case_construction_check',return_value={'valid':True}), \
             patch('lastmile_dataflow.workflows.native_station_input.evaluate_stations') as evaluate:
            with self.assertRaisesRegex(ValueError,'check failed'):
                revalidate_native_stations(self.variant,self.native,self.config,self.root/'result')
            evaluate.assert_not_called()
        self.assertEqual(read_json(self.root/'result/summary.json')['status'],'failed')

    def test_formal_vla_ingestion_precedes_model_startup(self):
        from types import SimpleNamespace
        from lastmile_dataflow.workflows.no_edit import run_scene
        events=[]
        class StopModel(Exception):pass
        class Client:
            def __init__(self,*args):
                events.append('model_start')
                if 'bind_points' not in events:raise AssertionError('VLA loaded before points bound')
                raise StopModel()
        source=SimpleNamespace(provenance=lambda:{})
        sim=SimpleNamespace(freeze=lambda p:None,close=lambda:events.append('close'))
        task={'task_id':'pick-test','target_body':'cup','asset_id':'Cup_30','instance_id':'cup','operation':'pick','category':'Cup'}
        metadata=self.root/'metadata.json';write_json(metadata,{})
        record={'house':103,'xml':str(self.root/'val_103.xml'),'metadata':str(metadata)}
        collection=SimpleNamespace(output_dir=str(self.root/'outputs'))
        with patch('lastmile_dataflow.scenes.source.SceneSource.procthor',return_value=source), \
             patch('lastmile_dataflow.runtime.simulation.Simulation.from_source',return_value=sim), \
             patch('lastmile_dataflow.tasks.raw_scene.robot_footprint',return_value={}), \
             patch('lastmile_dataflow.tasks.raw_scene.discover_tasks',return_value=([task],[])), \
             patch('lastmile_dataflow.workflows.no_edit.check_disk'), \
             patch('lastmile_dataflow.workflows.no_edit.prepare_execution_stations',side_effect=lambda *a:events.append('bind_points')), \
             patch('lastmile_dataflow.policies.molmobot.MolmoBotClient',Client):
            with self.assertRaises(StopModel):run_scene(record,self.robot,self.config,collection,self.root,self.root/'formal')
        self.assertEqual(events,['bind_points','model_start','close'])
        self.assertEqual(read_json(self.root/'formal/scenes/val_103/model_service_status.json')['status'],'failed')
