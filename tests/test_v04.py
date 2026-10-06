# -*- coding: utf-8 -*-
"""v0.4 变更测试：AutoClaw 保真度 / schema 守卫 / workspace chatSessions
补丁日志 / ChatGPT+Claude 官方导出解析 / 时间戳时区统一。"""

from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harvester.adapters.autoclaw import AutoClawAdapter
from harvester.adapters.official_export import ChatGptExportAdapter, ClaudeExportAdapter
from harvester.adapters.vscode_copilot import _replay_patches, _ws_extract
from harvester.models import to_local_ts

TMP = Path(__file__).resolve().parent / "_tmp"


def _try_unlink(f: Path) -> None:
    """沙箱对系统 Temp 的 unlink 有 shim 钩子（WinError 32），容错清理。"""
    try:
        f.unlink(missing_ok=True)
    except PermissionError:
        pass


# ---------- to_local_ts ----------

class TestToLocalTs(unittest.TestCase):
    def test_utc_z_converts(self):
        # 2026-09-28T05:48:10Z 北京时间应为 13:48:10（固定偏移不依赖 DST）
        got = to_local_ts("2026-09-28T05:48:10.951Z")
        self.assertRegex(got, r"2026-09-28 \d{2}:\d{2}:\d{2}")
        self.assertNotEqual(got, "2026-09-28 05:48:10")

    def test_epoch(self):
        local = datetime.fromtimestamp(1700000000)
        self.assertEqual(to_local_ts(1700000000),
                         local.strftime("%Y-%m-%d %H:%M:%S"))
        self.assertEqual(to_local_ts(1700000000000),
                         local.strftime("%Y-%m-%d %H:%M:%S"))
        self.assertEqual(to_local_ts("1700000000"),
                         local.strftime("%Y-%m-%d %H:%M:%S"))

    def test_naive_and_unparsable(self):
        self.assertEqual(to_local_ts("2026-03-05T10:30:00"), "2026-03-05 10:30:00")
        self.assertEqual(to_local_ts("不是时间"), "不是时间")
        self.assertIsNone(to_local_ts(None))
        self.assertIsNone(to_local_ts(""))


# ---------- AutoClaw：合成库 ----------

SCHEMA = """
CREATE TABLE work_sessions (
  session_id TEXT PRIMARY KEY, title TEXT, created_at TEXT,
  updated_at TEXT, deleted_at TEXT);
CREATE TABLE neutral_session_entries (
  session_id TEXT, entry_id TEXT, session_seq INTEGER, run_id TEXT,
  entry_type TEXT, payload TEXT, committed_at TEXT,
  PRIMARY KEY (session_id, entry_id));
"""


def _mk_db(path: Path, rows: list[tuple], schema: str = SCHEMA,
           sessions: list[tuple] | None = None,
           insert_session: bool = True) -> None:
    con = sqlite3.connect(path)
    con.executescript(schema)
    if insert_session:
        default = ("s1", "测试会话",
                   "2026-09-28T05:48:10.000Z", "2026-09-28T06:00:00.000Z", None)
        con.execute("INSERT INTO work_sessions VALUES (?,?,?,?,?)",
                    tuple(sessions) if sessions else default)
    for i, (run, et, data, committed) in enumerate(rows, 1):
        con.execute(
            "INSERT INTO neutral_session_entries VALUES (?,?,?,?,?,?,?)",
            ("s1", f"e{i}", i, run, et,
             json.dumps({"data": data}, ensure_ascii=False), committed))
    con.commit()
    con.close()


class AutoClawTestBase(unittest.TestCase):
    def setUp(self):
        TMP.mkdir(parents=True, exist_ok=True)
        self.db = TMP / f"autoclaw_{id(self):x}.sqlite"
        self.addCleanup(_try_unlink, self.db)
        self.base = TMP / f"base_{id(self):x}"
        self.addCleanup(lambda: _try_unlink(self.base) if self.base.is_file()
                        else None)
        acc = self.base / "accounts" / "acc1" / "runtime"
        acc.mkdir(parents=True, exist_ok=True)
        self.db_path = acc / "runtime.sqlite"


class TestAutoClawFidelity(AutoClawTestBase):
    """request 去重 / answer 去重 / reasoning note / tool_call note / 时区。"""

    ROWS = [
        # 用户轮次 1：同一文本被 3 次 run 重复记录（工具循环），中间无 answer
        ("r1", "request", {"text": "帮我分析", "effectivePrompt": "帮我分析"},
         "2026-09-28T05:48:10.951Z"),
        ("r1", "reasoning", {"text": "The user asks about analysis."},
         "2026-09-28T05:48:12.000Z"),
        ("r1", "reasoning", {"text": "用户想让我再分析一遍。"},
         "2026-09-28T05:48:12.500Z"),
        ("r1", "assistant", {"finishReason": "tool-calls",
                             "content": [
                                 {"type": "reasoning", "text": "The user asks about analysis."},
                                 {"type": "text", "text": "我先读取文件。"}],
                             "text": "我先读取文件。"},
         "2026-09-28T05:48:13.000Z"),
        ("r1", "tool_call", {"toolName": "read", "input": {"path": "a.md"}},
         "2026-09-28T05:48:14.000Z"),
        # run2：重复 request（同文本、无 answer 介入）→ 应去重
        ("r2", "request", {"text": "帮我分析"}, "2026-09-28T05:49:00.000Z"),
        ("r2", "assistant", {"finishReason": "stop", "text": "结论是 X。",
                             "content": [{"type": "text", "text": "结论是 X。"}]},
         "2026-09-28T05:49:10.000Z"),
        # answer 与 run2 的 stop assistant 精确重复 → 只留 answer
        ("r2", "answer", {"text": "结论是 X。"}, "2026-09-28T05:49:11.000Z"),
        # 用户真实重发同一文本（answer 已介入）→ 保留为第二条 user
        ("r3", "request", {"text": "帮我分析"}, "2026-09-28T05:50:00.000Z"),
        ("r3", "reasoning", {"text": "The user asks about analysis."},
         "2026-09-28T05:50:02.000Z"),
        ("r3", "reasoning", {"text": "用户想让我再分析一遍。"},
         "2026-09-28T05:50:03.000Z"),
        ("r3", "answer", {"text": "第二轮结论。"}, "2026-09-28T05:50:10.000Z"),
    ]

    def test_mapping(self):
        _mk_db(self.db_path, self.ROWS)
        ad = AutoClawAdapter(search_bases=[self.base])
        self.assertEqual(ad.detect().status, "OK")
        rec = ad.load_session("s1")
        roles = [m.role for m in rec.messages]
        # user x2（重复 1 条被去重）+ assistant(旁白) + note(tool_call)
        # + assistant(结论 X, answer 去重后留 1 条) + user + note(reasoning 中文版)
        # + assistant(第二轮)
        self.assertEqual(roles.count("user"), 2)
        self.assertEqual(roles.count("assistant"), 3)
        # 思考不进正文
        for m in rec.messages:
            if m.role == "assistant":
                self.assertNotIn("The user asks", m.text)
        # tool_call → note
        tool_notes = [m for m in rec.messages
                      if m.role == "note" and m.text.startswith("[tool_call]")]
        self.assertEqual(len(tool_notes), 1)
        self.assertIn("read", tool_notes[0].text)
        # 中英双语思考（同 run）只留中文版；全库任何 note 都不含英文思考
        reason_notes = [m for m in rec.messages
                        if m.role == "note" and m.text.startswith("[reasoning]")]
        self.assertEqual(len(reason_notes), 2)  # r1 中文版 + r3 中文版
        self.assertNotIn("The user asks",
                         "\n".join(m.text for m in reason_notes))
        # answer 与重复 assistant 去重计数
        self.assertGreaterEqual(rec.extra["dedup"]["request_dup"], 1)
        self.assertEqual(rec.extra["dedup"]["answer_dup_assistant"], 1)
        # 时间戳已转本地（UTC 05:48 → 本地 13:48）
        first_user = next(m for m in rec.messages if m.role == "user")
        self.assertEqual(first_user.timestamp, "2026-09-28 13:48:10")
        # 纲要时间同样转本地
        items = ad.list_sessions()
        self.assertEqual(items[0]["created_at"], "2026-09-28 13:48:10")


class TestAutoClawSchemaGuard(AutoClawTestBase):
    def test_missing_column_stubs(self):
        broken = ("CREATE TABLE work_sessions (session_id TEXT, title TEXT);"
                  "CREATE TABLE neutral_session_entries ("
                  "session_id TEXT, entry_type TEXT, payload TEXT);")
        _mk_db(self.db_path, [], schema=broken, insert_session=False)
        ad = AutoClawAdapter(search_bases=[self.base])
        rep = ad.detect()
        self.assertEqual(rep.status, "STUB")
        self.assertIn("缺少必需列", rep.detail)
        # load 也不应解析消息，而是 lossy 空消息 + 警告
        con = sqlite3.connect(self.db_path)
        con.execute("INSERT INTO work_sessions VALUES ('s1','x')")
        con.commit(); con.close()
        rec = ad.load_session("s1")
        self.assertEqual(rec.messages, [])
        self.assertTrue(rec.extra["lossy"])

    def test_dropped_table_stubs(self):
        _mk_db(self.db_path, [], schema="CREATE TABLE work_sessions (x TEXT);",
               insert_session=False)
        ad = AutoClawAdapter(search_bases=[self.base])
        self.assertEqual(ad.detect().status, "STUB")


# ---------- VS Code workspace chatSessions 补丁日志 ----------

class TestWsPatches(unittest.TestCase):
    def test_replay_and_extract(self):
        lines = [
            json.dumps({"kind": 0, "v": {"sessionId": "s", "requests": [],
                                         "inputState": {}}},
                       ensure_ascii=False),
            # 追加 request 对象（kind=2，v 为元素列表）
            json.dumps({"kind": 2, "k": ["requests"], "v": [
                {"timestamp": 1789450938062,
                 "message": {"text": "请检查语法"},
                 "response": []}]}, ensure_ascii=False),
            # 设值：路径含列表下标与字典键
            json.dumps({"kind": 1, "k": ["requests", 0, "completionTokens"],
                        "v": 6314}, ensure_ascii=False),
            # 追加回复正文块
            json.dumps({"kind": 2, "k": ["requests", 0, "response"], "v": [
                {"kind": None, "value": "语法没问题。"}]}, ensure_ascii=False),
            json.dumps({"kind": 2, "k": ["requests", 0, "response"], "v": [
                {"kind": "thinking", "value": "检查 description 字段。"},
                {"kind": "toolInvocationSerialized",
                 "invocationMessage": {"value": "Reading SKILL.md"}}]},
                ensure_ascii=False),
        ]
        state, applied, failed = _replay_patches(lines)
        self.assertEqual(failed, 0)
        self.assertEqual(applied, len(lines))
        self.assertEqual(state["requests"][0]["completionTokens"], 6314)
        msgs, warns = _ws_extract(state)
        roles = [(m.role, m.text) for m in msgs]
        self.assertEqual([t for r, t in roles if r == "user"], ["请检查语法"])
        self.assertEqual([t for r, t in roles if r == "assistant"],
                         ["语法没问题。"])
        kinds = [t.split("]")[0][1:] for r, t in roles if r == "note"]
        self.assertEqual(kinds, ["reasoning", "tool_call"])
        self.assertEqual(warns, [])

    def test_broken_lines_counted(self):
        lines = ["not json", json.dumps({"kind": 1, "k": ["a"], "v": 1})]
        state, applied, failed = _replay_patches(lines)
        self.assertIsNone(state)  # 无快照行
        self.assertEqual(failed, 2)


# ---------- ChatGPT 官方导出 ----------

def _gpt_conv() -> dict:
    return {
        "conversation_id": "g1", "title": "React 调试",
        "create_time": 1700000000.0, "update_time": 1700003600.0,
        "current_node": "n3",
        "mapping": {
            "root": {"id": "root", "message": None, "parent": None,
                     "children": ["n1"]},
            "n1": {"id": "n1", "parent": "root", "children": ["n2"],
                   "message": {"author": {"role": "user"},
                               "content": {"content_type": "text",
                                           "parts": ["为什么循环不工作?"]},
                               "create_time": 1700000001.0, "metadata": {}}},
            "n2_old": {"id": "n2_old", "parent": "n1", "children": [],
                       "message": {"author": {"role": "assistant"},
                                   "content": {"content_type": "text",
                                               "parts": ["废弃分支"]},
                                   "create_time": 1700000050.0,
                                   "metadata": {}}},
            "n2": {"id": "n2", "parent": "n1", "children": ["n3"],
                   "message": {"author": {"role": "assistant"},
                               "content": {"content_type": "text",
                                           "parts": ["问题出在闭包变量。"]},
                               "create_time": 1700000051.0, "metadata": {}}},
            "n2t": {"id": "n2t", "parent": "n2", "children": [],
                    "message": {"author": {"role": "tool"},
                                "content": {"content_type": "code",
                                            "text": "print(df)"},
                                "create_time": 1700000060.0, "metadata": {}}},
            "n3": {"id": "n3", "parent": "n2t", "children": [],
                   "message": {"author": {"role": "user"},
                               "content": {"content_type": "multimodal_text",
                                           "parts": ["看这段",
                                                     {"image_pointer": 1}]},
                               "create_time": 1700000100.0,
                               "metadata": {
                                   "is_visually_hidden_from_conversation":
                                       True}}},
        },
    }


class TestChatGptExport(unittest.TestCase):
    def _adapter(self, data) -> ChatGptExportAdapter:
        TMP.mkdir(parents=True, exist_ok=True)
        f = TMP / f"gpt_{id(self):x}.json"
        f.write_text(json.dumps(data, ensure_ascii=False),
                     encoding="utf-8", newline="\n")
        self.addCleanup(_try_unlink, f)
        return ChatGptExportAdapter(paths=[f])

    def test_branch_walk_and_filters(self):
        ad = self._adapter([_gpt_conv()])
        self.assertEqual(ad.detect().status, "OK")
        rec = ad.load_session("g1")
        roles = [m.role for m in rec.messages]
        # 隐藏节点(n3)跳过 → user, assistant, note(tool)
        self.assertEqual(roles, ["user", "assistant", "note"])
        joined = "\n".join(m.text for m in rec.messages)
        self.assertIn("为什么循环不工作?", joined)
        self.assertIn("闭包", joined)
        self.assertNotIn("废弃分支", joined)
        self.assertIn("print(df)", rec.messages[2].text)

    def test_rejects_unknown_top_level(self):
        ad = self._adapter({"chats": []})
        self.assertEqual(ad.detect().status, "STUB")

    def test_missing_mapping_is_lossy(self):
        ad = self._adapter([{"conversation_id": "g2", "title": "x"}])
        self.assertEqual(ad.detect().status, "OK")
        rec = ad.load_session("g2")
        self.assertEqual(rec.messages, [])
        self.assertTrue(rec.extra["lossy"])


# ---------- Claude 官方导出 ----------

class TestClaudeExport(unittest.TestCase):
    def _adapter(self, data) -> ClaudeExportAdapter:
        TMP.mkdir(parents=True, exist_ok=True)
        f = TMP / f"cla_{id(self):x}.json"
        f.write_text(json.dumps(data, ensure_ascii=False),
                     encoding="utf-8", newline="\n")
        self.addCleanup(_try_unlink, f)
        return ClaudeExportAdapter(paths=[f])

    def test_linear_messages(self):
        conv = {
            "uuid": "c1", "name": "SQL 优化", "model": "claude-sonnet-4-6",
            "created_at": "2026-02-14T10:04:22.000000+00:00",
            "chat_messages": [
                {"uuid": "m1", "sender": "human", "text": "帮我优化这条查询",
                 "content": [],
                 "created_at": "2026-02-14T10:04:22.000000+00:00"},
                {"uuid": "m2", "sender": "assistant", "text": "",
                 "content": [{"type": "text", "text": "加索引即可。"}],
                 "created_at": "2026-02-14T10:04:30.000000+00:00"},
                {"uuid": "m3", "sender": "weird", "text": "x", "content": []},
            ],
        }
        ad = self._adapter([conv])
        self.assertEqual(ad.detect().status, "OK")
        rec = ad.load_session("c1")
        self.assertEqual([m.role for m in rec.messages], ["user", "assistant"])
        self.assertEqual(rec.messages[1].text, "加索引即可。")
        self.assertIn("未核验 sender", " ".join(rec.extra["warnings"]))
        # ISO UTC → 本地
        self.assertRegex(rec.messages[0].timestamp or "",
                         r"2026-02-14 \d{2}:\d{2}:\d{2}")

    def test_conversations_wrapper(self):
        conv = {"uuid": "c2", "name": "n", "chat_messages": [
            {"sender": "human", "text": "hi", "content": []}]}
        ad = self._adapter({"conversations": [conv]})
        self.assertEqual(ad.detect().session_count, 1)

    def test_rejects_unknown_top_level(self):
        ad = self._adapter({"chats": []})
        self.assertEqual(ad.detect().status, "STUB")


# ---------- 插件注册 ----------

class TestPluginRegistry(unittest.TestCase):
    def test_new_ids_registered(self):
        from harvester.adapters import PLUGIN_IDS, _PARAM_MAP
        for pid in ("chatgpt-export", "claude-export"):
            self.assertIn(pid, PLUGIN_IDS)
            self.assertIn(pid, _PARAM_MAP)

    def test_build_adapters_with_plugin(self):
        from harvester.adapters import build_adapters
        ads = build_adapters({"plugins": [
            {"id": "chatgpt-export", "paths": ["x.json"]}]})
        ids = [a.id for a in ads]
        self.assertIn("chatgpt-export", ids)
        inst = next(a for a in ads if a.id == "chatgpt-export")
        self.assertEqual(inst._candidates, [Path("x.json")])

    def test_unknown_plugin_id_raises(self):
        from harvester.adapters import build_adapters
        with self.assertRaises(ValueError):
            build_adapters({"plugins": [{"id": "nope", "paths": []}]})


if __name__ == "__main__":
    unittest.main()
