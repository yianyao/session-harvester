# -*- coding: utf-8 -*-
"""v0.5 变更测试：tool_result 映射 / report-tools 统计 / FTS bigram 中文
检索 / MCP pack_context 修复 + 4 工具真实 tools/call 测试。"""

from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harvester.indexing import (
    _cjk_bigram,
    _fts_query,
    index_session,
    search,
)
from harvester.mcpserver import HarvesterMcpServer
from harvester.models import Message, SessionRecord
from harvester.toolstats import collect_stats, render_report

TMP = Path(__file__).resolve().parent / "_tmp"


def _try_unlink(f: Path) -> None:
    try:
        f.unlink(missing_ok=True)
    except PermissionError:
        pass


# ---------- AutoClaw 合成库（与 test_v04 同构） ----------

SCHEMA = """
CREATE TABLE work_sessions (
  session_id TEXT PRIMARY KEY, title TEXT, created_at TEXT,
  updated_at TEXT, deleted_at TEXT);
CREATE TABLE neutral_session_entries (
  session_id TEXT, entry_id TEXT, session_seq INTEGER, run_id TEXT,
  entry_type TEXT, payload TEXT, committed_at TEXT,
  PRIMARY KEY (session_id, entry_id));
CREATE TABLE schema_migrations (seq INTEGER, name TEXT);
"""


def _mk_db(path: Path, rows: list[tuple]) -> None:
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO work_sessions VALUES (?,?,?,?,?)",
                ("s1", "工具统计样本", "2026-09-28T05:48:10.000Z",
                 "2026-09-28T06:00:00.000Z", None))
    for i, (run, et, data, committed) in enumerate(rows, 1):
        con.execute(
            "INSERT INTO neutral_session_entries VALUES (?,?,?,?,?,?,?)",
            ("s1", f"e{i}", i, run, et,
             json.dumps({"data": data}, ensure_ascii=False), committed))
    con.commit()
    con.close()


class TestToolResultMapping(unittest.TestCase):
    """tool_result → note 映射（v0.5 之前整类条目被丢弃）。"""

    def setUp(self):
        TMP.mkdir(parents=True, exist_ok=True)
        base = TMP / f"trbase_{id(self):x}"
        acc = base / "accounts" / "acc1" / "runtime"
        acc.mkdir(parents=True, exist_ok=True)
        self.base = base
        self.addCleanup(lambda: _try_unlink(acc / "runtime.sqlite"))
        rows = [
            ("r1", "request", {"text": "跑工具"}, "2026-09-28T05:48:10.000Z"),
            ("r1", "tool_call", {"toolName": "read", "input": {"path": "a"}},
             "2026-09-28T05:48:11.000Z"),
            ("r1", "tool_result",
             {"toolName": "read", "status": "success", "error": None,
              "toolCallId": "c1", "content": "ok"},
             "2026-09-28T05:48:12.000Z"),
            ("r1", "tool_call", {"toolName": "browser_tool", "input": {}},
             "2026-09-28T05:48:13.000Z"),
            ("r1", "tool_result",
             {"toolName": "browser_tool", "status": "error",
              "error": {"code": "browser_element_obscured",
                        "message": "元素被遮挡", "retryable": True},
              "toolCallId": "c2"},
             "2026-09-28T05:48:14.000Z"),
        ]
        _mk_db(acc / "runtime.sqlite", rows)

    def test_notes_produced(self):
        from harvester.adapters.autoclaw import AutoClawAdapter
        ad = AutoClawAdapter(search_bases=[str(self.base)])
        rec = ad.load_session("s1")
        results = [m.text for m in rec.messages
                   if m.role == "note" and m.text.startswith("[tool_result]")]
        self.assertEqual(len(results), 2)
        self.assertIn("[tool_result] read: success", results)
        err = next(t for t in results if "browser_tool" in t)
        self.assertIn("error", err)
        self.assertIn("browser_element_obscured", err)

    def test_stats_from_real_adapter_output(self):
        from harvester.adapters.autoclaw import AutoClawAdapter
        from harvester.toolstats import collect_stats
        ad = AutoClawAdapter(search_bases=[str(self.base)])
        stats = collect_stats([ad.load_session("s1")])
        self.assertEqual(stats["read"].calls, 1)
        self.assertEqual(stats["read"].success, 1)
        self.assertEqual(stats["browser_tool"].error, 1)
        self.assertAlmostEqual(stats["browser_tool"].fail_rate, 1.0)


# ---------- toolstats 单元 ----------

class TestToolStats(unittest.TestCase):
    def _rec(self, notes: list[str]) -> SessionRecord:
        return SessionRecord(
            source="autoclaw", session_id="x", title="t",
            created_at=None, updated_at=None,
            messages=[Message("note", n) for n in notes])

    def test_counts_and_rate(self):
        rec = self._rec([
            "[tool_call] ls: -la",
            "[tool_result] ls: error tool_permission_revoked",
            "[tool_call] read: a.md",
            "[tool_result] read: success",
            "[tool_result] read: success",
        ])
        st = collect_stats([rec])
        self.assertEqual(st["ls"].calls, 1)
        self.assertEqual(st["ls"].error, 1)
        self.assertEqual(st["read"].calls, 1)
        self.assertEqual(st["read"].success, 2)
        # result 比 call 多（跨 run 迟到结果）不崩溃，只如实计数
        self.assertAlmostEqual(st["read"].fail_rate, 0.0)

    def test_render_orders_by_fail_rate(self):
        rec = self._rec([
            "[tool_call] read: a",
            "[tool_result] read: success",
            "[tool_call] bad: x",
            "[tool_result] bad: error E_TIMEOUT",
        ])
        report = render_report(collect_stats([rec]))
        self.assertIn("| bad | 1 | 0 | 1 | 100.0% |", report)
        self.assertLess(report.index("| bad |"), report.index("| read |"))
        self.assertIn("E_TIMEOUT", report)

    def test_empty_state(self):
        report = render_report({})
        self.assertIn("无工具调用数据", report)


# ---------- FTS bigram ----------

class TestCjkBigram(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(_cjk_bigram("登录态"), "登录 录态")
        self.assertEqual(_cjk_bigram("工作日志"), "工作 作日 日志")

    def test_mixed_ascii(self):
        self.assertEqual(_cjk_bigram("跑bigram方案"), "跑 bigram 方案")
        self.assertEqual(_cjk_bigram("cookie"), "cookie")

    def test_single_char_and_empty(self):
        self.assertEqual(_cjk_bigram("跑"), "跑")
        self.assertEqual(_cjk_bigram(""), "")
        self.assertEqual(_cjk_bigram("，。"), "，。")

    def test_query_rewrite(self):
        q = _fts_query("登录态")
        self.assertIn('"登录 录态"', q)
        q2 = _fts_query("跑")
        self.assertEqual(q2, '"跑*"')  # 单 CJK 字前缀兜底


class TestFtsBigramRoundtrip(unittest.TestCase):
    """建库→检索往返：2 字词必须命中，snippet 须为原文而非二元组。"""

    def setUp(self):
        TMP.mkdir(parents=True, exist_ok=True)
        self.db = TMP / f"fts_{id(self):x}.db"
        self.addCleanup(lambda: _try_unlink(self.db))
        rec = SessionRecord(
            source="workbuddy", session_id="ws::2026-09-01", title="登录态探测",
            created_at="2026-09-01", updated_at="2026-09-01",
            messages=[
                Message("user", "帮我设计浏览器 cookie 的登录态探测方案"),
                Message("assistant", "运行中浏览器对 Cookies 持独占锁，"
                                     "需要完全退出浏览器后再复制库文件。"),
            ])
        con = __import__("harvester.indexing", fromlist=["_connect"]) \
            ._connect(self.db)
        try:
            index_session(con, rec)
            con.commit()
        finally:
            con.close()

    def test_two_char_query_hits(self):
        rows = search(self.db, "登录")
        self.assertTrue(rows)
        rows = search(self.db, "独占锁")
        self.assertTrue(rows)

    def test_long_query_hits_and_no_cross_boundary(self):
        self.assertTrue(search(self.db, "登录态"))
        # 不存在的词必须不命中（证明改写没有引入跨词假阳性）
        self.assertFalse(search(self.db, "态独占"))

    def test_snippet_is_original_text(self):
        rows = search(self.db, "独占锁")
        self.assertIn("独占锁", rows[0]["snippet"])
        self.assertNotIn(" 占独", rows[0]["snippet"])  # 二元组汤不该出现
        self.assertEqual(rows[0]["role"], "assistant")


# ---------- MCP：真实 tools/call（v0.4 只测了 tools/list 清单） ----------

class _FakeAdapter:
    id = "fake-src"

    def __init__(self, rec: SessionRecord):
        self.rec = rec

    def load_session(self, session_id: str) -> SessionRecord:
        return self.rec


class TestMcpToolsCall(unittest.TestCase):
    def setUp(self):
        TMP.mkdir(parents=True, exist_ok=True)
        self.rec = SessionRecord(
            source="fake-src", session_id="s1", title="交接包样本",
            created_at="2026-10-01", updated_at="2026-10-01",
            messages=[
                Message("user", "第一条问题", timestamp="2026-10-01 10:00:00"),
                Message("assistant", "第一条回答", timestamp="2026-10-01 10:00:05"),
            ])
        self.srv = HarvesterMcpServer()
        self.srv._cache = ({self.rec.source: _FakeAdapter(self.rec)},
                           [{"no": 1, "adapter": self.rec.source,
                             "session_id": "s1", "title": "交接包样本",
                             "created_at": "2026-10-01",
                             "message_count": 2, "preview": "x"}])
        self._call_id = 0

    def _call(self, name: str, arguments: dict):
        self._call_id += 1
        out = self.srv.handle({
            "jsonrpc": "2.0", "id": self._call_id, "method": "tools/call",
            "params": {"name": name, "arguments": arguments}})
        return out

    def test_list_sessions(self):
        out = self._call("list_sessions", {})
        self.assertNotIn("isError", out)
        self.assertIn("交接包样本", out["result"]["content"][0]["text"])

    def test_read_session(self):
        out = self._call("read_session", {"no": 1, "turn": 1})
        self.assertNotIn("isError", out)
        self.assertIn("第一条回答", out["result"]["content"][0]["text"])

    def test_pack_context_fixed(self):
        """回归：修复前把 outline 条目 dict 当序号传给 build_pack，
        输出静默垃圾（含 dict repr）且不报错。"""
        out = self._call("pack_context", {"nos": "1", "tokens": 800})
        self.assertNotIn("isError", out)
        text = out["result"]["content"][0]["text"]
        self.assertIn("[1]", text)               # 序号正确渲染
        self.assertIn("交接包样本", text)
        self.assertNotIn("{'no'", text)          # dict repr 不再泄漏
        self.assertNotIn("message_count", text)

    def test_search_history_with_db(self):
        db = TMP / f"mcp_{id(self):x}.db"
        self.addCleanup(lambda: _try_unlink(db))
        from harvester.indexing import _connect as _c
        con = _c(db)
        try:
            index_session(con, self.rec)
            con.commit()
        finally:
            con.close()
        self.srv._db = db
        out = self._call("search_history", {"query": "第一条回答"})
        self.assertNotIn("isError", out)
        self.assertIn("第一条回答", out["result"]["content"][0]["text"])

    def test_unknown_no_is_error(self):
        out = self._call("read_session", {"no": 99})
        self.assertNotIn("isError", out)  # ValueError → 错误文本而非崩溃
        self.assertIn("无序号", out["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
