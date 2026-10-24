# -*- coding: utf-8 -*-
"""P10-② 的门：`export-analysis --save` 落盘到**报告索引那个目录**。

用户问的是「相应的报告文件后端是不是本来就有了？」——答案是"内容现算、目录一直有"。
所以这个功能的验收判据不是"能写文件"，而是：

1. `--save` 的落点与 `/api/reports-index` 列的目录**是同一个**（两个表达式漂开就白做）；
2. 给了 `--out` 时 `--save` 不许抢方向盘（显式路径优先）；
3. 不传 `--save` 时行为一个字不变（还走 stdout，老脚本不被改坏）。
"""
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harvester import apiserve            # noqa: E402
from harvester import cli                 # noqa: E402

DB = REPO / "harvester.db"
_NEED_DB = unittest.skipUnless(DB.is_file(), "需要真实 harvester.db（集成对账）")


def _run(argv) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


class TestSavePathContract(unittest.TestCase):
    def test_same_dir_as_reports_index(self):
        """命门：`--save` 的目录必须等于 `default_reports_dir()`（报告索引读那里）。"""
        got = cli._default_report_path("errors", "md").parent
        self.assertEqual(got, apiserve.default_reports_dir())

    def test_filename_shape(self):
        p = cli._default_report_path("triage", "json")
        self.assertEqual(p.suffix, ".json")
        self.assertTrue(p.name.startswith("triage-"))
        self.assertRegex(p.name, r"^triage-\d{4}-\d{2}-\d{2}\.json$")


class TestParserDefault(unittest.TestCase):
    def test_save_flag_off_by_default(self):
        ns = cli.build_parser().parse_args(["export-analysis", "--kind", "errors"])
        self.assertFalse(ns.save, "缺省不许落盘（老行为是打印到 stdout）")

    def test_save_flag_can_be_set(self):
        ns = cli.build_parser().parse_args(
            ["export-analysis", "--kind", "errors", "--save"])
        self.assertTrue(ns.save)


class TestOutWinsOverSave(unittest.TestCase):
    @_NEED_DB
    def test_out_takes_precedence(self):
        """同时给 --out 与 --save：写 --out 指的文件，**不**往 docs/reports 复制一份。"""
        default_path = cli._default_report_path("errors", "md")
        existed = default_path.exists()
        before_mtime = default_path.stat().st_mtime_ns if existed else None
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "mine.md"
            rc, so, se = _run(["export-analysis", "--kind", "errors",
                               "--db", str(DB), "--out", str(out), "--save"])
            self.assertEqual(rc, 0, se)
            self.assertTrue(out.is_file())
            self.assertIn(str(out), so)
        if existed:
            self.assertEqual(default_path.stat().st_mtime_ns, before_mtime,
                             "--out 在时 --save 还偷偷写了默认报告文件")
        else:
            self.assertFalse(default_path.exists(),
                             "--out 在时 --save 还偷偷写了默认报告文件")


class TestStdoutUnchanged(unittest.TestCase):
    @_NEED_DB
    def test_no_save_still_prints(self):
        rc, so, se = _run(["export-analysis", "--kind", "errors",
                           "--db", str(DB), "--top", "1"])
        self.assertEqual(rc, 0, se)
        self.assertIn("#", so)
        self.assertNotIn("已写入", so)


if __name__ == "__main__":
    unittest.main()