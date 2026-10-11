"""Output layout regressions using synthetic files, not physical rollouts."""
from pathlib import Path
import tempfile
import unittest

from lastmile_dataflow.exporting.no_edit_layout import assign_output_names, index_attempt, attempt_group
from lastmile_dataflow.io import read_json, write_json, file_digest
from lastmile_dataflow.recording.recorder import AttemptRecorder
from lastmile_dataflow.workflows.no_edit import rebuild_dataset_index


class LayoutTests(unittest.TestCase):
    def test_readable_names_do_not_change_task_identity_and_separate_articulations(self):
        tasks = [dict(task_id='pick-hash', instance_id='Cup_30', category='Cup', operation='pick'),
                 dict(task_id='open-hash1', instance_id='Fridge_2', category='Fridge', operation='open'),
                 dict(task_id='open-hash2', instance_id='Fridge_2', category='Fridge', operation='open')]
        assign_output_names(tasks)
        self.assertEqual([t['output_name'] for t in tasks], ['cup30', 'fridge2', 'fridge2-open2'])
        self.assertEqual(tasks[0]['task_id'], 'pick-hash')
        with self.assertRaises(ValueError):
            attempt_group({'operation':'pick'}, {'station_id':'../escape'})

    def test_status_indexes_preserve_original_evidence_and_distinct_unknowns(self):
        with tempfile.TemporaryDirectory() as d:
            for status, bucket in [('success','success'), ('failure','fail'),
                                   ('planning_no_solution','planning_no_solution'),
                                   ('infrastructure_error','infrastructure_error'), ('incomplete','incomplete')]:
                source = Path(d) / 'attempts/pick/S0016' / status
                source.mkdir(parents=True)
                write_json(source / 'result.json', {'status':status})
                before = file_digest(source / 'result.json')
                link = Path(index_attempt(source, status))
                self.assertEqual(link.parent.name, bucket)
                self.assertEqual(link.resolve(), source.resolve())
                self.assertEqual(file_digest(source / 'result.json'), before)

    def test_recorder_group_is_optional_safe_and_exclusive(self):
        with tempfile.TemporaryDirectory() as d:
            config={'collection':{'record_video':False},'robot':{'control_hz':20}}
            with self.assertRaises(ValueError):
                AttemptRecorder(d,config,attempt_id='id',attempt_group=('..',))
            recorder=AttemptRecorder(d,config,attempt_id='id',attempt_group=('open','S0016'))
            self.assertEqual(recorder.path,Path(d)/'attempts/open/S0016/id')
            with self.assertRaises(FileExistsError):
                AttemptRecorder(d,config,attempt_id='id',attempt_group=('open','S0016'))
            recorder.finish('planning_no_solution','no_solution',{})

    def test_task_workflow_uses_semantic_names_and_checks_visibility_without_image_export(self):
        from unittest.mock import Mock, patch
        import numpy as np
        from lastmile_dataflow.config import CollectionConfig
        from lastmile_dataflow.navigation.astar import Grid
        from lastmile_dataflow.stations.no_edit_config import NoEditConfig
        from lastmile_dataflow.workflows.no_edit import collect_task
        with tempfile.TemporaryDirectory() as d:
            scene=Path(d)/'scenes/val_103';scene.mkdir(parents=True)
            write_json(scene/'robot_geometry.json',{'radius_m':.4})
            task={'task_id':'pick-original-hash','instance_id':'Cup_30','category':'Cup',
                  'target_body':'Cup_30','operation':'pick','anchor_world':[0,0,1],'instruction':'拿起杯子。'}
            station={'station_id':'S0016','xy':[0,0],'geometry':'valid'}
            grid=Grid(np.zeros(2),.1,np.ones((3,3),bool))
            with patch('mujoco.Renderer'), \
                 patch('lastmile_dataflow.runtime.no_edit_execution.clone_sim'), \
                 patch('lastmile_dataflow.stations.no_edit_sampling.disk_samples',return_value=[station]), \
                 patch('lastmile_dataflow.stations.no_edit_sampling.initialize_station',return_value=station) as initialize, \
                 patch('lastmile_dataflow.tasks.raw_scene.grasp_candidates',return_value=[]), \
                 patch('lastmile_dataflow.navigation.astar.task_support_obstacles',return_value=[]), \
                 patch('lastmile_dataflow.navigation.astar.scene_grid',return_value=grid), \
                 patch('lastmile_dataflow.exporting.no_edit_heatmap.export_heatmap'), \
                 patch('lastmile_dataflow.exporting.navigation_map.export_navigation_map'), \
                 patch('lastmile_dataflow.workflows.no_edit.check_disk'), \
                 patch('lastmile_dataflow.runtime.no_edit_execution.run_raw_attempt',return_value={
                     'station_id':'S0016','attempt':'fixture','status':'incomplete'}) as execute:
                collect_task(Mock(),task,d,NoEditConfig(),CollectionConfig(output_dir=d),scene,max_trials=1)
            self.assertTrue((scene/'tasks/cup30/result.json').is_file())
            self.assertFalse((scene/'tasks/cup30/initial_views').exists())
            self.assertNotIn('evidence_path',initialize.call_args.kwargs)
            self.assertIn('renderer',initialize.call_args.kwargs)
            self.assertRegex(execute.call_args.args[8],r'^pick-cup30-S0016-t0-[0-9a-f]{8}$')

    def fixture(self, root, *, eligible=True):
        task={'task_id':'pick-original-hash','output_name':'cup30','operation':'pick',
              'instance_id':'Cup_30','instruction':'拿起杯子。'}
        folder=root/'scenes/val_103/tasks/cup30'
        attempt=root/'scenes/val_103/attempts/pick/S0016/pick-cup30-rollout-uuid'
        failure=attempt.parent/'pick-cup30-t0-uuid'
        for path in (attempt,failure):
            path.mkdir(parents=True)
            (path/'trajectory.jsonl').write_text('{"raw_action":[0]}\n')
            write_json(path/'observations.json',[])
            videos={}
            for camera in ('head_camera','wrist_camera_l','wrist_camera_r','third_person_camera'):
                file=path/'videos'/f'{camera}.mp4';file.parent.mkdir(exist_ok=True)
                file.write_bytes(b'synthetic fixture, NOT a valid video')
                videos[camera]={'path':f'videos/{camera}.mp4'}
            write_json(path/'videos.json',{'cameras':videos})
        write_json(folder/'task.json',task)
        write_json(folder/'result.json',{'dataset_eligible':eligible})
        write_json(folder/'selection.json',{'successful':[{'attempt':str(attempt),
            'status':'success','navigation':{'status':'success'},'start':{'station_id':'S0016'},
            'goal':{'station_id':'S0020'},'path':{'xy':[[0,0],[1,1]]},
            's0_failure_evidence':[{'attempt':str(failure),'status':'failure','reason':'collision'}]}]})
        return folder,attempt,failure

    def test_dataset_has_local_actions_four_videos_and_failure_evidence_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);folder,attempt,failure=self.fixture(root)
            before=file_digest(attempt/'trajectory.jsonl')
            self.assertEqual(rebuild_dataset_index(root),1)
            entry=read_json_lines(root/'dataset_index.jsonl')[0]
            self.assertNotIn('s0_failure_evidence',entry)
            sample_path=Path(entry['sample']);sample=read_json(sample_path);base=sample_path.parent
            self.assertEqual(sample['instruction'],'拿起杯子。')
            self.assertEqual(len(sample['videos']),4)
            for video in sample['videos'].values(): self.assertTrue((base/video).is_file())
            self.assertTrue((base/sample['trajectory']).is_file())
            failed=sample['s0_failure_evidence'][0]
            self.assertEqual(failed['reason'],'collision')
            self.assertTrue((base/failed['third_person_video']).is_file())
            self.assertEqual(file_digest(attempt/'trajectory.jsonl'),before)
            checksum=file_digest(sample_path)
            self.assertEqual(rebuild_dataset_index(root),1)
            self.assertEqual(file_digest(sample_path),checksum)
            self.assertFalse(list(sample_path.parent.parent.glob('.export-*')))

    def test_success_below_retention_threshold_still_preserved_not_in_eligible_index(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.fixture(root,eligible=False)
            self.assertEqual(rebuild_dataset_index(root),0)
            samples=list((root/'datasets').rglob('sample.json'))
            self.assertEqual(len(samples),1)
            self.assertFalse(read_json(samples[0])['task_eligible_at_export'])


def read_json_lines(path):
    import json
    return [json.loads(line) for line in path.read_text().splitlines()]
