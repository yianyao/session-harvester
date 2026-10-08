# -*- coding: utf-8 -*-
"""v0.20 可读性与批量导出改造测试：
- C1 错误明细根因聚合（toolstats.aggregate_error_roots / render_report）；
- C3 批量导出数据通路（api_sessions errors_only / api_session error_steps）。
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester import apiserve
from harvester.indexing import SCHEMA, index_session
from harvester.models import Message, SessionRecord
from harvester.toolstats import ToolStats, aggregate_error_roots, render_report

_ERR_A = ('Error: cannot edit "D:\\a\\x.py": file changed since it was read '
          "— re-read the file, then retry")
_ERR_B = ('Error: cannot edit "D:\\b\\y.py": file changed since it was read '
          "— re-read the file, then retry")
_ERR_MULTI = ("Error: String to replace not found in file. "
              "当前文件内容与 old_string 不一致.\nString: \"## 七步骨架\"\n"
              "### 批次 8：工单机制建立")


def _fixture_db(tmp: Path) -> Path:
    """会话 aaa：含 2 条错误步骤；会话 bbb：无任何错误（errors_only 反例）。"""
    db = tmp / "v20_test.db"
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)
    recs = [
        SessionRecord(source="src", session_id="aaa", title="A",
                      created_at="2026-10-07 10:00:00",
                      updated_at="2026-10-07 10:00:00",
                      messages=[Message(role="user", text="改文件")]),
        SessionRecord(source="src", session_id="bbb", title="B",
                      created_at="2026-10-06 10:00:00",
                      updated_at="2026-10-06 10:00:00",
                      messages=[Message(role="user", text="没错误")]),
    ]
    for rec in recs:
        index_session(con, rec)
    steps = [
        ("src:aaa", 0, "2026-10-07 10:00:01", "Edit", "call", "", "", ""),
        ("src:aaa", 1, "2026-10-07 10:00:02", "Edit", "result", "error",
         _ERR_A, ""),
        ("src:aaa", 2, "2026-10-07 10:00:03", "Edit", "result", "error",
         _ERR_B, ""),
        ("src:aaa", 3, "2026-10-07 10:00:04", "Edit", "result", "error",
         _ERR_MULTI, ""),
    ]
    for row in steps:
        con.execute("INSERT INTO steps (sid, seq, ts, tool, phase, status, "
                    "error, detail) VALUES (?,?,?,?,?,?,?,?)", row)
    con.commit()
    con.close()
    return db


class TestAggregateErrorRoots(unittest.TestCase):
    """C1：{错误原文: 次数} → 根因行（同构归并 + 三分类 + 单样例）。"""

    def test_merge_same_root(self):
        roots = aggregate_error_roots({_ERR_A: 2, _ERR_B: 3})
        self.assertEqual(len(roots), 1)  # 路径差异归一后同根因
        g = roots[0]
        self.assertIn("file changed since", g["pattern"])
        self.assertNotIn("D:", g["pattern"])  # 路径已占位符化
        self.assertEqual(g["class"], "tool_interface")
        self.assertEqual(g["count"], 5)

    def test_sorted_and_sample_collapsed(self):
        roots = aggregate_error_roots({_ERR_MULTI: 1, _ERR_A: 4})
        self.assertEqual(roots[0]["count"], 4)
        # 样例空白归一：不含换行（「七步骨架/批次 8」类多行污染不再进报告）
        self.assertNotIn("\n", roots[0]["sample"])
        self.assertEqual(len(roots[1]["pattern"].splitlines()), 1)

    def test_empty(self):
        self.assertEqual(aggregate_error_roots({}), [])


class TestRenderReportDetail(unittest.TestCase):
    """C1：G1 报告错误明细为根因聚合形态。"""

    def test_detail_section(self):
        st = ToolStats()
        st.calls, st.error, st.success = 6, 3, 3
        st.errors[_ERR_A] = 2
        st.errors[_ERR_B] = 3
        st.errors[_ERR_MULTI] = 1
        report = render_report({"Edit": st})
        self.assertIn("错误明细（根因聚合，按失败次数排序）", report)
        self.assertIn("- x5 [tool_interface]", report)  # 同根因合并计数
        # 多行 old_string 原文不进入报告（污染被样例空白归一挡住）
        self.assertNotIn("批次 8", report)
        self.assertNotIn("七步骨架", report)


class TestSessionsErrorsOnly(unittest.TestCase):
    """C3：api_sessions errors_only 过滤（分页前生效，total 同步）。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = _fixture_db(Path(cls.tmp.name))
        cls.con = apiserve.open_ro(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        cls.tmp.cleanup()

    def test_errors_only_filters_and_counts(self):
        d = apiserve.api_sessions(self.con, self.db, {"errors_only": "1"})
        self.assertEqual(d["total"], 1)
        self.assertEqual([it["sid"] for it in d["items"]], ["src:aaa"])
        self.assertEqual(d["items"][0]["error_count"], 3)

    def test_no_filter_keeps_all(self):
        d = apiserve.api_sessions(self.con, self.db, {})
        self.assertEqual(d["total"], 2)


class TestSessionErrorSteps(unittest.TestCase):
    """C3：api_session additive error_steps（含归一 pattern/class）。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = _fixture_db(Path(cls.tmp.name))
        cls.con = apiserve.open_ro(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        cls.tmp.cleanup()

    def test_error_steps_shape(self):
        d = apiserve.api_session(self.con, "src:aaa")
        steps = d["error_steps"]
        self.assertEqual(len(steps), 3)
        s0 = steps[0]
        self.assertEqual((s0["sid"], s0["seq"], s0["tool"]),
                         ("src:aaa", 1, "Edit"))
        self.assertEqual(s0["class"], "tool_interface")
        self.assertNotIn("D:", s0["pattern"])  # 归一 pattern 供跨会话去重
        # 多行错误文本的 pattern 只取首行
        self.assertNotIn("\n", steps[2]["pattern"])

    def test_session_without_errors(self):
        d = apiserve.api_session(self.con, "src:bbb")
        self.assertEqual(d["error_steps"], [])


class TestApiToolsRoots(unittest.TestCase):
    """P0-2：api_reports_tools additive roots（根因聚合接线到 API，
    view G1 不再直吐未聚合明细）。断言"过滤真的能滤"式不变量：
    roots 总计数 == 工具 error 计数（防聚合丢数）。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = _fixture_db(Path(cls.tmp.name))
        cls.con = apiserve.open_ro(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        cls.tmp.cleanup()

    def _edit_row(self, d):
        return next(t for t in d["tools"] if t["tool"] == "Edit")

    def test_roots_shape_and_count(self):
        d = apiserve.api_reports_tools(self.con, self.db, {})
        edit = self._edit_row(d)
        self.assertIn("roots", edit)
        self.assertLessEqual(len(edit["roots"]), 12)
        self.assertGreater(len(edit["roots"]), 0)
        for g in edit["roots"]:
            self.assertEqual(set(g), {"pattern", "class", "count", "sample"})
        # 防聚合丢数：roots 计数合计 == error 字段
        self.assertEqual(sum(g["count"] for g in edit["roots"]),
                         edit["error"])
        # 同根因（路径差异）已归并：_ERR_A/_ERR_B → 1 条
        self.assertEqual(len(edit["roots"]), 2)

    def test_errors_field_kept_additive(self):
        """additive 红线：原 errors 明细字段保留不动。"""
        d = apiserve.api_reports_tools(self.con, self.db, {})
        edit = self._edit_row(d)
        self.assertEqual(len(edit["errors"]), 3)  # 未聚合明细仍在
        self.assertEqual(edit["error"], 3)

    def test_by_source_rows_also_have_roots(self):
        d = apiserve.api_reports_tools(self.con, self.db, {})
        row = next(t for t in d["by_source"]["src"]["tools"]
                   if t["tool"] == "Edit")
        self.assertIn("roots", row)
        self.assertEqual(sum(g["count"] for g in row["roots"]), row["error"])


if __name__ == "__main__":
    unittest.main()
