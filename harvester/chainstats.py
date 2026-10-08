# -*- coding: utf-8 -*-
"""工具链失败翼检测（report-chains 子命令）——确定性，全部从 steps 算。

SOP-P2-1 五种检测（H14：全库首个实现，2026-10-08）：

1. 长回合：会话步数 > 全库 p95（步数 = steps 行数，call+result 均计）；
2. 同工具连击：连续 ≥5 个 call 相为同一工具。步进单位取 call 相——
   正常形态 call/result 交替、同工具 call→result 相邻同行是成对出现，
   若按原始行计会把阈值稀释一半；
3. 序列循环：call 序列相邻去重后，A↔B 交替完整周期 ≥3
   （A,B,A,B = 2 周期不报；拖尾半个周期不计入）；
4. 空转：同一工具连续 error 结果 ≥5 个 = 首错 + 重试 >3 仍全败
   （重试口径对齐 toolstats：error 后同会话同工具再调用；中间任何
   非 error 结果即断链）；
5. 高步会话 Top N：按步数降序。

合成注入是验收红线（SOP §5.6 防恒真测试）：40 步同工具合成会话必须报出。
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

STREAK_MIN = 5    # 连击：连续同工具 call 数
CYCLE_MIN = 3     # 循环：A↔B 完整周期数
SPIN_MIN = 5      # 空转：连续 error 结果数（首错 + 重试 >3）
TOP_N = 10


def _pct(ds: list[int], q: float) -> int:
    if not ds:
        return 0
    ds = sorted(ds)
    return ds[min(len(ds) - 1, int(q * (len(ds) - 1)))]


def collect(db: Path, since_days: float | None = None,
            top: int = TOP_N) -> dict:
    """扫描 steps 表做五种检测。只读，不写库。"""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        cutoff = None
        if since_days is not None:
            cutoff = time.strftime(
                "%Y-%m-%d %H:%M:%S",
                time.localtime(time.time() - since_days * 86400))
        sql = "SELECT sid, seq, tool, phase, status FROM steps"
        args: tuple = ()
        if cutoff:
            sql += " WHERE ts >= ?"
            args = (cutoff,)
        sql += " ORDER BY sid, seq"
        rows = con.execute(sql, args).fetchall()
    finally:
        con.close()

    by_sid: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        by_sid.setdefault(r["sid"], []).append(r)

    counts = {sid: len(rs) for sid, rs in by_sid.items()}
    p95 = _pct(list(counts.values()), 0.95) if counts else 0

    long_sessions = sorted(
        ((sid, n) for sid, n in counts.items() if n > p95),
        key=lambda kv: -kv[1])

    streaks: list[tuple[str, str, int, int]] = []
    loops: list[tuple[str, str, int, int]] = []
    spins: list[tuple[str, str, int]] = []
    err_results = 0
    result_rows = 0

    for sid, rs in by_sid.items():
        calls = [(r["tool"], r["seq"]) for r in rs if r["phase"] == "call"]

        # 2) 连击：连续同工具 call
        i = 0
        while i < len(calls):
            j = i
            while j + 1 < len(calls) and calls[j + 1][0] == calls[i][0]:
                j += 1
            if j - i + 1 >= STREAK_MIN:
                streaks.append((sid, calls[i][0], calls[i][1], j - i + 1))
            i = j + 1

        # 3) 循环：相邻去重后 A↔B 完整周期 ≥CYCLE_MIN
        ded: list[tuple[str, int]] = []
        for tool, seq in calls:
            if not ded or ded[-1][0] != tool:
                ded.append((tool, seq))
        i = 0
        while i + 1 < len(ded):
            if ded[i][0] == ded[i + 1][0]:  # 去重后不会发生，防御
                i += 1
                continue
            k = 1
            while (i + 2 * k + 1 < len(ded)
                   and ded[i + 2 * k][0] == ded[i][0]
                   and ded[i + 2 * k + 1][0] == ded[i + 1][0]):
                k += 1
            if k >= CYCLE_MIN:
                loops.append((sid, f"{ded[i][0]}↔{ded[i + 1][0]}", k,
                              ded[i][1]))
            i += 2 * k  # 跳过已判定的完整周期

        # 4) 空转：同工具连续 error 结果
        run: dict[str, int] = {}
        for r in rs:
            if r["phase"] != "result":
                continue
            result_rows += 1
            tool = r["tool"]
            if r["status"] == "error":
                err_results += 1
                run[tool] = run.get(tool, 0) + 1
            else:
                if run.get(tool, 0) >= SPIN_MIN:
                    spins.append((sid, tool, run[tool]))
                run[tool] = 0
        for tool, n in run.items():
            if n >= SPIN_MIN:
                spins.append((sid, tool, n))

    top_sessions = sorted(counts.items(), key=lambda kv: -kv[1])[:top]
    return {
        "sessions": len(counts),
        "steps": len(rows),
        "p95": p95,
        "long": long_sessions,
        "streaks": sorted(streaks, key=lambda x: (-x[3], x[0])),
        "loops": loops,
        "spins": spins,
        "error_results": err_results,
        "result_rows": result_rows,
        "top": top_sessions,
    }


def render_report(d: dict, title: str = "工具链失败翼报告（report-chains）",
                  top: int = TOP_N) -> str:
    if not d["steps"]:
        return (f"# {title}\n\n无 steps 数据。先运行 `harvester index`"
                " 生成索引库，或确认 --db 指向正确。\n")
    err = d["error_results"]
    res = d["result_rows"]
    lines = [
        f"# {title}", "",
        f"- 会话: {d['sessions']} | 步数: {d['steps']}"
        f" | 全库 p95 步数: {d['p95']}",
        f"- 错误结果: {err}/{res}"
        + (f"（{err / res * 100:.1f}%）" if res else ""),
        "",
    ]
    if d["long"]:
        lines += ["## 长回合（步数 > p95）", "",
                  "| 会话 | 步数 |", "|---|---:|"]
        lines += [f"| {sid} | {n} |" for sid, n in d["long"]]
        lines.append("")
    if d["streaks"]:
        lines += [f"## 同工具连击（连续 ≥{STREAK_MIN} call）", "",
                  "| 会话 | 工具 | 连击 | 起始 seq |",
                  "|---|---|---:|---:|"]
        lines += [f"| {sid} | {tool} | {n} | {seq} |"
                  for sid, tool, seq, n in d["streaks"]]
        lines.append("")
    if d["loops"]:
        lines += [f"## 序列循环（A↔B 完整周期 ≥{CYCLE_MIN}）", "",
                  "| 会话 | 环 | 周期数 | 起始 seq |",
                  "|---|---|---:|---:|"]
        lines += [f"| {sid} | {ring} | {k} | {seq} |"
                  for sid, ring, k, seq in d["loops"]]
        lines.append("")
    if d["spins"]:
        lines += [f"## 空转（同工具连续 error ≥{SPIN_MIN}）", "",
                  "| 会话 | 工具 | 连续错误 |", "|---|---|---:|"]
        lines += [f"| {sid} | {tool} | {n} |" for sid, tool, n in d["spins"]]
        lines.append("")
    if d["top"]:
        lines += [f"## 高步会话 Top {top}", "",
                  "| 会话 | 步数 |", "|---|---:|"]
        lines += [f"| {sid} | {n} |" for sid, n in d["top"]]
        lines.append("")
    return "\n".join(lines)
