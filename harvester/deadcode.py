# -*- coding: utf-8 -*-
"""死代码扫描（AST）——把每轮收尾都要跑的那道手检做成工具（v0.37）。

来源与教训：它此前是 `docs/reports/` 下的一次性脚本——但**每轮收尾都跑**（用户
2026-10-10 第 1 点要求：能力进工具本体）。H48 的教训一并保留：扫描范围只覆盖
`harvester/` 会把**测试用到的 API** 误判成死函数，故"名字引用统计"必须覆盖
`harvester/` + `tests/` + 兄弟仓库 `harvester-view/`。

三类检查（**启发式**：会漏、也可能误报，所以默认只提示，判失败要显式加
`--fail-on-found`）：

- **未用 import**：模块内出现过的名字（含属性根名 `json.dumps` 的 `json`，以及
  字符串字面量里的名字，兼容 `getattr`/字符串注解）都没有 → 报了基本就是真没用；
- **未被调用的函数**：函数名在**全仓文本**里只出现 1 次（即定义处）→ 可能死，
  也可能是给外部用的公开 API（导出/`__all__`），故只提示；
- **未用常量**：`UPPER_CASE` 模块级赋值名全仓只出现 1 次。

**为什么用"全仓文本出现次数"而不是调用图**：跨包/动态调用（CLI 的 `cmd_*` 查表、
`argparse` 的 `func=`、框架回调）用静态调用图会大面积误报；文本口径是**保守**的
（别人提一句就不算死）——宁可漏报，也不要误删。

只读：不写任何文件（`--out` 由 CLI 落盘）。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

#: 参与"名字引用统计"的根（存在于仓库时才扫；view 是兄弟仓库，可能不在）
DEFAULT_ROOTS = ("harvester", "tests", "harvester-view")

#: 只对哪些根下的文件报 AST 发现（默认与旧脚本一致：harvester/）
DEFAULT_REPORT_ROOT = "harvester"

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CONST = re.compile(r"[A-Z][A-Z0-9_]{2,}")


def _iter_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*.py")
                  if "__pycache__" not in p.parts)


def _used_names(tree: ast.AST) -> set[str]:
    """模块内"出现过"的名字：Name / 属性名 / 字符串字面量里的词。"""
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    used |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            used |= set(_WORD.findall(node.value))
    return used


def _class_method_names(tree: ast.AST) -> set[str]:
    """类内方法名：可能是父类接口覆盖（如 http.server 的 do_GET），不计死代码。"""
    out: set[str] = set()
    for cls in [n for n in ast.walk(tree)
                if isinstance(n, ast.ClassDef)]:
        for sub in cls.body:
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.add(sub.name)
    return out


def _count(blob: str, name: str) -> int:
    return len(re.findall(r"\b" + re.escape(name) + r"\b", blob))


def _waived(lines: list[str], lineno: int) -> bool:
    """行内带 `noqa` 即视为**有意保留**，不计入。

    真实案例（本工具第一次跑真仓库就遇到）：`dsh.py` 里
    `from compression import zstd as _z  # noqa: F401` —— 它 import 只为**探测
    可用性**（try/except 里 return），名字本就不用，是 flake8 的既有约定。
    工具认这个约定，比另造一套标记更省事。
    """
    if 1 <= lineno <= len(lines):
        return "noqa" in lines[lineno - 1].lower()
    return False


def scan(roots: tuple[str, ...] | list[str] | None = None,
         base: Path | None = None,
         report_root: str = DEFAULT_REPORT_ROOT) -> dict:
    """扫 `base/roots` 下的 .py，返回发现清单。

    `base` 缺省 = 当前目录。`report_root` 下的文件才做 AST 检查，但名字统计涵盖
    所有 roots（跨根引用不会被误判）。
    """
    base = Path(base) if base else Path(".")
    roots = tuple(roots or DEFAULT_ROOTS)
    files: list[Path] = []
    for r in roots:
        files += _iter_files(base / r)
    texts = {p: p.read_text(encoding="utf-8", errors="replace") for p in files}
    blob = "\n".join(texts.values())

    unused_imports: list[str] = []
    uncalled: list[str] = []
    unused_consts: list[str] = []
    for p, src in texts.items():
        rel = p.relative_to(base)
        if rel.parts[0] != report_root:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError as exc:                     # 语法错单独报，不当死代码
            unused_imports.append(f"{rel}: 语法错误，未扫描（{exc.msg}）")
            continue
        used = _used_names(tree)
        methods = _class_method_names(tree)
        lines = src.splitlines()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom) and node.module == "__future__":
                    continue                           # annotations 无名字引用
                if _waived(lines, node.lineno):
                    continue
                for a in node.names:
                    nm = (a.asname or a.name).split(".")[0]
                    if nm not in used:
                        unused_imports.append(
                            f"{rel}:{node.lineno} import {a.name}"
                            + (f" as {a.asname}" if a.asname else ""))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("__") or node.name in methods:
                    continue
                if _count(blob, node.name) <= 1 and not _waived(lines, node.lineno):
                    uncalled.append(f"{rel}:{node.lineno} def {node.name}()")
            elif isinstance(node, ast.Assign) and node.targets:
                tgt = node.targets[0]
                if isinstance(tgt, ast.Name) and _CONST.fullmatch(tgt.id):
                    if (_count(blob, tgt.id) <= 1
                            and not _waived(lines, node.lineno)):
                        unused_consts.append(f"{rel}:{node.lineno} {tgt.id}")
    return {"roots": roots, "report_root": report_root, "files": len(files),
            "unused_imports": sorted(unused_imports),
            "uncalled_functions": sorted(uncalled),
            "unused_constants": sorted(unused_consts)}


def found_total(report: dict) -> int:
    return (len(report["unused_imports"]) + len(report["uncalled_functions"])
            + len(report["unused_constants"]))


def render_deadcode(report: dict) -> str:
    L = [f"# 死代码扫描：{report['files']} 个 py 文件"
         f"（名字统计覆盖 {'、'.join(report['roots'])}；"
         f"只对 {report['report_root']}/ 报 AST 发现）"]
    for title, key in (("未用 import", "unused_imports"),
                       ("未被引用函数", "uncalled_functions"),
                       ("未被引用常量", "unused_constants")):
        items = report[key]
        L.append(f"\n== {title}（{len(items)}）")
        L += [f"  - {x}" for x in items]
    if found_total(report) == 0:
        L.append("\n未发现死代码。")
    else:
        L.append(f"\n合计 {found_total(report)} 条——**启发式提示**：确属给别人"
                 "用的公开 API 或框架回调可保留，但要在评审时说明；"
                 "其余该删（删不掉就说明它其实有人用）。")
    return "\n".join(L) + "\n"
