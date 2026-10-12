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
      adopted_at TEXT,               -- v0.46：采纳时刻（A/B 评估的分界 T）；
                                     -- 'YYYY-MM-DD' 或 'YYYY-MM-DD HH:MM:SS'，
                                     -- 非 adopted 态一律清空（NULL）
      note       TEXT,               -- v0.46：人工说明（如"建议句已退休"）；
                                     -- NULL=从未写/未覆盖，''=显式清空
      updated_at TEXT NOT NULL)      -- 'YYYY-MM-DD HH:MM:SS' 本地时间

adopted_at 的语义是**只增不减**（v0.46，P1-4）：
- `status="adopted"` 且调用方没给 adopted_at → 库里已有值就保留，没有才写当前时间；
  反复采纳**不会**把历史分界日期刷成"今天"（否则每次重跑评估都换分界，A/B 无法复现）；
- 调用方显式给了 → 用给的（`suggest-status --adopted-at` 回填历史采纳日期）；
- `status != "adopted"` → 清空（不再是采纳态，就不该再拿它当分界）；
- `note=None` → 保留库里已有说明；`note=""` → 显式清空。
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime
from pathlib import Path

STATUSES = ("pending", "adopted", "rejected")

#: 时间戳格式（零填充、可字典序比较——与 steps.ts 同口径）
_STAMP_FMT = "%Y-%m-%d %H:%M:%S"
_DATE_FMT = "%Y-%m-%d"

#: additive 迁移的列：(列名, 类型)。旧库缺列时 ALTER TABLE 补，已存在即忽略。
_ADDED_COLUMNS = (("adopted_at", "TEXT"), ("note", "TEXT"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS suggestion_status (
    key        TEXT PRIMARY KEY,
    status     TEXT NOT NULL,
    adopted_at TEXT,
    note       TEXT,
    updated_at TEXT NOT NULL
)
"""


def validate_stamp(value: str, field: str = "adopted_at") -> str:
    """校验时间戳：只接受 `YYYY-MM-DD` 或 `YYYY-MM-DD HH:MM:SS`，返回原值。

    为什么严格：分界日期错了，A/B 评估的结论会跟着错，而且是**静默**错的；
    宁可当场 ValueError，也不要把手滑的写法（`2026/10/05`、`2026-1-2`
    这类非零填充形式）写进库。零填充才保证 ts 字符串字典序 == 时间序。
    """
    for fmt in (_DATE_FMT, _STAMP_FMT):
        try:
            dt = datetime.strptime(value, fmt)
        except (ValueError, TypeError):
            continue
        if dt.strftime(fmt) == value:      # 拒掉 '2026-1-2' 这种非零填充
            return value
    raise ValueError(
        f"{field}={value!r} 不是 'YYYY-MM-DD' 或 'YYYY-MM-DD HH:MM:SS' 格式")


def _columns(con: sqlite3.Connection) -> set[str]:
    """表已有列名；表不存在 → 空集（PRAGMA 对不存在的表返回空）。"""
    return {r[1] for r in con.execute("PRAGMA table_info(suggestion_status)")}


def _ensure_schema(con: sqlite3.Connection) -> None:
    """建表 + 旧库补列（additive 迁移）。

    SQLite 的 `ADD COLUMN` 没有 `IF NOT EXISTS`，列已存在时抛
    `sqlite3.OperationalError: duplicate column name` —— 这正是"已迁移过"
    的正常信号，吞掉即可（迁移必须是幂等的：每次写都跑一遍）。
    """
    con.execute(_SCHEMA)
    for name, typ in _ADDED_COLUMNS:
        try:
            con.execute(f"ALTER TABLE suggestion_status ADD COLUMN {name} {typ}")
        except sqlite3.OperationalError:
            pass


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


def load_records(meta_db: Path | None) -> dict[str, dict]:
    """读取全部状态明细 {key: {"status","adopted_at","note","updated_at"}}。

    与 load_statuses 的约定一致（meta_db 不存在/表未建/status 非法 → 空/丢弃），
    但额外带上 v0.46 的两列。**旧库（未迁移）也能读**：缺列按 NULL 处理，
    读路径绝不改库（迁移只发生在写路径 set_status 里）——只读铁律的延伸。
    """
    if meta_db is None or not Path(meta_db).is_file():
        return {}
    con = sqlite3.connect(str(meta_db))
    try:
        cols = _columns(con)
        if not cols:
            return {}
        # 列名来自上面的白名单，不是用户输入（无注入面）
        adopted = "adopted_at" if "adopted_at" in cols else "NULL"
        note = "note" if "note" in cols else "NULL"
        rows = con.execute(
            f"SELECT key, status, {adopted}, {note}, updated_at "
            "FROM suggestion_status").fetchall()
    finally:
        con.close()
    return {k: {"status": s, "adopted_at": a, "note": n, "updated_at": u}
            for k, s, a, n, u in rows if s in STATUSES}


def set_status(meta_db: Path, key: str, status: str,
               adopted_at: str | None = None,
               note: str | None = None) -> None:
    """写入/更新一条状态（additive：v0.46 新增两个可选参数，旧调用不受影响）。

    - status 必须在 STATUSES 内，否则 ValueError；
    - adopted_at：给定时必须是合法时间戳（否则 ValueError）；`status="adopted"`
      且未给定时**只增不减**（库里已有值就保留，没有才写当前时间）；非 adopted
      态一律清空（NULL）；
    - note：None = 保留库里已有说明；'' = 显式清空；其余原样写入。
    """
    if status not in STATUSES:
        raise ValueError(f"status={status!r} 不在 {list(STATUSES)}")
    if not key:
        raise ValueError("key 不能为空")
    if adopted_at is not None:
        validate_stamp(adopted_at, "adopted_at")
    meta_db = Path(meta_db)
    meta_db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(meta_db))
    try:
        _ensure_schema(con)
        prev = con.execute("SELECT adopted_at, note FROM suggestion_status "
                           "WHERE key=?", (key,)).fetchone()
        prev_adopted, prev_note = (prev[0], prev[1]) if prev else (None, None)
        if status == "adopted":
            # 只增不减：显式给的优先（回填），否则保留旧值，都没有才落当前时间
            new_adopted = (adopted_at if adopted_at is not None
                           else prev_adopted or time.strftime(_STAMP_FMT))
        else:
            new_adopted = None             # 不再是采纳态 → 分界清空
        new_note = prev_note if note is None else note
        con.execute(
            "INSERT INTO suggestion_status"
            "(key, status, adopted_at, note, updated_at) "
            "VALUES(?, ?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
            "status=excluded.status, adopted_at=excluded.adopted_at, "
            "note=excluded.note, updated_at=excluded.updated_at",
            (key, status, new_adopted, new_note,
             time.strftime(_STAMP_FMT)))
        con.commit()
    finally:
        con.close()


# ---- 人账同步（v0.46 P2-5）------------------------------------------------

#: 主题级裁决的 key 命名空间：`topic:<注册表 id>`。**与建议句原文（H31 口径）
#: 分开命名空间**，否则两种裁决在同一张表里无法区分、coverage 会把主题全算成
#: "台账陈旧"。
TOPIC_PREFIX = "topic:"


def _norm_registry_stamp(value: str | None) -> str | None:
    """注册表的时间格式 → 台账口径。

    实测 `topics.created` 是 `'YYYY-MM-DD HH:MM'`（**无秒**），而台账
    `adopted_at` 只接受 `YYYY-MM-DD` 或 `YYYY-MM-DD HH:MM:SS`
    （严格是为了 ts 字符串字典序 == 时间序，A/B 切窗才可靠）——这里补 `:00`。
    """
    if not value:
        return None
    v = str(value).strip()
    if len(v) == 16 and v[10] == " ":      # 'YYYY-MM-DD HH:MM'
        return v + ":00"
    return v


def topic_rows(topics_meta_db: Path | None) -> list[dict]:
    """从主题注册表派生台账行：每个已登记主题 → `topic:<id>`（adopted）。

    **这不是替人下判断**：注册表本身就是"人已批准登记"的权威记录
    （`topic register --apply`），这里是把它**照抄**进台账，让"人账"完整。
    `adopted_at` 用注册表的 `created`（登记时刻 = 决定时刻）。
    """
    p = Path(topics_meta_db) if topics_meta_db else None
    if p is None or not p.is_file():
        return []
    con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    try:
        try:
            rows = con.execute("SELECT id, name, created FROM topics "
                               "ORDER BY id").fetchall()
        except sqlite3.OperationalError:
            return []
    finally:
        con.close()
    return [{"key": f"{TOPIC_PREFIX}{tid}", "status": "adopted",
             "adopted_at": _norm_registry_stamp(created),
             "note": f"主题注册表：{name}"}
            for tid, name, created in rows]


def registry_counts(topics_meta_db: Path | None) -> dict:
    """主题 / 零散两本账的行数（人账总览用；缺库/缺表 → 0）。"""
    out = {"topics": 0, "noise": 0}
    p = Path(topics_meta_db) if topics_meta_db else None
    if p is None or not p.is_file():
        return out
    con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    try:
        for table, key in (("topics", "topics"), ("sessions_noise", "noise")):
            try:
                out[key] = con.execute(
                    f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.OperationalError:
                continue
    finally:
        con.close()
    return out


def sync_rows(meta_db: Path, rows: list[dict], apply: bool = False) -> dict:
    """把派生行**幂等**并入台账（默认 dry-run：只算差异，不写）。

    只做两件事，绝不覆盖人已写下的信息：
    - 缺 key → 建（status/adopted_at/note 用派生值）；
    - 已有 key 且 `adopted_at IS NULL` 且派生值不是 NULL → 补分界（回填）；
    - 其余一律不动（尤其不覆盖已有 `adopted_at` 与已有 `note`）。
    返回 {"created": [...], "filled": [...], "unchanged": [...],
          "would_write": n, "applied": bool}
    """
    meta_db = Path(meta_db)
    existing = load_records(meta_db)
    created, filled, unchanged = [], [], []
    for row in rows:
        key = row.get("key")
        if not key:
            continue
        cur = existing.get(key)
        if cur is None:
            created.append(key)
        elif not cur.get("adopted_at") and row.get("adopted_at"):
            filled.append(key)
        else:
            unchanged.append(key)
    result = {"created": created, "filled": filled, "unchanged": unchanged,
              "would_write": len(created) + len(filled), "applied": apply}
    if not apply or not result["would_write"]:
        return result
    by_key = {r["key"]: r for r in rows if r.get("key")}
    for key in created:
        r = by_key[key]
        set_status(meta_db, key, r.get("status", "adopted"),
                   adopted_at=r.get("adopted_at"), note=r.get("note"))
    for key in filled:
        r = by_key[key]
        # note 传 None = 保留原有说明（只补分界日期）
        set_status(meta_db, key, existing[key]["status"],
                   adopted_at=r.get("adopted_at"), note=None)
    return result


def backfill_adopted_at(meta_db: Path, apply: bool = False) -> dict:
    """给"已采纳但没记分界"的**存量行**补 `adopted_at`（默认 dry-run）。

    值取台账自己的 `updated_at`——那正是**人写下该裁决的时刻**，是台账既有的
    记录，不是我另找一个日期（不需要外部推断）。只补 `adopted_at IS NULL`
    的行，绝不覆盖已有值；`note` 一律保留。
    返回 {"filled": [key...], "applied": bool}。
    """
    recs = load_records(meta_db)
    todo = sorted(k for k, r in recs.items()
                  if r.get("status") == "adopted" and not r.get("adopted_at")
                  and r.get("updated_at"))
    if apply:
        for k in todo:
            set_status(meta_db, k, "adopted",
                       adopted_at=recs[k]["updated_at"], note=None)
    return {"filled": todo, "applied": apply}
