# -*- coding: utf-8 -*-
"""v0.29 主题结构化导出（topic.json，给 Agent 消费）测试。

锁住：schema 与顶层字段、成员带证据与索引状态、**多链锚点合并视图**、
来源/月度分布、health 自检（未登记零散 / 不在索引库的成员）、
以及**同库快照重跑除 generated_at 外逐字节一致**（可复现红线）。
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester.apiserve import API_VERSION  # noqa: F401  （确认导入路径可用）
from harvester.indexing import SCHEMA, index_session
from harvester.models import Message, SessionRecord
from harvester.topicexport import SCHEMA as T_SCHEMA
from harvester.topicexport import render_topic_json, topic_bundle
from harvester.topics import add_members, ensure_topics_db, register_topic

CHAIN = """\
---
topic: {name}
topic_id: {tid}
members:
  - src:s1
anchors:
  - stage: {stage}
    span: 2026-01-01 ~ 2026-02-01
    nodes:
      - sid: src:s1
        turn: 1
        note: {note}
generated_from:
  db_mtime: "2026-10-10 00:00:00"
prompt_version: v-test
---

# 思维链：{name}
正文
"""


class TestTopicExport(unittest.TestCase):

    def setUp(self):
        # 缺 PyYAML 时显式报错（与其余 chain 类测试同口径，不做降级解析）：
        # 否则 topic_bundle 会把"读不出 chain"吞成空链，测试报的是断言失败，
        # 看不出真正原因（base 解释器当场抓出 failures=1）
        from harvester.topicchain import _HAS_YAML
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
            source="deepseek-export", session_id="s1", title="甲会话",
            created_at="2026-01-05 10:00:00", updated_at="2026-01-06 10:00:00",
            messages=[Message(role="user", text="u1"),
                      Message(role="assistant", text="a1"),
                      Message(role="user", text="u2")]))
        index_session(con, SessionRecord(
            source="yuanbao-raw", session_id="s2", title="乙会话",
            created_at="2026-02-07 10:00:00", updated_at="2026-02-07 10:00:00",
            messages=[Message(role="user", text="u3")]))
        con.commit()
        con.close()
        self.meta = ensure_topics_db(root / "topics_meta.db")
        self.t = register_topic(self.meta, "主题甲", keywords=["k1", "k2"])
        add_members(self.meta, self.t, ["deepseek-export:s1", "yuanbao-raw:s2"],
                    evidence="书名口径")
        add_members(self.meta, self.t, ["src:ghost"], evidence="未入索引库")
        self.chains = root / "chains"
        self.chains.mkdir()
        (self.chains / "chain-a-节奏.md").write_text(
            CHAIN.format(name="节奏", tid=self.t, stage="技法一",
                         note="节奏"),
            encoding="utf-8")
        (self.chains / "chain-b-本体.md").write_text(
            CHAIN.format(name="本体", tid=self.t, stage="本体一",
                         note="本体"),
            encoding="utf-8")

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def test_schema_and_top_level_fields(self):
        b = topic_bundle(self.meta, self.db, self.t, chain_root=self.chains)
        self.assertEqual(b["schema"], T_SCHEMA)
        for k in ("topic", "activity", "sources", "members", "chains",
                  "anchors", "health", "howto", "limits", "db_fingerprint"):
            self.assertIn(k, b)
        self.assertEqual(b["topic"]["id"], self.t)
        self.assertEqual(b["topic"]["keywords"], ["k1", "k2"])

    def test_members_carry_evidence_and_index_state(self):
        b = topic_bundle(self.meta, self.db, self.t, chain_root=self.chains)
        by = {m["sid"]: m for m in b["members"]}
        self.assertEqual(by["deepseek-export:s1"]["title"], "甲会话")
        self.assertEqual(by["deepseek-export:s1"]["evidence"], "书名口径")
        self.assertTrue(by["deepseek-export:s1"]["in_index"])
        self.assertFalse(by["src:ghost"]["in_index"])          # 未入索引库
        self.assertEqual(b["health"]["members_not_in_index"], ["src:ghost"])
        # 成员按时间序
        self.assertEqual([m["sid"] for m in b["members"][:2]],
                         ["deepseek-export:s1", "yuanbao-raw:s2"])

    def test_activity_and_sources(self):
        b = topic_bundle(self.meta, self.db, self.t, chain_root=self.chains)
        self.assertEqual(b["activity"]["first"], "2026-01-05 10:00:00")
        self.assertEqual(b["activity"]["last"], "2026-02-07 10:00:00")
        self.assertEqual(b["activity"]["months"], {"2026-01": 1, "2026-02": 1})
        self.assertEqual(b["sources"],
                         {"deepseek-export": 1, "yuanbao-raw": 1})

    def test_multichain_anchors_merged_with_chain_label(self):
        b = topic_bundle(self.meta, self.db, self.t, chain_root=self.chains)
        self.assertEqual([c["name"] for c in b["chains"]],
                         ["思维链：节奏", "思维链：本体"])
        self.assertEqual([a["chain"] for a in b["anchors"]],
                         ["思维链：节奏", "思维链：本体"])
        self.assertEqual([a["stage"] for a in b["anchors"]],
                         ["技法一", "本体一"])
        self.assertEqual(b["chains"][0]["nodes"], 1)
        self.assertNotIn("_anchors", b["chains"][0])           # 内部字段不外泄

    def test_health_flags_noise_registered_members(self):
        b = topic_bundle(self.meta, self.db, self.t, chain_root=self.chains,
                         noise_sids={"yuanbao-raw:s2"})
        self.assertEqual(b["health"]["noise_registered_members"],
                         ["yuanbao-raw:s2"])

    def test_howto_gives_commands_with_topic_id(self):
        b = topic_bundle(self.meta, self.db, self.t, chain_root=self.chains)
        h = b["howto"]
        self.assertIn(self.t, h["pack_coarse"])
        self.assertIn("--level coarse", h["pack_coarse"])
        self.assertIn("chain-validate", h["validate_chain"])
        # 只给命令文本，不含任何执行语义
        self.assertNotIn("subprocess", json.dumps(h))
        self.assertNotIn("os.system", json.dumps(h))

    def test_missing_topic_fails_loud(self):
        with self.assertRaises(KeyError):
            topic_bundle(self.meta, self.db, "tp-nonexistent")

    def test_reproducible_except_generated_at(self):
        """同库快照重跑：去掉 generated_at 后逐字节一致（可复现红线）。"""
        a = render_topic_json(topic_bundle(self.meta, self.db, self.t,
                                           chain_root=self.chains))
        b = render_topic_json(topic_bundle(self.meta, self.db, self.t,
                                           chain_root=self.chains))
        da = json.loads(a)
        db_ = json.loads(b)
        da.pop("generated_at")
        db_.pop("generated_at")
        self.assertEqual(da, db_)
        # 显式传 generated_at 时完全一致
        x = render_topic_json(topic_bundle(self.meta, self.db, self.t,
                                           chain_root=self.chains,
                                           generated_at="T"))
        y = render_topic_json(topic_bundle(self.meta, self.db, self.t,
                                           chain_root=self.chains,
                                           generated_at="T"))
        self.assertEqual(x, y)

    def test_json_is_machine_parseable_with_cjk_preserved(self):
        text = render_topic_json(topic_bundle(self.meta, self.db, self.t,
                                              chain_root=self.chains))
        self.assertIn("主题甲", text)          # 不转义，Agent 直接可读
        self.assertIsInstance(json.loads(text), dict)


class TestKeywordFinalize(unittest.TestCase):
    """合并会把各源的聚类碎片关键词并进来（实测小说主题 237 个，多为 bigram
    碎片）→ 需要"人工定稿关键词"这一步，工具只做确定性写入。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.meta = ensure_topics_db(Path(self.tmp.name) / "topics_meta.db")
        self.t = register_topic(self.meta, "主题甲",
                                keywords=["碎片1", "碎片2", "碎片3"])

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def test_set_keywords_overwrites_and_reports(self):
        from harvester.topics import set_keywords
        r = set_keywords(self.meta, self.t, ["甲", "乙"])
        self.assertEqual(r["old"], ["碎片1", "碎片2", "碎片3"])
        self.assertEqual(r["new"], ["甲", "乙"])
        from harvester.topics import show_topic
        self.assertEqual(show_topic(self.meta, self.t)["keywords"], ["甲", "乙"])
        # 幂等：同一份列表重跑结果一致
        set_keywords(self.meta, self.t, ["甲", "乙"])
        self.assertEqual(show_topic(self.meta, self.t)["keywords"], ["甲", "乙"])

    def test_set_keywords_cleans_blank_and_fails_loud(self):
        from harvester.topics import set_keywords, show_topic
        set_keywords(self.meta, self.t, [" 甲 ", "", "  ", "乙"])
        self.assertEqual(show_topic(self.meta, self.t)["keywords"], ["甲", "乙"])
        set_keywords(self.meta, self.t, [])          # 允许清空
        self.assertEqual(show_topic(self.meta, self.t)["keywords"], [])
        with self.assertRaises(KeyError):
            set_keywords(self.meta, "tp-ghost", ["x"])


if __name__ == "__main__":
    unittest.main()
