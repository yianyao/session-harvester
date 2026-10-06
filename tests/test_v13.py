# -*- coding: utf-8 -*-
"""v0.13 测试：triage 蒸馏队列（新 pattern / 旧坑重现 / skill 候选 /
高信号会话 / 卡片子串去重）。"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester.triage import collect_triage, render_triage


def _make_db(path: Path) -> None:
    con = sqlite3.connect(str(path))
    con.executescript("""
    CREATE TABLE sessions (sid TEXT PRIMARY KEY, source TEXT, session_id TEXT,
                           title TEXT, category TEXT, created_at TEXT,
                           updated_at TEXT, file TEXT);
    CREATE VIRTUAL TABLE messages USING fts5(
        sid UNINDEXED, role UNINDEXED, ts UNINDEXED, text, raw UNINDEXED,
        tokenize='unicode61');
    CREATE TABLE steps (sid TEXT, seq INTEGER, ts TEXT, tool TEXT,
                        phase TEXT, status TEXT, error TEXT, detail TEXT);
    """)
    # 三个会话：old（窗口前+窗口内都有错）、new（仅窗口内有新 pattern）、
    # skill（一次成功 skill 调用 + 行为链）
    sessions = [
        ("a:1", "a", "1", "旧坑会话", "", "", "2026-09-01T08:00:00", None),
        ("a:2", "a", "2", "新坑会话", "", "", "2026-10-06T08:00:00", None),
        ("a:3", "a", "3", "skill 会话", "", "", "2026-10-06T09:00:00", None),
    ]
    con.executemany("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?)", sessions)
    msgs = [
        ("a:1", "user", "2026-09-01 08:00:00", "你好", "你好"),
        ("a:1", "assistant", "2026-09-01 08:01:00", "回复", "回复"),
        ("a:2", "user", "2026-10-06 08:00:00", "问题", "问题"),
        ("a:3", "user", "2026-10-06 09:00:00", "做卡", "做卡"),
        ("a:3", "note", "2026-10-06 09:01:00", "思考", "思考"),
    ]
    con.executemany("INSERT INTO messages VALUES (?,?,?,?,?)", msgs)
    steps = [
        # 旧 pattern "old_string was not found in file"：窗口前后都有
        # （三条归一化后同键，窗口内 2 次 → 旧坑重现）
        ("a:1", 0, "2026-09-01 08:00:30", "edit", "result", "error",
         "Error: old_string was not found in file", None),
        ("a:1", 1, "2026-10-06 08:00:30", "edit", "result", "error",
         "Error: old_string was not found in file", None),
        ("a:2", 0, "2026-10-06 08:01:30", "edit", "result", "error",
         "Error: old_string was not found in file", None),
        # 新 pattern：仅窗口内 2 次
        ("a:1", 2, "2026-10-06 08:02:30", "web_fetch", "result", "error",
         "Error: web fetch failed: TypeError: fetch failed", None),
        ("a:2", 1, "2026-10-06 08:03:30", "web_fetch", "result", "error",
         "Error: web fetch failed: TypeError: fetch failed", None),
        # 无错误 call 步骤：垫高 a:1 的高信号分数（避免与 a:3 平分）
        ("a:1", 3, "2026-10-06 08:04:00", "Bash", "call", "", "", None),
        # skill 成功调用 + 行为链
        ("a:3", 0, "2026-10-06 09:00:30", "Skill", "call", "", "",
         '{"skill": "wechat-article-search", "args": "x"}'),
        ("a:3", 1, "2026-10-06 09:00:40", "Skill", "result", "ok", "", ""),
        ("a:3", 2, "2026-10-06 09:00:50", "WebFetch", "call", "", "", None),
    ]
    con.executemany("INSERT INTO steps VALUES (?,?,?,?,?,?,?,?)", steps)
    con.commit()
    con.close()


class TriageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"
        _make_db(self.db)

    def test_new_vs_old_patterns(self):
        r = collect_triage(self.db, since_days=7, min_count=2)
        new = {p["pattern"] for p in r["new_patterns"]}
        old = {p["pattern"] for p in r["old_patterns"]}
        self.assertTrue(any("web fetch failed" in p for p in new), new)
        self.assertTrue(any("old_string was not found" in p for p in old),
                        old)
        # 新 pattern 不得混入旧坑列表（反之亦然）
        self.assertFalse(any("web fetch failed" in p for p in old))
        self.assertFalse(any("old_string was not found" in p for p in new))

    def test_skill_candidate_with_chain(self):
        r = collect_triage(self.db, since_days=7)
        self.assertEqual(len(r["skills"]), 1)
        s = r["skills"][0]
        self.assertEqual(s["skill"], "wechat-article-search")
        self.assertEqual((s["n_calls"], s["n_ok"]), (1, 1))
        self.assertEqual(s["sample"], ("a:3", 0))
        self.assertIn("WebFetch", s["chain"])

    def test_hot_sessions_ranking(self):
        # 全库口径：a:1（2 消息 + 4 步 → 14 分）最高
        r = collect_triage(self.db, since_days=None, top_n=2)
        self.assertEqual(len(r["hot_sessions"]), 2)
        self.assertEqual(r["hot_sessions"][0]["sid"], "a:1")
        self.assertGreaterEqual(r["hot_sessions"][0]["score"],
                                r["hot_sessions"][1]["score"])
        # 窗口口径：updated_at 早于窗口的 a:1 被排除（窗口语义）
        r7 = collect_triage(self.db, since_days=7, top_n=10)
        self.assertNotIn("a:1", [h["sid"] for h in r7["hot_sessions"]])
        self.assertEqual(r7["hot_sessions"][0]["sid"], "a:3")

    def test_cards_dedup_marks_known(self):
        cards = Path(self.tmp.name) / "cards"
        cards.mkdir()
        (cards / "c1.md").write_text(
            "---\ntitle: 旧卡\n---\n遇到 Error: old_string was not found "
            "in file 时先重新 Read。", encoding="utf-8", newline="\n")
        r = collect_triage(self.db, since_days=7, cards_root=cards)
        old_hit = [p for p in r["old_patterns"] if p["known_card"]]
        self.assertTrue(old_hit, "旧 pattern 应命中已有卡")
        self.assertFalse(any(p["known_card"] for p in r["new_patterns"]))

    def test_render_contains_queue_and_discipline(self):
        text = render_triage(collect_triage(self.db, since_days=7))
        self.assertIn("# 蒸馏队列", text)
        self.assertIn("## A.", text)
        self.assertIn("cards new --sid", text)
        self.assertIn("机器只排队", text)


if __name__ == "__main__":
    unittest.main()
