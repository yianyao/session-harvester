# -*- coding: utf-8 -*-
"""v0.45 MCP 链工具测试（B 组续）——`topic_export` / `chain_read`。

单独一个文件的原因：这两条要读 **chain frontmatter**，而 `load_chain` 需要
PyYAML。按 H9 口径，依赖缺失就该在**导入期显式失败**（error），不静默跳过——
所以把需要它的测试与 `test_v45_mcp_tools.py`（不依赖 PyYAML、含漂移门）分开，
这样无 PyYAML 环境下**漂移门照样跑**，只有本文件转成 error。

判据：链读不到时必须 fail loud（不是返回空壳）；载荷与
`apiserve.api_topic_chain` 逐字段一致。
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import yaml  # noqa: F401 —— 本文件**依赖 PyYAML**：缺它就该在导入期显式失败（H9）

from harvester import mcpserver as mcp
from harvester.indexing import SCHEMA, index_session
from harvester.models import Message, SessionRecord
from harvester.topics import add_members, ensure_topics_db, register_topic

CHAIN = """\
---
topic: 主题甲
topic_id: {tid}
members:
  - src:s1
anchors:
  - stage: 阶段一
    span: 2026-01-01 ~ 2026-01-02
    nodes:
      - sid: src:s1
        turn: 1
        note: 起手
generated_from:
  db_mtime: "2026-10-10 00:00:00"
prompt_version: v-test
---

# 思维链：主题甲

开篇一句「丁樾瘫坐在椅子上回气」（{{src:s1, turn 1}}）。
"""


class TestChainTools(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "harvester.db"
        con = sqlite3.connect(str(self.db))
        con.executescript(SCHEMA)
        index_session(con, SessionRecord(
            source="src", session_id="s1", title="甲会话",
            created_at="2026-01-01 10:00:00", updated_at="2026-01-01 10:00:00",
            messages=[Message(role="user", text="丁樾瘫坐在椅子上回气。")]))
        con.commit()
        con.close()
        self.topics = ensure_topics_db(root / "topics_meta.db")
        self.tid = register_topic(self.topics, "主题甲", keywords=["节奏"])
        add_members(self.topics, self.tid, ["src:s1"], evidence="测试")
        self.chains = root / "chains"
        self.chains.mkdir()
        (self.chains / "chain-主题甲.md").write_text(
            CHAIN.format(tid=self.tid), encoding="utf-8")
        self.srv = mcp.HarvesterMcpServer(
            None, str(self.db), topics_meta=str(self.topics),
            chain_root=str(self.chains))

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def test_chain_read_matches_http_payload(self):
        from harvester.apiserve import api_topic_chain
        con = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            http = api_topic_chain(con, self.db, self.tid, self.topics,
                                   self.chains)
        finally:
            con.close()
        got = json.loads(self.srv._dispatch("chain_read", {"topic_id": self.tid}))
        self.assertEqual(got, http)
        self.assertEqual(got["fm"]["topic_id"], self.tid)
        self.assertEqual(got["chain_count"], 1)
        self.assertEqual(got["chains"][0]["stages"], 1)
        self.assertEqual(got["chains"][0]["nodes"], 1)

    def test_chain_read_fails_loud_when_absent(self):
        with self.assertRaises(ValueError):
            self.srv._dispatch("chain_read", {"topic_id": "tp-ghost"})

    def test_topic_export_is_the_cli_json_outlet(self):
        from harvester.topicexport import render_topic_json, topic_bundle
        got = self.srv._dispatch("topic_export", {"topic_id": self.tid})
        want = render_topic_json(topic_bundle(self.topics, self.db, self.tid,
                                              chain_root=self.chains))
        self.assertEqual(got, want)          # 同一函数、同一序列化
        d = json.loads(got)
        self.assertEqual(d["topic"]["id"], self.tid)
        self.assertEqual(d["topic"]["members_count"], 1)
        self.assertEqual(len(d["anchors"]), 1)   # 锚点来自已发布 chain

    def test_topic_export_unknown_topic_is_error(self):
        with self.assertRaises(KeyError):
            self.srv._dispatch("topic_export", {"topic_id": "tp-ghost"})


if __name__ == "__main__":
    unittest.main()
