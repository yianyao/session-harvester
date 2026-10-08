# -*- coding: utf-8 -*-
"""AutoClaw 桌面版会话 Adapter。

数据源（2026-10-05 实测验证）：
<AppData>/Roaming/AutoClaw-official/accounts/<account-hash>/runtime/runtime.sqlite

核心表：
- work_sessions(session_id, created_at, updated_at, title, deleted_at, ...)
- neutral_session_entries(session_id, session_seq, entry_type, payload, committed_at)
  entry_type ∈ {request, assistant, answer, reasoning, tool_call, tool_result,
                attachment, resource, compaction}，payload 为 JSON
- neutral_session_heads：会话指针（settled_answer_entry_id 等）
- schema_migrations：迁移台账（detect 时记录 seq 供溯源）

Schema 守卫（防逆向私有 schema 漂移）：
  detect/load 前用 PRAGMA table_info 校验两张核心表的必需列；
  不匹配 → STUB + 指引，绝不带病解析。

消息映射（v2，2026-10-05 依真实库复核修正）：
  request   → user    data.text。
              注意：request 是"每次模型调用"一条，不是"每条用户消息"一条——
              同一用户轮次内工具循环的后续 run 会重复记录同一文本。
              去重规则（实测核验）：文本与上一条保留的 request 相同、
              且其间没有 answer → 判为同轮重复，丢弃；answer 介入后的
              重复文本视为用户真实重发，保留。
  assistant → assistant 正文只取可见文本：优先 data.text（源端已是纯可见
              文本），否则取 content 块中 type=='text' 的块。
              reasoning 块与独立 reasoning 条目重复，一律不进正文。
  answer    → assistant data.text（run 的最终整合稿）。
              answer 与同 run 内 finishReason=stop 的 assistant 条目文本
              精确重复（实测 925==925）→ 去重：保留 answer、丢弃重复的
              assistant 条目；中间 tool-calls 旁白不重复，保留。
  reasoning → note  "[reasoning] ..."（--with-notes 时导出）。
              同一 run 内完全重复的思考只保留一条；中英文各一份且语义
              相同时只保留中文版（CJK 主导判定）。
  tool_call → note  "[tool_call] 工具名 + 输入摘要"（--with-notes 时导出）。
  tool_result → note "[tool_result] 工具名: status [错误码/摘要]"。
              与 tool_call 经 toolCallId 配对审计的原始证据源（2026-10-05
              实测 1697 条 / 50 条 error）；report-tools 子命令据此产出
              工具调用与失败率统计。
  其余类型不计入消息流，仅统计进 extra.entry_stats。

时间戳：committed_at / created_at / updated_at 源端为 UTC ISO（带 Z），
统一换算到本地时区（models.to_local_ts），避免纲要日期差一天。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path

from ..models import Message, SessionRecord, normalize_text, to_local_ts
from .base import BaseAdapter, DetectReport, open_ro_sqlite

APPDATA = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
CANDIDATES = [APPDATA / "AutoClaw-official", APPDATA / "AutoClaw"]

#: schema 守卫：两张核心表的必需列（实测于迁移 seq 72，2026-10-05）
_REQUIRED_COLUMNS = {
    "work_sessions": {"session_id", "created_at", "updated_at", "title",
                      "deleted_at"},
    "neutral_session_entries": {"session_id", "entry_id", "session_seq",
                                "run_id", "entry_type", "payload",
                                "committed_at"},
}

_CJK_RATIO_RE = re.compile(r"[\u4e00-\u9fff]")


def _cjk_ratio(s: str) -> float:
    """中文（CJK）字符占比，用于思考过程的中英文去重。"""
    s = s or ""
    return len(_CJK_RATIO_RE.findall(s)) / max(len(s), 1)


def _norm_key(s: str) -> str:
    """文本比较键：压缩空白差异，防同文异空白漏判。"""
    return re.sub(r"\s+", " ", normalize_text(s or "")).strip()


class AutoClawAdapter(BaseAdapter):
    id = "autoclaw"
    name = "AutoClaw 桌面版"
    category = "Agent"

    def __init__(self, include_deleted: bool = False,
                 search_bases: list[str] | None = None):
        self.include_deleted = include_deleted
        #: 搜索根目录：在这些目录下找 accounts/*/runtime/runtime.sqlite
        self.search_bases = [Path(b) for b in search_bases] if search_bases else list(CANDIDATES)
        self._dbs: list[Path] = []
        self._overview: list[dict] | None = None

    def _find_dbs(self) -> list[Path]:
        if self._dbs:
            return self._dbs
        out = []
        for base in self.search_bases:
            accounts = base / "accounts"
            if not accounts.is_dir():
                continue
            for acc in sorted(accounts.iterdir()):
                db = acc / "runtime" / "runtime.sqlite"
                if db.is_file():
                    out.append(db)
        self._dbs = out
        return out

    @staticmethod
    def _open_ro(db: Path) -> sqlite3.Connection:
        # WAL 锁冲突时 open_ro_sqlite 自动退化为临时副本（见 base）
        con = open_ro_sqlite(db)
        con.row_factory = sqlite3.Row
        return con

    # ---- schema 守卫 ----
    @staticmethod
    def _schema_check(con: sqlite3.Connection) -> tuple[bool, str]:
        """校验核心表列结构。返回 (ok, 说明)。

        私有 schema 改版（加列可容忍，改名/删列不可）时在此降级，
        而不是等解析报错才暴露。
        """
        for table, required in _REQUIRED_COLUMNS.items():
            try:
                cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
            except sqlite3.Error as e:  # noqa: BLE001
                return False, f"表 {table} 无法读取列信息: {e}"
            if not cols:
                return False, f"表 {table} 不存在（schema 已改版?）"
            missing = required - cols
            if missing:
                return False, f"表 {table} 缺少必需列: {sorted(missing)}"
        return True, "schema 匹配"

    @staticmethod
    def _migration_seq(con: sqlite3.Connection) -> int | None:
        """读 schema_migrations 的最大 sequence（仅溯源用，缺失不判失败）。"""
        try:
            row = con.execute("SELECT MAX(sequence) FROM schema_migrations").fetchone()
            return int(row[0]) if row and row[0] is not None else None
        except sqlite3.Error:  # noqa: BLE001
            return None

    def detect(self) -> DetectReport:
        dbs = self._find_dbs()
        if not dbs:
            return DetectReport(self.id, self.name, "MISSING", "未找到 AutoClaw 账号数据目录")
        total = 0
        seqs: list[int] = []
        for db in dbs:
            try:
                con = self._open_ro(db)
            except sqlite3.Error as e:  # noqa: BLE001
                return DetectReport(self.id, self.name, "STUB",
                                    f"runtime.sqlite 无法打开: {e}")
            try:
                ok, why = self._schema_check(con)
                if not ok:
                    return DetectReport(
                        self.id, self.name, "STUB",
                        f"schema 不匹配，已降级（{why}）",
                        hints=["私有 schema 可能已改版：请提供该库的 "
                               "sqlite_master 输出以更新 _REQUIRED_COLUMNS"])
                seq = self._migration_seq(con)
                total += con.execute(
                    "SELECT COUNT(*) FROM work_sessions").fetchone()[0]
            except sqlite3.Error as e:  # noqa: BLE001
                return DetectReport(self.id, self.name, "STUB",
                                    f"runtime.sqlite 无法读取: {e}")
            finally:
                con.close()
            if seq is not None:
                seqs.append(seq)
        detail = f"{len(dbs)} 个账号库，共 {total} 个会话"
        if seqs:
            detail += f"（schema 迁移 seq {max(seqs)}）"
        return DetectReport(self.id, self.name, "OK", detail, session_count=total)

    def _conns(self):
        for db in self._find_dbs():
            try:
                con = self._open_ro(db)
            except sqlite3.Error:
                continue  # 单个账号库打不开不拖垮其余库
            yield db, con

    def list_sessions(self) -> list[dict]:
        if self._overview is not None:
            return self._overview
        items = []
        for db, con in self._conns():
            try:
                # 单条聚合查询取全部会话的消息计数（避免逐会话 COUNT 的 N+1）
                rows = con.execute(
                    "SELECT w.session_id, w.title, w.created_at, w.updated_at, "
                    "       w.deleted_at, "
                    "       COUNT(e.entry_id) AS n_entry "
                    "FROM work_sessions w "
                    "LEFT JOIN neutral_session_entries e "
                    "       ON e.session_id = w.session_id "
                    "      AND e.entry_type IN ('request','assistant','answer') "
                    "GROUP BY w.session_id ORDER BY w.created_at"
                ).fetchall()
            finally:
                con.close()
            for row in rows:
                if row["deleted_at"] and not self.include_deleted:
                    continue
                items.append({
                    "session_id": row["session_id"],
                    "title": row["title"] or f"会话 {row['session_id'][:8]}",
                    "created_at": to_local_ts(row["created_at"]),
                    "updated_at": to_local_ts(row["updated_at"]),
                    # 注意：此计数为原始条目数（含同轮重复 request 与
                    # answer 重复），加载后会经去重变小
                    "message_count": row["n_entry"], "preview": "",
                    "extra_hint": {"db": str(db)},
                })
        self._overview = items
        return items

    def load_session(self, session_id: str) -> SessionRecord:
        for db, con in self._conns():
            try:
                # schema 守卫先行：列结构不匹配时连列名查询都可能失败，
                # 必须在任何 SELECT 之前降级，而不是等 OperationalError。
                ok, why = self._schema_check(con)
                if not ok:
                    try:
                        row = con.execute(
                            "SELECT title, deleted_at FROM work_sessions "
                            "WHERE session_id=?", (session_id,)).fetchone()
                    except sqlite3.Error:  # noqa: BLE001
                        row = None
                    return SessionRecord(
                        source=self.id, session_id=session_id,
                        title=(row["title"] if row and row["title"] else None)
                        or f"会话 {session_id[:8]}",
                        messages=[],
                        extra={"db": str(db), "lossy": True,
                               "warnings": [f"schema 不匹配，未解析消息（{why}）"],
                               "deleted_at": row["deleted_at"] if row else None})
                row = con.execute(
                    "SELECT session_id, title, created_at, updated_at, deleted_at "
                    "FROM work_sessions WHERE session_id=?", (session_id,)
                ).fetchone()
                if row is None:
                    continue
                entries = [
                    (r["session_seq"], r["run_id"], r["entry_type"],
                     r["payload"], r["committed_at"])
                    for r in con.execute(
                        "SELECT session_seq, run_id, entry_type, payload, "
                        "       committed_at "
                        "FROM neutral_session_entries WHERE session_id=? "
                        "ORDER BY session_seq", (session_id,))
                ]
            finally:
                con.close()  # continue/return/异常三条路径都确保关闭
            msgs, entry_stats, dedup = _build_messages(entries)
            return SessionRecord(
                source=self.id, session_id=session_id,
                title=row["title"] or f"会话 {session_id[:8]}",
                created_at=to_local_ts(row["created_at"]),
                updated_at=to_local_ts(row["updated_at"]),
                messages=msgs,
                extra={"db": str(db), "deleted_at": row["deleted_at"],
                       "entry_stats": entry_stats, "dedup": dedup,
                       # v0.22 P0-5（H13）：主模型（entry payload 众数）
                       "model": _model_from_entries(entries)},
            )
        raise KeyError(f"AutoClaw 未找到会话: {session_id}")


# ---- 条目 → 消息映射 ----

def _model_from_entries(entries: list[tuple]) -> str | None:
    """会话主模型抽取（v0.22 P0-5，H13）：entry payload 的
    data.model.model（真实库实测：request/assistant/reasoning/answer
    四类 entry 统一路径），取众数。纯函数，坏 payload 忽略。"""
    from collections import Counter
    seen: Counter[str] = Counter()
    for row in entries:
        payload_raw = row[3]
        if not isinstance(payload_raw, str):
            continue
        try:
            payload = json.loads(payload_raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        m = ((payload.get("data") or {}).get("model") or {}).get("model")
        if isinstance(m, str) and m:
            seen[m] += 1
    return seen.most_common(1)[0][0] if seen else None


def _entry_text(entry_type: str, payload: dict) -> str:
    """对话条目的可见正文（不含 reasoning；assistant 优先 data.text）。"""
    data = payload.get("data") or {}
    if entry_type in ("request", "answer"):
        return (data.get("text") or "").strip()
    if entry_type == "assistant":
        text = (data.get("text") or "").strip()
        if text:
            return text
        # data.text 缺失时退化：只拼 content 中 type=='text' 的块
        content = data.get("content")
        if isinstance(content, list):
            parts = [b.get("text") for b in content
                     if isinstance(b, dict) and b.get("type") in (None, "text")
                     and b.get("text")]
            return normalize_text("\n".join(parts)).strip()
        if isinstance(content, str):
            return content.strip()
    return ""


def _dedup_reasoning(texts: list[str]) -> list[str]:
    """run 内思考过程去重：完全重复只留一条；中英并存时只留中文版。"""
    uniq: list[str] = []
    seen: set[str] = set()
    for t in texts:
        key = _norm_key(t)
        if key and key not in seen:
            seen.add(key)
            uniq.append(t)
    has_zh = [t for t in uniq if _cjk_ratio(t) >= 0.05]
    # 存在中文版且也存在纯英文版 → 只保留中文版（同义双语场景）
    if has_zh and len(has_zh) < len(uniq):
        return has_zh
    return uniq


def _build_messages(entries: list[tuple]) -> tuple[list[Message], dict, dict]:
    """把 neutral_session_entries 流映射为统一消息流。

    entries: [(session_seq, run_id, entry_type, payload_json_str, committed_at)]
    返回 (messages, entry_stats, dedup 计数)。

    run（一次模型调用）内条目连续；reasoning 先按 run 缓存做重复/
    双语去重，再在原位置插入 note，保持时间顺序。
    """
    msgs: list[Message] = []
    stats: dict[str, int] = {}
    for row in entries:  # 各 entry_type 原始条数统计（写入 extra.entry_stats）
        stats[row[2]] = stats.get(row[2], 0) + 1
    dedup = {"request_dup": 0, "answer_dup_assistant": 0, "reasoning_dup": 0}

    # 同一用户轮次内多次模型调用会把 request 重复入库。
    # 判据（实测核验）：与上一条保留的 request 文本相同且其间无 answer
    # → 同轮重复，丢弃；answer 介入后的重复文本视为用户真实重发，保留。
    last_kept_user_key: str | None = None
    answered_since = True

    def parse(payload_raw):
        try:
            payload = (json.loads(payload_raw)
                       if isinstance(payload_raw, str) else payload_raw)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    # 先按 run 分块（保持 session_seq 顺序；run 块实测连续）
    runs: list[tuple[str, list[tuple]]] = []
    for row in entries:
        seq, run_id, et, payload_raw, committed_at = row
        if not runs or runs[-1][0] != run_id:
            runs.append((run_id, []))
        runs[-1][1].append(row)

    for run_id, rows in runs:
        # --- pass 1：收集本 run 的 reasoning 文本，确定保留集合 ---
        raw_reasoning: list[str] = []
        parsed_rows: list[tuple] = []
        for seq, run_id2, et, payload_raw, committed_at in rows:
            payload = parse(payload_raw)
            parsed_rows.append((seq, et, payload, committed_at))
            if et == "reasoning" and payload:
                text = ((payload.get("data") or {}).get("text") or "").strip()
                if text:
                    raw_reasoning.append(text)
        kept_reasoning = _dedup_reasoning(raw_reasoning)
        kept_keys = {_norm_key(t) for t in kept_reasoning}
        dedup["reasoning_dup"] += len(raw_reasoning) - len(kept_reasoning)

        # --- pass 2：按 seq 顺序映射 ---
        for seq, et, payload, committed_at in parsed_rows:
            ts = to_local_ts(committed_at)
            if payload is None:
                continue
            data = payload.get("data") or {}

            if et == "request":
                text = (data.get("text") or "").strip()
                if not text:
                    continue
                key = _norm_key(text)
                if key == last_kept_user_key and not answered_since:
                    dedup["request_dup"] += 1
                    continue  # 同一用户轮次的重复模型调用，丢弃
                msgs.append(Message(role="user", text=text, timestamp=ts,
                                    raw=payload))
                last_kept_user_key = key
                answered_since = False

            elif et == "assistant":
                text = _entry_text(et, payload)
                if text:
                    msgs.append(Message(role="assistant", text=text,
                                        timestamp=ts, raw=payload))

            elif et == "answer":
                text = (data.get("text") or "").strip()
                if not text:
                    continue
                akey = _norm_key(text)
                # answer 与本 run 的流式 assistant 条目（实测为最后一条
                # finishReason=stop）文本精确重复 → 只留 answer。
                i = len(msgs) - 1
                while i >= 0 and msgs[i].role in ("assistant", "note"):
                    if (msgs[i].role == "assistant"
                            and _norm_key(msgs[i].text) == akey):
                        del msgs[i]
                        dedup["answer_dup_assistant"] += 1
                    i -= 1
                msgs.append(Message(role="assistant", text=text, timestamp=ts,
                                    raw=payload))
                answered_since = True

            elif et == "reasoning":
                text = (data.get("text") or "").strip()
                if text and _norm_key(text) in kept_keys:
                    msgs.append(Message(
                        role="note",
                        text=f"[reasoning]\n{normalize_text(text).rstrip()}",
                        timestamp=ts, raw=payload))

            elif et == "tool_call":
                tool = data.get("toolName") or data.get("tool") or "unknown"
                try:
                    inp = json.dumps(data.get("input"), ensure_ascii=False)
                except (TypeError, ValueError):
                    inp = str(data.get("input"))
                if len(inp) > 300:
                    inp = inp[:300] + "…"
                msgs.append(Message(role="note",
                                    text=f"[tool_call] {tool}: {inp}",
                                    timestamp=ts,
                                    raw={**payload, "kind": "tool_call"}))

            elif et == "tool_result":
                tool = data.get("toolName") or data.get("tool") or "unknown"
                status = data.get("status") or "unknown"
                line = f"[tool_result] {tool}: {status}"
                if status != "success":
                    err = data.get("error")
                    detail = ""
                    if isinstance(err, dict):
                        detail = str(err.get("code") or err.get("message") or "")
                    elif err:
                        detail = str(err)
                    if not detail:
                        c = data.get("content")
                        if isinstance(c, str) and c:
                            detail = c[:120]
                    if detail:
                        line += f" {detail}"
                msgs.append(Message(role="note", text=line,
                                    timestamp=ts,
                                    raw={**payload, "kind": "tool_result"}))

    return msgs, stats, dedup


