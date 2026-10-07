"""v0.2 four-agent, local, observed single-scene construction.

M13 is explicitly unavailable: neither images nor fixed-base station tests are
allowed to upgrade construction candidates to verified mobile-manipulation cases.
"""
from dataclasses import asdict, replace
from pathlib import Path
import time
import uuid

from ..agents.case_normalizer import normalize_case
from ..agents.construction_strategy import choose_strategy
from ..agents.edit_proposer import propose_edits
from ..agents.edit_reviewer import review_edit
from ..agents.case_gateway import (AgentBudget, AgentFormatError, AgentServiceError,
                                   BudgetExhausted, CaseGateway, HTTPCaseBackend)
from ..catalog.library import ThorLibrary
from ..construction.compiler import compile_sample, SearchExhausted
from ..construction.layout_identity import layout_identity
from ..construction.scene_request import SceneConstructionRequest
from ..io import write_json
from ..recording.edit_views import ViewConfig, AuxiliaryViews
from ..recording.head_views import render_head_pair
from ..runtime.case_edit_session import CaseEditSession
from ..runtime.preparation import prepare_scene, SettleConfig
from ..runtime.simulation import InitializationError
from ..runtime.visible_initialization import prepare_visible, VisibleInitializationUnavailable
from ..scenes.local_context import sample_contexts, crop_context, ContextUnavailable
import re


class TargetChangeRequested(ContextUnavailable):
    def __init__(self, target):
        self.target = target
        super().__init__('reinitialize_requested_target:' + target)


def resolve_information(information, *, auxiliary, context, prepared, config, library, assets, path, seed, deadline):
    """Execute an information request, never a model-supplied operation or camera pose."""
    if information.kind == 'aux_view':
        return context, auxiliary.ensure(information)
    if information.kind == 'expand_context':
        if not context.radius_m < information.radius_m <= config.max_radius_m:
            raise AgentFormatError('requested expansion must increase radius within the configured maximum')
        context = crop_context(prepared.graph, context.target, context.support, information.radius_m,
                               max_nodes=config.max_nodes, context_id=context.context_id)
        auxiliary.context = context
        return context, {'status': 'fulfilled', 'radius_m': context.radius_m}
    if information.kind == 'assets':
        if not set(information.categories) <= set(library.categories()):
            raise AgentFormatError('requested asset category absent from library')
        extra = library.retrieve([{'categories': information.categories, 'intended_role': information.reason,
                                  'size_constraints_m': {}}], path, limit=config.assets_per_category,
                                 seed=seed, deadline=deadline)
        assets.assets.update(extra.assets)
        return context, {'status': 'fulfilled' if extra.assets else 'information_insufficient',
                         'loadable_asset_ids': sorted(extra.assets)}
    target = information.node_ids[0]
    if prepared.graph.nodes[target].get('root_motion') != 'free':
        raise AgentFormatError('requested target is not a movable scene instance')
    raise TargetChangeRequested(target)

def validate_run_id(run_id):
    if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', run_id):
        raise ValueError('invalid run ID')
    return run_id


def initial_result(request, run_id):
    return {'result_version': '0.2', 'run_id': run_id, 'case_type': request.case_type,
        'status': 'running', 'reason': 'started', 'target_variant_count': request.target_variant_count,
        'attempted_count': 0, 'construction_accepted_count': 0, 'accepted_samples': [], 'agent_calls': 0,
        'case_verified_count': 0, 'version_delivery_complete': False,
        'verification_scope': 'construction_only',
        'results': {'scene_validity': 'unknown', 'construction_satisfaction': 'unknown',
                    'visual_semantic_alignment': 'unknown', 'case_condition': 'unknown', 'task_completion': 'unknown'}}


def run_case_edit(request, robot, collection, *, backend=None, api_settings=None, provider=None,
                        run_id=None, settle_config=None, view_config=None, model=None):
    request = request if isinstance(request, SceneConstructionRequest) else SceneConstructionRequest.from_dict(request)
    run_id = validate_run_id(run_id or 'construct-' + uuid.uuid4().hex[:12])
    path = Path(collection.output_dir).resolve()/'case_construction'/run_id
    path.mkdir(parents=True, exist_ok=False)
    write_json(path/'request.json', request.to_dict())
    write_json(path/'configuration.json', {'robot': asdict(robot), 'collection': asdict(collection),
        'settling': asdict(settle_config or SettleConfig()), 'views': asdict(view_config or ViewConfig()),
        'provider_override': provider, 'model_override': model, 'backend': 'injected' if backend else 'http',
        'agent_request_policy': {'max_attempts_including_first': 5, 'service_backoff_initial_s': 2,
                                 'service_backoff_cap_s': 16, 'shared_global_budget': True},
        'mobile_task_executor': 'not_implemented', 'formal_task_base_motion': 'translation_and_yaw_allowed'})
    result = initial_result(request, run_id)
    feedback, accepted, seen = [], [], set()
    budget = AgentBudget(request.budgets.max_agent_calls, time.monotonic()+request.budgets.timeout_s)
    source_prepared, prepared, session = None, None, None
    view_config = view_config or ViewConfig()
    forced_target = None

    def checkpoint():
        result.update(agent_calls=budget.calls, construction_accepted_count=len(accepted), accepted_samples=list(accepted))
        if accepted:
            result['results'].update(scene_validity='valid', construction_satisfaction='pass', visual_semantic_alignment='pass')
        write_json(path/'result.json', result)
        write_json(path/'progress.json', {'feedback': feedback[-20:], 'agent_calls': budget.calls,
                                         'attempted_count': result['attempted_count'], 'accepted_samples': list(accepted)})

    checkpoint()
    try:
        source = request.scene_input.resolve()  # Exact identity; never queries a scene index.
        write_json(path/'source.json', source.provenance())
        if request.verification_mode != 'construction_only':
            result.update(status='unsupported', reason='mobile_task_executor_not_implemented; no fixed-base substitute')
            return path
        if backend is None:
            if api_settings is None:
                raise AgentServiceError('explicit_model_service_settings_required')
            backend = HTTPCaseBackend(api_settings, provider=provider, model=model)
        gateway = CaseGateway(backend, budget, timeout_s=request.budgets.agent_timeout_s, path=path/'agents')
        template = normalize_case(request, gateway)
        write_json(path/'template.json', template.to_dict())
        checkpoint()  # Keep completed HTTP accounting if native source preparation crashes.
        collection = replace(collection, seed=request.seed)
        result.update(stage='source_preparation', reason='loading_raw_source')
        checkpoint()
        source_prepared = prepare_scene(source, robot, collection, path=path/'source_baseline',
                                        settle_config=settle_config, deadline=budget.deadline)
        result.update(stage='local_context', reason='raw_source_ready')
        checkpoint()
        library = ThorLibrary(request.construction.asset_library)
        original_names = set(source_prepared.graph.nodes)
        seen.add(layout_identity(source_prepared.graph, original_names))
        for round_index in range(request.budgets.max_rounds):
            budget.remaining_s()
            if result['attempted_count'] >= request.budgets.max_samples:
                result['reason'] = 'sample_budget_exhausted'
                break
            round_path = path/'contexts'/f'round_{round_index:03d}'
            round_path.mkdir(parents=True, exist_ok=False)
            contexts, rejected = sample_contexts(source_prepared.graph, request.construction, seed=request.seed+round_index)
            if forced_target is not None:
                supports = [e['args'][1] for e in source_prepared.graph.edges
                            if e['predicate'] == 'supported_by' and e['args'][0] == forced_target]
                if supports:
                    contexts = [crop_context(source_prepared.graph, forced_target, supports[0], request.construction.radius_m,
                                             max_nodes=request.construction.max_nodes)]
                forced_target = None
            write_json(round_path/'candidates.json', {'contexts': [c.to_dict() for c in contexts], 'rejected': rejected})
            if not contexts:
                feedback.append({'stage': 'context', 'reason': 'no_supported_editable_anchor_in_current_candidates'})
                checkpoint()
                continue
            try:
                for expansion in range(3):
                    plan = choose_strategy(template, contexts, gateway, request.construction,
                                         categories=library.categories(), feedback=feedback[-20:])
                    write_json(round_path/f'plan_{expansion}.json', plan.to_dict())
                    if plan.decision != 'expand':
                        break
                    c = next(c for c in contexts if c.context_id == plan.context_id)
                    contexts = [crop_context(source_prepared.graph, c.target, c.support, plan.radius_m,
                                max_nodes=request.construction.max_nodes, context_id=c.context_id)]
                if plan.decision != 'select':
                    feedback.append({'stage': 'strategy', 'reason': plan.decision, 'details': plan.rationale})
                    checkpoint()
                    continue
                c = next(c for c in contexts if c.context_id == plan.context_id)
                c = crop_context(source_prepared.graph, c.target, c.support, plan.radius_m,
                                 max_nodes=request.construction.max_nodes, context_id=c.context_id)
                prepared, observation = prepare_visible(source_prepared, c, request.construction, collection,
                    round_path/'initialization', seed=request.seed+round_index, deadline=budget.deadline,
                    settle_config=settle_config, view_config=view_config)
                context = crop_context(prepared.graph, c.target, c.support, plan.radius_m,
                                       max_nodes=request.construction.max_nodes, context_id=c.context_id)
                write_json(round_path/'local_graph.json', context.to_dict())
                write_json(round_path/'construction_plan.json', plan.to_dict())
                auxiliary = AuxiliaryViews(prepared.sim, prepared.graph, context, observation,
                    round_path/'initialization'/'observation'/'auxiliary', config=view_config, deadline=budget.deadline)
                if view_config.initial_aux_views:
                    auxiliary.ensure({'kind': 'aux_view', 'reason': '构造前的目标及操作侧布局观察',
                        'node_ids': [context.target], 'work_region_ids': [c['work_region_id'] for c in plan.contrast_spec],
                        'view_hint': 'overview' if request.case_type == 'case1.5' else 'auto'},
                        minimum_views=view_config.initial_aux_views)
                assets = library.retrieve(plan.asset_requests, round_path/'assets',
                    limit=request.construction.assets_per_category, seed=request.seed+round_index, deadline=budget.deadline)
                session = CaseEditSession(prepared, assets=assets, settle_config=settle_config, deadline=budget.deadline)
                for information_index in range(request.construction.max_information_requests+1):
                    result.update(stage='proposing', reason='running')
                    checkpoint()
                    decision, proposals, information = propose_edits(template, context.graph, gateway, context=context, plan=plan,
                        observation=observation, assets=assets.describe(), asset_categories=library.categories(),
                        count=request.budgets.proposals_per_round, feedback=feedback[-20:])
                    if decision != 'request_information':
                        break
                    request_record = {'stage': 'proposal_information', 'request': information.to_dict(), 'index': information_index}
                    if information_index == request.construction.max_information_requests:
                        request_record['resolution'] = {'status': 'information_insufficient', 'reason': 'information_request_budget_exhausted'}
                        feedback.append(request_record)
                        break
                    context, resolution = resolve_information(information, auxiliary=auxiliary, context=context,
                        prepared=prepared, config=request.construction, library=library, assets=assets,
                        path=round_path/'assets'/f'information_{information_index:03d}',
                        seed=request.seed+information_index, deadline=budget.deadline)
                    request_record['resolution'] = resolution
                    feedback.append(request_record)
                    plan = replace(plan, radius_m=context.radius_m)
                    write_json(round_path/'local_graph.json', context.to_dict())
                    checkpoint()
                write_json(round_path/'proposals.json', {'decision': decision, 'information_request': information.to_dict() if information else None,
                                                       'proposals': [p.to_dict() for p in proposals]})
                if decision != 'propose' or not proposals:
                    feedback.append({'stage': 'proposal', 'reason': 'information_insufficient' if information else decision})
                for proposal in proposals:
                    rejected_streak = 0
                    for _ in range(min(request.budgets.samples_per_proposal, proposal.sampling.get('max_samples', 16))):
                        budget.remaining_s()
                        if result['attempted_count'] >= request.budgets.max_samples:
                            break
                        index = result['attempted_count']
                        sample_id = f'sample_{index:06d}'
                        result['attempted_count'] += 1
                        sample_path = path/'samples'/sample_id
                        sample_path.mkdir(parents=True, exist_ok=False)
                        record = {'sample_id': sample_id, 'context_round': round_index, 'context_id': context.context_id,
                            'case_type': request.case_type, 'seed': request.seed, 'index': index,
                            'proposal': proposal.to_dict(), 'status': 'compiling', 'case_condition': 'unknown', 'task_completion': 'unknown'}
                        write_json(sample_path/'sample.json', record)
                        checkpoint()
                        try:
                            executable = compile_sample(proposal, template, prepared.graph, seed=request.seed,
                                                        index=index, sample_id=sample_id, assets=assets)
                            write_json(sample_path/'executable_edit.json', executable.to_dict())
                            trial = session.execute(template, proposal, executable)
                            record.update(trial.to_dict())
                            write_json(sample_path/'rule_checks.json', trial.to_dict())
                            if trial.after_graph is not None:
                                write_json(sample_path/'graph_after.json', trial.after_graph.to_dict())
                            if trial.status == 'pending_review':
                                identity = layout_identity(trial.after_graph, original_names)
                                record['layout_id'] = identity
                                if identity in seen:
                                    record.update(status='duplicate_or_unchanged', reason='same_final_layout')
                                else:
                                    packet = render_head_pair(prepared.sim, session.sim, trial.before_graph,
                                        trial.after_graph, observation, sample_path/'rgb', sample_id=sample_id,
                                        target=context.target, min_target_pixels=request.construction.min_target_pixels,
                                        config=view_config)
                                    packet = auxiliary.extend_pair(packet, session.sim, trial.after_graph, sample_path/'rgb')
                                    record['view_pair_id'] = packet['pair_id']
                                    if not packet['valid']:
                                        record.update(status='observation_rejected', reason='initial_state_drift_or_target_visibility')
                                    else:
                                        after_context = crop_context(trial.after_graph, context.target, context.support,
                                            context.radius_m, max_nodes=request.construction.max_nodes, context_id=context.context_id)
                                        for review_index in range(view_config.max_review_retries+1):
                                            record.update(status='pending_review')
                                            write_json(sample_path/'sample.json', record)
                                            result.update(stage='reviewing', reason='running')
                                            checkpoint()
                                            visual = review_edit(request, template, proposal, executable, trial, packet, gateway,
                                                plan=plan, context_before=context, context_after=after_context)
                                            write_json(sample_path/'reviews'/f'{review_index:03d}.json', visual.to_dict())
                                            if visual.information_request is None:
                                                break
                                            if review_index == view_config.max_review_retries:
                                                record['information_status'] = 'information_insufficient'
                                                break
                                            resolution = auxiliary.ensure(visual.information_request,
                                                after_sim=session.sim, after_graph=trial.after_graph)
                                            feedback.append({'stage': 'review_information', 'sample_id': sample_id,
                                                             'request': visual.information_request, 'resolution': resolution})
                                            packet = auxiliary.extend_pair(packet, session.sim, trial.after_graph, sample_path/'rgb')
                                            record['view_pair_id'] = packet['pair_id']
                                        write_json(sample_path/'semantic_review.json', visual.to_dict())
                                        record['review'] = visual.to_dict()
                                        if visual.verdict == 'pass':
                                            session.accept(visual, sample_path/'scene')
                                            write_json(sample_path/'task_validation'/'status.json', {
                                                'status': 'not_tested', 'reason': 'construction_scope_no_robot_motion',
                                                'planner': 'curobo_comparison_deferred', 'agent_assessment_is_not_verification': True,
                                                'base_motion_required': 'translation_and_yaw_allowed',
                                                'task_hypotheses': template.task_hypotheses})
                                            accepted.append(sample_id)
                                            seen.add(identity)
                                            record.update(status='construction_accepted', reason='rules_and_visible_semantics_passed_not_task_verified')
                                        else:
                                            record.update(status='information_insufficient' if visual.information_request else 'visual_rejected', reason=visual.reason)
                        except AgentFormatError as exc:
                            record.update(status='agent_protocol_error', reason=str(exc))
                            result.update(status='failed', reason='agent_protocol_error:' + str(exc))
                        except (SearchExhausted, ContextUnavailable, ValueError) as exc:
                            record.update(status='candidate_rejected', reason=type(exc).__name__ + ':' + str(exc)[:2000])
                        except BaseException as exc:
                            record.update(status='interrupted' if isinstance(exc, KeyboardInterrupt) else 'incomplete',
                                          reason=type(exc).__name__)
                            raise
                        finally:
                            write_json(sample_path/'sample.json', record)
                            session.reject()
                            feedback.append({'sample_id': sample_id, 'status': record['status'], 'reason': record.get('reason', ''),
                                'failed_checks': [c for c in record.get('checks', []) if c['required'] and c['status'] != 'pass'][:20],
                                'visual_checks': record.get('review', {}).get('checks', [])})
                            checkpoint()
                        if len(accepted) >= request.target_variant_count:
                            result.update(status='completed', reason='construction_target_reached_not_mobile_case_verified')
                            break
                        rejected_streak = 0 if record['status'] == 'construction_accepted' else rejected_streak+1
                        if rejected_streak >= 3 or record['status'] in ('observation_rejected', 'agent_protocol_error', 'information_insufficient'):
                            break
                    if result['status'] in ('completed', 'failed') or result['attempted_count'] >= request.budgets.max_samples:
                        break
            except TargetChangeRequested as exc:
                forced_target = exc.target
                feedback.append({'stage': 'target_change', 'requested_target': exc.target, 'resolution': 'queued_for_new_initialization'})
            except (VisibleInitializationUnavailable, ContextUnavailable, AgentFormatError) as exc:
                feedback.append({'stage': 'context_or_strategy', 'reason': type(exc).__name__ + ':' + str(exc)[:1000]})
                if isinstance(exc, AgentFormatError):
                    result.update(status='failed', reason='agent_protocol_error:' + str(exc))
            finally:
                if session:
                    session.close()
                    session = None
                if prepared:
                    prepared.close()
                    prepared = None
                checkpoint()
            if result['status'] in ('completed', 'failed'):
                break
        if result['status'] not in ('completed', 'failed') and result['attempted_count'] >= request.budgets.max_samples:
            result.update(status='budget_exhausted', reason='sample_budget_exhausted')
        elif result['status'] not in ('completed', 'failed'):
            result.update(status='budget_exhausted', reason='round_budget_exhausted')
    except (BudgetExhausted, TimeoutError) as exc:
        result.update(status='budget_exhausted', reason=str(exc))
    except NotImplementedError as exc:
        result.update(status='unsupported', reason=str(exc))
    except InitializationError as exc:
        result.update(status='source_preparation_failed', reason=str(exc))
        write_json(path/'source_preparation_error.json', {'reason': str(exc), 'details': getattr(exc, 'details', {})})
    except KeyboardInterrupt:
        result.update(status='interrupted', reason='user_interrupt')
    except AgentFormatError as exc:
        result.update(status='failed', reason='agent_protocol_error:' + str(exc))
    except Exception as exc:
        result.update(status='infrastructure_error', reason=type(exc).__name__ + ':' + str(exc)[:1000])
    finally:
        if session:
            session.close()
        if prepared:
            prepared.close()
        if source_prepared:
            source_prepared.close()
        checkpoint()
    return path
