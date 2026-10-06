# -*- coding: utf-8 -*-
"""纯函数单测（标准库 unittest，零依赖；运行: python -m unittest discover tests）。

覆盖审查点名的 parse_selection / _extract_role_text / _entry_text / kb_stats，
以及本轮修复回归：normalize_text、cookie 域名精确匹配、文件名防覆盖后缀、
桩位路径注入。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harvester.adapters.stubs import DeepSeekDesktopStub
from harvester.exporter import _sid_suffix, parse_selection
from harvester.models import SessionRecord, normalize_text

# vscode_copilot / autoclaw 以子模块方式导入
from harvester.adapters import vscode_copilot  # noqa: E402

_extract_role_text = vscode_copilot._extract_role_text

from harvester.adapters.autoclaw import _entry_text  # noqa: E402
from harvester.distill import kb_stats  # noqa: E402
from harvester.weblogin import _host_matches  # noqa: E402


class TestParseSelection(unittest.TestCase):
    def test_single_and_range(self):
        picked, dropped = parse_selection("3,5-9,12", 20)
        self.assertEqual(picked, [3, 5, 6, 7, 8, 9, 12])
        self.assertEqual(dropped, [])

    def test_dedupe_and_order(self):
        picked, _ = parse_selection("9,3,5-7,5", 20)
        self.assertEqual(picked, [3, 5, 6, 7, 9])

    def test_out_of_range_dropped(self):
        picked, dropped = parse_selection("1,15,99", 10)
        self.assertEqual(picked, [1])
        self.assertEqual(dropped, [15, 99])

    def test_invalid_raises(self):
        with self.assertRaises(ValueError):
            parse_selection("abc", 10)

    def test_reversed_range(self):
        picked, _ = parse_selection("7-5", 20)
        self.assertEqual(picked, [5, 6, 7])


class TestNormalizeText(unittest.TestCase):
    def test_crlf(self):
        self.assertEqual(normalize_text("a\r\nb"), "a\nb")

    def test_none(self):
        self.assertEqual(normalize_text(None), "")


class TestExtractRoleText(unittest.TestCase):
    def test_plain_text(self):
        self.assertEqual(_extract_role_text({"role": "user", "text": "hi"}),
                         ("user", "hi"))

    def test_crlf_normalized(self):
        _, txt = _extract_role_text({"role": "user", "text": "a\r\nb"})
        self.assertEqual(txt, "a\nb")

    def test_list_content(self):
        obj = {"role": "assistant",
               "content": [{"text": "x"}, {"text": "y"}]}
        self.assertEqual(_extract_role_text(obj), ("assistant", "x\ny"))

    def test_non_dict(self):
        self.assertEqual(_extract_role_text("nope"), ("", ""))


class TestEntryText(unittest.TestCase):
    def test_request(self):
        self.assertEqual(_entry_text("request", {"data": {"text": " q "}}), "q")

    def test_assistant_blocks(self):
        payload = {"data": {"content": [{"text": "a"}, {"text": "b"}]}}
        self.assertEqual(_entry_text("assistant", payload), "a\nb")

    def test_unrelated_type(self):
        self.assertEqual(_entry_text("tool_call", {"data": {"text": "x"}}), "")


class TestHostMatches(unittest.TestCase):
    def test_exact(self):
        self.assertTrue(_host_matches("doubao.com", ["doubao.com"]))

    def test_subdomain(self):
        self.assertTrue(_host_matches("www.doubao.com", ["doubao.com"]))

    def test_lookalike_rejected(self):
        # 审查指出的误判场景：mydoubao.com 不应命中 doubao.com
        self.assertFalse(_host_matches("mydoubao.com", ["doubao.com"]))

    def test_other_rejected(self):
        self.assertFalse(_host_matches("example.com", ["doubao.com"]))


class TestSidSuffix(unittest.TestCase):
    def test_stable_and_differs(self):
        a = _sid_suffix("abc")
        self.assertEqual(a, _sid_suffix("abc"))
        self.assertEqual(len(a), 6)
        self.assertNotEqual(a, _sid_suffix("abd"))


class TestStubPathInjection(unittest.TestCase):
    def test_inject_overrides_class_paths(self):
        stub = DeepSeekDesktopStub(paths=["C:/tmp/fake-a", "C:/tmp/fake-b"])
        # Windows Path 会归一化分隔符，统一按 posix 斜杠比较
        self.assertEqual([p.as_posix() for p in stub.paths],
                         ["C:/tmp/fake-a", "C:/tmp/fake-b"])

    def test_no_injection_keeps_defaults(self):
        stub = DeepSeekDesktopStub()
        self.assertEqual(stub.paths, DeepSeekDesktopStub.paths)


class TestKbStats(unittest.TestCase):
    def test_missing_reference_detected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "topics").mkdir()
            (root / "INDEX.md").write_text(
                "| `topics/exists.md` | t | | |\n| `topics/ghost.md` | g | | |\n",
                encoding="utf-8")
            (root / "topics" / "exists.md").write_text("x", encoding="utf-8")
            s = kb_stats(root)
            self.assertEqual(s["topic_files"], 1)
            self.assertEqual(s["index_rows"], 2)
            self.assertEqual(s["missing_files"], ["topics/ghost.md"])

    def test_prose_reference_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "topics").mkdir()
            (root / "INDEX.md").write_text(
                "正文里提到 `README.md` 与 skill 结构，不应计入。\n",
                encoding="utf-8")
            s = kb_stats(root)
            self.assertEqual(s["index_rows"], 0)
            self.assertEqual(s["missing_files"], [])


class TestSessionRecord(unittest.TestCase):
    def test_title_crlf_normalized(self):
        rec = SessionRecord(source="x", session_id="1", title="a\r\nb")
        self.assertEqual(rec.title, "a\nb")

    def test_message_count_counts_roles(self):
        from harvester.models import Message
        rec = SessionRecord(source="x", session_id="1", title="t", messages=[
            Message("user", "u"), Message("note", "n"), Message("assistant", "a")])
        self.assertEqual(rec.message_count, 2)


if __name__ == "__main__":
    unittest.main()
