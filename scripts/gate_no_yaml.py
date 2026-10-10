# -*- coding: utf-8 -*-
"""无 PyYAML 门禁：先钉死 yaml 不可导入，再跑全套件。

口径（项目交接）：预期 Ran 573 / errors=59，**必须 0 failures**
——failures 说明有人把「环境缺依赖」写成了断言（H9）。
"""
import sys
import unittest


class _NoYaml:
    def find_module(self, name, path=None):  # pragma: no cover - 旧接口
        return None

    def find_spec(self, name, path=None, target=None):
        if name == "yaml" or name.startswith("yaml."):
            raise ModuleNotFoundError(f"blocked for gate test: {name}")
        return None


sys.meta_path.insert(0, _NoYaml())
for mod in [m for m in sys.modules if m == "yaml" or m.startswith("yaml.")]:
    del sys.modules[mod]

loader = unittest.TestLoader()
suite = loader.discover("tests")
runner = unittest.TextTestRunner(verbosity=0)
res = runner.run(suite)
print(f"GATE ran={res.testsRun} failures={len(res.failures)} "
      f"errors={len(res.errors)} skipped={len(res.skipped)}")
sys.exit(1 if res.failures else 0)
