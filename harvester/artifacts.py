# -*- coding: utf-8 -*-
"""产物提取 pass（T0-②，v0.22）——稿件演化 diff 序列。

设计稿：docs/ARTIFACT_EXTRACT_DESIGN.md（实施前冻结，本实现按稿执行）。
- 独立 meta 库 artifacts_meta.db，采集库 harvester.db 零改动（红线 §5.1）；
- 索引库 steps 表无 args 列（H1），detail 里只有 400 字 preview——完整
  args 只在原始数据源，故按需回源提取；
- MVP 只接 dsh 源（实测登记）；其余源一律 NotImplementedError——
  绝不臆测解析（适配器契约 §2.3）；
- 锚点 = (sid, seq)；turn 由消费侧按 reader.split_turns 口径推导，
  本表不冗余存储（契约 §1 T 轨锚点条款）。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .adapters.dsh import DshAdapter
from .models import to_local_ts

_SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (
    sid       TEXT NOT NULL,   -- 索引库 sessions.sid（source:session_id）
    seq       INTEGER NOT NULL,-- 该会话内提取序（0 起，按源时间序）
    ts        TEXT,            -- 调用时间（to_local_ts 口径）
    tool      TEXT NOT NULL,   -- Edit / Write / ...（源侧原名）
    file_path TEXT,            -- 目标文件
    old_text  TEXT,            -- Edit 前文；Write 为 NULL
    new_text  TEXT,            -- Edit 后文 / Write 全文
    PRIMARY KEY (sid, seq)
);
"""


def ensure_artifacts_db(meta_path: Path) -> Path:
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


def extract_dsh_records(lines: list[dict]) -> list[dict]:
    """dsh session.v4.jsonl 行流 → Write/Edit 提取记录（纯函数）。

    判定口径（设计稿 §2）：
    - Edit 类：args 同时含 old_string 与 new_string；
    - Write 类：含 file_path 与 new_string（或 content）且无 old_string；
    - 未命中两口径的调用一律跳过——绝不臆测解析。
    arguments 兼容 JSON 串与已解 dict 两种形态（实测 dsh 为 JSON 串）。
    """
    out: list[dict] = []
    for o in lines:
        if o.get("type") != "tool/call":
            continue
        data = o.get("data") or {}
        raw_args = data.get("arguments")
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                continue
        elif isinstance(raw_args, dict):
            args = raw_args
        else:
            continue
        if not isinstance(args, dict):
            continue
        old = args.get("old_string")
        new = args.get("new_string")
        fp = args.get("file_path")
        if old is not None and new is not None:          # Edit 类
            pass
        elif fp is not None and old is None and new is None \
                and args.get("content") is not None:     # Write 类（content）
            new = args["content"]
        elif fp is not None and old is None and new is not None:
            pass                                         # Write 类（new_string）
        else:
            continue                                     # 口径外：跳过
        tm = o.get("time")
        ts = to_local_ts(tm / 1000.0) if isinstance(tm, (int, float)) and tm > 0 \
            else None
        out.append({"ts": ts, "tool": str(data.get("tool") or ""),
                    "file_path": fp, "old_text": old, "new_text": new})
    return out


def _iter_dsh_lines(sid: str):
    """dsh sid（dsh:<rel>）→ 原始 jsonl 行迭代器（回源，on-demand）。"""
    if not sid.startswith("dsh:"):
        raise NotImplementedError(
            f"源 {sid.split(':', 1)[0]!r} 的产物提取未实测登记，"
            "不臆测解析（适配器契约 §2.3）")
    rel = sid[len("dsh:"):]
    ad = DshAdapter()
    f = ad.root / Path(*rel.split("/"))
    if not f.is_file():
        raise FileNotFoundError(f"dsh 原始文件不存在: {f}")
    yield from ad._lines(f)


def extract_session(meta_path: Path, db: Path, sid: str) -> int:
    """单会话提取（重建式幂等：先删该 sid 旧行再写入）。返回写入条数。"""
    meta_path = ensure_artifacts_db(meta_path)
    con = sqlite3.connect(f"file:{Path(db)}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute("SELECT sid FROM sessions WHERE sid=?",
                          (sid,)).fetchone()
    finally:
        con.close()
    if row is None:
        raise KeyError(f"索引库中找不到会话: {sid}")
    recs = extract_dsh_records(list(_iter_dsh_lines(sid)))
    con = _con(meta_path)
    try:
        con.execute("DELETE FROM artifacts WHERE sid=?", (sid,))
        for i, r in enumerate(recs):
            con.execute(
                "INSERT INTO artifacts (sid, seq, ts, tool, file_path, "
                "old_text, new_text) VALUES (?,?,?,?,?,?,?)",
                (sid, i, r["ts"], r["tool"], r["file_path"],
                 r["old_text"], r["new_text"]))
        con.commit()
        return len(recs)
    finally:
        con.close()


def show_artifacts(meta_path: Path, sid: str,
                   limit: int | None = None) -> list[dict]:
    if not Path(meta_path).is_file():
        return []
    con = _con(meta_path)
    try:
        q = ("SELECT seq, ts, tool, file_path, old_text, new_text "
             "FROM artifacts WHERE sid=? ORDER BY seq")
        if limit:
            q += f" LIMIT {int(limit)}"
        return [dict(r) for r in con.execute(q, (sid,)).fetchall()]
    finally:
        con.close()
