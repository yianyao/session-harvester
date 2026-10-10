# -*- coding: utf-8 -*-
"""v0.31 chain-audit 测试：两道内容门（引文逐字 / 锚点语义）。

这两道门此前是 docs/reports 下的一次性脚本，但每写一条 chain 都要跑，
且都抓到过真错：
- 引文门：子代理把 bigram 文本当原文，引文经"还原"后**不是逐字**；
  还有压缩改写（"该夸的夸，该骂的骂"漏掉后半句）。
- 锚点门：note 写着"王德荣与沈望的剧组旧交"，而该 turn 讲的是"锚点的
  心理学依据"——整条**挂错回合**；`chain-validate` 只查结构，查不出这个。
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import yaml  # noqa: F401 —— 本测试**依赖 PyYAML**：缺它就该在导入期显式失败（H9），
# 而不是悄悄跳过（`chain-audit` 的两道门都要读 chain frontmatter）

from harvester.chainaudit import audit_chain, render_audit
from harvester.indexing import SCHEMA, index_session
from harvester.models import Message, SessionRecord
from harvester.topics import ensure_topics_db

FM = """\
---
topic: 主题甲
topic_id: tp-x
members:
  - src:s1
anchors:
  - stage: 阶段一
    span: 2026-01-01 ~ 2026-01-02
    nodes:
      - sid: src:s1
        turn: 1
        note: {note1}
      - sid: src:s1
        turn: 2
        note: {note2}
generated_from:
  db_mtime: "2026-10-10 00:00:00"
prompt_version: v-test
---

# 思维链：主题甲

{body}
"""

BODY_OK = "他写道「丁樾瘫坐在椅子上回气」，这是原文逐字。"
BODY_BAD = "他写道「丁樾瘫坐在椅子上**喘气**」，这是改写过的。"


class TestChainAudit(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "h.db"
        con = sqlite3.connect(str(self.db))
        con.executescript(SCHEMA)
        index_session(con, SessionRecord(
            source="src", session_id="s1", title="甲会话",
            created_at="2026-01-01 10:00:00", updated_at="2026-01-01 10:00:00",
            messages=[Message(role="user", text="丁樾瘫坐在椅子上回气。"),
                      Message(role="assistant", text="收到。"),
                      Message(role="user", text="设计王德荣和沈望的剧组旧交")]))
        con.commit()
        con.close()
        self.meta = ensure_topics_db(root / "topics_meta.db")
        self.chain = root / "chain-x.md"

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def _write(self, body, note1="丁樾回气的动作描写", note2="王德荣与沈望"):
        self.chain.write_text(FM.format(note1=note1, note2=note2, body=body),
                              encoding="utf-8")

    def test_clean_chain_passes_both_gates(self):
        self._write(BODY_OK)
        r = audit_chain(self.chain, self.db)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["quotes"]["exact"], 1)
        self.assertEqual(r["quotes"]["misses"], [])
        self.assertEqual(r["anchors"]["suspicious"], [])

    def test_rewritten_quote_fails_quote_gate(self):
        """引文被改写 → 未命中 → 整体不合格（退出码非 0 的依据）。"""
        self._write(BODY_BAD)
        r = audit_chain(self.chain, self.db)
        self.assertFalse(r["ok"])
        self.assertEqual(len(r["quotes"]["misses"]), 1)
        self.assertIn("喘气", r["quotes"]["misses"][0])

    def test_quote_gate_ignores_markdown_emphasis(self):
        """`**加粗**` 是排版标记不是数据：加粗后仍应逐字命中。"""
        self._write("他写道「丁樾瘫坐在椅子上**回气**」。")
        r = audit_chain(self.chain, self.db)
        self.assertTrue(r["quotes"]["ok"], r["quotes"])

    def test_mismatched_anchor_is_flagged(self):
        """turn 1 的 note 说"锚点的心理学依据"（原文里没有），turn 2 的 note
        与原文对得上 → 只应有 1 条疑似错配。"""
        self._write(BODY_OK, note1="锚点的心理学与哲学依据",
                    note2="王德荣与沈望的剧组旧交")
        r = audit_chain(self.chain, self.db)
        self.assertFalse(r["anchors"]["ok"])
        sus = r["anchors"]["suspicious"]
        self.assertEqual(len(sus), 1)
        self.assertEqual(sus[0]["turn"], 1)
        self.assertEqual(sus[0]["overlap"], [])
        self.assertFalse(r["ok"])          # 有疑似错配 → 不通过

    def test_correct_anchor_has_overlap(self):
        self._write(BODY_OK, note1="丁樾瘫坐回气", note2="王德荣与沈望")
        r = audit_chain(self.chain, self.db)
        self.assertTrue(r["anchors"]["ok"], r["anchors"]["suspicious"])
        first = r["anchors"]["nodes"][0]
        self.assertTrue(first["overlap"])

    def test_gates_can_be_skipped_independently(self):
        self._write(BODY_BAD, note1="完全无关的说明", note2="也无关")
        r = audit_chain(self.chain, self.db, anchors=False)
        self.assertFalse(r["quotes"]["ok"])
        self.assertNotIn("anchors", r)
        r2 = audit_chain(self.chain, self.db, quotes=False)
        self.assertIn("quotes", r2) is False if False else None
        self.assertNotIn("quotes", r2)
        self.assertIn("anchors", r2)

    def test_report_states_limits(self):
        self._write(BODY_BAD, note1="完全无关的说明", note2="也无关")
        txt = render_audit(audit_chain(self.chain, self.db))
        self.assertIn("引文逐字门", txt)
        self.assertIn("锚点语义门", txt)
        self.assertIn("工具不判语义", txt)
        self.assertIn("未命中", txt)


if __name__ == "__main__":
    unittest.main()
