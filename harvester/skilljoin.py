# -*- coding: utf-8 -*-
"""skill 进化 join（report-skill-join 子命令）——T4 确定性交叉层。

chain 时间线锚点（stage → {sid, turn} 节点）× skill 调用锚点
（behstats.collect_skill_invocations 的 sid#seq）交叉，两种 join：

1. 成员内 join：调用 sid ∈ chain members。turn 解析：用户消息按
   rowid 序编号（split_turns 权威口径），调用 ts 用二分落在
   "turn k 的 user 消息 ts ≤ 调用 ts < turn k+1 的 ts" → 归 turn k；
   ts 缺失或越界则 turn=None。归组优先按 chain 节点 (sid, turn) 精确
   匹配，其次按 stage 时间窗。
2. 时间窗 join：非成员会话的调用，ts 落在 stage span（含尾日，即
   尾日+1天为界）+ margin 天内 → 归该 stage。

实测背景（T4 开工探查）：「叙事节奏」55 成员全为导出型源，成员内
skill 调用为 0；创作推进的 skill 使用（写作理论链）在 WorkBuddy 本体
会话——时间窗 join 是当前数据形态的主通道。成员内 join 仍保留：换
主题（含 workbuddy-transcript 成员）或未来源升级后即生效，不写死。

"贡献了什么/拖了后腿"的语义判定是 LLM 工作：本模块只产确定性交叉表
（含锚点回链），语义报告由 Agent 消费交叉表另行出具、交用户复核
（SOP-T4 第 2 步）。
"""

from __future__ import annotations

import sqlite3
from bisect import bisect_right
from datetime import date, timedelta
from pathlib import Path

from .behstats import collect_skill_invocations
from .topicchain import load_chain


def parse_span(s: str) -> tuple[date | None, date | None]:
    """解析 chain 节点 span："2025-02-25 ~ 2025-02-28" → (date, date)。

    不含合法日期对时返回 (None, None)（该 stage 不参与时间窗 join）。"""
    try:
        a, b = (x.strip() for x in s.split("~", 1))
        return date.fromisoformat(a), date.fromisoformat(b)
    except (ValueError, AttributeError):
        return (None, None)


def _user_turn_ts(db: Path) -> dict[str, list[str]]:
    """每 sid 的用户消息 ts 列表（rowid 序，下标+1 = turn 号）。"""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT sid, ts FROM messages WHERE role='user' "
            "ORDER BY sid, rowid").fetchall()
    finally:
        con.close()
    out: dict[str, list[str]] = {}
    for sid, ts in rows:
        out.setdefault(sid, []).append(ts or "")
    return out


def _resolve_turn(ts_list: list[str], ts: str | None) -> int | None:
    if not ts or not ts_list:
        return None
    k = bisect_right(ts_list, ts)
    return k if 1 <= k <= len(ts_list) else None


def build_cross(chain_path: Path, db_path: Path, skill: str | None = None,
                margin_days: float = 0.0) -> dict:
    """chain × skill 调用交叉表。只读。"""
    chain = load_chain(Path(chain_path))
    fm = chain["fm"]
    members = set(fm.get("members") or [])
    invs = collect_skill_invocations(db_path)
    if skill:
        invs = [i for i in invs if i["skill"] == skill]

    turn_ts = _user_turn_ts(db_path)
    titles: dict[str, str] = {}
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        for sid in {i["sid"] for i in invs}:
            r = con.execute("SELECT title FROM sessions WHERE sid=?",
                            (sid,)).fetchone()
            titles[sid] = (r[0] if r and r[0] else "")[:48]
    finally:
        con.close()

    margin = timedelta(days=margin_days)
    assigned_ids: set[int] = set()  # 以 (sid, seq) 唯一标识调用点

    stage_windows = []
    for st in fm.get("anchors") or []:
        start, end = parse_span(st.get("span") or "")
        stage_windows.append((st, start, end))

    stages_out: list[dict] = [
        {"stage": st["stage"], "span": st.get("span", ""), "rows": []}
        for st, _s, _e in stage_windows]

    def _row(inv: dict, kind: str, turn: int | None) -> dict:
        return {"sid": inv["sid"], "seq": inv["seq"],
                "anchor": f"{inv['sid']}#{inv['seq']}",
                "turn": turn, "kind": kind, "ts": inv["ts"] or "",
                "status": inv["status"], "args": inv["args"] or "",
                "skill": inv["skill"], "title": titles.get(inv["sid"], "")}

    # 第一遍：成员内 join（节点精确归组 → 时间窗归组）
    rest: list[dict] = []
    for inv in invs:
        key = (inv["sid"], inv["seq"])
        if inv["sid"] in members:
            turn = _resolve_turn(turn_ts.get(inv["sid"], []), inv["ts"])
            hit = None
            for st, _s, _e in stage_windows:
                if any(n.get("sid") == inv["sid"]
                       and n.get("turn") == turn
                       for n in (st.get("nodes") or [])):
                    hit = st
                    break
            if hit is not None:
                idx = stage_windows.index(
                    next(w for w in stage_windows if w[0] is hit))
                stages_out[idx]["rows"].append(
                    _row(inv, "member", turn))
                assigned_ids.add(key)
                continue
            # 时间窗兜底（成员会话但该 turn 不在节点上）
            placed = False
            if inv["ts"]:
                for wi, (st, s, e) in enumerate(stage_windows):
                    if s and e and s.isoformat() <= inv["ts"][:10] <= \
                            (e + margin).isoformat():
                        stages_out[wi]["rows"].append(
                            _row(inv, "member_span", turn))
                        assigned_ids.add(key)
                        placed = True
                        break
            if not placed:
                rest.append(inv)
        else:
            rest.append(inv)

    # 第二遍：非成员的时间窗 join
    for inv in rest:
        key = (inv["sid"], inv["seq"])
        if key in assigned_ids or not inv["ts"]:
            continue
        day = inv["ts"][:10]
        for wi, (st, s, e) in enumerate(stage_windows):
            if s and e and s.isoformat() <= day <= (e + margin).isoformat():
                stages_out[wi]["rows"].append(_row(inv, "temporal", None))
                assigned_ids.add(key)
                break
    unassigned = [_row(i, "unassigned", None) for i in rest
                  if (i["sid"], i["seq"]) not in assigned_ids]
    return {"skill": skill or "(全部)", "topic": fm.get("topic", ""),
            "members": len(members), "stages": stages_out,
            "unassigned": unassigned,
            "total": len(invs), "joined": len(assigned_ids)}


def render_cross(d: dict, title: str | None = None) -> str:
    title = title or f"skill 进化 join 交叉表（{d['skill']}）"
    lines = [f"# {title}", "",
             f"- 主题: {d['topic']} | 成员: {d['members']} 个"
             f" | 命中: {d['joined']}/{d['total']} 次调用", ""]
    for st in d["stages"]:
        if not st["rows"]:
            continue
        lines += [f"## {st['stage']}（{st['span']}）", "",
                  "| 调用锚点 | turn | 方式 | 状态 | 会话标题 | ts |",
                  "|---|---|---|---|---|---|"]
        for r in st["rows"]:
            lines.append(
                f"| `{r['anchor']}` | {r['turn'] if r['turn'] else '—'} "
                f"| {r['kind']} | {r['status']} | {r['title']} "
                f"| {r['ts']} |")
        lines.append("")
    if d["unassigned"]:
        lines += ["## 未归组调用", "",
                  "| 调用锚点 | skill | 状态 | 会话标题 | ts |",
                  "|---|---|---|---|---|"]
        for r in d["unassigned"]:
            lines.append(f"| `{r['anchor']}` | {r['skill']} "
                         f"| {r['status']} | {r['title']} | {r['ts']} |")
        lines.append("")
    lines += ["锚点格式：`sid#seq` 为 skill 调用（behstats 口径），"
              "可在索引库 steps 表按 sid+seq 回查；turn 为 "
              "reader.split_turns 权威口径。", ""]
    return "\n".join(lines)
