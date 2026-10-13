# -*- coding: utf-8 -*-
"""会话抑制（v0.49）：删除没营养的会话——删的是**仓内副本**，不碰采集源。

需求（用户 2026-10-12 第 1 条）：有些会话实在没营养，问「能不能删掉，
并且以后不会再被增量入库」。裁决方向 = 抑制表 + 下次 sync 真删。

三条**已查证**的事实（决定了本模块的形状；都不是推测）：

1. `sync` / `update` 每次都会**重导出 + 重建/增量索引**，库内容由 exports
   目录决定（`sync.run_sync` 调 `exporter.export` → `indexing.index_exports`）。
   所以**只从库里 DELETE 行 = 下次 sync 就回来**。要真删必须四处齐做：
   导出步拦（`export(skip_sids=...)`）+ 清旧 exports 副本
   （`exporter.prune_suppressed`）+ 索引侧兜底
   （`index_exports(skip_sids=...)`）+ `sync`/`update` 接线。
2. 既有 `sessions_noise`（`consolidate.py`）语义**不同**：那是「零散登记，
   不删、只排除分析」，而且**一次都没参与导出/索引**。故本模块**单独建表**，
   绝不复用（提案 §5 ②）。
3. FTS 形态（2026-10-13 探针实测，`docs/reports/probe-suppress-schema.py`）：
   `messages` 是 **self-contained FTS5 虚表**——`indexing.SCHEMA` 里
   `content=` **未设置**，影子表 `messages_content/_data/_docsize/_idx` 齐全；
   **不是 external-content，全库也没有任何 trigger**。所以直接
   `DELETE FROM messages WHERE sid=?` 时，FTS 索引由 FTS5 自己维护，
   无需任何额外同步语句。（提案 §2.3 要求实施前先确认这点，这是确认结果。）

**写入边界**（本项目铁律「`harvester.db` 只读」的唯一例外）：
`purge_session_rows()` 是本功能**唯一**会写采集库的函数，且只由用户显式
`suppress add --apply` 触发（提案 §5 ③⑦）。其余读路径一律 `mode=ro`。

口径诚实（提案 §5 ⑧ 用户裁决：**不设时限**，不写任何「N 天内可恢复」
之类的承诺）：`unsuppress()` 只删抑制表行，**不恢复任何数据**；能不能恢复
**只取决于语料是否还在**（DSH/WorkBuddy 自己的会话文件）。语料还在 →
下次 sync/update 会重新导出并入库；语料没了 → 就真没了。
CLI 与 README 的措辞一律以此为准。
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

#: 抑制表 DDL。与 `sessions_noise` **分开**（提案 §5 ②）：语义不同，
#: 混用会让「零散」从此等于「已删除」，而库内现有零散登记还有数据。
SUPPRESS_SCHEMA = """
CREATE TABLE IF NOT EXISTS suppressed_sessions (
    sid        TEXT PRIMARY KEY,
    reason     TEXT NOT NULL DEFAULT '',
    created    TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT ''
);
"""

TABLE = "suppressed_sessions"


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def ensure_suppress_table(meta_path: Path) -> Path:
    """建抑制表（幂等）。写入方调用；**读路径不调用**（读一下就顺手建表
    会让只读消费方产生副作用，`consolidate.list_noise` 的既有教训）。"""
    meta_path = Path(meta_path)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(meta_path))
    try:
        con.executescript(SUPPRESS_SCHEMA)
        con.commit()
    finally:
        con.close()
    return meta_path


def _read_con(meta_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{Path(meta_path).as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def list_suppressed(meta_path: Path, create: bool = False) -> list[dict]:
    """列出已抑制会话（谁 / 何时 / 为什么）。缺库或缺表 → 空列表。

    默认**只读**：`create=True` 才建表（与 `consolidate.list_noise` 同款）。
    """
    meta_path = Path(meta_path)
    if not meta_path.is_file():
        return []
    if create:
        ensure_suppress_table(meta_path)
    con = _read_con(meta_path)
    try:
        has = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (TABLE,)).fetchone()
        if not has:
            return []
        return [dict(r) for r in con.execute(
            f"SELECT sid, reason, created, created_by FROM {TABLE} "
            "ORDER BY created, sid")]
    finally:
        con.close()


def suppressed_sids(meta_path: Path | None) -> set[str]:
    """抑制 sid 集合（消费方过滤用；未配置 / 缺库 / 缺表 → 空集，行为不变）。"""
    if not meta_path:
        return set()
    return {r["sid"] for r in list_suppressed(Path(meta_path))}


# ── 采集库侧：预演（只读）与执行（本功能唯一的写） ──────────────────────
def db_row_counts(db_path: Path, sids) -> dict[str, dict]:
    """预演用：**只读**统计每个 sid 在 sessions/messages/steps 的行数 + 标题。

    CLI 的 dry-run 走这里，所以走的是 `mode=ro` 连接——预演**一字节不写**。
    """
    out = {s: {"sessions": 0, "messages": 0, "steps": 0,
               "title": "", "found": False} for s in sids}
    db_path = Path(db_path)
    if not db_path.is_file():
        return out
    con = _read_con(db_path)
    try:
        for sid in out:
            for tbl in ("sessions", "messages", "steps"):
                out[sid][tbl] = con.execute(
                    f"SELECT COUNT(*) FROM {tbl} WHERE sid=?", (sid,)).fetchone()[0]
            row = con.execute("SELECT title FROM sessions WHERE sid=?",
                              (sid,)).fetchone()
            if row:
                out[sid]["title"] = row["title"] or ""
                out[sid]["found"] = True
        return out
    finally:
        con.close()


def purge_session_rows(db_path: Path, sids) -> dict[str, dict]:
    """**本功能唯一会写采集库的函数**：删掉这些 sid 的三表行。

    `sessions` / `messages` / `steps` 三表各删一次；`messages` 是
    self-contained FTS5 虚表（见模块 docstring 第 3 条），DELETE 由 FTS5
    自己维护索引，无 trigger 需要照顾。过程中异常 → 不 commit（整批回滚）。

    调用者：`suppress()`（用户显式 `suppress add --apply`）。
    """
    counts = {s: {"sessions": 0, "messages": 0, "steps": 0} for s in sids}
    db_path = Path(db_path)
    if not db_path.is_file():
        return counts
    con = sqlite3.connect(str(db_path))
    try:
        con.execute("BEGIN")
        for sid in counts:
            for tbl in ("sessions", "messages", "steps"):
                cur = con.execute(f"DELETE FROM {tbl} WHERE sid=?", (sid,))
                counts[sid][tbl] = max(cur.rowcount, 0)
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    return counts


def suppress(meta_path: Path, db_path: Path, sids, reason: str = "",
             created_by: str = "", exports_dir: Path | None = None) -> dict:
    """抑制会话（用户已 `--apply`）：写抑制表 + 立刻删库内三表行 + 清旧副本。

    幂等：同一 sid 重复 `suppress` 只更新理由、不再重复计数（`INSERT OR
    REPLACE` 保留首次 `created`——「何时第一次被抑制」比「最后一次重写」
    更有信息量，且 `list` 的输出才稳定）。

    `exports_dir` 给定则顺带调 `exporter.prune_suppressed` 清掉该 sid 的
    旧 `.json`/`.md` 与 manifest 条目（旧文件还在，下次索引就会回来）。
    """
    from .exporter import prune_suppressed

    sids = sorted({s for s in sids if s})
    ensure_suppress_table(meta_path)
    con = sqlite3.connect(str(meta_path))
    added = 0
    try:
        for sid in sids:
            old = con.execute(f"SELECT created FROM {TABLE} WHERE sid=?",
                              (sid,)).fetchone()
            created = old[0] if old else _now()
            cur = con.execute(
                f"INSERT OR REPLACE INTO {TABLE} (sid, reason, created, "
                "created_by) VALUES (?,?,?,?)",
                (sid, reason, created, created_by))
            added += 1 if cur.rowcount and not old else 0
        con.commit()
    finally:
        con.close()
    purged = purge_session_rows(db_path, sids)
    pruned = (prune_suppressed(exports_dir, set(sids))
              if exports_dir else {"scanned": 0, "deleted": [], "unreadable": [],
                                   "manifest_removed": 0, "dry_run": False})
    return {"added": added, "sids": sids, "reason": reason,
            "created_by": created_by, "purged": purged, "pruned": pruned}


def find_candidates(db_path: Path,
                    known_sids=frozenset()) -> dict:
    """抑制候选清单（**只读**，v0.52）：工具先出机械候选，人按标题裁决。

    用户 2026-10-13 原话："我也不可能知道 sid 是哪些啊（这其实也是问题
    所在，一般人谁记这个）"——原工作流要人点名 sid，反人类。本函数给
    两类机械候选：

      ① 重复会话：会话级 user-raw bigram Jaccard ≥DUP_THRESHOLD（H40
         冻结口径，最终判定**逐对全量复核**）；每簇 **created_at 最早**
         的一条为代表，其余为候选；
      ② 零 user 轮次：没有任何 role='user' 消息的会话
         （常见 = 纯背景噪声 / 只有系统与工具消息）。

    规模安全（真库 1983 会话实测教训：全量 grams 常驻内存会被杀）：
    两阶段——先对每会话**前 `DISCOVER_CHARS` 字符**的 bigram 粗筛出候选对
    （配长度比窗口剪枝），再对候选对**逐对用全量 user raw** 重算 Jaccard
    按 H40 阈值定案。粗筛阈值放宽到 `DISCOVER_J`（宁可多复核），
    定案口径不打折。

    `known_sids`（已在抑制表里的）直接剔除，不重复提名。
    返回 `{"dup_clusters": [...], "zero_user": [...], "scanned": n}`；
    库缺失/为空时各项为空、scanned=0。全程 mode=ro，一字节不写。
    """
    from .topicprep import DUP_THRESHOLD, _grams, _jaccard

    DISCOVER_CHARS = 2000   # 粗筛只用每会话 user raw 拼接的前 N 字符
    DISCOVER_J = 0.75       # 粗筛阈值（放宽，宁可多复核）；定案仍 ≥0.90
    SIZE_RATIO = 0.70       # 长度比窗口（J≥0.90 必要条件的宽松版）

    db_path = Path(db_path)
    if not db_path.is_file():
        return {"dup_clusters": [], "zero_user": [], "scanned": 0}
    con = _read_con(db_path)
    try:
        sess = list(con.execute(
            "SELECT sid, title, created_at FROM sessions "
            "ORDER BY created_at, sid"))
        # 单趟全表扫（messages 是 FTS5 虚表且 sid UNINDEXED——逐会话查询
        # 等于 N 次全表扫，真库实测 463s；一趟拉回按 sid 分组只要几秒）。
        joined_of: dict[str, str] = {}
        user_seen: set[str] = set()
        for r in con.execute("SELECT sid, role, raw FROM messages"):
            if r["role"] != "user":
                continue
            user_seen.add(r["sid"])
            joined_of[r["sid"]] = joined_of.get(r["sid"], "") + (r["raw"] or "")

        items, zero_user = [], []
        for r in sess:
            if r["sid"] in known_sids:
                continue
            if r["sid"] not in user_seen:
                zero_user.append({"sid": r["sid"], "title": r["title"] or ""})
                continue
            joined = joined_of.get(r["sid"], "")
            items.append({"sid": r["sid"], "title": r["title"] or "",
                          "created_at": r["created_at"] or "",
                          "grams_disc": _grams(joined[:DISCOVER_CHARS]),
                          "joined": joined})
        del joined_of, user_seen

        # ---- 粗筛：按 bigram 数降序 + 长度比窗口，找候选对 ----
        by_size = sorted(items, key=lambda it: -len(it["grams_disc"]))
        cand_pairs: set[tuple[str, str]] = set()
        for i, a in enumerate(by_size):
            na = len(a["grams_disc"])
            if na == 0:
                continue
            for b in by_size[i + 1:]:
                nb = len(b["grams_disc"])
                if nb == 0 or nb < na * SIZE_RATIO:
                    break                     # 后面只会更小
                if _jaccard(a["grams_disc"], b["grams_disc"]) >= DISCOVER_J:
                    cand_pairs.add((min(a["sid"], b["sid"]),
                                    max(a["sid"], b["sid"])))
        del by_size

        # ---- 定案：候选对逐对用**全量** user raw 复核（H40 口径） ----
        joined_of2 = {it["sid"]: it["joined"] for it in items}
        verified: list[tuple[str, str, float]] = []
        for sa, sb in sorted(cand_pairs):
            ja = _jaccard(_grams(joined_of2.get(sa, "")),
                          _grams(joined_of2.get(sb, "")))
            if ja >= DUP_THRESHOLD:
                verified.append((sa, sb, round(ja, 3)))
        del joined_of2

        # ---- 聚簇（沿用 dedupe_items 同款并查集语义，仅对涉事会话） ----
        parent = {it["sid"]: it["sid"] for it in items}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        title_of = {it["sid"]: it["title"] for it in items}
        created_of = {it["sid"]: it["created_at"] for it in items}
        pair_j: dict[tuple[str, str], float] = {}
        for sa, sb, j in verified:
            pair_j[(sa, sb)] = j
            pair_j[(sb, sa)] = j
            ra, rb = find(sa), find(sb)
            if ra != rb:
                parent[ra] = rb
        clusters: dict[str, list] = {}
        for it in items:
            clusters.setdefault(find(it["sid"]), []).append(it["sid"])
        dup_clusters = []
        for members in clusters.values():
            if len(members) < 2:
                continue
            members.sort(key=lambda s: (created_of.get(s, ""), s))
            rep = members[0]
            cands = [{"sid": s, "title": title_of.get(s, ""),
                      "jaccard": pair_j.get((rep, s))}
                     for s in members[1:] if s not in known_sids]
            if cands:
                dup_clusters.append({"rep": rep,
                                     "rep_title": title_of.get(rep, ""),
                                     "candidates": cands})
        dup_clusters.sort(key=lambda c: c["rep"])
        return {"dup_clusters": dup_clusters, "zero_user": zero_user,
                "scanned": len(sess)}
    finally:
        con.close()


def unsuppress(meta_path: Path, sids) -> int:
    """取消抑制：只删抑制表行，**不恢复任何数据**（口径见模块 docstring）。

    返回真正被删掉的抑制行数（原本就没有的 sid 不计）。
    库内行/ exports 副本要靠下次 `sync`（或 `update`）重导出才会回来，
    前提是**语料还在**。
    """
    sids = sorted({s for s in sids if s})
    meta_path = Path(meta_path)
    if not meta_path.is_file() or not sids:
        return 0
    con = sqlite3.connect(str(meta_path))
    try:
        has = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                          "AND name=?", (TABLE,)).fetchone()
        if not has:
            return 0
        n = 0
        for sid in sids:
            cur = con.execute(f"DELETE FROM {TABLE} WHERE sid=?", (sid,))
            n += max(cur.rowcount, 0)
        con.commit()
        return n
    finally:
        con.close()
