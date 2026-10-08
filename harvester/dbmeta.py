# -*- coding: utf-8 -*-
"""库快照指纹（v0.19）——所有产物（G1-G4/triage/cards、API meta）统一携带。

目的：产物可追溯、可判陈旧。消费方拿到任何报告/JSON，先看指纹再信数字；
指纹变化 = 库已重索引，旧产物数字不可与新产物混排对比。

指纹字段（冻结）：
- sessions     会话总数（sessions 表行数）；
- steps        步骤总数（steps 表行数，含 call/result 两行口径）；
- errors       错误步骤数（steps.status='error' 的 result 行）；
- db_mtime     索引库文件 mtime（'YYYY-MM-DD HH:MM:SS' 本地时间）；
- generated_at 产物生成时刻（同格式）。

约束：查询必须走调用方连接（api-serve authorizer 守卫），不自开连接；
db_mtime 取自文件系统 stat，不经 SQL。
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path


def _fmt_ts(epoch: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch))


def db_fingerprint(db: Path, con: sqlite3.Connection | None = None) -> dict:
    """计算库快照指纹。con 为空时自开只读连接（mode=ro，CLI 兼容）；
    传入时 db 仅用于 mtime，查询全部走 con。"""
    own = con is None
    if own:
        con = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    try:
        sessions = con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        steps = con.execute("SELECT COUNT(*) FROM steps").fetchone()[0]
        errors = con.execute(
            "SELECT COUNT(*) FROM steps WHERE status='error'").fetchone()[0]
    finally:
        if own:
            con.close()
    try:
        mtime = Path(db).stat().st_mtime
        db_mtime = _fmt_ts(mtime)
    except OSError:
        db_mtime = ""
    return {"sessions": sessions, "steps": steps, "errors": errors,
            "db_mtime": db_mtime,
            "generated_at": _fmt_ts(time.time())}


def fingerprint_line(fp: dict) -> str:
    """指纹 → 单行产物头（Markdown blockquote 内容，CLI 报告首行用）。"""
    return (f"库快照：{fp['sessions']} 会话 / {fp['steps']} 步骤 / "
            f"{fp['errors']} 错误 @ {fp['db_mtime']}"
            f"（生成于 {fp['generated_at']}）")
