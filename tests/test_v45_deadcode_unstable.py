# -*- coding: utf-8 -*-
"""v0.45 死代码门的"扫描期间被改动"处理测试（SOP C4 的产物）。

背景：历史两次"红一次、再跑全绿、不复现"（v0.29 那例连用例名都没记、v0.38 台账 H82
那例）与 v0.43 已归因的并发假红属同一类——`deadcode-scan` 要读遍项目+兄弟仓库源码，
撞上别人正在写文件就会报出"死代码"。机制实验（`docs/reports/probe-scan-during-edit.py`）
已复现：高频改写一个文件 + 连跑 12 轮扫描，**9 轮报出且每轮都命中被改写的文件**。

本文件钉住由此新增的三处行为：
1. `unstable_files()`：戳不一致才算"被改动"（纯函数，可判真假）；
2. `scan()`：这些文件的发现进 `unstable_findings`，**不进** `found_total`；
3. CLI：有不可信发现时**不返回 0**，而是 **3（不确定/需重跑）**——不许把不确定当通过。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harvester.deadcode import (found_total, render_deadcode, scan,
                                unstable_files, unstable_total)

REPO = Path(__file__).resolve().parents[1]


class TestUnstableFiles(unittest.TestCase):

    def test_detects_changed_and_new_missing(self):
        before = {"a.py": (1, 10), "b.py": (2, 20), "c.py": (3, 30)}
        after = {"a.py": (1, 10), "b.py": (9, 99), "c.py": (3, 30)}
        self.assertEqual(unstable_files(before, after), ["b.py"])
        self.assertEqual(unstable_files(before, before), [])
        # 扫描后**消失**的文件同样算"不可信"（不是"干净"）
        self.assertEqual(unstable_files(before, {"a.py": (1, 10)}),
                         ["b.py", "c.py"])


class TestScanReportShape(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        pkg = self.root / "pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "mod.py").write_text(
            "# -*- coding: utf-8 -*-\nimport os\n\n\n"
            "def used():\n    return os.sep\n\n\nused()\n",
            encoding="utf-8", newline="\n")
        self.addCleanup(self.tmp.cleanup)

    def test_static_tree_has_no_unstable(self):
        r = scan(roots=("pkg",), base=self.root, report_root="pkg")
        self.assertEqual(r["unstable"], [])
        self.assertEqual(unstable_total(r), 0)
        self.assertEqual(found_total(r), 0)
        self.assertIn("未发现死代码", render_deadcode(r))

    def test_unstable_findings_are_excluded_from_found_total(self):
        """构造"只有不可信发现"的报告：`found_total` 必须是 0，但总数与提示必须可见。"""
        fake = {"files": 1, "roots": ["pkg"], "report_root": "pkg", "found": {},
                "missing": [], "unused_imports": [], "uncalled_functions": [],
                "unused_constants": [],
                "unstable": ["pkg/mod.py"],
                "unstable_findings": {"unused_imports": ["pkg/mod.py:9 os"],
                                      "uncalled_functions": [],
                                      "unused_constants": []}}
        self.assertEqual(found_total(fake), 0)          # 不算死代码
        self.assertEqual(unstable_total(fake), 1)       # 但也不是"干净"
        txt = render_deadcode(fake)
        self.assertIn("结论不可信", txt)
        self.assertIn("[不可信·未用 import]", txt)
        self.assertIn("未发现死代码", txt)
        self.assertIn("不确定，请重跑", txt)


class TestCliVerdict(unittest.TestCase):
    """CLI 判定：不可信 ≠ 通过。用打桩把"扫描期间被改动"做成确定性输入。"""

    def _fake_report(self) -> dict:
        return {"files": 1, "roots": ["harvester"], "report_root": "harvester",
                "found": {}, "missing": [], "unused_imports": [],
                "uncalled_functions": [], "unused_constants": [],
                "unstable": ["harvester/x.py"],
                "unstable_findings": {"unused_imports": ["harvester/x.py:3 os"],
                                      "uncalled_functions": [],
                                      "unused_constants": []}}

    def test_cli_returns_three_when_only_unstable(self):
        import harvester.cli as cli
        import harvester.deadcode as dc
        orig = dc.scan
        dc.scan = lambda **kw: self._fake_report()
        try:
            # 注意：cli 里**没有** parser 工厂（`main()` 内联建 parser），
            # 所以只能走 `main([...])` 并按它的返回值判（首版按 build_parser 写→error）
            rc = cli.main(["deadcode-scan", "--report-root", "harvester",
                           "--fail-on-found"])
        finally:
            dc.scan = orig
        self.assertEqual(rc, 3, "只有不可信发现时必须返回 3（不确定），不许返回 0")

    def test_cli_still_one_when_real_findings(self):
        import harvester.cli as cli
        import harvester.deadcode as dc
        orig = dc.scan
        rep = self._fake_report()
        rep["unused_imports"] = ["harvester/y.py:5 json"]
        dc.scan = lambda **kw: rep
        try:
            rc = cli.main(["deadcode-scan", "--report-root", "harvester",
                           "--fail-on-found"])
        finally:
            dc.scan = orig
        self.assertEqual(rc, 1, "真有死代码仍须判 1（别被新分支吃掉）")


if __name__ == "__main__":
    unittest.main()
