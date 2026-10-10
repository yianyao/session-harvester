# -*- coding: utf-8 -*-
"""v0.45 跨测试状态泄漏门（SOP C4 的产物）。

**为什么有这道门**：`scripts/flake_hunt.py` 首跑就抓到 `test_v06` 4/5 轮偶发失败，
根因是另一个测试文件对**模块级函数**打桩后没还原（`dshmod.zstd_decompress`）。
这类缺陷有三个要命特征：
1. **单跑一次永远看不见**——discover 顺序让受害者先跑（`test_v06` 在 `test_v45` 之前），
   只有"同进程重复跑"才暴露；
2. 失败点离病灶很远（报 `[policy]` 断言，病灶在别的文件）；
3. 与历史上那例"沙箱首跑 flake 未定位"属**同一类**，故值得机械钉住。

判据（AST 静态，不做运行期探测）：
- 在 `tests/*.py` 里找**对模块级对象属性的赋值**（`obj.attr = …`，且 `obj` 不是 `self`）；
- 该函数内若没有任何还原手段（同函数里再次赋值、`addCleanup`、`mock.patch`、
  `try/finally`）→ **报出来**；
- 允许显式豁免：注释 `# noqa: monkeypatch-leak`（例如"故意永久改动、且只影响本文件顺序"）。

**已知弱点（写在明处，别当成强门）**：还原证据是**文件级**的——文件里只要出现
`addCleanup`/`patch`/`try:finally`，该文件的打桩就都放行；它区分不出"哪个 cleanup
还原的是哪一处"。这是**故意**的取舍：函数级搜索会把大量"打在 setUp、还原在
tearDown"的正确写法报成泄漏（首版即此，假阳性刷屏）。**真正的权威是动态的
`scripts/flake_hunt.py`**（同进程重复跑），本门只拦"连还原手段都没有"的裸泄漏。
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ALLOW = "# noqa: monkeypatch-leak"


def _dotted(node: ast.AST) -> str | None:
    """把 `a.b.c` 形式的属性链还原成字符串。

    **必须要求链上至少有一个 Attribute**：纯 `Name`（`c = …` 这种普通局部变量）
    不是打桩——首版没这道判断，把 `statuses = {...}` 全报成泄漏（假阳性刷屏）。
    """
    parts: list[str] = []
    cur = node
    saw_attr = False
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
        saw_attr = True
    if not saw_attr or not isinstance(cur, ast.Name):
        return None
    parts.append(cur.id)
    return ".".join(reversed(parts))


def _local_names(fn: ast.AST) -> set[str]:
    """函数内**本地绑定**的名字（参数/赋值/for/with）——它们的属性不是模块状态。"""
    names: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store,)):
            names.add(node.id)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            for t in ast.walk(node.target):
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, ast.withitem) and isinstance(node.optional_vars,
                                                           ast.Name):
            names.add(node.optional_vars.id)
    return names


def leak_suspects(src: str, name: str = "<src>") -> list[str]:
    """列出"给**模块级**对象打桩、且文件里找不到还原手段"的位置（`name:行`）。

    两处必须的收敛（首版太宽，假阳性刷屏）：
    1. 目标根名**不能是函数内的本地绑定**——`con.row_factory = sqlite3.Row`
       这种是对局部连接对象设属性，不是打桩；
    2. 还原证据在**整个文件**里找（`addCleanup` / `patch` / `try/finally` /
       同目标再次赋值）——常见写法是打在 `setUp`、还原在 `tearDown`。
    """
    out: list[str] = []
    lines = src.splitlines()
    tree = ast.parse(src)
    file_text = src
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        local = _local_names(fn)
        assigns: list[tuple[ast.Assign, str]] = []
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    dotted = _dotted(tgt)
                    if dotted and dotted.split(".")[0] not in local:
                        assigns.append((node, dotted))
        if not assigns:
            continue
        for node, dotted in assigns:
            line = node.lineno
            if 0 < line <= len(lines) and ALLOW in lines[line - 1]:
                continue
            # 还原证据（文件级）：任一即可
            if ("addCleanup" in file_text or "mock.patch" in file_text
                    or "patch.object" in file_text
                    or ("try:" in file_text and "finally:" in file_text)):
                continue
            if file_text.count(f"{dotted} =") >= 2:      # 打桩 + 还原各一次
                continue
            out.append(f"{name}:{line}")
    return out


class TestNoMonkeypatchLeak(unittest.TestCase):

    #: 已知且**有意**的豁免（每条要能说出为什么安全）。目前为空——
    #: 说明套件里所有对模块级对象的打桩都自带还原手段。
    KNOWN_LEAKS: tuple[str, ...] = ()

    def test_checker_can_fail(self):
        """元测试：泄漏必须被抓到，且 `# noqa` 豁免必须被尊重。"""
        bad = ('import m\n'
               'class T:\n'
               '    def setUp(self):\n'
               '        m.f = lambda: 1\n')
        self.assertEqual(leak_suspects(bad, "x"), ["x:4"])
        ok = ('import m\n'
              'class T:\n'
              '    def setUp(self):\n'
              '        self._orig = m.f\n'
              '        self.addCleanup(lambda: setattr(m, "f", self._orig))\n'
              '        m.f = lambda: 1\n')
        self.assertEqual(leak_suspects(ok, "x"), [])
        allowed = ('import m\n'
                   'class T:\n'
                   '    def test_x(self):\n'
                   '        m.f = 1  # noqa: monkeypatch-leak\n')
        self.assertEqual(leak_suspects(allowed, "x"), [])

    def test_suite_has_no_unrestored_monkeypatch(self):
        found: list[str] = []
        for p in sorted((REPO / "tests").glob("*.py")):
            found += leak_suspects(p.read_text(encoding="utf-8"), p.name)
        found = [f for f in found if f.split(":")[0] not in {
            f"{k.split(':')[0]}" for k in self.KNOWN_LEAKS}]
        self.assertEqual(found, [], "存在打桩未还原的疑似泄漏（跨测试污染）：\n  "
                         + "\n  ".join(found))


if __name__ == "__main__":
    unittest.main()
