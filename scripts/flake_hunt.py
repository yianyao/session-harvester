# -*- coding: utf-8 -*-
"""flake 猎人（v0.45，SOP C4）：把全量套件连跑 N 轮，报告"哪条测试曾失败"。

为什么需要它：项目里挂着 **1 例未定位的 flake**（沙箱首跑那次）。flake 的本质是
"偶发"，靠人工碰运气复现等于不定位；正确做法是**把复现变成可重跑的动作**，
并在它下次出现时**自动留下测试名与 traceback**（不用回头找日志）。

用法（在仓库根，须先设沙箱补丁）：
  $env:PYTHONPATH = "<repo>\\scripts\\sandbox"
  & $venv -X utf8 scripts\\flake_hunt.py --runs 5 [--out docs/reports/flake-hunt.md]

退出码：**0 = N 轮全绿（未复现）**；1 = 至少一条测试曾红（并已在报告里点名）。
注意：这是**开发期工具**，不参与产品链路；它跑的是套件本身，不改任何数据。
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import time
import unittest
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def run_once(stream: io.StringIO) -> tuple[int, list[str]]:
    """跑一轮全量套件，返回 (用例数, 红掉的测试 id 列表)。

    发现方式与 CLI 口径一致：**切到仓库根再 `discover("tests")`**。
    用绝对路径 + `top_level_dir` 反而会抛 "Start directory is not importable"
    （tests/ 不是包，靠 cwd 在 sys.path 上才可发现）——首版即踩这个。
    """
    os.chdir(REPO)
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    suite = unittest.defaultTestLoader.discover("tests")
    res = unittest.TextTestRunner(stream=stream, verbosity=0).run(suite)
    red = [t.id() for t, _ in list(res.failures) + list(res.errors)]
    return res.testsRun, red


def main() -> int:
    ap = argparse.ArgumentParser(description="flake 猎人：连跑全量套件并点名偶发失败")
    ap.add_argument("--runs", type=int, default=3, help="跑几轮（缺省 3）")
    ap.add_argument("--out", default=None, help="报告输出路径（缺省打印摘要）")
    args = ap.parse_args()
    if args.runs < 1:
        print("错误: --runs 必须 >= 1", file=sys.stderr)
        return 2

    hits: Counter[str] = Counter()
    runs_detail: list[str] = []
    buf = io.StringIO()
    for i in range(1, args.runs + 1):
        t0 = time.time()
        n, red = run_once(buf)
        dt = time.time() - t0
        runs_detail.append(f"- 第 {i} 轮：{n} 例，{'全绿' if not red else '红 ' + ', '.join(red)}"
                           f"（{dt:.1f}s）")
        print(runs_detail[-1])
        hits.update(red)
        if red:                      # 红的那轮把 -v 级细节一并留档
            buf.write(f"\n===== 第 {i} 轮失败明细 =====\n")

    lines = ["# flake 猎人报告", "",
             f"- 轮数 **{args.runs}**｜报告时间 {time.strftime('%Y-%m-%d %H:%M:%S')}",
             f"- 结论：**{'未复现（N 轮全绿）' if not hits else '复现到偶发失败'}**", "",
             "## 每轮结果", ""] + runs_detail
    if hits:
        lines += ["", "## 偶发失败点名（出现轮数）", ""]
        lines += [f"- {k}：{v}/{args.runs} 轮" for k, v in hits.most_common()]
        lines += ["", "## 明细", "", "```", buf.getvalue().strip(), "```"]
    else:
        lines += ["", "仍按既有口径记「未定位」：本工具已就绪，",
                  "下次它出现时会自动点名并留 traceback，不必翻日志。"]
    text = "\n".join(lines)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8", newline="\n")
        print(f"报告已写入: {out}")
    else:
        print("\n".join(lines[-6:]))
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
