# -*- coding: utf-8 -*-
"""v0.51 S6 测试：corpus_from_* 逐会话流式落盘。

审计 c2 内存峰值项：原实现把全部会话攒进 sections 大列表再一次写盘
（真库 410MB 级会话一次进内存）。改为 body 逐段写 .part 临时文件、
流完才经 header_fn 取头部计数、流式拷贝组装正式文件——输出内容与
原格式逐字节一致（钉住），且 .part 即用即删。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harvester.distill import corpus_from_exports  # noqa: E402


class CorpusFromExportsStreamTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.out = self.tmp / "corpus.md"

    def _export(self, name: str, payload: dict | str) -> Path:
        d = self.tmp / "exports"
        d.mkdir(exist_ok=True)
        p = d / name
        p.write_text(payload if isinstance(payload, str)
                     else json.dumps(payload, ensure_ascii=False),
                     encoding="utf-8")
        return p

    def test_streaming_output_shape(self):
        self._export("a.json", {
            "source": "test", "session_id": "s1", "title": "会话一",
            "created_at": "2026-10-16 10:00:00",
            "messages": [
                {"role": "user", "text": "问题", "timestamp": "2026-10-16 10:00:00"},
                {"role": "assistant", "text": "回答"},
            ]})
        self._export("b.json", {
            "source": "test", "session_id": "s2", "title": "会话二",
            "created_at": "2026-10-15 09:00:00",
            "messages": [{"role": "note", "text": "不该出现"}]})
        rep = corpus_from_exports(self.tmp / "exports", self.out)
        self.assertEqual(rep["files"], 2)
        self.assertTrue(rep["chars"] > 0)
        body = self.out.read_text(encoding="utf-8")
        # 头部计数必须是流后真值（不是流前 0）
        self.assertIn("来源文件数：2", body)
        self.assertNotIn("来源文件数：0", body)
        # 段序 = 文件序；过滤 note；两段之间以 \n 连接（原 join 语义）
        self.assertLess(body.index("会话一"), body.index("会话二"))
        self.assertNotIn("不该出现", body)
        self.assertIn("## [user] · 2026-10-16 10:00:00", body)

    def test_warn_and_part_cleanup(self):
        self._export("bad.json", "{nope")
        self._export("ok.json", {
            "source": "test", "session_id": "s3", "title": "会话三",
            "created_at": "2026-10-14 08:00:00",
            "messages": [{"role": "user", "text": "hi"}]})
        rep = corpus_from_exports(self.tmp / "exports", self.out)
        body = self.out.read_text(encoding="utf-8")
        self.assertIn("解析失败", body)          # WARN 段保留（诚实失败）
        self.assertEqual(rep["files"], 1)
        self.assertFalse((self.tmp / "corpus.md.part").exists(),
                         ".part 临时文件必须即用即删")

    def test_manifest_skipped(self):
        self._export("export_manifest.json", {"k": "v"})
        rep = corpus_from_exports(self.tmp / "exports", self.out)
        self.assertEqual(rep["files"], 0)
        self.assertIn("来源文件数：0",
                      self.out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
