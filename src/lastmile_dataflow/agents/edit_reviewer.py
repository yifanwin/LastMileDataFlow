"""Agent4: identity-bound paired-view semantic review; uncertain is not acceptance."""
from .prompts import REVIEWER
from ..construction.case_schema import require
from ..construction.case_schema import Review
from ..construction.case_schema import CheckResult
from .contracts import ConstructionReview, validate_information
from ..scenes.local_context import agent_context


SYSTEM = REVIEWER


def review_edit(request, template, proposal, executable, trial, packet, gateway, *,
                plan=None, context_before=None, context_after=None):
    if context_before is not None:
        return _review_head(request, template, plan, proposal, executable, trial, context_before, context_after, packet, gateway)
    if trial.status != 'pending_review' or packet['sample_id'] != executable.sample_id:
        raise ValueError('review requires matching rule-valid pending candidate')
    if packet['before_graph_id'] != trial.before_graph.to_dict()['graph_id'] or packet['after_graph_id'] != trial.after_graph.to_dict()['graph_id']:
        raise ValueError('stale paired views')
    view_names = {image['view'] for image in packet['images']}
    expected_views = {s+'/'+v for s in ('before', 'after') for v in ('top', 'oblique_a', 'oblique_b')}
    if view_names != expected_views or len(packet['images']) != 6 or any(i.get('pair_id') != packet['pair_id'] for i in packet['images']):
        raise ValueError('six identity-bound paired views required')
    expected = {'intent', *('semantic:' + str(i) for i in range(len(template.semantic_checks)))}
    def parse(value):
        review = Review.from_dict(value)
        if review.sample_id != executable.sample_id:
            raise ValueError('stale review sample_id')
        if {c.item for c in review.checks} != expected or len(review.checks) != len(expected):
            raise ValueError('missing or duplicate required visual checks')
        for check in review.checks:
            if not check.required or not set(check.views) <= view_names:
                raise ValueError('required checks or view refs invalid')
            if check.status == 'pass' and (not any(v.startswith('before/') for v in check.views) or not any(v.startswith('after/') for v in check.views)):
                raise ValueError('pass needs before and after evidence')
        return review
    return gateway.call('reviewer', SYSTEM, {'sample_id': executable.sample_id,
        'case_description': request.case_description, 'template': template.to_dict(),
        'proposal': proposal.to_dict(), 'executable': executable.to_dict(),
        'before_graph': trial.before_graph.to_dict(), 'after_graph': trial.after_graph.to_dict(),
        'rule_checks': [c.to_dict() for c in trial.checks], 'pair_id': packet['pair_id'],
        'camera_rig': packet['rig'], 'camera_rigs': packet.get('view_rigs', {}),
        'framing': packet.get('framing', 'fixed_affected_union'), 'required_visual_checks': {'intent': request.case_description,
         **{'semantic:' + str(i): text for i, text in enumerate(template.semantic_checks)}}},
        parse, images=packet['images'])


def _review_head(request, template, plan, proposal, executable, trial, context_before, context_after, packet, gateway):
    require(trial.status == 'pending_review' and packet['valid'], 'review', 'valid paired pending trial required')
    require(packet['sample_id'] == executable.sample_id, 'sample_id', 'stale paired images')
    require(packet['before_graph_id'] == trial.before_graph.to_dict()['graph_id']
            and packet['after_graph_id'] == trial.after_graph.to_dict()['graph_id'], 'graphs', 'stale graph pair')
    views = {i['view'] for i in packet['images']}
    require({'before/head', 'after/head'} <= views and len(views) == len(packet['images'])
            and all(i['pair_id'] == packet['pair_id'] for i in packet['images']), 'images', 'identity-bound paired views required')
    require({v.split('/', 1)[1] for v in views if v.startswith('before/')} ==
            {v.split('/', 1)[1] for v in views if v.startswith('after/')}, 'images', 'unpaired auxiliary view')
    expected = {'layout:' + str(i): s for i, s in enumerate(template.semantic_checks)}
    def parse(value):
        return parse_layout_review(value, sample_id=executable.sample_id, expected=expected, views=views,
                                   template=template, context_after=context_after)
    return gateway.call('reviewer', REVIEWER, {'sample_id': executable.sample_id,
        'case_description': request.case_description, 'template': template.to_dict(), 'plan': plan.to_dict(),
        'proposal': proposal.to_dict(), 'executable': executable.to_dict(), 'before_context': agent_context(context_before),
        'after_context': agent_context(context_after), 'rule_checks': [{
            'item': c.item, 'status': c.status, 'required': c.required, 'reason': c.reason,
            'value': c.value if isinstance(c.value, (str, float, int, bool)) else None} for c in trial.checks],
        'pair': {k: v for k, v in packet.items() if k != 'images'}, 'required_visual_checks': expected,
        'review_scope': 'visible_construction_only', 'view_registry': packet['images'],
        'task_hypotheses_not_acceptance_checks': template.task_hypotheses},
        parse, images=packet['images'])


def parse_layout_review(value, *, sample_id, expected, views, template, context_after):
    require(set(value['checks']) == set(expected), 'checks', 'each layout ID required exactly once')
    checks = []
    for key, item in value['checks'].items():
        references = set(item['views'])
        require(references <= views, 'views', 'invalid evidence references')
        paired = {v.split('/', 1)[1] for v in references if v.startswith('before/')} & {
            v.split('/', 1)[1] for v in references if v.startswith('after/')}
        require(item['status'] != 'pass' or bool(paired), 'views', 'pass needs matching before/after view')
        checks.append(CheckResult(item=key, required=True, **item))
    information = value['information_request']
    if information is not None:
        information = validate_information(information, context_after).to_dict()
        require(information['kind'] == 'aux_view', 'information_request', 'reviewer may only request paired auxiliary views')
        require(any(c.status == 'unknown' for c in checks), 'checks', 'information request needs an unknown check')
    statuses = {c.status for c in checks}
    verdict = 'fail' if 'fail' in statuses else 'uncertain' if 'unknown' in statuses else 'pass'
    return ConstructionReview(sample_id, verdict, checks,
        unverified_claims=list(template.task_hypotheses), reason='; '.join(c.reason for c in checks),
        information_request=information, agent_assessment=value['agent_assessment'])
