# -*- coding: utf-8 -*-
"""v0.30 零散分诊测试（用户判据的可判定化）。

用户 2026-10-10 提出的判据：只要求查询、没提分析/提炼/整理/归纳，且同一轮
对话前后内容大相径庭 → 目的多半只是取信息（而非整合信息）。

**真库反例是这份测试的来源**：只按该判据跑，小说创作素材检索（"交通锥与围栏
材质区别""描写皱眉动作的方法"）会被判成零散；所以才需要"创作素材型"这一类，
且必须排在"查询型零散"之前。每条断言都能说清什么情况会红。
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester.noisetriage import (INCOHERENT_BELOW, classify, coherence,
                                   intent_of, render_triage, triage)
from harvester.consolidate import register_noise
from harvester.indexing import SCHEMA, index_session
from harvester.models import Message, SessionRecord
from harvester.topics import add_members, ensure_topics_db, register_topic


class TestClassify(unittest.TestCase):

    def test_lookup_single_turn_is_high_confidence_noise(self):
        v = classify("“差额”的英文表达", ["“差额”的英文表达"])
        self.assertEqual(v["verdict"], "noise_high")
        self.assertTrue(v["lookup"])
        self.assertFalse(v["integrate"])
        self.assertFalse(v["craft"])

    def test_craft_material_beats_lookup(self):
        """写作素材检索看着像查词，但目的不是取信息——必须不判零散。"""
        for text in ("描写皱眉动作的方法", "表示强烈注视的词汇",
                     "形容车祸的多样短语", "优雅举止的近义词"):
            v = classify(text, [text])
            self.assertEqual(v["verdict"], "craft_material", text)

    def test_integrate_intent_is_substantive(self):
        for text in ("请详细分析这段文本并给出改进建议",
                     "把这些内容整理归纳成一份规范"):
            self.assertEqual(classify(text, [text])["verdict"], "substantive")

    def test_incoherent_multiturn_is_maybe_not_high(self):
        """多轮前后无关（问方差→天气→护照）也不进高置信：字符 bigram 连贯度
        在中文短句上区分力弱（相关追问也可能零重叠），拿它定高置信会误伤。
        它只作报告信号，判定落在"待定"。"""
        turns = ["查一下方差公式", "顺便问下明天上海天气", "再问一个：护照怎么办"]
        v = classify(turns[0], turns)
        self.assertEqual(v["verdict"], "noise_maybe")
        self.assertLess(v["coherence"], INCOHERENT_BELOW)   # 信号仍照实报出
        self.assertIn("连贯度", v["reason"])

    def test_coherent_multiturn_is_maybe_not_high(self):
        """多轮查询型一律"待定"——**不断言连贯度数值**：实测同一话题的追问
        （方差/标准差）bigram 连贯度也只有 0.067，这个信号本来就弱，
        断言它会变成"断言一个不可靠的量"。"""
        turns = ["查一下方差公式", "那标准差怎么算", "标准差和方差的区别是什么"]
        v = classify(turns[0], turns)
        self.assertEqual(v["verdict"], "noise_maybe")
        self.assertEqual(v["turns"], 3)

    def test_broad_words_do_not_leak_into_craft(self):
        """「表达/表现/形容」这类泛词曾把"英文表达"误判成创作素材——
        词表收紧后，纯词义查询必须仍判零散（测试当场抓到这个串味）。"""
        self.assertEqual(
            classify("“差额”的英文表达", ["“差额”的英文表达"])["verdict"],
            "noise_high")
        self.assertEqual(
            classify("“陡然”与“骤然”的区别", ["“陡然”与“骤然”的区别"])["verdict"],
            "noise_high")

    def test_topic_hint_wins_over_everything(self):
        v = classify("“差额”的英文表达", ["“差额”的英文表达"],
                     topic_hint="某主题")
        self.assertEqual(v["verdict"], "topic_hint")
        self.assertIn("某主题", v["reason"])

    def test_coherence_is_one_for_single_turn(self):
        self.assertEqual(coherence(["x"]), 1.0)
        self.assertLess(coherence(["完全无关的一句话", "毫不相干的另一段"]), 0.2)


class TestTriageOnDb(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "h.db"
        con = sqlite3.connect(str(self.db))
        con.executescript(SCHEMA)
        for sid, title, text in (
                ("s1", "差额英文", "“差额”的英文表达"),
                ("s2", "皱眉描写", "描写皱眉动作的方法"),      # 创作素材
                ("s3", "文本分析", "请分析这段文本并给建议"),   # 整合
                ("s4", "已入主题", "“悄摸摸”的含义解析")):      # 已是成员
            index_session(con, SessionRecord(
                source="yuanbao-raw", session_id=sid, title=title,
                created_at="2026-01-01 10:00:00",
                updated_at="2026-01-01 10:00:00",
                messages=[Message(role="user", text=text),
                          Message(role="assistant", text="答")]))
        con.commit()
        con.close()
        self.meta = ensure_topics_db(root / "topics_meta.db")
        self.t = register_topic(self.meta, "某主题", keywords=["悄摸摸"])
        add_members(self.meta, self.t, ["yuanbao-raw:s4"], evidence="e")

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def test_triage_classifies_and_skips_members_and_noise(self):
        register_noise(self.meta, [{"sid": "yuanbao-raw:s9", "reason": "x"}])
        t = triage(self.db, self.meta, max_turns=3)
        by = {r["sid"]: r for r in t["rows"]}
        self.assertEqual(by["yuanbao-raw:s1"]["verdict"], "noise_high")
        self.assertEqual(by["yuanbao-raw:s2"]["verdict"], "craft_material")
        self.assertEqual(by["yuanbao-raw:s3"]["verdict"], "substantive")
        self.assertNotIn("yuanbao-raw:s4", by)   # 已是主题成员 → 不参与分诊
        self.assertEqual(t["counts"]["noise_high"], 1)
        self.assertEqual(t["scanned"], 3)

    def test_triage_max_turns_excludes_deep_sessions(self):
        con = sqlite3.connect(str(self.db))
        index_session(con, SessionRecord(
            source="yuanbao-raw", session_id="s5", title="深会话",
            created_at="2026-01-02 10:00:00", updated_at="2026-01-02 10:00:00",
            messages=[Message(role="user", text=f"问题{i}") for i in range(6)]))
        con.commit()
        con.close()
        t = triage(self.db, self.meta, max_turns=3)
        self.assertNotIn("yuanbao-raw:s5", {r["sid"] for r in t["rows"]})
        t2 = triage(self.db, self.meta, max_turns=8)
        self.assertIn("yuanbao-raw:s5", {r["sid"] for r in t2["rows"]})

    def test_render_mentions_verdicts_and_is_not_an_action(self):
        t = triage(self.db, self.meta)
        txt = render_triage(t)
        self.assertIn("高置信零散", txt)
        self.assertIn("创作素材型", txt)
        self.assertIn("登记与否由 plan 决定", txt)   # 报告只给判定，不自动登记


if __name__ == "__main__":
    unittest.main()
