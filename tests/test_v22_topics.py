# -*- coding: utf-8 -*-
"""v0.22 T1 主题注册表 MVP 测试（SOP-T1）。

topics_meta.db 是独立 meta 库（suggestions_meta.db 范式），不触碰
采集库 schema（红线 §5.1）。验收口径：
- 注册/加成员/列表/详情 roundtrip；
- title_chain：成员会话标题按时间排序去重（每标题带首现时间）+
  月度分布；产物挂 db_fingerprint；
- 同库快照重跑，产物 byte 级一致（可复现红线，SOP-T1-5）。
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester.dbmeta import db_fingerprint
from harvester.indexing import SCHEMA, index_session
from harvester.models import Message, SessionRecord
from harvester.topics import (add_members, ensure_topics_db, register_topic,
                              remove_member, show_topic, title_chain)


def _fixture_db(tmp: Path) -> Path:
    """4 个会话，标题刻意跨月重复，验证去重与首现排序。"""
    db = tmp / "t1_test.db"
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)
    rows = [
        ("src:s1", "2026-06-10 10:00:00", "节奏初探"),
        ("src:s2", "2026-07-01 09:00:00", "氛围铺陈"),
        ("src:s3", "2026-07-15 09:00:00", "节奏初探"),   # 同标题：晚于 s1
        ("src:s4", "2026-07-20 09:00:00", "从节奏到意象"),
    ]
    for sid, ts, title in rows:
        index_session(con, SessionRecord(
            source="src", session_id=sid.split(":")[1], title=title,
            created_at=ts, updated_at=ts,
            messages=[Message(role="user", text="u")]))
    con.commit()
    con.close()
    return db


class TestTopicsRegistry(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.meta = ensure_topics_db(Path(self.tmp.name) / "topics_meta.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_register_and_show(self):
        tid = register_topic(self.meta, "叙事节奏", keywords=["节奏", "叙事"])
        d = show_topic(self.meta, tid)
        self.assertEqual(d["name"], "叙事节奏")
        self.assertEqual(d["keywords"], ["节奏", "叙事"])
        self.assertEqual(d["members"], [])

    def test_add_remove_members(self):
        tid = register_topic(self.meta, "T")
        n = add_members(self.meta, tid, ["src:s1", "src:s2"])
        self.assertEqual(n, 2)
        # 重复 add 幂等
        self.assertEqual(add_members(self.meta, tid, ["src:s1"]), 0)
        remove_member(self.meta, tid, "src:s1")
        d = show_topic(self.meta, tid)
        self.assertEqual([m["sid"] for m in d["members"]], ["src:s2"])

    def test_unknown_topic_raises(self):
        with self.assertRaises(KeyError):
            show_topic(self.meta, "tp-nope")


class TestTitleChain(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.meta = ensure_topics_db(self.root / "topics_meta.db")
        self.db = _fixture_db(self.root)
        self.tid = register_topic(self.meta, "叙事节奏", keywords=["节奏"])
        add_members(self.meta, self.tid,
                    ["src:s1", "src:s2", "src:s3", "src:s4"])

    def tearDown(self):
        self.tmp.cleanup()

    def test_chain_sorted_dedup_monthly(self):
        chain = title_chain(self.meta, self.tid, self.db)
        titles = [r["title"] for r in chain["rows"]]
        # s3 的"节奏初探"被去重（s1 更早）
        self.assertEqual(titles, ["节奏初探", "氛围铺陈", "从节奏到意象"])
        first = {r["title"]: r["first_seen"] for r in chain["rows"]}
        self.assertTrue(first["节奏初探"].startswith("2026-06"))
        self.assertEqual(chain["monthly"], {"2026-06": 1, "2026-07": 3})
        self.assertEqual(chain["members"], 4)
        self.assertIn("db_fingerprint", chain)

    def test_reproducible_bytes(self):
        """同库快照重跑产物 byte 级一致（SOP-T1-5）。"""
        from harvester.topics import render_title_chain
        c1 = title_chain(self.meta, self.tid, self.db)
        c2 = title_chain(self.meta, self.tid, self.db)
        self.assertEqual(render_title_chain(c1), render_title_chain(c2))
        # fingerprint 冻结后仍一致
        fp = db_fingerprint(self.db)
        self.assertEqual(fp["sessions"], 4)

    def test_member_not_in_db_counts_missing(self):
        add_members(self.meta, self.tid, ["src:ghost"])
        chain = title_chain(self.meta, self.tid, self.db)
        self.assertEqual(chain["members"], 5)
        self.assertEqual(chain["missing"], ["src:ghost"])


if __name__ == "__main__":
    unittest.main()
