# -*- coding: utf-8 -*-
"""v0.51 S3 测试：统计面只读连接收口（dbmeta.ro_con）。

铁律：只读纪律 + 坏库 fail loud 不静默建空库（原则③）。审计 c1-1 族——
统计面 6 模块此前缺省 `sqlite3.connect(str(db))`，缺库时会**静默建一个
空库**并把空态当数据用。收口后：存在性预检 + mode=ro，缺库一律
FileNotFoundError，且目标路径绝不落盘新文件。
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from harvester.behstats import collect_skill_invocations  # noqa: E402
from harvester.dbmeta import ro_con  # noqa: E402
from harvester.drafting import render_transcript  # noqa: E402
from harvester.errstats import collect_errors_from_db  # noqa: E402
from harvester.export_analysis import build_analysis  # noqa: E402
from harvester.toolstats import (collect_model_stats,  # noqa: E402
                                 collect_source_stats, collect_stats_from_db)
from harvester.triage import collect_triage  # noqa: E402


class FailLoudTest(unittest.TestCase):
    """缺库 fail loud：6 模块入口一律 FileNotFoundError，且不建空库。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.missing = Path(self.tmp.name) / "absent.db"

    def test_missing_db_fails_loud(self):
        cases = [
            ("toolstats.collect_stats_from_db",
             lambda: collect_stats_from_db(self.missing)),
            ("toolstats.collect_source_stats",
             lambda: collect_source_stats(self.missing)),
            ("toolstats.collect_model_stats",
             lambda: collect_model_stats(self.missing)),
            ("errstats.collect_errors_from_db",
             lambda: collect_errors_from_db(self.missing)),
            ("behstats.collect_skill_invocations",
             lambda: collect_skill_invocations(self.missing)),
            ("export_analysis.build_analysis",
             lambda: build_analysis(self.missing, "tools")),
            ("triage.collect_triage",
             lambda: collect_triage(self.missing)),
            ("drafting.render_transcript",
             lambda: render_transcript(self.missing, "s1")),
        ]
        for name, fn in cases:
            with self.subTest(entry=name):
                with self.assertRaises(FileNotFoundError, msg=name):
                    fn()

    def test_no_empty_db_created(self):
        """收口的本义：绝不静默落一个空库文件（审计场景回放）。"""
        with self.assertRaises(FileNotFoundError):
            collect_stats_from_db(self.missing)
        self.assertFalse(self.missing.exists(),
                         "缺库调用后路径上不得出现新文件")


class ConPassthroughTest(unittest.TestCase):
    """con 透传语义不变：传入的连接被使用、不被关闭（api-serve 口径）。"""

    def test_passthrough_not_closed(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = Path(tmp.name) / "fix.db"
        con = sqlite3.connect(str(db))
        self.addCleanup(con.close)
        con.executescript(
            "CREATE TABLE sessions (sid TEXT PRIMARY KEY, session_id TEXT,"
            " title TEXT, category TEXT, source TEXT DEFAULT 'test',"
            " model TEXT);"
            "CREATE TABLE steps (sid TEXT, seq INTEGER, ts TEXT, tool TEXT,"
            " phase TEXT, status TEXT, error TEXT, detail TEXT);")
        con.commit()
        stats, _flow = collect_stats_from_db(db, con=con)
        self.assertEqual(stats, {})
        con.execute("SELECT 1").fetchone()  # 未被 close：仍可用


class RoConUnitTest(unittest.TestCase):
    """ro_con 本体：mode=ro 打开 + own 语义。"""

    def test_own_true_and_row_factory(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = Path(tmp.name) / "real.db"
        sqlite3.connect(str(db)).close()
        c, own = ro_con(db)
        self.addCleanup(c.close)
        self.assertTrue(own)
        self.assertIs(c.row_factory, sqlite3.Row)

    def test_passthrough_returns_same(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = Path(tmp.name) / "real.db"
        con = sqlite3.connect(str(db))
        self.addCleanup(con.close)
        c, own = ro_con(db, con)
        self.assertIs(c, con)
        self.assertFalse(own)


if __name__ == "__main__":
    unittest.main()
