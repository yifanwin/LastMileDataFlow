"""Transfer orchestration fault injections, not real navigation/VLA evidence."""
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lastmile_dataflow.io import read_json

SCRIPT=Path(__file__).resolve().parents[1]/'scripts/run_molmobot_station_transfer.py'
spec=importlib.util.spec_from_file_location('molmobot_station_transfer',SCRIPT)
transfer=importlib.util.module_from_spec(spec)
spec.loader.exec_module(transfer)


class TransferTrialsTests(unittest.TestCase):
    def run_trials(self, outcomes):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            with patch.object(transfer,'run_vla_attempt',side_effect=outcomes) as run:
                results=transfer.run_trials(None,{'task_id':'pick-fixture'},
                    {'station_id':'S0009'},{'station_id':'S0023'},
                    SimpleNamespace(seed=1),None,out,None,{'xy':[]})
            return results,read_json(out/'summary.json'),read_json(out/'trials.json'),run.call_count

    def test_local_missing_navigation_continues_without_fabricating_success(self):
        local={'status':'initialization_error','reason':'bad station','failure_scope':'episode'}
        good={'status':'success','reason':'completed','navigation':{'status':'success'}}
        results,summary,saved,count=self.run_trials([local.copy()]+[good.copy() for _ in range(4)])
        self.assertEqual(count,5)
        self.assertEqual(len(results),5)
        self.assertNotIn('navigation',saved[0])
        self.assertEqual(summary['statuses'][0]['navigation']['status'],'unknown')
        self.assertEqual(summary['navigation_successes'],4)
        self.assertEqual(summary['end_to_end_successes'],4)
        self.assertFalse(summary['stopped_early'])
        self.assertIsNone(summary['stop_reason'])

    def test_shared_failure_saved_and_stops_before_reusing_policy(self):
        for failure in ({'status':'infrastructure_error','reason':'model connection lost'},
                        {'status':'recording_error','reason':'shared cleanup failure','failure_scope':'shared'}):
            with self.subTest(failure=failure):
                results,summary,saved,count=self.run_trials([failure.copy()])
                self.assertEqual(count,1)
                self.assertEqual(saved,results)
                self.assertEqual(summary['completed_trials'],1)
                self.assertEqual(summary['requested_trials'],5)
                self.assertTrue(summary['stopped_early'])
                self.assertEqual(summary['stop_reason'],failure['reason'])
                self.assertEqual(summary['end_to_end_successes'],0)

    def test_timeout_and_local_recording_error_continue(self):
        outcomes=[{'status':status,'reason':status,'navigation':{'status':'unknown'}}
                  for status in ('timeout','episode_error','recording_error','failure','timeout')]
        _,summary,_,count=self.run_trials(outcomes)
        self.assertEqual(count,5)
        self.assertEqual(summary['navigation_successes'],0)
        self.assertIsNone(summary['operation_success_rate_given_arrival'])
        self.assertFalse(summary['stopped_early'])
