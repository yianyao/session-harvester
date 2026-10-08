# -*- coding: utf-8 -*-
"""建议池状态存储（suggestion_status 表）——G2 建议池的唯一持久化元数据。

边界声明（只读铁律的适用范围）：harvester.db 是采集库，全程只读；
建议池本身无状态，人工审阅结论（采纳/拒绝）需要落库时写到**独立的
meta 库**（默认 <项目根>/suggestions_meta.db 或 --meta 指定路径），
与本套件的分析产物一致，属于元数据而非采集数据。

表结构（冻结，additive 演进）：
  suggestion_status(
      key        TEXT PRIMARY KEY,   -- 建议条目 key：entries 用 title，
                                     -- leftover 用 pattern（两者均跨运行稳定）
      status     TEXT NOT NULL,      -- pending/adopted/rejected
      updated_at TEXT NOT NULL)      -- 'YYYY-MM-DD HH:MM:SS' 本地时间
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

STATUSES = ("pending", "adopted", "rejected")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS suggestion_status (
    key        TEXT PRIMARY KEY,
    status     TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


def load_statuses(meta_db: Path | None) -> dict[str, str]:
    """读取全部状态映射 {key: status}。meta_db 不存在/不可读 → 空表。"""
    if meta_db is None or not Path(meta_db).is_file():
        return {}
    con = sqlite3.connect(str(meta_db))
    try:
        rows = con.execute("SELECT key, status FROM suggestion_status").fetchall()
    except sqlite3.OperationalError:  # 表未建
        return {}
    finally:
        con.close()
    return {k: s for k, s in rows if s in STATUSES}


def set_status(meta_db: Path, key: str, status: str) -> None:
    """写入/更新一条状态。status 必须在 STATUSES 内，否则 ValueError。"""
    if status not in STATUSES:
        raise ValueError(f"status={status!r} 不在 {list(STATUSES)}")
    if not key:
        raise ValueError("key 不能为空")
    meta_db = Path(meta_db)
    meta_db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(meta_db))
    try:
        con.execute(_SCHEMA)
        con.execute(
            "INSERT INTO suggestion_status(key, status, updated_at) "
            "VALUES(?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
            "status=excluded.status, updated_at=excluded.updated_at",
            (key, status, time.strftime("%Y-%m-%d %H:%M:%S")))
        con.commit()
    finally:
        con.close()
