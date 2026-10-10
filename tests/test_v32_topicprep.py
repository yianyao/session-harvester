# -*- coding: utf-8 -*-
"""v0.32 topic dedupe / topic turns 测试（把每轮都要跑的脚本提升为工具）。

判据都能说清"什么情况会红"：
- 去重：两份**逐字相同**的 user 正文 → 必须并成 1 簇、代表取最早、duplicates=1；
  0.80–0.90 的**部分相似**必须只进边界带、**不并入**（H40 裁决）；
- 回合索引：turn 号必须是 1-based 的 user 消息序号（H24 口径），raw 全文模式
  必须给原文而不是预览——引文要逐字取自 raw（text 列是 bigram，H52）。
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester.indexing import SCHEMA, index_session
from harvester.models import Message, SessionRecord
from harvester.topicprep import (dedupe_topic, render_dedupe, render_turns,
                                 turns_of)
from harvester.topics import add_members, ensure_topics_db, register_topic

SAME = "对提交文本的叙事风格、遣词造句、冲突悬念设置进行详细分析"


class TestDedupeTopic(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "h.db"
        con = sqlite3.connect(str(self.db))
        con.executescript(SCHEMA)
        for sid, created, text in (
                ("s1", "2026-01-01", SAME),                    # 代表（最早）
                ("s2", "2026-01-02", SAME),                    # 逐字重复
                # 夹具按口径算过：SAME 27 个 bigram，追加 4 字 → 27/31 ≈ 0.87，
                # 落在 0.80–0.90 边界带（首版追加 9 字得 0.75，掉到带外——
                # 断言红的是夹具不是代码，AGENTS.md 第 6 条）
                ("s3", "2026-01-03", SAME + "，并给出"),
                ("s4", "2026-01-04", "完全不同的另一件事" * 6)):
            index_session(con, SessionRecord(
                source="src", session_id=sid, title=f"标题{sid}",
                created_at=f"{created} 10:00:00",
                updated_at=f"{created} 10:00:00",
                messages=[Message(role="user", text=text),
                          Message(role="assistant", text="答")]))
        con.commit()
        con.close()
        self.meta = ensure_topics_db(root / "topics_meta.db")
        self.t = register_topic(self.meta, "主题甲")
        add_members(self.meta, self.t, ["src:s1", "src:s2", "src:s3", "src:s4"],
                    evidence="e")

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def test_identical_sessions_are_deduped_to_earliest_rep(self):
        r = dedupe_topic(self.meta, self.db, self.t)
        self.assertEqual(r["members"], 4)
        self.assertEqual(r["duplicates"], 1)
        self.assertEqual(r["reps"], 3)
        self.assertEqual(len(r["clusters"]), 1)
        self.assertEqual(r["clusters"][0][0], "src:s1")     # 代表取最早
        self.assertIn("src:s2", r["clusters"][0])
        self.assertIn("src:s1", r["rep_list"])

    def test_boundary_band_is_reported_but_not_merged(self):
        """s3 与 s1 高度相似但未达 0.90 → 只进边界带，不当重复并入。"""
        r = dedupe_topic(self.meta, self.db, self.t)
        self.assertEqual(r["duplicates"], 1)                # 只有 s2
        pair_sids = {p[0] for p in r["band_pairs"]} | {p[1]
                                                       for p in r["band_pairs"]}
        self.assertIn("src:s3", pair_sids)
        self.assertNotIn("src:s3", [s for cl in r["clusters"] for s in cl])

    def test_unrelated_session_stays_separate(self):
        r = dedupe_topic(self.meta, self.db, self.t)
        self.assertIn("src:s4", r["rep_list"])
        self.assertNotIn("src:s4", [s for cl in r["clusters"] for s in cl])

    def test_report_states_threshold_and_carries_rep_list(self):
        txt = render_dedupe(dedupe_topic(self.meta, self.db, self.t))
        self.assertIn("0.9", txt)
        self.assertIn("独立代表 3", txt)
        self.assertIn("代表 `src:s1`", txt)
        self.assertIn("不可挂锚点", txt)

    def test_missing_topic_fails_loud(self):
        with self.assertRaises(KeyError):
            dedupe_topic(self.meta, self.db, "tp-ghost")


class TestTurns(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "h.db"
        con = sqlite3.connect(str(self.db))
        con.executescript(SCHEMA)
        index_session(con, SessionRecord(
            source="src", session_id="s1", title="甲会话",
            created_at="2026-01-01 10:00:00", updated_at="2026-01-01 10:00:00",
            messages=[Message(role="user", text="第一问"),
                      Message(role="assistant", text="答一"),
                      Message(role="user", text="第二问"),
                      Message(role="assistant", text="答二"),
                      Message(role="user", text="第三问")]))
        con.commit()
        con.close()
        self.meta = ensure_topics_db(root / "topics_meta.db")
        self.t = register_topic(self.meta, "主题甲")
        add_members(self.meta, self.t, ["src:s1"], evidence="e")
        add_members(self.meta, self.t, ["src:ghost"], evidence="不在库")

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def test_turn_numbers_are_one_based_user_ordinals(self):
        t = turns_of(self.meta, self.db, self.t)
        row = [r for r in t["rows"] if r["sid"] == "src:s1"][0]
        self.assertEqual([x["turn"] for x in row["turns"]], [1, 2, 3])
        self.assertIn("第一问", row["turns"][0]["preview"])

    def test_full_mode_gives_raw_not_preview(self):
        long_text = "前缀" + "很长" * 200
        con = sqlite3.connect(str(self.db))
        index_session(con, SessionRecord(
            source="src", session_id="s9", title="长会话",
            created_at="2026-01-02 10:00:00", updated_at="2026-01-02 10:00:00",
            messages=[Message(role="user", text=long_text)]))
        con.commit()
        con.close()
        add_members(self.meta, self.t, ["src:s9"], evidence="e")
        short = turns_of(self.meta, self.db, self.t, cap=10)
        full = turns_of(self.meta, self.db, self.t, full=True)
        row_s = [r for r in short["rows"] if r["sid"] == "src:s9"][0]
        row_f = [r for r in full["rows"] if r["sid"] == "src:s9"][0]
        self.assertLessEqual(len(row_s["turns"][0]["preview"]), 10)
        self.assertEqual(row_f["turns"][0]["raw"], long_text)   # 逐字原文
        self.assertIsNone(row_f["turns"][0]["preview"])

    def test_member_not_in_index_is_listed_not_dropped(self):
        t = turns_of(self.meta, self.db, self.t)
        ghost = [r for r in t["rows"] if r["sid"] == "src:ghost"]
        self.assertTrue(ghost)
        self.assertTrue(ghost[0]["missing"])

    def test_render_marks_raw_source_and_anchor_rule(self):
        txt = render_turns(turns_of(self.meta, self.db, self.t))
        self.assertIn("messages.raw", txt)
        self.assertIn("1-based", txt)
        self.assertIn("chain-audit", txt)


if __name__ == "__main__":
    unittest.main()
