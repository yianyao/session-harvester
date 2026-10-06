# -*- coding: utf-8 -*-
"""v0.14：T2 蒸馏包（drafting.draft）测试。"""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harvester.drafting import (build_distill_packet, render_transcript,
                                write_packet)

_DB_SCHEMA = """
CREATE TABLE sessions (
    sid TEXT PRIMARY KEY, source TEXT NOT NULL, session_id TEXT NOT NULL,
    title TEXT, category TEXT, created_at TEXT, updated_at TEXT, file TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS messages USING fts5(
    sid UNINDEXED, role UNINDEXED, ts UNINDEXED, text, raw UNINDEXED,
    tokenize='unicode61');
CREATE TABLE steps (
    sid TEXT NOT NULL, seq INTEGER NOT NULL, ts TEXT, tool TEXT NOT NULL,
    phase TEXT NOT NULL, status TEXT, error TEXT, detail TEXT);
"""


def _make_db(root: Path) -> Path:
    db = root / "harvester.db"
    con = sqlite3.connect(str(db))
    con.executescript(_DB_SCHEMA)
    con.execute("INSERT INTO sessions VALUES "
                "('wb:t1','workbuddy-transcript','t1','调试会话',"
                "NULL,'2026-10-06 08:00:00','2026-10-06 09:00:00',NULL)")
    for role, text in [("user", "为什么 Edit 一直失败？"),
                       ("assistant", "old_string 与文件当前内容不一致。")]:
        con.execute("INSERT INTO messages(sid, role, ts, text) "
                    "VALUES (?,?,?,?)", ("wb:t1", role, "2026-10-06 08:01",
                                         text))
    con.execute("INSERT INTO steps VALUES "
                "('wb:t1', 12, '2026-10-06 08:02:00', 'Edit', 'call', "
                "NULL, NULL, '{\"file_path\": \"a.py\"}')")
    con.execute("INSERT INTO steps VALUES "
                "('wb:t1', 13, '2026-10-06 08:02:05', 'Edit', 'result', "
                "'error', 'Error: old_string was not found in file', NULL)")
    con.commit()
    con.close()
    return db


class TestDraft(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db = _make_db(self.root)

    def tearDown(self):
        try:
            self._tmp.cleanup()
        except OSError:
            pass  # 该 Windows 构建不支持 ignore_errors，残留交系统清

    def test_transcript_contains_anchors(self):
        text, meta = render_transcript(self.db, "wb:t1")
        self.assertIn("[wb:t1#1] [user]", text)
        self.assertIn("[wb:t1#2] [assistant]", text)
        self.assertIn(">> [wb:t1#12] Edit call", text)
        self.assertIn("<< [wb:t1#13] Edit error", text)
        self.assertEqual(meta["n_msgs"], 2)
        self.assertEqual(meta["n_steps"], 2)
        self.assertFalse(meta["truncated"])

    def test_transcript_by_session_id_and_missing(self):
        text, meta = render_transcript(self.db, "t1")   # session_id 亦可
        self.assertEqual(meta["sid"], "wb:t1")
        with self.assertRaises(KeyError):
            render_transcript(self.db, "ghost")

    def test_packet_self_contained(self):
        packet = build_distill_packet(self.db, "wb:t1", ctype="pitfall")
        for part in ("## 任务指令", "## 卡片规范", "## 会话原文",
                     "## 产出要求", "confidence 固定 0.3",
                     "cards validate --root", "session_id=`t1`"):
            self.assertIn(part, packet)
        self.assertIn("## 现象", packet)     # pitfall 骨架注入指令
        self.assertIn("[wb:t1#1]", packet)   # 原文内嵌

    def test_packet_invalid_type(self):
        with self.assertRaises(ValueError):
            build_distill_packet(self.db, "wb:t1", ctype="essay")

    def test_truncation_budget(self):
        text, meta = render_transcript(self.db, "wb:t1", max_chars=50)
        self.assertTrue(meta["truncated"])
        self.assertIn("超预算截断", text)

    def test_write_packet_lf(self):
        packet = build_distill_packet(self.db, "wb:t1")
        p = write_packet(self.root / "pkg" / "d.md", packet)
        raw = p.read_bytes()
        self.assertNotIn(b"\r\n", raw)
        self.assertIn("蒸馏包", p.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
