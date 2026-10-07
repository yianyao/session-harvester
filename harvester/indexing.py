# -*- coding: utf-8 -*-
"""FTS5 全文检索层：把会话索引进 SQLite，供 harvester search 查询。

设计：
- 零依赖：标准库 sqlite3（Python 3.10+ 的官方构建含 FTS5；运行时探测，
  不可用时给明确报错而非静默降级）。
- 索引来源两种：export 产物目录（*.json 结构化原文）或数据源实时扫描（--all）。
- 每次索引为整库重建（个人历史规模在毫秒级，简单优于增量；
  会话规模大到重建变慢时再考虑 mtime 增量）。
- 分词：unicode61 + CJK bigram 预改写（v0.5，2026-10-05 实测）。unicode61
  把整段连续汉字当一个 token，中文检索基本不可用（2 字词命中率 0/8 字样
  中的 3 个）；trigram 对 2 字查询同样为 0 命中（FTS5 少于 3 字符不匹配）。
  方案：插入与查询两侧把 CJK 连续串改写为重叠二元组（Lucene CJKAnalyzer
  语义），实测 2-5 字查询 100% 命中；索引体积约 1.9x（规模可接受）。
  原文另存 raw 列供结果展示。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import normalize_text
from .outline import scan_all

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    sid TEXT PRIMARY KEY,          -- source + ':' + session_id
    source TEXT NOT NULL,
    session_id TEXT NOT NULL,
    title TEXT,
    category TEXT,
    created_at TEXT,
    updated_at TEXT,
    file TEXT,
    model TEXT                     -- 主模型（v0.15，transcript 源从 providerData 提取；其余源 NULL）
);
CREATE VIRTUAL TABLE IF NOT EXISTS messages USING fts5(
    sid UNINDEXED, role UNINDEXED, ts UNINDEXED, text, raw UNINDEXED,
    tokenize='unicode61'
);
CREATE TABLE IF NOT EXISTS steps (
    sid TEXT NOT NULL,             -- source + ':' + session_id
    seq INTEGER NOT NULL,          -- 会话内顺序（按消息出现次序）
    ts TEXT,
    tool TEXT NOT NULL,
    phase TEXT NOT NULL,           -- call | result
    status TEXT,                   -- result 相：ok / error / 源端原文
    error TEXT,                    -- 错误码或错误摘要
    detail TEXT                    -- 入参（call 相）/ 出参摘要（result 相）
);
CREATE INDEX IF NOT EXISTS idx_steps_sid ON steps(sid, seq);
CREATE INDEX IF NOT EXISTS idx_steps_tool ON steps(tool);
"""


def fts5_available() -> bool:
    try:
        con = sqlite3.connect(":memory:")
        try:
            con.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        finally:
            con.close()  # 探测失败路径也关闭连接
        return True
    except sqlite3.OperationalError:
        return False


def _cjk_bigram(text: str) -> str:
    """把文本中每个 CJK 连续串改写为空格分隔的重叠二元组。

    "登录态" → "登录 录态"；非 CJK 片段原样保留。单个 CJK 字符无法
    成对，原样保留（查询侧对该情形用前缀查询兜底）。
    """

    def _run(s: str) -> str:
        if len(s) < 2:
            return s
        return " ".join(s[i:i + 2] for i in range(len(s) - 1))

    pieces: list[str] = []
    run: list[str] = []
    ascii_run: list[str] = []
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            if ascii_run:
                pieces.append("".join(ascii_run))
                ascii_run = []
            run.append(ch)
        else:
            if run:
                pieces.append(_run("".join(run)))
                run = []
            ascii_run.append(ch)
    if run:
        pieces.append(_run("".join(run)))
    if ascii_run:
        pieces.append("".join(ascii_run))
    out = " ".join(pieces)
    return " ".join(out.split())  # 折叠连续空白


def _connect(db: Path) -> sqlite3.Connection:
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db))
    # 消息表 schema 带 raw 列（v0.5）；旧库（4 列）直接重建——
    # 两个索引入口本来就是整库重建，删除无副作用。
    cols = 0
    if _fts_table_exists(con):
        try:
            cur = con.execute("SELECT * FROM messages LIMIT 0")
            cols = len(cur.description)
        except sqlite3.DatabaseError:
            cols = 0
    if cols != 5:
        con.execute("DROP TABLE IF EXISTS messages")
    con.executescript(SCHEMA)
    # 旧库迁移：sessions 加 model 列（已存在则忽略）
    try:
        con.execute("ALTER TABLE sessions ADD COLUMN model TEXT")
    except sqlite3.OperationalError:
        pass
    return con


def _fts_table_exists(con: sqlite3.Connection) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='messages'"
    ).fetchone()
    return row is not None


# ---- 索引构建 ----

class _RawMsg:
    """index_exports 用：把导出 JSON 的 message dict 适配成 _step_of()
    期望的 `.raw` / `.timestamp` 属性接口（与 Message 鸭子类型兼容）。"""

    def __init__(self, raw: dict, timestamp):
        self.raw = raw
        self.timestamp = timestamp


def _step_of(m) -> dict | None:
    """从 Message.raw 提取规范化 tool 步骤；非工具消息返回 None。

    兼容两种 raw 形态（2026-10-06 实测）：
    - 规范化：{"kind": "tool_call"|"tool_result", "tool", "status", "error", "detail"}
    - AutoClaw payload：{"entry_type": ..., "data": {"toolName", "status", "error"}}
    """
    raw = m.raw if isinstance(m.raw, dict) else None
    if not raw:
        return None
    kind = raw.get("kind") or raw.get("entry_type")
    if kind == "tool_call":
        data = raw.get("data") or {}
        tool = raw.get("tool") or data.get("toolName") or data.get("tool")
        if not tool:
            return None
        detail = raw.get("detail")
        if detail is None and data.get("input") is not None:
            detail = json.dumps(data["input"], ensure_ascii=False)[:400]
        return {"tool": str(tool), "phase": "call", "status": None,
                "error": None, "detail": detail}
    if kind == "tool_result":
        data = raw.get("data") or {}
        tool = raw.get("tool") or data.get("toolName") or data.get("tool")
        if not tool:
            return None
        status = raw.get("status") or data.get("status") or "unknown"
        err = raw.get("error")
        if err is None:
            e = data.get("error")
            if isinstance(e, dict):
                err = str(e.get("code") or e.get("message") or "") or None
            elif e:
                err = str(e)
        if isinstance(err, str) and len(err) > 200:
            err = err[:200]
        return {"tool": str(tool), "phase": "result", "status": str(status),
                "error": err, "detail": raw.get("detail")}
    return None


def index_session(con: sqlite3.Connection, rec, file: str | None = None,
                  category: str | None = None,
                  include_notes: bool = True) -> int:
    """写入/覆盖一个会话（先删后插）。返回索引的消息条数。

    steps 表从全部消息构建（工具步骤不受 include_notes 过滤影响——
    统计口径独立于 FTS 收录范围）。
    """
    sid = f"{rec.source}:{rec.session_id}"
    con.execute("DELETE FROM sessions WHERE sid=?", (sid,))
    con.execute("DELETE FROM messages WHERE sid=?", (sid,))
    con.execute("DELETE FROM steps WHERE sid=?", (sid,))
    con.execute(
        "INSERT INTO sessions (sid, source, session_id, title, category, "
        "created_at, updated_at, file, model) VALUES (?,?,?,?,?,?,?,?,?)",
        (sid, rec.source, rec.session_id, rec.title, category,
         rec.created_at, rec.updated_at, file,
         rec.extra.get("model") if isinstance(rec.extra, dict) else None))
    roles = ("user", "assistant", "note") if include_notes \
        else ("user", "assistant")
    n = 0
    seq = 0
    for m in rec.messages:
        step = _step_of(m)
        if step:
            con.execute("INSERT INTO steps VALUES (?,?,?,?,?,?,?,?)",
                        (sid, seq, m.timestamp, step["tool"], step["phase"],
                         step["status"], step["error"], step["detail"]))
            seq += 1
        if m.role not in roles:
            continue
        text = normalize_text(m.text).strip()
        if not text:
            continue
        con.execute("INSERT INTO messages VALUES (?,?,?,?,?)",
                    (sid, m.role, m.timestamp, _cjk_bigram(text), text))
        n += 1
    return n


def index_exports(export_dir: Path, db: Path, verbose: bool = False) -> dict:
    """从 export 产物目录索引全部 *.json（排除 manifest）。"""
    export_dir = Path(export_dir)
    jsons = [p for p in export_dir.rglob("*.json")
             if p.name != "export_manifest.json"]
    con = _connect(db)
    try:
        con.execute("DELETE FROM sessions")
        con.execute("DELETE FROM messages")
        con.execute("DELETE FROM steps")
        n_sessions, n_msgs = 0, 0
        n_bad = 0
        for p in jsons:
            # 单个文件损坏/读不出只跳过该文件，不拖垮整库重建
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                n_bad += 1
                if verbose:
                    print(f"  [skip] {p.name}: {e}")
                continue
            if not isinstance(data, dict) or "messages" not in data:
                continue
            sid = f"{data.get('source', '?')}:{data.get('session_id', p.stem)}"
            # export 目录布局为 <category>/<YYYY-MM>/<file>.json，取首段为大类
            rel = p.relative_to(export_dir).parts
            category = rel[0] if len(rel) >= 3 else None
            con.execute("DELETE FROM messages WHERE sid=?", (sid,))
            con.execute("DELETE FROM steps WHERE sid=?", (sid,))
            con.execute(
                "INSERT OR REPLACE INTO sessions (sid, source, session_id, "
                "title, category, created_at, updated_at, file, model) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (sid, data.get("source", "?"), data.get("session_id", ""),
                 data.get("title", ""), category,
                 data.get("created_at"), data.get("updated_at"),
                 str(p),
                 (data.get("extra") or {}).get("model")
                 if isinstance(data.get("extra"), dict) else None))
            seq = 0
            for m in data["messages"]:
                raw = m.get("raw")
                if isinstance(raw, dict):
                    step = _step_of(_RawMsg(raw, m.get("timestamp")))
                    if step:
                        con.execute(
                            "INSERT INTO steps VALUES (?,?,?,?,?,?,?,?)",
                            (sid, seq, m.get("timestamp"), step["tool"],
                             step["phase"], step["status"], step["error"],
                             step["detail"]))
                        seq += 1
                text = normalize_text(m.get("text", "")).strip()
                if not text:
                    continue
                con.execute("INSERT INTO messages VALUES (?,?,?,?,?)",
                            (sid, m.get("role", "note"), m.get("timestamp"),
                             _cjk_bigram(text), m.get("text", "")))
                n_msgs += 1
            n_sessions += 1
            if verbose:
                print(f"  [idx] {p.name}")
        con.commit()
    finally:
        con.close()
    return {"sessions": n_sessions, "messages": n_msgs, "db": str(db),
            "skipped": n_bad}


def index_sources(sources: dict | None, db: Path,
                  include_notes: bool = False,
                  verbose: bool = False) -> dict:
    """实时扫描全部可用数据源并索引。"""
    adapters_list, items, _reports = scan_all(sources=sources)
    adapters = {ad.id: ad for ad in adapters_list}
    con = _connect(db)
    try:
        con.execute("DELETE FROM sessions")
        con.execute("DELETE FROM messages")
        con.execute("DELETE FROM steps")
        n_sessions, n_msgs = 0, 0
        for it in items:
            ad = adapters[it["adapter"]]
            try:
                rec = ad.load_session(it["session_id"])
            except Exception:  # noqa: BLE001 - 单会话失败不拖垮索引
                continue
            n_msgs += index_session(con, rec, category=it.get("category"),
                                    include_notes=include_notes)
            n_sessions += 1
            if verbose:
                print(f"  [idx] {it['adapter']}#{it['session_id'][:12]}")
        con.commit()
    finally:
        con.close()
    return {"sessions": n_sessions, "messages": n_msgs, "db": str(db)}


# ---- 检索 ----

def _fts_query(raw: str) -> str:
    """把自由文本转成安全 FTS5 查询：token 逐个加引号，词间 OR。

    用户直觉是"包含任一词"（ai-hist search 语义）；FTS5 原生隐式 AND
    对多词查询过严。带引号防注入 FTS 语法。
    CJK token 同步做 bigram 改写（与插入侧一致）后作为短语查询；
    改写后仍为单字符的（单字查询）用前缀查询兜底。
    """
    tokens = [t for t in raw.replace('"', " ").split() if t]
    if not tokens:
        return '""'
    parts: list[str] = []
    for t in tokens:
        rewritten = _cjk_bigram(t)
        if rewritten == t or " " not in rewritten:
            # 纯 ASCII / 单个 CJK 字符：原文直查；单 CJK 字用前缀兜底
            if len(rewritten) == 1 and "\u4e00" <= rewritten <= "\u9fff":
                parts.append(f'"{rewritten}*"')
            else:
                parts.append(f'"{rewritten}"')
        else:
            parts.append(f'"{rewritten}"')  # 引号内多 token = 短语查询
    return " OR ".join(parts)


def _snippet(raw: str, query: str, width: int = 60) -> str:
    """在原文中定位首个查询词位置并截窗展示（替代 FTS snippet，
    因索引列为 bigram 改写文本、直接展示会变成"二元组汤"）。"""
    low = raw.lower()
    pos = -1
    for t in query.replace('"', " ").split():
        t = t.strip()
        if not t:
            continue
        pos = low.find(t.lower())
        if pos >= 0:
            break
    if pos < 0:
        pos = 0
    start = max(0, pos - 20)
    end = min(len(raw), start + width * 2)
    seg = raw[start:end]
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(raw) else ""
    return f"{prefix}{seg}{suffix}"


def search(db: Path, query: str, limit: int = 20,
           source: str | None = None) -> list[dict]:
    """全文检索。返回 [{sid, source, title, role, snippet, rank}]。"""
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        sql = """
            SELECT ms.sid, se.source, se.title, ms.role, ms.ts, ms.raw
            FROM messages ms JOIN sessions se ON se.sid = ms.sid
            WHERE messages MATCH ?
        """
        params: list = [_fts_query(query)]
        if source:
            sql += " AND se.source = ?"
            params.append(source)
        sql += " ORDER BY bm25(messages) LIMIT ?"
        params.append(limit)
        rows = con.execute(sql, params).fetchall()
        return [{"sid": r["sid"], "source": r["source"], "title": r["title"],
                 "role": r["role"], "ts": r["ts"],
                 "snippet": _snippet(r["raw"] or "", query),
                 "rank": i}
                for i, r in enumerate(rows)]
    finally:
        con.close()


def sessions_in_db(db: Path) -> int:
    con = sqlite3.connect(str(db))
    try:
        return con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    finally:
        con.close()
