# -*- coding: utf-8 -*-
"""v0.82 G2 结构化层：四个只读报告命令的 `--json` 机读出口。

钉住四件事：
① 四个命令都接 `--json PATH`（真解析器 `build_parser().parse_args`，
   不是对源码做子串检查）；
② `--json -` 的 stdout 是**纯 JSON**（能整体 `json.loads`），形状为
   `schema` / `generated_at` / `db_fingerprint` / `data`，且 `data` 带
   该命令的顶层键；
③ `--json <file>` 与 `--json -` 写出**同一串字节**（时钟冻结后比对，
   见 `FROZEN` 的注释）；
④ 缺省（不给 `--json`）仍是人读文本、非空、无 traceback——新选项纯 additive。

真库 `harvester.db` 缺失时整类 `skipTest`：夹具不在时不许判红。
"""

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from harvester.cli import build_parser, main

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "harvester.db"

#: (命令名, 真解析器要吃的 argv, schema, data 的期望顶层键)
CASES = (
    ("report-tools", ["report-tools", "--db", str(DB)], "report-tools/1",
     ("basis", "tools")),
    ("report-errors", ["report-errors", "--db", str(DB)], "report-errors/1",
     ("errors", "meta")),
    ("report-skill", ["report-skill", "--db", str(DB)], "report-skill/1",
     ("invocations", "summary")),
    ("report-chains", ["report-chains", "--db", str(DB)], "report-chains/1",
     ("sessions", "steps")),
)

#: 冻结时钟用的固定时刻。为什么要冻：机读载荷里有**不止一个**时间戳
#: （顶层 `generated_at` + `db_fingerprint.generated_at`/`db_mtime`），
#: 两次进程间运行跨秒就会让「同字节」断言随机红（AGENTS §三 20 的老坑）。
#: 两处都经 `time.strftime`，所以冻结这一个入口即可全部钉住。
FROZEN = "2026-01-01T00:00:00+0800"


def _run_main(argv, extra=()):
    """跑真 CLI（`main` 而非 `cmd_*`），返回 (退出码, stdout 文本)。"""
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main([*argv, *extra])
    return rc, buf.getvalue()


class CliJsonExit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not DB.is_file():
            raise unittest.SkipTest("缺真库 harvester.db（不在缺夹具时判红）")
        # 一次冻结时钟跑完四条，缓存原样 stdout，供「文件与 stdout 同字节」比对
        cls.raw: dict[str, str] = {}
        cls.payloads: dict[str, dict] = {}
        with mock.patch("time.strftime", return_value=FROZEN):
            for name, argv, _schema, _keys in CASES:
                rc, out = _run_main(argv, ["--json", "-"])
                assert rc == 0, f"{name} 退出码 {rc}"
                cls.raw[name] = out
                cls.payloads[name] = json.loads(out)

    # ---- ① 解析器接线 ----
    def test_parser_accepts_json_path(self):
        for name, argv, _schema, _keys in CASES:
            with self.subTest(cmd=name):
                a = build_parser().parse_args([*argv, "--json", "-"])
                self.assertEqual(a.json, "-", "--json - 必须原样进 args.json")
                a2 = build_parser().parse_args([*argv, "--json", "x.json"])
                self.assertEqual(a2.json, "x.json")

    def test_json_flag_defaults_to_none(self):
        """缺省 None = 旧行为（纯 additive 的接线前提）。"""
        for name, argv, _schema, _keys in CASES:
            with self.subTest(cmd=name):
                self.assertIsNone(build_parser().parse_args(argv).json)

    # ---- ② stdout 是纯 JSON + 形状 ----
    def test_stdout_is_pure_json(self):
        for name, _argv, schema, keys in CASES:
            with self.subTest(cmd=name):
                raw = self.raw[name]
                self.assertTrue(raw.lstrip().startswith("{"),
                                "stdout 不是纯 JSON（前面混了文本）")
                self.assertNotIn("Traceback", raw)
                obj = json.loads(raw)          # 整体可解析 = 没混人读文本
                self.assertEqual(obj["schema"], schema)
                self.assertIsInstance(obj["generated_at"], str)
                self.assertTrue(obj["generated_at"])
                self.assertIsInstance(obj["db_fingerprint"], dict)
                self.assertIsInstance(obj["data"], dict)
                for k in keys:
                    self.assertIn(k, obj["data"],
                                  f"{name} 的 data 缺顶层键 {k!r}")

    def test_db_fingerprint_is_real(self):
        """机读出口必须带真实库快照（不是空 dict 占位）。"""
        for name, _argv, _schema, _keys in CASES:
            with self.subTest(cmd=name):
                fp = self.payloads[name]["db_fingerprint"]
                for k in ("sessions", "steps", "errors"):
                    self.assertIn(k, fp)
                    self.assertIsInstance(fp[k], int)
                self.assertGreater(fp["sessions"], 0)

    # ---- ③ 文件与 stdout 同字节 ----
    def test_json_file_matches_stdout_bytes(self):
        with mock.patch("time.strftime", return_value=FROZEN):
            for name, argv, _schema, _keys in CASES:
                with self.subTest(cmd=name), \
                        tempfile.TemporaryDirectory() as td:
                    p = Path(td) / "out.json"
                    rc, _out = _run_main(argv, ["--json", str(p)])
                    self.assertEqual(rc, 0)
                    self.assertTrue(p.is_file(), "--json PATH 必须落盘")
                    self.assertEqual(
                        p.read_bytes(), self.raw[name].encode("utf-8"),
                        "落盘字节与 --json - 的 stdout 不一致")
                    json.loads(p.read_text(encoding="utf-8"))

    # ---- ④ 缺省仍是文本 ----
    def test_default_output_still_text(self):
        for name, argv, _schema, _keys in CASES:
            with self.subTest(cmd=name):
                rc, out = _run_main(argv)
                self.assertEqual(rc, 0)
                self.assertTrue(out.strip(), "缺省报告不该是空的")
                self.assertNotIn("Traceback", out)
                self.assertIn("# ", out, "缺省输出应是 Markdown 文本")
                with self.assertRaises(json.JSONDecodeError):
                    json.loads(out)            # 没给 --json 就不许吐 JSON

    # ---- data 与聚合函数同源的便宜对账 ----
    def test_data_is_self_consistent(self):
        tools = self.payloads["report-tools"]["data"]
        self.assertIn(tools["basis"], ("steps", "notes"))
        self.assertTrue(tools["tools"], "真库应有工具行")
        # 真对账（会红）：flow.errors 与逐工具 error 合计必须相等——
        # 两者都由 collect_stats_from_db 同一遍扫描产出；若 JSON 另写一套
        # 聚合（或去解析渲染文本），这里迟早对不上。
        self.assertEqual(
            sum(r["error"] for r in tools["tools"]),
            tools["flow"]["errors"],
            "逐工具 error 合计必须等于 flow.errors（同一份聚合）")

        err = self.payloads["report-errors"]["data"]
        self.assertEqual(len(err["errors"]), err["meta"]["error_count"],
                         "errors 条数与 meta.error_count 必须对得上")

        skill = self.payloads["report-skill"]["data"]
        self.assertEqual(
            sum(d["calls"] for d in skill["summary"].values()),
            len(skill["invocations"]),
            "summary 的 calls 合计应等于 invocations 条数（同一份聚合）")

        chains = self.payloads["report-chains"]["data"]
        for k in ("long", "streaks", "loops", "spins", "top"):
            self.assertIsInstance(chains[k], list, f"{k} 应是列表")


def _first_chain() -> Path | None:
    """真库边上的第一份 chain 长文（skill-join / chain-audit 要它当输入）。"""
    found = sorted((ROOT / "knowledge" / "topics").glob("chain-*.md"))
    return found[0] if found else None


class CliJsonExtended(unittest.TestCase):
    """P9 扩面：report-daily / report-skill-join / chain-audit / cards validate。

    与上类的差别只在**参数来源**（这几条要文件/目录当输入），口径完全一致：
    `--json -` 的 stdout 必须整体可解析、schema 对、data 带顶层键，
    并且**缺省行为不变**（人读文本、非空、不许吐 JSON）。
    """

    @classmethod
    def setUpClass(cls):
        if not DB.is_file():
            raise unittest.SkipTest("缺真库 harvester.db（不在缺夹具时判红）")
        # 无 PyYAML 时 chain 加载**不可用**（不是链不存在）——按「不适用」跳过，
        # 不按「失败」红。口径见 AGENTS §三 24：缺依赖要响亮，但缺依赖不等于
        # 被测行为错了；no-YAML 门禁那边有三道专门的门盯 loud 行为。
        try:
            has_yaml = importlib.util.find_spec("yaml") is not None
        except (ImportError, ModuleNotFoundError):
            # 无 YAML 门禁（scripts/gate_no_yaml.py）把 find_spec 直接改成抛
            # ModuleNotFoundError —— 「找不到」与「探针被拦」都按"不适用"处理。
            has_yaml = False
        if not has_yaml:
            raise unittest.SkipTest("缺 PyYAML：chain 面不适用（H9 另有 loud 门）")
        chain = _first_chain()
        if chain is None:
            raise unittest.SkipTest("缺 knowledge/topics/chain-*.md")
        cards = ROOT / "knowledge" / "cards"
        if not cards.is_dir():
            raise unittest.SkipTest("缺 knowledge/cards 目录")
        cls.cases = (
            ("report-daily",
             ["report-daily", "--db", str(DB), "--since", "1"],
             "report-daily/1", ("queues", "tools", "errors", "chains")),
            ("report-skill-join",
             ["report-skill-join", "--chain", str(chain), "--db", str(DB)],
             "report-skill-join/1", ("topic", "members", "joined", "stages")),
            ("chain-audit",
             ["chain-audit", str(chain), "--db", str(DB)],
             "chain-audit/1", ("chain", "members", "quotes", "coverage")),
            ("cards-validate",
             ["cards", "validate", "--root", str(cards), "--db", str(DB)],
             "cards-validate/1", ("root", "summary", "cards")),
        )
        cls.raw: dict[str, str] = {}
        cls.payloads: dict[str, dict] = {}
        with mock.patch("time.strftime", return_value=FROZEN):
            for name, argv, _schema, _keys in cls.cases:
                rc, out = _run_main(argv, ["--json", "-"])
                cls.rc = rc
                assert out.lstrip().startswith("{"), \
                    f"{name} 的 --json - 不是纯 JSON（混了人读文本）"
                cls.raw[name] = out
                cls.payloads[name] = json.loads(out)

    def test_stdout_is_pure_json(self):
        for name, _argv, schema, keys in self.cases:
            with self.subTest(cmd=name):
                obj = self.payloads[name]
                self.assertEqual(obj["schema"], schema)
                self.assertIsInstance(obj["generated_at"], str)
                self.assertIsInstance(obj["db_fingerprint"], dict)
                for k in keys:
                    self.assertIn(k, obj["data"],
                                  f"{name} 的 data 缺顶层键 {k!r}")
                self.assertNotIn("Traceback", self.raw[name])

    def test_json_file_matches_stdout_bytes(self):
        with mock.patch("time.strftime", return_value=FROZEN):
            for name, argv, _schema, _keys in self.cases:
                with self.subTest(cmd=name), \
                        tempfile.TemporaryDirectory() as td:
                    p = Path(td) / "out.json"
                    _run_main(argv, ["--json", str(p)])
                    self.assertEqual(
                        p.read_bytes(), self.raw[name].encode("utf-8"),
                        "落盘字节与 --json - 的 stdout 不一致")

    def test_default_output_still_text(self):
        """不给 --json 时行为零变化：人读文本、非空、不是 JSON。"""
        for name, argv, _schema, _keys in self.cases:
            if name == "chain-audit":
                continue      # 它的缺省文本走 stdout，但退出码依赖门结果，
                              # 由 test_v20 那类专门的门负责；此处不重复判
            with self.subTest(cmd=name):
                rc, out = _run_main(argv)
                self.assertIn(rc, (0, 1), "只允许 0/1：无错误数据面")
                self.assertTrue(out.strip(), "缺省报告不该是空的")
                self.assertNotIn("Traceback", out)
                with self.assertRaises(json.JSONDecodeError):
                    json.loads(out)

    # ---- 对账：data 与文本同源（会红的断言，不是形状检查） ----
    def test_data_is_self_consistent(self):
        daily = self.payloads["report-daily"]["data"]
        self.assertEqual(set(daily["queues"]),
                         {"decide", "repair", "distill", "verify"})
        self.assertTrue(all(isinstance(v, int) for v in daily["queues"].values()))
        # 错误段与 errors 面同源：条数 == meta.error_count
        self.assertEqual(len(daily["errors"]["errors"]),
                         daily["errors"]["meta"]["error_count"])
        # 工具段逐工具失败合计 == flow.errors（同一份聚合）
        self.assertEqual(sum(r["error"] for r in daily["tools"]["tools"]),
                         daily["tools"]["flow"]["errors"])

        audit = self.payloads["chain-audit"]["data"]
        # 引文门自洽：exact+partial == total-hit（hit 是口径字段）
        q = audit["quotes"]
        self.assertLessEqual(q["exact"] + q["partial"], q["total"])

        cards = self.payloads["cards-validate"]["data"]
        self.assertEqual(cards["summary"]["cards"], len(cards["cards"]),
                         "summary.cards 应等于逐卡结果条数（同一遍校验）")


if __name__ == "__main__":
    unittest.main()