# -*- coding: utf-8 -*-
"""v0.25：一个主题多条 chain（additive）测试。

背景：语义梳理把小说线并成一个主题后，`tp-20261008-010` 同时拥有
「叙事节奏」与「吾好梦中救人」两条 chain；旧实现只返回文件名序第一条
（第二条在 view 里根本看不见）。本组测试锁住新契约：
- 旧字段（fm/body/chain_path）= 第一条，语义不变（只增不删红线）；
- 新增 chains（全量，含 name/stages/nodes）与 chain_count；
- api_topics 在给 chain_root 时带 chains_count / chain_names。
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester.apiserve import api_topic_chain, api_topics
from harvester.indexing import SCHEMA, index_session
from harvester.models import Message, SessionRecord
from harvester.topicchain import _HAS_YAML
from harvester.topics import add_members, ensure_topics_db, register_topic

FM = """\
---
topic: {name}
topic_id: {tid}
members:
  - src:s1
anchors:
  - stage: 阶段一
    span: 2026-01-01 ~ 2026-01-02
    nodes:
      - sid: src:s1
        turn: 1
        note: n1
      - sid: src:s1
        turn: 2
        note: n2
generated_from:
  db_mtime: "2026-10-10 00:00:00"
prompt_version: v-test
---

# 思维链：{name}

正文 {name}
"""


class TestMultiChainPerTopic(unittest.TestCase):

    def setUp(self):
        # 与其余 chain 测试同口径：缺 PyYAML 显式报错，不走降级解析（H9）。
        # 否则 api_topic_chain 会把"环境缺库"静默吞成 404，测试报的是
        # TypeError/断言失败，看不出真正原因。
        if not _HAS_YAML:
            raise RuntimeError(
                "topic-chain 校验需要 PyYAML（venv 解释器，H9）；"
                "不提供降级解析——块结构静默误读比报错更危险")
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "h.db"
        con = sqlite3.connect(str(self.db))
        con.executescript(SCHEMA)
        index_session(con, SessionRecord(
            source="src", session_id="s1", title="会话一",
            created_at="2026-01-01 10:00:00", updated_at="2026-01-02 10:00:00",
            messages=[Message(role="user", text="u1"),
                      Message(role="assistant", text="a1"),
                      Message(role="user", text="u2")]))
        con.commit()
        con.close()
        self.meta = ensure_topics_db(root / "topics_meta.db")
        self.t1 = register_topic(self.meta, "合并后的小说主题")
        self.t2 = register_topic(self.meta, "无链主题")
        add_members(self.meta, self.t1, ["src:s1"], evidence="e")
        self.chains = root / "chains"
        self.chains.mkdir()
        # 文件名序：a- 在 z- 之前 → 旧字段必须取 a-
        (self.chains / "chain-a-叙事节奏.md").write_text(
            FM.format(name="叙事节奏", tid=self.t1), encoding="utf-8")
        (self.chains / "chain-z-吾好梦中救人.md").write_text(
            FM.format(name="吾好梦中救人", tid=self.t1), encoding="utf-8")
        self.con = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)

    def tearDown(self):
        self.con.close()
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def test_legacy_fields_keep_first_match(self):
        d = api_topic_chain(self.con, self.db, self.t1, self.meta, self.chains)
        self.assertEqual(d["fm"]["topic"], "叙事节奏")      # 旧字段语义不变
        self.assertTrue(d["chain_path"].endswith("chain-a-叙事节奏.md"))
        self.assertIn("正文 叙事节奏", d["body"])

    def test_chains_array_is_complete_and_counted(self):
        d = api_topic_chain(self.con, self.db, self.t1, self.meta, self.chains)
        self.assertEqual(d["chain_count"], 2)
        # name 取正文 H1（同主题多条 chain 靠它区分），而非同名 topic
        self.assertEqual([c["name"] for c in d["chains"]],
                         ["思维链：叙事节奏", "思维链：吾好梦中救人"])
        for c in d["chains"]:
            self.assertEqual(c["stages"], 1)
            self.assertEqual(c["nodes"], 2)
            self.assertEqual(c["prompt_version"], "v-test")
            self.assertIn("body", c)

    def test_topic_without_chain_returns_none(self):
        self.assertIsNone(
            api_topic_chain(self.con, self.db, self.t2, self.meta, self.chains))

    def test_api_topics_reports_chain_count(self):
        d = api_topics(self.con, self.db, self.meta, self.chains)
        by_id = {t["id"]: t for t in d["topics"]}
        self.assertEqual(by_id[self.t1]["chains_count"], 2)
        self.assertEqual(by_id[self.t1]["chain_names"],
                         ["思维链：叙事节奏", "思维链：吾好梦中救人"])
        self.assertEqual(by_id[self.t2]["chains_count"], 0)
        self.assertEqual(by_id[self.t2]["chain_names"], [])

    def test_api_topics_without_chain_root_still_works(self):
        """旧调用（3 参）不炸：chains_count 恒 0（降级不破旧上游）。"""
        d = api_topics(self.con, self.db, self.meta)
        self.assertTrue(all(t["chains_count"] == 0 for t in d["topics"]))


if __name__ == "__main__":
    unittest.main()
