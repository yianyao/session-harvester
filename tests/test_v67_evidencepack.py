# -*- coding: utf-8 -*-
"""P6-6 评审包（evidence-pack）的验收门。

**本文件最重要的那条**：`test_md_has_verdict_slot` —— 设计 §3.2 说得很硬：
**没有判定槽的包不是评审包，是报告**。所以"判定槽必须在 md 里、行数等于条目数、
裁决列必须为空"这几条就是"可评审"的定义性特征（变异自测确认它会红）。

其余三类：
- **等级由内容算**（R0/R1/R2/R3），不写死：写死的等级会随数据变化而说谎；
- **对账**：包里的归因候选必须与 `attrib.collect_attributions` 逐条一致
  （2026-10-22 实测踩到"取错键 → rule_id=None 但看起来有归因"，本条钉住）；
- **边界诚实**：`artifact` 不在包内、未归因≠没问题、链型锚点是会话级。
"""
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harvester.evidencepack import (JSON_SCHEMA, KINDS, LEVELS, SLOT_COLS,
                                    build_pack, level_of, level_reason,
                                    render_json, render_md)

REPO = Path(__file__).resolve().parents[1]
REAL_DB = REPO / "harvester.db"

#: `build_pack` 要读归因规则表（YAML，无降级解析，H9）。
#: 无 PyYAML 门禁下必须 **skip** 而不是失败——门禁的判据只有 `failures=0`，
#: 让"环境缺依赖"长成 failure 就没人分得清它与真 bug（README「测试基线」）。
try:
    import yaml as _yaml_mod          # noqa: F401
    _HAS_YAML = True
except ImportError:                    # pragma: no cover - 门禁环境
    _HAS_YAML = False

_NEED_REAL = unittest.skipUnless(_HAS_YAML and REAL_DB.is_file(),
                                 "需要 PyYAML + 真实 harvester.db（集成对账）")


def _pack(**over) -> dict:
    """最小合法包（纯渲染测试用，不碰库）。"""
    p = {
        "schema": JSON_SCHEMA,
        "kind": "errors",
        "generated_at": "2026-10-22 00:00:00",
        "window": "全库（无时间窗）",
        "since_days": None,
        "db": {"path": "x.db", "sessions": 10, "steps": 100, "errors": 4,
               "db_mtime": "2026-10-22 00:00:00", "stale": False},
        "level": "R3",
        "level_reason": "有条目 + 原文锚点 + 归因候选 + 回填通道",
        "backfill": True,
        "coverage": {"covered": 2, "total": 10, "pct": 20.0,
                     "basis": "窗口内有错误步骤的会话", "note": "只覆盖上述会话"},
        "entries": [
            {"pattern_key": "PAT_A", "title": "PAT_A", "count": 3,
             "anchors": [{"sid": "s1", "seq": 7, "text": "boom"}],
             "extra": {"class": "tool_interface", "given_up": 0},
             "attrib": {"rule_id": "AR-001", "class": "tool_interface",
                        "why": "因为 A", "fix": "做 B", "confidence": "high",
                        "candidate": True}},
            {"pattern_key": "PAT_B", "title": "PAT_B", "count": 1,
             "anchors": [], "extra": {"class": "unclassified"}, "attrib": None},
        ],
        "slot": {"cols": ["pattern_key", *SLOT_COLS],
                 "verdicts": ["accepted", "rejected", "revise"],
                 "rows": [{"pattern_key": "PAT_A", "verdict": "", "note": "",
                           "reviewer": "", "reviewed_at": ""},
                          {"pattern_key": "PAT_B", "verdict": "", "note": "",
                           "reviewer": "", "reviewed_at": ""}]},
        "boundaries": ["归因是机器判断，会错"],
    }
    p.update(over)
    return p


class TestVerdictSlot(unittest.TestCase):
    """③ 判定槽 = 「可评审」的定义性特征。"""

    def test_md_has_verdict_slot(self):
        md = render_md(_pack())
        self.assertIn("③ 判定槽", md)

    def test_slot_table_has_all_five_columns(self):
        md = render_md(_pack())
        header = next(ln for ln in md.splitlines()
                      if ln.startswith("| pattern_key |"))
        for col in ("pattern_key", *SLOT_COLS):
            self.assertIn(col, header, f"判定槽缺列 {col}")

    def test_slot_has_one_row_per_entry(self):
        p = _pack()
        md = render_md(p)
        rows = [ln for ln in md.splitlines()
                if ln.startswith("| `") and ln.count("  |") >= len(SLOT_COLS)]
        self.assertEqual(len(rows), len(p["entries"]),
                         "判定槽行数必须等于条目数（否则有的条目无法裁决）")

    @_NEED_REAL
    def test_slot_verdict_cells_are_empty(self):
        """判定槽必须是**空的**——包不许替人下结论。

        ⚠ 这里必须查 `build_pack` 产出的包，不能只查渲染夹具：夹具的 verdict
        永远是我手写的空串，**改代码也不会让它变红**（等于没断言）。
        """
        p = build_pack("errors", REAL_DB, top=5)
        self.assertTrue(p["slot"]["rows"], "空判定槽也要有行（见下一条）")
        for r in p["slot"]["rows"]:
            for col in SLOT_COLS:
                self.assertEqual(r[col], "", f"判定槽 {col} 被预填了")

    def test_empty_pack_still_has_slot(self):
        """空包也必须有判定槽（"这段时间没问题"同样需要人来确认）。"""
        md = render_md(_pack(entries=[], slot={
            "cols": ["pattern_key", *SLOT_COLS], "verdicts": [], "rows": []}))
        self.assertIn("③ 判定槽", md)

    def test_slot_section_is_last(self):
        """判定槽必须在证据体**之后**（评审顺序：先看证据，再落笔）。"""
        md = render_md(_pack())
        self.assertLess(md.index("② 证据体"), md.index("③ 判定槽"))


class TestLevels(unittest.TestCase):
    def test_empty_is_r0(self):
        self.assertEqual(level_of([], backfill=True), "R0")

    def test_no_attribution_is_r1(self):
        rows = [{"pattern_key": "x", "count": 1, "attrib": None}]
        self.assertEqual(level_of(rows, backfill=True), "R1")

    def test_with_attribution_and_backfill_is_r3(self):
        rows = [{"pattern_key": "x", "count": 1, "attrib": {"rule_id": "AR-1"}}]
        self.assertEqual(level_of(rows, backfill=True), "R3")

    def test_with_attribution_but_no_backfill_is_r2(self):
        rows = [{"pattern_key": "x", "count": 1, "attrib": {"rule_id": "AR-1"}}]
        self.assertEqual(level_of(rows, backfill=False), "R2")

    def test_r1_reason_says_not_reviewable(self):
        """R1 的理由必须写清"评不出根因"——不许让 R1 看起来像可评审。"""
        rows = [{"pattern_key": "x", "count": 1, "attrib": None}]
        self.assertIn("没有根因候选", level_reason(rows, "R1", False))

    def test_levels_constant_matches_design(self):
        self.assertEqual(LEVELS, ("R0", "R1", "R2", "R3"))


class TestRenderJson(unittest.TestCase):
    def test_json_is_machine_readable_and_schema_tagged(self):
        import json
        d = json.loads(render_json(_pack()))
        self.assertEqual(d["schema"], JSON_SCHEMA)
        self.assertEqual(d["slot"]["cols"][0], "pattern_key")

    def test_two_exits_do_not_share_implementation(self):
        """人读/机读两出口不许共用实现（P6-2 的纪律，这里同样适用）。"""
        import inspect

        from harvester import evidencepack as E
        src_md = inspect.getsource(E.render_md)
        src_js = inspect.getsource(E.render_json)
        self.assertNotEqual(src_md, src_js)
        self.assertNotIn("render_md", src_js,
                         "机读出口不得转调人读渲染（那样就不是两个出口）")


class TestBoundariesHonesty(unittest.TestCase):
    def test_artifact_kind_is_not_supported(self):
        """`artifact` 故意不在包内（只覆盖 dsh 源 ≈2%），必须 fail loud。"""
        self.assertNotIn("artifact", KINDS)
        self.assertEqual(set(KINDS),
                         {"errors", "skills", "chains", "attrib"})

    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            build_pack("artifact", Path("nope.db"))

    def test_md_says_unattributed_is_not_no_problem(self):
        md = render_md(_pack())
        self.assertIn("不是「没问题」", md)

    def test_md_marks_candidate_true(self):
        md = render_md(_pack())
        self.assertIn("candidate=true", md)
        self.assertIn("机器判断会错", md)

    def test_md_prints_coverage_ratio(self):
        md = render_md(_pack())
        self.assertIn("2 / 10 会话（20.0%）", md)


@_NEED_REAL
class TestRealPack(unittest.TestCase):
    """真库集成：只验**对账关系**，不写死任何数字。"""

    def test_errors_pack_three_sections_and_slot_rows(self):
        p = build_pack("errors", REAL_DB, top=8)
        md = render_md(p)
        for sec in ("① 判据头", "② 证据体", "③ 判定槽"):
            self.assertIn(sec, md)
        self.assertEqual(len(p["slot"]["rows"]), len(p["entries"]))

    def test_attrib_rules_reconcile_with_collect_attributions(self):
        """包里的归因候选必须与归因层逐条一致（钉住"取错键"那类静默空值）。"""
        from harvester.attrib import collect_attributions, load_rules
        from harvester.errstats import collect_errors_from_db

        errors, _ = collect_errors_from_db(REAL_DB)
        by = collect_attributions(errors, load_rules())["by_pattern"]
        p = build_pack("errors", REAL_DB, top=1000)
        checked = 0
        for e in p["entries"]:
            want = (by.get(e["pattern_key"]) or {}).get("rule")
            got = e.get("attrib")
            self.assertEqual((got or {}).get("rule_id"),
                             (want or {}).get("rule_id"),
                             f"{e['pattern_key']} 的归因与归因层不一致")
            if want:
                checked += 1
        self.assertGreater(checked, 0, "一条都没对上，说明对账本身没跑")

    def test_skills_and_chains_are_r1_not_reviewable(self):
        """skills/chains 没有根因候选 → 只能是 R1（写成 R2/R3 就是夸大）。"""
        for kind in ("skills", "chains"):
            p = build_pack(kind, REAL_DB, top=5)
            self.assertIn(p["level"], ("R0", "R1"),
                          f"{kind} 不应标 {p['level']}（无归因候选）")

    def test_level_matches_its_own_entries(self):
        """等级必须与包内条目自洽（不是外部写死的字符串）。"""
        p = build_pack("errors", REAL_DB, top=8)
        self.assertEqual(p["level"],
                         level_of(p["entries"], p["backfill"]))

    def test_coverage_is_computed_and_bounded(self):
        p = build_pack("errors", REAL_DB, top=5)
        c = p["coverage"]
        self.assertGreaterEqual(c["covered"], 0)
        self.assertLessEqual(c["covered"], c["total"])
        self.assertTrue(0.0 <= c["pct"] <= 100.0)
        # 百分比必须是**算出来的**，不是写死的 100 / 空话
        want = round(c["covered"] / c["total"] * 100.0, 1) if c["total"] else 0.0
        self.assertEqual(c["pct"], want, "覆盖度百分比与 covered/total 不一致")

    def test_every_entry_carries_an_anchor(self):
        """证据体必须给得出原文锚点——没有锚点的"证据"无法回溯，等于断言。"""
        for kind in ("errors", "skills", "chains"):
            p = build_pack(kind, REAL_DB, top=5)
            self.assertTrue(p["entries"], f"{kind} 包没有条目，本条无从验证")
            for e in p["entries"]:
                self.assertTrue(e["anchors"],
                                f"{kind} 包条目缺锚点：{e['pattern_key']}")
                self.assertTrue(e["anchors"][0]["sid"], "锚点缺 sid")

    def test_boundaries_mention_artifact_exclusion(self):
        p = build_pack("errors", REAL_DB, top=3)
        joined = "".join(p["boundaries"])
        self.assertIn("artifact", joined)
        self.assertIn("未归因", joined)


class TestCli(unittest.TestCase):
    def test_parser_accepts_all_four_kinds(self):
        from harvester.cli import build_parser
        p = build_parser()
        for kind in KINDS:
            ns = p.parse_args(["evidence-pack", "--kind", kind])
            self.assertEqual(ns.kind, kind)
            self.assertIs(ns.func.__name__, "cmd_evidence_pack")

    def test_help_mentions_verdict_slot(self):
        """`--help` 必须点明判定槽——它是本命令与"报告"的区别。"""
        from harvester.cli import build_parser
        help_text = build_parser()._subparsers._group_actions[0].choices[
            "evidence-pack"].format_help()
        self.assertIn("判定槽", help_text)


if __name__ == "__main__":
    unittest.main()