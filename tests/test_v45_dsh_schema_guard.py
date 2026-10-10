# -*- coding: utf-8 -*-
"""v0.45 DSH schema 守卫测试（SOP C3）。

背景：本 adapter 依赖的是**私有 transcript 形态**（`session.v4.jsonl.zstd`），
此前上游改版时的失败方式是**静默产出半成品**（消息全丢但纲要/统计照跑）。
这里照 autoclaw 的守卫先例钉住两条判据 + 一条可追溯指纹：

- **声明版本**：文件名 `session.v<N>.` 不在已知集合 → 守卫不过；
- **消费字段形态**：`REQUIRED_SHAPE` 里我们真正读的字段路径缺失 → 守卫不过；
- 守卫不过时：`detect` → **STUB**（带 hints），`load_session` → **lossy 且
  messages 为空**（不是"半解析"），`list_sessions` 的条目带 `schema_ok=False`。

判据都能说清何时会红：把字段改名、把文件名版本改掉、或让守卫"恒过"，
对应断言即红（另有元测试证明 `shape_problems` 对改名输入确实报错）。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import harvester.adapters.dsh as dshmod
from harvester.adapters.dsh import (DshAdapter, declared_version,
                                    schema_fingerprint, shape_problems)

GOOD_LINES = [
    {"type": "session", "time": 1791300000000,
     "data": {"origin": "user", "delegationDepth": 0, "cwd": "C:/x"}},
    {"type": "session/title", "time": 1791300000001,
     "data": {"title": "DSH 测试会话"}},
    {"type": "user/message", "time": 1791300000002,
     "data": {"content": [{"type": "text", "text": "跑个测试"}]}},
    {"type": "tool/call", "time": 1791300000003,
     "data": {"name": "bash", "callId": "c1", "arguments": "ls"}},
    {"type": "tool/result", "time": 1791300000004,
     "data": {"message": {"toolCallId": "c1",
                          "content": [{"type": "text", "text": "ok"}]}}},
    {"type": "assistant/message", "time": 1791300000005,
     "data": {"message": {"content": [{"type": "text", "text": "跑好了"}]}}},
]


class TestSchemaGuard(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "sessions"
        self.sess = self.root / "--ws--" / "sess-1"
        self.sess.mkdir(parents=True)
        self.ad = DshAdapter(sessions_root=self.root)
        self.ad._zstd_ok = True          # 跳过解压能力探测（本测试不打桩解压）
        # **必须还原**：`zstd_decompress` 是**模块级**函数，打桩会泄漏到同进程内
        # 之后运行的所有测试（真实后果：test_v06 的 `[policy]` 断言偶发失败——
        # flake 猎人在第 2~5 轮抓到 4/5 次，根因就是本文件首版没还原）。
        self._orig_dec = dshmod.zstd_decompress
        self.addCleanup(self._restore)

    def _restore(self):
        dshmod.zstd_decompress = self._orig_dec

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def _write(self, lines, name="session.v4.jsonl.zstd"):
        f = self.sess / name
        f.write_bytes(b"")               # 内容由 zstd_decompress 打桩提供
        dshmod.zstd_decompress = lambda p: "\n".join(
            json.dumps(x, ensure_ascii=False) for x in lines)
        return f

    def test_declared_version_extraction(self):
        self.assertEqual(declared_version(Path("session.v4.jsonl.zstd")), "4")
        self.assertEqual(declared_version(Path("session.v12.jsonl.zstd")), "12")
        self.assertIsNone(declared_version(Path("weird.jsonl.zstd")))

    def test_clean_transcript_passes_guard(self):
        self._write(GOOD_LINES)
        rep = self.ad.detect()
        self.assertEqual(rep.status, "OK")
        self.assertTrue(rep.schema_ok)
        self.assertEqual(rep.schema_version, "4")
        self.assertTrue(rep.schema_fingerprint)
        rec = self.ad.load_session("--ws--/sess-1/session.v4.jsonl.zstd")
        self.assertFalse(rec.extra["lossy"])
        self.assertEqual(rec.extra["dsh_schema"]["ok"], True)
        self.assertTrue(any(m.text == "跑个测试" for m in rec.messages))

    def test_unknown_declared_version_degrades(self):
        """上游把文件名升到 v5：detect 必须 STUB，不许当 v4 硬解。"""
        self._write(GOOD_LINES, name="session.v5.jsonl.zstd")
        rep = self.ad.detect()
        self.assertEqual(rep.status, "STUB")
        self.assertFalse(rep.schema_ok)
        self.assertEqual(rep.schema_version, "5")
        self.assertTrue(any("REQUIRED_SHAPE" in h for h in rep.hints))

    def test_changed_shape_degrades_and_does_not_half_parse(self):
        """字段改名（content → blocks）：detect STUB；load_session 交付 lossy
        空消息记录，**不许**产出"看起来正常"的半成品。"""
        bad = [dict(x) for x in GOOD_LINES]
        bad[2] = {"type": "user/message", "time": 1791300000002,
                  "data": {"blocks": [{"type": "text", "text": "跑个测试"}]}}
        self._write(bad)
        rep = self.ad.detect()
        self.assertEqual(rep.status, "STUB")
        self.assertIn("user/message 缺字段 data.content", rep.detail)
        items = self.ad.list_sessions()
        self.assertEqual(len(items), 1)                 # 清单照列
        self.assertFalse(items[0]["schema_ok"])         # 但标明守卫没过
        rec = self.ad.load_session(items[0]["session_id"])
        self.assertTrue(rec.extra["lossy"])
        self.assertEqual(rec.messages, [])
        self.assertIn("schema 守卫未通过", rec.extra["warnings"][0])
        self.assertFalse(rec.extra["dsh_schema"]["ok"])

    def test_missing_version_in_filename_degrades(self):
        self._write(GOOD_LINES, name="session.jsonl.zstd")
        rep = self.ad.detect()
        self.assertEqual(rep.status, "STUB")
        self.assertIsNone(rep.schema_version)

    def test_shape_problems_can_fail(self):
        """元测试：守卫对合成输入必须报错（否则这道门恒过）。"""
        self.assertEqual(shape_problems(GOOD_LINES), [])
        renamed = [{"type": "tool/call", "data": {"toolName": "bash"}}]
        self.assertTrue(shape_problems(renamed))
        # 稀疏数据（类型没出现）**不算**问题——那是内容差异不是 schema 差异
        self.assertEqual(shape_problems([GOOD_LINES[0]]), [])

    def test_fingerprint_is_stable_for_same_shape_and_differs_on_change(self):
        fp1 = schema_fingerprint(GOOD_LINES)
        fp2 = schema_fingerprint([dict(x) for x in GOOD_LINES])
        self.assertEqual(fp1, fp2)
        changed = [dict(x) for x in GOOD_LINES]
        changed[3] = {"type": "tool/call", "data": {"name": "bash", "extra": 1}}
        self.assertNotEqual(fp1, schema_fingerprint(changed))


if __name__ == "__main__":
    unittest.main()
