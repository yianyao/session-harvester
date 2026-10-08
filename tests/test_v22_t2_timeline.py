# -*- coding: utf-8 -*-
"""v0.22 T2 分层时间线 + topic pack 测试（SOP-T2）。

验收口径（PLAN §4 T2 / SOP-T2）：
- 五档（title/coarse/mid/fine/artifact）各自字符预算不爆：title/coarse/mid
  硬预算 + 截断提示；fine/artifact 必须选会话（--sid）按需拉取；
- artifact 档展示同文本版本序（时间序即版本序，H19 不走文件快照）；
- 全部走 raw（H3 text-raw 契约）；
- artifacts 提取（T0-② MVP dsh）：完整非截断（>400 字 old_string 防截断
  回潮，设计稿 §5 验收）、Write 类 old_text=None、口径外调用跳过；
- 未实测登记的源不臆测解析（适配器契约 §2.3）。
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester.artifacts import (ensure_artifacts_db, extract_dsh_records,
                                 extract_session, show_artifacts)
from harvester.indexing import SCHEMA, index_session
from harvester.models import Message, SessionRecord
from harvester.topics import (LEVEL_BUDGETS, LEVELS, add_members,
                              build_topic_packet, ensure_topics_db,
                              register_topic, render_timeline, timeline)


def _fixture_db(tmp: Path, long_text: str = "") -> Path:
    """3 个会话：s1 含 user×2 + assistant×1；s2/s3 各 1 user。
    long_text 非空时写入 s1 首条 user（测预算截断）。"""
    db = tmp / "t2_test.db"
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)
    s1_msgs = [
        Message(role="user", text=long_text or "u1-first"),
        Message(role="assistant", text="a1-only"),
        Message(role="user", text="u1-second"),
    ]
    data = [
        ("src:s1", "2026-06-10 10:00:00", "节奏初探", s1_msgs),
        ("src:s2", "2026-07-01 09:00:00", "氛围铺陈",
         [Message(role="user", text="u2-only")]),
        ("src:s3", "2026-07-15 09:00:00", "从节奏到意象",
         [Message(role="user", text="u3-only")]),
    ]
    for sid, ts, title, msgs in data:
        index_session(con, SessionRecord(
            source="src", session_id=sid.split(":")[1], title=title,
            created_at=ts, updated_at=ts, messages=msgs))
    con.commit()
    con.close()
    return db


class _T2Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.meta = ensure_topics_db(self.root / "topics_meta.db")
        self.db = _fixture_db(self.root)
        self.tid = register_topic(self.meta, "叙事节奏", keywords=["节奏"])
        add_members(self.meta, self.tid,
                    ["src:s1", "src:s2", "src:s3"])

    def tearDown(self):
        self.tmp.cleanup()


class TestLevels(unittest.TestCase):

    def test_levels_and_budgets_registry(self):
        self.assertEqual(LEVELS, ("title", "coarse", "mid", "fine", "artifact"))
        self.assertEqual(LEVEL_BUDGETS,
                         {"title": 2000, "coarse": 10000, "mid": 30000})

    def test_unknown_level_raises(self):
        with tempfile.TemporaryDirectory() as d:
            meta = ensure_topics_db(Path(d) / "m.db")
            tid = register_topic(meta, "T")
            with self.assertRaises(ValueError):
                timeline(meta, tid, db=Path(d) / "nope.db", level="nano")


class TestCoarseMid(_T2Base):

    def test_coarse_rows_shape(self):
        d = timeline(self.meta, self.tid, db=self.db, level="coarse")
        self.assertEqual(d["level"], "coarse")
        self.assertEqual(len(d["rows"]), 3)
        r1 = next(r for r in d["rows"] if r["sid"] == "src:s1")
        self.assertEqual(r1["title"], "节奏初探")
        self.assertEqual(r1["first_user"], "u1-first")   # 首 user
        self.assertEqual(r1["last_asst"], "a1-only")     # 末 assistant
        self.assertIn("db_fingerprint", d)

    def test_mid_user_sequence(self):
        d = timeline(self.meta, self.tid, db=self.db, level="mid")
        texts = [r["text"] for r in d["rows"]]
        # user 消息序（s1 两条 user 都在，assistant 不入）
        self.assertEqual(texts, ["u1-first", "u1-second", "u2-only", "u3-only"])

    def test_render_budget_not_exceeded(self):
        """预算不爆：max_chars 压低预算，render 输出 ≤ 预算且带截断提示。"""
        long_text = "节奏" * 5000          # 10K 字符单条消息（先被 400 截短）
        db = _fixture_db(self.root, long_text=long_text)
        for level, tiny in (("coarse", 600), ("mid", 700)):
            d = timeline(self.meta, self.tid, db=db, level=level,
                         max_chars=tiny)
            out = render_timeline(d)
            self.assertLessEqual(len(out), tiny + 500,
                                 f"{level} 档爆预算：{len(out)}")
            self.assertIn("预算", out)      # 截断提示可见

    def test_render_deterministic(self):
        d1 = timeline(self.meta, self.tid, db=self.db, level="coarse")
        d2 = timeline(self.meta, self.tid, db=self.db, level="coarse")
        self.assertEqual(render_timeline(d1), render_timeline(d2))


class TestFineArtifact(_T2Base):

    def test_fine_requires_sid(self):
        with self.assertRaises(ValueError):
            timeline(self.meta, self.tid, db=self.db, level="fine")

    def test_fine_nonmember_sid_rejected(self):
        with self.assertRaises(ValueError):
            timeline(self.meta, self.tid, db=self.db, level="fine",
                     sid="src:outsider")

    def test_fine_renders_member_turns(self):
        d = timeline(self.meta, self.tid, db=self.db, level="fine",
                     sid="src:s1", max_chars=24000)
        self.assertIn("节奏初探", d["transcript"])
        self.assertIn("u1-first", d["transcript"])
        self.assertIn("u1-second", d["transcript"])

    def test_artifact_requires_sid_and_meta(self):
        with self.assertRaises(ValueError):
            timeline(self.meta, self.tid, db=self.db, level="artifact")
        with self.assertRaises(ValueError):
            timeline(self.meta, self.tid, db=self.db, level="artifact",
                     sid="src:s1")

    def test_artifact_version_series(self):
        """时间序即版本序：同文件按 seq 展示 old→new 对照（H19）。"""
        adb = ensure_artifacts_db(self.root / "artifacts_meta.db")
        con = sqlite3.connect(str(adb))
        rows = [
            ("src:s1", 0, "2026-06-10 10:01:00", "Write", "draft.md",
             None, "v1 全文"),
            ("src:s1", 1, "2026-06-10 10:05:00", "Edit", "draft.md",
             "v1 段落", "v2 段落"),
            ("src:s1", 2, "2026-06-10 10:09:00", "Edit", "draft.md",
             "v2 段落", "v3 段落"),
        ]
        con.executemany("INSERT INTO artifacts VALUES (?,?,?,?,?,?,?)", rows)
        con.commit()
        con.close()
        d = timeline(self.meta, self.tid, db=self.db, level="artifact",
                     sid="src:s1", artifacts_db=adb)
        out = render_timeline(d)
        # 版本序可见：v1→v2→v3 同文件时间序
        self.assertIn("draft.md", out)
        self.assertLess(out.index("v1 段落"), out.index("v2 段落"))
        self.assertLess(out.index("v2 段落"), out.index("v3 段落"))
        self.assertEqual(len(d["rows"]), 3)

    def test_artifact_empty_prompts_extract(self):
        adb = ensure_artifacts_db(self.root / "artifacts_meta.db")
        d = timeline(self.meta, self.tid, db=self.db, level="artifact",
                     sid="src:s1", artifacts_db=adb)
        out = render_timeline(d)
        self.assertIn("artifacts extract", out)   # 指引提取命令


class TestTopicPacket(_T2Base):

    def test_packet_budget_layered(self):
        long_text = "节奏" * 5000
        db = _fixture_db(self.root, long_text=long_text)
        for level in ("coarse", "mid"):
            packet = build_topic_packet(db, self.meta, self.tid, level=level)
            self.assertLessEqual(len(packet), LEVEL_BUDGETS[level] + 1200,
                                 f"{level} pack 爆预算：{len(packet)}")
            self.assertIn("蒸馏包", packet)
            self.assertIn("db_fingerprint", packet)

    def test_packet_fine_on_demand(self):
        packet = build_topic_packet(db=self.db, meta_path=self.meta,
                                    topic_id=self.tid, level="fine",
                                    sid="src:s1")
        self.assertIn("u1-first", packet)


class TestExtractDsh(unittest.TestCase):
    """extract_dsh_records 纯函数（T0-② 设计稿 §2/§5 口径）。"""

    def test_edit_and_write_full_no_truncation(self):
        long_old = "旧" * 600          # >400：防 400 字截断回潮
        long_new = "新" * 650
        lines = [
            {"type": "tool/call", "time": 1760000000000,
             "data": {"tool": "Edit",
                      "arguments": json.dumps({
                          "file_path": "a.md", "old_string": long_old,
                          "new_string": long_new}, ensure_ascii=False)}},
            {"type": "tool/call", "time": 1760000060000,
             "data": {"tool": "Write",
                      "arguments": json.dumps({
                          "file_path": "b.md", "content": "全文"},
                          ensure_ascii=False)}},
            # 口径外：无 old/new 键 → 跳过
            {"type": "tool/call", "time": 1760000120000,
             "data": {"tool": "Bash", "arguments": json.dumps(
                 {"command": "ls"})}},
        ]
        recs = extract_dsh_records(lines)
        self.assertEqual(len(recs), 2)
        self.assertEqual(len(recs[0]["old_text"]), 600)   # 完整非截断
        self.assertEqual(len(recs[0]["new_text"]), 650)
        self.assertIsNone(recs[1]["old_text"])            # Write 类
        self.assertEqual(recs[1]["new_text"], "全文")

    def test_arguments_dict_form(self):
        lines = [{"type": "tool/call", "time": 1,
                  "data": {"tool": "Edit",
                           "arguments": {"file_path": "x.md",
                                         "old_string": "a",
                                         "new_string": "b"}}}]
        recs = extract_dsh_records(lines)
        self.assertEqual(recs[0]["old_text"], "a")


class TestExtractSession(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.meta = ensure_artifacts_db(self.root / "artifacts_meta.db")
        self.db = _fixture_db(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_sid_must_exist_in_index(self):
        with self.assertRaises(KeyError):
            extract_session(self.meta, self.db, "src:ghost")

    def test_unregistered_source_not_guessed(self):
        with self.assertRaises(NotImplementedError):
            extract_session(self.meta, self.db, "src:s1")

    def test_roundtrip_via_monkeypatched_lines(self):
        import harvester.artifacts as A
        lines = [{"type": "tool/call", "time": 1760000000000,
                  "data": {"tool": "Edit",
                           "arguments": json.dumps({
                               "file_path": "a.md", "old_string": "旧" * 500,
                               "new_string": "新"}, ensure_ascii=False)}}]
        orig = A._iter_dsh_lines
        A._iter_dsh_lines = lambda sid: iter(lines)
        try:
            n = extract_session(self.meta, self.db, "src:s1")
        finally:
            A._iter_dsh_lines = orig
        self.assertEqual(n, 1)
        rows = show_artifacts(self.meta, "src:s1")
        self.assertEqual(len(rows[0]["old_text"]), 500)
        # 重复提取幂等（重建式：先删该 sid 旧行）
        A._iter_dsh_lines = lambda sid: iter(lines[:0])
        try:
            self.assertEqual(extract_session(self.meta, self.db, "src:s1"), 0)
        finally:
            A._iter_dsh_lines = orig
        self.assertEqual(show_artifacts(self.meta, "src:s1"), [])


if __name__ == "__main__":
    unittest.main()
