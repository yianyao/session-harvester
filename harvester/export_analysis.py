# -*- coding: utf-8 -*-
"""export-analysis 统一导出器（v0.22 P1-4）——人读 md 与机器读 JSON 同源。

SOP-P1-4 口径：
- 五类 kind：sessions / tools / errors / skills / triage；
- 去重唯一权威键 = errstats.normalize_error：tools/errors 走
  (class, normalize_error(pattern))（pattern_stats / aggregate_error_roots
  既有实现，禁第二套）；sessions 走 patterns_dedup（v2.3 view 导出的
  「错误按归一模式跨会话去重聚合」口径下沉后端，occurrences 留锚点）；
- json 出口统一 machineWrap 头（kind/generated_at/db_fingerprint/dedup/
  hint 去重口径说明）；
- md 出口复用各 render_* 权威渲染（零新渲染逻辑）；
- view 导出按钮改调 API 端点产物（view 端只做下载，不再本地拼装）。

只读：con 为 api-serve 传入的 mode=ro + authorizer 连接或普通连接，
全路径无写操作。
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

from .behstats import collect_skill_invocations, render_skill_report, \
    skill_summary
from .dbmeta import db_fingerprint
from .errstats import classify_error, collect_errors_from_db, \
    normalize_error, pattern_stats, render_report as render_errors_md
from .reader import split_turns
from .toolstats import collect_stats_from_db, render_report as render_tools_md, \
    tool_rows
from .triage import collect_triage, render_triage

KINDS = ("sessions", "tools", "errors", "skills", "triage")
MAX_SIDS = 200  # 单次导出会话数上限（防滥用；view 勾选远小于此）

_HINTS = {
    "sessions": ("机器可读快照：errors 已按归一模式跨会话去重聚合"
                 "（patterns_dedup，键=errstats.normalize_error，"
                 "occurrences 的 (sid,seq) 可定位原会话错误步骤），"
                 "sessions 内 error_steps 为逐会话明细。"),
    "tools": ("工具统计：roots 为 (class, normalize_error(pattern)) 根因"
              "聚合（同根因一条），errors 为原文明细；可经 steps 表按 "
              "sid+seq 回溯原文。"),
    "errors": ("错误三分类：patterns 按 normalize_error 归一键聚类"
               "（路径/引号/数字差异已占位符化），samples 含 (sid,seq) "
               "锚点。"),
    "skills": ("Skill 行为画像：behstats.skill_summary 口径（G4 权威"
               "聚合），chains 为调用后行为链 top。"),
    "triage": ("蒸馏队列（T1 四类候选）；A 节已按根因组分块"
               "（P1-2，连通分量归并），samples 含 (sid,seq) 锚点。"),
}


def _machine_wrap(kind: str, data: dict, db: Path,
                  con: sqlite3.Connection | None) -> dict:
    return {"kind": f"analysis-{kind}",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "db_fingerprint": db_fingerprint(db, con=con),
            "dedup": "root",
            "hint": _HINTS[kind],
            "data": data}


def _session_rows(con: sqlite3.Connection, sids: list[str] | None,
                  days: float | None) -> list[str]:
    """解析目标 sid 清单：显式 sids 优先；缺省=全库含错误步骤的会话。"""
    if sids:
        if len(sids) > MAX_SIDS:
            raise ValueError(f"sids 超过单次导出上限 {MAX_SIDS}")
        return sids
    sql = ("SELECT DISTINCT st.sid FROM steps st "
           "JOIN sessions s ON st.sid = s.sid WHERE st.status='error'")
    params: list = []
    if days is not None:
        sql += " AND s.updated_at >= ?"
        params.append(time.strftime(
            "%Y-%m-%d %H:%M:%S",
            time.localtime(time.time() - days * 86400)))
    sql += " ORDER BY s.updated_at DESC"
    return [r[0] for r in con.execute(sql, params)]


def _session_errors(con: sqlite3.Connection, sid: str) -> list[dict]:
    return [{"sid": sid, "seq": r[0], "ts": r[1], "tool": r[2],
             "error": r[3] or "",
             "class": classify_error(r[3] or ""),
             "pattern": normalize_error(r[3] or "")}
            for r in con.execute(
                "SELECT seq, ts, tool, error FROM steps "
                "WHERE sid=? AND status='error' ORDER BY seq", (sid,))]


def _session_full(con: sqlite3.Connection, sid: str) -> dict:
    """会话包：元信息 + 错误步骤 + 全回合（raw 优先，H3 契约）。"""
    row = con.execute("SELECT sid, source, session_id, title, model, "
                      "created_at, updated_at FROM sessions WHERE sid=?",
                      (sid,)).fetchone()
    if row is None:
        raise KeyError(sid)
    msgs, contents, ts_list = _raw_messages(con, sid)
    turns = split_turns(SimpleNamespace(messages=msgs))
    out_turns, cursor = [], 0
    for i, turn in enumerate(turns, start=1):
        seg = contents[cursor:cursor + len(turn)]
        seg_ts = ts_list[cursor:cursor + len(turn)]
        cursor += len(turn)
        out_turns.append({"no": i, "ts": seg_ts[0] if seg_ts else None,
                          "messages": [{"role": m.role, "ts": t, "content": c}
                                       for m, t, c in zip(turn, seg_ts, seg)]})
    return {"meta": dict(row), "error_steps": _session_errors(con, sid),
            "turns": out_turns}


def _raw_messages(con: sqlite3.Connection, sid: str):
    rows = con.execute("SELECT role, ts, text, raw FROM messages "
                       "WHERE sid = ? ORDER BY rowid", (sid,)).fetchall()
    msgs = [SimpleNamespace(role=r["role"]) for r in rows]
    # raw 恒为原文（H38）；raw 空则退 text（部分源无 raw 列值）
    contents = [(r["raw"] if r["raw"] else r["text"]) or "" for r in rows]
    ts_list = [r["ts"] for r in rows]
    return msgs, contents, ts_list


def _sessions_data(con: sqlite3.Connection, db: Path, sids, days) -> dict:
    """sessions kind：逐会话明细 + patterns_dedup 跨会话去重聚合。"""
    targets = _session_rows(con, sids, days)
    packs = []
    by_pat: dict[str, dict] = {}
    for sid in targets:
        d = _session_full(con, sid)
        packs.append({"sid": d["meta"]["sid"],
                      "title": d["meta"]["title"] or "",
                      "source": d["meta"]["source"] or "",
                      "model": d["meta"]["model"] or "",
                      "updated_at": d["meta"]["updated_at"] or "",
                      "n_turns": len(d["turns"]),
                      "error_count": len(d["error_steps"]),
                      "error_steps": d["error_steps"],
                      "turns": d["turns"]})
        for e in d["error_steps"]:
            k = e["pattern"] or (e["error"][:80] if e["error"] else "（空）")
            g = by_pat.setdefault(k, {
                "pattern": k, "class": e["class"], "count": 0,
                "sample_error": e["error"], "tools": set(),
                "occurrences": []})
            g["count"] += 1
            g["tools"].add(e["tool"])
            g["occurrences"].append({"sid": sid, "seq": e["seq"],
                                     "ts": e["ts"], "tool": e["tool"]})
    patterns_dedup = sorted(by_pat.values(), key=lambda g: -g["count"])
    for g in patterns_dedup:
        g["tools"] = sorted(g["tools"])
    return {"n_sessions": len(packs), "sessions": packs,
            "patterns_dedup": patterns_dedup}


def build_analysis(db: Path, kind: str, *, con: sqlite3.Connection | None = None,
                   days: float | None = None, sids: list[str] | None = None,
                   min_count: int = 2, top: int = 10,
                   cards_root: Path | None = None) -> tuple[dict, str]:
    """统一导出：返回 (json_obj, md_str)，同源双出口。

    json_obj 已含 machineWrap 头；md 为人读 Markdown（复用各权威渲染）。
    kind 不合法 → ValueError；sessions 的 sid 不存在 → KeyError。
    """
    if kind not in KINDS:
        raise ValueError(f"kind={kind!r} 不在 {list(KINDS)}")
    db = Path(db)
    own = con is None
    if own:
        con = sqlite3.connect(str(db))
        con.row_factory = sqlite3.Row
    try:
        if kind == "errors":
            errors, meta = collect_errors_from_db(db, since_days=days, con=con)
            stats = pattern_stats(errors)
            data = {"meta": meta,
                    "by_class": {c: sum(1 for e in errors
                                        if e["class"] == c)
                                 for c in ("env", "tool_interface",
                                           "context", "unclassified")},
                    "patterns": [{"pattern": pat, "class": d["class"],
                                  "count": d["count"],
                                  "given_up": d.get("given_up", 0),
                                  "tools": sorted(d["tools"]),
                                  "samples": [{"sid": s, "seq": q,
                                               "error": raw}
                                              for s, q, raw in d["samples"]]}
                                 for pat, d in sorted(
                                     stats.items(),
                                     key=lambda kv: (-kv[1]["count"],
                                                     kv[0]))]}
            md = render_errors_md(errors, meta)
        elif kind == "tools":
            stats_d, _flow = collect_stats_from_db(db, since_days=days,
                                                   con=con)
            data = {"tools": tool_rows(stats_d)}
            md = render_tools_md(stats_d)
        elif kind == "skills":
            invocations = collect_skill_invocations(db, con=con)
            if days is not None:
                cutoff = time.strftime(
                    "%Y-%m-%d %H:%M:%S",
                    time.localtime(time.time() - days * 86400))
                invocations = [i for i in invocations
                               if (i["ts"] or "") >= cutoff]
            summary = skill_summary(invocations)
            data = {"n_invocations": len(invocations),
                    "skills": [{"skill": name, "calls": d["calls"],
                                "sids": d["sids"], "sources": d["sources"],
                                "ok": d["ok"], "err": d["err"],
                                "no_result": d["no_result"],
                                "args_samples": d["args_samples"],
                                "chains": d["chains"],
                                "anchors": d["anchors"]}
                               for name, d in sorted(
                                   summary.items(),
                                   key=lambda kv: -kv[1]["calls"])]}
            md = render_skill_report(invocations)
        elif kind == "triage":
            r = collect_triage(db, since_days=days, cards_root=cards_root,
                               min_count=min_count, top_n=top, con=con)
            md = render_triage(r)  # 元组态渲染（render_triage 权威口径）
            for p in r["new_patterns"] + r["old_patterns"]:
                p["samples"] = [{"sid": s, "seq": q, "error": err}
                                for s, q, err in p["samples"]]
            for s in r["skills"]:
                s["sample"] = ({"sid": s["sample"][0], "seq": s["sample"][1]}
                               if s["sample"] else None)
            data = r
        else:  # sessions
            data = _sessions_data(con, db, sids, days)
            md = render_sessions_md(data)
        return _machine_wrap(kind, data, db, con), md
    finally:
        if own:
            con.close()


def render_sessions_md(data: dict) -> str:
    """sessions kind 的人读出口：会话分节（元信息+错误清单+回合全文）。

    与 view v2.3 批量导出口径一致；全文走 raw（H3 契约）。
    """
    lines = [f"# 异常会话分析导出", "",
             f"- 会话数 {data['n_sessions']}｜错误模式 "
             f"{len(data['patterns_dedup'])} 个（已按归一模式跨会话去重）",
             ""]
    lines += ["## 错误模式去重汇总（patterns_dedup）", "",
              "| 次数 | 类别 | 模式 |", "|---:|---|---|"]
    for g in data["patterns_dedup"][:30]:
        pat = (g["pattern"] or "").replace("|", "\\|")[:80]
        lines.append(f"| {g['count']} | {g['class']} | `{pat}` |")
    lines.append("")
    for s in data["sessions"]:
        # 会话级异常类型：按 pattern 计数（与 view 口径一致）
        cnt: dict[str, int] = defaultdict(int)
        for e in s["error_steps"]:
            cnt[e["pattern"]] += 1
        pats = [f"`{k}` x{v}" for k, v in
                sorted(cnt.items(), key=lambda kv: -kv[1])]
        lines += ["---", "",
                  f"# {s['title'] or s['sid']}", "",
                  f"- 会话ID: `{s['sid']}`",
                  f"- 异常类型: {'、'.join(pats) if pats else '（无错误步骤）'}",
                  f"- 来源 {s['source']} · 模型 {s['model'] or '（未知）'}"
                  f" · 更新 {s['updated_at'] or '?'} · 回合 {s['n_turns']}",
                  ""]
        if s["error_steps"]:
            lines += ["## 错误步骤清单", ""]
            for e in s["error_steps"]:
                lines.append(f"- `#{e['seq']}` {e['ts'] or ''} [{e['tool']}]"
                             f"（{e['class']}）{e['error'][:160]}")
            lines.append("")
        for t in s["turns"]:
            lines.append(f"## 回合 #{t['no']}"
                         + (f"（{t['ts']}）" if t["ts"] else ""))
            lines.append("")
            for m in t["messages"]:
                lines += [f"**[{m['role']}]** {m['ts'] or ''}", "",
                          m["content"] or "", ""]
    return "\n".join(lines).rstrip() + "\n"


def to_json(json_obj: dict) -> str:
    return json.dumps(json_obj, ensure_ascii=False, indent=2)
