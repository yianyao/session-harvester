# -*- coding: utf-8 -*-
"""v0.19 产物有效性改造测试：A1 工具名归一 / A2 未解决维度 / A3 库快照
指纹 / A4 owner+status+引文核对+min_calls。

红线延续 test_v17/v18：全部查询走调用方连接（api_meta/reports/triage/
cards 均传入 con）；suggestmeta 是独立 meta 库，允许自开连接（不属于
采集库只读范围）。
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester import apiserve
from harvester.cards import validate_cards
from harvester.dbmeta import db_fingerprint, fingerprint_line
from harvester.indexing import SCHEMA, index_session
from harvester.models import Message, SessionRecord
from harvester.suggestmeta import load_statuses, set_status
from harvester.toolstats import (ToolStats, collect_stats_from_db,
                                 merge_case_alias)

_ERR = "String to replace not found: X"
_FETCH_ERR = "Error: web fetch failed: TypeError: fetch failed"


def _fixture_db(tmp: Path) -> Path:
    """会话 aaa：Edit 错误后无重试（放弃）+ webfetch 错误后重试（自愈）；
    会话 bbb：edit（小写）错误后无重试——供归一合并与未解决维度对账。"""
    db = tmp / "v19_test.db"
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)
    recs = [
        SessionRecord(source="src", session_id="aaa", title="A",
                      created_at="2026-10-07 10:00:00",
                      updated_at="2026-10-07 10:00:00",
                      messages=[Message(role="user", text="改文件"),
                                Message(role="assistant", text="好"),
                                Message(role="assistant",
                                        text="证据原文 Alpha beta gamma delta")]),
        SessionRecord(source="src", session_id="bbb", title="B",
                      created_at="2026-10-06 10:00:00",
                      updated_at="2026-10-06 10:00:00",
                      messages=[Message(role="user", text="再改")]),
    ]
    for rec in recs:
        index_session(con, rec)
    steps = [
        # aaa：Edit call → error，之后无 Edit call（放弃）
        ("src:aaa", 0, "2026-10-07 10:00:01", "Edit", "call", "", "", ""),
        ("src:aaa", 1, "2026-10-07 10:00:02", "Edit", "result", "error",
         _ERR, ""),
        # aaa：webfetch call → error → 再次 call（重试/自愈）
        ("src:aaa", 2, "2026-10-07 10:00:03", "webfetch", "call", "", "", ""),
        ("src:aaa", 3, "2026-10-07 10:00:04", "webfetch", "result", "error",
         _FETCH_ERR, ""),
        ("src:aaa", 4, "2026-10-07 10:00:05", "webfetch", "call", "", "", ""),
        ("src:aaa", 5, "2026-10-07 10:00:06", "webfetch", "result", "ok",
         "", ""),
        # bbb：小写 edit error（与 Edit 归一同组），无重试
        ("src:bbb", 0, "2026-10-06 10:00:01", "edit", "call", "", "", ""),
        ("src:bbb", 1, "2026-10-06 10:00:02", "edit", "result", "error",
         _ERR, ""),
        # bbb：Skill 调用对（G4 min_calls 测试素材）
        ("src:bbb", 2, "2026-10-06 10:00:03", "Skill", "call", "", "", ""),
        ("src:bbb", 3, "2026-10-06 10:00:04", "Skill", "result", "ok",
         "", ""),
    ]
    for row in steps:
        con.execute("INSERT INTO steps (sid, seq, ts, tool, phase, status, "
                    "error, detail) VALUES (?,?,?,?,?,?,?,?)", row)
    con.commit()
    con.close()
    return db


class TestMergeAlias(unittest.TestCase):
    """A1：merge_case_alias 纯函数层。"""

    def test_case_merge_most_calls_wins(self):
        stats = {"edit": ToolStats(), "Edit": ToolStats()}
        stats["edit"].calls = 3
        stats["Edit"].calls = 5
        merged, raw_map = merge_case_alias(stats)
        self.assertEqual(set(merged), {"Edit"})
        self.assertEqual(merged["Edit"].calls, 8)
        self.assertEqual(sorted(merged["Edit"].raw_names), ["Edit", "edit"])
        self.assertEqual(raw_map["Edit"], ["Edit", "edit"])

    def test_underscore_fold(self):
        # web_fetch/WebFetch 差异不止大小写（下划线），必须同组
        stats = {"web_fetch": ToolStats(), "WebFetch": ToolStats()}
        stats["web_fetch"].calls = 59
        stats["web_fetch"].error = 17
        stats["WebFetch"].calls = 98
        stats["WebFetch"].error = 10
        merged, _ = merge_case_alias(stats)
        self.assertEqual(set(merged), {"WebFetch"})
        self.assertEqual(merged["WebFetch"].calls, 157)
        self.assertEqual(merged["WebFetch"].error, 27)
        self.assertAlmostEqual(merged["WebFetch"].fail_rate, 27 / 157)

    def test_counts_not_lost(self):
        stats = {"a": ToolStats(), "A": ToolStats()}
        stats["a"].calls = 1
        stats["a"].success = 1
        stats["a"].errors["boom"] = 1
        stats["A"].calls = 2
        stats["A"].error = 1
        stats["A"].errors["boom"] = 2
        stats["A"].retried = 1
        stats["A"].given_up = 0
        merged, _ = merge_case_alias(stats)
        st = merged["A"]
        self.assertEqual((st.calls, st.success, st.error), (3, 1, 1))
        self.assertEqual(st.errors["boom"], 3)
        self.assertEqual(st.retried, 1)

    def test_single_name_noop(self):
        stats = {"Bash": ToolStats()}
        stats["Bash"].calls = 4
        merged, raw_map = merge_case_alias(stats)
        self.assertEqual(merged["Bash"].calls, 4)
        self.assertEqual(raw_map, {"Bash": ["Bash"]})


class TestGivenUpPerTool(unittest.TestCase):
    """A2：per-tool 重试/放弃落到 ToolStats（随归一合并相加）。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = _fixture_db(Path(cls.tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_given_up_and_retried(self):
        stats, flow = collect_stats_from_db(self.db)
        # Edit（合并 edit）：两次错误均无重试 → given_up=2、retried=0
        self.assertEqual(stats["Edit"].given_up, 2)
        self.assertEqual(stats["Edit"].retried, 0)
        # webfetch：1 次错误后重试 → retried=1、given_up=0
        self.assertEqual(stats["webfetch"].retried, 1)
        self.assertEqual(stats["webfetch"].given_up, 0)
        self.assertEqual(flow, {"retried": 1, "given_up": 2, "errors": 3})


class TestUnresolvedAgents(unittest.TestCase):
    """A2：建议池 unresolved_count / 排序 / status。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = _fixture_db(Path(cls.tmp.name))
        cls.con = apiserve.open_ro(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        cls.tmp.cleanup()

    def test_entries_unresolved_fields_and_order(self):
        d = apiserve.api_reports_agents(self.con, self.db, {"min_count": "1"})
        self.assertEqual(d["api_version"], 1)
        entries = d["entries"]
        titles = {e["title"] for e in entries}
        self.assertIn("Edit/Write 前必须先 Read 目标文件最新内容。", titles)
        edit_entry = next(e for e in entries if e["title"].startswith(
            "Edit/Write"))
        self.assertEqual(edit_entry["total"], 2)
        self.assertEqual(edit_entry["unresolved_count"], 2)
        self.assertEqual(edit_entry["owner"], "workflow")
        self.assertEqual(edit_entry["status"], "pending")
        # 排序：unresolved_count 降序 → Edit 条目（2）在 webfetch（0）前
        fetch_entry = next(e for e in entries if "网络类失败" in e["title"])
        self.assertEqual(fetch_entry["unresolved_count"], 0)
        self.assertLess(entries.index(edit_entry), entries.index(fetch_entry))
        self.assertEqual(fetch_entry["owner"], "harness")

    def test_status_from_meta_db(self):
        meta_db = Path(self.tmp.name) / "meta.db"
        set_status(meta_db, "Edit/Write 前必须先 Read 目标文件最新内容。",
                   "adopted")
        d = apiserve.api_reports_agents(self.con, self.db, {"min_count": "1"},
                                        meta_db=meta_db)
        edit_entry = next(e for e in d["entries"] if e["title"].startswith(
            "Edit/Write"))
        self.assertEqual(edit_entry["status"], "adopted")
        other = next(e for e in d["entries"]
                     if not e["title"].startswith("Edit/Write"))
        self.assertEqual(other["status"], "pending")


class TestFingerprint(unittest.TestCase):
    """A3：库快照指纹（CLI 头 + API meta）。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = _fixture_db(Path(cls.tmp.name))
        cls.con = apiserve.open_ro(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        cls.tmp.cleanup()

    def test_fields(self):
        fp = db_fingerprint(self.db, con=self.con)
        self.assertEqual(set(fp), {"sessions", "steps", "errors",
                                   "db_mtime", "generated_at"})
        self.assertEqual(fp["sessions"], 2)
        self.assertEqual(fp["steps"], 10)
        self.assertEqual(fp["errors"], 3)
        self.assertTrue(fp["db_mtime"])
        self.assertTrue(fp["generated_at"])

    def test_endpoints_carry_fingerprint(self):
        self.assertIn("db_fingerprint", apiserve.api_meta(self.con, self.db))
        self.assertIn("db_fingerprint",
                      apiserve.api_reports_tools(self.con, self.db, {}))
        self.assertIn("db_fingerprint",
                      apiserve.api_reports_errors(self.con, self.db, {}))
        self.assertIn("db_fingerprint",
                      apiserve.api_reports_skills(self.con, self.db, {}))
        self.assertIn("db_fingerprint",
                      apiserve.api_reports_agents(self.con, self.db, {}))
        self.assertIn("db_fingerprint",
                      apiserve.api_triage(self.con, self.db, None, {}))

    def test_fingerprint_line(self):
        line = fingerprint_line(db_fingerprint(self.db, con=self.con))
        self.assertIn("2 会话", line)
        self.assertIn("3 错误", line)


class TestSuggestMeta(unittest.TestCase):
    """A4：suggestion_status 独立 meta 库。"""

    def test_roundtrip_and_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            meta_db = Path(tmp) / "m" / "meta.db"  # 父目录不存在，须自建
            self.assertEqual(load_statuses(meta_db), {})
            set_status(meta_db, "t1", "adopted")
            set_status(meta_db, "t2", "rejected")
            set_status(meta_db, "t1", "pending")  # upsert 覆盖
            st = load_statuses(meta_db)
            self.assertEqual(st, {"t1": "pending", "t2": "rejected"})
            with self.assertRaises(ValueError):
                set_status(meta_db, "t3", "bogus")
            with self.assertRaises(ValueError):
                set_status(meta_db, "", "adopted")


class TestMinCalls(unittest.TestCase):
    """A4：--min-calls → low_sample 标记（G4/G1 API）。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = _fixture_db(Path(cls.tmp.name))
        cls.con = apiserve.open_ro(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        cls.tmp.cleanup()

    def test_skills_low_sample(self):
        d0 = apiserve.api_reports_skills(self.con, self.db, {})
        self.assertFalse(d0["skills"][0]["low_sample"])  # 默认不标
        d = apiserve.api_reports_skills(self.con, self.db, {"min_calls": "5"})
        self.assertEqual(d["min_calls"], 5)
        self.assertTrue(all(s["low_sample"] for s in d["skills"]))

    def test_tools_low_sample(self):
        d = apiserve.api_reports_tools(self.con, self.db, {"min_calls": "3"})
        rows = {t["tool"]: t for t in d["tools"]}
        self.assertTrue(rows["webfetch"]["low_sample"])  # calls=2 < 3
        # Edit 合并（edit+Edit）后 calls=2，同样 < 3 → low_sample
        self.assertTrue(rows["Edit"]["low_sample"])
        self.assertEqual(rows["Edit"]["raw_tools"], ["Edit", "edit"])


class TestEvidenceCheck(unittest.TestCase):
    """A4：cards 引文核对（evidence 必须出自锚点会话原文）+ turn: null 警告。"""

    def _make_card(self, root: Path, name: str, evidence: str,
                   turn) -> None:
        turn_part = str(turn) if turn is not None else "null"
        (root / name).write_text(
            "---\n"
            f"id: kc-x-{name}\n"
            "title: t\n"
            "type: pitfall\n"
            "tags: []\n"
            f'anchors: [{{session_id: "aaa", turn: {turn_part}}}]\n'
            "evidence: |\n"
            + "".join(f"  {ln}\n" for ln in evidence.splitlines())
            + "confidence: 0.8\n"
            "created: 2026-10-08\n"
            "---\n正文\n",
            encoding="utf-8", newline="\n")

    def test_evidence_match_and_miss(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = _fixture_db(root)
            con = apiserve.open_ro(db)
            try:
                cards = root / "cards"
                cards.mkdir()
                # 命中：aaa 会话 assistant 消息原文（空白归一后子串）
                self._make_card(cards, "ok.md",
                                "证据原文 Alpha beta gamma delta", turn=1)
                # 未命中：原文里没有
                self._make_card(cards, "miss.md", "完全不存在的一行原文",
                                turn=None)
                results, summary = validate_cards(cards, db, con=con)
                by_path = {r["path"]: r for r in results}
                self.assertEqual(by_path["ok.md"]["errors"], [])
                self.assertEqual(by_path["ok.md"]["warnings"], [])
                self.assertEqual(summary["evidence_checked"], 2)
                self.assertEqual(summary["evidence_misses"], 1)
                miss_warns = by_path["miss.md"]["warnings"]
                self.assertTrue(any("第 1 行未在锚点会话原文中找到" in w
                                    for w in miss_warns))
                self.assertTrue(any("turn 为 null" in w for w in miss_warns))
            finally:
                con.close()


if __name__ == "__main__":
    unittest.main()
