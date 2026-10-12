# -*- coding: utf-8 -*-
"""测试基线门（B8 / 外部评审 v5 第 5 条）：**README 的基线数字必须与实测一致**。

为什么值得钉：README 顶部此前写着 `529 例全绿` / `Ran 469 / errors=34`，
而 `scripts/gate_no_yaml.py` 的 docstring 里又写着**第三种**数字
（`Ran 573 / errors=59`）——三处互不相同、全都没人量过。读者按哪一处都会得出
错误判据；而"照 README 跑一遍、看结果对不对"这件事**此前从未被测**（文档腐坏典型形态）。

判据（说清何时会红）：
- README 的 `Ran N tests` ≠ **实测**套件例数 → **加/删测试却忘了改 README**，红；
- README 的 `GATE ran=/errors=/skipped=` ≠ 门禁**实测**输出 → 红；
- README 删掉 `failures=0` 这条判据 → 红（它是无 PyYAML 门禁的唯一硬判据）；
- 解析器（`parse_ran`/`parse_gate`/`readme_baseline`）对坏输入必须抛/取值
  → 元测试证明它们不恒真。

## 自递归：必须靠环境位守住（不守就是套件爆炸）

本文件**真跑套件**，而套件里又包含本文件 → 不守就是
"套件→本文件→套件→…"无限递归（实测跑不完）。两道守卫：

1. `run_suite()` / `run_gate()` 给子进程设 **`HARVESTER_BASELINE_NESTED=1`**；
2. `scripts/gate_no_yaml.py` 自己也设这一位（门禁**绝不会**去跑门禁）。

带这一位时，本文件的**两条实测用例**转成 `skip`（`@skipUnless`）——
它们是**这一层无法自证**的，不是"通过"：

- **谁是权威**：README 的数字描述的是**顶层一次**套件运行（由人 / 开工协议在
  顶层跑 `unittest discover -s tests` 与 `scripts/gate_no_yaml.py` 后核对）。
  在**嵌套**的这一层里，例数已经被"本文件自己的两条 skip"改写，
  所以拿它去比对 README 既不成立、也无意义。
- **所以**：嵌套层只跑元测试与 README 结构断言（便宜、无副作用），
  实测对账只在顶层发生。代价是"嵌套层里的失败点"要等下一次顶层跑才暴露
  ——而顶层跑正是基线自查本身，不会漏。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
README = REPO / "README.md"
GATE = REPO / "scripts" / "gate_no_yaml.py"

#: "我已在嵌套子进程里"标记（见模块 docstring 的自递归守卫）
NESTED_FLAG = "HARVESTER_BASELINE_NESTED"


def parse_ran(text: str) -> int:
    """从 unittest 输出里取 `Ran N tests` 的 N（取不到 → 抛，别返回 0）。"""
    m = re.findall(r"^Ran (\d+) tests?", text, re.M)
    if not m:
        raise AssertionError(f"输出里找不到 `Ran N tests`：{text[-800:]!r}")
    if len(set(m)) != 1:
        raise AssertionError(f"输出里有多个不同的例数 {m}（输出拼错了？）")
    return int(m[0])


def parse_gate(text: str) -> dict[str, int]:
    """从门禁的 `GATE ran=.. failures=.. errors=.. skipped=..` 行取值。"""
    m = re.search(r"^GATE ran=(\d+) failures=(\d+) errors=(\d+) skipped=(\d+)$",
                  text, re.M)
    if not m:
        raise AssertionError(f"门禁输出里找不到 GATE 行：{text[-800:]!r}")
    return dict(zip(("ran", "failures", "errors", "skipped"),
                    (int(g) for g in m.groups())))


def readme_baseline(text: str) -> dict[str, int]:
    """从 README 顶部「测试基线」一节抽它**声称**的数字。

    刻意做成"找不到就抛"——README 若把声明删了，这条门必须红，
    而不是静默退化成"没得比、算通过"。
    """
    ran = re.search(r"Ran (\d+) tests", text)
    gate = re.search(r"GATE ran=(\d+) failures=(\d+) errors=(\d+) skipped=(\d+)", text)
    if not ran or not gate:
        raise AssertionError(
            "README 顶部缺少基线声明：需要 `Ran N tests` 与 "
            "`GATE ran=.. failures=.. errors=.. skipped=..` 两行")
    out = {"suite": int(ran.group(1))}
    out.update(dict(zip(("gate_ran", "gate_failures", "gate_errors", "gate_skipped"),
                        (int(g) for g in gate.groups()))))
    return out


def _env(nested: bool = False) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    if nested:
        env[NESTED_FLAG] = "1"      # 让子进程里的本文件不再往下递归
    else:
        # 显式清掉：本进程若**恰好**是在嵌套层里跑的（自身带这一位），
        # 子进程也必须是"顶层"语义——否则 `test_plain_env_has_no_flag`
        # 会因为**继承**了环境位而假红（首版就是这么错的）。
        env.pop(NESTED_FLAG, None)
    return env


def run_suite() -> tuple[int, str]:
    """真跑全量套件（与仓库基线自查同一条命令），返回 (例数, 输出)。"""
    r = subprocess.run(
        [sys.executable, "-X", "utf8", "-W", "ignore::ResourceWarning",
         "-m", "unittest", "discover", "-s", "tests"],
        cwd=str(REPO), env=_env(nested=True), capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    text = (r.stdout or "") + (r.stderr or "")
    return parse_ran(text), text


def run_gate() -> tuple[dict[str, int], str]:
    """真跑无 PyYAML 门禁脚本，返回 (计数, 输出)。"""
    r = subprocess.run([sys.executable, "-X", "utf8", str(GATE)],
                       cwd=str(REPO), env=_env(nested=True), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    text = (r.stdout or "") + (r.stderr or "")
    return parse_gate(text), text


class TestParsersCanFail(unittest.TestCase):
    """元测试：解析器必须能红，否则下面的对账可能是"抽不到就算过"。"""

    def test_parse_ran_rejects_garbage(self):
        with self.assertRaises(AssertionError):
            parse_ran("没有任何例数的一行\n")
        with self.assertRaises(AssertionError):
            parse_ran("Ran 3 tests\nRan 4 tests\n")   # 两个不同的数 → 不许静默取一个
        self.assertEqual(parse_ran("Ran 12 tests in 3.4s\nOK\n"), 12)

    def test_parse_gate_rejects_garbage(self):
        with self.assertRaises(AssertionError):
            parse_gate("GATE 没有这行\n")
        self.assertEqual(
            parse_gate("GATE ran=7 failures=0 errors=1 skipped=2\n"),
            {"ran": 7, "failures": 0, "errors": 1, "skipped": 2})

    def test_readme_baseline_rejects_missing_declaration(self):
        with self.assertRaises(AssertionError):
            readme_baseline("# 没有基线声明的 README\n")
        got = readme_baseline("Ran 5 tests\nGATE ran=4 failures=0 errors=1 skipped=0\n")
        self.assertEqual(got["suite"], 5)
        self.assertEqual(got["gate_ran"], 4)


class TestNestingGuard(unittest.TestCase):
    """守卫本身要有钉子：改了 `_env` 不传位 → 这条红（否则就是无声爆炸）。"""

    def test_nested_env_sets_flag(self):
        self.assertEqual(_env(nested=True)[NESTED_FLAG], "1")

    def test_plain_env_drops_inherited_flag(self):
        """`nested=False` 必须**显式清掉**继承来的位。

        首版没清，于是本文件在嵌套层里被跑时 `test_plain_env_has_no_flag`
        假红（实测踩到：嵌套子进程自身带着这一位）。这条就是那颗钉子。
        """
        os.environ[NESTED_FLAG] = "1"       # 假装本进程自己就在嵌套层里
        try:
            self.assertNotIn(NESTED_FLAG, _env(nested=False))
            self.assertEqual(_env(nested=True)[NESTED_FLAG], "1")
        finally:
            os.environ.pop(NESTED_FLAG, None)

    def test_gate_script_sets_the_same_flag(self):
        """门禁脚本必须自己设位（否则 `run_gate()` 会递归回自己）。"""
        src = GATE.read_text(encoding="utf-8")
        self.assertIn(NESTED_FLAG, src,
                      "scripts/gate_no_yaml.py 必须设 HARVESTER_BASELINE_NESTED"
                      "（防「门禁跑门禁」的自递归）")


class TestReadmeDeclarations(unittest.TestCase):
    """README 结构断言（嵌套层也跑：便宜、无副作用）。"""

    def test_readme_declares_the_hard_criterion(self):
        """`failures=0` 必须写在 README 里：它是无 PyYAML 门禁的唯一硬判据。"""
        text = README.read_text(encoding="utf-8")
        self.assertRegex(text, r"GATE ran=\d+ failures=0 errors=\d+ skipped=\d+")
        self.assertIn("只看 `failures=0`", text,
                      "README 必须写清门禁的硬判据是 failures=0（errors 是设计行为）")

    def test_readme_numbers_are_parseable(self):
        """README 的基线数字必须可解析（抽不到 → 红，而不是"没得比算过"）。"""
        got = readme_baseline(README.read_text(encoding="utf-8"))
        self.assertGreater(got["suite"], 100)
        self.assertGreater(got["gate_ran"], 100)
        self.assertEqual(got["gate_failures"], 0)


class TestBaselineMatchesReadme(unittest.TestCase):
    """实测 ↔ README 对账（**真门**；只在顶层跑，理由见模块 docstring）。"""

    @unittest.skipUnless(not os.environ.get(NESTED_FLAG),
                         "嵌套子进程里跳过实测（防自递归）；顶层跑时必执行")
    def test_live_suite_count_matches_readme(self):
        """加/删测试后忘了改 README 的 `Ran N tests` → 这里红。"""
        claimed = readme_baseline(README.read_text(encoding="utf-8"))["suite"]
        got, text = run_suite()
        self.assertNotIn("FAILED (", text,
                         f"全量套件并非全绿（README 声称 {claimed} 例 OK）：\n"
                         f"{text[-3000:]}")
        self.assertEqual(
            got, claimed,
            f"README 声称 `Ran {claimed} tests`，实测 {got} 例"
            "——加/删测试后必须同步 README 顶部「测试基线」一节。")

    @unittest.skipUnless(not os.environ.get(NESTED_FLAG),
                         "嵌套子进程里跳过实测（防自递归）；顶层跑时必执行")
    def test_live_gate_counts_match_readme(self):
        """门禁的 ran/errors/skipped 与 README 不一致 → 红（failures 必须 0）。"""
        claimed = readme_baseline(README.read_text(encoding="utf-8"))
        got, text = run_gate()
        self.assertEqual(got["failures"], 0,
                         "无 PyYAML 门禁出现 failures（H9：缺依赖必须长成 error，"
                         f"不许写成断言失败）：\n{text[-3000:]}")
        for k in ("ran", "errors", "skipped"):
            self.assertEqual(
                got[k], claimed[f"gate_{k}"],
                f"README 声称 gate {k}={claimed[f'gate_{k}']}，实测 {got[k]}")


if __name__ == "__main__":
    unittest.main()
