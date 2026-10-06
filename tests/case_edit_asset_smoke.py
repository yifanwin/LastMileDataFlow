"""Opt-in REAL mesh asset add/remove in a SYNTHETIC scene; no Agent acceptance."""
import argparse
import json
import math
from pathlib import Path
import time

import mujoco
from case_edit_helpers import table_scene, template, proposal
from lastmile_dataflow.catalog.edit_assets import EditAssetCatalog
from lastmile_dataflow.construction.compiler import compile_sample
from lastmile_dataflow.construction.dsl import SymbolicDSL
from lastmile_dataflow.io import file_digest, write_json
from lastmile_dataflow.recording.edit_views import ViewConfig, render_edit_pair
from lastmile_dataflow.runtime.case_edit_session import CaseEditSession
from lastmile_dataflow.runtime.preparation import prepare_scene


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--asset-xml', type=Path, required=True)
    parser.add_argument('--root-body', required=True)
    parser.add_argument('--wrap-free', action='store_true', help='derive movable wrapper via exact rigid-body fusion; source remains read-only')
    args = parser.parse_args()
    path = Path('outputs/case_edits/real-asset-smoke-' + str(time.time_ns()))
    path.mkdir(parents=True, exist_ok=False)
    report = {'scope': 'real_mesh_asset_synthetic_scene_robot_rule_checks_RGB_not_agent_acceptance', 'status': 'failed'}
    source_hash = file_digest(args.asset_xml)
    try:
        asset_path = args.asset_xml.resolve()
        if args.wrap_free:
            spec = mujoco.MjSpec.from_file(str(asset_path))
            body = spec.body(args.root_body)
            if list(body.joints):
                raise ValueError('wrapper expects a static asset root')
            spec.compiler.fusestatic = True
            spec.compiler.meshdir = str(asset_path.parent)
            spec.compiler.texturedir = str(asset_path.parent)
            body.add_freejoint(name='case_asset_free')
            # This chosen library asset is y-up; an explicit adapter, not a DSL unit change.
            body.quat = [math.sqrt(.5), math.sqrt(.5), 0, 0]
            fused = mujoco.MjModel.from_xml_string(spec.to_xml())
            asset_path = path/'derived_asset.xml'
            mujoco.mj_saveLastXML(str(asset_path), fused)
            report['asset_adapter'] = {'source_readonly': str(args.asset_xml.resolve()),
                'derived': str(asset_path.resolve()), 'root_mass_kg': float(fused.body_mass[fused.body(args.root_body).id]),
                'method': 'explicit_free_joint_and_exact_static_fusion_y_up_to_z_up'}
        catalog_path = path/'assets.json'
        write_json(catalog_path, {'asset_catalog_version': '0.1', 'assets': [
            {'asset_id': 'real-clock', 'xml_path': str(asset_path.resolve()), 'root_body': args.root_body,
             'category': 'AlarmClock', 'type': 'object'}]})
        assets = EditAssetCatalog(catalog_path)
        source, robot = table_scene(path)
        with prepare_scene(source, robot, base=[0, 0, 0], path=path/'baseline') as prepared, CaseEditSession(prepared, assets=assets) as session:
            add = {'op': 'add', 'bind_as': '$new', 'asset_selector': {'asset_id': 'real-clock'},
                   'search_space': {'support': '$support', 'xy': {'mode': 'uniform', 'x': [-.3, -.3], 'y': [-.2, -.2]}}}
            symbolic = SymbolicDSL('real-add', proposal().bindings, [add],
                goals=[{'predicate': 'supported_by', 'args': ['$new', '$support'], 'value': True}])
            executable = compile_sample(symbolic, template(), prepared.graph, assets=assets)
            trial = session.execute(template(), symbolic, executable)
            report['addition'] = trial.to_dict()
            if trial.status != 'pending_review':
                raise ValueError('real asset addition did not pass rules')
            write_json(path/'graph_added.json', trial.after_graph.to_dict())
            packet = render_edit_pair(prepared.sim, session.sim, prepared.graph, trial.after_graph,
                {executable.operations[0]['instance'], 'target'}, path/'rgb', sample_id=executable.sample_id,
                config=ViewConfig(width=320, height=240))
            report['real_rgb_count'] = len(packet['images'])
            session.reject()
            symbolic = SymbolicDSL('real-add-remove', proposal().bindings,
                                    [add, {'op': 'remove', 'subject': '$new'}])
            executable = compile_sample(symbolic, template(), prepared.graph, assets=assets)
            trial = session.execute(template(), symbolic, executable)
            report['add_then_remove'] = trial.to_dict()
            added_name = executable.sampled_parameters['bindings']['new']
            if trial.status != 'pending_review' or added_name in trial.after_graph.nodes:
                raise ValueError('topology removal failed')
            write_json(path/'graph_removed.json', trial.after_graph.to_dict())
            session.reject()
            report.update(status='rule_and_topology_checks_passed', accepted_count=0,
                          baseline_restored=session.sim is prepared.sim,
                          results={'case_condition': 'unknown', 'task_completion': 'unknown'})
        report['source_unchanged'] = source_hash == file_digest(args.asset_xml)
    except Exception as exc:
        report.update(error_type=type(exc).__name__, reason=str(exc)[:1000])
    write_json(path/'result.json', report)
    print(report['status'], path, flush=True)
    return 0 if report['status'] == 'rule_and_topology_checks_passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
