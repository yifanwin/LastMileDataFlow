"""Import direction of the pipeline layout: a module may only import lower layers (docs/architecture.md)."""

import ast
from pathlib import Path
import unittest

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "lastmile_dataflow"
NAME = "lastmile_dataflow"

# Lowest first. A module may import its own layer and lower ones; inside operation and
# station_map the groups are ordered the same way.
LAYERS = [
    ("utils",),
    ("sim.model",),
    ("robots",),
    ("sim",),
    ("planning",),
    ("operation",),
    ("policies",),
    ("scenes",),
    ("station_map",),
    ("lastmile_dataset", "lastmile_benchmark"),
]
GROUPS = {
    "operation": [
        ("episode", "protocol", "config"),
        ("outcomes",),
        ("control",),
        ("recording",),
        ("preparation", "attempt", "curobo"),
    ],
    "station_map": [("config", "sampling", "aggregate", "heatmap"), ("planner_trials", "rl")],
}
# Parallel parts of one layer never import each other.
PEERS = [("station_map.planner_trials", "station_map.rl"), ("lastmile_dataset", "lastmile_benchmark")]


def module_name(path):
    parts = list(path.relative_to(PACKAGE).with_suffix("").parts)
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts), parts[-1] == "__init__"


def within(module, prefix):
    return module == prefix or module.startswith(prefix + ".")


def layer(module):
    found = [(i, p) for i, prefixes in enumerate(LAYERS) for p in prefixes if within(module, p)]
    return max(found, key=lambda item: len(item[1]))[0]


def group(module):
    top, _, rest = module.partition(".")
    for index, names in enumerate(GROUPS.get(top, [])):
        if rest.split(".")[0] in names:
            return index
    return None


def imports(path):
    module, package = module_name(path)
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = module.split(".") if package else module.split(".")[:-1]
                base = base[: len(base) - node.level + 1]
                target = ".".join(base + ([node.module] if node.module else []))
            elif node.module and within(node.module, NAME):
                target = node.module[len(NAME) + 1 :]
            else:
                continue
            for alias in node.names:
                as_module = f"{target}.{alias.name}"
                exists = (PACKAGE / as_module.replace(".", "/")).with_suffix(".py").exists()
                yield node.lineno, as_module if exists else target
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if within(alias.name, NAME):
                    yield node.lineno, alias.name[len(NAME) + 1 :]


def violation(source, target):
    if not target or not source:
        return None
    for left, right in PEERS:
        if within(source, left) and within(target, right) or within(source, right) and within(target, left):
            return "parallel parts import each other"
    if layer(target) > layer(source):
        return "imports a higher layer"
    if layer(target) == layer(source) and source.split(".")[0] == target.split(".")[0]:
        mine, theirs = group(source), group(target)
        if mine is not None and theirs is not None and theirs > mine:
            return "imports a later group of its package"
    if layer(target) == layer(source) and source.split(".")[0] != target.split(".")[0]:
        return "imports a parallel package"
    return None


class LayerTests(unittest.TestCase):
    def test_imports_point_down(self):
        problems = []
        for path in sorted(PACKAGE.rglob("*.py")):
            source, _ = module_name(path)
            if within(source, "policies.molmobot.multitask") or within(source, "policies.pi05.core"):
                continue
            for line, target in imports(path):
                reason = violation(source, target)
                if reason:
                    problems.append(f"{path.relative_to(PACKAGE)}:{line} {target}: {reason}")
        self.assertEqual(problems, [])

    def test_package_markers_are_empty(self):
        filled = [str(p.relative_to(PACKAGE)) for p in PACKAGE.rglob("__init__.py") if p.read_text().strip()]
        filled = [p for p in filled if not p.startswith(("policies/molmobot/multitask", "policies/pi05/core"))]
        self.assertEqual(filled, [])


if __name__ == "__main__":
    unittest.main()
