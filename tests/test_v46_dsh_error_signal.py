# -*- coding: utf-8 -*-
"""v0.46 P1-3 测试：DSH 错误判据改用结构化 `data.message.isError`。

背景（SOP §6 P1-3，真机实测）：原判据
`is_err = text.lower().startswith("error")` 用的是**输出文本前缀**，而每条
tool/result 都带结构化 `data.message.isError`（8404/8404 条）。旧判据的
交叉表是"201 命中 / 0 漏判 / 1 误判"——**今天够用，但判据脆**：上游改一句
文案、或正文恰好以 error 开头/含 Error: 就会判反。

本文件钉两红两绿 + 一条归因：
- ① `Traceback` 开头但 isError=true → 旧判据漏，新判据抓到；
- ② isError=false 但正文含 `Error:` → 旧判据误判，新判据不误；
- ③ 没有 isError 字段（旧 frame）→ 文本前缀兜底仍然生效（不许回归）；
- ④ `data.error` 的 code/message 结构化落进 steps.error（比 400 字预览可读）。
"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import harvester.adapters.dsh as dshmod
from harvester.adapters.dsh import DshAdapter
from harvester.indexing import SCHEMA, index_session


def _lines(result_text: str, *, is_error=None, error_obj=None) -> list[dict]:
    """一条 tool/call + 一条工具结果（isError / data.error 可控）。"""
    msg: dict = {"toolCallId": "c1",
                 "content": [{"type": "text", "text": result_text}]}
    data: dict = {"message": msg}
    if is_error is not None:
        msg["isError"] = is_error
    if error_obj is not None:
        data["error"] = error_obj
    return [
        {"type": "session", "time": 1791300000000,
         "data": {"origin": "user", "delegationDepth": 0, "cwd": "C:/x"}},
        {"type": "tool/call", "time": 1791300000001,
         "data": {"name": "bash", "callId": "c1", "arguments": "ls"}},
        {"type": "tool/result", "time": 1791300000002, "data": data},
    ]


def _result_of(rec):
    return next(m for m in rec.messages
                if isinstance(m.raw, dict) and m.raw.get("kind") == "tool_result")


class ErrorSignalTest(unittest.TestCase):
    """"判据"层面的红绿（不落库，直接看 raw）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "sessions"
        self.sess = self.root / "--ws--" / "sess-1"
        self.sess.mkdir(parents=True)
        self.ad = DshAdapter(sessions_root=self.root)
        self.ad._zstd_ok = True
        # 模块级打桩**必须还原**（tests/test_v45_no_monkeypatch_leak.py 会查）
        self._orig_dec = dshmod.zstd_decompress
        self.addCleanup(self._restore)

    def _restore(self):
        dshmod.zstd_decompress = self._orig_dec

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def _load(self, lines) -> object:
        (self.sess / "session.v4.jsonl.zstd").write_bytes(b"")
        dshmod.zstd_decompress = lambda p: "\n".join(
            json.dumps(x, ensure_ascii=False) for x in lines)
        return self.ad.load_session("--ws--/sess-1/session.v4.jsonl.zstd")

    def test_traceback_with_iserror_true_is_caught(self):
        """① 旧判据漏：正文以 Traceback 开头但结构化判为错误。"""
        text = "Traceback (most recent call last): boom"
        self.assertFalse(text.lower().startswith("error"))   # 旧判据看不到
        raw = _result_of(self._load(_lines(text, is_error=True))).raw
        self.assertEqual(raw["status"], "error")
        self.assertEqual(raw["errorBy"], "isError")
        self.assertTrue(raw["error"])

    def test_error_looking_text_with_iserror_false_is_not_flagged(self):
        """② 旧判据误：正文含 'Error:' 但结构化判为成功。"""
        text = "编译输出含 Error: 但这是被测程序的正常回显"
        self.assertTrue(text.lower().startswith("error")
                        or "error:" in text.lower())
        raw = _result_of(self._load(_lines(text, is_error=False))).raw
        self.assertEqual(raw["status"], "completed")
        self.assertIsNone(raw["error"])
        self.assertEqual(raw["errorBy"], "isError")

    def test_missing_iserror_falls_back_to_text(self):
        """③ 旧 frame（无 isError）→ 文本兜底仍生效，不许回归。"""
        raw = _result_of(self._load(_lines("Error: permission denied"))).raw
        self.assertEqual(raw["status"], "error")
        self.assertEqual(raw["errorBy"], "text")
        self.assertIn("permission denied", raw["error"])

    def test_structured_error_object_wins_over_preview(self):
        """④ data.error 的 code/message 落库，比 400 字正文预览更结构化。"""
        raw = _result_of(self._load(_lines(
            "Error: 一大堆无关正文 " * 40, is_error=True,
            error_obj={"code": "E_PERMISSION", "message": "沙箱拒绝写入"}))).raw
        self.assertEqual(raw["status"], "error")
        self.assertEqual(raw["error"], "E_PERMISSION / 沙箱拒绝写入")


class ErrorSignalToDbTest(unittest.TestCase):
    """判据 → steps 表：分类依据（errstats 读 steps.error）不许丢。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "h.db"

    def tearDown(self):
        self.tmp.cleanup()

    def _steps(self, rec) -> list[tuple]:
        con = sqlite3.connect(str(self.db))
        try:
            con.executescript(SCHEMA)
            index_session(con, rec)
            con.commit()
            return con.execute(
                "SELECT tool, phase, status, error FROM steps ORDER BY seq"
            ).fetchall()
        finally:
            con.close()

    def test_structured_code_reaches_steps_error_column(self):
        from harvester.models import Message, SessionRecord
        rec = SessionRecord(
            source="dsh", session_id="s1", title="t",
            messages=[
                Message("note", "[tool_call] bash: ls", timestamp="1",
                        raw={"kind": "tool_call", "tool": "bash",
                             "detail": "ls"}),
                Message("note", "[tool_result] bash: error", timestamp="2",
                        raw={"kind": "tool_result", "tool": "bash",
                             "status": "error",
                             "error": "E_PERMISSION / 沙箱拒绝写入",
                             "errorBy": "isError", "callId": "c1"}),
            ])
        rows = self._steps(rec)
        self.assertEqual(rows[0][2], None)          # call 相无状态
        self.assertEqual(rows[1][2], "error")
        self.assertEqual(rows[1][3], "E_PERMISSION / 沙箱拒绝写入")


if __name__ == "__main__":
    unittest.main()
