# -*- coding: utf-8 -*-
"""v0.23 #5：依赖清单（pyproject.toml）与代码实际可选依赖的一致性守卫。

背景：项目此前**没有任何依赖清单**（pyproject/setup.py/requirements 全缺），
而代码里有 3 处可选依赖导入（cards/topicchain 的 yaml、dsh 的 zstandard、
weblogin 的 playwright）。后果是"缺 PyYAML 时 23 个测试报错"这条信息
只存在于 HANDOFF 里，新环境无从得知要装什么。

本测试把 manifest 与代码导入点**钉死**：增删任一侧而不同步，这里就红。
"""

from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"

#: 代码里的可选依赖导入点 → 期望出现在哪个 extra 里。
#: 键为 pyproject 的 extra 名，值为该 extra 覆盖的导入模块名。
EXPECTED_EXTRAS: dict[str, set[str]] = {
    "yaml": {"yaml"},
    "dsh": {"zstandard"},
    "web": {"playwright"},
}

#: 这些导入不属包本体（verify/ 采集脚本独立 venv 运行）。
NOT_IN_PACKAGE = {"requests", "websocket_client", "websocket"}

#: try 块内但**不是第三方依赖**的导入——需豁免，并说明为什么。
STDLIB_OR_PROBE = {
    # 3.14+ 才有 compression.zstd；3.10–3.13 走 zstandard extra 或外部 zstd。
    # sys.stdlib_module_names 在 3.13 里不含 compression，故显式豁免。
    "compression",
}


def _load_pyproject() -> dict:
    try:
        import tomllib  # Python 3.11+
    except ModuleNotFoundError:  # 3.10 无 tomllib
        import tomli as tomllib  # type: ignore
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _optional_imports() -> set[str]:
    """扫描 harvester/ 收集"被 try/except 包住的可选导入"的顶层模块名。

    判定方式：找出所有在 Try 节点内的 Import/ImportFrom 语句——正是本项目
    "缺依赖则降级/报错" 的写法；再剔除标准库与已知探针模块。
    """
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    found: set[str] = set()
    for path in (ROOT / "harvester").rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            for sub in ast.walk(node):
                if isinstance(sub, ast.Import):
                    for a in sub.names:
                        found.add(a.name.split(".")[0])
                elif isinstance(sub, ast.ImportFrom):
                    if sub.module and sub.level == 0:
                        found.add(sub.module.split(".")[0])
    return found - NOT_IN_PACKAGE - stdlib - STDLIB_OR_PROBE


def _declared_extra_modules(extras: dict[str, list[str]]) -> dict[str, set[str]]:
    """extra → 其 requirements 里的分发名（归一为小写、去版本约束）。"""
    out: dict[str, set[str]] = {}
    for name, reqs in extras.items():
        mods: set[str] = set()
        for r in reqs:
            pkg = re.split(r"[<>=!~\[\s]", r.strip())[0].strip().lower()
            if pkg:
                mods.add(pkg)
        out[name] = mods
    return out


class TestDependencyManifest(unittest.TestCase):

    def setUp(self):
        self.file = PYPROJECT
        if not PYPROJECT.is_file():
            self.fail("pyproject.toml 缺失——依赖清单是本测试的存在前提")

    def test_toml_parses_and_core_is_zero_dep(self):
        d = _load_pyproject()
        proj = d.get("project") or {}
        self.assertEqual(proj.get("dependencies"), [],
                         "核心依赖必须为零（README「依赖边界」声明的承诺）")
        self.assertEqual(proj.get("requires-python"), ">=3.10")

    def test_every_optional_import_has_an_extra(self):
        """代码里每个可选导入，都必须有对应 extra 声明。"""
        declared = _declared_extra_modules(
            _load_pyproject()["project"]["optional-dependencies"])
        declared_mods = {m for mods in declared.values() for m in mods}
        # PyYAML 的分发名是 pyyaml，导入名是 yaml——归一别名
        aliases = {"pyyaml": "yaml"}
        declared_mods = {aliases.get(m, m) for m in declared_mods}

        missing = sorted(_optional_imports() - declared_mods)
        self.assertEqual(
            missing, [],
            f"这些可选导入未在 pyproject 任何 extra 中声明: {missing}"
            f"（已声明: {sorted(declared_mods)}）")

    def test_expected_extras_exist_and_map_to_right_modules(self):
        declared = _declared_extra_modules(
            _load_pyproject()["project"]["optional-dependencies"])
        aliases = {"pyyaml": "yaml"}
        for extra, want_mods in EXPECTED_EXTRAS.items():
            self.assertIn(extra, declared,
                          f"extra {extra!r} 缺失（文档/README 已承诺）")
            got = {aliases.get(m, m) for m in declared[extra]}
            self.assertTrue(
                want_mods <= got,
                f"extra {extra!r} 应覆盖 {sorted(want_mods)}，实际 {sorted(got)}")

    def test_entrypoint_uses_run_not_main(self):
        """console script 必须挂 run（main 返回 int，包装器不当退出码）。"""
        d = _load_pyproject()
        scripts = (d.get("project") or {}).get("scripts") or {}
        self.assertEqual(scripts.get("harvester"), "harvester.cli:run",
                         "入口须为 cli:run；挂 cli:main 会让非零退出码静默变 0")

    def test_run_wrapper_propagates_exit_code(self):
        """反向验证上一条的意义：run() 抛 SystemExit 且 code 非 0。

        必须清空 sys.argv——否则 unittest 自身的 argv（如
        ['-m','unittest','tests.test_x']）会被 argparse 当成子命令，
        测试"通过"但通过的是错的原因（同一类"恒真断言"陷阱）。
        """
        sys.path.insert(0, str(ROOT))
        saved = sys.argv
        try:
            from harvester import cli
            sys.argv = ["harvester"]        # 无子命令 → argparse 报错退出
            with self.assertRaises(SystemExit) as cm:
                cli.run()
            self.assertNotEqual(cm.exception.code, 0,
                                "参数错误必须透传非零退出码")
            self.assertEqual(cm.exception.code, 2,
                             "argparse 用法错误的约定退出码为 2")
        finally:
            sys.argv = saved
            sys.path.remove(str(ROOT))


if __name__ == "__main__":
    unittest.main()
