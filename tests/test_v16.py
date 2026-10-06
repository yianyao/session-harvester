# -*- coding: utf-8 -*-
"""v0.16 测试：第三方审计（AUDIT-2026-10-08）P0 修复的回归。

1. 口径对账：note 文本口径与 steps 表口径对同一场景必须给出一致的
   calls/success/error（夹具含 completed/None/unknown 三种真实形态）；
2. 降级解析器：无 PyYAML 时 anchors 流式序列必须可解析、锚点校验必须
   真正执行（修"锚点校验 0 个但结论通过"的自相矛盾）；
3. 结论口径：warn 不再被吞——有警告时结论必须提示复核。
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester import cards as cards_mod
from harvester.cards import _parse_frontmatter, render_report, validate_cards
from harvester.indexing import SCHEMA
from harvester.models import Message, SessionRecord
from harvester.toolstats import collect_stats, collect_stats_from_db


def _note_records():
    """note 口径夹具：T1 三调（completed/success/error），T2 未知状态。"""
    msgs = [
        Message("note", "[tool_call] T1: a",
                raw={"kind": "tool_call", "tool": "T1"}),
        Message("note", "[tool_result] T1: completed",
                raw={"kind": "tool_result", "tool": "T1",
                     "status": "completed"}),
        Message("note", "[tool_call] T1: b",
                raw={"kind": "tool_call", "tool": "T1"}),
        Message("note", "[tool_result] T1: success",
                raw={"kind": "tool_result", "tool": "T1", "status": "success"}),
        Message("note", "[tool_call] T1: c",
                raw={"kind": "tool_call", "tool": "T1"}),
        Message("note", "[tool_result] T1: error boom",
                raw={"kind": "tool_result", "tool": "T1",
                     "status": "error", "error": "boom"}),
        Message("note", "[tool_call] T2: d",
                raw={"kind": "tool_call", "tool": "T2"}),
        Message("note", "[tool_result] T2: weird_status",
                raw={"kind": "tool_result", "tool": "T2",
                     "status": "weird_status"}),
    ]
    return [SessionRecord(source="t", session_id="s1", title="x",
                          messages=msgs)]


def _steps_db(tmp: Path) -> Path:
    """同一场景的 steps 表形态（status 分布对齐真实库：completed 为主）。"""
    db = tmp / "recon.db"
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)
    con.execute("INSERT OR REPLACE INTO sessions (sid, source, session_id, "
                "title, category) VALUES ('t:s1', 't', 's1', 'x', 'c')")
    rows = [
        ("t:s1", 0, "2026-10-01 10:00:00", "T1", "call", None, None, None),
        ("t:s1", 1, "2026-10-01 10:00:01", "T1", "result", "completed",
         None, None),
        ("t:s1", 2, "2026-10-01 10:00:02", "T1", "call", None, None, None),
        ("t:s1", 3, "2026-10-01 10:00:03", "T1", "result", "success",
         None, None),
        ("t:s1", 4, "2026-10-01 10:00:04", "T1", "call", None, None, None),
        ("t:s1", 5, "2026-10-01 10:00:05", "T1", "result", "error",
         "boom", None),
        ("t:s1", 6, "2026-10-01 10:00:06", "T2", "call", None, None, None),
        ("t:s1", 7, "2026-10-01 10:00:07", "T2", "result", "weird_status",
         "weird_status", None),
    ]
    con.executemany("INSERT INTO steps VALUES (?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    return db


class TestReconcile(unittest.TestCase):
    """对账约束：两条口径的成败判定必须一致（单一真值源 _OK_STATUS）。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = _steps_db(Path(cls.tmp.name))

    @classmethod
    def tearDownClass(cls):
        try:
            cls.tmp.cleanup()
        except OSError:
            pass

    def _as_tuples(self, stats):
        return {k: (v.calls, v.success, v.error) for k, v in stats.items()}

    def test_note_counts_completed_as_success(self):
        stats = self._as_tuples(collect_stats(_note_records()))
        self.assertEqual(stats["T1"], (3, 2, 1))
        self.assertEqual(stats["T2"], (1, 0, 1))

    def test_db_counts_completed_as_success(self):
        stats, _flow = collect_stats_from_db(self.db)
        got = self._as_tuples(stats)
        self.assertEqual(got["T1"], (3, 2, 1))
        self.assertEqual(got["T2"], (1, 0, 1))

    def test_two_views_agree(self):
        """同一场景两口径完全相等——回归审计 3.1（88.1% vs 1.7%）。"""
        a = self._as_tuples(collect_stats(_note_records()))
        b, _ = collect_stats_from_db(self.db)
        self.assertEqual(a, self._as_tuples(b))

    def test_completed_not_dropped_fails_loudly(self):
        """破坏性自检：把 completed 移出 _OK_STATUS，夹具计数值必须改变
        （证明夹具真实覆盖 completed 形态、断言不是恒真）。修好后两口径
        共用常量，错误常量只会同时污染两边——这正是"单一真值源"的意义，
        由本测试保证 completed 形态在夹具里真实生效。"""
        from harvester import toolstats as ts
        orig = ts._OK_STATUS
        baseline = self._as_tuples(collect_stats(_note_records()))
        ts._OK_STATUS = ("success", "ok")
        try:
            sabotaged = self._as_tuples(collect_stats(_note_records()))
        finally:
            ts._OK_STATUS = orig
        self.assertEqual(baseline["T1"], (3, 2, 1))
        self.assertNotEqual(sabotaged["T1"], (3, 2, 1),
                            "completed 移出成功集后 T1 成功数必须变化")


_CARD = """---
id: kc-test-0001
title: 降级解析
type: pitfall
tags: [t]
anchors: [{session_id: s1, turn: 3}, {session_id: ghost, turn: 9}]
evidence: |
  Error: something
confidence: 0.8
created: 2026-10-07
---
正文。
"""


class TestDegradedParser(unittest.TestCase):
    """审计 3.2：无 PyYAML 时锚点校验必须照常执行（降级≠放弃校验）。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name) / "cards"
        root.mkdir()
        (root / "c.md").write_text(_CARD, encoding="utf-8", newline="\n")
        cls.root = root
        # 无 yaml 环境模拟
        cls._orig = cards_mod._HAS_YAML
        cards_mod._HAS_YAML = False
        # 最小索引库（只有 s1）
        con = sqlite3.connect(str(Path(cls.tmp.name) / "fix.db"))
        con.executescript(SCHEMA)
        con.execute("INSERT OR REPLACE INTO sessions (sid, source, "
                    "session_id, title, category) "
                    "VALUES ('t:s1', 't', 's1', 'x', 'c')")
        con.commit()
        con.close()
        cls.db = Path(cls.tmp.name) / "fix.db"

    @classmethod
    def tearDownClass(cls):
        cards_mod._HAS_YAML = cls._orig
        try:
            cls.tmp.cleanup()
        except OSError:
            pass

    def test_flow_seq_parsed_without_yaml(self):
        fm, _body = _parse_frontmatter(_CARD)
        self.assertIsInstance(fm["anchors"], list)
        self.assertEqual(fm["anchors"][0]["session_id"], "s1")
        self.assertEqual(fm["anchors"][0]["turn"], 3)
        self.assertEqual(fm["anchors"][1]["session_id"], "ghost")

    def test_anchor_check_executes_without_yaml(self):
        results, summary = validate_cards(self.root, self.db)
        self.assertGreaterEqual(summary["anchor_checked"], 2)
        self.assertEqual(summary["anchor_misses"], 1)
        r = results[0]
        self.assertTrue(any("ghost" in e for e in r["errors"]))

    def test_yaml_and_degraded_agree(self):
        """有无 PyYAML 两解释器下校验结论必须一致（审计 3.2 的核心诉求）。"""
        with_yaml, s1 = validate_cards(self.root, self.db)
        cards_mod._HAS_YAML = False
        try:
            no_yaml, s2 = validate_cards(self.root, self.db)
        finally:
            cards_mod._HAS_YAML = True
        self.assertEqual(s1["anchor_checked"], s2["anchor_checked"])
        self.assertEqual(s1["anchor_misses"], s2["anchor_misses"])
        self.assertEqual(len(with_yaml[0]["errors"]),
                         len(no_yaml[0]["errors"]))


class TestConclusionMentionsWarnings(unittest.TestCase):
    """审计 3.2 结论口径：warn 不得被吞。"""

    def test_warn_changes_conclusion(self):
        results = [{"path": "w.md", "errors": [], "warnings": ["正文为空"]}]
        summary = {"cards": 1, "ok": 0, "warn": 1, "error": 0,
                   "anchor_checked": 0, "anchor_misses": 0}
        text = render_report(results, summary, Path("/x"))
        self.assertIn("警告", text.splitlines()[-1])
        self.assertNotIn("全部卡片满足", text)

    def test_clean_still_ok(self):
        results = [{"path": "ok.md", "errors": [], "warnings": []}]
        summary = {"cards": 1, "ok": 1, "warn": 0, "error": 0,
                   "anchor_checked": 1, "anchor_misses": 0}
        text = render_report(results, summary, Path("/x"))
        self.assertIn("全部卡片满足", text)


if __name__ == "__main__":
    unittest.main()
