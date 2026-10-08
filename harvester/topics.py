# -*- coding: utf-8 -*-
"""主题注册表（T1 MVP，v0.22）——T 轨的确定性一半。

topics_meta.db 是独立 meta 库（suggestions_meta.db 范式），采集库
harvester.db 只读（红线 §5.1：schema 冻结，新状态类数据一律写 meta 库）。

口径：
- topic = 语义边界由用户裁决的主题（名称 + 关键词 + 成员会话证据）；
  自动聚类只产候选（T5），不当权威——本模块即权威。
- title_chain（T1 唯一产物档）：成员会话标题按 created_at 排序去重
  （每标题带首现时间）+ 月度分布；产物挂 db_fingerprint，同库快照
  重跑 byte 级一致（可复现红线）。
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .dbmeta import db_fingerprint

_SCHEMA = """
CREATE TABLE IF NOT EXISTS topics (
    id       TEXT PRIMARY KEY,
    name     TEXT NOT NULL UNIQUE,
    keywords TEXT NOT NULL DEFAULT '[]',
    members  TEXT NOT NULL DEFAULT '[]',
    created  TEXT NOT NULL
);
"""


def ensure_topics_db(meta_path: Path) -> Path:
    """建库建表（幂等），返回路径。"""
    meta_path = Path(meta_path)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(meta_path))
    try:
        con.executescript(_SCHEMA)
        con.commit()
    finally:
        con.close()
    return meta_path


def _con(meta_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(meta_path))
    con.row_factory = sqlite3.Row
    return con


def register_topic(meta_path: Path, name: str, keywords: list[str] | None = None,
                   note: str | None = None) -> str:
    """注册主题。id 自动编号 tp-YYYYMMDD-NNN（同日重名即幂等返回旧 id）。"""
    meta_path = Path(meta_path)
    ensure_topics_db(meta_path)
    con = _con(meta_path)
    try:
        row = con.execute("SELECT id FROM topics WHERE name=?", (name,)).fetchone()
        if row:
            return row["id"]
        today = time.strftime("%Y%m%d")
        n = con.execute("SELECT COUNT(*) FROM topics WHERE id LIKE ?",
                        (f"tp-{today}-%",)).fetchone()[0]
        tid = f"tp-{today}-{n + 1:03d}"
        con.execute(
            "INSERT INTO topics (id, name, keywords, members, created) "
            "VALUES (?,?,?,?,?)",
            (tid, name, json.dumps(keywords or [], ensure_ascii=False),
             json.dumps([], ensure_ascii=False), time.strftime("%Y-%m-%d %H:%M")))
        con.commit()
        return tid
    finally:
        con.close()


def add_members(meta_path: Path, topic_id: str, sids: list[str],
                evidence: str = "") -> int:
    """加成员（幂等，逐 sid 记证据）。返回实际新增数。"""
    con = _con(meta_path)
    try:
        row = con.execute("SELECT members FROM topics WHERE id=?",
                          (topic_id,)).fetchone()
        if row is None:
            raise KeyError(f"主题不存在: {topic_id}")
        members = json.loads(row["members"])
        have = {m["sid"] for m in members}
        added = 0
        for sid in sids:
            if sid in have:
                continue
            members.append({"sid": sid, "evidence": evidence})
            have.add(sid)
            added += 1
        con.execute("UPDATE topics SET members=? WHERE id=?",
                    (json.dumps(members, ensure_ascii=False), topic_id))
        con.commit()
        return added
    finally:
        con.close()


def remove_member(meta_path: Path, topic_id: str, sid: str) -> None:
    con = _con(meta_path)
    try:
        row = con.execute("SELECT members FROM topics WHERE id=?",
                          (topic_id,)).fetchone()
        if row is None:
            raise KeyError(f"主题不存在: {topic_id}")
        members = [m for m in json.loads(row["members"]) if m["sid"] != sid]
        con.execute("UPDATE topics SET members=? WHERE id=?",
                    (json.dumps(members, ensure_ascii=False), topic_id))
        con.commit()
    finally:
        con.close()


def list_topics(meta_path: Path) -> list[dict]:
    if not Path(meta_path).is_file():
        return []
    con = _con(meta_path)
    try:
        rows = con.execute("SELECT id, name, keywords, members, created "
                           "FROM topics ORDER BY id").fetchall()
        return [{"id": r["id"], "name": r["name"],
                 "keywords": json.loads(r["keywords"]),
                 "members": len(json.loads(r["members"])),
                 "created": r["created"]} for r in rows]
    finally:
        con.close()


def show_topic(meta_path: Path, topic_id: str) -> dict:
    con = _con(meta_path)
    try:
        row = con.execute("SELECT id, name, keywords, members, created "
                          "FROM topics WHERE id=?", (topic_id,)).fetchone()
        if row is None:
            raise KeyError(f"主题不存在: {topic_id}")
        return {"id": row["id"], "name": row["name"],
                "keywords": json.loads(row["keywords"]),
                "members": json.loads(row["members"]),
                "created": row["created"]}
    finally:
        con.close()


def title_chain(meta_path: Path, topic_id: str,
                db: Path | None = None,
                con: sqlite3.Connection | None = None) -> dict:
    """标题时间序链（T1 产物）：成员会话标题按 created_at 排序去重
    （每标题带首现时间）+ 月度分布 + 库快照指纹。

    con：外部连接（mode=ro + authorizer 口径）；缺省自开只读连接。
    成员 sid 不在索引库 → 进 missing（不静默丢）。
    """
    topic = show_topic(meta_path, topic_id)
    sids = [m["sid"] for m in topic["members"]]
    if con is None and db is not None:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        own = True
    else:
        own = False
    rows: list = []
    missing: list[str] = []
    fp = None
    try:
        for sid in sids:
            r = con.execute(
                "SELECT sid, session_id, title, created_at FROM sessions "
                "WHERE sid=? OR session_id=? ORDER BY CASE WHEN sid=? "
                "THEN 0 ELSE 1 END LIMIT 1", (sid, sid, sid)).fetchone()
            if r is None:
                missing.append(sid)
                continue
            rows.append({"sid": r["sid"], "title": r["title"] or "",
                         "created_at": r["created_at"] or ""})
        # 指纹在连接存活期间计算
        fp = db_fingerprint(db, con=con)
    finally:
        if own:
            con.close()
    # 标题去重：同标题只留最早首现
    rows.sort(key=lambda r: r["created_at"])
    seen: dict[str, dict] = {}
    monthly: dict[str, int] = {}
    for r in rows:
        if r["title"] and r["title"] not in seen:
            seen[r["title"]] = r
        if r["created_at"]:
            monthly[r["created_at"][:7]] = monthly.get(r["created_at"][:7], 0) + 1
    out_rows = [{"title": t, "first_seen": r["created_at"], "sid": r["sid"]}
                for t, r in sorted(seen.items(),
                                   key=lambda kv: kv[1]["created_at"])]
    return {"topic": {"id": topic["id"], "name": topic["name"],
                      "keywords": topic["keywords"]},
            "members": len(sids), "rows": out_rows,
            "monthly": dict(sorted(monthly.items())),
            "missing": missing, "db_fingerprint": fp}


def render_title_chain(chain: dict) -> str:
    """chain dict → 确定性 Markdown 产物（byte 级可复现）。"""
    t = chain["topic"]
    lines = [f"# 主题时间线：{t['name']}", ""]
    lines.append(f"- 主题 {t['id']}｜关键词 {('、'.join(t['keywords'])) or '（无）'}"
                 f"｜成员 {chain['members']} 会话")
    if chain.get("missing"):
        lines.append(f"- 未入索引库成员 {len(chain['missing'])} 个"
                     "（已列名，不静默丢）")
    fp = chain.get("db_fingerprint")
    if fp:
        from .dbmeta import fingerprint_line
        lines.append(f"- 库快照：{fingerprint_line(fp)}")
    lines += ["", "## 标题演进链（时间序去重，每标题带首现时间）", ""]
    for i, r in enumerate(chain["rows"], 1):
        lines.append(f"{i}. `{r['first_seen'][:10]}` {r['title']}（{r['sid']}）")
    lines += ["", "## 月度分布", ""]
    for m, n in chain["monthly"].items():
        lines.append(f"- {m}: {n}")
    if chain.get("missing"):
        lines += ["", "## 未命中索引库的成员", ""]
        for sid in chain["missing"]:
            lines.append(f"- {sid}")
    return "\n".join(lines).rstrip() + "\n"
