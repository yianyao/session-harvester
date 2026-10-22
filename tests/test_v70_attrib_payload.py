# -*- coding: utf-8 -*-
"""P6-9 归因台（数据面）：`attrib` 机读载荷的覆盖度与等级。"""
from __future__ import annotations

import json
import unittest

from harvester.attrib import (JSON_SCHEMA, attribution_coverage, render_brief,
                              render_json, _summary_level)


def _summary(**kw):
    base = {"patterns_total": 67, "patterns_attributed": 19,
            "patterns_unattributed": 48, "errors_total": 532,
            "errors_attributed": 401, "errors_unattributed": 131,
            "rules_total": 7, "rules_hit": 7}
    base.update(kw)
    return base


def _result(summary=None, reviewed=None):
    s = summary or _summary()
    return {"summary": s, "by_pattern": {
        "ABORTED": {"pattern": "ABORTED", "class": "unclassified", "count": 1,
                    "given_up": 1, "tools": ["pwsh"],
                    "anchors": [{"sid": "s1", "seq": 1, "error": "ABORTED"}],
                    "rule": None, "rule_ids": [], "rule_ids_conflict": False}},
        "reviewed": reviewed or []}


class TestCoverage(unittest.TestCase):
    def test_pct_is_computed_not_hardcoded(self):
        c = attribution_coverage(_summary())
        self.assertEqual(c["pct_patterns"], 28.4)   # 19/67
        self.assertEqual(c["pct_errors"], 75.4)     # 401/532

    def test_line_carries_both_denominators(self):
        line = attribution_coverage(_summary())["line"]
        self.assertIn("19 / 67", line)
        self.assertIn("401 / 532", line)
        self.assertIn("28.4%", line)
        self.assertIn("75.4%", line)

    def test_unattributed_part_is_named_not_hidden(self):
        line = attribution_coverage(_summary())["line"]
        self.assertIn("未归因的 48 个模式", line)
        self.assertIn("131 条错误", line)
        self.assertIn("不在本面", line)

    def test_full_coverage_never_says_all_covered(self):
        line = attribution_coverage(_summary(
            patterns_attributed=67, patterns_unattributed=0,
            errors_attributed=532, errors_unattributed=0))["line"]
        self.assertNotIn("全部归因", line)
        self.assertNotIn("已全覆盖", line)
        self.assertIn("候选仍会错", line)

    def test_zero_totals_do_not_divide_by_zero(self):
        c = attribution_coverage({})
        self.assertEqual(c["pct_patterns"], 0.0)
        self.assertEqual(c["pct_errors"], 0.0)
        self.assertIn("0 / 0", c["line"])

    def test_counts_are_int_coerced(self):
        c = attribution_coverage({"patterns_total": "10",
                                  "patterns_attributed": "5"})
        self.assertEqual(c["patterns_total"], 10)
        self.assertEqual(c["pct_patterns"], 50.0)

    def test_line_text_is_plain_and_says_same_numbers(self):
        """页面显示纯文本形态：不许把 Markdown 标记漏到 HTML 上，
        且两个形态必须说同一组数字（否则就是"前端去星号"的第二套口径）。"""
        c = attribution_coverage(_summary())
        self.assertNotIn("**", c["line_text"])
        self.assertNotIn("`", c["line_text"])
        for frag in ("19 / 67", "401 / 532", "28.4%", "75.4%", "不在本面"):
            self.assertIn(frag, c["line_text"])


class TestJsonPayloadForWorkbench(unittest.TestCase):
    def test_json_carries_coverage_and_reviewable(self):
        doc = json.loads(render_json(_result()))
        self.assertEqual(doc["schema"], JSON_SCHEMA)
        self.assertIn("coverage", doc, "归因台要用现算的覆盖度，页面不自己拼")
        self.assertIn("line", doc["coverage"])
        self.assertIn("reviewable", doc, "等级必须由后端算（页面只显示）")
        self.assertEqual(doc["reviewable"]["level"], "R3")

    def test_json_and_brief_level_agree(self):
        res = _result()
        doc = json.loads(render_json(res))
        self.assertEqual(doc["reviewable"]["level"],
                         _summary_level(res["summary"]).level)

    def test_json_level_drops_to_r1_when_rules_miss_all(self):
        res = _result(_summary(patterns_attributed=0, errors_attributed=0,
                               patterns_unattributed=67,
                               errors_unattributed=532))
        doc = json.loads(render_json(res))
        self.assertEqual(doc["reviewable"]["level"], "R1")
        self.assertFalse(doc["reviewable"]["can_review"])
        # 人读出口同样不许说"可评审"
        brief = render_brief(res)
        self.assertIn("不可评审", brief)

    def test_reviewed_rows_survive_into_payload(self):
        reviewed = [{"pattern": "X", "class": "env", "count": 2,
                     "rule": {"rule_id": "AR-001"}, "verdict": {"verdict": "accepted"}}]
        doc = json.loads(render_json(_result(reviewed=reviewed)))
        self.assertEqual(len(doc["reviewed"]), 1, "已裁决条目不许静默消失")
        self.assertEqual(doc["reviewed"][0]["verdict"]["verdict"], "accepted")


if __name__ == "__main__":
    unittest.main()