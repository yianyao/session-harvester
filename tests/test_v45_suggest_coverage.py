# -*- coding: utf-8 -*-
"""v0.45 建议池 ↔ 台账覆盖核对测试（SOP C2）。

判据（都能说清何时会红）：
- 台账里有、建议池里没有的键 → 必须进 `stale`（建议句被改写后没清台账，
  这类"数字在涨但其实对不上"最容易蒙混过去）；
- 建议池里有、台账里没有/仍 pending → 必须进 `undecided`；
- 已裁决的必须出现在 `decided`，且**不许**同时出现在 `undecided`。
"""
from __future__ import annotations

import unittest

from harvester.agent_suggest import coverage_report, render_coverage

ENTRIES = [{"title": "建议甲。"}, {"title": "建议乙。"}, {"title": "建议丙。"}]


class TestCoverageReport(unittest.TestCase):

    def test_three_classes_are_separated(self):
        statuses = {"建议甲。": "adopted", "建议乙。": "pending",
                    "已退休的建议。": "rejected"}
        c = coverage_report(ENTRIES, statuses)
        self.assertEqual(c["suggestions"], 3)
        self.assertEqual(c["ledger"], 3)
        self.assertEqual(c["decided"], ["建议甲。"])
        # sorted() 按码点：丙(U+4E19) < 乙(U+4E59)——顺序是**确定**的，别按直觉写
        self.assertEqual(c["undecided"], ["建议丙。", "建议乙。"])
        self.assertEqual(c["stale"], ["已退休的建议。"])
        self.assertEqual(c["ledger_counts"], {"adopted": 1, "pending": 1,
                                              "rejected": 1})

    def test_empty_ledger_means_everything_pending(self):
        c = coverage_report(ENTRIES, {})
        self.assertEqual(c["decided"], [])
        self.assertEqual(len(c["undecided"]), 3)
        self.assertEqual(c["stale"], [])
        self.assertEqual(c["ledger_counts"], {})

    def test_ledger_key_not_in_pool_is_stale_not_undecided(self):
        """反向对照：陈旧键**不许**混进待裁决（否则人会去裁决一条不存在的建议）。"""
        c = coverage_report(ENTRIES, {"幽灵建议。": "pending"})
        self.assertEqual(c["stale"], ["幽灵建议。"])
        self.assertNotIn("幽灵建议。", c["undecided"])

    def test_render_states_the_human_boundary(self):
        txt = render_coverage(coverage_report(ENTRIES, {"建议甲。": "adopted"}))
        self.assertIn("待裁决", txt)
        self.assertIn("台账陈旧", txt)
        self.assertIn("裁决本身仍由人做", txt)


if __name__ == "__main__":
    unittest.main()
