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
        entry=(root/'bin/lastmile-dataflow').read_text()
        self.assertNotIn('setup_env.sh',entry)
        self.assertNotIn('lastmile_pipeline',entry)


if __name__=='__main__':unittest.main()
