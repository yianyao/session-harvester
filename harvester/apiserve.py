# -*- coding: utf-8 -*-
"""api-serve —— 只读 HTTP JSON API（stdio mcp-serve 之外的通用查询面）。

定位：通用只读查询能力，供任意前端/脚本经 HTTP 消费本套件索引库。
安全设计（本命令的生命线）：

1. **只读**：连接以 ``file:...?mode=ro`` 打开 + SQLite authorizer 白名单
   （SELECT/READ/聚合函数放行，其余内核级 deny）——双保险，进程内想写
   也写不进；FTS 检索、skill 扫描与 v2 决策产物（triage/reports/cards）
   复用同一连接，**全部查询路径**均处于 authorizer 之下；
2. **自检 fail loud**：启动时逐列比对冻结 schema、FTS 冒烟，任何缺失拒绝
   启动（绝不让客户端拿到静默错的数据）；
3. **不裸奔**：默认绑 127.0.0.1；``--host`` 给非回环地址时必须配
   ``--token``（请求须带 ``X-Token`` 头），否则拒绝启动；
4. 不设 CORS 头——浏览器同源策略天然阻止任意网页 drive-by 读取。

接口版本：/api/meta 返回 api_version=1；未来演进只增端点不改语义，
major 变更须消费方同步升级。

v2 端点（仍然 api_version=1，只增不改）：
- GET /api/triage?days=&min_count=&top=   蒸馏队列（T1 四类候选）
- GET /api/reports/errors?days=           G2 错误三分类 + 模式聚类
- GET /api/reports/tools?days=            G1 工具统计 + 重试/放弃
- GET /api/reports/skills?days=           G4 Skill 行为画像（skill_summary 口径）
- GET /api/reports/agents?days=&min_count=&max_items=  G2 建议条目（结构化）
- GET /api/cards                          G3 卡片校验（需 --cards-root）
- GET /api/export-analysis?kind=&format=md|json&sids=  统一分析导出（P1-4，
  五类 kind，md/JSON 同源双出口；format=md 时响应 text/markdown）

cards_root 仅来自启动参数（--cards-root）：访问面在启动时钉死，
不接受 URL 参数指定任意目录——与"只读 + 白名单"同一安全哲学。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__
from .agent_suggest import build_suggestion_entries
from .behstats import collect_skill_invocations, skill_summary
from .cards import validate_cards
from .dbmeta import db_fingerprint
from .errstats import (classify_error, collect_errors_from_db, cross_stats,
                       normalize_error, pattern_stats)
from .export_analysis import build_analysis
from .indexing import search
from .reader import split_turns
from .suggestmeta import load_statuses
from .toolstats import (collect_source_stats, collect_stats_from_db,
                        tool_rows)
from .triage import collect_triage

API_VERSION = 1
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
UNKNOWN_MODEL = "（未知）"

#: 启动自检的冻结 schema 清单（与 docs/ADAPTER_CONTRACT.md 同步维护；
#: 注意 messages 无 seq 列，行序=rowid）
EXPECTED_SCHEMA: dict[str, list[str]] = {
    "sessions": ["sid", "source", "session_id", "title", "category",
                 "created_at", "updated_at", "file", "model"],
    "messages": ["sid", "role", "ts", "text", "raw"],
    "steps": ["sid", "seq", "ts", "tool", "phase", "status", "error",
              "detail"],
}

# authorizer 动作常量（Python 3.11 才有 sqlite3.SQLITE_* 封装，为兼容
# 3.10 直接给裸值；来源：sqlite3.c）
_SQLITE_OK = getattr(sqlite3, "SQLITE_OK", 0)
_SQLITE_DENY = getattr(sqlite3, "SQLITE_DENY", 2)
_SQLITE_SELECT = getattr(sqlite3, "SQLITE_SELECT", 21)
_SQLITE_READ = getattr(sqlite3, "SQLITE_READ", 20)
_SQLITE_FUNCTION = getattr(sqlite3, "SQLITE_FUNCTION", 31)
_SQLITE_PRAGMA = getattr(sqlite3, "SQLITE_PRAGMA", 19)
_SQLITE_RECURSIVE = getattr(sqlite3, "SQLITE_RECURSIVE", 34)


def _readonly_authorizer(action, arg1, arg2, db_name, trigger):
    """内核级只读白名单：SELECT/READ/函数 与递归子查询放行；PRAGMA 仅
    放行内部只读标记 data_version（sqlite3 驱动自用），其余（含可写
    PRAGMA 与一切写动作）一律 deny。"""
    if action in (_SQLITE_SELECT, _SQLITE_READ, _SQLITE_FUNCTION,
                  _SQLITE_RECURSIVE):
        return _SQLITE_OK
    if action == _SQLITE_PRAGMA and arg1 == "data_version":
        return _SQLITE_OK
    return _SQLITE_DENY


def open_ro(db: Path) -> sqlite3.Connection:
    """打开物理只读连接（mode=ro + authorizer 双保险）。"""
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.set_authorizer(_readonly_authorizer)
    return con


def self_check(db: Path) -> list[str]:
    """启动自检：schema 逐列比对 + FTS 冒烟。返回问题清单（空=通过）。"""
    problems: list[str] = []
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        for table, cols in EXPECTED_SCHEMA.items():
            try:
                actual = [r[1] for r in
                          con.execute(f"PRAGMA table_info({table})")]
            except sqlite3.DatabaseError as e:
                problems.append(f"{table}: 查询失败 {e}")
                continue
            if not actual:
                problems.append(f"缺少表 {table}")
                continue
            missing = [c for c in cols if c not in actual]
            if missing:
                problems.append(f"{table} 缺少列: {missing}"
                                "（主项目 schema 已变更？同步"
                                " docs/ADAPTER_CONTRACT.md 与本清单）")
        try:
            con.execute("SELECT 1 FROM messages WHERE messages MATCH "
                        "'harvester_selfcheck_probe' LIMIT 1")
        except sqlite3.DatabaseError as e:
            problems.append(f"FTS 不可用（请先在主项目跑 index/sync）: {e}")
    finally:
        con.close()
    return problems


def _check_host(host: str, token: str | None) -> str | None:
    """host/token 组合校验。返回错误说明，None=通过。"""
    h = host.split("%")[0]
    if h in LOOPBACK:
        return None
    if not token:
        return (f"host={host} 非回环地址：跨机器暴露必须配 --token "
                "(请求带 X-Token 头)，否则拒绝启动")
    return None


def _norm_date(val: str | None, end: bool = False) -> str | None:
    if not val:
        return None
    val = val.strip()
    if len(val) == 10:  # YYYY-MM-DD → 覆盖全天
        return val + (" 23:59:59" if end else " 00:00:00")
    return val


def _days_cutoff(days: float) -> str:
    """days → 'YYYY-MM-DD HH:MM:SS' 截止串（与 report-*/triage 同口径）。"""
    return time.strftime("%Y-%m-%d %H:%M:%S",
                         time.localtime(time.time() - days * 86400))


def _since_days(params: dict) -> float | None:
    """/api/* 查询参数 days → since_days（非法值静默回退全库）。"""
    v = params.get("days")
    if not v:
        return None
    try:
        return max(float(v), 0.0)
    except ValueError:
        return None


def _int_param(params: dict, name: str, default: int,
               lo: int, hi: int) -> int:
    try:
        return min(max(int(params.get(name) or default), lo), hi)
    except (TypeError, ValueError):
        return default


def _samples_json(samples: list) -> list[dict]:
    """(sid, seq, text) 元组列表 → {sid, seq, error} 对象列表（API 形状稳定）。"""
    return [{"sid": s, "seq": q, "error": t} for s, q, t in samples]


# ---------- 查询逻辑（纯函数，可测，连接由调用方给） ----------

def _schema_fingerprint() -> str:
    """冻结 schema 指纹（设计稿 §3 承诺的 meta 字段）：EXPECTED_SCHEMA
    规范序列化的 sha256 前 12 位。主项目改表 → 指纹变化 → 消费方可
    感知（api_version 之外的第二根漂移探针）。"""
    canon = json.dumps(EXPECTED_SCHEMA, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:12]


def api_meta(con: sqlite3.Connection, db: Path) -> dict:
    n = con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    row = con.execute("SELECT MIN(updated_at), MAX(updated_at) "
                      "FROM sessions").fetchone()
    return {"api_version": API_VERSION, "server": "session-harvester",
            "package_version": __version__, "readonly": True,
            "schema_fingerprint": _schema_fingerprint(),
            "sessions": n,
            "time_min": row[0], "time_max": row[1],
            # v0.19 additive：库快照指纹（前端 meta 行与消费方判陈旧用）
            "db_fingerprint": db_fingerprint(db, con=con)}


def api_facets(con: sqlite3.Connection, db: Path) -> dict:
    sources = [{"name": r[0], "count": r[1]} for r in con.execute(
        "SELECT source, COUNT(*) FROM sessions GROUP BY source "
        "ORDER BY 2 DESC")]
    models = [{"name": r[0] or UNKNOWN_MODEL, "count": r[1]} for r in
              con.execute("SELECT model, COUNT(*) FROM sessions "
                          "GROUP BY model ORDER BY 2 DESC")]
    invocations = collect_skill_invocations(db, con=con)
    by_skill = Counter(i["skill"] for i in invocations)
    skills = [{"name": k, "count": v}
              for k, v in by_skill.most_common()]
    row = con.execute("SELECT MIN(updated_at), MAX(updated_at) "
                      "FROM sessions").fetchone()
    return {"sources": sources, "models": models, "skills": skills,
            "time_min": row[0], "time_max": row[1]}


def api_sessions(con: sqlite3.Connection, db: Path, params: dict) -> dict:
    """筛选会话列表。SQL 侧筛 source/model/时间；q/skill 在 Python 侧
    交集（q 走 bigram search()，skill 复用 behstats 口径）。"""
    sql = ("SELECT sid, source, title, category, model, created_at, "
           "updated_at FROM sessions WHERE 1=1")
    p: list = []
    if params.get("source"):
        sql += " AND source = ?"
        p.append(params["source"])
    model = params.get("model")
    if model:
        if model == UNKNOWN_MODEL:
            sql += " AND model IS NULL"
        else:
            sql += " AND model = ?"
            p.append(model)
    since = _norm_date(params.get("since"))
    until = _norm_date(params.get("until"), end=True)
    if since:
        sql += " AND updated_at >= ?"
        p.append(since)
    if until:
        sql += " AND updated_at <= ?"
        p.append(until)
    rows = con.execute(sql + " ORDER BY updated_at DESC", p).fetchall()
    items = [dict(r) for r in rows]

    if params.get("q"):
        hits = search(db, params["q"], limit=2000, con=con)
        sids = {h["sid"] for h in hits}
        items = [r for r in items if r["sid"] in sids]
    if params.get("skill"):
        want = {i["sid"] for i in collect_skill_invocations(db, con=con)
                if i["skill"] == params["skill"]}
        items = [r for r in items if r["sid"] in want]

    total = len(items)
    # errors_only=1（v0.20 additive）：只含有错误步骤的会话（分页前过滤，
    # total 同步反映过滤后数量）；error_count 全量预聚合一并给出。
    err_cnt: dict[str, int] = {}
    if params.get("errors_only"):
        err_cnt = {r[0]: r[1] for r in con.execute(
            "SELECT sid, COUNT(*) FROM steps WHERE status='error' GROUP BY sid")}
        items = [r for r in items if err_cnt.get(r["sid"], 0) > 0]
        total = len(items)
    limit = _int_param(params, "limit", 50, 1, 200)
    offset = _int_param(params, "offset", 0, 0, 10 ** 9)
    page_items = items[offset:offset + limit]
    # 附加 error_count（additive，v1 兼容）：仅对分页切片按 steps.status=
    # 'error' 预聚合，≤200 行 IN 查询，避免全表 JOIN。
    if page_items:
        sids = [r["sid"] for r in page_items]
        if err_cnt:
            cnt = {s: err_cnt[s] for s in sids if s in err_cnt}
        else:
            marks = ",".join("?" * len(sids))
            cnt = {r[0]: r[1] for r in con.execute(
                f"SELECT sid, COUNT(*) FROM steps WHERE status='error' "
                f"AND sid IN ({marks}) GROUP BY sid", sids)}
        for it in page_items:
            it["error_count"] = cnt.get(it["sid"], 0)
    return {"api_version": API_VERSION, "total": total,
            "offset": offset, "limit": limit,
            "items": page_items}


def _session_messages(con: sqlite3.Connection, sid: str):
    rows = con.execute("SELECT role, ts, text, raw FROM messages "
                       "WHERE sid = ? ORDER BY rowid", (sid,)).fetchall()
    msgs = [SimpleNamespace(role=r["role"]) for r in rows]
    contents = [(r["raw"] if r["raw"] else r["text"]) or "" for r in rows]
    ts_list = [r["ts"] for r in rows]
    return msgs, contents, ts_list


def api_session(con: sqlite3.Connection, sid: str) -> dict:
    row = con.execute("SELECT sid, source, session_id, title, category, "
                      "model, created_at, updated_at, file FROM sessions "
                      "WHERE sid = ?", (sid,)).fetchone()
    if row is None:
        raise KeyError(sid)
    msgs, contents, ts_list = _session_messages(con, sid)
    turns = split_turns(SimpleNamespace(messages=msgs))
    out_turns = []
    cursor = 0  # split_turns 是连续切片，这里按下标推进对齐原文内容
    for i, turn in enumerate(turns, start=1):
        n = len(turn)
        seg = contents[cursor:cursor + n]
        seg_ts = ts_list[cursor:cursor + n]
        cursor += n
        preview = ""
        for m, c in zip(turn, seg):
            if m.role == "user":
                preview = c[:120]
                break
        out_turns.append({"no": i, "n_messages": n,
                          "roles": [m.role for m in turn],
                          "ts": seg_ts[0] if seg_ts else None,
                          "preview": preview})
    return {"api_version": API_VERSION, "meta": dict(row),
            "n_turns": len(out_turns), "turns": out_turns,
            # v0.20 additive：错误步骤清单（pattern/class 为 errstats 归一口径，
            # 供前端批量导出按「会话ID+异常类型」分类与机器可读聚合去重）
            "error_steps": [
                {"sid": sid, "seq": r[0], "ts": r[1], "tool": r[2],
                 "error": r[3] or "",
                 "class": classify_error(r[3] or ""),
                 "pattern": normalize_error(r[3] or "")}
                for r in con.execute(
                    "SELECT seq, ts, tool, error FROM steps "
                    "WHERE sid=? AND status='error' ORDER BY seq", (sid,))]}


def api_turn(con: sqlite3.Connection, sid: str, no: int) -> dict:
    msgs, contents, ts_list = _session_messages(con, sid)
    turns = split_turns(SimpleNamespace(messages=msgs))
    if no < 1 or no > len(turns):
        raise IndexError(f"turn {no} 越界（共 {len(turns)} 回合）")
    turn = turns[no - 1]
    start = sum(len(t) for t in turns[:no - 1])
    seg_c = contents[start:start + len(turn)]
    seg_ts = ts_list[start:start + len(turn)]
    messages = [{"role": m.role, "ts": t, "content": c}
                for m, t, c in zip(turn, seg_ts, seg_c)]
    return {"api_version": API_VERSION, "sid": sid, "turn": no,
            "messages": messages}


# ---------- v2 决策产物端点（纯函数层） ----------

def api_triage(con: sqlite3.Connection, db: Path,
               cards_root: Path | None, params: dict) -> dict:
    """蒸馏队列（T1）。samples/sample 元组转对象，JSON 形状稳定。"""
    r = collect_triage(db, since_days=_since_days(params),
                       cards_root=cards_root,
                       min_count=_int_param(params, "min_count", 2, 1, 100),
                       top_n=_int_param(params, "top", 10, 1, 50),
                       con=con)
    for p in r["new_patterns"] + r["old_patterns"]:
        p["samples"] = _samples_json(p["samples"])
    for s in r["skills"]:
        s["sample"] = ({"sid": s["sample"][0], "seq": s["sample"][1]}
                       if s["sample"] else None)
    r["api_version"] = API_VERSION
    # v0.19 additive：库快照指纹
    r["db_fingerprint"] = db_fingerprint(db, con=con)
    return r


def api_reports_errors(con: sqlite3.Connection, db: Path,
                       params: dict) -> dict:
    """G2 错误三分类：分布 + 模式聚类（决策要聚类不要流水，全量 errors 不出）。"""
    errors, meta = collect_errors_from_db(db, since_days=_since_days(params),
                                          con=con)
    by_class = Counter(e["class"] for e in errors)
    by_bucket = Counter(e["bucket"] for e in errors)
    by_source: dict[str, Counter] = defaultdict(Counter)
    for e in errors:
        by_source[e["source"]][e["class"]] += 1
    stats = pattern_stats(errors)
    patterns = [{"pattern": pat, "class": d["class"], "count": d["count"],
                 "tools": sorted(d["tools"]),
                 "samples": _samples_json(d["samples"])}
                for pat, d in sorted(stats.items(),
                                     key=lambda kv: (-kv[1]["count"], kv[0]))]
    return {"api_version": API_VERSION, "meta": meta,
            "by_class": dict(by_class), "by_bucket": dict(by_bucket),
            "by_source": {k: dict(v) for k, v in by_source.items()},
            "patterns": patterns,
            # P1-3 additive：交叉表 class × harness × model（行序 total 降序）
            "cross": cross_stats(errors),
            "db_fingerprint": db_fingerprint(db, con=con)}


def api_reports_tools(con: sqlite3.Connection, db: Path,
                      params: dict) -> dict:
    """G1 工具统计：总表 + 重试/放弃 + 按源分布（工具名已归一）。
    min_calls（v0.19 additive）：样本量阈值，calls<阈值的行 low_sample=true。"""
    days = _since_days(params)
    min_calls = _int_param(params, "min_calls", 0, 0, 10 ** 6)
    stats, flow = collect_stats_from_db(db, since_days=days, con=con)
    by_source = collect_source_stats(db, since_days=days, con=con)
    src_out = {}
    for src, tm in by_source.items():
        src_out[src] = {
            "calls": sum(st.calls for st in tm.values()),
            "errors": sum(st.error for st in tm.values()),
            "tools": tool_rows(dict(tm), min_calls=min_calls),
        }
    return {"api_version": API_VERSION,
            "tools": tool_rows(stats, min_calls=min_calls),
            "flow": flow, "by_source": src_out, "min_calls": min_calls,
            "db_fingerprint": db_fingerprint(db, con=con)}


def api_reports_skills(con: sqlite3.Connection, db: Path,
                       params: dict) -> dict:
    """G4 Skill 行为画像：behstats.skill_summary 口径（G4 权威聚合复用）。
    min_calls（v0.19 additive）：样本量阈值，calls<阈值的行 low_sample=true。"""
    invocations = collect_skill_invocations(db, con=con)
    days = _since_days(params)
    min_calls = _int_param(params, "min_calls", 0, 0, 10 ** 6)
    if days is not None:
        cutoff = _days_cutoff(days)
        invocations = [i for i in invocations if (i["ts"] or "") >= cutoff]
    summary = skill_summary(invocations)
    skills = [{"skill": name, "calls": d["calls"], "sids": d["sids"],
               "sources": d["sources"], "ok": d["ok"], "err": d["err"],
               "no_result": d["no_result"],
               "low_sample": bool(min_calls > 0 and d["calls"] < min_calls),
               "args_samples": d["args_samples"], "chains": d["chains"],
               "anchors": d["anchors"]}
              for name, d in sorted(summary.items(),
                                    key=lambda kv: -kv[1]["calls"])]
    return {"api_version": API_VERSION,
            "n_invocations": len(invocations), "skills": skills,
            "min_calls": min_calls,
            "db_fingerprint": db_fingerprint(db, con=con)}


def api_reports_agents(con: sqlite3.Connection, db: Path,
                       params: dict, meta_db: Path | None = None) -> dict:
    """G2 建议条目（结构化）：agent_suggest.build_suggestion_entries 复用。
    v0.19 additive：每条 entries 带 unresolved_count/owner；status 取自
    独立 meta 库（suggestion_status 表，key=title），未记录默认 pending。
    排序口径：unresolved_count 降序 → total 降序（未解决优先）；
    待修清单 = unresolved_count>=1，自愈条目（=0）由消费方降级"观察区"。"""
    errors, meta = collect_errors_from_db(db, since_days=_since_days(params),
                                          con=con)
    min_count = _int_param(params, "min_count", 3, 1, 100)
    max_items = _int_param(params, "max_items", 15, 1, 50)
    built = build_suggestion_entries(errors, min_count=min_count,
                                     max_items=max_items)
    statuses = load_statuses(meta_db)
    entries = [{"title": e["title"], "body": e["body"], "total": e["total"],
                "unresolved_count": e["unresolved_count"],
                "owner": e["owner"],
                "status": statuses.get(e["title"], "pending"),
                "samples": _samples_json(e["samples"])}
               for e in built["entries"]]
    leftover = [{"pattern": pat, "class": d["class"], "count": d["count"],
                 "given_up": d.get("given_up", 0),
                 "tools": sorted(d["tools"]),
                 "status": statuses.get(pat, "pending"),
                 "samples": _samples_json(d["samples"][:3])}
                for pat, d in built["leftover"][:20]]
    return {"api_version": API_VERSION, "meta": meta,
            "min_count": min_count, "entries": entries,
            "leftover": leftover,
            "db_fingerprint": db_fingerprint(db, con=con)}


def api_cards(con: sqlite3.Connection, db: Path,
              cards_root: Path | None, params: dict) -> dict:
    """G3 卡片校验。cards_root 只认启动参数（访问面启动时钉死）；
    未配置 → ValueError（HTTP 400），目录不存在 → FileNotFoundError
    （HTTP 404）。"""
    if not cards_root:
        raise ValueError("api-serve 未配置 --cards-root，卡片校验端点不可用"
                         "（启动时加 --cards-root <目录>）")
    root = Path(cards_root)
    if not root.is_dir():
        raise FileNotFoundError(f"cards 目录不存在: {root}")
    results, summary = validate_cards(root, db, con=con)
    return {"api_version": API_VERSION, "root": str(root),
            "summary": summary, "cards": results,
            "db_fingerprint": db_fingerprint(db, con=con)}


# ---------- HTTP 层（薄壳） ----------

def api_topics(con: sqlite3.Connection, db: Path,
               topics_meta: Path | None) -> dict:
    """T3：主题注册表 + 簇统计（additive；api_version=1 不动）。

    topics_meta 未配置或文件缺失 → 空表 + hint（降级不炸，view 需兼容
    旧上游，红线 §5.2）。
    """
    topics: list[dict] = []
    hint = None
    if not topics_meta or not Path(topics_meta).is_file():
        hint = ("topics_meta 未配置或不存在（启动时加 "
                "--topics-meta <topics_meta.db>）")
    else:
        mcon = sqlite3.connect(f"file:{Path(topics_meta)}?mode=ro", uri=True)
        mcon.row_factory = sqlite3.Row
        try:
            for row in mcon.execute(
                    "SELECT id, name, keywords, members, created "
                    "FROM topics ORDER BY id"):
                members = json.loads(row["members"]) if row["members"] else []
                member_sids = [m["sid"] if isinstance(m, dict) else m
                               for m in members]
                first_at = last_at = None
                for sid in member_sids:
                    r = con.execute(
                        "SELECT MIN(created_at), MAX(created_at) "
                        "FROM sessions WHERE sid=?", (sid,)).fetchone()
                    if r and r[0]:
                        first_at = (min(first_at, r[0])
                                    if first_at else r[0])
                        last_at = (max(last_at, r[1]) if last_at else r[1])
                topics.append({
                    "id": row["id"], "name": row["name"],
                    "keywords": row["keywords"], "created": row["created"],
                    "members_count": len(member_sids),
                    "first_activity": first_at, "last_activity": last_at})
        finally:
            mcon.close()
    r = {"api_version": API_VERSION, "topics": topics,
         "db_fingerprint": db_fingerprint(db, con=con)}
    if hint:
        r["hint"] = hint
    return r


def api_topic_chain(con: sqlite3.Connection, db: Path, topic_id: str,
                    topics_meta: Path | None, chain_root: Path | None) -> dict | None:
    """T3：chain 长文结构化。在 chain_root 下扫 chain-*.md，按
    frontmatter.topic_id 匹配。未找到/未配置 → None（HTTP 404）。"""
    from .topicchain import load_chain
    root = Path(chain_root) if chain_root else None
    if not root or not root.is_dir() or not topics_meta \
            or not Path(topics_meta).is_file():
        return None
    for p in sorted(root.glob("chain-*.md")):
        try:
            d = load_chain(p)
        except (ValueError, RuntimeError, OSError):
            continue  # 坏文档跳过：暴露端点不因单文件失败而 500
        fm = d["fm"]
        if isinstance(fm, dict) and fm.get("topic_id") == topic_id:
            return {"api_version": API_VERSION, "topic_id": topic_id,
                    "fm": fm, "body": d["body"], "chain_path": str(p),
                    "db_fingerprint": db_fingerprint(db, con=con)}
    return None


def api_keywords(con: sqlite3.Connection, db: Path,
                 keywords_meta: Path | None, qs: dict) -> dict:
    """P2-2：n-gram 关键词频次（additive；api_version=1 不动）。

    读 keywords_meta 最新 run；未配置/缺失 → 空表 + hint（降级不炸，
    对齐 /api/topics 口径）。query: n（档位，默认 2）、limit（默认 50）。
    """
    from .kwstats import load_stats
    try:
        n = int(qs.get("n", "2"))
    except ValueError:
        n = 2
    try:
        limit = max(1, min(500, int(qs.get("limit", "50"))))
    except ValueError:
        limit = 50
    d = load_stats(Path(keywords_meta) if keywords_meta else None,
                   n=n, limit=limit)
    r = {"api_version": API_VERSION, "n": n, "limit": limit,
         "rows": d.get("rows", []),
         "db_fingerprint": db_fingerprint(db, con=con)}
    if d.get("run"):
        r["run"] = d["run"]
    if d.get("hint"):
        r["hint"] = d["hint"]
    return r


def api_export_analysis(con: sqlite3.Connection, db: Path,
                        cards_root: Path | None,
                        params: dict) -> tuple[dict, str]:
    """P1-4 统一导出端点（/api/export-analysis）：返回 (json_obj, md)。

    kind=sessions|tools|errors|skills|triage；format 由路由层选择出口
    （json→json_obj / md→md）。同源双出口 = export_analysis.build_analysis，
    与 CLI 完全同一实现（禁第二套口径）。sessions 的 sid 不存在 → KeyError
    （HTTP 404）；kind 非法 / sids 超限 → ValueError（HTTP 400）。
    """
    kind = str(params.get("kind") or "").strip().lower()
    sids_raw = str(params.get("sids") or "")
    sids = [s for s in sids_raw.split(",") if s] or None
    min_count = _int_param(params, "min_count", 2, 1, 100)
    top = _int_param(params, "top", 10, 1, 50)
    obj, md = build_analysis(db, kind, con=con, days=_since_days(params),
                             sids=sids, min_count=min_count, top=top,
                             cards_root=cards_root)
    obj["api_version"] = API_VERSION
    return obj, md


def make_handler(db: Path, token: str | None,
                 cards_root: Path | None = None,
                 suggestions_meta: Path | None = None,
                 topics_meta: Path | None = None,
                 chain_root: Path | None = None,
                 keywords_meta: Path | None = None):
    """生成 Handler 类（闭包携带配置，便于测试时随机端口起停）。"""

    class ApiHandler(BaseHTTPRequestHandler):
        server_version = f"harvester/{__version__}"

        def log_message(self, fmt, *args):  # 日志走 stderr，一行一条
            print(f"[api-serve] {self.address_string()} {fmt % args}",
                  file=sys.stderr)

        # -- 响应工具 --
        def _json(self, obj, status: int = 200) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type",
                             "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _text(self, text: str, status: int = 200) -> None:
            body = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        # -- 鉴权与方法守卫 --
        def _authorized(self) -> bool:
            if not token:
                return True
            # 常数时间比较：启用 token 的场景恰是跨机器暴露之时
            return hmac.compare_digest(
                self.headers.get("X-Token") or "", token)

        def do_GET(self) -> None:  # noqa: N802 (http.server 命名约定)
            if not self._authorized():
                self._json({"error": "unauthorized：缺少或错误的 X-Token 头"},
                           401)
                return
            u = urlparse(self.path)
            qs = {k: v[0] for k, v in parse_qs(u.query).items()}
            path = u.path
            con = open_ro(db)
            try:
                m = re.fullmatch(r"/api/session/(.+)/turn/(\d+)", path)
                if m:
                    try:
                        self._json(api_turn(con, unquote(m.group(1)),
                                            int(m.group(2))))
                    except IndexError as e:
                        self._json({"error": str(e)}, 404)
                    except KeyError:
                        self._json({"error": "sid 不存在"}, 404)
                    return
                m = re.fullmatch(r"/api/session/(.+)", path)
                if m:
                    try:
                        self._json(api_session(con, unquote(m.group(1))))
                    except KeyError:
                        self._json({"error": "sid 不存在"}, 404)
                    return
                if path == "/api/meta":
                    self._json(api_meta(con, db))
                elif path == "/api/facets":
                    self._json(api_facets(con, db))
                elif path == "/api/sessions":
                    self._json(api_sessions(con, db, qs))
                elif path == "/api/triage":
                    self._json(api_triage(con, db, cards_root, qs))
                elif path == "/api/reports/errors":
                    self._json(api_reports_errors(con, db, qs))
                elif path == "/api/reports/tools":
                    self._json(api_reports_tools(con, db, qs))
                elif path == "/api/reports/skills":
                    self._json(api_reports_skills(con, db, qs))
                elif path == "/api/reports/agents":
                    self._json(api_reports_agents(con, db, qs,
                                                  meta_db=suggestions_meta))
                elif path == "/api/topics":
                    self._json(api_topics(con, db, topics_meta))
                elif (m2 := re.fullmatch(r"/api/topic/(.+)/chain", path)):
                    doc = api_topic_chain(con, db, unquote(m2.group(1)),
                                          topics_meta, chain_root)
                    if doc is None:
                        self._json({"error": "chain 文档不存在或未配置"
                                             "（--topics-meta/--chain-root）"},
                                   404)
                    else:
                        self._json(doc)
                elif path == "/api/keywords":
                    self._json(api_keywords(con, db, keywords_meta, qs))
                elif path == "/api/export-analysis":
                    try:
                        obj, md = api_export_analysis(con, db, cards_root, qs)
                    except ValueError as e:
                        self._json({"error": str(e)}, 400)
                        return
                    except KeyError as e:
                        self._json({"error": f"sid 不存在: {e}"}, 404)
                        return
                    fmt = str(qs.get("format") or "md").lower()
                    if fmt == "json":
                        self._json(obj)
                    else:
                        self._text(md)
                elif path == "/api/cards":
                    try:
                        self._json(api_cards(con, db, cards_root, qs))
                    except ValueError as e:
                        self._json({"error": str(e)}, 400)
                    except FileNotFoundError as e:
                        self._json({"error": str(e)}, 404)
                else:
                    self._json({"error": f"未知路由 {path}（本服务只提供 "
                                         "/api/* 只读查询）"}, 404)
            except sqlite3.DatabaseError as e:
                self._json({"error": f"数据库错误: {e}"}, 500)
            finally:
                con.close()

        def do_POST(self) -> None:  # noqa: N802
            self._json({"error": "本服务只读，仅支持 GET"}, 405)

        do_PUT = do_POST
        do_DELETE = do_POST

    return ApiHandler


def run(db: Path, port: int = 8765, host: str = "127.0.0.1",
        token: str | None = None, cards_root: Path | None = None,
        suggestions_meta: Path | None = None,
        topics_meta: Path | None = None,
        chain_root: Path | None = None,
        keywords_meta: Path | None = None) -> int:
    """启动入口（cli 调用）。自检失败/配置非法 → 打印问题并返回 1。"""
    err = _check_host(host, token)
    if err:
        print(f"[api-serve] 拒绝启动: {err}", file=sys.stderr)
        return 1
    if not db.exists():
        print(f"[api-serve] DB 不存在: {db}（先在主项目跑 index/sync）",
              file=sys.stderr)
        return 1
    problems = self_check(db)
    if problems:
        print("[api-serve] 启动自检未通过：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    if cards_root and not Path(cards_root).is_dir():
        # 卡片目录是可选能力且可能后建（cards new 会 mkdir），警告不阻断；
        # 运行期 /api/cards 对该情形返回 404（fail loud 给客户端）。
        print(f"[api-serve] 警告: --cards-root 目录不存在: {cards_root}"
              "（/api/cards 将返回 404）", file=sys.stderr)
    srv = ThreadingHTTPServer((host, port), make_handler(db, token,
                                                         cards_root,
                                                         suggestions_meta,
                                                         topics_meta,
                                                         chain_root,
                                                         keywords_meta))
    real = srv.socket.getsockname()[1]
    tok = "（已启用 X-Token 鉴权）" if token else ""
    print(f"[api-serve] api_version={API_VERSION} schema 自检通过 | "
          f"只读服务 http://{host}:{real} {tok}", file=sys.stderr)
    print("[api-serve] Ctrl+C 停止", file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[api-serve] 已停止", file=sys.stderr)
    finally:
        srv.server_close()
    return 0
