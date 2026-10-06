# -*- coding: utf-8 -*-
"""OTel trace 工具统计（report-traces 子命令）——确定性数据，不依赖 LLM。

数据源（2026-10-06 实测核验，~401 个文件）：
  ~/.workbuddy/traces/<pid>/trace_*.json
顶层 {"trace": {...}, "spans": [...]}；span 字段：
  name / type(custom|generation|function|agent) / status(ok|error|
  cancelled|running) / duration(ms) / startedAt / toolName / toolInput /
  toolOutput / error / agentName

统计口径：
- 工具调用 = type=="function" 且带 toolName 的 span（实测与内置工具一一对应；
  mcp_tools 是外层伞 span，generation 是模型调用，均不计入工具）。
- 失败 = status=="error"；取消 = status=="cancelled"（用户中断，是 2.4
  用户画像的信号）；running = 未闭合 span（进程异常退出的痕迹）。
- 耗时 = span.duration（毫秒，宿主已算好）。

与 report-tools（steps 表）的分工：steps 来自会话轨迹内的 function_call/
result（对齐对话轮次，可算重试/放弃）；traces 来自运行时遥测（有精确耗时、
有子代理 agentName 维度）。两者互相校验。
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SpanStats:
    calls: int = 0
    errors: int = 0
    cancelled: int = 0
    running: int = 0
    durations: list[int] = field(default_factory=list)

    @property
    def fail_rate(self) -> float:
        return self.errors / self.calls if self.calls else 0.0

    def p(self, q: float) -> int:
        """分位耗时（毫秒）。无样本返回 0。"""
        if not self.durations:
            return 0
        ds = sorted(self.durations)
        return ds[min(len(ds) - 1, int(q * (len(ds) - 1)))]


@dataclass
class GenStats:
    """generation span（模型调用）汇总。"""
    calls: int = 0
    errors: int = 0
    total_ms: int = 0
    durations: list[int] = field(default_factory=list)


def collect(traces_root: Path) -> tuple[dict[str, SpanStats], GenStats, dict]:
    """扫描 traces 目录。返回 (工具统计, generation 统计, 概要)。

    概要含文件数/span 总数/坏文件数。单文件损坏跳过不中断。
    """
    root = Path(traces_root)
    files = sorted(root.glob("*/*.json")) if root.is_dir() else []
    stats: dict[str, SpanStats] = defaultdict(SpanStats)
    gen = GenStats()
    n_bad = 0
    n_spans = 0
    agents: dict[str, int] = defaultdict(int)
    for p in files:
        try:
            data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError):
            n_bad += 1
            continue
        if not isinstance(data, dict):
            n_bad += 1
            continue
        for s in data.get("spans") or []:
            if not isinstance(s, dict):
                continue
            n_spans += 1
            if s.get("agentName"):
                agents[s["agentName"]] += 1
            stype = s.get("type")
            if stype == "generation":
                gen.calls += 1
                if s.get("status") == "error":
                    gen.errors += 1
                d = s.get("duration")
                if isinstance(d, (int, float)) and d >= 0:
                    gen.total_ms += int(d)
                    gen.durations.append(int(d))
                continue
            if stype != "function" or not s.get("toolName"):
                continue
            st = stats[str(s["toolName"])]
            st.calls += 1
            status = s.get("status")
            if status == "error":
                st.errors += 1
            elif status == "cancelled":
                st.cancelled += 1
            elif status == "running":
                st.running += 1
            d = s.get("duration")
            if isinstance(d, (int, float)) and d >= 0:
                st.durations.append(int(d))
    summary = {"files": len(files), "bad_files": n_bad, "spans": n_spans,
               "agents": dict(agents)}
    return dict(stats), gen, summary


def render_report(stats: dict[str, SpanStats], gen: GenStats,
                  summary: dict, title: str = "OTel Trace 工具统计") -> str:
    if not stats and gen.calls == 0:
        return (f"# {title}\n\n无 trace 数据。确认 {summary.get('files', 0)} "
                "个文件均可解析，或目录配置正确。\n")
    total_calls = sum(s.calls for s in stats.values())
    total_err = sum(s.errors for s in stats.values())
    total_cancel = sum(s.cancelled for s in stats.values())
    lines = [
        f"# {title}", "",
        f"- trace 文件: {summary.get('files', 0)}"
        + (f"（{summary['bad_files']} 个解析失败）" if summary.get("bad_files") else "")
        + f" | span 总数: {summary.get('spans', 0)}",
        f"- 工具调用: {total_calls} | 失败: {total_err}"
        f"（{total_err / total_calls * 100:.1f}%）"
        if total_calls else "- 工具调用: 0",
        f"- 用户取消: {total_cancel}（取消是用户画像信号：哪个环节被打断）",
        "",
        "| 工具 | 调用 | 失败 | 失败率 | 取消 | p50(ms) | p95(ms) |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    order = sorted(stats.items(),
                   key=lambda kv: (-kv[1].fail_rate, -kv[1].calls, kv[0]))
    for tool, st in order:
        lines.append(f"| {tool} | {st.calls} | {st.errors} | "
                     f"{st.fail_rate * 100:.1f}% | {st.cancelled} | "
                     f"{st.p(0.50)} | {st.p(0.95)} |")
    if summary.get("agents"):
        lines += ["", "## 子代理/宿主 span 分布（agentName）", ""]
        for a, n in sorted(summary["agents"].items(), key=lambda kv: -kv[1]):
            lines.append(f"- {a}: {n}")
    if gen.calls:
        gp50 = _pct(gen.durations, 0.50)
        gp95 = _pct(gen.durations, 0.95)
        lines += [
            "", "## 模型调用（generation span）", "",
            f"- 调用: {gen.calls} | 失败: {gen.errors}"
            f"（{gen.errors / gen.calls * 100:.1f}%）",
            f"- 总耗时: {gen.total_ms / 1000:.1f}s"
            f" | p50: {gp50}ms | p95: {gp95}ms",
        ]
    return "\n".join(lines) + "\n"


def _pct(durations: list[int], q: float) -> int:
    if not durations:
        return 0
    ds = sorted(durations)
    return ds[min(len(ds) - 1, int(q * (len(ds) - 1)))]
