# -*- coding: utf-8 -*-
"""v0.43 测试：端到端回归语料（`regress`）本身必须是**会红**的门。

这个文件测的是"回归工具自己"——所以每条断言都要能回答"它什么情况下红"：
- **真库隔离**：`run_regress` 前后两个真库文件的指纹（大小 + mtime_ns +
  sha256）必须逐字节不变；且 `cmd_regress` **没有** `--db` / `--meta` 参数
  （给了入口就等于给"顺手指向真库"留后门）。若有人把临时库换成真库读、
  或加一个 `--db` 参数，这里红。
- **期望值真的在比对**：故意改掉 `EXPECT` 的一个数字 → 必须变红；故意把
  自造语料换掉 → 必须变红（不是"语料换了还照过"）。
- **未执行 ≠ 通过**：把 `topicchain._HAS_YAML` 换成 False，`run_regress`
  必须返回 `status=not_run` + `exit_code=3`，**不能**是 `pass`/`0`。
- **退出码三分**：CLI 全通过 0；断言失败 1；前置缺失 3。

为什么值得为"测试工具"再写一层测试：项目有"改了 A 结果 B 悄悄变了"的历史
（H83 双出口漏条、H87 跨进程不确定），而 `regress` 是唯一一道**跨步骤**的
门——它自己静默失效（比如断言恒真、临时库指向真库）比没有它更危险。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harvester import regress as reg
from harvester.regress import (CORPUS_VERSION, EXPECT, REPORT_VERSION,
                               SessionSpec, default_corpus, dump_json,
                               render_report, run_regress)

REPO = Path(__file__).resolve().parents[1]

#: H9：缺 PyYAML 时**显式 error**，而不是让"断言 status=='fail'"静默不成立。
#: 这一条是 v0.43 门禁实测抓出来的：本模块原先在无 PyYAML 环境下产生 **4 条
#: failures**（前置把整条链记成 `not_run`，断言期望 `fail`）——故障模式正是
#: 项目 §五 16「不要把跳过伪装成通过」要防的那种：**门禁要求 0 failures**，
#: 而 failures 意味着"有人把环境缺依赖写成了断言"。改成导入期显式失败后，
#: 无 PyYAML 时它计入 errors（与 test_v29/test_v31 的既有口径一致）。
try:  # pragma: no cover - 环境相关
    import yaml  # noqa: F401
except ImportError as _e:  # pragma: no cover - 环境相关
    raise RuntimeError(
        "[H9] tests/test_v43_regress.py 需要 PyYAML（regress 的链路要读写 YAML）："
        f"{_e}。请用带 PyYAML 的解释器跑全量；无 PyYAML 环境下本模块应显式 error，"
        "不允许降级成 failures。") from _e


def _ns(**kw):
    """`cmd_regress` 的 argparse 命名空间替身（只需要它真正读的字段）。"""
    import argparse
    base = {"out": None, "json": None, "keep_temp": False, "temp_base": None,
            "list": False, "corpus": "default"}
    base.update(kw)
    return argparse.Namespace(**base)


def _need_yaml() -> None:
    """缺 PyYAML 时**显式报错**（项目约定 H9）：无 PyYAML 门禁跑出来必须是
    errors，不能把"环境缺依赖"写成断言失败（那会把真回归淹掉）。"""
    from harvester.topicchain import _HAS_YAML
    if not _HAS_YAML:
        raise RuntimeError("regress 的 plan/apply 段需要 PyYAML（H9）")


def _fingerprint(p: Path) -> dict:
    if not p.is_file():
        return {"exists": False}
    st = p.stat()
    return {"exists": True, "size": st.st_size, "mtime_ns": st.st_mtime_ns,
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}


class TestRegressEndToEnd(unittest.TestCase):
    """正例：内置语料下，每一步都通过，且结果结构可机读。"""

    @classmethod
    def setUpClass(cls):
        _need_yaml()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.result = run_regress(Path(cls.tmp.name))

    @classmethod
    def tearDownClass(cls):
        try:
            cls.tmp.cleanup()
        except OSError:
            pass

    def test_overall_pass(self):
        self.assertEqual(self.result["status"], reg.S_PASS,
                         self.result["why"])
        self.assertEqual(self.result["exit_code"], reg.EXIT_OK)
        self.assertEqual(self.result["failed_assertions"], 0,
                         [c for s in self.result["steps"] for c in s["checks"]
                          if not c["ok"]])
        self.assertEqual(self.result["why"], "")

    def test_step_set_is_the_whole_chain(self):
        """步骤集合就是链路本身；少一步（比如没人再跑 export）必须红。"""
        self.assertEqual([s["step"] for s in self.result["steps"]],
                         ["precondition", "corpus", "triage", "plan_seed",
                          "apply", "export", "determinism"])
        self.assertEqual({s["status"] for s in self.result["steps"]},
                         {reg.S_PASS})

    def test_assertions_are_not_vacuous(self):
        """断言条数是"这道门有多厚"的代理指标：低于 40 条说明有人删空了检查。

        这条本身不管对错，只管"还有没有在查"——删掉一半断言时它会红。
        """
        self.assertGreaterEqual(self.result["assertions"], 40)
        for s in self.result["steps"]:
            self.assertTrue(s["checks"], f"{s['step']} 一条断言都没有")

    def test_machine_output_is_json_and_self_describing(self):
        text = dump_json(self.result)
        parsed = json.loads(text)
        self.assertEqual(parsed["version"], REPORT_VERSION)
        self.assertEqual(parsed["corpus_version"], CORPUS_VERSION)
        self.assertEqual(parsed["corpus"] and len(parsed["corpus"]),
                         EXPECT["sessions"])
        # 下游是程序 → 每个断言都要带 expected/actual（没有 expected 的"断言"
        # 就是恒真，机读出口是它唯一的取证处）
        for s in parsed["steps"]:
            for c in s["checks"]:
                self.assertIn("expected", c)
                self.assertIn("actual", c)

    def test_report_is_human_readable_and_states_each_expectation(self):
        text = render_report(self.result)
        self.assertIn("端到端回归", text)
        self.assertIn("期望", text)
        self.assertIn("实测", text)
        self.assertIn("未执行", text)          # 退出码口径必须写在报告里
        # 语料说明必须印出来（否则"某类计数变了"无从判断是不是语料被改过）
        for sid in ("s01", "s12"):
            self.assertIn(sid, text)

    def test_time_to_run_is_sane(self):
        """整条链路是"随手就能重跑"的门：跑不动就没人会跑它。"""
        import time
        t0 = time.monotonic()
        run_regress(Path(self.tmp.name))
        self.assertLess(time.monotonic() - t0, 120.0)


class TestNeverTouchesRealDb(unittest.TestCase):
    """铁律：真库只读到底。这条红了说明有人把临时库换成了真库。"""

    def test_real_dbs_byte_identical_before_and_after(self):
        real = [REPO / "harvester.db", REPO / "topics_meta.db"]
        before = [_fingerprint(p) for p in real]
        tmp = tempfile.TemporaryDirectory()
        try:
            run_regress(Path(tmp.name))
        finally:
            tmp.cleanup()
        after = [_fingerprint(p) for p in real]
        for p, b, a in zip(real, before, after):
            self.assertEqual(b, a, f"{p.name} 被 regress 改动了（铁律破线）")

    def test_cli_has_no_real_db_switches(self):
        """**没有** `--db` / `--meta` 是设计（不给"顺手指向真库"留后门）。

        有人出于好意加这两个参数时，这条会红——请连同本测试一起改，
        而不是把它删掉。
        """
        r = self._run("regress", "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("--db", r.stdout)
        self.assertNotIn("--meta", r.stdout)
        self.assertIn("--json", r.stdout)
        self.assertIn("--out", r.stdout)
        self.assertIn("--keep-temp", r.stdout)

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(REPO / "scripts" / "sandbox"), str(REPO)]
            + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
        return subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "harvester", *args],
            cwd=str(REPO), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace")


class TestExitCodes(unittest.TestCase):
    """退出码三分：0 通过 / 1 断言失败 / 3 未执行。"""

    @classmethod
    def setUpClass(cls):
        _need_yaml()

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(REPO / "scripts" / "sandbox"), str(REPO)]
            + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
        return subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "harvester",
             "regress", *args], cwd=str(REPO), env=env, capture_output=True,
            text=True, encoding="utf-8", errors="replace")

    def test_cli_pass_writes_both_exits(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            out = Path(tmp.name) / "r.md"
            js = Path(tmp.name) / "r.json"
            r = self._run("--out", str(out), "--json", str(js))
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertIn("通过", r.stderr)
            self.assertGreater(out.stat().st_size, 1000)
            parsed = json.loads(js.read_text(encoding="utf-8"))
            self.assertEqual(parsed["exit_code"], 0)
        finally:
            tmp.cleanup()

    def test_cli_without_out_prints_report(self):
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("端到端回归", r.stdout)

    def test_cli_fail_returns_1_and_points_at_the_assertion(self):
        """故意漂移语料：CLI 必须退出码 1 且**点名是哪条断言**。

        用 `--corpus drift`（CLI 是独立进程，patch 不到它的 `EXPECT`）：
        多一条会话 → 期望值对不上 → 断言失败 → 退出码 1。
        """
        r = self._run("--corpus", "drift")
        self.assertEqual(r.returncode, 1, r.stderr + r.stdout)
        self.assertIn("会话数", r.stderr)
        self.assertIn("失败", r.stderr)
        # 下游步骤必须显示"未执行"而不是被跳过当成通过
        self.assertIn("未执行", r.stderr)

    def test_unknown_corpus_is_usage_error(self):
        r = self._run("--corpus", "不存在")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("未知语料", r.stderr)

    def test_not_run_maps_to_exit_3_in_cli_path(self):
        """`not_run` 必须翻译成 CLI 退出码 **3**（不是 0，也不是 1）。

        在进程内把 `_HAS_YAML` 打掉再走 `cmd_regress` 的真实路径——
        比"影子 yaml 目录 + 子进程"稳，且测的是**真实的退出码映射**。
        """
        tmp = tempfile.TemporaryDirectory()
        try:
            js = Path(tmp.name) / "r.json"
            args = _ns(out=None, json=str(js))
            with mock.patch("harvester.topicchain._HAS_YAML", False):
                from harvester.cli import cmd_regress
                code = cmd_regress(args)
            self.assertEqual(code, reg.EXIT_NOT_RUN)
            parsed = json.loads(js.read_text(encoding="utf-8"))
            self.assertEqual(parsed["status"], "not_run")
            self.assertNotEqual(parsed["status"], "pass")
            self.assertEqual(parsed["exit_code"], 3)
        finally:
            tmp.cleanup()


class TestNotRunIsNotPass(unittest.TestCase):
    """在进程内直接验证：前置缺失 → `not_run`/3，且后续步骤**不许**记成通过。"""

    def test_missing_yaml_yields_not_run(self):
        _need_yaml()
        tmp = tempfile.TemporaryDirectory()
        try:
            with mock.patch("harvester.topicchain._HAS_YAML", False):
                r = run_regress(Path(tmp.name))
        finally:
            tmp.cleanup()
        self.assertEqual(r["status"], reg.S_NOT_RUN)
        self.assertEqual(r["exit_code"], reg.EXIT_NOT_RUN)
        # 前置那一份是 pass，**链路后半段 5 步全部未执行**——一条都不许记成通过
        self.assertEqual(r["counts"]["pass"], 0)
        self.assertEqual(r["counts"]["not_run"], len(reg._CHAIN_STEPS) + 1)
        self.assertEqual(r["counts"]["fail"], 0)
        # 报告必须把"未执行"写出来，并且**不许**出现"通过"当结论
        text = render_report(r)
        self.assertIn("未执行", text)
        self.assertIn("不是通过", text)

    def test_corpus_step_failure_blocks_downstream(self):
        """造库失败 → 下游全记未执行；**绝不能**静默降级成通过。"""
        tmp = tempfile.TemporaryDirectory()
        try:
            with mock.patch.object(reg, "build_fixture",
                                   side_effect=RuntimeError("磁盘炸了")):
                r = run_regress(Path(tmp.name))
        finally:
            tmp.cleanup()
        self.assertEqual(r["status"], reg.S_FAIL)
        self.assertEqual(r["counts"]["pass"], 1)      # 只有 precondition
        self.assertEqual(r["counts"]["fail"], 1)
        blocked = [s for s in r["steps"] if s["step"] != "corpus"]
        self.assertEqual(len(blocked), len(reg._CHAIN_STEPS) + 1)
        for s in blocked:
            if s["step"] == "precondition":
                continue
            self.assertEqual(s["status"], reg.S_NOT_RUN, s["step"])
            self.assertIn("未执行", s["why"])


class TestAssertionsCanFail(unittest.TestCase):
    """**这道门必须会红**：把期望值改一点、或把语料换掉，都必须失败。"""

    def test_expectation_drift_is_caught(self):
        tmp = tempfile.TemporaryDirectory()
        old = EXPECT["plan_stats_totals"]
        EXPECT["plan_stats_totals"] = {**old, "assign": old["assign"] + 1}
        try:
            r = run_regress(Path(tmp.name))
        finally:
            EXPECT["plan_stats_totals"] = old
            tmp.cleanup()
        self.assertEqual(r["status"], reg.S_FAIL)
        failed = [c["name"] for s in r["steps"] for c in s["checks"]
                  if not c["ok"]]
        self.assertIn("覆盖统计 _total", failed)

    def test_keyword_order_drift_is_caught(self):
        """H87 那道门真的在比对次序：把期望次序调换 → 必须红。"""
        tmp = tempfile.TemporaryDirectory()
        old = EXPECT["keyword_order"]
        EXPECT["keyword_order"] = list(reversed(old))
        try:
            r = run_regress(Path(tmp.name))
        finally:
            EXPECT["keyword_order"] = old
            tmp.cleanup()
        self.assertEqual(r["status"], reg.S_FAIL)
        failed = [c["name"] for s in r["steps"] for c in s["checks"]
                  if not c["ok"]]
        self.assertIn("关键词全序 = (-长度, 主题名, 关键词) 的纯函数", failed)

    def test_custom_corpus_must_match_expectations(self):
        """换语料不会"照过"：计数对不上就红（语料与期望是一体的）。"""
        tmp = tempfile.TemporaryDirectory()
        tiny = [SessionSpec("x1", "只有一条", ["随便问问"])]
        try:
            r = run_regress(Path(tmp.name), corpus=tiny)
        finally:
            tmp.cleanup()
        self.assertEqual(r["status"], reg.S_FAIL)
        self.assertNotEqual(r["exit_code"], reg.EXIT_OK)

    def test_default_corpus_is_stable_between_calls(self):
        """语料是纯函数：两次调用逐字段相同（否则回归自己就不确定）。"""
        a = [(s.sid, s.title, tuple(s.turns)) for s in default_corpus()]
        b = [(s.sid, s.title, tuple(s.turns)) for s in default_corpus()]
        self.assertEqual(a, b)


class TestKeepTemp(unittest.TestCase):
    """`--keep-temp`：临时库留着人工翻查，且报告里给出路径。"""

    def test_temp_dir_survives_and_is_reported(self):
        _need_yaml()
        base = Path(tempfile.mkdtemp(prefix="regress-base-"))
        try:
            r = run_regress(base, keep_temp=True)
            self.assertEqual(r["failed_assertions"], 0, r["why"])
            self.assertTrue(r["temp_dir"])
            kept = Path(r["temp_dir"])
            self.assertTrue(kept.is_dir())
            self.assertTrue((kept / "harvester.db").is_file())
            self.assertTrue((kept / "topics_meta.db").is_file())
            self.assertTrue((kept / "judgment.yaml").is_file())
            # 产物真落盘（这正是"人读/机读分出口"要保住的东西）
            self.assertGreater((kept / "topic-tp-A.json").stat().st_size, 0)
            self.assertGreater((kept / "topic-tp-A.md").stat().st_size, 0)
            self.assertIn(r["temp_dir"], render_report(r))
        finally:
            shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
