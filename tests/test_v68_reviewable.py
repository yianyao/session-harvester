# -*- coding: utf-8 -*-
"""P6-7 可评审分级（`reviewable`）的验收门。

钉住四件事，每件都能失败：

1. **判据完整**：R0–R3 四等齐全、标签与判据一一对应、拼错等级不静默降级；
2. **等级由内容算**：条目数/候选数/回填通道决定等级，**不由篇幅或面名决定**；
3. **R1 及以下不得标「可评审」**（设计 §3.1 唯一硬约束）——用负向前瞻扫裸词，
   "不可评审" 因带"不"不算违规；
4. **每个报告面真的带等级行**，且 API 与 CLI **同一判据**（同源对账）。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from harvester import reviewable as RV

ROOT = Path(__file__).resolve().parents[1]
REAL_DB = ROOT / "harvester.db"

try:
    import yaml  # noqa: F401
    _HAS_YAML = True
except ImportError:  # pragma: no cover
    _HAS_YAML = False

_NEED_REAL = unittest.skipUnless(
    REAL_DB.is_file() and _HAS_YAML,
    "需要真库 harvester.db 与 PyYAML（归因规则表是 YAML）")

#: 裸的「可评审」——前面不能是"不"。用它扫"是否在声称可评审"。
BARE_REVIEWABLE = re.compile(r"(?<!不)可评审")


class TestCriteria(unittest.TestCase):
    def test_four_levels_in_order(self):
        self.assertEqual(RV.LEVELS, ("R0", "R1", "R2", "R3"))
        self.assertEqual([RV.rank(x) for x in RV.LEVELS], [0, 1, 2, 3])

    def test_every_level_has_label_and_criteria(self):
        for lv in RV.LEVELS:
            self.assertIn(lv, RV.LABELS, "缺 %s 的人读标签" % lv)
            self.assertIn(lv, RV.CRITERIA, "缺 %s 的判据" % lv)
            self.assertTrue(RV.LABELS[lv].strip())
            self.assertTrue(RV.CRITERIA[lv].strip())

    def test_unknown_level_raises(self):
        """拼错等级必须炸——静默降级会让 R1 被当成 R3 展示。"""
        with self.assertRaises(ValueError):
            RV.rank("R4")
        with self.assertRaises(ValueError):
            RV.Level(level="R9", reason="x")

    def test_weakest_takes_minimum(self):
        self.assertEqual(RV.weakest(["R3", "R1", "R2"]), "R1")
        self.assertEqual(RV.weakest([]), "R0")
        self.assertEqual(RV.weakest(["R2"]), "R2")

    def test_weakest_rejects_single_string(self):
        """传字符串会按字符迭代成 'R'——入口就挡住，别让它静默降级。"""
        with self.assertRaises(TypeError):
            RV.weakest("R1")


class TestLevelFromContent(unittest.TestCase):
    def test_empty_is_r0(self):
        self.assertEqual(RV.level_of_counts(0, 0, True), "R0")
        # 没有条目时，即使有候选/通道也只能 R0（没东西可评）
        self.assertEqual(RV.level_of_counts(0, 5, True), "R0")

    def test_counts_without_candidates_is_r1(self):
        self.assertEqual(RV.level_of_counts(12, 0, True), "R1")

    def test_candidates_without_backfill_is_r2(self):
        self.assertEqual(RV.level_of_counts(12, 3, False), "R2")

    def test_candidates_with_backfill_is_r3(self):
        self.assertEqual(RV.level_of_counts(12, 3, True), "R3")

    def test_can_review_starts_at_r2(self):
        """**R1 不能评审**——这条是本项存在的理由。"""
        self.assertFalse(RV.assess(entries=5, candidates=0,
                                   backfill=True).can_review)
        self.assertTrue(RV.assess(entries=5, candidates=1,
                                  backfill=False).can_review)

    def test_can_verdict_only_r3(self):
        self.assertFalse(RV.assess(entries=5, candidates=1,
                                   backfill=False).can_verdict)
        self.assertTrue(RV.assess(entries=5, candidates=1,
                                  backfill=True).can_verdict)

    def test_as_dict_is_json_ready(self):
        d = RV.assess(entries=5, candidates=0, coverage="1/10").as_dict()
        for k in ("level", "label", "criteria", "reason", "can_review",
                  "can_verdict", "coverage"):
            self.assertIn(k, d)
        self.assertEqual(d["level"], "R1")
        self.assertFalse(d["can_review"])


class TestRenderHonesty(unittest.TestCase):
    def test_r1_render_never_claims_reviewable(self):
        cases = {
            "R0": RV.assess(entries=0),
            "R1": RV.assess(entries=1, candidates=0, backfill=True),
        }
        for lv, obj in cases.items():
            self.assertEqual(obj.level, lv)
            text = obj.render()
            self.assertIn("不可评审", text, "%s 行必须说清" % lv)
            self.assertIsNone(BARE_REVIEWABLE.search(text),
                              "%s 行出现了裸的「可评审」：%s" % (lv, text))

    def test_r3_render_claims_reviewable(self):
        text = RV.assess(entries=3, candidates=2, backfill=True).render()
        self.assertIn("R3", text)
        self.assertIsNotNone(BARE_REVIEWABLE.search(text))
        self.assertIn("可裁决", text)

    def test_r2_says_reviewable_but_not_verdictable(self):
        text = RV.assess(entries=3, candidates=2, backfill=False).render()
        self.assertIn("R2", text)
        self.assertIn("不可裁决", text)


@_NEED_REAL
class TestSurfacesDeclareLevel(unittest.TestCase):
    """每个报告面必须自己带等级行——"裸数字"的门就从这里开始。"""

    def setUp(self):
        self.db = REAL_DB

    def _errors(self, with_attrib=True):
        from harvester.errstats import collect_errors_from_db, render_report
        errors, meta = collect_errors_from_db(self.db, since_days=30)
        attributions = None
        if with_attrib:
            from harvester.attrib import collect_attributions, load_rules
            attributions = collect_attributions(errors, load_rules())
        return render_report(errors, meta, attributions=attributions)

    def test_errors_with_attribution_is_r3(self):
        text = self._errors(True)
        line = next(ln for ln in text.splitlines() if "本面等级" in ln)
        self.assertIn("R3", line)

    def test_errors_without_attribution_is_r1_and_says_not_reviewable(self):
        """归因层不可用 → 只能是 R1；**不许因为报告很长就升等级**。"""
        text = self._errors(False)
        line = next(ln for ln in text.splitlines() if "本面等级" in ln)
        self.assertIn("R1", line)
        self.assertIn("不可评审", line)
        self.assertIsNone(BARE_REVIEWABLE.search(line), line)

    def test_chains_is_r1(self):
        from harvester.chainstats import collect, render_report
        text = render_report(collect(self.db, since_days=365))
        line = next(ln for ln in text.splitlines() if "本面等级" in ln)
        self.assertIn("R1", line)
        self.assertIsNone(BARE_REVIEWABLE.search(line), line)

    def test_skill_is_r1(self):
        from harvester.behstats import (collect_skill_invocations,
                                        render_skill_report)
        text = render_skill_report(collect_skill_invocations(self.db))
        line = next(ln for ln in text.splitlines() if "本面等级" in ln)
        self.assertIn("R1", line)
        self.assertIsNone(BARE_REVIEWABLE.search(line), line)

    def test_attrib_brief_is_r3(self):
        from harvester.attrib import (collect_attributions, load_rules,
                                      render_brief)
        from harvester.errstats import collect_errors_from_db
        errors, meta = collect_errors_from_db(self.db, since_days=30)
        result = collect_attributions(errors, load_rules())
        text = render_brief(result, errors_meta=meta)
        line = next(ln for ln in text.splitlines() if "本面等级" in ln)
        self.assertIn("R3", line)


@_NEED_REAL
class TestApiAndCliAgree(unittest.TestCase):
    """页面上的等级必须与 CLI 同一判据（**不许前端自己猜**）。"""

    def test_payload_carries_reviewable_and_matches_cli(self):
        import sqlite3
        from harvester.apiserve import api_reports_errors
        from harvester.errstats import collect_errors_from_db
        con = sqlite3.connect(f"file:{REAL_DB.as_posix()}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            d = api_reports_errors(con, REAL_DB, {"days": "30"})
        finally:
            con.close()
        self.assertIn("reviewable", d, "API 载荷必须带 reviewable")
        rv = d["reviewable"]
        for k in ("level", "label", "can_review", "can_verdict"):
            self.assertIn(k, rv)

        errors, _meta = collect_errors_from_db(REAL_DB, since_days=30)
        from harvester.attrib import collect_attributions, load_rules
        from harvester.reviewable import level_of_counts, backfill_available
        att = collect_attributions(errors, load_rules())
        n_got = (d.get("attrib_summary") or {}).get("patterns_attributed", 0)
        want = level_of_counts(len(errors), n_got, backfill_available())
        self.assertEqual(rv["level"], want,
                         "API 等级与 reviewable 判据不一致（两套口径？）")
        if rv["level"] in ("R0", "R1"):
            self.assertFalse(rv["can_review"])

    def test_unavailable_attribution_downgrades_to_r1(self):
        """归因层不可用（无 rules）时 API 必须降级为 R1，而不是继续报 R3。

        **故意造一个"自相矛盾的上游"**：status 说不可用、却还带着候选字典。
        真实世界里这正是危险形态（规则表半加载、meta 缓存串味、旧进程残留条目），
        而"按 status 决定算不算候选"就是防它的那道闸（变异 M8 钉住）。
        """
        import sqlite3
        from harvester import apiserve as A
        from harvester.errstats import pattern_stats
        con = sqlite3.connect(f"file:{REAL_DB.as_posix()}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        orig = A._attrib_payload

        def _poison(errors):
            """给**每一个真实模式**都挂一个候选，但 status 说不可用。"""
            by_pat = {p: {"rule": {"rule_id": "AR-X", "why": "x", "fix": "y",
                                   "candidate": True}}
                      for p in pattern_stats(errors)}
            return ("unavailable", "规则表不可用", by_pat)

        A._attrib_payload = _poison
        try:
            d = A.api_reports_errors(con, REAL_DB, {"days": "30"})
        finally:
            A._attrib_payload = orig
            con.close()
        self.assertEqual(d["attrib_status"], "unavailable")
        self.assertEqual(d["reviewable"]["level"], "R1",
                         "status=unavailable 却因残留候选升了级")
        self.assertFalse(d["reviewable"]["can_review"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()