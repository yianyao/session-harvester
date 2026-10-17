# -*- coding: utf-8 -*-
"""v0.77 测试：报告族 md 索引 + 报告文件全文（P5 后续，用户 2026-10-17 批准）。

依据：`docs/HANDOFF-v0.52-next.md` §三-2 挂账两项（§2-⑤ 报告族 md 索引页 /
「昨日报告」卡正文全文查看）。钉的验收（每条红=什么坏了）：

1. `/api/reports-index`：目录不存在 → found=False+hint（诚实空态）；
   只列 .md **文件**（子目录/非 md 不入列，防目录里混进别的东西被当成报告）；
   mtime 新→旧排序；daily-YYYY-MM-DD.md 自动抽日期（前端标「日报」用）；
2. `/api/report-get`：name 只收**纯文件名**（.md 结尾、无路径分隔符、
   不以 . 开头）+ resolve 后必须仍在报告目录之下（双保险防路径穿越）；
   正常 → 全文 + n_chars；文件不存在 → found=False+hint；非法名 → ValueError；
3. **只读**：跑前后目录字节指纹不变 → 有人顺手写/删文件 → 红。

夹具全 tempfile；真目录一字节不动。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from harvester.apiserve import api_report_get, api_reports_index


class TestReportsIndexEndpoint(unittest.TestCase):
    """/api/reports-index（view 报告页「报告索引」区取数口）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name) / "reports"
        self.d.mkdir()

    def test_lists_md_files_only_with_daily_date(self):
        (self.d / "report-errors.md").write_text("e", encoding="utf-8")
        (self.d / "daily-2026-10-16.md").write_text("d", encoding="utf-8")
        (self.d / "notes.txt").write_text("x", encoding="utf-8")
        (self.d / "sub").mkdir()
        (self.d / "sub" / "nested.md").write_text("x", encoding="utf-8")
        (self.d / "draft.md.bak").write_text("x", encoding="utf-8")
        r = api_reports_index(self.d, {})
        self.assertTrue(r["found"])
        names = sorted(f["name"] for f in r["files"])
        self.assertEqual(names, ["daily-2026-10-16.md", "report-errors.md"],
                         "只列 .md 文件；子目录/非 md/带后缀变体一律不入列")
        daily = next(f for f in r["files"]
                     if f["name"] == "daily-2026-10-16.md")
        self.assertEqual(daily["date"], "2026-10-16")
        other = next(f for f in r["files"]
                     if f["name"] == "report-errors.md")
        self.assertIsNone(other["date"])

    def test_sorted_mtime_newest_first(self):
        a = self.d / "a-report.md"
        b = self.d / "b-report.md"
        a.write_text("a", encoding="utf-8")
        b.write_text("b", encoding="utf-8")
        os.utime(a, (1000, 1000))
        os.utime(b, (2000, 2000))
        r = api_reports_index(self.d, {})
        self.assertEqual([f["name"] for f in r["files"]],
                         ["b-report.md", "a-report.md"])

    def test_missing_dir_honest_empty(self):
        r = api_reports_index(Path(self.tmp.name) / "nope", {})
        self.assertFalse(r["found"])
        self.assertIn("hint", r)

    def test_empty_dir_found_with_zero(self):
        r = api_reports_index(self.d, {})
        self.assertTrue(r["found"])
        self.assertEqual(r["files"], [])


class TestReportGetEndpoint(unittest.TestCase):
    """/api/report-get?name=（报告索引的「看全文」口 + 昨日卡全文懒加载）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name) / "reports"
        self.d.mkdir()

    def test_read_full_text(self):
        (self.d / "daily-2026-10-16.md").write_text("# 头\n正文",
                                                    encoding="utf-8")
        r = api_report_get(self.d, {"name": "daily-2026-10-16.md"})
        self.assertTrue(r["found"])
        self.assertEqual(r["md"], "# 头\n正文")
        self.assertEqual(r["n_chars"], len("# 头\n正文"))
        self.assertEqual(r["name"], "daily-2026-10-16.md")

    def test_missing_file_honest_empty(self):
        r = api_report_get(self.d, {"name": "daily-2026-10-16.md"})
        self.assertFalse(r["found"])
        self.assertIn("hint", r)

    def test_invalid_and_traversal_names_rejected(self):
        for bad in (None, "", "../../etc.md", "a/b.md", "a\\b.md", "x.txt",
                    "C:\\boot.md", "report.md.bak", "..md", ".hidden.md"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                api_report_get(self.d, {"name": bad})

    def test_directory_named_md_is_not_read(self):
        (self.d / "weird.md").mkdir()
        r = api_report_get(self.d, {"name": "weird.md"})
        self.assertFalse(r["found"])

    def test_readonly_fingerprint(self):
        (self.d / "daily-2026-10-16.md").write_text("正文", encoding="utf-8")
        fp0 = sorted((p.name, p.read_bytes()) for p in self.d.iterdir())
        api_reports_index(self.d, {})
        api_report_get(self.d, {"name": "daily-2026-10-16.md"})
        fp1 = sorted((p.name, p.read_bytes()) for p in self.d.iterdir())
        self.assertEqual(fp0, fp1,
                         "索引/取全文是只读，任何文件变化都是写事故")


if __name__ == "__main__":
    unittest.main()
