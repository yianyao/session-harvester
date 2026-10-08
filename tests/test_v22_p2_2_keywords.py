# -*- coding: utf-8 -*-
"""v0.22 P2-2 report-keywords 测试——SOP §SOP-P2-2。

规格：n-gram（2/3-gram）词频 + 可选停用词；**只对 messages.raw 统计**
（H3 契约第一个既有适用点，H38：raw 恒为原文）；落独立 meta 库
（keywords_meta.db，runs + stats 两表）+ 只读端点 /api/keywords。

通用性（用户红线）：不写死任何主题/skill/会话——范围由 --sid /
--topic（读 topics 注册表 members）参数决定，对任何主题的会话可用。
fixture 的 skill/主题名一律中性。
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester.kwstats import (build_stats, extract_grams, load_stats,
                              render_report)


def make_db(path: Path, rows: list[tuple]) -> None:
    """rows=(sid, role, ts, text, raw)——schema 对齐 test_v13 惯例。"""
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


def make_topics_meta(path: Path, topics: list[tuple]) -> None:
    """topics=(topic_id, name, members_json)——对齐 topics_meta.db。"""
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE IF NOT EXISTS topics("
                " id TEXT PRIMARY KEY, name TEXT, keywords TEXT,"
                " members TEXT, created TEXT)")
    for tid, name, members in topics:
        con.execute("INSERT INTO topics(id, name, keywords, members, created)"
                    " VALUES (?,?,?,?,?)", (tid, name, "", members, ""))
    con.commit()
    con.close()


class TestExtractGrams(unittest.TestCase):
    def test_bigram_trigram_sliding(self):
        # "叙事节奏" 4 字：bigram 3 个、trigram 2 个（滑窗不越界）
        self.assertEqual(extract_grams("叙事节奏", 2),
                         ["叙事", "事节", "节奏"])
        self.assertEqual(extract_grams("叙事节奏", 3),
                         ["叙事节", "事节奏"])
        self.assertEqual(extract_grams("节奏技法", 2),
                         ["节奏", "奏技", "技法"])

    def test_punct_whitespace_breaks(self):
        # 标点/空白切断：不产跨标点 n-gram
        self.assertEqual(extract_grams("节奏， 技法", 2),
                         ["节奏", "技法"])
        self.assertEqual(extract_grams("abc def", 2),
                         ["ab", "bc", "de", "ef"])

    def test_short_segment(self):
        self.assertEqual(extract_grams("节", 2), [])
        self.assertEqual(extract_grams("节奏", 2), ["节奏"])


class TestBuildStats(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.db = self.dir / "t.db"
        self.meta = self.dir / "kw.db"
        # text 列故意放 bigram 垃圾、raw 放原文——验证 raw 口径（H38）
        rows = [
            ("s1", "user", "2026-01-01T10:00:00", "叙 事 节 奏", "叙事节奏"),
            ("s1", "assistant", "2026-01-01T10:00:05", "x", "节奏技法"),  # 非 user
            ("s2", "user", "2026-01-02T10:00:00", "y", "叙事节奏 技法"),  # 含停用词命中
        ]
        make_db(self.db, rows)

    def test_raw_column_is_source(self):
        s = build_stats(self.db, self.meta, ns=[2], role="user")
        grams = {r["gram"]: r["freq"] for r in s["rows"]}
        # raw 口径（H38）：s1 raw"叙事节奏"→叙事/事节/节奏；s2 raw
        # "叙事节奏 技法"→同三段 + 独立段"技法"（空白硬边界不桥接，
        # "奏技"不存在——奏是段尾字）。
        self.assertEqual(grams.get("节奏"), 2)
        self.assertEqual(grams.get("技法"), 1)
        self.assertEqual(sorted(grams), sorted(
            ["叙事", "事节", "节奏", "技法"]))

    def test_role_filter_excludes_assistant(self):
        s = build_stats(self.db, self.meta, ns=[2], role="user")
        # assistant 行的 "节奏技法" 不计入：叙事节奏只出现 2 次（s1+s2 的 user）
        grams = {r["gram"]: r["freq"] for r in s["rows"]}
        self.assertEqual(grams.get("节奏"), 2)

    def test_stopwords(self):
        stop = self.dir / "stop.txt"
        stop.write_text("# 注释行\n叙事\n", encoding="utf-8", newline="\n")
        s = build_stats(self.db, self.meta, ns=[2], role="user",
                        stopwords_path=stop)
        grams = {r["gram"]: r["freq"] for r in s["rows"]}
        self.assertNotIn("叙事", grams)

    def test_sid_filter(self):
        s = build_stats(self.db, self.meta, ns=[2], role="user",
                        sids=["s1"])
        grams = {r["gram"]: r["freq"] for r in s["rows"]}
        self.assertEqual(grams.get("节奏"), 1)  # 只有 s1 的 raw

    def test_topic_expansion(self):
        tmeta = self.dir / "topics.db"
        make_topics_meta(tmeta, [
            ("tp-demo", "演示主题",
             json.dumps([{"sid": "s1"}, {"sid": "s2"}]))])
        s = build_stats(self.db, self.meta, ns=[2], role="user",
                        topic_ids=["tp-demo"], topics_meta=tmeta)
        grams = {r["gram"]: r["freq"] for r in s["rows"]}
        self.assertEqual(grams.get("节奏"), 2)
        self.assertEqual(s["params"]["sids_used"], 2)

    def test_run_history_and_latest(self):
        build_stats(self.db, self.meta, ns=[2], role="user")
        build_stats(self.db, self.meta, ns=[3], role="user",
                    sids=["s1"])
        d = load_stats(self.meta, n=3, limit=10)
        self.assertEqual(d["run"]["params"]["ns"], [3])
        grams = {r["gram"] for r in d["rows"]}
        self.assertIn("叙事节", grams)
        # n=2 的旧 run 不串台
        d2 = load_stats(self.meta, n=2, limit=10)
        self.assertTrue(all(len(r["gram"]) == 2 for r in d2["rows"]))

    def test_render_report(self):
        s = build_stats(self.db, self.meta, ns=[2, 3], role="user")
        text = render_report(s, top=10)
        self.assertIn("n-gram", text)
        self.assertIn("raw", text)  # 口径声明在报告头


if __name__ == "__main__":
    unittest.main()
