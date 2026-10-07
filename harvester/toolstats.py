# -*- coding: utf-8 -*-
"""工具调用统计（report-tools 子命令）——确定性数据，不依赖 LLM。

数据来源：Adapter 产出的 note 消息文本标记（来源无关，任何 adapter 只要
按约定产出 `[tool_call] ...` / `[tool_result] ...` 即自动纳入统计）：
- [tool_call] <工具名>: <输入摘要>
- [tool_result] <工具名>: success|error [错误码/摘要]

产出：每工具调用次数 / 失败次数 / 失败率 / 错误码 TOP，按失败率排序。
用途（对应用户目标 2.2/2.3）：
- 2.2 工具改进：高失败率工具是第一优先改进对象；
- 2.3 Harness 踩坑：错误码（如沙箱拦截、权限撤销）直接暴露环境问题。

设计取舍：从 note 文本解析而非读原始 DB——跨源通用、零额外查询；
错误详情需要更长上下文时，note 的 raw 字段保留原始 payload 可回溯。
"""

from __future__ import annotations

import re
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

_CALL_RE = re.compile(r"^\[tool_call\]\s+([^\s:]+):")
_RESULT_RE = re.compile(r"^\[tool_result\]\s+([^\s:]+):\s+(\S+)\s*(.*)$")


class ToolStats:
    """单工具的调用/结果聚合。"""

    def __init__(self) -> None:
        self.calls = 0
        self.success = 0
        self.error = 0
        self.no_result = 0
        self.errors: dict[str, int] = defaultdict(int)  # 错误码/摘要 → 次数

    @property
    def total(self) -> int:
        return self.calls

    @property
    def fail_rate(self) -> float:
        return self.error / self.calls if self.calls else 0.0


def collect_stats(session_records) -> dict[str, ToolStats]:
    """从可迭代的 SessionRecord 聚合工具统计。

    结果与调用按 (来源, 会话内顺序) 各自计数后配对：本实现取简化口径——
    每个工具的 calls 计 tool_call 数，success/error 计 tool_result 数；
    不做严格 toolCallId 配对（note 文本未含 id；需要严格配对时从
    Message.raw 的 payload 回溯）。

    成败判定与 steps 口径共用 _OK_STATUS（对账约束：两口径结论必须一致，
    见 tests 对账测试）。
    """
    stats: dict[str, ToolStats] = defaultdict(ToolStats)
    for rec in session_records:
        for m in rec.messages:
            if m.role != "note":
                continue
            text = m.text.lstrip()
            mc = _CALL_RE.match(text)
            if mc:
                stats[mc.group(1)].calls += 1
                continue
            mr = _RESULT_RE.match(text)
            if mr:
                tool, status = mr.group(1), mr.group(2)
                st = stats[tool]
                if status in _OK_STATUS:
                    st.success += 1
                else:
                    st.error += 1
                    detail = (mr.group(3) or "").strip() or status
                    st.errors[detail] += 1
    return dict(stats)


# ---- 结构化统计（steps 表，v0.6） ----

#: 成功状态的单一真值源：note 文本口径与 steps 表口径共用，
#: 真实数据里 WorkBuddy/DSH 用 completed、AutoClaw 用 success（同义不同词）。
_OK_STATUS = ("success", "completed", "ok")


def _is_error_status(status: str) -> bool:
    return status not in _OK_STATUS


def collect_stats_from_db(db: Path, since_days: float | None = None,
                          con: sqlite3.Connection | None = None
                          ) -> tuple[dict[str, ToolStats], dict]:
    """从索引库 steps 表聚合（结构化口径，替代 note 文本正则）。

    同时计算重试/放弃（确定性近似口径，steps 无用户轮次边界）：
    - 重试：error 结果之后、同会话内同一工具再次被调用；
    - 放弃：error 结果之后、同会话内该工具未再被调用。
    返回 (stats, flow)，flow = {"retried": n, "given_up": n,
    "errors": 总错误数}。
    --since_days 只统计最近 N 天（ts 'YYYY-MM-DD HH:MM:SS' 字典序即时间序；
    截断处跨界的重试对会漏配对，近似口径可接受）。
    con：外部连接（api-serve 传入 mode=ro + authorizer 连接）；缺省自开
    普通连接（CLI 兼容）。传入时 db 参数被忽略。
    """
    cutoff = None
    if since_days is not None:
        cutoff = time.strftime(
            "%Y-%m-%d %H:%M:%S",
            time.localtime(time.time() - since_days * 86400))
    own = con is None
    if own:
        con = sqlite3.connect(str(db))
        con.row_factory = sqlite3.Row
    try:
        if cutoff:
            rows = con.execute(
                "SELECT sid, seq, ts, tool, phase, status, error FROM steps "
                "WHERE ts>=? ORDER BY sid, seq", (cutoff,)).fetchall()
        else:
            rows = con.execute(
                "SELECT sid, seq, tool, phase, status, error FROM steps "
                "ORDER BY sid, seq").fetchall()
    finally:
        if own:
            con.close()
    stats: dict[str, ToolStats] = defaultdict(ToolStats)
    # 每会话每工具的最后 error 位置，供重试判定
    last_err_seq: dict[tuple[str, str], int] = {}
    flow = {"retried": 0, "given_up": 0, "errors": 0}
    for r in rows:
        tool = r["tool"]
        if r["phase"] == "call":
            st = stats[tool]
            st.calls += 1
            key = (r["sid"], tool)
            if key in last_err_seq:
                flow["retried"] += 1
                del last_err_seq[key]  # 一次重试只计一次
        else:
            st = stats[tool]
            status = r["status"] or "unknown"
            if _is_error_status(status):
                st.error += 1
                flow["errors"] += 1
                detail = (r["error"] or status).strip()
                if detail:
                    st.errors[detail] += 1
                last_err_seq[(r["sid"], tool)] = r["seq"]
            else:
                st.success += 1
    flow["given_up"] = flow["errors"] - flow["retried"]
    return dict(stats), flow


def collect_source_stats(db: Path, since_days: float | None = None,
                         con: sqlite3.Connection | None = None
                         ) -> dict[str, dict[str, ToolStats]]:
    """按数据源（=Agent Harness 身份）分别聚合工具统计。

    steps 只来自 agent harness 会话（workbuddy-transcript/dsh/autoclaw/
    vscode-copilot）；Chat 平台会话无工具遥测，天然不参与。
    注意：工具名词表因 harness 而异（如 Bash/pwsh/exec 都是执行类）。
    con：外部连接（同 collect_stats_from_db 口径）。
    """
    cutoff = None
    if since_days is not None:
        cutoff = time.strftime(
            "%Y-%m-%d %H:%M:%S",
            time.localtime(time.time() - since_days * 86400))
    own = con is None
    if own:
        con = sqlite3.connect(str(db))
        con.row_factory = sqlite3.Row
    try:
        if cutoff:
            rows = con.execute(
                "SELECT st.tool, st.phase, st.status, st.error, s.source "
                "FROM steps st LEFT JOIN sessions s ON st.sid = s.sid "
                "WHERE st.ts>=? ORDER BY st.sid, st.seq", (cutoff,)).fetchall()
        else:
            rows = con.execute(
                "SELECT st.tool, st.phase, st.status, st.error, s.source "
                "FROM steps st LEFT JOIN sessions s ON st.sid = s.sid "
                "ORDER BY st.sid, st.seq").fetchall()
    finally:
        if own:
            con.close()
    by_source: dict[str, dict[str, ToolStats]] = defaultdict(
        lambda: defaultdict(ToolStats))
    for r in rows:
        st = by_source[r["source"] or "?"][r["tool"]]
        if r["phase"] == "call":
            st.calls += 1
        else:
            status = r["status"] or "unknown"
            if _is_error_status(status):
                st.error += 1
                detail = (r["error"] or status).strip()
                if detail:
                    st.errors[detail] += 1
            else:
                st.success += 1
    return dict(by_source)


def collect_model_stats(db: Path, since_days: float | None = None,
                        con: sqlite3.Connection | None = None
                        ) -> dict[str, ToolStats]:
    """按模型聚合工具统计（v0.15：sessions.model，transcript 源提供）。

    返回 {model: ToolStats}；无模型信息的源（model 为 NULL）不参与。
    con：外部连接（同 collect_stats_from_db 口径）。
    """
    cutoff = None
    if since_days is not None:
        cutoff = time.strftime(
            "%Y-%m-%d %H:%M:%S",
            time.localtime(time.time() - since_days * 86400))
    own = con is None
    if own:
        con = sqlite3.connect(str(db))
        con.row_factory = sqlite3.Row
    try:
        sql = ("SELECT st.tool, st.phase, st.status, st.error, s.model "
               "FROM steps st LEFT JOIN sessions s ON st.sid = s.sid ")
        params: list = []
        if cutoff:
            sql += "WHERE st.ts>=? "
            params.append(cutoff)
        sql += "ORDER BY st.sid, st.seq"
        rows = con.execute(sql, params).fetchall()
    finally:
        if own:
            con.close()
    by_model: dict[str, ToolStats] = defaultdict(ToolStats)
    for r in rows:
        model = r["model"]
        if not model:
            continue
        st = by_model[model]
        if r["phase"] == "call":
            st.calls += 1
        else:
            status = r["status"] or "unknown"
            if _is_error_status(status):
                st.error += 1
            else:
                st.success += 1
    return dict(by_model)


def render_model_table(by_model: dict[str, ToolStats]) -> str:
    """渲染"按模型分布"小节。无模型数据时返回空串。"""
    if not by_model:
        return ""
    lines = [
        "",
        "## 按模型分布",
        "",
        "| 模型 | 调用 | 失败 | 失败率 |",
        "|---|---:|---:|---:|",
    ]
    for model in sorted(by_model, key=lambda m: -by_model[m].calls):
        st = by_model[model]
        rate = f"{st.error / st.calls * 100:.1f}%" if st.calls else "-"
        lines.append(f"| {model} | {st.calls} | {st.error} | {rate} |")
    lines += [
        "",
        "- 说明：模型来自会话级行级 providerData 众数（当前仅 "
        "workbuddy-transcript 源提供；同一 harness 会话中途换模型时按"
        "使用最多者归属）。",
    ]
    return "\n".join(lines) + "\n"


def render_source_table(by_source: dict[str, dict[str, ToolStats]]) -> str:
    """渲染"按 Agent（数据源）分布"小节。单源或空时返回空串。"""
    if len(by_source) < 2:
        return ""
    lines = [
        "",
        "## 按 Agent（数据源）分布",
        "",
        "| 数据源 | 工具数 | 调用 | 失败 | 失败率 |",
        "|---|---:|---:|---:|---:|",
    ]
    for src in sorted(by_source, key=lambda s: -sum(
            st.calls for st in by_source[s].values())):
        stats = by_source[src]
        calls = sum(st.calls for st in stats.values())
        errs = sum(st.error for st in stats.values())
        lines.append(f"| {src} | {len(stats)} | {calls} | {errs} | "
                     f"{errs / calls * 100:.1f}% |" if calls else
                     f"| {src} | {len(stats)} | 0 | 0 | - |")
    lines += [
        "",
        "- 说明：数据源即 Agent Harness 身份；工具名词表因 harness 而异"
        "（如 Bash/pwsh/exec 同为执行类）。Chat 平台会话无工具遥测，不在本报告。",
    ]
    return "\n".join(lines) + "\n"


def render_flow(flow: dict) -> list[str]:
    """渲染重试/放弃小节。无错误时返回空。"""
    if not flow.get("errors"):
        return []
    retried, given = flow.get("retried", 0), flow.get("given_up", 0)
    total = flow["errors"]
    return [
        "",
        "## 失败后续行为（重试/放弃）",
        "",
        f"- 失败 {total} 次：重试 {retried}"
        f"（{retried / total * 100:.0f}%）｜ 放弃 {given}"
        f"（{given / total * 100:.0f}%）",
        "- 口径：同会话内失败后同一工具再次被调用=重试，否则=放弃"
        "（近似口径，steps 表无用户轮次边界）。",
        "- 高放弃率 + 高失败率 = 用户路径被环境问题硬性挡断，优先修。",
    ]


def render_report(stats: dict[str, ToolStats], title: str = "工具调用统计",
                  flow: dict | None = None) -> str:
    """渲染 Markdown 报告。无数据时返回可读的空态说明。"""
    if not stats:
        return (f"# {title}\n\n无工具调用数据。需要带 note 的导出：\n\n"
                "    python -m harvester export --all --with-notes ...\n"
                "或索引时 include_notes=True（AutoClaw 源产出 tool_call/"
                "tool_result note）。\n")
    total_calls = sum(s.calls for s in stats.values())
    total_err = sum(s.error for s in stats.values())
    lines = [
        f"# {title}",
        "",
        f"- 工具数: {len(stats)} | 总调用: {total_calls} | 总失败: {total_err}"
        f"（{total_err / total_calls * 100:.1f}%）" if total_calls else
        f"- 工具数: {len(stats)} | 总调用: 0",
        "",
        "| 工具 | 调用 | 成功 | 失败 | 失败率 |",
        "|---|---:|---:|---:|---:|",
    ]
    # 失败率高的排前面；无失败的按调用量排
    order = sorted(stats.items(),
                   key=lambda kv: (-kv[1].fail_rate, -kv[1].calls, kv[0]))
    for tool, st in order:
        lines.append(f"| {tool} | {st.calls} | {st.success} | {st.error} | "
                     f"{st.fail_rate * 100:.1f}% |")
    err_tools = [(t, s) for t, s in order if s.errors]
    if err_tools:
        lines += ["", "## 错误明细（按失败次数排序）", ""]
        for tool, st in err_tools:
            lines.append(f"### {tool}")
            for detail, n in sorted(st.errors.items(), key=lambda kv: -kv[1]):
                lines.append(f"- x{n} {detail[:160]}")
    if flow:
        lines += render_flow(flow)
    return "\n".join(lines) + "\n"
