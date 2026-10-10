# -*- coding: utf-8 -*-
"""v0.45 keywords GC 测试（SOP C1）：`--keep-runs` / `--vacuum`。

背景：`keyword_stats` 是累积表——真库 `keyword_runs` 只 4 行，`keyword_stats`
已 **435,583 行 / 40.2 MB**，且没有 GC。判据都能说清何时会红：

- 只保留最近 N 次 → 更早的 run **与其统计行**都要消失；
- 留下**孤儿统计行**（stats 有、runs 无）→ 必须被测试抓住（比不删更坏）；
- `keep<=0` 必须报错（否则一条命令清空历史）；
- CLI `keywords --keep-runs` 真跑一次要对得上库内实际行数。
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester.cli import main
from harvester.indexing import SCHEMA, index_session
from harvester.kwstats import build_stats, prune_runs
from harvester.models import Message, SessionRecord


class TestKeywordsGc(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "h.db"
        con = sqlite3.connect(str(self.db))
        con.executescript(SCHEMA)
        index_session(con, SessionRecord(
            source="src", session_id="s1", title="甲会话",
            created_at="2026-01-01 10:00:00", updated_at="2026-01-01 10:00:00",
            messages=[Message(role="user",
                              text="叙事节奏与氛围渲染的技法讨论" * 3),
                      Message(role="assistant", text="收到。")]))
        con.commit()
        con.close()
        self.meta = root / "keywords_meta.db"

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def _runs(self) -> list[int]:
        con = sqlite3.connect(str(self.meta))
        try:
            return [r[0] for r in con.execute(
                "SELECT id FROM keyword_runs ORDER BY id")]
        finally:
            con.close()

    def _stats(self) -> list[int]:
        con = sqlite3.connect(str(self.meta))
        try:
            return [r[0] for r in con.execute(
                "SELECT DISTINCT run_id FROM keyword_stats ORDER BY run_id")]
        finally:
            con.close()

    def _build(self, times: int = 3):
        for _ in range(times):
            build_stats(self.db, self.meta, ns=[2])

    def test_keep_one_deletes_older_runs_and_their_stats(self):
        self._build(3)
        before = self._runs()
        self.assertEqual(len(before), 3)
        r = prune_runs(self.meta, keep=1)
        self.assertEqual(r["deleted_runs"], 2)
        self.assertGreater(r["deleted_stats"], 0)
        self.assertEqual(self._runs(), [before[-1]])
        # 关键：不留孤儿行（stats 里不许出现已删的 run_id）
        self.assertEqual(self._stats(), [before[-1]])
        con = sqlite3.connect(str(self.meta))
        try:
            orphans = con.execute(
                "SELECT COUNT(*) FROM keyword_stats s LEFT JOIN keyword_runs r "
                "ON r.id = s.run_id WHERE r.id IS NULL").fetchone()[0]
        finally:
            con.close()
        self.assertEqual(orphans, 0)

    def test_keep_more_than_present_is_noop(self):
        self._build(2)
        r = prune_runs(self.meta, keep=5)
        self.assertEqual((r["deleted_runs"], r["deleted_stats"]), (0, 0))
        self.assertFalse(r["vacuumed"])
        self.assertEqual(len(self._runs()), 2)

    def test_keep_zero_is_rejected(self):
        self._build(1)
        with self.assertRaises(ValueError):
            prune_runs(self.meta, keep=0)
        self.assertEqual(len(self._runs()), 1)      # 报错不许动库

    def test_vacuum_only_when_deleting(self):
        self._build(2)
        r = prune_runs(self.meta, keep=1, vacuum=True)
        self.assertTrue(r["vacuumed"])
        r2 = prune_runs(self.meta, keep=1, vacuum=True)
        self.assertFalse(r2["vacuumed"])            # 没删就不 VACUUM

    def test_missing_meta_is_reported_not_crashed(self):
        r = prune_runs(self.tmp.name and Path(self.tmp.name) / "nope.db", keep=1)
        self.assertEqual(r["deleted_runs"], 0)
        self.assertIn("hint", r)

    def test_cli_keep_runs_flag_actually_prunes(self):
        self._build(2)
        rc = main(["keywords", "--db", str(self.db), "--meta", str(self.meta),
                   "--n", "2", "--keep-runs", "1", "--vacuum",
                   "--no-stopwords"])
        self.assertEqual(rc, 0)
        self.assertEqual(len(self._runs()), 1)
        self.assertEqual(self._stats(), self._runs())


if __name__ == "__main__":
    unittest.main()
