# -*- coding: utf-8 -*-
"""v0.11 测试：G4 行为画像（behstats）、数据源归属（source stats）、
卡片脚手架（cards new）。"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester.behstats import (collect_skill_invocations, render_skill_report,
                                skill_summary)
from harvester.cards import scaffold_card, validate_card
from harvester.toolstats import collect_source_stats, render_source_table


def _make_db(path: Path, sessions, steps):
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE sessions (sid TEXT PRIMARY KEY, source TEXT, "
                "session_id TEXT, title TEXT, category TEXT, created_at TEXT,"
                " updated_at TEXT, file TEXT)")
    con.execute("CREATE TABLE steps (sid TEXT, seq INTEGER, ts TEXT, "
                "tool TEXT, phase TEXT, status TEXT, error TEXT, detail TEXT)")
    con.executemany("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?)", sessions)
    con.executemany("INSERT INTO steps VALUES (?,?,?,?,?,?,?,?)", steps)
    con.commit()
    con.close()


class BehstatsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"
        sid = "wb:s1.jsonl"
        sessions = [(sid, "workbuddy-transcript", "s1", "标题", "", "",
                     "", None),
                    ("ac:1", "autoclaw", "a1", "A", "", "", "", None),
                    ("dsh:1", "dsh", "d1", "D", "", "", "", None)]
        steps = [
            # 一次完整的 Skill 调用：call → (无关 result 不配对) → result → 后续工具
            (sid, 0, "2026-10-01 10:00:00", "Skill", "call", "", "",
             '{"skill": "demo-skill", "args": "做一件事"}'),
            (sid, 1, "2026-10-01 10:00:01", "Skill", "result", "completed",
             "", None),
            (sid, 2, "2026-10-01 10:00:02", "Read", "call", "", "", "{}"),
            (sid, 3, "2026-10-01 10:00:03", "Read", "result", "success", "",
             None),
            (sid, 4, "2026-10-01 10:00:04", "Bash", "call", "", "", "{}"),
            # command 键形态 + 无 result（中断）
            (sid, 5, "2026-10-01 10:00:05", "Skill", "call", "", "",
             '{"command": "find-skills", "args": "找技能"}'),
            # skill_read_active（AutoClaw 路径形态）
            ("ac:1", 0, "2026-10-01 09:00:00", "skill_read_active", "call",
             "", "", '{"path": "pdf-tools\\\\SKILL.md"}'),
            ("ac:1", 1, "2026-10-01 09:00:01", "skill_read_active", "result",
             "success", "", None),
            # name 键形态（DSH）
            ("dsh:1", 6, "2026-10-01 11:00:06", "skill", "call", "", "",
             '{"name": "diagnose-x"}'),
            ("dsh:1", 7, "2026-10-01 11:00:07", "skill", "result", "error",
             "boom", None),
        ]
        _make_db(self.db, sessions, steps)

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass  # Windows 偶发文件锁，残留于系统临时目录无害

    def test_collect_pairs_and_after_tools(self):
        invs = collect_skill_invocations(self.db)
        by = {(i["sid"], i["seq"]): i for i in invs}
        first = by[("wb:s1.jsonl", 0)]
        self.assertEqual(first["skill"], "demo-skill")
        self.assertEqual(first["args"], "做一件事")
        self.assertEqual(first["status"], "completed")
        self.assertEqual(first["after_tools"], ["Read", "Bash"])
        # 无 result 的调用 → no_result
        self.assertEqual(by[("wb:s1.jsonl", 5)]["status"], "no_result")
        self.assertEqual(by[("wb:s1.jsonl", 5)]["skill"], "find-skills")
        # skill_read_active 路径取技能目录首段
        self.assertEqual(by[("ac:1", 0)]["skill"], "pdf-tools")
        # name 键兜底 + error 状态
        dsh = by[("dsh:1", 6)]
        self.assertEqual(dsh["skill"], "diagnose-x")
        self.assertEqual(dsh["status"], "error")
        self.assertEqual(dsh["error"], "boom")

    def test_summary_aggregates(self):
        invs = collect_skill_invocations(self.db)
        s = skill_summary(invs)
        self.assertEqual(s["demo-skill"]["calls"], 1)
        self.assertEqual(s["demo-skill"]["ok"], 1)
        self.assertIn("Read→Bash", s["demo-skill"]["chains"])
        self.assertEqual(s["diagnose-x"]["err"], 1)

    def test_render_overview_and_filter(self):
        invs = collect_skill_invocations(self.db)
        text = render_skill_report(invs)
        self.assertIn("demo-skill", text)
        self.assertIn("行为链", text)
        deep = render_skill_report(invs, skill_filter="find-skills")
        self.assertIn("wb:s1.jsonl#5", deep)
        self.assertIn("no_result", deep)
        self.assertIn("没有该 skill", render_skill_report(invs,
                                                         skill_filter="不存在"))

    def test_new_skill_call_resets_chain(self):
        # 第二次 Skill 调用后，第一次的 after_tools 不应继续增长
        sid = "wb:s1.jsonl"
        con = sqlite3.connect(str(self.db))
        con.executemany("INSERT INTO steps VALUES (?,?,?,?,?,?,?,?)", [
            (sid, 6, "2026-10-01 10:00:06", "Skill", "call", "", "",
             '{"skill": "demo-skill"}'),
            (sid, 7, "2026-10-01 10:00:07", "Skill", "result", "completed",
             "", None),
            (sid, 8, "2026-10-01 10:00:08", "Write", "call", "", "", "{}"),
        ])
        con.commit()
        con.close()
        invs = [i for i in collect_skill_invocations(self.db)
                if i["sid"] == sid]
        self.assertEqual(len(invs), 3)
        self.assertEqual(invs[0]["after_tools"], ["Read", "Bash"])
        self.assertEqual(invs[1]["skill"], "find-skills")  # no_result 的
        self.assertEqual(invs[2]["skill"], "demo-skill")
        self.assertEqual(invs[2]["after_tools"], ["Write"])


class SourceStatsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"
        sessions = [("a", "srcA", "sa", "A", "", "", "", None),
                    ("b", "srcB", "sb", "B", "", "", "", None)]
        steps = [
            ("a", 1, "2026-10-01 10:00:00", "Bash", "call", "", "", None),
            ("a", 2, "2026-10-01 10:00:01", "Bash", "result", "error",
             "boom", None),
            ("b", 1, "2026-10-01 10:00:00", "Bash", "call", "", "", None),
            ("b", 2, "2026-10-01 10:00:01", "Bash", "result", "success", "",
             None),
        ]
        _make_db(self.db, sessions, steps)

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass  # Windows 偶发文件锁，残留于系统临时目录无害

    def test_collect_source_stats(self):
        by = collect_source_stats(self.db)
        self.assertEqual(set(by), {"srcA", "srcB"})
        self.assertEqual(by["srcA"]["Bash"].error, 1)
        self.assertEqual(by["srcB"]["Bash"].success, 1)

    def test_render_source_table(self):
        by = collect_source_stats(self.db)
        table = render_source_table(by)
        self.assertIn("srcA", table)
        self.assertIn("100.0%", table)
        self.assertEqual(render_source_table({"only": by["srcA"]}), "")


class CardsNewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"
        self.root = Path(self.tmp.name) / "cards"
        _make_db(self.db, [
            ("yuanbao-raw:abc", "yuanbao-raw", "abc", "测试会话标题", "", "",
             "", None)], [])

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass  # Windows 偶发文件锁，残留于系统临时目录无害

    def test_scaffold_and_validate(self):
        p = scaffold_card(self.db, "yuanbao-raw:abc", self.root,
                          ctype="insight", turn=3)
        self.assertTrue(p.is_file())
        text = p.read_text(encoding="utf-8")
        self.assertIn('session_id: "abc"', text)  # 用 adapter 级 session_id
        self.assertIn("turn: 3", text)
        errors, warns, fm = validate_card(p)
        # v0.21 P0-1（H11）：脚手架含占位符 → 必须错误级拒绝
        # （旧断言"脚手架即满足 §8"是空壳卡通过校验的漏洞，已修）
        self.assertTrue(all(e.startswith("[占位符]") for e in errors))
        self.assertEqual(len(errors), 2)  # <粘贴 evidence + <待补 正文
        self.assertEqual(fm["type"], "insight")
        # 第二张自动编号
        p2 = scaffold_card(self.db, "yuanbao-raw:abc", self.root,
                           ctype="pitfall")
        self.assertNotEqual(p.name, p2.name)

    def test_scaffold_accepts_session_id(self):
        p = scaffold_card(self.db, "abc", self.root)
        self.assertTrue(p.is_file())  # session_id 也能查到

    def test_scaffold_rejects_bad(self):
        with self.assertRaises(KeyError):
            scaffold_card(self.db, "nope", self.root)
        with self.assertRaises(ValueError):
            scaffold_card(self.db, "yuanbao-raw:abc", self.root, ctype="bad")


if __name__ == "__main__":
    unittest.main()
