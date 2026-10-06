"""Opt-in REAL Agent1/2 HTTP calls with a synthetic scene graph, not real-house proof."""
import argparse
import tempfile
import time
from pathlib import Path
from case_edit_helpers import table_scene
from lastmile_dataflow.agents.case_gateway import AgentBudget, CaseGateway, HTTPCaseBackend
from lastmile_dataflow.agents.case_normalizer import normalize_case
from lastmile_dataflow.agents.edit_proposer import propose_edits
from lastmile_dataflow.construction.case_schema import CaseEditRequest
from lastmile_dataflow.runtime.preparation import prepare_scene
from lastmile_dataflow.io import write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--api-settings', default='configs/agent_api.json')
    args = parser.parse_args()
    out = Path('outputs/case_edits/s4-agent-smoke-' + str(time.time_ns()))
    out.mkdir(parents=True)
    request = CaseEditRequest('保持目标物体在支撑面上，但将其移到支撑面的另一处，使布局明显不同。',
                             {'scene_id': 'synthetic', 'xml_path': 'synthetic.xml'})
    gateway = CaseGateway(HTTPCaseBackend(args.api_settings), AgentBudget(5, time.monotonic()+170),
                          timeout_s=70, path=out/'agents')
    try:
        template = normalize_case(request, gateway)
        write_json(out/'template.json', template.to_dict())
        print('normalizer parsed', flush=True)
        with tempfile.TemporaryDirectory() as root:
            source, robot = table_scene(root)
            with prepare_scene(source, robot, base=[0, 0, 0]) as prepared:
                proposals = propose_edits(template, prepared.graph, gateway, count=2)
                write_json(out/'proposals.json', {'proposals': [p.to_dict() for p in proposals]})
                print('proposer parsed', len(proposals), flush=True)
                if not proposals:
                    raise ValueError('applicable synthetic scene requires at least one proposal for S4 smoke')
        write_json(out/'result.json', {'status': 'parsed', 'scope': 'real model calls, synthetic graph; no accepted scenes', 'calls': gateway.budget.calls})
    except Exception as exc:
        write_json(out/'result.json', {'status': 'failed', 'error_type': type(exc).__name__, 'reason': str(exc), 'calls': gateway.budget.calls})
        print(type(exc).__name__, str(exc), flush=True)
        raise SystemExit(1)
    finally:
        print('artifacts', out, flush=True)


if __name__ == '__main__':
    main()
