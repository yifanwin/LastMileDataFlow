"""Case-factory contracts: synthetic evidence is never physical validation."""
import copy
from pathlib import Path
import tempfile
import unittest

from lastmile_dataflow.io import read_json, write_json

ROOT = Path(__file__).resolve().parents[1]


def policy():
    return {'scene_version': 'scene-v1', 'target': 'cup', 'grasp_digest': 'a'*64,
            'grasp_ids': [0, 1], 'arms': ['left', 'right'], 'torso_heights': [0., .369, .738],
            'seed': 7, 'num_ik_seeds': 8, 'max_attempts': 2,
            'timeout_s': 30., 'protocol': 'strict-pick-v3'}


def station(name, edge, base, reach='no_solution', plan='no_solution', execution='not_executed'):
    return {'station_id': name, 'edge': edge, 'base': base, 'reach': reach, 'plan': plan,
            'execution': execution, 'legal': True, 'visible': True, 'facing_target': True,
            'edge_gap_m': .1, 'policy': policy(), 'evidence': ['synthetic-test-only']}


def pair():
    s0 = station('S0', 'v-', [0, -1, 1.57])
    s1 = station('S1', 'u-', [-1, 0, 0], 'success', 'success', 'success')
    yaw = station('Y1', 'v-', [0, -1, 1.9])
    return {'C0': s0, 'C_yaw': [copy.deepcopy(s0), yaw], 'C1': s1,
            'start_edge': [copy.deepcopy(s0)], 'coverage': {'yaw_complete': True, 'start_edge_complete': True},
            'path': {'status': 'pass', 'poses': [[0, -1], [-1, 0]], 'method': 'synthetic-test-only'},
            'success_region_count': 2, 'counterfactual': None, 'standing': None}


class CaseSpecTests(unittest.TestCase):
    def test_all_specs(self):
        from lastmile_dataflow.construction.case_spec import load_case_spec
        specs = [load_case_spec(p) for p in sorted((ROOT/'configs/case_specs').glob('*.json'))]
        self.assertEqual({s.case_type for s in specs}, {'case1', 'case1.5', 'case2', 'case3', 'case1-S'})

    def test_strict_schema(self):
        from lastmile_dataflow.construction.case_spec import CaseSpec
        raw = read_json(ROOT/'configs/case_specs/case1.json')
        for mutate in (lambda x: x.pop('preconditions'), lambda x: x.update(typo=True),
                       lambda x: x['predicates'].append('made_up'),
                       lambda x: x['thresholds'].update(edge_gap_max_m=float('nan')),
                       lambda x: x.update(move_types=['yaw_only'])):
            value = copy.deepcopy(raw); mutate(value)
            with self.assertRaises(ValueError): CaseSpec.from_dict(value)


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        from lastmile_dataflow.construction.case_spec import load_case_spec
        self.spec = load_case_spec(ROOT/'configs/case_specs/case1.json')

    def verdict(self, data=None, spec=None):
        from lastmile_dataflow.validation.comparison import compare_pair
        return compare_pair(spec or self.spec, data or pair())

    def test_l1_not_l2_without_control_execution(self):
        result = self.verdict()
        self.assertEqual(result['improvement_level'], 'L1')
        self.assertEqual(result['case_type'], 'case1')
        self.assertEqual(result['task_success'], 'unknown')

    def test_l2_strict_execution(self):
        data = pair(); data['C0']['execution'] = 'failure'
        for k in ('C0','C1'):
            data[k]['strict_attempt']={'protocol':'strict-pick-v3','audit':{'valid':True},'path':'synthetic-test-only'}
        self.assertEqual(self.verdict(data)['improvement_level'], 'L2')
        data['C0']['execution'] = 'success'
        self.assertEqual(self.verdict(data)['status'], 'fail')

    def test_infrastructure_is_unknown(self):
        data = pair(); data['C0']['reach'] = 'infrastructure_error'
        self.assertEqual(self.verdict(data)['status'], 'unknown')

    def test_no_yaw_shortcut(self):
        data = pair(); data['C_yaw'][1]['plan'] = 'success'
        self.assertEqual(self.verdict(data)['move_type'], 'yaw_only')
        self.assertEqual(self.verdict(data)['status'], 'fail')

    def test_same_edge_is_separate(self):
        data = pair(); data['C1']['edge'] = 'v-'; data['C1']['base'] = [.8, -1, 1.57]
        self.assertEqual(self.verdict(data)['case_type'], 'case1-S')
        self.assertEqual(self.verdict(data)['move_type'], 'same_edge')

    def test_unknown_edge_cannot_prove_case1(self):
        for mutation in ('unknown', 'success'):
            data = pair(); data['start_edge'].append(station('S2', 'v-', [.5, -1, 1.57], reach=mutation))
            self.assertNotEqual(self.verdict(data)['status'], 'pass')

    def test_require_coverage_path_visibility_margin(self):
        for kind in ('coverage', 'path', 'visible', 'gap', 'region'):
            data = pair()
            if kind == 'coverage': data['coverage']['yaw_complete'] = False
            elif kind == 'path': data['path']['status'] = 'unknown'
            elif kind == 'visible': data['C0']['visible'] = False
            elif kind == 'gap': data['C0']['edge_gap_m'] = 99
            else: data['success_region_count'] = 1
            self.assertNotEqual(self.verdict(data)['status'], 'pass', kind)

    def test_fairness_and_yaw_position(self):
        for kind in ('arms', 'seed', 'torso_heights', 'grasp_digest', 'xy'):
            data = pair()
            if kind == 'xy': data['C_yaw'][1]['base'][0] = .1
            else: data['C1']['policy'][kind] = {'arms': ['left'], 'seed': 8,
                  'torso_heights': [0.], 'grasp_digest': 'b'*64}[kind]
            with self.assertRaises(ValueError): self.verdict(data)

    def test_case3_requires_rollback_counterfactual(self):
        from lastmile_dataflow.construction.case_spec import load_case_spec
        spec = load_case_spec(ROOT/'configs/case_specs/case3.json')
        data = pair(); data['C0']['reach'] = 'success'
        self.assertEqual(self.verdict(data, spec)['status'], 'unknown')
        data['counterfactual'] = {'kind': 'remove_corridor_clutter', 'plan': 'success',
                                  'rolled_back': True, 'policy': policy(), 'evidence': ['transaction']}
        self.assertEqual(self.verdict(data, spec)['status'], 'pass')
        data['counterfactual']['rolled_back'] = False
        self.assertNotEqual(self.verdict(data, spec)['status'], 'pass')


class ReviewTests(unittest.TestCase):
    def response(self):
        return {'checks': {f'P{i}': {'status': 'pass', 'confidence': .99,
                'reason': 'fixture', 'views': ['head'], 'objects': ['target']} for i in range(1, 7)}}

    def test_relaxed_policy(self):
        from lastmile_dataflow.agents.plausibility_reviewer import aggregate_review
        config = read_json(ROOT/'configs/case_factory/plausibility.json')
        value = self.response(); value['checks']['P2']['status'] = 'fail'
        result = aggregate_review(value, config, branch='unedited', views=['head'], objects=['target'])
        self.assertEqual(result['conclusion'], 'plausible')
        self.assertIn('P2', result['flags'])
        value['checks']['P1']['status'] = 'fail'
        self.assertEqual(aggregate_review(value, config, branch='unedited', views=['head'], objects=['target'])['conclusion'], 'implausible')
        value['checks']['P1']['confidence'] = .5
        self.assertEqual(aggregate_review(value, config, branch='unedited', views=['head'], objects=['target'])['conclusion'], 'unknown')

    def test_bad_contract_unknown_not_pass(self):
        from lastmile_dataflow.agents.plausibility_reviewer import aggregate_review
        config = read_json(ROOT/'configs/case_factory/plausibility.json')
        for mutate in (lambda x: x['checks'].pop('P1'), lambda x: x.update(conclusion='plausible'),
                       lambda x: x['checks']['P1'].update(views=['invented'])):
            value = self.response(); mutate(value)
            with self.assertRaises(ValueError):
                aggregate_review(value, config, branch='unedited', views=['head'], objects=['target'])

    def test_payload_does_not_leak_plan_results(self):
        from lastmile_dataflow.agents.plausibility_reviewer import review_payload
        result = review_payload({'room_type': 'kitchen', 'plan_ok': True, 'comparison': pair(),
                                 'target_category': 'Cup', 'task_success': True}, 'unedited', ['head'], ['target'])
        self.assertNotIn('plan_ok', str(result)); self.assertNotIn('task_success', str(result))


class NavigationTests(unittest.TestCase):
    def test_path_does_not_cut_corners(self):
        from lastmile_dataflow.navigation.grid import find_path
        import numpy as np
        grid = np.zeros((5, 5), bool); grid[1, 0] = True; grid[0, 1] = True
        self.assertIsNone(find_path(grid, (0, 0), (4, 4)))
        grid[0, 1] = False
        path = find_path(grid, (0, 0), (4, 4))
        self.assertEqual(path[0], (0, 0)); self.assertEqual(path[-1], (4, 4))
        self.assertTrue(all(not grid[p] for p in path))

    def test_inflation(self):
        from lastmile_dataflow.navigation.grid import inflate_obstacles
        import numpy as np
        grid = np.zeros((7, 7), bool); grid[3, 3] = True
        inflated = inflate_obstacles(grid, 1)
        self.assertTrue(inflated[3, 2]); self.assertFalse(inflated[1, 1])




class FactoryWorkflowTests(unittest.TestCase):
    def test_unedited_priority_freeze_unknown_review_and_no_overwrite(self):
        from lastmile_dataflow.workflows.case_factory import run_case_factory
        from lastmile_dataflow.construction.case_spec import load_case_spec
        from lastmile_dataflow.scenes.source import SceneSource
        from lastmile_dataflow.io import file_digest
        class Backend:
            review_config=None
            def prepare(self,source,spec,path): return [{'snapshot':str(snapshot),'candidate_id':'fixture'}]
            def station_map(self,candidate,spec,path):
                path.mkdir(parents=True)
                data=pair(); second=copy.deepcopy(data['C1']); second.update(station_id='S2',base=[-1,.1,0])
                rows=[data['C0'],data['C1'],second]; write_json(path/'stations.json',rows); return rows
            def pair_packet(self,candidate,spec,s0,s1,path):
                path.mkdir(parents=True); data=pair(); data.update(C0=s0,C1=s1); return data
            def execute_pair(self,*args): raise AssertionError('no physical execution in test')
            def review(self,*args): return {'conclusion':'unknown','calls':1,'manual_review':True}
            def edited_candidates(self,*args): raise AssertionError('N must take precedence')
            def close(self): pass
        with tempfile.TemporaryDirectory() as d:
            snapshot=Path(d)/'scene'; snapshot.mkdir(); write_json(snapshot/'version.json',{'version_id':'scene-v1','fixture':True})
            write_json(snapshot/'checksums.json',{'version.json':file_digest(snapshot/'version.json')})
            spec=load_case_spec(ROOT/'configs/case_specs/case1.json')
            args=([SceneSource('fixture','fixture.xml')],spec,Backend(),d)
            path=run_case_factory(*args,run_id='test',execute=False,max_pairs=1)
            task=read_json(path/'tasks.json')[0]
            self.assertEqual(task['construction_branch'],'unedited'); self.assertEqual(task['edits'],[])
            self.assertEqual(task['improvement_level'],'L1'); self.assertTrue(task['manual_review'])
            self.assertEqual(task['task_success'],'unknown')
            with self.assertRaises(FileExistsError): run_case_factory(*args,run_id='test',execute=False)

    def test_one_review_call_on_service_error(self):
        from lastmile_dataflow.agents.plausibility_reviewer import review_once
        calls=[]
        def backend(**kwargs): calls.append(kwargs); raise TimeoutError()
        result=review_once(backend,{},'unedited',[{'view':'head','path':'unused'}],['target'],
                          read_json(ROOT/'configs/case_factory/plausibility.json'))
        self.assertEqual(len(calls),1); self.assertEqual(result['conclusion'],'unknown')


class FactorySafetyTests(unittest.TestCase):
    def test_supervisor_timeout_reserves_unique_run_and_reaps(self):
        from lastmile_dataflow.workflows.case_factory import supervised_case_factory
        from lastmile_dataflow.construction.case_spec import load_case_spec
        from lastmile_dataflow.config import RobotConfig,CollectionConfig
        from lastmile_dataflow.scenes.source import SceneSource
        spec=load_case_spec(ROOT/'configs/case_specs/case1.json')
        with tempfile.TemporaryDirectory() as d:
            args=([SceneSource('fixture','fixture.xml')],spec,RobotConfig('fixture.xml'),CollectionConfig(),{'timeout_s':.001})
            p=supervised_case_factory(*args,output=d,run_id='deadline')
            self.assertEqual(read_json(p/'summary.json')['status'],'budget_exhausted')
            self.assertTrue(read_json(p/'interruption.json')['worker_reaped'])
            before=(p/'summary.json').read_bytes()
            with self.assertRaises(FileExistsError): supervised_case_factory(*args,output=d,run_id='deadline')
            self.assertEqual(before,(p/'summary.json').read_bytes())

    def test_l2_requires_both_audited_attempts(self):
        from lastmile_dataflow.construction.case_spec import load_case_spec
        from lastmile_dataflow.validation.comparison import compare_pair
        spec=load_case_spec(ROOT/'configs/case_specs/case1.json'); data=pair()
        data['C0']['execution']='failure'
        self.assertEqual(compare_pair(spec,data)['improvement_level'],'L1')
        for key in ('C0','C1'):
            data[key]['strict_attempt']={'status':'planning_no_solution' if key=='C0' else 'executed_success',
                'protocol':'strict-pick-v3','audit':{'valid':True},'path':'synthetic-test-only'}
        data['C0']['execution']='not_executed'
        self.assertEqual(compare_pair(spec,data)['improvement_level'],'L2')
        data['C1']['strict_attempt']['audit']['valid']=False
        self.assertEqual(compare_pair(spec,data)['improvement_level'],'L1')

    def test_case15_requires_three_roles_positive_gap(self):
        from lastmile_dataflow.construction.case_spec import load_case_spec
        from lastmile_dataflow.validation.comparison import compare_pair
        spec=load_case_spec(ROOT/'configs/case_specs/case1_5.json'); data=pair()
        self.assertEqual(compare_pair(spec,data)['status'],'unknown')
        data['standing']={'coverage_complete':True,'A_edge':'v+','B_edge':'v-','C_edge':'u-',
                          'A_standable':False,'clearance_m':.2,'required_depth_m':.6}
        self.assertEqual(compare_pair(spec,data)['status'],'pass')
        data['standing']['clearance_m']=0
        self.assertEqual(compare_pair(spec,data)['status'],'fail')

    def test_case2_must_have_human_annotation_and_body_control(self):
        from lastmile_dataflow.construction.case_spec import load_case_spec
        from lastmile_dataflow.validation.comparison import compare_pair
        spec=load_case_spec(ROOT/'configs/case_specs/case2.json'); data=pair()
        self.assertEqual(compare_pair(spec,data)['status'],'unknown')
        data['handle_annotation_human_checked']=True
        data['counterfactual']={'kind':'body_grasp','plan':'success','rolled_back':True,
                                'policy':policy(),'evidence':['synthetic-test-only']}
        self.assertEqual(compare_pair(spec,data)['status'],'pass')
        data['counterfactual']['plan']='no_solution'
        self.assertEqual(compare_pair(spec,data)['status'],'fail')

    def test_ground_path_includes_exact_endpoints(self):
        import numpy as np
        from lastmile_dataflow.navigation.grid import ground_path
        result=ground_path({'occupied':np.zeros((5,5),bool),'origin':[0,0],'resolution_m':.1,
                            'method':'synthetic'},[.01,.02,0],[.32,.33,0])
        self.assertEqual(result['poses'][0],[.01,.02]);self.assertEqual(result['poses'][-1],[.32,.33])
        self.assertEqual(result['navigation_execution'],'unknown')


if __name__=='__main__': unittest.main()
