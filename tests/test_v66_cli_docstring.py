# -*- coding: utf-8 -*-
"""文档里的可复制命令必须真能跑（真门）。

**为什么需要这道门**：`harvester/cli.py` 的模块 docstring 是使用者最先读到的东西，
里面每条 `python -m harvester ...` 都是**鼓励照抄**的。2026-10-22 实测发现两处
**照抄即失败**、却躲过了所有既有门：

- `report-errors [--since 30d]`：`--since` 是 **float**（天数），`30d` 直接
  `invalid float value`——这条在 docstring 里躺了很久，没人试过；
- 新写的 `attrib [--out 文件]`：`attrib` **没有** `--out`（它只有 `--json`/`--brief`）。

**做法**（不是"整行直喂解析器"——docstring 用的是 `[--opt]` 用法记法，直喂必假红）：
1. 用**真解析器**（`build_parser()`）定位该命令对应的子解析器（支持嵌套，
   如 `cards validate`、`topic md`）；
2. 该行出现的每个 `--opt` 必须是那个子解析器认识的选项 → 抓到 `attrib --out`；
3. 对**数字型**选项，若行里给了字面值，必须能被解析成数字 → 抓到 `--since 30d`。

两条判据都取自真实缺陷，不是想象出来的规则。
"""
import ast
import re
import unittest
from pathlib import Path

from harvester.cli import build_parser

REPO = Path(__file__).resolve().parents[1]

#: 取值型且**数值**的选项：行里若给字面值，必须能转成数字。
NUMERIC_OPTS = {"--since", "--min-count", "--tokens", "--top", "--limit",
                "--n", "--turn", "--max-chars", "--margin-days", "--runs",
                "--seq", "--rowid", "--window", "--days", "--count", "--min",
                "--max", "--top-n", "--depth"}


def _docstring_lines() -> list[str]:
    src = (REPO / "harvester" / "cli.py").read_text(encoding="utf-8")
    doc = ast.get_docstring(ast.parse(src)) or ""
    return [ln.strip() for ln in doc.splitlines()
            if re.match(r"\s*python -m harvester\s+\S", ln)]


def _norm(line: str) -> str:
    """把用法记法变成可分析的词序列：去方括号、选择组取第一个。"""
    s = line.replace("[", " ").replace("]", " ").replace("<", " ").replace(">", " ")
    s = re.sub(r"\{([^}]*)\}", lambda m: re.split(r"[|,]", m.group(1))[0], s)
    return re.sub(r"\s+", " ", s).strip()


def _subparser_of(argv: list[str]):
    """沿嵌套子命令走到底，返回该命令自己的 ArgumentParser（走不到返回 None）。"""
    parser = build_parser()
    i = 0
    while True:
        subs = None
        for act in parser._actions:  # noqa: SLF001 - 探针就是要读真解析器
            if act.__class__.__name__ in ("_SubParsersAction",):
                subs = act.choices
        if not subs:
            return parser
        # 跳过选项与其值，找到第一个位置参数
        while i < len(argv) and argv[i].startswith("-"):
            i += 2
        if i >= len(argv):
            return parser
        nxt = argv[i]
        if nxt not in subs:
            return parser
        parser = subs[nxt]
        i += 1


def _is_placeholder(val: str) -> bool:
    """用法记法里的占位符（`N天` / `N|all|last` / 中文名）——不是字面值，不判它。"""
    return (val.startswith("N") or "|" in val or not val.isascii())


class DocstringCommandsAreRunnable(unittest.TestCase):
    def test_docstring_has_enough_commands(self):
        """前提：抽不到命令，这组门就等于没跑（防正则悄悄失配）。"""
        lines = _docstring_lines()
        self.assertGreaterEqual(len(lines), 20,
                                f"只抽到 {len(lines)} 条，正则可能失配")

    def test_every_option_exists_on_its_parser(self):
        """行里每个 `--opt` 都必须是该命令认识的选项。"""
        bad = []
        for line in _docstring_lines():
            argv = _norm(line).split()[3:]          # 去掉 python -m harvester
            parser = _subparser_of(list(argv))
            known = {s for a in parser._actions for s in a.option_strings}
            for tok in argv:
                if tok.startswith("--") and "|" not in tok and tok not in known:
                    bad.append(f"{line}  →  `{tok}` 不是该命令的选项")
        self.assertEqual(bad, [], "docstring 里的命令用了不存在的选项：\n  "
                         + "\n  ".join(bad))

    def test_numeric_options_have_parseable_values(self):
        """数字型选项的字面值必须能解析（`--since 30d` 这种就是错的）。"""
        bad = []
        for line in _docstring_lines():
            toks = _norm(line).split()[3:]
            for i, tok in enumerate(toks[:-1]):
                if tok not in NUMERIC_OPTS:
                    continue
                val = toks[i + 1]
                if _is_placeholder(val):
                    continue                     # 用法记法里的占位符，不判
                if not re.fullmatch(r"-?\d+(\.\d+)?", val):
                    bad.append(f"{line}  →  `{tok} {val}` 不是数字"
                               "（--since 接受的是天数，写法是 `--since 30`）")
        self.assertEqual(bad, [], "docstring 里数字型选项的值解析不了：\n  "
                         + "\n  ".join(bad))

    def test_no_bare_since_with_d_suffix_anywhere_in_cli(self):
        """`--since 30d` 在**整个 cli.py**（含 docstring/注释）都不许出现。"""
        src = (REPO / "harvester" / "cli.py").read_text(encoding="utf-8")
        hits = [ln.strip() for ln in src.splitlines()
                if re.search(r"--since\s+[0-9]+d\b", ln)]
        self.assertEqual(hits, [], "`--since 30d` 过不了解析器，应写 `--since 30`："
                         + str(hits))


if __name__ == "__main__":
    unittest.main()