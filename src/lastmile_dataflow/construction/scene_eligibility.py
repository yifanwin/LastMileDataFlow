"""Explicit semantic eligibility, not measured policy success probabilities."""
import re

PICK_TYPES = ('cup', 'mug', 'wine bottle', 'atomizer', 'salt shaker', 'pepper shaker', 'soap dispenser')
SUPPORT_TYPES = ('CounterTop', 'Table', 'DiningTable', 'CoffeeTable', 'SideTable', 'Desk')


def normalized(value):
    return re.sub(r'[^a-z0-9]', '', str(value).lower())


PICK_ALIASES = {
    'cup': 'cup', 'mug': 'mug', 'winebottle': 'wine bottle',
    'atomizer': 'atomizer', 'spraybottle': 'atomizer',
    'saltshaker': 'salt shaker', 'peppershaker': 'pepper shaker',
    'soapdispenser': 'soap dispenser', 'soapbottle': 'soap dispenser',
}


def pick_type(task):
    # No generic Bottle, SoapBar, or asset-name substring fallback.
    return PICK_ALIASES.get(normalized(task.get('category')))


def support_category(sim, support_id):
    return next((r.get('category') for r in sim.catalog if r.get('mjcf_body') == support_id), None)


def allowed_case1_support(sim, support_id, allowed=SUPPORT_TYPES):
    category = support_category(sim, support_id)
    return normalized(category) in {normalized(t) for t in allowed}
