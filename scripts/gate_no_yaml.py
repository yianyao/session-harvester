# -*- coding: utf-8 -*-
"""无 PyYAML 门禁：先钉死 yaml 不可导入，再跑全套件。

**判据：`failures=0`**（这是唯一的硬判据）。`errors=N` 是**设计行为**——
按 H9 口径，依赖 PyYAML 的模块在导入期**显式失败**（不降级、不静默跳过），
所以无 PyYAML 时它们整体计成 error。

**这里不写 `ran=` / `errors=` 的期望值**：它们是会随套件增减漂移的数字，
写死在 docstring 里就会腐烂（旧值 `Ran 573 / errors=59` 曾长期与实际不符）。
**唯一权威的数字在 `README.md` 顶部「测试基线」一节**，且由
`tests/test_v48_test_baseline.py` 实测门禁输出与 README 逐数字对账钉住。
"""
import os
import sys
import unittest

#: 防自递归：`tests/test_v48_test_baseline.py` 会**真跑本脚本**来核对 README 的数字。
#: 若不设这一位，本脚本跑套件 → 那个测试又跑本脚本 → 无限递归（实测跑不完）。
os.environ["HARVESTER_BASELINE_NESTED"] = "1"


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
