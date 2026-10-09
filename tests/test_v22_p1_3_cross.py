# -*- coding: utf-8 -*-
"""P1-3 交叉表 class × harness(source) × model 测试。

SOP-P1-3：一条 SQL 取 source/model，分类复用 errstats.classify_error
（禁第二套分类）；API 走 /api/reports/errors additive 字段 `cross`。

红线 §5.6：以下断言在实现前均为红——
- collect_errors_from_db 的 errors 项此前无 model 键；
- api_reports_errors 此前无 cross 字段；
- render_report 此前无"数据源 × model × 错误类别交叉表"节。
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester import apiserve
from harvester.errstats import (CLASSES, collect_errors_from_db,
                                cross_stats, render_report)
from harvester.indexing import SCHEMA, index_session
from harvester.models import Message, SessionRecord

_ERR_TI = "String to replace not found: X"   # tool_interface
_ERR_ENV = "fetch failed: connection refused"  # env


def _fixture_db(tmp: Path) -> Path:
    """3 会话（两个带 model、一个无 model）+ 3 条错误步骤。

    src:aaa(model=glm-4.6)  → 1 条 tool_interface
    src:bbb(model=kimi-k2)  → 1 条 env
    src:ccc(model=NULL)     → 1 条 tool_interface（model 归"（未知）"）
    """
    db = tmp / "p1_3_test.db"
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)
    recs = [
        SessionRecord(source="src", session_id="aaa", title="交叉表样例A",
                      created_at="2026-10-07 10:00:00",
                      updated_at="2026-10-07 10:00:00",
                      extra={"model": "glm-4.6"},
                      messages=[Message(role="user", text="改文件")]),
        SessionRecord(source="src", session_id="bbb", title="交叉表样例B",
                      created_at="2026-10-06 10:00:00",
                      updated_at="2026-10-06 10:00:00",
                      extra={"model": "kimi-k2"},
                      messages=[Message(role="user", text="连网络")]),
        SessionRecord(source="other", session_id="ccc", title="交叉表样例C",
                      created_at="2026-10-05 10:00:00",
                      updated_at="2026-10-05 10:00:00",
                      messages=[Message(role="user", text="再改")]),
    ]
    for rec in recs:
        index_session(con, rec)
    steps = [
        ("src:aaa", 5, "2026-10-07 10:00:04", "Edit", "result", "error",
         _ERR_TI, ""),
        # 同会话第二条错误（不同类别）——保证 src×glm-4.6 组合计 >1，
        # "首行=最多坑组合"的排序断言才有区分度
        ("src:aaa", 6, "2026-10-07 10:00:05", "Bash", "result", "error",
         _ERR_ENV, ""),
        ("src:bbb", 5, "2026-10-06 10:00:04", "WebFetch", "result", "error",
         _ERR_ENV, ""),
        ("other:ccc", 5, "2026-10-05 10:00:04", "Edit", "result", "error",
         _ERR_TI, ""),
    ]
    for row in steps:
        con.execute("INSERT INTO steps (sid, seq, ts, tool, phase, status, "
                    "error, detail) VALUES (?,?,?,?,?,?,?,?)", row)
    con.commit()
    con.close()
    return db


class TestCollectCarriesModel(unittest.TestCase):
    """collect_errors_from_db 的 errors 项必须带 model（NULL 归"（未知）"）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _fixture_db(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_errors_carry_model(self):
        errors, _ = collect_errors_from_db(self.db)
        by_sid = {e["sid"]: e for e in errors}
        self.assertEqual(len(errors), 4)
        self.assertEqual(by_sid["src:aaa"]["model"], "glm-4.6")
        self.assertEqual(by_sid["src:bbb"]["model"], "kimi-k2")
        self.assertEqual(by_sid["other:ccc"]["model"], "（未知）")

    def test_classification_reused(self):
        """交叉表的 class 必须与 classify_error 单点口径一致。"""
        from harvester.errstats import classify_error
        errors, _ = collect_errors_from_db(self.db)
        for e in errors:
            self.assertEqual(e["class"], classify_error(e["error"]))


class TestCrossStats(unittest.TestCase):
    """cross_stats 纯聚合：source × model × class → 行列表。"""

    def _errs(self):
        return [
            {"source": "src", "model": "glm-4.6", "class": "tool_interface"},
            {"source": "src", "model": "glm-4.6", "class": "tool_interface"},
            {"source": "src", "model": "glm-4.6", "class": "env"},
            {"source": "src", "model": "（未知）", "class": "context"},
            {"source": "other", "model": "kimi-k2", "class": "env"},
        ]

    def test_rows_and_totals(self):
        rows = cross_stats(self._errs())
        self.assertEqual(len(rows), 3)
        # 排序：total 降序 → src/glm-4.6(3) 第一
        self.assertEqual(rows[0]["source"], "src")
        self.assertEqual(rows[0]["model"], "glm-4.6")
        self.assertEqual(rows[0]["tool_interface"], 2)
        self.assertEqual(rows[0]["env"], 1)
        self.assertEqual(rows[0]["context"], 0)
        self.assertEqual(rows[0]["unclassified"], 0)
        self.assertEqual(rows[0]["total"], 3)
        self.assertEqual(sum(r["total"] for r in rows), 5)

    def test_all_class_keys_present(self):
        """每行必须含全部四类键（缺类渲染列不塌）。"""
        rows = cross_stats(self._errs())
        for r in rows:
            for c in CLASSES:
                self.assertIn(c, r)
            self.assertIn("total", r)

    def test_empty(self):
        self.assertEqual(cross_stats([]), [])


class TestApiAndRender(unittest.TestCase):
    """/api/reports/errors additive `cross` + render_report 交叉表节。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _fixture_db(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_api_reports_errors_cross(self):
        con = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            r = apiserve.api_reports_errors(con, self.db, {})
        finally:
            con.close()
        self.assertIn("cross", r)
        rows = r["cross"]
        self.assertEqual(len(rows), 3)
        # 对账：cross 合计 == meta.error_count == by_class 合计（聚合不丢数）
        self.assertEqual(sum(x["total"] for x in rows),
                         r["meta"]["error_count"])
        self.assertEqual(sum(x["total"] for x in rows),
                         sum(r["by_class"].values()))
        # 排序第一行回答"哪个 harness 的哪类坑最多"（src×glm-4.6 合计 2）
        top = rows[0]
        self.assertEqual(top["source"], "src")
        self.assertEqual(top["model"], "glm-4.6")
        self.assertEqual(top["total"], 2)

    def test_render_report_cross_section(self):
        errors, meta = collect_errors_from_db(self.db)
        md = render_report(errors, meta)
        self.assertIn("数据源 × model × 错误类别交叉表", md)
        self.assertIn("glm-4.6", md)
        self.assertIn("（未知）", md)

    def test_render_report_no_errors_no_cross(self):
        md = render_report([], {"total_steps": 10, "error_count": 0,
                                "sessions": 0, "since": None})
        self.assertNotIn("交叉表", md)


if __name__ == "__main__":
    unittest.main()
