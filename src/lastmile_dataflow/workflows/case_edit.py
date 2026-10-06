"""Cross-module orchestration for case-to-edit; rule trials are not accepted samples."""
from ..agents.case_normalizer import normalize_case
from ..agents.edit_proposer import propose_edits
from ..construction.compiler import compile_candidates
from ..agents.edit_reviewer import review_edit
from ..recording.edit_views import render_edit_pair, ViewConfig
from ..io import write_json
from pathlib import Path
from dataclasses import asdict
import re
import time
import uuid
import math

from ..agents.case_gateway import AgentBudget, CaseGateway, AgentFormatError, AgentServiceError, BudgetExhausted, HTTPCaseBackend
from ..catalog.edit_assets import EditAssetCatalog
from ..construction.case_schema import CaseEditRequest, RunResult
from ..construction.compiler import compile_sample, SearchExhausted
from ..construction.layout_identity import layout_identity
from ..runtime.preparation import prepare_scene, SettleConfig
from ..runtime.case_edit_session import CaseEditSession
from ..runtime.simulation import InitializationError
from ..scenes.source import SceneSource
from ..recording.edit_views import RenderError


def generate_rule_trials(request, prepared, session, gateway):
    """S4 integration hook. S5/S7 final entry also requires visual acceptance."""
    template = normalize_case(request, gateway)
    proposals = propose_edits(template, prepared.graph, gateway, count=request.generation.proposals_per_round)
    trials = []
    for proposal in proposals:
        batch = compile_candidates(proposal, template, prepared.graph, seed=request.generation.seed,
                                   max_samples=request.generation.samples_per_proposal, deadline=session.deadline)
        for executable in batch.candidates:
            trials.append(session.execute(template, proposal, executable))
            session.reject()
    return template, proposals, trials


def review_pending_trial(request, template, proposal, executable, trial, session, gateway, path, *, view_config=None):
    """Rule failure skips the model. Uncertain gets only a finite paired reshoot."""
    if trial.status != 'pending_review':
        return None, None
    view_config = view_config or ViewConfig()
    path = Path(path)
    context = set()
    bindings = executable.sampled_parameters.get('bindings', proposal.bindings)
    if any(c.predicate in ('direction_angle',) or c.predicate.startswith('distance') for c in proposal.effective_requirements(template)) or any(
            trial.after_graph.nodes.get(node, {}).get('kind') == 'station' for node in bindings.values()):
        context = {node for node in bindings.values() if node in trial.after_graph.nodes}
    for retry in range(view_config.max_review_retries+1):
        session.active()
        packet = render_edit_pair(session.baseline, session.sim, trial.before_graph, trial.after_graph,
            {o['instance'] for o in executable.operations}, path/('rgb' if retry == 0 else f'rgb_retry_{retry}'),
            sample_id=executable.sample_id, config=view_config, context_nodes=context, expansion=1.+retry*.15,
            view_rotation_rad=retry*math.pi/2)
        # A paired reshoot uses another azimuth, not an independently tracked camera.
        review = review_edit(request, template, proposal, executable, trial, packet, gateway)
        write_json(path/f'review_{retry}.json', review.to_dict())
        if review.verdict != 'uncertain':
            return review, packet
    return review, packet


def validate_run_id(run_id):
    if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', run_id):
        raise ValueError('invalid run ID')
    return run_id


def run_case_edit(request, robot_config, collection, *, backend=None, api_settings=None, run_id=None,
                  asset_catalog=None, settle_config=None, view_config=None, initial_snapshot=None, provider=None):
    """Production in-process pipeline; CLI uses a killable worker for a hard deadline.

    No template/proposal file is required. An injectable backend exists for protocol
    tests, not as a substitute for external-model acceptance evidence.
    """
    request = request if isinstance(request, CaseEditRequest) else CaseEditRequest.from_dict(request)
    run_id = validate_run_id(run_id or 'case-edit-' + uuid.uuid4().hex[:12])
    path = Path(collection.output_dir).resolve()/'case_edits'/run_id
    path.mkdir(parents=True, exist_ok=False)
    write_json(path/'request.json', request.to_dict())
    write_json(path/'configuration.json', {'robot': asdict(robot_config), 'collection': asdict(collection),
        'settling': asdict(settle_config or SettleConfig()), 'views': asdict(view_config or ViewConfig()),
        'asset_catalog': str(asset_catalog) if asset_catalog else None,
        'initial_snapshot': str(initial_snapshot) if initial_snapshot else None,
        'provider_override': provider, 'verification_scope': 'construction_only_not_robot_task', 'backend': 'http' if api_settings else 'injected'})
    config = request.generation
    budget = AgentBudget(config.max_agent_calls, time.monotonic()+config.timeout_s)
    accepted, feedback, attempted = [], [], 0
    prepared, session = None, None
    baseline_invalid = False
    status, reason = 'budget_exhausted', 'max_rounds_reached'

    def save_result(current_status, current_reason):
        results = {'scene_validity': 'valid' if accepted else 'invalid' if baseline_invalid else 'unknown',
                   'edit_satisfaction': 'pass' if accepted else 'unknown',
                   'semantic_alignment': 'pass' if accepted else 'unknown',
                   'case_condition': 'unknown', 'task_completion': 'unknown'}
        result = RunResult(run_id, current_status, config.target_scene_count, attempted, len(accepted),
                           list(accepted), results, current_reason)
        write_json(path/'result.json', result.to_dict())
        write_json(path/'progress.json', {'agent_calls': budget.calls, 'attempted_count': attempted,
                    'accepted_samples': list(accepted), 'last_feedback': feedback[-20:]})

    save_result('partial', 'started')
    try:
        if backend is None:
            if api_settings is None:
                raise AgentServiceError('explicit_model_service_settings_required')
            backend = HTTPCaseBackend(api_settings, provider=provider)
        gateway = CaseGateway(backend, budget, path=path/'agents')
        # Always normalize afresh, and keep this template for the entire request.
        template = normalize_case(request, gateway)
        write_json(path/'template.json', template.to_dict())
        source = SceneSource(**request.scene_source)
        prepared = prepare_scene(source, robot_config, collection, base=request.task_context.get('robot_base'),
                        settle_config=settle_config, path=path/'baseline', deadline=budget.deadline,
                        initial_snapshot=initial_snapshot)
        assets = EditAssetCatalog(asset_catalog)
        session = CaseEditSession(prepared, settle_config=settle_config, deadline=budget.deadline, assets=assets)
        baseline_names = set(prepared.graph.nodes)
        original_layout = layout_identity(prepared.graph, baseline_names)
        seen = {original_layout}
        operation_names = ('move', 'rotate', 'add', 'remove')
        for round_index in range(config.max_rounds):
            budget.remaining_s()
            if attempted >= config.max_samples:
                reason = 'sample_budget_exhausted'
                break
            try:
                proposals = propose_edits(template, prepared.graph, gateway, count=config.proposals_per_round,
                    feedback=feedback[-20:], operations=operation_names, assets=assets.describe())
            except AgentFormatError:
                feedback.append({'round': round_index, 'status': 'proposal_format_failed'})
                save_result('partial', 'proposal_format_failed')
                continue
            if not proposals:
                feedback.append({'round': round_index, 'status': 'no_proposals', 'reason': 'missing_roles_or_unexpressed_strategy'})
            write_json(path/'proposals'/f'round_{round_index:03d}.json', {'proposals': [p.to_dict() for p in proposals]})
            for proposal_index, proposal in enumerate(proposals):
                limit = min(config.samples_per_proposal, proposal.sampling.get('max_samples', 16))
                rejected_streak = 0
                for _ in range(limit):
                    budget.remaining_s()
                    if attempted >= config.max_samples:
                        reason = 'sample_budget_exhausted'
                        break
                    sample_id = f'sample_{attempted:06d}'
                    index = attempted
                    attempted += 1  # Include compilation failures in the shared budget.
                    sample_path = path/'samples'/sample_id
                    sample_path.mkdir(parents=True, exist_ok=False)
                    record = {'sample_id': sample_id, 'round': round_index, 'proposal': proposal.to_dict(),
                              'seed': config.seed, 'index': index, 'status': 'compiling'}
                    write_json(sample_path/'sample.json', record)
                    save_result('partial', 'sampling')
                    strategy_failed = False
                    try:
                        executable = compile_sample(proposal, template, prepared.graph, seed=config.seed,
                                                    index=index, sample_id=sample_id, assets=assets)
                        record['executable'] = executable.to_dict()
                        trial = session.execute(template, proposal, executable)
                        record.update(trial.to_dict())
                        strategy_failed = trial.status == 'binding_not_applicable'
                        if trial.after_graph is not None:
                            write_json(sample_path/'graph_after.json', trial.after_graph.to_dict())
                        if trial.status == 'pending_review':
                            identity = layout_identity(trial.after_graph, baseline_names)
                            record['layout_id'] = identity
                            if identity in seen:
                                record.update(status='duplicate_or_unchanged', reason='final_layout_matches_baseline_or_accepted')
                            else:
                                review, packet = review_pending_trial(request, template, proposal, executable,
                                    trial, session, gateway, sample_path, view_config=view_config)
                                record['review'] = review.to_dict()
                                record['view_pair_id'] = packet['pair_id']
                                if review.verdict == 'pass':
                                    session.accept(review, sample_path/'scene')
                                    accepted.append(sample_id)
                                    seen.add(identity)
                                    record.update(status='accepted', reason='rules_visual_and_unique_layout_passed')
                                else:
                                    record.update(status='visual_rejected', reason=review.verdict)
                                    # Re-sampling cannot expose the SAME hidden baseline object.
                                    fixed_direction = any(c.predicate == 'direction_angle' for c in
                                        proposal.effective_requirements(template)) and not any(
                                        'range' in o.get('angle', o.get('search_space', {}).get('rotation', {}).get('angle', {}))
                                        for o in proposal.operations)
                                    strategy_failed = review.verdict == 'uncertain' or (review.verdict == 'fail' and fixed_direction)
                        feedback.append({'sample_id': sample_id, 'proposal_id': proposal.proposal_id,
                                         'status': record['status'], 'reason': record.get('reason', ''),
                                         'failed_checks': [c.to_dict() for c in trial.checks
                                                           if c.required and c.status != 'pass'][:20],
                                         'visual_reason': record.get('review', {}).get('reason', ''),
                                         'visual_checks': [c for c in record.get('review', {}).get('checks', [])
                                                           if c.get('required', True) and c['status'] != 'pass']})
                    except SearchExhausted as exc:
                        record.update(status='search_exhausted', reason=str(exc))
                        feedback.append({'sample_id': sample_id, 'status': 'search_exhausted', 'reason': str(exc)})
                    except (AgentFormatError, ValueError) as exc:
                        record.update(status='format_or_candidate_failed', reason=type(exc).__name__ + ':' + str(exc)[:500])
                        feedback.append({'sample_id': sample_id, 'status': record['status'], 'reason': record['reason']})
                    except BaseException as exc:
                        record.update(status='interrupted' if isinstance(exc, KeyboardInterrupt) else 'incomplete',
                                      reason=type(exc).__name__)
                        raise
                    finally:
                        write_json(sample_path/'sample.json', record)
                        session.reject()
                        save_result('partial', 'sampling')
                    if len(accepted) >= config.target_scene_count:
                        status, reason = 'completed', 'target_scene_count_reached'
                        break
                    rejected_streak = 0 if record['status'] == 'accepted' else rejected_streak + 1
                    if strategy_failed or rejected_streak >= 4:
                        break  # Change binding/strategy, keeping the shared budgets.
                if status == 'completed' or attempted >= config.max_samples:
                    break
            if status == 'completed' or attempted >= config.max_samples:
                if status != 'completed':
                    reason = 'sample_budget_exhausted'
                break
    except (BudgetExhausted, TimeoutError) as exc:
        status, reason = 'budget_exhausted', str(exc)
    except KeyboardInterrupt:
        status, reason = 'interrupted', 'user_interrupt'
    except InitializationError as exc:
        baseline_invalid = True
        status, reason = 'failed', 'invalid_baseline:' + str(exc)[:1000]
        write_json(path/'baseline_error.json', {'reason': str(exc), 'details': getattr(exc, 'details', {})})
    except AgentFormatError as exc:
        status, reason = 'failed', 'normalization_format_failed:' + str(exc)[:500]
    except Exception as exc:
        status, reason = 'infrastructure_error', type(exc).__name__ + ':' + str(exc)[:500]
    finally:
        if session:
            session.close()
        if prepared:
            prepared.close()
        save_result(status, reason)
    return path
