# -*- coding: utf-8 -*-
"""v0.39 测试：深会话单列两类（`--triage-deep`）。

**要解决的问题**（v0.38 实测）：`triage(max_turns=3)` 的缺省值是为了不让多轮打磨
被"前后不连贯"之类的弱信号误判成零散——但副作用是**深会话永远进不了归位视野**：
真库把口径放宽到 ≤12 回合，池子从 505 涨到 821（+316，其中 **53 条已机械命中现有
主题**却从未归位）。改默认值会让噪声判定被多轮会话污染，所以补的是"单列 + 只提示"。

本文件的**核心断言是负向的**：一条"如果它是单轮就该被判 noise_high"的会话，只要它
是深会话，就**绝不许**出现在任何零散类里——它只能进 `deep_*`。这条一旦破，深会话
会被错误登记为零散（不可逆：登记后不再进池）。
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester.indexing import SCHEMA, index_session
from harvester.models import Message, SessionRecord
from harvester.noisetriage import render_brief, render_triage, triage
from harvester.planseed import build_seed
from harvester.topics import add_members, ensure_topics_db, register_topic

#: 单轮形态就很像纯查询（若它只有 1 回合，必判 noise_high）
LOOKUP = "“差额”的英文表达"
#: 深会话里夹一条纯查询——用来证明"深了就不许算零散"
DEEP_TEXTS = [LOOKUP] + [f"第{i}个后续问题，继续聊这个话题" for i in range(2, 12)]


def _fixture(root: Path):
    db = root / "h.db"
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)

    def add(sid: str, texts: list[str], title: str) -> None:
        index_session(con, SessionRecord(
            source="yuanbao-raw", session_id=sid, title=title,
            created_at="2026-01-01 10:00:00", updated_at="2026-01-01 10:00:00",
            messages=[Message(role="user", text=t) for t in texts]))

    add("shallow", [LOOKUP], "差额英文")                  # → noise_high
    add("deep", DEEP_TEXTS, "差额英文深聊")               # 深：12 回合
    add("deep_hit", ["丁樾" + "梦里啥都有"] + [f"第{i}问" for i in range(2, 9)],
        "丁樾的梦")                                       # 深 + 命中主题关键词
    con.commit()
    con.close()
    meta = ensure_topics_db(root / "topics_meta.db")
    tid = register_topic(meta, "小说主题", keywords=["丁樾"])
    return db, meta, tid


class TestDeepClass(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db, self.meta, self.tid = _fixture(self.root)

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def test_default_still_excludes_deep(self):
        """缺省口径不变：深会话不进池（旧行为不能被这次改动动到）。"""
        t = triage(self.db, self.meta, max_turns=3)
        sids = {r["sid"] for r in t["rows"]}
        self.assertIn("yuanbao-raw:shallow", sids)
        self.assertNotIn("yuanbao-raw:deep", sids)
        self.assertNotIn("yuanbao-raw:deep_hit", sids)
        self.assertEqual(t["counts"]["noise_high"], 1)
        self.assertFalse(t["include_deep"])

    def test_deep_never_lands_in_noise_classes(self):
        """核心负向断言：深会话一律只进 deep_*，绝不进 noise_high/noise_maybe。"""
        t = triage(self.db, self.meta, max_turns=3, include_deep=True)
        by = {r["sid"]: r for r in t["rows"]}
        self.assertIn("yuanbao-raw:deep", by)
        self.assertIn("yuanbao-raw:deep_hit", by)
        for sid in ("yuanbao-raw:deep", "yuanbao-raw:deep_hit"):
            self.assertTrue(by[sid]["verdict"].startswith("deep_"), by[sid])
        # 单轮那条仍是零散（口径没被放宽）
        self.assertEqual(by["yuanbao-raw:shallow"]["verdict"], "noise_high")
        self.assertEqual(t["counts"]["noise_high"], 1)
        self.assertEqual(t["counts"].get("noise_maybe", 0)
                         + t["counts"].get("craft_material", 0), 0)
        self.assertTrue(t["include_deep"])

    def test_deep_split_by_topic_hit(self):
        t = triage(self.db, self.meta, max_turns=3, include_deep=True)
        by = {r["sid"]: r for r in t["rows"]}
        self.assertEqual(by["yuanbao-raw:deep_hit"]["verdict"], "deep_topic_hint")
        self.assertIn("机械命中主题关键词", by["yuanbao-raw:deep_hit"]["reason"])
        self.assertEqual(by["yuanbao-raw:deep"]["verdict"], "deep_unassigned")
        self.assertEqual(t["counts"]["deep_topic_hint"], 1)
        self.assertEqual(t["counts"]["deep_unassigned"], 1)

    def test_report_and_brief_show_deep_classes(self):
        t = triage(self.db, self.meta, max_turns=3, include_deep=True)
        md = render_triage(t)
        self.assertIn("深会话·疑似归主题", md)
        self.assertIn("深会话·未归主题", md)
        self.assertIn("含深会话", md)
        brief = render_brief(t, "deep_topic_hint")
        self.assertIn("yuanbao-raw:deep_hit", brief)
        self.assertNotIn("yuanbao-raw:deep\t", brief)

    def test_planseed_maps_deep_topic_hint_like_topic_hint(self):
        """深会话的命中也要能直接进 plan（否则捞出来还是得手抄 sid）。"""
        t = triage(self.db, self.meta, max_turns=3, include_deep=True)
        plan, stats = build_seed(t, [{"id": self.tid, "name": "小说主题",
                                      "keywords": ["丁樾"]}])
        targets = {a.get("target"): a["sids"] for a in plan["assign"]}
        self.assertEqual(targets[self.tid], ["yuanbao-raw:deep_hit"])
        self.assertEqual(stats["deep_topic_hint"]["assign"], 1)
        # 无命中的深会话缺省不动，但必须在统计里看得见
        self.assertEqual(stats["deep_unassigned"]["unhandled"], 1)

    def test_deep_session_of_own_topic_member_is_skipped(self):
        """已是成员/已登记零散的深会话不进池（与浅会话同一套过滤）。"""
        add_members(self.meta, self.tid, ["yuanbao-raw:deep_hit"], evidence="e")
        t = triage(self.db, self.meta, max_turns=3, include_deep=True)
        self.assertNotIn("yuanbao-raw:deep_hit", {r["sid"] for r in t["rows"]})


if __name__ == "__main__":
    unittest.main()
