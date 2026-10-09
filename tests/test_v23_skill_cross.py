# -*- coding: utf-8 -*-
"""v0.23 #14：交叉表补 skill 维度（活跃 skill × harness × 错误类别）。

需求来源（用户 S1 原话）：要能对"模型/Agent/harness/**skill**/工具"的异常
做捞取、整理、分类、归因。原交叉表（P1-3）只有 harness × model × class，
**skill 是五类实体里唯一缺的一维**。

本测试钉死：
  1. `collect_errors_from_db` 的每条错误带 `skill`（无 skill 参与为 None）；
  2. skill 归因口径 = 该错误 seq 之前**最近一次** Skill 类调用载入的技能；
  3. `cross_stats_by_skill` 与 `cross_stats` 同源恒等（sum(total) 相等）；
  4. 渲染含 skill 交叉表节；API additive `cross_skill` 字段。
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester import apiserve
from harvester.errstats import (CLASSES, collect_errors_from_db, cross_stats,
                                cross_stats_by_skill, render_report)
from harvester.indexing import SCHEMA, index_session
from harvester.models import Message, SessionRecord

_ERR = "String to replace not found: X"          # tool_interface


def _fixture_db(tmp: Path) -> Path:
    """两条会话：
    - src:with   先 Skill 载入 demo-skill，再 Edit 报错（→ 活跃 skill）
    - src:none   直接 Edit 报错（→ 无 skill）
    另加一条 skill 自身的错误（tool=Skill）验证"取自己"。
    """
    db = tmp / "skill_cross.db"
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)
    recs = [
        SessionRecord(source="src", session_id="with", title="有skill",
                      created_at="2026-10-07 10:00:00",
                      updated_at="2026-10-07 10:00:00",
                      extra={"model": "glm-4.6"},
                      messages=[Message(role="user", text="用技能改文件")]),
        SessionRecord(source="src", session_id="none", title="无skill",
                      created_at="2026-10-06 10:00:00",
                      updated_at="2026-10-06 10:00:00",
                      extra={"model": "glm-4.6"},
                      messages=[Message(role="user", text="直接改")]),
    ]
    for rec in recs:
        index_session(con, rec)
    skill_call = json.dumps({"skill": "demo-skill"}, ensure_ascii=False)
    rows = [
        # 会话 with：seq1 载入 skill（call），seq5 Edit 报错
        ("src:with", 1, "2026-10-07 10:00:01", "Skill", "call", None, None,
         skill_call),
        ("src:with", 2, "2026-10-07 10:00:02", "Skill", "result", "ok",
         None, None),
        ("src:with", 5, "2026-10-07 10:00:05", "Edit", "result", "error",
         _ERR, None),
        # 会话 none：seq3 Edit 报错（无 skill）
        ("src:none", 3, "2026-10-06 10:00:03", "Edit", "result", "error",
         _ERR, None),
        # skill 调用自身失败（tool=Skill，应取自己）
        ("src:with", 9, "2026-10-07 10:00:09", "Skill", "result", "error",
         "skill_asset_invalid", None),
    ]
    for row in rows:
        con.execute("INSERT INTO steps (sid, seq, ts, tool, phase, status, "
                    "error, detail) VALUES (?,?,?,?,?,?,?,?)", row)
    con.commit()
    con.close()
    return db


class TestSkillAttribution(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _fixture_db(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_errors_carry_active_skill(self):
        errors, _ = collect_errors_from_db(self.db)
        by = {(e["sid"], e["seq"]): e for e in errors}
        self.assertEqual(len(errors), 3)
        # Edit 报错时最近一次 Skill 调用载入 demo-skill
        self.assertEqual(by[("src:with", 5)]["skill"], "demo-skill")
        # 无 skill 参与的会话 → None
        self.assertIsNone(by[("src:none", 3)]["skill"])
        # skill 调用自身失败 → 取自己
        self.assertEqual(by[("src:with", 9)]["skill"], "demo-skill")

    def test_skill_cross_totals_equal_model_cross(self):
        errors, _ = collect_errors_from_db(self.db)
        m = cross_stats(errors)
        s = cross_stats_by_skill(errors)
        self.assertEqual(sum(r["total"] for r in m),
                         sum(r["total"] for r in s),
                         "两张交叉表必须同源恒等（同一 errors 输入）")
        self.assertEqual(sum(r["total"] for r in s), 3)

    def test_none_skill_bucketed_not_dropped(self):
        errors, _ = collect_errors_from_db(self.db)
        s = cross_stats_by_skill(errors)
        no_sk = [r for r in s if r["skill"] == "（无 skill）"]
        self.assertEqual(sum(r["total"] for r in no_sk), 1)
        demo = [r for r in s if r["skill"] == "demo-skill"]
        self.assertEqual(sum(r["total"] for r in demo), 2)

    def test_all_class_keys_present(self):
        errors, _ = collect_errors_from_db(self.db)
        for r in cross_stats_by_skill(errors):
            for c in CLASSES:
                self.assertIn(c, r)
            self.assertIn("total", r)
            self.assertIn("skill", r)
            self.assertIn("source", r)

    def test_empty(self):
        self.assertEqual(cross_stats_by_skill([]), [])

    def test_render_contains_skill_section(self):
        errors, meta = collect_errors_from_db(self.db)
        md = render_report(errors, meta)
        self.assertIn("活跃 skill × 数据源 × 错误类别交叉表", md)
        self.assertIn("demo-skill", md)
        self.assertIn("（无 skill）", md)

    def test_api_cross_skill_additive(self):
        con = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            r = apiserve.api_reports_errors(con, self.db, {})
        finally:
            con.close()
        self.assertIn("cross_skill", r, "API 须 additive 暴露 cross_skill")
        self.assertIn("cross", r, "既有 cross 字段不得移除")
        self.assertEqual(sum(x["total"] for x in r["cross_skill"]),
                         r["meta"]["error_count"])


    def test_shell_command_is_not_treated_as_skill(self):
        """回归：pwsh/Bash 的 {"command": "..."} 不得被当成 skill 名。

        `_skill_name_from_detail` 的键回退链含 `command`（服务 skill 自身
        command 形态）。若索引构建不按 SKILL_TOOLS 白名单过滤，shell 命令
        会整条变成"skill"。真实库实测过该污染（"icacls tests\\... | dsh"）。
        """
        con = sqlite3.connect(str(self.db))
        con.execute(
            "INSERT INTO steps (sid, seq, ts, tool, phase, status, error, detail)"
            " VALUES (?,?,?,?,?,?,?,?)",
            ("src:none", 7, "2026-10-06 10:00:07", "pwsh", "call", None, None,
             json.dumps({"command": "icacls tests\\x.py 2>&1"}, ensure_ascii=False)))
        con.execute(
            "INSERT INTO steps (sid, seq, ts, tool, phase, status, error, detail)"
            " VALUES (?,?,?,?,?,?,?,?)",
            ("src:none", 8, "2026-10-06 10:00:08", "Edit", "result", "error",
             _ERR, None))
        con.commit()
        con.close()
        errors, _ = collect_errors_from_db(self.db)
        by = {(e["sid"], e["seq"]): e for e in errors}
        self.assertIsNone(
            by[("src:none", 8)]["skill"],
            "非 Skill 类工具（pwsh）不得成为活跃 skill")
        rows = cross_stats_by_skill(errors)
        names = {r["skill"] for r in rows}
        self.assertNotIn("icacls tests\\x.py 2>&1", names)
        self.assertTrue(all("icacls" not in n for n in names),
                        f"shell 命令污染了 skill 维度: {names}")

    def test_skill_tool_allowlist_matches_behstats(self):
        """归因白名单必须与 behstats.collect_skill_invocations 同源。"""
        from harvester.behstats import SKILL_TOOLS
        from harvester.errstats import _skill_name_of
        # 白名单内：可归因
        self.assertIsNotNone(
            _skill_name_of("Skill", json.dumps({"skill": "x-skill"})))
        # 白名单外：即便 detail 有 command 也必须 None
        self.assertIsNone(
            _skill_name_of("pwsh", json.dumps({"command": "ls"})))
        self.assertIsNone(
            _skill_name_of("Edit", json.dumps({"skill": "spoofed"})))
        self.assertTrue({"Skill", "skill"} <= SKILL_TOOLS)


if __name__ == "__main__":
    unittest.main()
