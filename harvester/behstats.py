# -*- coding: utf-8 -*-
"""Skill 行为画像（report-skill 子命令）——G4 的确定性主干。

对应《轨迹分析工具设计方案》G4"行为画像"中用户最关心的切面：
**按 skill 分类，抽取每个 skill 的操作行为模式与决策触发上下文**。

数据来源：steps 表中 skill 调用步骤——
- tool in {Skill, skill}（WorkBuddy 系 harness），detail JSON 含
  "skill"（或 "command"）= skill 名、"args" = 触发任务描述；
- tool = skill_read_active（AutoClaw），detail JSON 的 path 指向技能
  目录下文件，取首段目录名近似 skill 名（启发式，注明）。

每个调用点产出：
- skill 名 / 触发 args（决策触发上下文）/ 来源 harness / 状态（与
  紧随的 result 步骤配对）；
- 调用后行为链：该点之后最多 12 步的 call 工具序列（相邻去重、截 8 个）
  ——即"用了这个 skill 之后 agent 实际做了什么"。

"决策过程"的语义提炼（为什么这时用这个 skill、决策依据）是 LLM 工作：
本报告输出带锚点的深挖清单（--skill <name>），供把相关会话喂给 Agent
蒸馏成知识卡片（见 docs/CARD_WORKFLOW.md）。
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

SKILL_TOOLS = {"Skill", "skill"}
_AFTER_STEPS = 12     # 行为链最多回看的 call 步数
_CHAIN_LEN = 8        # 行为链展示截断
_MAX_ANCHORS = 10     # 报告每技能最多列的锚点数


def _skill_name_from_detail(tool: str, detail: str | None) -> tuple[str, str]:
    """从 call 步骤 detail 提取 (skill 名, args)。解析失败返回 ('', '')。"""
    if not detail:
        return "", ""
    try:
        d = json.loads(detail)
    except (ValueError, TypeError):
        return "", ""
    if not isinstance(d, dict):
        return "", ""
    if tool == "skill_read_active":  # AutoClaw：path 取技能目录首段
        path = str(d.get("path") or "")
        parts = re.split(r"[\\/]+", path)
        if len(parts) >= 2 and parts[0]:
            return parts[0], path
        return "", ""
    name = d.get("skill") or d.get("command") or d.get("name") or ""
    return str(name), str(d.get("args") or "")


def collect_skill_invocations(db: Path,
                              con: sqlite3.Connection | None = None
                              ) -> list[dict]:
    """扫描 steps 表，产出全部 skill 调用点（含配对状态与行为链）。

    返回列表每项：{sid, seq, ts, source, skill, args, status, error,
    after_tools}；按 (sid, seq) 有序。

    con：外部连接（api-serve 传入 mode=ro + authorizer 连接，使 steps
    全表扫描同样处于内核级只读之下）；缺省自开普通连接（CLI 兼容）。
    """
    own = con is None
    if own:
        con = sqlite3.connect(str(db))
        con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT st.sid, st.seq, st.ts, st.tool, st.phase, st.status, "
            "st.error, st.detail, s.source "
            "FROM steps st LEFT JOIN sessions s ON st.sid = s.sid "
            "ORDER BY st.sid, st.seq").fetchall()
    finally:
        if own:
            con.close()
    invocations: list[dict] = []
    by_sid: dict[str, list] = defaultdict(list)
    for r in rows:
        by_sid[r["sid"]].append(r)
    for sid, srows in by_sid.items():
        pending: dict | None = None   # 已 call 未 result 的调用点
        current: dict | None = None   # 已配对、正在收集行为链的调用点
        for r in srows:
            if r["phase"] == "call" and r["tool"] in SKILL_TOOLS | \
                    {"skill_read_active", "skill_run_asset"}:
                if pending:  # 上一个 call 没有 result（中断）
                    pending["status"] = "no_result"
                    invocations.append(pending)
                current = None  # 新 skill 调用终结上一个调用点的行为链
                name, args = _skill_name_from_detail(r["tool"], r["detail"])
                pending = {
                    "sid": sid, "seq": r["seq"], "ts": r["ts"],
                    "source": r["source"] or "?", "skill": name,
                    "args": args, "status": "no_result", "error": "",
                    "after_tools": [], "_tool": r["tool"],
                }
            elif r["phase"] == "result" and pending is not None and \
                    r["tool"] == pending["_tool"]:
                pending["status"] = r["status"] or "unknown"
                pending["error"] = r["error"] or ""
                invocations.append(pending)
                current = pending
                pending = None
            elif r["phase"] == "call" and pending is None and \
                    current is not None and \
                    len(current["after_tools"]) < _AFTER_STEPS:
                current["after_tools"].append(r["tool"])
        if pending:
            pending["status"] = "no_result"
            invocations.append(pending)
    for inv in invocations:
        inv.pop("_tool", None)
        chain: list[str] = []
        for t in inv["after_tools"]:
            if not chain or chain[-1] != t:
                chain.append(t)
        inv["after_tools"] = chain[:_CHAIN_LEN]
    return invocations


def skill_summary(invocations: list[dict]) -> dict[str, dict]:
    """按 skill 聚合：调用数/会话数/来源/状态/args 样本/行为链 top。"""
    out: dict[str, dict] = {}
    for inv in invocations:
        name = inv["skill"] or "?"
        d = out.setdefault(name, {
            "calls": 0, "sids": set(), "sources": set(),
            "ok": 0, "err": 0, "no_result": 0,
            "args_samples": [], "chains": defaultdict(int), "anchors": [],
        })
        d["calls"] += 1
        d["sids"].add(inv["sid"])
        d["sources"].add(inv["source"])
        st = inv["status"]
        if st in ("success", "completed", "ok"):
            d["ok"] += 1
        elif st == "no_result":
            d["no_result"] += 1
        else:
            d["err"] += 1
        if inv["args"] and len(d["args_samples"]) < 3:
            d["args_samples"].append(inv["args"][:80])
        if inv["after_tools"]:
            d["chains"]["→".join(inv["after_tools"])] += 1
        if len(d["anchors"]) < _MAX_ANCHORS:
            d["anchors"].append(f"{inv['sid']}#{inv['seq']}")
    for d in out.values():
        d["sids"] = len(d["sids"])
        d["sources"] = sorted(d["sources"])
        d["chains"] = dict(sorted(d["chains"].items(),
                                  key=lambda kv: -kv[1])[:5])
    return out


def render_skill_report(invocations: list[dict],
                        skill_filter: str | None = None) -> str:
    """渲染 Markdown：总览 + 每技能画像；--skill 时渲染全量调用清单（深挖）。"""
    if skill_filter:
        invocations = [i for i in invocations if i["skill"] == skill_filter]
        if not invocations:
            return (f"# Skill 行为画像：{skill_filter}\n\n"
                    "steps 表中没有该 skill 的调用记录。\n")
        lines = [f"# Skill 行为画像：{skill_filter}（深挖清单）", "",
                 f"共 {len(invocations)} 次调用。以下每行可交给 Agent 按"
                 "锚点读取原会话，蒸馏该 skill 的决策过程与行为模式（→ 卡片）。", ""]
        for inv in invocations:
            chain = "→".join(inv["after_tools"]) or "（无后续工具步）"
            args = inv["args"] or inv["sid"].split("::")[-1][:60]
            lines.append(
                f"- `{inv['sid']}#{inv['seq']}` [{inv['source']}] "
                f"{inv['status']}｜任务：{args}｜后续：{chain}")
        return "\n".join(lines) + "\n"
    if not invocations:
        return ("# Skill 行为画像（G4）\n\nsteps 表中没有 skill 调用记录。\n"
                "说明：Chat 平台会话无工具遥测；本报告只覆盖 agent harness 会话。\n")
    summary = skill_summary(invocations)
    ok_total = sum(d["ok"] for d in summary.values())
    lines = [
        "# Skill 行为画像（G4）", "",
        f"- skill {len(summary)} 个｜调用 {len(invocations)} 次｜"
        f"成功 {ok_total}（{ok_total / len(invocations) * 100:.0f}%）"
        f"｜失败 {sum(d['err'] for d in summary.values())}"
        f"｜无结果 {sum(d['no_result'] for d in summary.values())}", "",
        "- 数据源：steps 表的 Skill/skill/skill_read_active 步骤；"
        "状态与紧随的 result 配对。'行为链'=调用后前 8 步工具序列（相邻去重）。", "",
    ]
    for name in sorted(summary, key=lambda k: -summary[k]["calls"]):
        d = summary[name]
        lines += [f"## {name}", ""]
        lines.append(f"- 调用 {d['calls']} 次 / {d['sids']} 个会话"
                     f"（来源: {', '.join(d['sources'])}）｜成功 {d['ok']}"
                     f" 失败 {d['err']} 无结果 {d['no_result']}")
        if d["args_samples"]:
            lines.append("- 触发任务抽样（决策触发上下文）:")
            for a in d["args_samples"]:
                lines.append(f"  - 「{a}」")
        if d["chains"]:
            lines.append("- 调用后行为链（高频模式）:")
            for chain, n in d["chains"].items():
                lines.append(f"  - x{n}  {chain}")
        lines.append("- 锚点: " + " ".join(f"`{a}`" for a in d["anchors"]))
        lines.append("")
    lines += [
        "## 决策过程怎么蒸馏？", "",
        "行为链是\"做了什么\"（确定性）；\"为什么这时用/怎么决策\"是语义工作：",
        "",
        "    python -m harvester report-skill --db harvester.db "
        "--skill <名>   # 深挖清单",
        "",
        "把清单中的会话喂给 Agent（或用 `read`/`pack` 读取），按 "
        "`docs/CARD_WORKFLOW.md` 蒸馏成知识卡片（type: workflow / insight）。",
    ]
    return "\n".join(lines).rstrip() + "\n"
