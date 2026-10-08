# -*- coding: utf-8 -*-
"""v0.22 T3 topic-chain 长文独立校验器测试（SOP-T3 第 3 步）。

验收口径（PLAN §4 T3 / SOP-T3）：
- 独立校验器（不复用 §8 卡片校验，PLAN §0.2-A 裁决：链是长文不是卡片）；
- 锚点逐个可回溯：node.sid 必须是主题成员、在索引库存在、turn 在该会话
  user 消息数范围内（turn=split_turns 口径，user 消息 1-based）；
- stages 必须有成员证据（每 stage 至少 1 个 node）；
- frontmatter 必填项检查：topic/topic_id/members/anchors/generated_from/
  prompt_version。
测试全用合成库（T2 测试同款 fixture），不依赖真实库与真实草稿。
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester.indexing import SCHEMA, index_session
from harvester.models import Message, SessionRecord
from harvester.topics import add_members, ensure_topics_db, register_topic
from harvester.topicchain import load_chain, validate_chain


def _fixture_db(tmp: Path) -> Path:
    """2 个会话：src:s1 含 user×3 + assistant×1；src:s2 含 user×1。"""
    db = tmp / "t3_test.db"
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)
    data = [
        ("s1", "2026-06-10 10:00:00", "节奏初探",
         [Message(role="user", text="u1-a"),
          Message(role="assistant", text="a1"),
          Message(role="user", text="u1-b"),
          Message(role="user", text="u1-c")]),
        ("s2", "2026-07-01 09:00:00", "氛围铺陈",
         [Message(role="user", text="u2-a")]),
    ]
    for session_id, ts, title, msgs in data:
        index_session(con, SessionRecord(
            source="src", session_id=session_id, title=title,
            created_at=ts, updated_at=ts, messages=msgs))
    con.commit()
    con.close()
    return db


def _fm(topic="叙事节奏", topic_id="tp-test-001", members=None,
        anchors=None, generated_from=None, prompt_version="t3-pack-test"):
    return {
        "topic": topic,
        "topic_id": topic_id,
        "members": members if members is not None else [
            "src:s1", "src:s2"],
        "anchors": anchors if anchors is not None else [
            {"stage": "阶段一", "span": "2026-06 ~ 2026-07",
             "nodes": [
                 {"sid": "src:s1", "turn": 1, "note": "起点"},
                 {"sid": "src:s2", "turn": 1, "note": "收束"},
             ]},
        ],
        "generated_from": generated_from if generated_from is not None else {
            "db_mtime": "2026-10-08 15:48:53", "sessions": 2,
            "steps": 6, "errors": 0},
        "prompt_version": prompt_version,
    }


def _write_chain(root: Path, fm: dict, body: str = "正文内容。") -> Path:
    import yaml
    p = root / "chain-test.md"
    p.write_text("---\n" + yaml.safe_dump(fm, allow_unicode=True,
                                         sort_keys=False)
                 + "---\n" + body, encoding="utf-8", newline="\n")
    return p


class _T3Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.meta = ensure_topics_db(self.root / "topics_meta.db")
        self.db = _fixture_db(self.root)
        self.tid = register_topic(self.meta, "叙事节奏", keywords=["节奏"])
        add_members(self.meta, self.tid, ["src:s1", "src:s2"])

    def tearDown(self):
        self.tmp.cleanup()

    def _validate(self, fm: dict, body: str = "正文内容。") -> dict:
        p = _write_chain(self.root, fm, body)
        return validate_chain(p, self.meta, self.db)

    def _errs(self, fm: dict, body: str = "正文内容。") -> list[str]:
        r = self._validate(fm, body)
        self.assertFalse(r["ok"], f"expected errors, got: {r}")
        return r["errors"]


class TestValidChain(_T3Base):

    def test_valid_chain_passes(self):
        r = self._validate(_fm())
        self.assertTrue(r["ok"], f"errors: {r['errors']}")
        self.assertEqual(r["stats"]["members"], 2)
        self.assertEqual(r["stats"]["stages"], 1)
        self.assertEqual(r["stats"]["nodes"], 2)

    def test_load_chain_roundtrip(self):
        p = _write_chain(self.root, _fm())
        d = load_chain(p)
        self.assertEqual(d["fm"]["topic_id"], "tp-test-001")
        self.assertIn("正文内容", d["body"])


class TestFrontmatter(_T3Base):

    def test_missing_prompt_version(self):
        fm = _fm()
        del fm["prompt_version"]
        errs = self._errs(fm)
        self.assertTrue(any("prompt_version" in e for e in errs), errs)

    def test_missing_topic_id(self):
        fm = _fm()
        del fm["topic_id"]
        errs = self._errs(fm)
        self.assertTrue(any("topic_id" in e for e in errs), errs)

    def test_empty_members(self):
        errs = self._errs(_fm(members=[]))
        self.assertTrue(any("members" in e for e in errs), errs)

    def test_empty_anchors(self):
        errs = self._errs(_fm(anchors=[]))
        self.assertTrue(any("anchors" in e for e in errs), errs)

    def test_generated_from_not_dict(self):
        errs = self._errs(_fm(generated_from="x"))
        self.assertTrue(any("generated_from" in e for e in errs), errs)

    def test_generated_from_missing_db_mtime(self):
        gf = {"sessions": 2, "steps": 6}
        errs = self._errs(_fm(generated_from=gf))
        self.assertTrue(any("db_mtime" in e for e in errs), errs)

    def test_empty_body(self):
        errs = self._errs(_fm(), body="")
        self.assertTrue(any("正文" in e or "body" in e for e in errs), errs)


class TestAnchors(_T3Base):

    def test_node_turn_out_of_range(self):
        fm = _fm(anchors=[{"stage": "阶段一", "nodes": [
            {"sid": "src:s1", "turn": 99, "note": "越界"}]}])
        errs = self._errs(fm)
        self.assertTrue(any("src:s1" in e and "99" in e for e in errs), errs)

    def test_node_turn_zero(self):
        fm = _fm(anchors=[{"stage": "阶段一", "nodes": [
            {"sid": "src:s1", "turn": 0, "note": "非法"}]}])
        errs = self._errs(fm)
        self.assertTrue(any("turn" in e for e in errs), errs)

    def test_node_sid_not_member(self):
        fm = _fm(anchors=[{"stage": "阶段一", "nodes": [
            {"sid": "src:other", "turn": 1, "note": "非成员"}]}])
        errs = self._errs(fm)
        self.assertTrue(any("成员" in e for e in errs), errs)

    def test_node_sid_member_but_absent_from_db(self):
        fm = _fm(members=["src:s1", "src:s2", "src:ghost"],
                 anchors=[{"stage": "阶段一", "nodes": [
                     {"sid": "src:ghost", "turn": 1, "note": "库中不存在"}]}])
        errs = self._errs(fm)
        self.assertTrue(any("src:ghost" in e for e in errs), errs)

    def test_stage_without_nodes(self):
        fm = _fm(anchors=[{"stage": "空阶段", "nodes": []}])
        errs = self._errs(fm)
        self.assertTrue(any("空阶段" in e for e in errs), errs)

    def test_node_missing_sid_or_turn(self):
        fm = _fm(anchors=[{"stage": "阶段一", "nodes": [
            {"turn": 1, "note": "缺 sid"},
            {"sid": "src:s1", "note": "缺 turn"}]}])
        errs = self._errs(fm)
        self.assertTrue(len(errs) >= 2, errs)


class TestTurnSemantics(_T3Base):

    def test_turn_counts_user_messages_only(self):
        """turn 上界 = 该会话 user 消息数（split_turns 口径）：
        src:s1 有 3 条 user → turn 3 合法、turn 4 越界。"""
        fm = _fm(anchors=[{"stage": "阶段一", "nodes": [
            {"sid": "src:s1", "turn": 3, "note": "合法上界"}]}])
        r = self._validate(fm)
        self.assertTrue(r["ok"], f"errors: {r['errors']}")
        fm["anchors"][0]["nodes"][0]["turn"] = 4
        errs = self._errs(fm)
        self.assertTrue(any("4" in e for e in errs), errs)


if __name__ == "__main__":
    unittest.main()
