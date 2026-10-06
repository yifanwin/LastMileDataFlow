"""Agent3: identity-bound paired-view semantic review; uncertain is not acceptance."""
from .case_gateway import COMMON
from ..construction.case_schema import Review


SYSTEM = COMMON + '''
Review the SIX actual paired RGB images and actual before/after graphs/rule results.
Same three cameras cover both endpoints: object displacement must not be hidden by
camera recentering. Return Review v0.1: sample_id(copied), verdict pass/fail/uncertain,
checks [{item:required_check_id,status:pass/fail/unknown,required:true,reason:text,
views:["before/top","after/top",...]}], unverified_claims:[text], reason:text.
If emitting review_version, it must be the exact string "0.1" (not "v0.1" or "1.0"); otherwise omit it.
Include EVERY required visual check ID exactly once; do not rename or omit checks.
For each claimed pass reference actual before/after views supporting that claim.
Use uncertain/unknown for occlusion, inadequate resolution or unavailable evidence.
Review original intent, not only the suggested strategy; geometric proxies alone do
not establish robot difficulty. Pending hypotheses stay unverified and nonblocking.
The intent check asks whether OBSERVABLE editing aligns with the requested phenomenon,
not whether pending robot hypotheses are proven. Do not require RGB to certify those.
Never change rules, thresholds or physical conclusions. No robot success fields.
A pass means only that visible semantic editing criteria align with the description.'''


def review_edit(request, template, proposal, executable, trial, packet, gateway):
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
