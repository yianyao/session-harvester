# -*- coding: utf-8 -*-
"""v0.45 卫生门（A 组）：① 发布版本号 ↔ 交接快照对账；② f-string 无占位符。

两条都是"改一处忘改另一处"型缺陷，且都曾被第三方评审点名：

- **版本号**：`harvester/__init__.py` 长期停在 `0.18.1`，而真实版本已到 v0.45；
  `apiserve.api_meta.package_version` 与 `mcpserver.SERVER_INFO.version` 都取自它
  ——"产物可对账"的起点是错的。
- **f-string 无占位符**：`f"纯中文"` 既不是占位也不要格式化，属笔误级噪声；
  光靠人看会回潮，故用 AST 静态检查钉住。

判据（都能说清何时会红）：
- 交接 §0 快照写 `v0.46` 而 `__version__` 没跟上 → 版本测试红；
- 任何 `harvester/` 下出现无占位符的 f-string → 卫生测试红（含元测试：
  拿合成源码喂检查器，必须报出来，证明检查本身不是恒真）。
"""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

import harvester

REPO = Path(__file__).resolve().parents[1]
HANDOFF = REPO / "docs" / "HANDOFF-v0.33-next.md"


def fstring_issues(src: str, name: str = "<src>") -> list[str]:
    """列出源码里**不含任何占位符**的 f-string（AST 口径）。

    **必须排除 `FormattedValue.format_spec`**：Python 3.12+（PEP 701）把格式说明符
    也建成了 `JoinedStr`——`f"{x:04d}"` 的 `04d` 就是一个无占位符的 JoinedStr。
    首版没排除，30 条"违规"里 26 条是假阳性（本仓库 venv 为 3.13）。
    """
    tree = ast.parse(src)
    specs = {id(n.format_spec) for n in ast.walk(tree)
             if isinstance(n, ast.FormattedValue) and n.format_spec is not None}
    out: list[str] = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.JoinedStr) and id(node) not in specs
                and not any(isinstance(v, ast.FormattedValue)
                            for v in node.values)):
            out.append(f"{name}:{node.lineno}")
    return out


def release_version_in_handoff(text: str) -> str | None:
    """从交接 §0 快照里抽"当前发布线"版本号：形如 `→ **v0.45**`。"""
    m = re.search(r"→\s*\*\*v(\d+\.\d+)(?:\.\d+)?\*\*", text)
    return m.group(1) if m else None


class TestReleaseVersion(unittest.TestCase):

    def test_version_matches_handoff_snapshot(self):
        """交接 §0 的发布线版本号与 `__version__` 必须一致（前两位）。"""
        want = release_version_in_handoff(
            HANDOFF.read_text(encoding="utf-8"))
        self.assertIsNotNone(want, "交接 §0 里找不到 `→ **vX.Y**` 形式的版本号")
        self.assertEqual(
            harvester.__version__.rsplit(".", 1)[0], want,
            f"harvester/__init__.py 的 __version__={harvester.__version__!r} "
            f"与交接快照 v{want} 不一致")

    def test_version_is_semver_and_not_stale(self):
        """必须是三段式，且不许停在评审点名过的过期值。"""
        self.assertRegex(harvester.__version__, r"^\d+\.\d+\.\d+$")
        self.assertNotEqual(harvester.__version__, "0.18.1")


class TestFStringHygiene(unittest.TestCase):

    def test_checker_can_fail(self):
        """元测试：检查器对合成源码必须报出问题（否则这条门恒真）。"""
        self.assertEqual(fstring_issues('x = f"abc"\n'), ["<src>:1"])
        self.assertEqual(fstring_issues('x = f"a{b}"\n'), [])

    def test_checker_ignores_format_spec(self):
        """元测试（回归）：`{x:04d}` 的格式说明符在 3.12+ 也是 JoinedStr，
        **不是**违规——首版把它算进去，30 条里 26 条是假阳性。"""
        self.assertEqual(fstring_issues('x = f"{n:04d}"\n'), [])
        self.assertEqual(fstring_issues('x = f"{v:>{w}}"\n'), [])
        self.assertEqual(fstring_issues('x = f"纯文字"\n'), ["<src>:1"])

    def test_harvester_has_no_placeholderless_fstring(self):
        issues: list[str] = []
        for p in sorted((REPO / "harvester").rglob("*.py")):
            issues += fstring_issues(p.read_text(encoding="utf-8"),
                                     str(p.relative_to(REPO)))
        self.assertEqual(issues, [], "存在无占位符的 f-string：\n  "
                         + "\n  ".join(issues))


if __name__ == "__main__":
    unittest.main()
