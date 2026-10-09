# -*- coding: utf-8 -*-
"""VS Code (GitHub Copilot Chat) 会话 Adapter。

数据源（2026-10-05 实测验证）：
1. <AppData>/Roaming/Code/User/globalStorage/github.copilot-chat/session-store.db
   SQLite：sessions(id, cwd, summary, agent_name, created_at, updated_at)
           turns(session_id, turn_index, user_message, assistant_response, timestamp)
2. <AppData>/Roaming/Code/User/globalStorage/emptyWindowChatSessions/*.jsonl
3. <AppData>/Roaming/Code/User/workspaceStorage/<workspace>/chatSessions/*.jsonl
   工作区级聊天（chat.ChatSessionStore）。文件为增量补丁日志：
   kind=0 快照 / kind=1 设值(k 为点路径) / kind=2 数组插入(k 指向目标数组，
   v 为待插入元素列表)。标题与时间取自同目录 state.vscdb 的
   chat.ChatSessionStore.index（entries[sessionId].title/timing/lastMessageDate）。
   该结构为逆向所得、无官方承诺：重构按行容错，失败行计数进 extra.warnings。

已知局限：
- 仅覆盖上述三路；VS Code 内嵌 chat 的其他落点（如旧版内联聊天）未覆盖。
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from ..models import Message, SessionRecord, normalize_text, to_local_ts
from .base import BaseAdapter, DetectReport, open_ro_sqlite

CODE_GLOBAL = Path(os.environ.get(
    "APPDATA", Path.home() / "AppData" / "Roaming")) / "Code" / "User" / "globalStorage"

WS_INDEX_KEY = "chat.ChatSessionStore.index"


def _model_from_state(state: dict | None) -> tuple[str | None, dict]:
    """会话主模型抽取（v0.22 P0-5，H13）：requests[].result.metadata
    .resolvedModel 优先（路由后真实模型，真实日志实测路径），缺失兜底
    requests[].modelId（用户选择的模型项，常为 copilot/auto）；众数胜出。
    返回 (众数, 全分布)。纯函数。"""
    from collections import Counter
    if not isinstance(state, dict):
        return None, {}
    seen: Counter[str] = Counter()
    for req in state.get("requests") or []:
        if not isinstance(req, dict):
            continue
        meta = ((req.get("result") or {}).get("metadata") or {})
        m = meta.get("resolvedModel") or req.get("modelId")
        if isinstance(m, str) and m:
            seen[m] += 1
    if not seen:
        return None, {}
    return seen.most_common(1)[0][0], dict(seen)


def _replay_patches(lines: list[str]) -> tuple[dict | None, int, int]:
    """重放 workspace chatSessions 的增量补丁日志。

    行格式（2026-10-05 实测逆向，无官方承诺）：
      {"kind": 0, "v": {...}}                      初始快照
      {"kind": 1, "k": ["a","b"], "v": x}          设值：k 为点路径，末段为键/下标
      {"kind": 2, "k": ["a","b"], "v": [...] }     数组插入：k 指向目标数组，v 为元素列表
    返回 (最终状态, 成功行数, 失败行数)。路径缺失处自动补建（{} / []）。
    """
    state: dict | None = None
    applied = failed = 0
    for ln in lines:
        try:
            o = json.loads(ln)
        except json.JSONDecodeError:
            failed += 1
            continue
        kind = o.get("kind")
        if kind == 0:
            state = o.get("v") if isinstance(o.get("v"), dict) else {}
            applied += 1
            continue
        keys = o.get("k")
        if not keys or not isinstance(keys, list) or state is None:
            failed += 1
            continue
        try:
            cur: Any = state
            for k in keys[:-1]:
                if isinstance(cur, list):
                    i = int(k)
                    while len(cur) <= i:
                        cur.append({})
                    cur = cur[i]
                elif isinstance(cur, dict):
                    if not isinstance(cur.get(k), (dict, list)):
                        cur[k] = {}
                    cur = cur[k]
                else:
                    raise TypeError(type(cur).__name__)
            last = keys[-1]
            if kind == 2:  # 插入：末段定位目标数组
                if isinstance(cur, list):
                    i = int(last)
                    while len(cur) <= i:
                        cur.append([])
                    cur = cur[i]
                elif isinstance(cur, dict):
                    if not isinstance(cur.get(last), list):
                        cur[last] = []
                    cur = cur[last]
                else:
                    raise TypeError(type(cur).__name__)
                v = o.get("v")
                if isinstance(v, list):
                    cur.extend(v)
                else:
                    cur.append(v)
            else:  # kind == 1 设值
                if isinstance(cur, list):
                    i = int(last)
                    while len(cur) <= i:
                        cur.append(None)
                    cur[i] = o.get("v")
                elif isinstance(cur, dict):
                    cur[last] = o.get("v")
                else:
                    raise TypeError(type(cur).__name__)
            applied += 1
        except (ValueError, TypeError, IndexError):
            failed += 1
    return state, applied, failed


def _ws_extract(state: dict) -> tuple[list[Message], list[str]]:
    """从重构后的会话状态提取消息。返回 (messages, warnings)。"""
    warns: list[str] = []
    msgs: list[Message] = []
    requests = state.get("requests")
    if not isinstance(requests, list):
        return [], ["重构状态缺少 requests 数组"]
    for i, req in enumerate(requests):
        if not isinstance(req, dict):
            warns.append(f"requests[{i}] 不是对象，已跳过")
            continue
        m = req.get("message")
        user_text = ""
        if isinstance(m, str):
            user_text = m
        elif isinstance(m, dict):
            user_text = m.get("text") or ""
        if user_text.strip():
            msgs.append(Message(role="user", text=normalize_text(user_text),
                                timestamp=to_local_ts(req.get("timestamp"))))
        reply_parts: list[str] = []
        response = req.get("response")
        if isinstance(response, list):
            for item in response:
                if not isinstance(item, dict):
                    continue
                kind = item.get("kind")
                value = item.get("value")
                if kind is None and isinstance(value, str) and value.strip():
                    # 无 kind 的 str value 为回复正文块（实测形态）
                    reply_parts.append(value)
                elif kind == "thinking" and isinstance(value, str) and value.strip():
                    msgs.append(Message(
                        role="note",
                        text=f"[reasoning]\n{normalize_text(value).rstrip()}"))
                elif kind == "toolInvocationSerialized":
                    inv = item.get("invocationMessage")
                    label = ""
                    if isinstance(inv, dict):
                        label = inv.get("value") or ""
                    elif isinstance(inv, str):
                        label = inv
                    # 规范工具名取 toolId（如 copilot_readFile）；
                    # invocationMessage.value 是给人看的描述句
                    # （"Reading [](file://...)"），旧版误当工具名入库（v0.15 修）。
                    tool = normalize_text(item.get("toolId") or "")
                    if not tool:
                        tool = normalize_text(label)[:60]  # 兜底：无 toolId 旧格式
                    if tool:
                        msgs.append(Message(
                            role="note",
                            text=f"[tool_call] {tool}: {normalize_text(label)}",
                            raw={"kind": "tool_call", "tool": tool,
                                 "detail": normalize_text(label)[:200]}))
        # 本回合回复正文：同一 request 的正文块合并为一条 assistant
        if reply_parts:
            msgs.append(Message(role="assistant",
                                text=normalize_text("\n".join(reply_parts)).strip()))
    return msgs, warns


def _ws_index(wshash_dir: Path) -> dict:
    """读 workspace state.vscdb 的 chat.ChatSessionStore.index。

    返回 {sessionId: entry_dict}；不可读时返回空（索引仅提供标题/时间，
    缺失不阻塞会话解析）。
    """
    db = wshash_dir / "state.vscdb"
    if not db.is_file():
        return {}
    try:
        con = open_ro_sqlite(db)  # WAL 锁冲突时自动退化临时副本
    except sqlite3.Error:  # noqa: BLE001
        return {}
    try:
        row = con.execute(
            "SELECT value FROM ItemTable WHERE key=?", (WS_INDEX_KEY,)).fetchone()
        if not row:
            return {}
        obj = json.loads(row[0])
        entries = obj.get("entries") if isinstance(obj, dict) else None
        return entries if isinstance(entries, dict) else {}
    except (sqlite3.Error, json.JSONDecodeError):  # noqa: BLE001
        return {}
    finally:
        con.close()


def _fmt_ts(v) -> str | None:
    """时间戳归一化：容忍 epoch(ms/s) 与 ISO 字符串两种来源。

    epoch 按**本地时区**渲染：纲要日期须与用户墙钟一致（用 UTC 会差一天）。
    """
    if v is None:
        return None
    if isinstance(v, (int, float)):
        # epoch 秒 ~ 1e9，毫秒 ~ 1e12
        if v > 1e11:
            v = v / 1000.0
        return datetime.fromtimestamp(v).strftime("%Y-%m-%d %H:%M:%S")
    return str(v)


class VscodeCopilotAdapter(BaseAdapter):
    id = "vscode-copilot"
    name = "VS Code Copilot Chat"
    category = "IDE助手"

    def __init__(self, global_storage: Path = CODE_GLOBAL):
        self.gs = Path(global_storage)
        self.db = self.gs / "github.copilot-chat" / "session-store.db"
        self.empty_dir = self.gs / "emptyWindowChatSessions"
        self.ws_root = self.gs.parent / "workspaceStorage"
        self._overview: dict[str, dict] | None = None

    # ---- 工作区级 chatSessions（chat.ChatSessionStore） ----
    def _ws_files(self) -> list[tuple[str, str, Path]]:
        """扫描 workspaceStorage/<hash>/chatSessions/*.jsonl。

        返回 [(workspace_hash, session_id, jsonl_path)]。
        """
        out: list[tuple[str, str, Path]] = []
        if not self.ws_root.is_dir():
            return out
        for wsdir in sorted(self.ws_root.iterdir()):
            cs = wsdir / "chatSessions"
            if not cs.is_dir():
                continue
            for f in sorted(cs.glob("*.jsonl")):
                out.append((wsdir.name, f.stem, f))
        return out

    def detect(self) -> DetectReport:
        found = []
        n = 0
        db_broken = False
        if self.db.is_file():
            try:
                con = open_ro_sqlite(self.db)  # VS Code 运行时 WAL 锁 → 临时副本
                try:
                    n = con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
                finally:
                    con.close()  # 查询失败也要关闭，避免连接泄漏
                found.append(f"session-store.db ({n} sessions)")
            except Exception as e:  # noqa: BLE001 - 探测阶段吞掉一切读取错误
                found.append(f"session-store.db 存在但读取失败: {e}")
                db_broken = True  # 有会话库却读不动：降级 STUB，不能报 OK
        n_empty = len(list(self.empty_dir.glob("*.jsonl"))) if self.empty_dir.is_dir() else 0
        if n_empty:
            found.append(f"emptyWindowChatSessions ({n_empty} files)")
        n_ws = len(self._ws_files())
        if n_ws:
            found.append(f"workspaceStorage chatSessions ({n_ws} files)")
        if not found:
            return DetectReport(self.id, self.name, "MISSING", "未找到 Copilot Chat 会话存储")
        status = "STUB" if db_broken else "OK"
        if db_broken:
            found.append("已降级 STUB：会话库不可读，scan 将跳过本数据源")
        return DetectReport(self.id, self.name, status, "; ".join(found),
                            session_count=n + n_empty + n_ws)

    # ---- DB 部分 ----
    def _db_overview(self) -> dict[str, dict]:
        if self._overview is not None:
            return self._overview
        out: dict[str, dict] = {}
        if self.db.is_file():
            con = open_ro_sqlite(self.db)
            try:
                for sid, cwd, summary, agent, ca, ua in con.execute(
                    "SELECT id, cwd, summary, agent_name, created_at, updated_at FROM sessions"
                ):
                    out[str(sid)] = {
                        "title": summary or f"Copilot 会话 {sid}",
                        "created_at": _fmt_ts(ca), "updated_at": _fmt_ts(ua),
                        "cwd": cwd, "agent": agent, "origin": "db",
                    }
            finally:
                con.close()
        self._overview = out
        return out

    def list_sessions(self) -> list[dict]:
        items = []
        for sid, info in self._db_overview().items():
            items.append({
                "session_id": f"db::{sid}", "title": info["title"],
                "created_at": info["created_at"], "updated_at": info["updated_at"],
                "message_count": None, "preview": info.get("cwd") or "",
            })
        if self.empty_dir.is_dir():
            for f in sorted(self.empty_dir.glob("*.jsonl")):
                preview = ""
                try:
                    for line in self._read_text(f).splitlines()[:20]:
                        obj = json.loads(line)
                        txt = _extract_text(obj)
                        if txt:
                            preview = txt[:80]
                            break
                except Exception:  # noqa: BLE001
                    preview = ""
                items.append({
                    "session_id": f"empty::{f.stem}",
                    "title": f"空窗口会话 {f.stem[:8]}",
                    "created_at": _fmt_ts(f.stat().st_mtime),
                    "updated_at": _fmt_ts(f.stat().st_mtime),
                    "message_count": None, "preview": preview,
                })
        for wshash, sid, f in self._ws_files():
            idx = _ws_index(self.ws_root / wshash).get(sid) or {}
            if idx.get("isEmpty"):
                continue  # 空会话（无任何请求）不进纲要
            created = (idx.get("timing") or {}).get("created")
            updated = idx.get("lastMessageDate")
            title = idx.get("title") or f"工作区会话 {sid[:8]}"
            preview = ""
            try:
                for line in self._read_text(f).splitlines()[:20]:
                    obj = json.loads(line)
                    txt = _extract_text(obj)
                    if txt:
                        preview = txt[:80]
                        break
            except Exception:  # noqa: BLE001
                preview = ""
            items.append({
                "session_id": f"ws::{wshash}::{sid}", "title": title,
                "created_at": _fmt_ts(created) or _fmt_ts(f.stat().st_mtime),
                "updated_at": _fmt_ts(updated) or _fmt_ts(f.stat().st_mtime),
                "message_count": None, "preview": preview,
            })
        return items

    def load_session(self, session_id: str) -> SessionRecord:
        kind, _, rest = session_id.partition("::")
        if kind == "ws":
            wshash, _, sid = rest.partition("::")
            return self._load_ws_session(wshash, sid, session_id)
        sid = rest
        if kind == "db":
            con = open_ro_sqlite(self.db)
            try:
                row = con.execute(
                    "SELECT cwd, summary, agent_name, created_at, updated_at FROM sessions WHERE id=?",
                    (sid,),
                ).fetchone()
                if row is None:
                    # 契约区分：解析失败应 lossy 跳过，但 id 查无属于调用方错误，
                    # 与其他 Adapter 一致抛 KeyError（而非让 None 解包成 TypeError）。
                    raise KeyError(f"session-store.db 中不存在会话 id: {sid!r}")
                msgs: list[Message] = []
                for um, ar, ts in con.execute(
                    "SELECT user_message, assistant_response, timestamp FROM turns "
                    "WHERE session_id=? ORDER BY turn_index", (sid,)
                ):
                    if um:
                        msgs.append(Message("user", um, _fmt_ts(ts)))
                    if ar:
                        msgs.append(Message("assistant", ar, _fmt_ts(ts)))
            finally:
                con.close()
            cwd, summary, agent, ca, ua = row
            return SessionRecord(
                # v0.22 H47：session_id 用完整 kind 形态（与 list_sessions
                # 一致），保证增量水位 sid == 入库 sid
                source=self.id, session_id=session_id,
                title=summary or f"Copilot 会话 {sid}",
                created_at=_fmt_ts(ca), updated_at=_fmt_ts(ua), messages=msgs,
                extra={"cwd": cwd, "agent": agent, "origin": "session-store.db"},
            )
        # empty window jsonl
        f = self.empty_dir / f"{sid}.jsonl"
        msgs = []
        lines = self._read_text(f).splitlines()
        for line in lines:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            role, txt = _extract_role_text(obj)
            if txt:
                msgs.append(Message(role, txt))
        # v0.22 P0-5（H13）：文件为增量补丁日志，重放后取模型众数
        # （消息仍按既有逐行口径提取，不在本任务范围）
        state, _applied, _failed = _replay_patches(lines)
        model, models = _model_from_state(state)
        return SessionRecord(
            source=self.id, session_id=session_id, title=f"空窗口会话 {sid[:8]}",
            created_at=_fmt_ts(f.stat().st_mtime), updated_at=_fmt_ts(f.stat().st_mtime),
            messages=msgs,
            extra={"origin": "emptyWindowChatSessions",
                   "model": model, "models": models or None},
        )

    def _load_ws_session(self, wshash: str, sid: str,
                         full_id: str) -> SessionRecord:
        """加载 workspaceStorage/<wshash>/chatSessions/<sid>.jsonl。

        结构为逆向所得的补丁日志，重构按行容错；标题/时间取 index，
        缺失回退文件 mtime。
        """
        wsdir = self.ws_root / wshash
        f = wsdir / "chatSessions" / f"{sid}.jsonl"
        if not f.is_file():
            raise KeyError(f"工作区会话文件不存在: {f}")
        idx = _ws_index(wsdir).get(sid) or {}
        state, applied, failed = _replay_patches(
            self._read_text(f).splitlines())
        msgs: list[Message] = []
        warns: list[str] = []
        if state is None:
            warns.append("补丁日志无初始快照行（kind=0），未能重构状态")
        else:
            msgs, w2 = _ws_extract(state)
            warns.extend(w2)
        if failed:
            warns.append(f"{failed} 行补丁应用失败（已跳过，源格式可能已改版）")
        created = (idx.get("timing") or {}).get("created")
        updated = idx.get("lastMessageDate")
        title = idx.get("title") or f"工作区会话 {sid[:8]}"
        model, models = _model_from_state(state)  # v0.22 P0-5（H13）
        return SessionRecord(
            source=self.id, session_id=full_id, title=title,
            created_at=_fmt_ts(created) or _fmt_ts(f.stat().st_mtime),
            updated_at=_fmt_ts(updated) or _fmt_ts(f.stat().st_mtime),
            messages=msgs,
            extra={"origin": "workspaceStorage/chatSessions",
                   "workspace": wshash,
                   "patches": {"applied": applied, "failed": failed},
                   "model": model, "models": models or None,
                   "lossy": bool(warns), "warnings": warns},
        )


def _extract_text(obj) -> str:
    role, txt = _extract_role_text(obj)
    return txt if role else ""


def _extract_role_text(obj):
    """防御式抽取：jsonl 行结构未做官方承诺，逐键探测。换行统一归一化为 LF。"""
    if not isinstance(obj, dict):
        return "", ""
    role = obj.get("role") or obj.get("type") or ""
    for k in ("text", "content", "message", "value"):
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            return str(role), normalize_text(v)
        if isinstance(v, dict):
            inner = _extract_text(v)
            if inner:
                return str(role), inner
        if isinstance(v, list):
            parts = [p.get("text", "") if isinstance(p, dict) else str(p) for p in v]
            joined = "\n".join(p for p in parts if p).strip()
            if joined:
                return str(role), normalize_text(joined)
    return "", ""
