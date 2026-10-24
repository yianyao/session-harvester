# -*- coding: utf-8 -*-
"""按数据源（=Agent Harness 身份）的体检画像（report-harness 子命令）。

用户对 VIEW 的四大定位之一 = 「实现对 Agent harness 和 skill 的感知与
治理」。本面是 harness 感知的**底座**：每个源一行画像——会话/消息/
步骤/失败率/代表工具/代表 skill/最近活跃，一眼看出哪个 harness 在真
干活、哪个只是聊天平台、哪个错误率异常。

口径纪律（与既有各面一致）：
1. 只读，不写库；全部从 sessions/messages/steps 三张表算，**不猜**；
2. 失败率分母 = result 相步骤数（与 toolstats 的 error 计数同口径），
   不用 call 相（配对失败会让分母虚高）；
3. 工具/skill 排行只取每个源的 Top N（治理看代表形态，不看流水），
   N 之外的条数报出来（「另有 N 件」），不静默截断；
4. 没有工具遥测的源（Chat 平台）不造假：telemetry=false，失败率
   给 null 而非 0——「没有数据」与「没有错误」是两件事；
5. 取消率不在本面：steps.status 无 cancelled 形态，用户取消只在
   OTel traces 有（/api/reports/traces）——两处口径分开说，不混。
"""

from __future__ import annotations

import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path

TOP_N = 5          # 每源代表工具/skill 的取条数


def _cutoff(since_days: float | None) -> str | None:
    if since_days is None:
        return None
    return time.strftime("%Y-%m-%d %H:%M:%S",
                         time.localtime(time.time() - since_days * 86400))


def _is_error_status(status: str | None) -> bool:
    """与 toolstats 同一判据：result 相 status 判错（大小写不敏感）。"""
    return (status or "").strip().lower() in {"error", "failed", "fail",
                                              "failure", "exception"}


def collect(db: Path, since_days: float | None = None,
            con: sqlite3.Connection | None = None) -> dict:
    """扫描 sessions/messages/steps，产出每源画像。只读，不写库。

    con：外部连接（api-serve 传入 authorizer 只读连接）；缺省自开
    mode=ro 连接（缺库 fail loud）。
    """
    own = con is None
    if own:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
    try:
        cutoff = _cutoff(since_days)
        pargs: tuple = (cutoff,) if cutoff else ()
        sess = con.execute(
            "SELECT s.sid, s.source, s.model, s.created_at, s.updated_at "
            "FROM sessions s"
            + (" WHERE s.updated_at >= ?" if cutoff else "")
            + " ORDER BY s.source, s.updated_at", pargs).fetchall()
        msg = con.execute(
            "SELECT m.sid, COUNT(*) AS n FROM messages m"
            + (" JOIN sessions s ON m.sid = s.sid" if cutoff else "")
            + (" WHERE s.updated_at >= ?" if cutoff else "")
            + " GROUP BY m.sid", pargs).fetchall()
        steps = con.execute(
            "SELECT st.sid, st.tool, st.phase, st.status, s.source "
            "FROM steps st LEFT JOIN sessions s ON st.sid = s.sid"
            + (" WHERE s.updated_at >= ?" if cutoff else "")
            + " ORDER BY st.sid, st.seq", pargs).fetchall()
    finally:
        if own:
            con.close()

    msg_n = {r["sid"]: r["n"] for r in msg}
    # 每源累加器
    agg: dict[str, dict] = defaultdict(lambda: {
        "sessions": 0, "messages": 0, "steps": 0, "results": 0,
        "errors": 0, "tools": Counter(), "tool_err": Counter(),
        "models": Counter(), "first": None, "last": None,
        "steps_per_session": Counter()})
    for r in sess:
        a = agg[r["source"] or "?"]
        a["sessions"] += 1
        a["messages"] += msg_n.get(r["sid"], 0)
        if r["model"]:
            a["models"][r["model"]] += 1
        if r["created_at"] and (a["first"] is None or r["created_at"] < a["first"]):
            a["first"] = r["created_at"]
        if r["updated_at"] and (a["last"] is None or r["updated_at"] > a["last"]):
            a["last"] = r["updated_at"]
    for r in steps:
        a = agg[r["source"] or "?"]
        a["steps"] += 1
        a["steps_per_session"][r["sid"]] += 1
        # 工具出现按**步骤行**计（不挑 phase）：call-only 的源
        # （vscode-copilot）、result-only 的段（中断会话）、成对的源
        # 都要能进排行——挑 phase 会让一整类会话在「代表工具」里隐身。
        # 与 toolstats 的 calls（call 相）是两回事，字段名叫 steps。
        a["tools"][r["tool"]] += 1
        if r["phase"] == "result":
            a["results"] += 1
            if _is_error_status(r["status"]):
                a["errors"] += 1
                a["tool_err"][r["tool"]] += 1

    # skill 画像：behstats 的调用点扫描（含 source），失败也计数。
    # 自开连接已在上面 finally 关闭——这里只传外部连接（否则会把一个
    # 已关闭的连接递给 behstats，直接 ProgrammingError）。
    from .behstats import collect_skill_invocations
    invos = collect_skill_invocations(db, con=None if own else con)
    skill_by_src: dict[str, Counter] = defaultdict(Counter)
    for i in invos:
        if since_days is not None and (i.get("ts") or "") < (cutoff or ""):
            continue
        skill_by_src[i["source"] or "?"][i["skill"]] += 1

    sources = []
    for name, a in sorted(agg.items(),
                          key=lambda kv: (-kv[1]["sessions"], kv[0])):
        calls = a["results"]
        fail_rate = (a["errors"] / calls) if calls else None
        top_tools = [{"tool": t, "steps": c, "errors": a["tool_err"].get(t, 0)}
                     for t, c in a["tools"].most_common(TOP_N)]
        src_skills = skill_by_src.get(name, Counter())
        skills = [{"skill": k or "（未识别）", "count": v}
                  for k, v in src_skills.most_common(TOP_N)]
        n_tools = len(a["tools"])
        n_skills = sum(src_skills.values())
        sps = list(a["steps_per_session"].values())
        sources.append({
            "name": name,
            "sessions": a["sessions"],
            "messages": a["messages"],
            "steps": a["steps"],
            "results": calls,
            "errors": a["errors"],
            # null = 没有 result 相步骤可算（Chat 平台无遥测；也有源只
            # 落了 call 相）——不是 0：「没有数据」与「没有错误」两件事。
            "fail_rate": round(fail_rate, 4) if fail_rate is not None else None,
            # telemetry=有任何工具步骤；paired=有 result 相（失败率可算）。
            # 两者分开：只落 call 相的源（如 vscode-copilot）不是无遥测。
            "telemetry": a["steps"] > 0,
            "paired": calls > 0,
            "avg_steps": round(sum(sps) / len(sps), 1) if sps else 0.0,
            "models": [m for m, _ in a["models"].most_common(3)],
            "top_tools": top_tools,
            "top_skills": skills,
            "tools_total": n_tools,
            "skills_total": n_skills,
            "first_active": a["first"],
            "last_active": a["last"],
        })

    tot_sess = sum(s["sessions"] for s in sources)
    tot_steps = sum(s["steps"] for s in sources)
    tot_results = sum(s["results"] for s in sources)
    tot_err = sum(s["errors"] for s in sources)
    return {
        "sources": sources,
        "totals": {
            "sources": len(sources),
            "sessions": tot_sess,
            "messages": sum(s["messages"] for s in sources),
            "steps": tot_steps,
            "results": tot_results,
            "errors": tot_err,
            "fail_rate": round(tot_err / tot_results, 4) if tot_results else None,
        },
        "window": {"since_days": since_days, "top_n": TOP_N},
        "note": ("取消率不在本面：steps 无 cancelled 形态，用户取消只在 "
                 "OTel traces（/api/reports/traces）"),
    }


def render_report(d: dict) -> str:
    """CLI md 出口（与 API 同一 collect，一处实现两出口）。"""
    t = d["totals"]
    L = ["# Harness 体检（按数据源）", ""]
    w = d["window"]["since_days"]
    L.append(f"- 窗口：{'全库' if w is None else f'最近 {w:g} 天'}"
             f"｜数据源 {t['sources']} 个｜会话 {t['sessions']}"
             f"｜步骤 {t['steps']}")
    fr = t["fail_rate"]
    L.append(f"- 总体失败率：{'—（无结果相可算）' if fr is None else f'{fr*100:.1f}%'}"
             f"（error {t['errors']} / result {t['results']}）")
    L.append(f"- {d['note']}")
    L.append("")
    if not d["sources"]:
        L.append("（窗口内没有会话）")
        return "\n".join(L) + "\n"
    L.append("| 数据源 | 会话 | 消息 | 步骤 | 失败率 | 代表工具（按步骤行数） |"
             " 代表 skill | 最近活跃 |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for s in d["sources"]:
        if not s["paired"]:
            fr_s = "—（无结果相）" if s["telemetry"] else "—（无遥测）"
        else:
            fr_s = f"{s['fail_rate']*100:.1f}%"
        tools = "、".join(f"{x['tool']}×{x['steps']}" for x in s["top_tools"]) \
            or "—"
        if s["tools_total"] > len(s["top_tools"]):
            tools += f" 等{s['tools_total']}件"
        skills = "、".join(f"{x['skill']}×{x['count']}" for x in s["top_skills"]) \
            or "—"
        if s["skills_total"] > len(s["top_skills"]):
            skills += f" 等{s['skills_total']}次"
        L.append(f"| {s['name']} | {s['sessions']} | {s['messages']} "
                 f"| {s['steps']} | {fr_s} | {tools} | {skills} "
                 f"| {s['last_active'] or '—'} |")
    L.append("")
    return "\n".join(L) + "\n"
