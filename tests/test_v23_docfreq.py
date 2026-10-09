# -*- coding: utf-8 -*-
"""v0.22/v0.23 P2-2 排序口径修正：doc_freq 优先于 freq。

缺陷背景（2026-10-09 全量复查发现）：旧实现按 `freq`（gram 出现总次数）
排序，而 `freq` 是"单条文本内出现次数 × 条数"的累加——于是**长会话里反复
出现的人名**会压过真正的主题词。真实库实测：「丁樾」freq=50577 排在首位，
而全库只有 6723 条消息——50577 显然不是"提到丁樾的消息条数"。

本测试用同一 fixture 断言：
  1. doc_freq = 含该 gram 的消息条数（单条内重复只记一次）；
  2. 排序按 doc_freq；高 freq 但低 doc_freq 的 gram 不得压过；
  3. 旧 schema（缺 doc_freq 列）读时显式提示重跑，不静默按 freq 排序。
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester.kwstats import build_stats, load_stats, render_report


def make_db(path: Path, rows: list[tuple]) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE IF NOT EXISTS sessions("
        " sid TEXT PRIMARY KEY, source TEXT, sid2 TEXT, title TEXT,"
        " category TEXT, created_at TEXT, updated_at TEXT, file TEXT);"
        "CREATE TABLE IF NOT EXISTS messages("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, sid TEXT, role TEXT,"
        " ts TEXT, text TEXT, raw TEXT);")
    for sid, role, ts, text, raw in rows:
        con.execute("INSERT OR IGNORE INTO sessions(sid) VALUES (?)", (sid,))
        con.execute("INSERT INTO messages(sid, role, ts, text, raw)"
                    " VALUES (?,?,?,?,?)", (sid, role, ts, text, raw))
    con.commit()
    con.close()


class TestDocFreqRanking(unittest.TestCase):

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.db = self.dir / "t.db"
        self.meta = self.dir / "kw.db"
        # 构造：主角名「甲乙」在 s1 一条消息里重复 6 次（freq 高、doc_freq=1）；
        # 主题词「节奏」出现在 s2/s3/s4 三条消息各 1 次（freq 低、doc_freq=3）。
        # 旧口径（按 freq）→ 甲乙第一；新口径（按 doc_freq）→ 节奏第一。
        rows = [
            ("s1", "user", "2026-01-01T10:00:00", "-", "甲乙甲乙甲乙甲乙甲乙甲乙"),
            ("s2", "user", "2026-01-02T10:00:00", "-", "节奏"),
            ("s3", "user", "2026-01-03T10:00:00", "-", "节奏"),
            ("s4", "user", "2026-01-04T10:00:00", "-", "节奏"),
        ]
        make_db(self.db, rows)

    def test_doc_freq_counts_messages_not_occurrences(self):
        s = build_stats(self.db, self.meta, ns=[2], role="user")
        rows = {r["gram"]: r for r in s["rows"]}
        # 滑窗推导（自证口径）："甲乙甲乙甲乙甲乙甲乙甲乙" 12 字，
        # bigram 滑窗 11 位 → 交替产出 "甲乙" ×6 与 "乙甲" ×5。
        # 两者 doc_freq 均为 1（都只出现在这 1 条消息里）。
        self.assertEqual(rows["甲乙"]["freq"], 6, "freq = 出现总次数口径")
        self.assertEqual(rows["乙甲"]["freq"], 5)
        self.assertEqual(rows["甲乙"]["doc_freq"], 1, "doc_freq 只算 1 条消息")
        self.assertEqual(rows["乙甲"]["doc_freq"], 1)
        self.assertEqual(rows["节奏"]["freq"], 3)
        self.assertEqual(rows["节奏"]["doc_freq"], 3)

    def test_ranking_prefers_doc_freq(self):
        s = build_stats(self.db, self.meta, ns=[2], role="user")
        order = [r["gram"] for r in s["rows"]]
        self.assertEqual(order[0], "节奏",
                         "doc_freq 高的主题词必须排在 freq 高的人名之前")
        self.assertLess(order.index("节奏"), order.index("甲乙"))

    def test_load_stats_returns_doc_freq_and_same_order(self):
        build_stats(self.db, self.meta, ns=[2], role="user")
        d = load_stats(self.meta, n=2, limit=10)
        grams = [r["gram"] for r in d["rows"]]
        self.assertEqual(grams[0], "节奏")
        self.assertEqual(d["rows"][0]["doc_freq"], 3)
        self.assertIn("freq", d["rows"][0])

    def test_render_report_shows_both_columns(self):
        s = build_stats(self.db, self.meta, ns=[2], role="user")
        text = render_report(s, top=10)
        self.assertIn("doc_freq", text)
        self.assertIn("freq", text)

    def test_old_schema_meta_is_rejected_loudly(self):
        """旧 meta 库（缺 doc_freq）：读时给显式重跑提示，不静默降级。"""
        old = self.dir / "old.db"
        con = sqlite3.connect(old)
        con.executescript(
            "CREATE TABLE keyword_runs(id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " generated_at TEXT, params TEXT, db_fingerprint TEXT);"
            "CREATE TABLE keyword_stats(run_id INTEGER, n INTEGER,"
            " gram TEXT, freq INTEGER, PRIMARY KEY(run_id,n,gram));"
            "INSERT INTO keyword_runs(generated_at,params) "
            "VALUES('x','{}');"
            "INSERT INTO keyword_stats VALUES(1,2,'甲乙',5);")
        con.commit()
        con.close()
        d = load_stats(old, n=2, limit=10)
        self.assertEqual(d["rows"], [])
        self.assertIn("doc_freq", d.get("hint", ""))
        self.assertIn("重跑", d.get("hint", ""))

    def test_build_stats_migrates_old_schema_in_place(self):
        """写侧旧库：build_stats 应能就地重建该表并写入 doc_freq。"""
        old = self.dir / "migrate.db"
        con = sqlite3.connect(old)
        con.executescript(
            "CREATE TABLE keyword_stats(run_id INTEGER, n INTEGER,"
            " gram TEXT, freq INTEGER, PRIMARY KEY(run_id,n,gram));"
            "CREATE TABLE keyword_runs(id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " generated_at TEXT, params TEXT, db_fingerprint TEXT);")
        con.commit()
        con.close()
        s = build_stats(self.db, old, ns=[2], role="user")
        self.assertTrue(all("doc_freq" in r for r in s["rows"]))
        d = load_stats(old, n=2, limit=10)
        self.assertEqual(d["rows"][0]["gram"], "节奏")
        self.assertNotIn("hint", d)


if __name__ == "__main__":
    unittest.main()
