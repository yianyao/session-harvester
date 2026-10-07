# -*- coding: utf-8 -*-
"""错误三分类统计（report-errors 子命令）——G2 的确定性一半。

对应《轨迹分析工具设计方案》§6 report-errors 规格：
- 错误三分类：env / tool_interface / context（正则映射，规则序即优先级）；
- 按轨迹位置分桶：开场 / 中途 / 收尾（以错误步骤 seq 在全会话 steps 中的
  相对位置近似，steps 无用户轮次边界）。

三分类语义（写入代码与文档，勿混）：
- env            环境问题：沙箱/权限撤销/OS/网络/宿主限制——agent 无法自愈；
- tool_interface 工具用法问题：参数错、先改后读、盲猜行号——改用法即消；
- context        目标状态与预期不符：文件已删/资产失效——需重新侦察目标；
- unclassified   未命中任何规则，报告中显式列出（不许静默吞掉）。

模式归一：把同类错误的差异部分（路径/引号内容/数字）替换为占位符后聚类，
使 "cannot edit a.md" 与 "cannot edit b.py" 聚成同一模式。
"""

from __future__ import annotations

import re
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

CLASSES = ("env", "tool_interface", "context", "unclassified")

# 分类规则：列表序 = 判定优先级。先 env 后 context 是刻意的——
# "old_string was not found in <path>" 必须先被 tool_interface 的 old_string
# 规则截住，不能落进 context 的 "not found"。
_RULES: list[tuple[str, list[str]]] = [
    ("env", [
        r"tool_permission_revoked",
        r"sandbox",
        r"Win32|SetNamedSecurityInfo",
        r"Permission to use",
        r"fetch failed|socket hang up|ECONN|ETIMEDOUT|\bnetwork\b",
        r"web fetch",
        r"timeout|timed out",
        r"browser_instance_unknown|browser_cancelled",
        r"host_bridge_declared_error",
        r"exceeds maximum allowed tokens",
        r"LOCKED|EACCES|EPERM|permission denied|access denied",
        r"secret_detected",  # 安全护栏拦截（artifact_secret_detected）
        r"拒绝访问",
    ]),
    ("tool_interface", [
        r"String to replace not found|old_string",
        r"file changed since it was read|has not been read|not been read yet",
        r"Found \d+ matches|replace_all",
        r"match_not_found",  # workspace_edit_match_not_found 等编辑类
        r"out of range",
        r"exceeds max|exceeds maxDepth",
        r"Edit error on",
        r"(?<!skill_asset_)invalid|unsupported|malformed",
        r"regex parse error",  # grep 正则写错
        r"multiple_in_progress",  # checklist 状态冲突（用法时序）
        r"No files were searched",  # rg 过滤器把目标全滤掉了
    ]),
    ("context", [
        r"file no longer exists|File not found|does not exist|不存在",
        r"not found|找不到|os error 2",  # 目标缺失（edit 老串已被上层规则截住）
        r"skill_asset",  # 资产失效/不可执行
        r"obscured",  # 页面元素被遮挡（目标状态变了）
        r"unknown job",  # 引用的后台任务已不存在
        r"filesystem_not_found",
        r"IO error",  # rg IO 错误（路径失效/不可达）
        r"plan_changes_requested",  # 用户要求改方案（目标变了）
    ]),
]

_COMPILED = [(cls, [re.compile(p, re.IGNORECASE) for p in pats])
             for cls, pats in _RULES]

# 模式归一用
_PATH_RE = re.compile(r"[A-Za-z]:[\\/][^\s\"']+")
_QUOTED_RE = re.compile(r"\"[^\"]*\"|'[^']*'")
_NUM_RE = re.compile(r"\d+")
_WS_RE = re.compile(r"\s+")


def classify_error(text: str) -> str:
    """返回错误三分类。规则序即优先级（见 _RULES 注释）。"""
    for cls, regs in _COMPILED:
        for reg in regs:
            if reg.search(text):
                return cls
    return "unclassified"


def normalize_error(text: str) -> str:
    """把同类错误的差异部分替换为占位符，产出聚类键。"""
    s = text.splitlines()[0] if text else ""
    s = re.sub(r"^Error:\s*", "", s)
    s = _QUOTED_RE.sub("<q>", _PATH_RE.sub("<path>", s))
    s = _NUM_RE.sub("N", s)
    s = _WS_RE.sub(" ", s).strip()
    return s[:100]


def _bucket(ratio: float) -> str:
    if ratio <= 1 / 3:
        return "开场"
    if ratio <= 2 / 3:
        return "中途"
    return "收尾"


def collect_errors_from_db(db: Path, since_days: float | None = None,
                           con: sqlite3.Connection | None = None
                           ) -> tuple[list[dict], dict]:
    """从 steps 表取全部错误步骤并归类。

    返回 (errors, meta)：errors 每项含 sid/seq/ts/tool/error/class/pattern/
    bucket；meta = {"total_steps": n, "error_count": n, "sessions": n,
    "since": cutoff 或 None}。
    --since_days 只统计最近 N 天（ts 为 'YYYY-MM-DD HH:MM:SS' 字符串，
    字典序即时间序）。
    con：外部连接（api-serve 传入 mode=ro + authorizer 连接，使 steps
    全表扫描同样处于内核级只读之下）；缺省自开普通连接（CLI 兼容）。
    传入时 db 参数被忽略。
    """
    cutoff = None
    if since_days is not None:
        cutoff = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(time.time() - since_days * 86400))
    own = con is None
    if own:
        con = sqlite3.connect(str(db))
        con.row_factory = sqlite3.Row
    try:
        if cutoff:
            rows = con.execute(
                "SELECT st.sid, st.seq, st.ts, st.tool, st.error, s.source "
                "FROM steps st LEFT JOIN sessions s ON st.sid = s.sid "
                "WHERE st.status=? AND st.ts>=? ORDER BY st.sid, st.seq",
                ("error", cutoff)).fetchall()
            total = con.execute(
                "SELECT COUNT(*) FROM steps WHERE ts>=?", (cutoff,)).fetchone()[0]
        else:
            rows = con.execute(
                "SELECT st.sid, st.seq, st.ts, st.tool, st.error, s.source "
                "FROM steps st LEFT JOIN sessions s ON st.sid = s.sid "
                "WHERE st.status=? ORDER BY st.sid, st.seq", ("error",)).fetchall()
            total = con.execute("SELECT COUNT(*) FROM steps").fetchone()[0]
        max_seq = dict(con.execute(
            "SELECT sid, MAX(seq) FROM steps GROUP BY sid").fetchall())
    finally:
        if own:
            con.close()
    errors = []
    for r in rows:
        hi = max_seq.get(r["sid"], 0) or 1
        ratio = r["seq"] / hi
        errors.append({
            "sid": r["sid"], "seq": r["seq"], "ts": r["ts"],
            "tool": r["tool"], "error": r["error"] or "",
            "source": r["source"] or "?",
            "class": classify_error(r["error"] or ""),
            "pattern": normalize_error(r["error"] or ""),
            "bucket": _bucket(ratio),
        })
    meta = {"total_steps": total, "error_count": len(errors),
            "sessions": len({e["sid"] for e in errors}), "since": cutoff}
    return errors, meta


def pattern_stats(errors: list[dict]) -> dict[str, dict]:
    """按归一模式聚类：pattern → {class, count, tools, samples[(sid,seq,text)]}。"""
    out: dict[str, dict] = {}
    for e in errors:
        p = out.setdefault(e["pattern"], {
            "class": e["class"], "count": 0, "tools": set(), "samples": []})
        p["count"] += 1
        p["tools"].add(e["tool"])
        if len(p["samples"]) < 3:
            p["samples"].append((e["sid"], e["seq"], e["error"]))
    return out


def render_report(errors: list[dict], meta: dict,
                  max_detail: int = 12) -> str:
    """渲染 Markdown 报告：三分类分布 + 位置分桶 + 模式明细（带锚点）。"""
    title = "错误三分类报告"
    if meta.get("since"):
        title += f"（最近 {meta['since']} 起）"
    if not errors:
        return (f"# {title}\n\nsteps 表无错误记录"
                f"（总步骤 {meta.get('total_steps', 0)}）。\n")
    by_class: dict[str, int] = defaultdict(int)
    by_bucket: dict[str, int] = defaultdict(int)
    for e in errors:
        by_class[e["class"]] += 1
        by_bucket[e["bucket"]] += 1
    n = len(errors)
    lines = [
        f"# {title}", "",
        f"- 错误步骤 {n} / 总步骤 {meta['total_steps']}"
        f"（失败率 {n / max(meta['total_steps'], 1) * 100:.1f}%），"
        f"涉及 {meta['sessions']} 个会话", "",
        "## 三分类分布", "",
        "| 类别 | 次数 | 占比 | 语义 |",
        "|---|---:|---:|---|",
        "| env | %d | %.0f%% | 环境：沙箱/权限/OS/网络/宿主限制，agent 无法自愈 |" % (
            by_class["env"], by_class["env"] / n * 100),
        "| tool_interface | %d | %.0f%% | 用法：参数错/先改后读/盲猜行号，改用法即消 |" % (
            by_class["tool_interface"], by_class["tool_interface"] / n * 100),
        "| context | %d | %.0f%% | 目标状态：文件已删/资产失效，需重新侦察 |" % (
            by_class["context"], by_class["context"] / n * 100),
        "| unclassified | %d | %.0f%% | 未命中规则，需人工归因后补规则 |" % (
            by_class["unclassified"], by_class["unclassified"] / n * 100),
        "",
        "## 轨迹位置分桶", "",
        "| 开场（前 1/3） | 中途（中 1/3） | 收尾（后 1/3） |",
        "|---:|---:|---:|",
        "| %d | %d | %d |" % (by_bucket["开场"], by_bucket["中途"],
                              by_bucket["收尾"]),
        "",
        "- 口径：错误步骤 seq 在全会话 steps 中的相对位置（无用户轮次边界，"
        "近似桶）。开场高发=环境/权限未就绪；收尾高发=收尾动作踩坑。",
    ]
    # 按 Agent（数据源）分布：steps 只来自 agent harness 会话
    by_src: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for e in errors:
        by_src[e["source"]][e["class"]] += 1
    if len(by_src) > 1:
        lines += [
            "",
            "## 按 Agent（数据源）分布",
            "",
            "| 数据源 | env | tool_interface | context | unclassified |",
            "|---|---:|---:|---:|---:|",
        ]
        for src in sorted(by_src, key=lambda s: -sum(by_src[s].values())):
            d = by_src[src]
            lines.append("| %s | %d | %d | %d | %d |" % (
                src, d["env"], d["tool_interface"], d["context"],
                d["unclassified"]))
        lines += [
            "",
            "- 说明：数据源即 Agent Harness 身份（如 workbuddy-transcript=WorkBuddy、"
            "dsh=DSH、autoclaw、vscode-copilot）；Chat 平台会话无工具遥测，不产生错误步骤。",
        ]
    # 模式明细，按 class 分组、count 降序
    stats = pattern_stats(errors)
    order = sorted(stats.items(), key=lambda kv: (-kv[1]["count"], kv[0]))
    lines += ["", "## 错误模式明细（归一聚类，带锚点）", ""]
    for cls in CLASSES:
        items = [(p, d) for p, d in order if d["class"] == cls]
        if not items:
            continue
        lines.append(f"### {cls}")
        for pat, d in items[:max_detail]:
            tools = "/".join(sorted(d["tools"]))
            lines.append(f"- **x{d['count']}** [{tools}] `{pat}`")
            for sid, seq, raw in d["samples"][:2]:
                lines.append(f"      - `{sid}#{seq}` {raw[:140]}")
        if len(items) > max_detail:
            lines.append(f"- （其余 {len(items) - max_detail} 个模式从略）")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
