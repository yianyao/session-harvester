# -*- coding: utf-8 -*-
"""v0.51 S5 测试：load_sources 缺失/损坏分流。

缺失 → 空表回退 + stderr 提示行（不算错误）；损坏（坏 JSON / 编码 /
顶层非对象）→ ValueError fail loud——配置坏掉不能静默当"没配置"。
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harvester.adapters import load_sources  # noqa: E402


class LoadSourcesSplitTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def _write(self, body: str) -> Path:
        p = self.tmp / "sources.json"
        p.write_text(body, encoding="utf-8")
        return p

    def test_missing_falls_back_with_hint(self):
        p = self.tmp / "absent.json"
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(load_sources(p), {})
        self.assertIn("默认路径回退", err.getvalue())
        self.assertIn(str(p), err.getvalue())

    def test_corrupt_json_fails_loud(self):
        p = self._write("{not json")
        with self.assertRaises(ValueError) as cm:
            load_sources(p)
        self.assertIn("损坏", str(cm.exception))

    def test_corrupt_encoding_fails_loud(self):
        p = self.tmp / "sources.json"
        p.write_bytes(b'{"a": "\xff\xfe"}')
        with self.assertRaises(ValueError):
            load_sources(p)

    def test_top_level_array_fails_loud(self):
        p = self._write("[]")
        with self.assertRaises(ValueError) as cm:
            load_sources(p)
        self.assertIn("顶层应为对象", str(cm.exception))

    def test_valid_dict_passthrough(self):
        p = self._write(json.dumps(
            {"dsh": {"paths": ["C:/x"]}}, ensure_ascii=False))
        self.assertEqual(load_sources(p), {"dsh": {"paths": ["C:/x"]}})


if __name__ == "__main__":
    unittest.main()
