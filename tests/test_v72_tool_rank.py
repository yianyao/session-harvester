# -*- coding: utf-8 -*-
"""第①步门（2026-10-23，用户批准）：**工具榜不许被小样本霸榜**。

用户原话（转述）："各种工具调用异常、踩坑、诊断结果……不是太少，而是偏多，失衡了
……需要设置异常阈值，不到的就不显示"。

真库实测的病（`docs/reports/probe-tool-ordering.py`）：`tool_rows` 原先按 `fail_rate`
倒序 → 榜首 `skill_run_asset`（**2 次调用**、100%）、榜二 `memory_source`（**1 次调用**、
100%），而 275 次失败的 `Edit` 排到十几名开外。榜单头部两名合计 **3 次调用**。

本文件钉住四件事（都是"改回去就会红"的）：
  1. 样本不足的行**永远排在**样本充足的行之后（不靠 rate 抢位）；
  2. 排行按**绝对失败数**降序（rate 只做同分裁决）；
  3. 样本不足段**按调用量**降序（**不按 rate**——否则 1/1 又跑到最前，等于没改）；
  4. `ranked` / `low_sample` 两个字段各管一件事，不许互相顶替；
  5. CLI Markdown（`render_report`）与 JSON（`tool_rows`）**同序**（禁第二套口径）。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO))

from harvester.toolstats import (  # noqa: E402
    RANK_MIN_CALLS, ToolStats, render_report, tool_rows)


def _st(calls: int, error: int, success: int | None = None) -> ToolStats:
    s = ToolStats()
    s.calls = calls
    s.error = error
    s.success = calls - error if success is None else success
    for i in range(error):
        s.errors["E%d" % i] += 1
    return s


class TestSmallSampleNeverTops(unittest.TestCase):
    def test_one_call_one_error_does_not_outrank_real_mass(self):
        """真库形状：1/1=100% 与 275/6505=4.2% —— 后者必须在前。"""
        rows = tool_rows({"memory_source": _st(1, 1),
                          "Edit": _st(6505, 275)})
        self.assertEqual([r["tool"] for r in rows], ["Edit", "memory_source"])
        self.assertTrue(rows[0]["ranked"])
        self.assertFalse(rows[1]["ranked"])

    def test_all_ranked_rows_come_before_any_small_row(self):
        rows = tool_rows({"tiny": _st(2, 2), "mid": _st(25, 1),
                          "big": _st(30, 9)})
        flags = [r["ranked"] for r in rows]
        self.assertEqual(flags, [True, True, False],
                         "样本不足的行必须整体排在后面，不许穿插")

    def test_ranked_orders_by_absolute_errors_then_rate(self):
        # 失败数 10 > 6，尽管 6/30=20% 高于 10/100=10%
        rows = tool_rows({"low_rate_many": _st(100, 10),
                          "high_rate_few": _st(30, 6)})
        self.assertEqual([r["tool"] for r in rows],
                         ["low_rate_many", "high_rate_few"])

    def test_small_section_orders_by_calls_not_rate(self):
        rows = tool_rows({"a": _st(1, 1), "b": _st(5, 1), "c": _st(2, 2)})
        small = [r["tool"] for r in rows if not r["ranked"]]
        self.assertEqual(small, ["b", "c", "a"], "样本不足段按调用量降序")

    def test_rank_min_calls_is_the_boundary_and_zero_disables(self):
        rows = tool_rows({"k": _st(RANK_MIN_CALLS, 1),
                          "k1": _st(RANK_MIN_CALLS - 1, 1)})
        self.assertTrue(rows[0]["ranked"], "等于下限算进排行")
        self.assertFalse(rows[1]["ranked"])
        rows0 = tool_rows({"k1": _st(RANK_MIN_CALLS - 1, 1)},
                          rank_min_calls=0)
        self.assertTrue(rows0[0]["ranked"], "下限设 0 = 关掉排序门槛")


class TestFieldsDoNotStandInForEachOther(unittest.TestCase):
    def test_low_sample_is_user_threshold_not_rank_floor(self):
        """`low_sample` 只由 min_calls 决定；`ranked` 只由 rank_min_calls 决定。"""
        rows = tool_rows({"t": _st(2, 1)}, min_calls=5, rank_min_calls=20)
        self.assertTrue(rows[0]["low_sample"])
        self.assertFalse(rows[0]["ranked"])
        rows = tool_rows({"t": _st(50, 1)}, min_calls=5, rank_min_calls=20)
        self.assertFalse(rows[0]["low_sample"])
        self.assertTrue(rows[0]["ranked"])


class TestCliAndJsonShareOneOrder(unittest.TestCase):
    def test_render_report_uses_tool_rows_order(self):
        stats = {"memory_source": _st(1, 1), "Edit": _st(6505, 275)}
        md = render_report(stats)
        self.assertLess(md.index("| Edit"), md.index("| memory_source"))
        # 样本不足要有可见标记（不静默丢弃，也不伪装成正常行）
        self.assertIn("（样本不足）", md)

    def test_render_report_keeps_every_row(self):
        stats = {"memory_source": _st(1, 1), "Edit": _st(6505, 275)}
        md = render_report(stats)
        for tool in stats:
            self.assertIn(tool, md, "样本不足的行仍要在表里（可见但不排行）")


class TestApiPayloadCarriesThePolicy(unittest.TestCase):
    """HTTP 面（页面就吃这个）：`rank_min_calls` 必须出现在载荷里，
    且 tools 顺序已经是"排行在前、样本不足在后"。"""

    DDL = """
CREATE TABLE sessions (sid TEXT PRIMARY KEY, source TEXT, session_id TEXT,
    title TEXT, category TEXT, created_at TEXT, updated_at TEXT,
    file TEXT, model TEXT);
CREATE TABLE messages (sid TEXT, role TEXT, ts TEXT, text TEXT, raw TEXT);
CREATE TABLE steps (sid TEXT, seq INT, ts TEXT, tool TEXT, phase TEXT,
    status TEXT, error TEXT, detail TEXT);
"""

    def _db(self, tmp: str) -> Path:
        import sqlite3
        db = Path(tmp) / "t.db"
        con = sqlite3.connect(str(db))
        con.executescript(self.DDL)
        con.execute("INSERT INTO sessions VALUES ('s','t','i','题','agent',"
                    "'2026-10-01','2026-10-01','f','m')")
        seq = 0
        for tool, calls, errs in (("Edit", 30, 5), ("memory_source", 1, 1)):
            for i in range(calls):
                seq += 1
                con.execute("INSERT INTO steps VALUES ('s',?,'2026-10-01 00:00:00',"
                            "?,'call',NULL,NULL,'')", (seq, tool))
                seq += 1
                has_err = i < errs
                con.execute("INSERT INTO steps VALUES ('s',?,'2026-10-01 00:00:00',"
                            "?,'result',?,?, '')",
                            (seq, tool, "error" if has_err else "ok",
                             "E_BOOM" if has_err else None))
        con.commit()
        con.close()
        return db

    def test_payload_order_and_field(self):
        import sqlite3
        from harvester.apiserve import api_reports_tools
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db(tmp)
            con = sqlite3.connect(str(db))
            con.row_factory = sqlite3.Row
            try:
                out = api_reports_tools(con, db, {})
            finally:
                con.close()
        self.assertEqual(out["rank_min_calls"], RANK_MIN_CALLS)
        names = [t["tool"] for t in out["tools"]]
        self.assertEqual(names, ["Edit", "memory_source"])
        self.assertTrue(out["tools"][0]["ranked"])
        self.assertFalse(out["tools"][1]["ranked"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()