# -*- coding: utf-8 -*-
"""v0.6 测试：WorkBuddy transcript adapter / steps 表 / tracestats / DSH。"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harvester.adapters.dsh import DshAdapter, ZstdUnavailable, _split_frames  # noqa: E402
from harvester.adapters.workbuddy_transcript import (  # noqa: E402
    WorkBuddyTranscriptAdapter, _extract_user_text)
from harvester.indexing import index_session, index_exports  # noqa: E402
from harvester.models import Message, SessionRecord  # noqa: E402
from harvester.toolstats import (collect_stats_from_db, render_flow,  # noqa: E402
                                 render_report)
from harvester.tracestats import collect, render_report as rrep  # noqa: E402

_TMP = Path(__file__).parent / "_tmp"


# ---- 提取与映射 -----------------------------------------------------------

class TestExtractUserText(unittest.TestCase):
    def test_query_with_context(self):
        raw = ('<system-reminder data-role="user-context">\nctx\n'
               '</system-reminder>\n<user_query>把我知识库转换为MD</user_query>')
        query, ctx = _extract_user_text(raw)
        self.assertEqual(query, "把我知识库转换为MD")
        self.assertIn("ctx", ctx)
        self.assertNotIn("user_query", ctx)

    def test_plain_text(self):
        q, ctx = _extract_user_text("纯文本消息")
        self.assertEqual(q, "纯文本消息")
        self.assertIsNone(ctx)


class TestWorkBuddyTranscript(unittest.TestCase):
    def setUp(self):
        _TMP.mkdir(parents=True, exist_ok=True)
        self.root = _TMP / "wb_proj"
        ws = self.root / "c-Users-test-Workspace"
        (ws / "subagents").mkdir(parents=True, exist_ok=True)
        # 主会话：reminder 包裹的 user + assistant + reasoning + 工具对
        lines = [
            {"type": "session-meta", "id": "x", "sessionId": "s1"},
            {"type": "message", "id": "m1", "timestamp": 1791000000000,
             "role": "user",
             "content": [{"type": "input_text", "text":
                          "<system-reminder>ctx</system-reminder>"
                          "<user_query>你好，帮我查一下</user_query>"}]},
            {"type": "reasoning", "id": "r1", "timestamp": 1791000001000,
             "content": [{"type": "reasoning_text", "text": "先想一下"}]},
            {"type": "function_call", "id": "f1", "timestamp": 1791000002000,
             "callId": "c1", "name": "read",
             "arguments": '{"file_path": "a.py"}'},
            {"type": "function_call_result", "id": "fr1",
             "timestamp": 1791000003000, "callId": "c1", "name": "read",
             "status": "completed",
             "output": {"type": "text", "text": "文件内容"}},
            {"type": "function_call_result", "id": "fr2",
             "timestamp": 1791000003500, "callId": "c2", "name": "edit",
             "status": "completed",
             "output": {"type": "text", "text": "Error: old_string 不匹配"}},
            {"type": "message", "id": "m2", "timestamp": 1791000004000,
             "role": "assistant",
             "providerData": {"model": "glm-5.2"},
             "content": [{"type": "output_text", "text": "帮你查好了"}]},
            {"type": "ai-title", "id": "t1", "timestamp": 1791000004500,
             "aiTitle": "测试轨迹会话"},
            {"type": "file-history-snapshot", "id": "sn1",
             "timestamp": 1791000004600, "snapshot": {}},
        ]
        self.f_main = ws / "sess-1.jsonl"
        self.f_main.write_text(
            "\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n",
            encoding="utf-8", newline="\n")
        # 子代理
        (ws / "subagents" / "agent-abc.jsonl").write_text(
            json.dumps({"type": "message", "id": "s1", "role": "user",
                        "timestamp": 1791000005000,
                        "content": [{"type": "input_text",
                                     "text": "子任务"}]}) + "\n",
            encoding="utf-8", newline="\n")
        self.ad = WorkBuddyTranscriptAdapter(projects_root=self.root)

    def test_detect_and_list(self):
        rep = self.ad.detect()
        self.assertEqual(rep.status, "OK")
        self.assertEqual(rep.session_count, 2)
        items = self.ad.list_sessions()
        main = next(x for x in items if x["title"] == "测试轨迹会话")
        self.assertEqual(main["message_count"], 2)
        self.assertEqual(main["preview"], "你好，帮我查一下")
        sub = next(x for x in items if x["title"].startswith("[subagent]"))
        self.assertIn("subagents/", sub["session_id"])

    def test_load_mapping(self):
        items = self.ad.list_sessions()
        main = next(x for x in items if x["title"] == "测试轨迹会话")
        rec = self.ad.load_session(main["session_id"])
        roles = [(m.role, m.text) for m in rec.messages]
        kinds = [t.split("]")[0] + "]" for r, t in roles if r == "note"]
        self.assertEqual(kinds, ["[context]", "[reasoning]", "[tool_call]",
                                 "[tool_result]", "[tool_result]"])
        users = [t for r, t in roles if r == "user"]
        self.assertEqual(users, ["你好，帮我查一下"])
        asst = [t for r, t in roles if r == "assistant"]
        self.assertEqual(asst, ["帮你查好了"])
        # 工具 note 携带结构化 raw（steps 表数据源）
        tr = [m for m in rec.messages
              if m.role == "note" and m.raw and m.raw.get("kind") == "tool_result"]
        self.assertEqual(len(tr), 2)
        err = next(m for m in tr if m.raw["status"] == "error")
        self.assertEqual(err.raw["tool"], "edit")
        self.assertIn("old_string", err.raw["error"])

    def test_load_missing_raises(self):
        with self.assertRaises(KeyError):
            self.ad.load_session("no/such.jsonl")


# ---- steps 表 -------------------------------------------------------------

def _mk_con(db: Path, msgs):
    rec = SessionRecord(source="test", session_id="s1", title="t",
                        messages=msgs)
    con = sqlite3.connect(str(db))
    from harvester.indexing import SCHEMA
    con.executescript(SCHEMA)
    n = index_session(con, rec, include_notes=True)
    con.commit()
    con.close()
    return n


class TestStepsTable(unittest.TestCase):
    def setUp(self):
        _TMP.mkdir(parents=True, exist_ok=True)
        self.db = _TMP / "steps_test.db"
        if self.db.exists():
            self.db.unlink()

    def test_normalized_raw(self):
        msgs = [
            Message("user", "帮我"),
            Message("note", "[tool_call] read: a.py",
                    raw={"kind": "tool_call", "tool": "read",
                         "detail": "a.py"}),
            Message("note", "[tool_result] read: success",
                    raw={"kind": "tool_result", "tool": "read",
                         "status": "success"}),
        ]
        _mk_con(self.db, msgs)
        con = sqlite3.connect(str(self.db))
        rows = con.execute(
            "SELECT tool, phase, status FROM steps ORDER BY seq").fetchall()
        con.close()
        self.assertEqual(rows, [("read", "call", None),
                                ("read", "result", "success")])

    def test_autoclaw_payload_raw(self):
        # 真实形态：adapter 在 payload 上盖 kind 章（autoclaw.py 实装），
        # data 内结构保持源端原样（toolName/status/error）。
        msgs = [
            Message("note", "[tool_result] ls: error tool_permission_revoked",
                    raw={"kind": "tool_result", "schemaVersion": 3,
                         "data": {"toolName": "ls", "status": "error",
                                  "error": {"code": "tool_permission_revoked",
                                            "retryable": True}}}),
        ]
        _mk_con(self.db, msgs)
        con = sqlite3.connect(str(self.db))
        rows = con.execute("SELECT tool, status, error FROM steps").fetchall()
        con.close()
        self.assertEqual(rows, [("ls", "error", "tool_permission_revoked")])


class TestToolStatsFromDb(unittest.TestCase):
    def setUp(self):
        _TMP.mkdir(parents=True, exist_ok=True)
        self.db = _TMP / "tstats_test.db"
        if self.db.exists():
            self.db.unlink()
        # 会话1：error 后重试（retried=1）；会话2：error 后放弃（given_up=1）
        msgs = [
            Message("note", "[tool_call] browser: x",
                    raw={"kind": "tool_call", "tool": "browser"}),
            Message("note", "[tool_result] browser: error obscured",
                    raw={"kind": "tool_result", "tool": "browser",
                         "status": "error", "error": "browser_element_obscured"}),
            Message("note", "[tool_call] browser: y",
                    raw={"kind": "tool_call", "tool": "browser"}),
            Message("note", "[tool_result] browser: success",
                    raw={"kind": "tool_result", "tool": "browser",
                         "status": "success"}),
            Message("user", "下一个问题"),
            Message("note", "[tool_call] ls: z",
                    raw={"kind": "tool_call", "tool": "ls"}),
            Message("note", "[tool_result] ls: error denied",
                    raw={"kind": "tool_result", "tool": "ls",
                         "status": "error", "error": "tool_permission_revoked"}),
        ]
        rec = SessionRecord(source="test", session_id="s1", title="t",
                            messages=msgs)
        con = sqlite3.connect(str(self.db))
        from harvester.indexing import SCHEMA
        con.executescript(SCHEMA)
        index_session(con, rec, include_notes=True)
        con.commit()
        con.close()

    def test_flow(self):
        stats, flow = collect_stats_from_db(self.db)
        self.assertEqual(stats["browser"].calls, 2)
        self.assertEqual(stats["browser"].error, 1)   # .error=计数
        self.assertEqual(stats["ls"].error, 1)
        self.assertEqual(stats["browser"].errors["browser_element_obscured"], 1)
        self.assertEqual(flow["errors"], 2)
        self.assertEqual(flow["retried"], 1)
        self.assertEqual(flow["given_up"], 1)
        report = render_report(stats, flow=flow)
        self.assertIn("重试 1", report)
        self.assertIn("放弃 1", report)

    def test_render_flow_empty(self):
        self.assertEqual(render_flow({"errors": 0, "retried": 0,
                                      "given_up": 0}), [])


class TestExportRawRoundtrip(unittest.TestCase):
    def test_index_exports_builds_steps(self):
        _TMP.mkdir(parents=True, exist_ok=True)
        exp = _TMP / "exp_roundtrip"
        if exp.exists():
            shutil.rmtree(exp)
        d = exp / "agent" / "2026-10"
        d.mkdir(parents=True)
        data = {
            "source": "test", "session_id": "rt1", "title": "往返",
            "created_at": "2026-10-06 08:00:00",
            "updated_at": "2026-10-06 08:01:00",
            "messages": [
                {"role": "note", "text": "[tool_call] read: x",
                 "timestamp": None,
                 "raw": {"kind": "tool_call", "tool": "read"}},
                {"role": "user", "text": "hi", "timestamp": None},
            ],
        }
        (d / "s.json").write_text(json.dumps(data, ensure_ascii=False),
                                  encoding="utf-8", newline="\n")
        db = _TMP / "roundtrip.db"
        index_exports(exp, db)
        con = sqlite3.connect(str(db))
        rows = con.execute("SELECT tool, phase FROM steps").fetchall()
        con.close()
        self.assertEqual(rows, [("read", "call")])


# ---- tracestats -----------------------------------------------------------

class TestTraceStats(unittest.TestCase):
    def setUp(self):
        _TMP.mkdir(parents=True, exist_ok=True)
        self.root = _TMP / "traces"
        pid = self.root / "1234"
        pid.mkdir(parents=True, exist_ok=True)
        spans = [
            {"name": "Read", "type": "function", "status": "ok",
             "duration": 30, "toolName": "read"},
            {"name": "Read", "type": "function", "status": "error",
             "duration": 100, "toolName": "read", "error": {"code": "x"}},
            {"name": "Bash", "type": "function", "status": "cancelled",
             "duration": 5000, "toolName": "Bash"},
            {"name": "mcp_tools", "type": "custom", "status": "ok",
             "duration": 10},
            {"name": "generation", "type": "generation", "status": "ok",
             "duration": 2000},
        ]
        (pid / "trace_a.json").write_text(
            json.dumps({"trace": {"traceId": "t1"}, "spans": spans}),
            encoding="utf-8", newline="\n")

    def test_collect(self):
        stats, gen, summary = collect(self.root)
        self.assertEqual(stats["read"].calls, 2)
        self.assertEqual(stats["read"].errors, 1)
        self.assertEqual(stats["bash".capitalize()].cancelled, 1)
        self.assertNotIn("mcp_tools", stats)  # 伞 span 不计入工具
        self.assertEqual(gen.calls, 1)
        self.assertEqual(gen.total_ms, 2000)
        self.assertEqual(summary["files"], 1)
        report = rrep(stats, gen, summary)
        self.assertIn("OTel Trace 工具统计", report)
        self.assertIn("用户取消: 1", report)
        self.assertIn("p95(ms)", report)


# ---- DSH ------------------------------------------------------------------

_NODE_SCRIPT = (
    "const fs=require('fs'),z=require('zlib');"
    "const [src,dst]=process.argv.slice(2);"
    "const lines=fs.readFileSync(src,'utf8').trim().split('\\n');"
    "const half=Math.ceil(lines.length/2);"
    "const f1=z.zstdCompressSync(Buffer.from(lines.slice(0,half).join('\\n')+'\\n'));"
    "const f2=z.zstdCompressSync(Buffer.from(lines.slice(half).join('\\n')+'\\n'));"
    "fs.writeFileSync(dst,Buffer.concat([f1,f2]));"
)


def _node_available() -> bool:
    return shutil.which("node") is not None


@unittest.skipUnless(_node_available(), "需要 node 生成 zstd 测试样本")
class TestDsh(unittest.TestCase):
    def setUp(self):
        _TMP.mkdir(parents=True, exist_ok=True)
        self.root = _TMP / "dsh_sessions"
        sess = self.root / "--C-test-ws--" / "sess-1"
        sess.mkdir(parents=True, exist_ok=True)
        lines = [
            {"type": "session", "version": 4, "id": "sess-1",
             "createdAt": 1791300000000, "cwd": "C:/ws",
             "origin": "user", "delegationDepth": 0},
            {"type": "approval/policy", "seq": 1, "time": 1791300000001,
             "data": {"policy": "never", "source": "user"}},
            {"type": "session/title", "seq": 2, "time": 1791300000002,
             "data": {"title": "DSH 测试会话"}},
            {"type": "user/message", "seq": 3, "time": 1791300000003,
             "data": {"content": [{"type": "text", "text": "跑个测试"}]}},
            {"type": "assistant/message", "seq": 4, "time": 1791300000004,
             "data": {"message": {"role": "assistant", "content": [
                 {"type": "reasoning", "text": "想想"},
                 {"type": "text", "text": "跑好了"}]}}},
            {"type": "tool/call", "seq": 5, "time": 1791300000005,
             "data": {"callId": "call_1", "name": "bash",
                      "arguments": "{\"cmd\":\"ls\"}"}},
            {"type": "tool/result", "seq": 6, "time": 1791300000006,
             "data": {"message": {"role": "tool",
                                  "toolCallId": "call_1",
                                  "content": [{"type": "text",
                                               "text": "ok"}]}}},
            {"type": "step/start", "seq": 7, "time": 1791300000007,
             "data": {"turn": 1, "step": 1}},
        ]
        self.src = _TMP / "dsh_src.jsonl"
        self.src.write_text(
            "\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n",
            encoding="utf-8", newline="\n")
        zst = sess / "session.v4.jsonl.zstd"
        script = _TMP / "mkzstd.js"
        script.write_text(_NODE_SCRIPT, encoding="utf-8", newline="\n")
        r = subprocess.run([shutil.which("node"), str(script),
                            str(self.src), str(zst)],
                           capture_output=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        self.ad = DshAdapter(sessions_root=self.root)

    def test_split_frames(self):
        buf = self.root / "--C-test-ws--" / "sess-1" / "session.v4.jsonl.zstd"
        frames = _split_frames(buf.read_bytes())
        self.assertEqual(len(frames), 2)

    def test_roundtrip(self):
        rep = self.ad.detect()
        self.assertEqual(rep.status, "OK")
        items = self.ad.list_sessions()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "DSH 测试会话")
        rec = self.ad.load_session(items[0]["session_id"])
        roles = [(m.role, m.text) for m in rec.messages]
        users = [t for r, t in roles if r == "user"]
        self.assertEqual(users, ["跑个测试"])
        asst = [t for r, t in roles if r == "assistant"]
        self.assertEqual(asst, ["跑好了"])
        notes = [t for r, t in roles if r == "note"]
        self.assertTrue(any(t.startswith("[policy]") for t in notes))
        self.assertTrue(any(t.startswith("[reasoning]") for t in notes))
        self.assertIn("[tool_call] bash:", notes[notes.index(
            next(t for t in notes if t.startswith("[tool_call]")))])
        tr = next(t for t in notes if t.startswith("[tool_result]"))
        self.assertIn("bash: completed", tr)
        self.assertEqual(rec.extra["dsh_meta"]["origin"], "user")

    def test_detect_stub_without_decompressor(self):
        import harvester.adapters.dsh as dshmod
        orig = dshmod.zstd_decompress
        dshmod.zstd_decompress = _raise_unavail
        try:
            self.ad._zstd_ok = None
            # 探测函数不被 monkeypatch 影响：直接置 False
            self.ad._zstd_ok = False
            rep = self.ad.detect()
            self.assertEqual(rep.status, "STUB")
        finally:
            dshmod.zstd_decompress = orig


def _raise_unavail(path):
    raise ZstdUnavailable("no decompressor")


if __name__ == "__main__":
    unittest.main()
