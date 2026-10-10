# -*- coding: utf-8 -*-
"""v0.24 #10 主题合并 + 蒸馏取文口径（raw 优先）测试。

两处均是"新维度接入必须复用既有口径"的产物：
- merge_topics：把"并成一个主题"（用户 2026-10-10 裁决 skill 自学习三主题
  合一）做成确定性操作，成员证据带来源尾注可追溯、事务内完成；
- render_transcript：H3/H38 的 raw 优先契约在代码里曾写成 text 优先
  （真库 33832/62899 条 text 为检索 bigram），此处把契约变成断言。
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester.drafting import render_transcript
from harvester.indexing import SCHEMA, index_session
from harvester.models import Message, SessionRecord
from harvester.topics import (add_members, ensure_topics_db, list_topics,
                              merge_topics, register_topic, show_topic)


class TestTopicMerge(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.meta = ensure_topics_db(Path(self.tmp.name) / "topics_meta.db")
        self.t = register_topic(self.meta, "目标主题", keywords=["甲"])
        self.s1 = register_topic(self.meta, "源一", keywords=["乙"])
        self.s2 = register_topic(self.meta, "源二", keywords=["甲", "丙"])
        add_members(self.meta, self.t, ["src:keep"], evidence="目标原有")
        add_members(self.meta, self.s1, ["src:a"], evidence="源一证据")
        add_members(self.meta, self.s1, ["src:keep"], evidence="源一也有")
        add_members(self.meta, self.s1, ["src:shared"], evidence="源一共有")
        add_members(self.meta, self.s2, ["src:b"], evidence="")
        add_members(self.meta, self.s2, ["src:shared"], evidence="源二也有")

    def tearDown(self):
        self.tmp.cleanup()

    def test_merge_unions_and_deletes_sources(self):
        r = merge_topics(self.meta, self.t, [self.s1, self.s2])
        self.assertEqual(r["added"], 3)          # a、shared、b
        self.assertEqual(r["kept"], 1)           # keep 已在目标
        self.assertEqual(r["deleted"], [self.s1, self.s2])
        self.assertEqual(r["keywords"], ["甲", "乙", "丙"])
        d = show_topic(self.meta, self.t)
        self.assertEqual({m["sid"] for m in d["members"]},
                         {"src:keep", "src:a", "src:shared", "src:b"})
        # 两个源共享的成员只并入一次（去重口径），证据取先出现的源
        sids = [m["sid"] for m in d["members"]]
        self.assertEqual(len(sids), len(set(sids)))
        self.assertEqual(sids.count("src:shared"), 1)
        ev = {m["sid"]: m["evidence"] for m in d["members"]}
        self.assertEqual(ev["src:shared"], f"源一共有（合并自 {self.s1}）")
        # target 原有证据不被源覆盖
        self.assertEqual(ev["src:keep"], "目标原有")
        # 并入者证据带来源尾注（可追溯），无证据者只有尾注
        self.assertEqual(ev["src:a"], f"源一证据（合并自 {self.s1}）")
        self.assertEqual(ev["src:b"], f"合并自 {self.s2}")
        # 源主题行已删除
        ids = {t["id"] for t in list_topics(self.meta)}
        self.assertEqual(ids, {self.t})

    def test_merge_keep_sources(self):
        merge_topics(self.meta, self.t, [self.s1, self.s2], delete_sources=False)
        ids = {t["id"] for t in list_topics(self.meta)}
        self.assertEqual(ids, {self.t, self.s1, self.s2})

    def test_merge_fail_loud(self):
        with self.assertRaises(KeyError):        # 源不存在
            merge_topics(self.meta, self.t, ["tp-nonexistent"])
        with self.assertRaises(KeyError):        # 目标不存在
            merge_topics(self.meta, "tp-nonexistent", [self.s1])
        with self.assertRaises(KeyError):        # 源含目标自身
            merge_topics(self.meta, self.t, [self.t])
        # 失败路径不得留下半合并状态
        self.assertEqual(len(list_topics(self.meta)), 3)
        d = show_topic(self.meta, self.t)
        self.assertEqual([m["sid"] for m in d["members"]], ["src:keep"])

    def test_merge_missing_source_leaves_target_untouched(self):
        """先存在的源正常、后一个源不存在 → 整体回滚（单事务）。"""
        with self.assertRaises(KeyError):
            merge_topics(self.meta, self.t, [self.s1, "tp-ghost"])
        d = show_topic(self.meta, self.t)
        self.assertEqual([m["sid"] for m in d["members"]], ["src:keep"])
        self.assertEqual(d["keywords"], ["甲"])
        self.assertEqual(len(list_topics(self.meta)), 3)


class TestTranscriptRawFirst(unittest.TestCase):
    """render_transcript 必须取 raw（H3/H38），不取检索用 bigram 的 text。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"
        con = sqlite3.connect(str(self.db))
        con.executescript(SCHEMA)
        index_session(con, SessionRecord(
            source="src", session_id="s1", title="取文口径",
            created_at="2026-10-10 09:00:00", updated_at="2026-10-10 09:00:00",
            messages=[Message(role="user", text="丁樾瘫坐在椅子上回气。"),
                      Message(role="assistant", text="收到。")]))
        # 模拟真实库：text 列被写成 bigram 检索形态，raw 才是原文
        con.execute("UPDATE messages SET text=? WHERE sid='src:s1' AND rowid=1",
                    ("丁樾 樾瘫 瘫坐 坐在",))
        con.commit()
        con.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_raw_wins_over_bigram_text(self):
        text, _meta = render_transcript(self.db, "src:s1")
        self.assertIn("丁樾瘫坐在椅子上回气。", text)
        self.assertNotIn("樾瘫 瘫坐", text)

    def test_falls_back_to_text_when_raw_empty(self):
        con = sqlite3.connect(str(self.db))
        con.execute("UPDATE messages SET raw='' WHERE sid='src:s1' AND rowid=1")
        con.commit()
        con.close()
        text, _meta = render_transcript(self.db, "src:s1")
        self.assertIn("丁樾 樾瘫 瘫坐 坐在", text)   # 无 raw 时仍可渲染


if __name__ == "__main__":
    unittest.main()
