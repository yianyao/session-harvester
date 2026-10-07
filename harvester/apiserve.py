# -*- coding: utf-8 -*-
"""api-serve —— 只读 HTTP JSON API（stdio mcp-serve 之外的通用查询面）。

定位：通用只读查询能力，供任意前端/脚本经 HTTP 消费本套件索引库。
安全设计（本命令的生命线）：

1. **只读**：连接以 ``file:...?mode=ro`` 打开 + SQLite authorizer 白名单
   （SELECT/READ/聚合函数放行，其余内核级 deny）——双保险，进程内想写
   也写不进；FTS 检索与 skill 扫描复用同一连接，**全部查询路径**均处于
   authorizer 之下；
2. **自检 fail loud**：启动时逐列比对冻结 schema、FTS 冒烟，任何缺失拒绝
   启动（绝不让客户端拿到静默错的数据）；
3. **不裸奔**：默认绑 127.0.0.1；``--host`` 给非回环地址时必须配
   ``--token``（请求须带 ``X-Token`` 头），否则拒绝启动；
4. 不设 CORS 头——浏览器同源策略天然阻止任意网页 drive-by 读取。

接口版本：/api/meta 返回 api_version=1；未来演进只增端点不改语义，
major 变更须消费方同步升级。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
import sys
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__
from .behstats import collect_skill_invocations
from .indexing import search
from .reader import split_turns

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


# ---------- 查询逻辑（纯函数，可测，连接由调用方给） ----------

def _schema_fingerprint() -> str:
    """冻结 schema 指纹（设计稿 §3 承诺的 meta 字段）：EXPECTED_SCHEMA
    规范序列化的 sha256 前 12 位。主项目改表 → 指纹变化 → 消费方可
    感知（api_version 之外的第二根漂移探针）。"""
    canon = json.dumps(EXPECTED_SCHEMA, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:12]


def api_meta(con: sqlite3.Connection) -> dict:
    n = con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    row = con.execute("SELECT MIN(updated_at), MAX(updated_at) "
                      "FROM sessions").fetchone()
    return {"api_version": API_VERSION, "server": "session-harvester",
            "package_version": __version__, "readonly": True,
            "schema_fingerprint": _schema_fingerprint(),
            "sessions": n,
            "time_min": row[0], "time_max": row[1]}


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
    try:
        limit = min(int(params.get("limit") or 50), 200)
    except ValueError:
        limit = 50
    try:
        offset = max(int(params.get("offset") or 0), 0)
    except ValueError:
        offset = 0
    return {"api_version": API_VERSION, "total": total,
            "offset": offset, "limit": limit,
            "items": items[offset:offset + limit]}


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
            "n_turns": len(out_turns), "turns": out_turns}


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


# ---------- HTTP 层（薄壳） ----------

def make_handler(db: Path, token: str | None):
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
                    self._json(api_meta(con))
                elif path == "/api/facets":
                    self._json(api_facets(con, db))
                elif path == "/api/sessions":
                    self._json(api_sessions(con, db, qs))
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
        token: str | None = None) -> int:
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
    srv = ThreadingHTTPServer((host, port), make_handler(db, token))
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
