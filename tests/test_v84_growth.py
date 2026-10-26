# -*- coding: utf-8 -*-
"""P2 #12 个人成长总线：按月聚合的判据与"别读过头"的边界。

这组用例盯三件事：

1. **每个数字的算法与写下来的口径一致**（会话按 created_at 归月、错误按
   steps.error 非空、错误率=errors÷sessions、活跃主题=成员会话落在那月）；
2. **薄数据不当结论**（会话 <5 的月份标 thin；0 会话时错误率给 None 而不是 0）；
3. **不声称因果**：`honest` 里必须写明"趋势不是因果"——这条是用户对这个
   视图最容易误读的地方，写成断言防止哪天被"优化"掉。

夹具全在临时目录里现造（三本账 + 卡片目录），不碰真库。
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harvester import growth as G  # noqa: E402


def _mkdb(path: Path, sessions, steps, topics=None) -> None:
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE sessions (sid TEXT PRIMARY KEY, source TEXT,"
                " session_id TEXT, title TEXT, category TEXT, created_at TEXT,"
                " updated_at TEXT, file TEXT, model TEXT)")
    con.execute("CREATE TABLE steps (sid TEXT, seq INTEGER, ts TEXT, tool TEXT,"
                " phase TEXT, status TEXT, error TEXT, detail TEXT)")
    con.executemany("INSERT INTO sessions (sid, created_at) VALUES (?,?)",
                    sessions)
    con.executemany("INSERT INTO steps (sid, error) VALUES (?,?)", steps)
    con.commit()
    con.close()
    if topics is not None:
        t = sqlite3.connect(str(path))
        t.execute("CREATE TABLE topics (id TEXT PRIMARY KEY, name TEXT,"
                  " keywords TEXT, members TEXT, created TEXT)")
        for tid, members in topics:
            t.execute("INSERT INTO topics VALUES (?,?,?,?,?)",
                      (tid, tid, "[]", json.dumps(members), "2026-10-01"))
        t.commit()
        t.close()


class GrowthShapeTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.td, True))
        self.db = self.td / "h.db"
        _mkdb(self.db,
              [("s1", "2026-09-02 10:00:00"), ("s2", "2026-09-03 10:00:00"),
               ("s3", "2026-10-01 10:00:00"), ("s4", "2026-10-02 10:00:00"),
               ("s5", "2026-10-03 10:00:00"), ("s6", "2026-10-04 10:00:00"),
               ("s7", "2026-10-05 10:00:00")],
              [("s1", ""), ("s2", "FS_NOT_FOUND"), ("s3", ""),
               ("s3", "FS_NOT_FOUND"), ("s7", ""), ("s7", "FS_NOT_FOUND")])

    def test_sessions_and_errors_by_month(self):
        g = G.build(self.db, months=12)
        by = {m["month"]: m for m in g["months"]}
        self.assertEqual(by["2026-09"]["sessions"], 2)
        self.assertEqual(by["2026-10"]["sessions"], 5)
        self.assertEqual(by["2026-09"]["errors"], 1)
        self.assertEqual(by["2026-10"]["errors"], 2)

    def test_err_per_session_is_a_ratio_not_percent(self):
        g = G.build(self.db, months=12)
        by = {m["month"]: m for m in g["months"]}
        # 1/2 = 0.5；2/5 = 0.4（写错成百分比会是 50 / 40）
        self.assertAlmostEqual(by["2026-09"]["err_per_session"], 0.5)
        self.assertAlmostEqual(by["2026-10"]["err_per_session"], 0.4)

    def test_definitions_and_sources_cover_every_number(self):
        g = G.build(self.db, months=12)
        for k in g["definitions"]:
            self.assertTrue(g["sources"].get(k),
                            "口径 %s 没有写来源（页面上会显示「来源：—」）" % k)

    def test_honest_says_trend_not_causality(self):
        g = G.build(self.db, months=12)
        joined = "\n".join(g["honest"])
        self.assertIn("趋势", joined)
        self.assertIn("不是因果", joined)


class GrowthEdgeTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.td, True))
        self.db = self.td / "h.db"

    def test_thin_month_flagged(self):
        _mkdb(self.db, [("a", "2026-09-01 10:00:00")], [])
        g = G.build(self.db, months=12)
        self.assertTrue(g["months"][0]["thin"], "1 个会话的月份必须标样本少")

    def test_zero_session_month_rate_is_none(self):
        # 有卡片但当月没有会话：错误率不能写 0（那会被读成"零错误"）
        _mkdb(self.db, [], [])
        cards = self.td / "cards"
        cards.mkdir()
        (cards / "kc-x.md").write_text("---\nid: kc-x\ncreated: 2026-08-03\n---\n",
                                       encoding="utf-8")
        g = G.build(self.db, cards_root=cards, months=12)
        m = g["months"][0]
        self.assertEqual(m["month"], "2026-08")
        self.assertEqual(m["sessions"], 0)
        self.assertIsNone(m["err_per_session"], "没有会话时错误率给 None，不是 0")

    def test_members_both_shapes(self):
        # 真库 members 是 [{sid, evidence}]；旧/手工写入可能是纯字符串列表。
        # 两种都要能算（2026-10-26 真库实测踩过 dict 不可哈希）。
        _mkdb(self.db, [("s1", "2026-10-02 10:00:00")], [],
              topics=[("t1", [{"sid": "s1", "evidence": "x"}]),
                      ("t2", ["s1"])])
        g = G.build(self.db, topics_meta=self.db, months=12)
        self.assertEqual(g["months"][0]["active_topics"], 2)
        self.assertEqual(g["topics_total"], 2)

    def test_new_cards_reads_frontmatter_created(self):
        _mkdb(self.db, [("s1", "2026-10-02 10:00:00")], [])
        cards = self.td / "cards"
        cards.mkdir()
        (cards / "a.md").write_text("---\ncreated: 2026-10-01\n---\n",
                                    encoding="utf-8")
        (cards / "b.md").write_text("---\ncreated: \"2026-10-09\"\n---\n",
                                    encoding="utf-8")
        (cards / "c.md").write_text("---\ncreated: 2026-08-09\n---\n",
                                    encoding="utf-8")
        g = G.build(self.db, cards_root=cards, months=12)
        by = {m["month"]: m for m in g["months"]}
        self.assertEqual(by["2026-10"]["new_cards"], 2, "带引号的 created 也要认")
        self.assertEqual(by["2026-08"]["new_cards"], 1)

    def test_rules_adopted_by_month(self):
        _mkdb(self.db, [("s1", "2026-10-02 10:00:00")], [])
        sug = self.td / "s.db"
        con = sqlite3.connect(str(sug))
        con.execute("CREATE TABLE suggestion_status (key TEXT, status TEXT,"
                    " updated_at TEXT, adopted_at TEXT, note TEXT)")
        con.executemany("INSERT INTO suggestion_status VALUES (?,?,?,?,?)", [
            ("k1", "adopted", "2026-10-05", "2026-10-05", ""),
            ("k2", "adopted", "2026-10-06", "2026-10-06", ""),
            ("k3", "pending", "2026-10-07", None, ""),
        ])
        con.commit()
        con.close()
        g = G.build(self.db, suggestions_meta=sug, months=12)
        by = {m["month"]: m for m in g["months"]}
        self.assertEqual(by["2026-10"]["rules_adopted"], 2, "只数 adopted_at 有值的")

    def test_months_limit_keeps_latest(self):
        _mkdb(self.db, [("a", "2026-07-01 10:00:00"),
                        ("b", "2026-08-01 10:00:00"),
                        ("c", "2026-09-01 10:00:00")], [])
        g = G.build(self.db, months=2)
        self.assertEqual([m["month"] for m in g["months"]], ["2026-08", "2026-09"])

    def test_missing_optional_sources_are_zero_not_error(self):
        _mkdb(self.db, [("a", "2026-09-01 10:00:00")], [])
        g = G.build(self.db, topics_meta=self.td / "nope.db",
                    cards_root=self.td / "nope", suggestions_meta=self.td / "x.db",
                    months=12)
        m = g["months"][0]
        self.assertEqual((m["active_topics"], m["new_cards"], m["rules_adopted"]),
                         (0, 0, 0))

    def test_missing_db_is_loud(self):
        with self.assertRaises(FileNotFoundError):
            G.build(self.td / "nope.db")

    def test_md_renders_table_and_caveats(self):
        _mkdb(self.db, [("a", "2026-09-01 10:00:00")], [("a", "E")])
        md = G.render_md(G.build(self.db, months=12))
        self.assertIn("个人成长总线", md)
        self.assertIn("| 2026-09 | 1 |", md)
        self.assertIn("趋势，不是因果", md)
        self.assertIn("口径", md)


if __name__ == "__main__":
    unittest.main()
