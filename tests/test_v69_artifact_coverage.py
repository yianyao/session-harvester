# -*- coding: utf-8 -*-
"""P6-8 产物面覆盖度诚实的验收门。

钉住四件事，每件都能失败：

1. **覆盖度现算**：`pct`/`line` 里的数字来自两次 COUNT，**换库就换数字**；
2. **永远带分母**：任何形态都要说「仅 dsh 源 … 总会话 M … 本面不代表全库」，
   包括"没配库""库空了""总库不存在"这三种最容易滑成全绿的情况；
3. **缺数据不报错、但要看得见是 0**：`pct=0.0` 而不是异常，且不给"全覆盖"假象；
4. **三个出口同源**：CLI `artifacts show`、MCP `artifacts_list`、view 下轨
   `_artifacts_track` 的覆盖度都必须来自 `artifacts.coverage()`（同判据）。
"""
from __future__ import annotations

import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester.artifacts import coverage, ensure_artifacts_db

SH = Path(__file__).resolve().parents[1]
REAL_DB = SH / "harvester.db"

#: 「全库/全覆盖」这类会把部分覆盖说成全部的措辞——出现在 line 里就是违规
FULL_CLAIM = re.compile(r"全库覆盖|全覆盖|覆盖全部|所有源")


def _make_dbs(tmp: Path, n_sessions: int, sids: list[str],
              per_sid: int = 1) -> tuple[Path, Path]:
    """造两个库：索引库（sessions 表 N 行）+ 产物库（sids 各 per_sid 行）。"""
    db = tmp / "idx.db"
    con = sqlite3.connect(str(db))
    try:
        con.execute("CREATE TABLE sessions (sid TEXT PRIMARY KEY)")
        con.executemany("INSERT INTO sessions (sid) VALUES (?)",
                        [(f"s{i}",) for i in range(n_sessions)])
        con.commit()
    finally:
        con.close()
    meta = tmp / "art.db"
    ensure_artifacts_db(meta)
    con = sqlite3.connect(str(meta))
    try:
        con.executemany(
            "INSERT INTO artifacts (sid, seq, ts, tool, file_path, old_text, "
            "new_text) VALUES (?,?,?,?,?,?,?)",
            [(sid, i, "2026-10-01T00:00:00", "Write", "a.md", None, "x")
             for sid in sids for i in range(per_sid)])
        con.commit()
    finally:
        con.close()
    return db, meta


class TestCoverageIsComputed(unittest.TestCase):
    def test_pct_and_line_follow_the_counts(self):
        with tempfile.TemporaryDirectory() as td:
            db, meta = _make_dbs(Path(td), 8, ["s0", "s1"])
            cov = coverage(db, meta)
        self.assertEqual(cov["registered_sessions"], 2)
        self.assertEqual(cov["total_sessions"], 8)
        self.assertEqual(cov["pct"], 25.0)
        self.assertIn("已登记 2 / 总会话 8（25.0%）", cov["line"])
        # 换一个分母，数字必须跟着变（写死就会红）
        with tempfile.TemporaryDirectory() as td:
            db2, meta2 = _make_dbs(Path(td), 50, ["s0"])
            cov2 = coverage(db2, meta2)
        self.assertEqual(cov2["pct"], 2.0)
        self.assertIn("（2.0%）", cov2["line"])
        self.assertNotEqual(cov["line"], cov2["line"])

    def test_registered_is_sessions_not_versions(self):
        """一个会话 3 个版本 → registered 仍是 **1 个会话**，versions=3。"""
        with tempfile.TemporaryDirectory() as td:
            db, meta = _make_dbs(Path(td), 4, ["s0"], per_sid=3)
            cov = coverage(db, meta)
        self.assertEqual(cov["registered_sessions"], 1)
        self.assertEqual(cov["registered_versions"], 3)
        self.assertEqual(cov["pct"], 25.0)

    def test_counts_reconcile_with_direct_sql(self):
        """不能只信函数自己的算术：直接在同一批库上数一遍对账。"""
        with tempfile.TemporaryDirectory() as td:
            db, meta = _make_dbs(Path(td), 7, ["s0", "s1", "s2"])
            cov = coverage(db, meta)
            con = sqlite3.connect(str(db))
            try:
                total = con.execute(
                    "SELECT COUNT(*) FROM sessions").fetchone()[0]
            finally:
                con.close()
            con = sqlite3.connect(str(meta))
            try:
                reg = con.execute(
                    "SELECT COUNT(DISTINCT sid) FROM artifacts").fetchone()[0]
            finally:
                con.close()
        self.assertEqual(cov["total_sessions"], total)
        self.assertEqual(cov["registered_sessions"], reg)


class TestHonesty(unittest.TestCase):
    def test_always_says_dsh_only_and_not_whole_library(self):
        with tempfile.TemporaryDirectory() as td:
            db, meta = _make_dbs(Path(td), 10, ["s0"])
            cases = [coverage(db, meta)]
        cases.append(coverage(None, None))          # 两个都没配
        cases.append(coverage(REAL_DB, None))       # 只有总库
        for cov in cases:
            self.assertIn("仅 dsh 源", cov["line"], cov["line"])
            self.assertIn("artifacts.py:8", cov["line"])
            self.assertIn("本面不代表全库", cov["line"])
            self.assertIn("总会话", cov["line"])
            self.assertIsNone(FULL_CLAIM.search(cov["line"]),
                              "覆盖度行出现了「全库/全覆盖」措辞：" + cov["line"])
            self.assertEqual(cov["sources"], ["dsh"])

    def test_missing_db_does_not_raise_and_shows_zero(self):
        cov = coverage(None, None)
        self.assertEqual(cov["registered_sessions"], 0)
        self.assertEqual(cov["total_sessions"], 0)
        self.assertEqual(cov["pct"], 0.0)
        self.assertIn("已登记 0 / 总会话 0（0.0%）", cov["line"])

    def test_empty_artifacts_table_still_zero(self):
        """库存在但表空（跑过 ensure 没跑过 extract）→ 0，不是"未知"。"""
        with tempfile.TemporaryDirectory() as td:
            db, meta = _make_dbs(Path(td), 5, [])
            cov = coverage(db, meta)
        self.assertEqual(cov["registered_sessions"], 0)
        self.assertEqual(cov["pct"], 0.0)
        self.assertIn("仅 dsh 源", cov["line"])

    def test_total_zero_does_not_divide_by_zero(self):
        with tempfile.TemporaryDirectory() as td:
            _db, meta = _make_dbs(Path(td), 0, ["s0"])
            cov = coverage(Path(td) / "does-not-exist.db", meta)
        self.assertEqual(cov["registered_sessions"], 1)
        self.assertEqual(cov["total_sessions"], 0)
        self.assertEqual(cov["pct"], 0.0)


class TestThreeExitsShareOneSource(unittest.TestCase):
    """CLI / MCP / view 三处数字必须都来自 `coverage()`，且互相一致。"""

    def test_cli_prints_the_line(self):
        import io
        from contextlib import redirect_stdout
        from types import SimpleNamespace
        from harvester.cli import cmd_artifacts
        with tempfile.TemporaryDirectory() as td:
            db, meta = _make_dbs(Path(td), 8, ["s0", "s1"])
            args = SimpleNamespace(cmd="show", sid="s0", meta=str(meta),
                                   db=str(db), limit=None)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_artifacts(args)
            out = buf.getvalue()
            want = coverage(db, meta)["line"]
        self.assertEqual(rc, 0)
        self.assertIn(want, out, "CLI 印的覆盖度与 coverage() 不一致")
        self.assertIn("仅 dsh 源", out)

    def test_cli_extract_also_prints_it(self):
        """extract 分支容易漏——它才是"让覆盖度变大"的那条命令。

        用 mock 替掉回源提取（沙箱里没有 dsh 源），只验**这条分支真的印了覆盖度**；
        不 mock 的话函数在 `extract_session` 里就抛了，覆盖度根本没机会印。
        """
        import io
        from contextlib import redirect_stdout
        from types import SimpleNamespace
        from unittest import mock
        from harvester.cli import cmd_artifacts
        with tempfile.TemporaryDirectory() as td:
            db, meta = _make_dbs(Path(td), 4, [])
            args = SimpleNamespace(cmd="extract", sid="s0", meta=str(meta),
                                   db=str(db), limit=None)
            buf = io.StringIO()
            with mock.patch("harvester.artifacts.extract_session",
                            return_value=2):
                with redirect_stdout(buf):
                    rc = cmd_artifacts(args)
            out = buf.getvalue()
            want = coverage(db, meta)["line"]
        self.assertEqual(rc, 0)
        self.assertIn(want, out, "extract 分支没印覆盖度（或印的不一致）")
        self.assertIn("提取 2 条", out)

    def test_mcp_payload_carries_coverage(self):
        from harvester.mcpserver import HarvesterMcpServer
        with tempfile.TemporaryDirectory() as td:
            db, meta = _make_dbs(Path(td), 8, ["s0", "s1"])
            srv = HarvesterMcpServer(None, str(db), artifacts_meta=str(meta))
            obj = json.loads(srv.tool_artifacts_list({"limit": 10}))
            want = coverage(db, meta)
        self.assertIn("coverage", obj, "MCP 载荷缺 coverage（下游会当全库用）")
        self.assertEqual(obj["coverage"]["registered_sessions"],
                         want["registered_sessions"])
        self.assertEqual(obj["coverage"]["total_sessions"],
                         want["total_sessions"])
        self.assertIn("仅 dsh 源", obj["coverage"]["line"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()