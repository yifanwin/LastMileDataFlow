"""Synthetic fixtures only: no claim of real house/RBY-1 verification."""
from pathlib import Path
from helpers import fixtures
from lastmile_dataflow.construction.case_schema import CaseTemplate
from lastmile_dataflow.construction.dsl import SymbolicDSL


def table_scene(root):
    source, robot = fixtures(root)
    Path(source.xml_path).write_text('''<mujoco><compiler angle="radian"/>
    <worldbody><light pos="2 0 3" dir="0 0 -1" diffuse=".8 .8 .8"/><geom name="floor" type="plane" size="0 0 .01"/>
    <body name="table" pos="2 0 .4"><geom name="table_top" type="box" size=".6 .5 .04" rgba=".7 .6 .4 1"/></body>
    <body name="target" pos="2 0 .50"><freejoint name="target_free"/><geom name="target_geom" type="box" size=".04 .04 .05" rgba=".9 .1 .1 1" mass=".1"/></body>
    <body name="neighbor" pos="2.3 .2 .50"><freejoint name="neighbor_free"/><geom name="neighbor_geom" type="box" size=".04 .04 .05" rgba=".1 .2 .9 1" mass=".1"/></body>
    </worldbody></mujoco>''')
    return source, robot


def template():
    return CaseTemplate.from_dict({'case_type': 'layout', 'intent': 'Change the supported layout',
        'roles': {'target': {'type': 'object'}, 'support': {'type': 'surface'}},
        'requirements': [{'predicate': 'supported_by', 'args': ['$target', '$support'], 'value': True}],
        'invariants': [{'predicate': 'supported', 'args': ['$target'], 'value': True}],
        'semantic_checks': ['The layout changed visibly']})


def proposal(x=None):
    xy = {'mode': 'uniform', 'margin': .01}
    if x is not None:
        xy.update(x=[x, x], y=[-.2, -.2])
    return SymbolicDSL('p1', {'target': 'target', 'support': 'table'},
        [{'op': 'move', 'subject': '$target', 'search_space': {'support': '$support', 'xy': xy}}])
