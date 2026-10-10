# -*- coding: utf-8 -*-
"""v0.3 新模块测试：DeepSeek 导出解析 / 检索 / 分层读取 / 交接包 / MCP。"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harvester.adapters.deepseek_export import (DeepSeekExportAdapter,
                                                _content_text, _norm_time)
from harvester.indexing import index_session
from harvester.mcpserver import HarvesterMcpServer
from harvester.models import Message, SessionRecord
from harvester.pack import build_pack, est_tokens
from harvester.reader import render_read, split_turns

# ---------- DeepSeek 导出解析 ----------

CONV_A = {  # 形态 A：顶层数组 + epoch + string content
    "id": "conv-001", "title": "异步调试", "create_time": 1704067200,
    "model": "deepseek-chat",
    "messages": [
        {"role": "user", "content": "为什么 await 不生效?", "create_time": 1704067200},
        {"role": "assistant", "content": "你在调用处漏了 await…",
         "create_time": 1704067205, "reasoning_content": "先检查函数定义。"},
    ],
}
CONV_B = {  # 形态 B 变体：ISO 时间 + parts 内容
    "id": "conv-002", "title": "SQL 优化", "created_at": "2026-03-05T10:30:00Z",
    "updated_at": "2026-03-05T11:00:00Z",
    "messages": [
        {"role": "USER", "content": {"parts": ["帮我优化这条 SQL", "要快"]}},
        {"role": "ASSISTANT", "content": [{"text": "加索引即可"}, {"text": "再看执行计划"}]},
        {"role": "unknown_role", "content": "应被跳过并警告"},
        "非对象条目",
    ],
}


def _try_unlink(f: Path) -> None:
    """沙箱 shim 可能令 unlink 抛 PermissionError（WinError 32），容错。"""
    try:
        f.unlink(missing_ok=True)
    except PermissionError:
        pass


class TestDeepSeekParse(unittest.TestCase):
    def _adapter_with(self, data) -> DeepSeekExportAdapter:
        # 注意：沙箱对系统 Temp 的 unlink 有 shim 钩子（WinError 32），
        # 临时文件放项目内 tests/_tmp 并容错清理。
        tmp = Path(__file__).resolve().parent / "_tmp"
        tmp.mkdir(exist_ok=True)
        f = tmp / f"export_{id(data):x}.json"
        f.write_text(json.dumps(data, ensure_ascii=False),
                     encoding="utf-8", newline="\n")
        self.addCleanup(_try_unlink, f)
        return DeepSeekExportAdapter(paths=[f])

    def test_top_level_array(self):
        ad = self._adapter_with([CONV_A, CONV_B])
        rep = ad.detect()
        self.assertEqual(rep.status, "OK")
        self.assertEqual(rep.session_count, 2)

    def test_conversations_key(self):
        ad = self._adapter_with({"conversations": [CONV_A]})
        self.assertEqual(ad.detect().session_count, 1)
        items = ad.list_sessions()
        self.assertEqual(items[0]["session_id"], "conv-001")
        self.assertEqual(items[0]["message_count"], 2)

    def test_unknown_top_level_rejected(self):
        ad = self._adapter_with({"chats": []})  # 未核验形态 → 拒绝而非猜测
        self.assertEqual(ad.detect().status, "STUB")

    def test_role_and_parts_normalization(self):
        ad = self._adapter_with([CONV_B])
        rec = ad.load_session("conv-002")
        roles = [m.role for m in rec.messages]
        self.assertEqual(roles, ["user", "assistant"])
        self.assertIn("帮我优化这条 SQL", rec.messages[0].text)
        self.assertIn("再看执行计划", rec.messages[1].text)
        self.assertTrue(rec.extra["lossy"])          # 有跳过条目 → lossy
        self.assertTrue(any("unknown_role" in w for w in rec.extra["warnings"]))

    def test_reasoning_content_as_note(self):
        ad = self._adapter_with([CONV_A])
        rec = ad.load_session("conv-001")
        notes = [m for m in rec.messages if m.role == "note"]
        self.assertEqual(len(notes), 1)
        self.assertIn("先检查函数定义", notes[0].text)

    def test_missing_id_skipped_with_warning(self):
        ad = self._adapter_with([{"title": "无 id", "messages": []}])
        self.assertEqual(ad.list_sessions(), [])
        self.assertTrue(any("id" in w for w in ad._parse_warns))

    def test_not_configured_is_stub_with_hints(self):
        rep = DeepSeekExportAdapter().detect()
        self.assertEqual(rep.status, "STUB")
        self.assertTrue(rep.hints)


class TestContentAndTime(unittest.TestCase):
    def test_content_variants(self):
        self.assertEqual(_content_text("abc"), ("abc", []))
        t, _ = _content_text({"parts": ["a", {"text": "b"}]})
        self.assertEqual(t, "a\nb")
        t, w = _content_text({"weird": 1})
        self.assertEqual(t, "")
        self.assertTrue(w)

    def test_norm_time(self):
        s = _norm_time(1704067200)
        self.assertIn("2024-01-01", s)          # epoch 秒 → 本地 ISO
        self.assertIn("2024", _norm_time(1704067200000))
        self.assertEqual(_norm_time("2026-01-01T00:00:00Z"),
                         "2026-01-01T00:00:00Z")
        self.assertIsNone(_norm_time(None))


# ---------- 分层读取 ----------

def _mk_rec():
    msgs = [
        Message("user", "第一问"), Message("assistant", "第一答"),
        Message("note", "[reasoning]\n中间思考"),
        Message("user", "第二问"), Message("assistant", "第二答"),
    ]
    return SessionRecord(source="t", session_id="s1", title="测试",
                         messages=msgs)


class TestReader(unittest.TestCase):
    def test_split_turns(self):
        turns = split_turns(_mk_rec())
        self.assertEqual(len(turns), 2)
        self.assertEqual([m.role for m in turns[0]], ["user", "assistant", "note"])

    def test_render_read_last_and_range(self):
        rec = _mk_rec()
        text, n = render_read(rec, "last")
        self.assertIn("第二答", text)
        self.assertEqual(n, 2)
        with self.assertRaises(IndexError):
            render_read(rec, 5)


# ---------- 交接包 ----------

class TestPack(unittest.TestCase):
    def test_est_tokens(self):
        self.assertGreaterEqual(est_tokens("中文内容四个字"), 6)
        self.assertGreaterEqual(est_tokens("abcdefgh"), 2)

    def test_build_pack_budget(self):
        rec = SessionRecord(
            source="t", session_id="s1", title="长会话",
            messages=[Message("user", "问题" * 100),
                      Message("assistant", "回答" * 100)] * 20)
        text = build_pack([(rec, 7)], total_budget=500, question="接着做")
        self.assertIn("上下文交接包", text)
        self.assertIn("接着做", text)
        self.assertIn("[!]", text)   # 预算不足 → 截断标注
        self.assertIn("[7]", text)


# ---------- 索引与检索 ----------

class TestIndexing(unittest.TestCase):
    def test_index_and_search_roundtrip(self):
        import sqlite3
        con = sqlite3.connect(":memory:")
        from harvester.indexing import SCHEMA
        con.executescript(SCHEMA)
        rec = SessionRecord(
            source="autoclaw", session_id="x1", title="登录态探测",
            created_at="2026-09-01T10:00:00",
            messages=[Message("user", "怎么探测浏览器 cookie 登录态?"),
                      Message("assistant", "读 Cookies SQLite 库。")])
        n = index_session(con, rec, file="f.md")
        self.assertEqual(n, 2)
        # 覆盖写不重复
        index_session(con, rec)
        con.commit()
        from harvester.indexing import _fts_query
        self.assertEqual(_fts_query('cookie "登录"'), '"cookie" OR "登录"')
        rows = con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        self.assertEqual(rows, 2)
        con.close()


# ---------- MCP server ----------

class TestMcp(unittest.TestCase):
    def setUp(self):
        self.srv = HarvesterMcpServer()

    def _rpc(self, method, params=None, mid=1):
        return self.srv.handle({"jsonrpc": "2.0", "id": mid,
                                "method": method, "params": params or {}})

    def test_initialize(self):
        r = self._rpc("initialize")
        self.assertEqual(r["result"]["protocolVersion"], "2024-11-05")
        self.assertIn("tools", r["result"]["capabilities"])

    def test_tools_list(self):
        r = self._rpc("tools/list")
        names = {t["name"] for t in r["result"]["tools"]}
        # v0.45：会话面 4 个 + 进化数据面 6 个（漂移门在 test_v45_mcp_tools.py）
        self.assertEqual(names, {"list_sessions", "search_history",
                                 "read_session", "pack_context",
                                 "topic_list", "topic_export", "chain_read",
                                 "suggest_list", "cards_list",
                                 "artifacts_list"})

    def test_unknown_tool_is_error_result(self):
        r = self._rpc("tools/call", {"name": "nope", "arguments": {}})
        self.assertTrue(r["result"]["isError"])

    def test_read_session_bad_no(self):
        # 注意：read_session 基于实时纲要，序号会随语料增长失效——
        # 用一个必然越界的大数，不要用小数值（元宝 1223 会话入库后 999 已合法）
        r = self._rpc("tools/call", {"name": "read_session",
                                     "arguments": {"no": 10 ** 9}})
        self.assertTrue(r["result"]["isError"])
        self.assertIn("无序号", r["result"]["content"][0]["text"])

    def test_notification_no_reply(self):
        self.assertIsNone(self.srv.handle(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_unknown_request_method(self):
        r = self._rpc("resources/list")
        self.assertEqual(r["error"]["code"], -32601)


if __name__ == "__main__":
    unittest.main()
