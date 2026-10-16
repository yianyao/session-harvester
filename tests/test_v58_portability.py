# -*- coding: utf-8 -*-
"""v0.75 可迁移批次一（用户裁决 2026-10-15 清单 A1–A3/A5）。

钉住四件事：
1. 版本单一真值源：pyproject 的 version == harvester.__version__（此前
   0.23.0/0.46.0/v0.66 三处漂移，apiserve 对外自报 0.46 而仓库在 v0.74）；
2. _force_utf8_streams 必须覆盖 stdin（mcp-serve GBK 换机即崩的根因）；
3. 被跟踪文件不得再出现本机用户名/绝对路径（test_v23、采集器 node 路径、
   workbuddy docstring——回归门，谁再硬编码谁红）；
4. 采集器 node 解析器的优先级（env → PATH → WorkBuddy versions）。
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]


def _load_verify_module(name: str):
    """verify/ 不是包：按路径加载（仅标准库依赖，无 venv 也能导入）。"""
    spec = importlib.util.spec_from_file_location(
        name, REPO / "verify" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestVersionSingleSource(unittest.TestCase):
    """A4：pyproject 与 __init__ 必须同版（此前三处漂移的回归门）。"""

    def test_pyproject_matches_dunder(self):
        from harvester import __version__
        text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
        self.assertIsNotNone(m, "pyproject.toml 里找不到 version =")
        self.assertEqual(m.group(1), __version__,
                         "pyproject 与 harvester.__version__ 漂移了——"
                         "改版本必须两处同改（apiserve/MCP 对外自报的是后者）")


class TestStdinUtf8(unittest.TestCase):
    """A1：_force_utf8_streams 必须把 stdin 一起重配（GBK 换机即崩的根因）。"""

    def test_covers_stdin(self):
        from harvester import cli

        class Fake:
            def __init__(self):
                self.kw = None

            def reconfigure(self, **kw):
                self.kw = kw

        fakes = {name: Fake() for name in ("stdout", "stderr", "stdin")}
        with mock.patch.multiple(sys, **{k: v for k, v in fakes.items()}):
            cli._force_utf8_streams()
        for name, f in fakes.items():
            self.assertIsNotNone(f.kw, f"{name} 没被 reconfigure")
            self.assertEqual(f.kw.get("encoding"), "utf-8")


class TestNoMachinePathsInTrackedFiles(unittest.TestCase):
    """A2/A3/A5：被跟踪文件不得再出现本机用户名/绝对路径（回归门）。"""

    PINS = [
        ("tests/test_v23_yuanbao_blockguard.py", "yianyao"),
        ("verify/yuanbao_detail_harvest.py", "yianyao"),
        ("harvester/adapters/workbuddy.py", "yianyao"),
    ]

    def test_no_username_in_pinned_files(self):
        for rel, bad in self.PINS:
            text = (REPO / rel).read_text(encoding="utf-8")
            self.assertNotIn(bad, text, f"{rel} 又出现了本机用户名")

    def test_test_v23_uses_repo_relative(self):
        text = (REPO / "tests" / "test_v23_yuanbao_blockguard.py").read_text(
            encoding="utf-8")
        self.assertIn("parents[1]", text, "H 应为 Path(__file__)+parents[1]")

    def test_suppress_oneoff_not_in_scripts(self):
        self.assertFalse((REPO / "scripts" / "build_suppress_report.py").exists(),
                         "机器绑定一次性脚本应留在 _audit/attic/，不回 scripts/")


class TestNodeResolver(unittest.TestCase):
    """A3：采集器 node 解析优先级 env → PATH → WorkBuddy versions。"""

    def test_env_wins(self):
        ybh = _load_verify_module("yuanbao_detail_harvest")
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "node.exe"
            fake.write_bytes(b"")
            with mock.patch.dict(os.environ, {"HARVESTER_NODE_EXE": str(fake)}):
                self.assertEqual(ybh._find_node(), str(fake))

    def test_agent_browser_prefers_node_sibling(self):
        ybh = _load_verify_module("yuanbao_detail_harvest")
        with tempfile.TemporaryDirectory() as tmp:
            node = Path(tmp) / "node.exe"
            ab = (Path(tmp) / "node_modules" / "agent-browser" / "bin")
            ab.mkdir(parents=True)
            (ab / "agent-browser.js").write_text("//", encoding="utf-8")
            got = ybh._find_agent_browser_js(str(node))
            self.assertEqual(Path(got), ab / "agent-browser.js")


if __name__ == "__main__":
    unittest.main()
