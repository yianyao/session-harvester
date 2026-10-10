# -*- coding: utf-8 -*-
"""flake 猎人（v0.45，SOP C4）：把全量套件连跑 N 轮，报告"哪条测试曾失败"。

为什么需要它：项目里挂着 **1 例未定位的 flake**（沙箱首跑那次）。flake 的本质是
"偶发"，靠人工碰运气复现等于不定位；正确做法是**把复现变成可重跑的动作**，
并在它下次出现时**自动留下测试名与 traceback**（不用回头找日志）。

**两种跑法（轴不同，缺一不可）**：

- 默认（**热跑**，in-process）：同一进程里重复 discover——**专抓跨测试状态泄漏**
  （打桩没还原之类）。首跑就抓到过一例（`test_v06` 的 `[policy]` 断言，根因是另一个
  测试文件打桩了模块级函数没还原）。
- `--cold`（**冷跑**，每轮独立子进程 + 可选清 `__pycache__`）：每轮都是全新解释器
  ——**专抓"首跑/冷启动才出现"的偶发**（历史那例的原始描述正是"沙箱迁移后首跑"，
  热跑轴抓不到这一类）。子进程输出按轮落盘，便于事后逐轮对照。

用法（在仓库根，须先设沙箱补丁）：
  $env:PYTHONPATH = "<repo>\\scripts\\sandbox"
  & $venv -X utf8 scripts\\flake_hunt.py --runs 5 [--cold] [--clear-pycache]
                                    [--out docs/reports/flake-hunt-<日期>.md]

退出码：**0 = N 轮全绿（未复现）**；1 = 至少一条测试曾红（并已在报告里点名）。
注意：这是**开发期工具**，不参与产品链路；它跑的是套件本身，不改任何数据。
"""
from __future__ import annotations

import argparse
import io
import os
import re
import shutil
import subprocess
import sys
import time
import unittest
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

_FAIL_RE = re.compile(r"^(FAIL|ERROR): (\S+)", re.M)


def _collect(stdout: str, stderr: str) -> tuple[int, list[str]]:
    """从 unittest 输出里取 (用例数, 红掉的测试名)。"""
    text = stdout + stderr
    m = re.search(r"^Ran (\d+) tests", text, re.M)
    n = int(m.group(1)) if m else 0
    return n, sorted({name for _, name in _FAIL_RE.findall(text)})


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


def run_cold(stream: io.StringIO, clear_pycache: bool) -> tuple[int, list[str]]:
    """冷跑一轮：**独立子进程**（每轮全新解释器），可选先清 `__pycache__`。

    与热跑的区别是**假设不同**：冷跑覆盖"首跑/冷启动才出现"的偶发（历史那例的
    原始描述即"沙箱迁移后首跑"）。子进程输出一并写进 stream，便于事后逐轮对照。
    """
    os.chdir(REPO)
    if clear_pycache:
        for d in list(REPO.rglob("__pycache__")):
            shutil.rmtree(d, ignore_errors=True)
    r = subprocess.run([sys.executable, "-X", "utf8", "-m", "unittest",
                        "discover", "-s", "tests"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=3600, cwd=str(REPO))
    stream.write(f"\n===== 冷跑（python={sys.executable}，"
                 f"clear_pycache={clear_pycache}，rc={r.returncode}）=====\n")
    stream.write((r.stdout or "") + (r.stderr or ""))
    return _collect(r.stdout or "", r.stderr or "")


def main() -> int:
    ap = argparse.ArgumentParser(description="flake 猎人：连跑全量套件并点名偶发失败")
    ap.add_argument("--runs", type=int, default=3, help="跑几轮（缺省 3）")
    ap.add_argument("--cold", action="store_true",
                    help="冷跑：每轮独立子进程（覆盖'首跑/冷启动'类偶发）")
    ap.add_argument("--clear-pycache", dest="clear_pycache", action="store_true",
                    help="冷跑时每轮先清 __pycache__（更接近真正首跑）")
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
        if args.cold:
            n, red = run_cold(buf, args.clear_pycache)
        else:
            n, red = run_once(buf)
        dt = time.time() - t0
        runs_detail.append(f"- 第 {i} 轮：{n} 例，{'全绿' if not red else '红 ' + ', '.join(red)}"
                           f"（{dt:.1f}s）")
        print(runs_detail[-1])
        hits.update(red)
        if red:                      # 红的那轮把细节一并留档（输出已在 buf 里）
            buf.write(f"\n===== 第 {i} 轮失败明细（见上）=====\n")

    mode = "冷跑（独立子进程" + ("＋清 __pycache__" if args.clear_pycache else "")
    lines = ["# flake 猎人报告", "",
             f"- 轮数 **{args.runs}**｜模式：**{mode if args.cold else '热跑（同进程）'}**"
             f"｜报告时间 {time.strftime('%Y-%m-%d %H:%M:%S')}",
             f"- 结论：**{'未复现（N 轮全绿）' if not hits else '复现到偶发失败'}**", "",
             "## 每轮结果", ""] + runs_detail
    if hits:
        lines += ["", "## 偶发失败点名（出现轮数）", ""]
        lines += [f"- {k}：{v}/{args.runs} 轮" for k, v in hits.most_common()]
        lines += ["", "## 明细", "", "```", buf.getvalue().strip(), "```"]
    else:
        lines += ["", "仍按既有口径记「未定位」：本工具已就绪，",
                  "下次它出现时会自动点名并留 traceback，不必翻日志。",
                  "", "**两种轴都要跑过才算有界尝试**：热跑抓跨测试状态泄漏，",
                  "冷跑抓'首跑/冷启动'类偶发（历史那例的原始描述属后者）。"]
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
