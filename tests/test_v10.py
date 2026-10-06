# -*- coding: utf-8 -*-
"""v0.10 测试：report-errors 三分类 / suggest-agents / cards validate / --since。"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester.agent_suggest import build_suggestions
from harvester.cards import validate_card, validate_cards
from harvester.errstats import (classify_error, collect_errors_from_db,
                                normalize_error, render_report)

try:
    import yaml  # noqa: F401  cards frontmatter 完整解析依赖（可选降级）
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

# ---- fixture 索引库（sessions + steps 最小schema） ----

def _make_db(tmp: Path) -> Path:
    db = tmp / "fix.db"
    con = sqlite3.connect(str(db))
    con.executescript("""
    CREATE TABLE sessions (sid TEXT PRIMARY KEY, session_id TEXT,
                           title TEXT, category TEXT, source TEXT
                           DEFAULT 'test');
    CREATE TABLE steps (sid TEXT, seq INTEGER, ts TEXT, tool TEXT,
                        phase TEXT, status TEXT, error TEXT, detail TEXT);
    """)
    con.execute("INSERT INTO sessions (sid, session_id, title, category) "
                "VALUES ('s1', 's1', '甲', 't')")
    con.execute("INSERT INTO sessions (sid, session_id, title, category) "
                "VALUES ('s2', 's2', '乙', 't')")
    # 时间戳相对 now 生成（防时间炸弹：绝不硬编码日期）。
    # s1 = 5 天前（旧），s2 = 1 天前（新），供 since_days 窗口测试切分。
    from datetime import datetime, timedelta
    d_old = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
    d_new = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    steps = [
        # s1: 9 步（seq 0..8），错误在 1（开场）与 7（收尾）
        ("s1", 0, f"{d_old} 10:00:00", "read", "call", None, None, None),
        ("s1", 1, f"{d_old} 10:00:01", "ls", "result", "error",
         "tool_permission_revoked", None),
        ("s1", 2, f"{d_old} 10:00:02", "ls", "result", "success", None, None),
        ("s1", 3, f"{d_old} 10:00:03", "edit", "result", "error",
         'Error: String to replace not found in file. old_string 不一致', None),
        ("s1", 4, f"{d_old} 10:00:04", "edit", "result", "success", None, None),
        ("s1", 5, f"{d_old} 10:00:05", "write", "result", "error",
         'Error: cannot write "D:\\x\\a.md": file no longer exists', None),
        ("s1", 6, f"{d_old} 10:00:06", "Bash", "result", "success", None, None),
        ("s1", 7, f"{d_old} 10:00:07", "web_fetch", "result", "error",
         "Error: web fetch failed: TypeError: fetch failed", None),
        ("s1", 8, f"{d_old} 10:00:08", "Bash", "result", "success", None, None),
        # s2: 完全未知的错误（unclassified）
        ("s2", 0, f"{d_new} 10:00:00", "foo", "result", "error",
         "zzz_mystery_failure_zzz", None),
        ("s2", 1, f"{d_new} 10:00:01", "foo", "result", "success", None, None),
    ]
    con.executemany("INSERT INTO steps VALUES (?,?,?,?,?,?,?,?)", steps)
    con.commit()
    con.close()
    return db

class TestClassify(unittest.TestCase):
    def test_env(self):
        for t in ("tool_permission_revoked",
                  "Error: sandbox-center blocked",
                  "Error: SetNamedSecurityInfoW failed (Win32 5)",
                  "Error: web fetch failed: TypeError: fetch failed",
                  "browser_instance_unknown",
                  "artifact_secret_detected"):
            self.assertEqual(classify_error(t), "env", t)

    def test_tool_interface(self):
        for t in ("Error: String to replace not found in file. old_string...",
                  'Error: cannot edit "D:\\a.md": file changed since it was read',
                  "Error: offset 536 is out of range (517 lines)",
                  "Error: subagent depth 2 exceeds maxDepth 1",
                  "workspace_edit_match_not_found",
                  "Error: grep pattern rejected by ripgrep: regex parse error"):
            self.assertEqual(classify_error(t), "tool_interface", t)

    def test_context(self):
        for t in ('Error: cannot write "D:\\x": file no longer exists',
                  "Error: File not found: C:\\x.md",
                  "skill_asset_invalid",
                  "filesystem_not_found"):
            self.assertEqual(classify_error(t), "context", t)

    def test_unclassified(self):
        self.assertEqual(classify_error("zzz_mystery_failure_zzz"),
                         "unclassified")

    def test_normalize_clusters(self):
        a = normalize_error('Error: cannot edit "D:\\a\\x.md": '
                            "file changed since it was read")
        b = normalize_error('Error: cannot edit "D:\\b\\y.py": '
                            "file changed since it was read")
        self.assertEqual(a, b)
        self.assertNotIn("D:", a)
        self.assertNotIn("x.md", a)


class TestCollectAndRender(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = _make_db(Path(cls.tmp.name))

    @classmethod
    def tearDownClass(cls):
        try:
            cls.tmp.cleanup()
        except OSError:
            pass  # Windows 偶发文件锁，残留于系统临时目录无害

    def test_collect_counts_and_buckets(self):
        errors, meta = collect_errors_from_db(self.db)
        self.assertEqual(meta["error_count"], 5)
        self.assertEqual(meta["total_steps"], 11)
        by_sid = {e["seq"]: e for e in errors if e["sid"] == "s1"}
        self.assertEqual(by_sid[1]["bucket"], "开场")   # 1/8
        self.assertEqual(by_sid[7]["bucket"], "收尾")   # 7/8
        self.assertEqual(by_sid[3]["class"], "tool_interface")
        self.assertEqual(by_sid[5]["class"], "context")

    def test_since_filters(self):
        # fixture：s1 错误在 5 天前，s2 在 1 天前（相对 now 生成）。
        # 4.5 天窗口只应纳入 s2 的错误（1 个自然日）。
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(days=4.5)).strftime("%Y-%m-%d")
        errors, _ = collect_errors_from_db(self.db, since_days=4.5)
        self.assertEqual(meta_days(errors), 1)
        self.assertTrue(errors, "窗口内应有错误")
        self.assertTrue(all(e["ts"] >= cutoff for e in errors))

    def test_report_sections(self):
        errors, meta = collect_errors_from_db(self.db)
        rep = render_report(errors, meta)
        for sec in ("三分类分布", "轨迹位置分桶", "错误模式明细"):
            self.assertIn(sec, rep)
        self.assertIn("unclassified", rep)  # 未知错误显式列出

    def test_report_empty(self):
        rep = render_report([], {"total_steps": 5, "sessions": 0})
        self.assertIn("无错误记录", rep)


def meta_days(errors):
    from collections import Counter
    return len(Counter(e["ts"][:10] for e in errors))


class TestSuggestAgents(unittest.TestCase):
    def _errors(self):
        return [
            {"sid": "s1", "seq": 1, "tool": "edit", "class": "tool_interface",
             "pattern": "String to replace not found in file. old_string",
             "error": "Error: String to replace not found in file. old_string 不一致"},
        ] * 5 + [
            {"sid": "s2", "seq": 2, "tool": "web_fetch", "class": "env",
             "pattern": "web fetch failed: TypeError: fetch failed",
             "error": "Error: web fetch failed: TypeError: fetch failed"},
        ]

    def test_builds_entries_with_evidence(self):
        out = build_suggestions(self._errors(), min_count=3)
        self.assertIn("人工审阅", out)
        self.assertIn("Edit/Write 前必须先 Read", out)
        self.assertIn("`s1#1`", out)          # 锚点
        self.assertIn("实测 5 次", out)        # 聚合计数
        # 未达 min_count 的网络模板（1 次）不出条目
        self.assertNotIn("网络类失败", out)

    def test_leftover_section(self):
        errors = self._errors() + [
            {"sid": "s3", "seq": 0, "tool": "foo", "class": "unclassified",
             "pattern": "zzz_mystery", "error": "zzz_mystery_failure"}]
        out = build_suggestions(errors, min_count=3)
        self.assertIn("待人工归因", out)
        self.assertIn("zzz_mystery", out)


_CARD_OK = """---
id: kc-20261006-0001
title: 测试卡片
type: pitfall
tags: [test]
anchors: [{session_id: s1, turn: 2}]
evidence: |
  Error: old_string was not found
confidence: 0.8
created: 2026-10-06
---
正文内容。
"""


class TestCards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "ok.md").write_text(_CARD_OK, encoding="utf-8", newline="\n")
        (root / "bad.md").write_text(
            "---\ntitle: 缺字段\ntype: unknown_type\n---\n正文",
            encoding="utf-8", newline="\n")
        (root / "nofm.md").write_text("没有 frontmatter", encoding="utf-8",
                                      newline="\n")
        cls.root = root
        cls.db = _make_db(Path(cls.tmp.name))

    @classmethod
    def tearDownClass(cls):
        try:
            cls.tmp.cleanup()
        except OSError:
            pass  # Windows 偶发文件锁，残留于系统临时目录无害

    def test_validate_card_ok(self):
        errs, warns, fm = validate_card(self.root / "ok.md")
        self.assertEqual(errs, [])
        self.assertEqual(fm["id"], "kc-20261006-0001")

    def test_validate_card_bad(self):
        errs, _w, _fm = validate_card(self.root / "bad.md")
        joined = " ".join(errs)
        self.assertIn("id", joined)
        self.assertIn("type", joined)
        self.assertIn("anchors", joined)
        self.assertIn("evidence", joined)

    def test_validate_card_no_frontmatter(self):
        errs, _w, fm = validate_card(self.root / "nofm.md")
        self.assertTrue(errs)
        self.assertIsNone(fm)

    def test_validate_cards_with_anchor_check(self):
        # 降级解析器（无 PyYAML）也必须解析 anchors 并查库——
        # 不再 skipUnless：有无 yaml 两解释器下行为必须一致。
        results, summary = validate_cards(self.root, self.db)
        self.assertEqual(summary["cards"], 3)
        self.assertEqual(summary["error"], 2)  # bad.md + nofm.md
        self.assertEqual(summary["anchor_checked"], 1)
        self.assertEqual(summary["anchor_misses"], 0)  # s1 在库里
        ok = next(r for r in results if r["path"] == "ok.md")
        self.assertEqual(ok["errors"], [])

    def test_anchor_unknown_reported(self):
        tmp2 = tempfile.TemporaryDirectory()
        root = Path(tmp2.name)
        (root / "c.md").write_text(
            _CARD_OK.replace("session_id: s1", "session_id: ghost"),
            encoding="utf-8", newline="\n")
        try:
            _results, summary = validate_cards(root, self.db)
            self.assertEqual(summary["anchor_misses"], 1)
        finally:
            tmp2.cleanup()


if __name__ == "__main__":
    unittest.main()
