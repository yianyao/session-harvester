# -*- coding: utf-8 -*-
"""v0.81 测试：`noise` 子命令（P5-⑧「登记零散」的 CLI 通道）。

依据：`docs/PROPOSAL-faster-data-access.md` §5-⑧（用户 2026-10-16 批准）。
钉的验收（每条都说明它在什么情况下会红）：

1. `noise add --apply` 写 sessions_noise、重复 add 幂等跳过 →
   把 register 换成 INSERT 非 OR IGNORE / 或漏去重 → 红；
2. **dry-run 不建表不写行** → 把 add 改成顺手 `ensure_noise_table` → 红；
3. `noise list` 空态诚实、非空态逐行列出 → 文案改动不影响断言（钉行为）；
4. 零散登记≠抑制：登记后库内会话行数不变 → 若有人把 noise 做成删除 → 红。

夹具一律 tempfile 临时 meta 库；**真 topics_meta.db 一字节不动**。
消费侧（candidates/keywords 排除零散）已由 test_v26_consolidate 钉住，不重复。
"""
from __future__ import annotations

import contextlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester.cli import cmd_noise
from harvester.consolidate import list_noise


def _args(**kw) -> object:
    """手工 namespace（与 test_v49 同款调用方式）。"""
    import argparse
    base = dict(action="add", sids=["src:s1"], reason="测试判零散",
                meta="topics_meta.db", apply=False)
    base.update(kw)
    return argparse.Namespace(**base)


class TestNoiseCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.meta = Path(self.tmp.name) / "topics_meta.db"

    def _out(self, *a, **kw) -> str:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cmd_noise(*a, **kw)
        self.assertEqual(rc, 0)
        return buf.getvalue()

    def test_add_dry_run_writes_nothing_not_even_table(self):
        out = self._out(_args(meta=str(self.meta), sids=["src:s1"],
                              apply=False))
        self.assertIn("dry-run", out)
        self.assertIn("将登记", out)
        self.assertFalse(self.meta.exists(),
                         "dry-run 连库文件都不该创建")
        self.assertEqual(list_noise(self.meta), [])

    def test_add_apply_and_idempotent(self):
        self._out(_args(meta=str(self.meta), sids=["src:s1", "src:s2"],
                        apply=True))
        rows = list_noise(self.meta)
        self.assertEqual({r["sid"] for r in rows}, {"src:s1", "src:s2"})
        self.assertEqual(len(rows), 2)
        # 幂等：重复登记 → 新增 0，行数不变
        out = self._out(_args(meta=str(self.meta), sids=["src:s1", "src:s3"],
                              apply=True))
        self.assertIn("已在表里（跳过）", out)
        rows2 = list_noise(self.meta)
        self.assertEqual(len(rows2), 3)
        self.assertEqual({r["sid"] for r in rows2},
                         {"src:s1", "src:s2", "src:s3"})

    def test_list_honest_empty_and_nonempty(self):
        out = self._out(_args(action="list", meta=str(self.meta)))
        self.assertIn("为空", out)
        self._out(_args(meta=str(self.meta), sids=["src:s9"],
                        reason="零散理由A", apply=True))
        out2 = self._out(_args(action="list", meta=str(self.meta)))
        self.assertIn("src:s9", out2)
        self.assertIn("零散理由A", out2)
        self.assertIn("可逆", out2)  # 撤销方式必须随清单交代

    def test_noise_does_not_touch_session_data(self):
        """登记≠抑制：sessions_noise 只写 meta 表，采集库一行不动。"""
        db = Path(self.tmp.name) / "harvester.db"
        con = sqlite3.connect(str(db))
        con.execute("CREATE TABLE sessions (sid TEXT PRIMARY KEY, title TEXT)")
        con.execute("INSERT INTO sessions VALUES ('src:s1', '正常会话')")
        con.commit()
        con.close()
        self._out(_args(meta=str(self.meta), sids=["src:s1"], apply=True))
        con = sqlite3.connect(str(db))
        n = con.execute(
            "SELECT COUNT(*) FROM sessions WHERE sid='src:s1'").fetchone()[0]
        con.close()
        self.assertEqual(n, 1, "零散登记绝不能动采集库")

    def test_unknown_action_fails_loud(self):
        ns = _args(action="explode", meta=str(self.meta))
        buf = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(buf):
            rc = cmd_noise(ns)
        self.assertEqual(rc, 2)
        self.assertIn("未知的 noise 动作", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
