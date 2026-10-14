"""静态检查新运行代码不依赖旧管线或 MolmoSpaces Python 包。"""
import ast
from pathlib import Path
import unittest


class IndependenceTests(unittest.TestCase):
    def test_no_old_package_imports_or_environment_script(self):
        root=Path(__file__).resolve().parents[1]
        banned={'lastmile','molmo_spaces','molmospaces'}
        for path in (root/'src').rglob('*.py'):
            tree=ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node,ast.Import):names=[a.name for a in node.names]
                elif isinstance(node,ast.ImportFrom):names=[node.module or '']
                else:continue
                for name in names:
                    self.assertNotIn(name.split('.')[0],banned,msg=str(path))
        import tomllib
        project = tomllib.loads((root / 'pyproject.toml').read_text())
        self.assertEqual(project['project']['scripts']['lastmile-dataflow'], 'lastmile_dataflow.cli:main')
        self.assertFalse((root / 'bin/lastmile-dataflow').exists())

    def test_installed_module_starts_outside_repository_without_pythonpath(self):
        import os
        import subprocess
        import sys
        import tempfile
        environment = dict(os.environ)
        environment.pop('PYTHONPATH', None)
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-m', 'lastmile_dataflow.cli', '--help'],
                cwd=directory, env=environment, capture_output=True, text=True, check=True)
        self.assertIn('evaluate-stations', result.stdout)
        self.assertNotIn('collect-no-edit', result.stdout)


if __name__=='__main__':unittest.main()
