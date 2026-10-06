import tempfile
import time
import unittest
from case_edit_helpers import table_scene, template, proposal
from lastmile_dataflow.agents.case_gateway import AgentBudget, CaseGateway, AgentFormatError
from lastmile_dataflow.agents.edit_reviewer import review_edit
from lastmile_dataflow.construction.case_schema import CaseEditRequest
from lastmile_dataflow.construction.compiler import compile_sample
from lastmile_dataflow.runtime.case_edit_session import CaseEditSession
from lastmile_dataflow.runtime.preparation import prepare_scene


class ReviewTests(unittest.TestCase):
    def test_complete_identity_bound_review_and_reject_missing(self):
        with tempfile.TemporaryDirectory() as root:
            source, robot = table_scene(root)
            with prepare_scene(source, robot, base=[0, 0, 0]) as prepared, CaseEditSession(prepared) as session:
                p = proposal()
                exe = compile_sample(p, template(), prepared.graph)
                trial = session.execute(template(), p, exe)
                # Protocol-only mock view refs, never accepted real render evidence.
                packet = {'sample_id': exe.sample_id, 'pair_id': 'mock', 'rig': {},
                  'before_graph_id': trial.before_graph.to_dict()['graph_id'],
                  'after_graph_id': trial.after_graph.to_dict()['graph_id'],
                  'images': [{'view': s+'/'+v, 'path': 'unused', 'pair_id': 'mock'}
                   for s in ('before', 'after') for v in ('top', 'oblique_a', 'oblique_b')]}
                request = CaseEditRequest('Change layout', {'scene_id': 'a', 'xml_path': 'a.xml'})
                response = {'sample_id': exe.sample_id, 'verdict': 'pass', 'checks': [
                    {'item': name, 'status': 'pass', 'views': ['before/top', 'after/top']}
                    for name in ('intent', 'semantic:0')]}
                def gateway(value):
                    return CaseGateway(lambda **k: value, AgentBudget(2, time.monotonic()+10), format_retries=0)
                review = review_edit(request, template(), p, exe, trial, packet, gateway(response))
                self.assertEqual(review.verdict, 'pass')
                response['checks'].pop()
                with self.assertRaises(AgentFormatError):
                    review_edit(request, template(), p, exe, trial, packet, gateway(response))
                packet['sample_id'] = 'old'
                with self.assertRaises(ValueError):
                    review_edit(request, template(), p, exe, trial, packet, gateway(response))
