# -*- coding: utf-8 -*-
"""库快照指纹（v0.19）+ 库龄/陈旧判定（v0.46 P0-2）——产物统一携带。

目的：产物可追溯、可判陈旧。消费方拿到任何报告/JSON，先看指纹再信数字；
指纹变化 = 库已重索引，旧产物数字不可与新产物混排对比。

指纹字段（v0.19 冻结 + v0.46 additive）：
- sessions     会话总数（sessions 表行数）；
- steps        步骤总数（steps 表行数，含 call/result 两行口径）；
- errors       错误步骤数（steps.status='error' 的 result 行）；
- db_mtime     索引库文件 mtime（'YYYY-MM-DD HH:MM:SS' 本地时间）；
- generated_at 产物生成时刻（同格式）；
- corpus_mtime          调用方传入的**当前语料**最新 mtime（不传 = None）；
- corpus_indexed_mtime  库内记录的**上次索引时看到的**语料水位；
- stale                 语料比库新 ⇒ True；False = 新鲜；**None = 未判定**
                        （调用方没做 stat，或库内还没有水位记录）。

**为什么不用"库文件 mtime"当基准**（2026-10-12 实测纠偏）：无变化的
`update` 不会写库文件（`CREATE TABLE IF NOT EXISTS` 与空 commit 都不落盘），
库 mtime 停在原地，`touch 会话文件 + update` 之后仍会判 STALE——那是**假
STALE**。改用"上次索引时观察到的语料水位"（`index_meta` 表）后，
update 记下它实际看到的最新 mtime，touch 场景才会正确转绿。

约束：查询必须走调用方连接（api-serve authorizer 守卫），不自开连接；
db_mtime 取自文件系统 stat，不经 SQL。
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

#: 索引元信息表 DDL（v0.46 P0-2）。表由 indexing.SCHEMA 建（写入方），
#: dbmeta 只读/写键值；两处共用一个定义，避免表名写两遍漂移。
META_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS index_meta (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT
);
"""

META_CORPUS_MTIME = "corpus_mtime"          # 上次索引时语料最新 mtime（epoch 秒）
META_CORPUS_SOURCES = "corpus_sources"      # {"covered": [...], "uncovered": [...]}
META_CORPUS_AT = "corpus_recorded_at"       # 记录时刻（可读时间串）


def _fmt_ts(epoch: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch))


def ro_con(db: Path, con: sqlite3.Connection | None = None
           ) -> tuple[sqlite3.Connection, bool]:
    """索引库只读连接收口（v0.51 S3，统计面统一走这里）。

    con 透传（api-serve 传入 mode=ro + authorizer 连接，内核级只读）；
    缺省时**存在性预检 + mode=ro 打开**——缺库 fail loud（FileNotFoundError），
    绝不 `sqlite3.connect(path)` 静默建空库（原则③：坏库不许当空库用）。
    返回 (con, own)；own=True 表示本函数自开，调用方负责 close。
    """
    if con is not None:
        return con, False
    db = Path(db)
    if not db.is_file():
        raise FileNotFoundError(f"索引库不存在: {db}（先运行 harvester index）")
    c = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c, True


def record_corpus_watermark(db: Path, wm: dict) -> None:
    """把"本次索引实际看到的语料水位"写进库（= STALE 判定的基准）。

    wm 为 `outline.corpus_watermark()` 的返回值。`mtime is None`（一个源都
    覆盖不到）时**不写**——宁可不记，也不记一个假的"最新"。
    """
    if not isinstance(wm, dict) or wm.get("mtime") is None:
        return
    db = Path(db)
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db))
    try:
        con.executescript(META_TABLE_DDL)
        rows = [(META_CORPUS_MTIME, repr(float(wm["mtime"])), _fmt_ts(time.time())),
                (META_CORPUS_SOURCES, json.dumps(
                    {"covered": wm.get("covered", []),
                     "uncovered": wm.get("uncovered", [])},
                    ensure_ascii=False), _fmt_ts(time.time())),
                (META_CORPUS_AT, _fmt_ts(time.time()), _fmt_ts(time.time()))]
        con.executemany("INSERT OR REPLACE INTO index_meta (key, value, "
                        "updated_at) VALUES (?,?,?)", rows)
        con.commit()
    finally:
        con.close()


def read_corpus_watermark(db: Path | None = None,
                          con: sqlite3.Connection | None = None) -> dict:
    """读回水位。缺表 / 缺键 / 值损坏 → mtime=None（**未判定，不是新鲜**）。"""
    own = con is None
    if own:
        if db is None:
            return {"mtime": None, "covered": [], "uncovered": [],
                    "recorded_at": None}
        try:
            con = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
        except sqlite3.Error:
            return {"mtime": None, "covered": [], "uncovered": [],
                    "recorded_at": None}
    try:
        try:
            rows = dict(con.execute("SELECT key, value FROM index_meta"))
        except sqlite3.DatabaseError:
            return {"mtime": None, "covered": [], "uncovered": [],
                    "recorded_at": None}
    finally:
        if own:
            con.close()
    try:
        mtime = float(rows[META_CORPUS_MTIME])
    except (KeyError, TypeError, ValueError):
        mtime = None
    try:
        src = json.loads(rows.get(META_CORPUS_SOURCES) or "{}")
    except (json.JSONDecodeError, TypeError):
        src = {}
    return {"mtime": mtime,
            "covered": list(src.get("covered", []) or []),
            "uncovered": list(src.get("uncovered", []) or []),
            "recorded_at": rows.get(META_CORPUS_AT)}


def db_fingerprint(db: Path, con: sqlite3.Connection | None = None,
                   corpus_mtime: float | None = None) -> dict:
    """计算库快照指纹。con 为空时自开只读连接（mode=ro，CLI 兼容）；
    传入时 db 仅用于 mtime，查询全部走 con。

    corpus_mtime：**调用方**测得的当前语料最新 mtime（epoch 秒；不传 = 不做
    库龄判定，`stale=None`——"未判定"与"新鲜"必须区分得开）。
    """
    own = con is None
    if own:
        con = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    try:
        sessions = con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        steps = con.execute("SELECT COUNT(*) FROM steps").fetchone()[0]
        errors = con.execute(
            "SELECT COUNT(*) FROM steps WHERE status='error'").fetchone()[0]
        wm = read_corpus_watermark(db, con=con)
    finally:
        if own:
            con.close()
    try:
        mtime = Path(db).stat().st_mtime
        db_mtime = _fmt_ts(mtime)
    except OSError:
        db_mtime = ""
    stale: bool | None = None
    if corpus_mtime is not None and wm["mtime"] is not None:
        stale = float(corpus_mtime) > float(wm["mtime"])
    return {"sessions": sessions, "steps": steps, "errors": errors,
            "db_mtime": db_mtime,
            "generated_at": _fmt_ts(time.time()),
            "corpus_mtime": (_fmt_ts(corpus_mtime)
                             if corpus_mtime is not None else None),
            "corpus_indexed_mtime": (_fmt_ts(wm["mtime"])
                                     if wm["mtime"] is not None else None),
            "stale": stale}


#: 库快照前缀的唯一来源——调用方一律用本常量拼接，禁止再写字面量。
#: 历史缺陷：topics.py 曾写 f"- 库快照：{fingerprint_line(fp)}"，
#: 而 fingerprint_line 已自带前缀 → 产物出现"库快照：库快照："。
FINGERPRINT_PREFIX = "库快照："


def fingerprint_line(fp: dict) -> str:
    """指纹 → 单行产物头（Markdown blockquote 内容，CLI 报告首行用）。

    返回值**含** FINGERPRINT_PREFIX；调用方直接使用，勿再补前缀。
    `fp["stale"] is True` 时追加 STALE 标记（只在**确实判为陈旧**时出现：
    未判定 None 不加噪音，但 JSON 里 `stale: null` 仍与 `false` 可区分）。
    """
    line = (f"{FINGERPRINT_PREFIX}{fp['sessions']} 会话 / {fp['steps']} 步骤 / "
            f"{fp['errors']} 错误 @ {fp['db_mtime']}"
            f"（生成于 {fp['generated_at']}）")
    if fp.get("stale") is True:
        line += (f"｜⚠ STALE 语料比库新（语料 {fp.get('corpus_mtime')} > "
                 f"入库 {fp.get('corpus_indexed_mtime')}，请跑 update）")
    return line
