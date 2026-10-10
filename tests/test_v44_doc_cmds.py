# -*- coding: utf-8 -*-
"""v0.44 测试：**README 与 CLI 子命令不许漂移**（双向）。

为什么值得钉：全局记忆 §四 14「让文档里的命令被测试真跑」是防文档腐烂最有效的
手段，而这条更前面一步——**命令表本身**。项目历史上这活一直是"人工通读"，
本轮实测才发现此前**从没人量过**（v0.39 我试图抽取时脚本返回 0 个子命令，
结论不可用，只能记"未执行"）。两个方向都要查，且**方向②更危险**：

- ① **CLI 有、README 没写** → 使用者不知道有这个命令（本轮实测 37/37 都有提及）；
- ② **README 写了、CLI 没有** → 使用者照着文档跑，**命令不存在**（实测为 0）。

比对逻辑用**元测试**证明它自己会红（"不能失败的断言等于没断言"）。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: 形如 `harvester xxx` 但不是子命令的词（README 里会出现，属误报要排除）
NOT_COMMANDS = {"cli", "db", "json", "md", "yaml"}


def _subcommands() -> list[str]:
    """从 `harvester/cli.py` 的 `add_parser("...")` 抽子命令名（不跑子进程）。"""
    src = (REPO / "harvester" / "cli.py").read_text(encoding="utf-8")
    return sorted(set(re.findall(r'add_parser\(\s*"([a-z0-9][a-z0-9-]*)"', src)))


def _readme() -> str:
    return (REPO / "README.md").read_text(encoding="utf-8")


def missing_in_readme(subs: list[str], readme: str) -> list[str]:
    """CLI 有、README 里查不到的（**大小写敏感**地按整词查）。"""
    return [s for s in subs if not re.search(r"(?<![\w-])" + re.escape(s)
                                             + r"(?![\w-])", readme)]


def ghost_commands(readme: str, subs: list[str]) -> list[str]:
    """README 里写成 `harvester <x>` 但 CLI 里没有的（排除 NOT_COMMANDS）。"""
    found = re.findall(r"harvester ([a-z][a-z0-9-]+)", readme)
    return sorted({c for c in found if c not in set(subs) and c not in NOT_COMMANDS})


class TestReadmeCliDrift(unittest.TestCase):

    def test_subcommand_extraction_is_sane(self):
        """抽取本身要对（否则下面两条会"因为抽不到而永远绿"——假通过）。"""
        subs = _subcommands()
        self.assertGreaterEqual(len(subs), 30, f"只抽到 {len(subs)} 个：{subs}")
        for must in ("triage", "topic-consolidate", "cards", "regress", "scan"):
            self.assertIn(must, subs)

    def test_every_subcommand_is_documented(self):
        subs = _subcommands()
        miss = missing_in_readme(subs, _readme())
        self.assertEqual(
            miss, [],
            "以下子命令在 CLI 里存在，但 README **一个字都没提**（使用者不知道有它）："
            f"{miss}\n→ 在 README 的命令表里补一行（含用途与最小示例）。")

    def test_no_ghost_commands_in_readme(self):
        subs = _subcommands()
        ghosts = ghost_commands(_readme(), subs)
        self.assertEqual(
            ghosts, [],
            "README 里写成 `harvester <x>` 但 CLI 里**不存在**的命令（照着跑会报错）："
            f"{ghosts}\n→ 若是改写/删掉的命令，清掉文档里的痕迹；"
            "若本来就是非命令词，加进 NOT_COMMANDS 并说明。")

    def test_comparison_itself_can_fail(self):
        """元测试：证明上面两条的比对逻辑**会红**（不是恒真）。"""
        fake = ["zzz-not-a-real-command"]
        self.assertEqual(missing_in_readme(fake, _readme()), fake)
        self.assertEqual(
            ghost_commands("跑 `harvester zzz-not-a-real-command` 即可", subs := _subcommands()),
            ["zzz-not-a-real-command"])
        self.assertEqual(subs, _subcommands())


if __name__ == "__main__":
    unittest.main()
