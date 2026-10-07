# -*- coding: utf-8 -*-
"""WorkBuddy 真实会话轨迹 Adapter（~/.workbuddy/projects/）。

数据源（2026-10-06 实测核验，158 个 jsonl / 262MB）：
  ~/.workbuddy/projects/<workspace-slug>/<session-uuid>.jsonl

逐行 JSON，type 取值（实测分布）：
- message           role=user|assistant，content 为块列表：
                    user→input_text / assistant→output_text
- reasoning         顶层行，content/rawContent 为思考块（note）
- function_call     name / arguments(JSON 字符串) / callId（note）
- function_call_result  callId / name / output{type,text} / status
                    实测 status 恒为 "completed"，错误以 "Error:" 前缀
                    出现在 output.text 中（无结构化错误码）→ note
- ai-title          aiTitle（取最后一次出现的作为会话标题）
- session-meta / file-history-snapshot  跳过

user 消息构成（实测 514 条：399 条被包裹 / 115 条纯文本）：
  <system-reminder data-role="user-context">…harness 注入上下文…</system-reminder>
  <user_query>真实用户文本</user_query>
映射规则：user_query 内文本 → user 角色；reminder 包裹体 → note "[context]"
（蒸馏时上下文注入不算用户发言，但保留供溯源）。

与 workbuddy adapter（memory 日志）的关系：后者采集的是 agent 自述的每日
工作总结（非对话轨迹），category 独立；本 adapter 才是真实逐回合轨迹。
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path

from ..models import Message, SessionRecord, normalize_text, to_local_ts
from .base import BaseAdapter, DetectReport

WB_PROJECTS = Path(os.environ.get(
    "USERPROFILE", str(Path.home()))) / ".workbuddy" / "projects"

#: 顶层行类型 → 是否产出消息
_SKIP_TYPES = {"file-history-snapshot", "session-meta"}

_USER_QUERY_RE = re.compile(r"<user_query>(.*?)</user_query>", re.DOTALL)


def _extract_user_text(text: str) -> tuple[str, str | None]:
    """从 user 消息中拆出 (真实用户文本, harness 注入上下文)。

    有 <user_query> 标记：query 内为真实文本，其余为注入上下文。
    无标记：整段视为真实文本（旧数据形态）。
    """
    m = _USER_QUERY_RE.search(text)
    if not m:
        return text.strip(), None
    query = m.group(1).strip()
    context = (text[:m.start()] + text[m.end():]).strip()
    return query, (context or None)


def _args_preview(arguments: str, limit: int = 200) -> str:
    """function_call.arguments 是 JSON 字符串 → 取关键字段摘要。"""
    try:
        obj = json.loads(arguments)
    except (json.JSONDecodeError, TypeError):
        return (arguments or "")[:limit]
    if isinstance(obj, dict):
        keep = {}
        for k, v in obj.items():
            s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
            if len(s) > 80:
                s = s[:77] + "..."
            keep[k] = s
        out = json.dumps(keep, ensure_ascii=False)
        return out[:limit]
    return str(obj)[:limit]


class WorkBuddyTranscriptAdapter(BaseAdapter):
    """projects/*.jsonl 逐回合轨迹。id: workbuddy-transcript。"""

    id = "workbuddy-transcript"
    name = "WorkBuddy 会话轨迹"
    category = "Agent"

    def __init__(self, projects_root: Path | None = None):
        self.root = Path(projects_root) if projects_root else WB_PROJECTS

    # ---- helpers -------------------------------------------------

    def _jsonl_files(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        # 递归扫描：顶层 <ws>/<session>.jsonl 为主会话；
        # <ws>/<session>/subagents/agent-*.jsonl 为子代理轨迹（多智能体协作
        # 信号，实测 43 个），一并纳入。
        return sorted(self.root.rglob("*.jsonl"))

    def _scan_lines(self, path: Path):
        """逐行解析 JSON；坏行跳过，返回 (可解析行列表, 坏行数)。"""
        items, bad = [], 0
        with open(path, encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    items.append(json.loads(ln))
                except json.JSONDecodeError:
                    bad += 1
        return items, bad

    @staticmethod
    def _ts_of(obj: dict) -> float | None:
        """epoch 毫秒 → 秒（list_sessions 排序与 mtime 兜底用）。"""
        ts = obj.get("timestamp")
        if isinstance(ts, (int, float)) and ts > 0:
            return ts / 1000.0
        return None

    # ---- protocol ------------------------------------------------

    def detect(self) -> DetectReport:
        files = self._jsonl_files()
        if not files:
            return DetectReport(self.id, self.name, "MISSING",
                                f"未找到轨迹文件（{self.root}）")
        total_mb = sum(f.stat().st_size for f in files) / 1e6
        return DetectReport(
            self.id, self.name, "OK",
            f"{len(files)} 个会话轨迹 / {total_mb:.0f} MB（真实逐回合记录）",
            session_count=len(files))

    def list_sessions(self) -> list[dict]:
        items = []
        for f in self._jsonl_files():
            lines, bad = self._scan_lines(f)
            if bad:
                pass  # 纲要阶段不告警，load 时再报
            first_ts = last_ts = None
            title = None
            n_msgs = 0
            preview = ""
            for o in lines:
                t = o.get("type")
                if t == "ai-title" and o.get("aiTitle"):
                    title = o["aiTitle"]
                ts = self._ts_of(o)
                if ts is not None:
                    first_ts = first_ts or ts
                    last_ts = ts if last_ts is None else max(last_ts, ts)
                if t == "message" and o.get("role") in ("user", "assistant"):
                    n_msgs += 1
                    if o.get("role") == "user" and not preview:
                        txt = "".join(
                            b.get("text", "") for b in o.get("content") or []
                            if isinstance(b, dict))
                        q, _ctx = _extract_user_text(txt)
                        preview = q
            rel = f.relative_to(self.root).as_posix()
            is_sub = "/subagents/" in rel
            ws_name = rel.split("/")[0]
            items.append({
                "session_id": rel,
                "title": (("[subagent] " if is_sub else "")
                          + (title or f"轨迹会话 {f.stem[:8]}")),
                "created_at": to_local_ts(first_ts),
                "updated_at": to_local_ts(last_ts),
                "message_count": n_msgs,
                "preview": preview[:100],
                "workspace": ws_name,
            })
        items.sort(key=lambda x: x["updated_at"] or "", reverse=True)
        return items

    def load_session(self, session_id: str) -> SessionRecord:
        rel = session_id  # 相对 projects 根的 posix 路径
        f = self.root / Path(*rel.split("/"))
        if not f.is_file():
            raise KeyError(f"轨迹文件不存在: {f}")
        lines, bad = self._scan_lines(f)
        warns: list[str] = []
        if bad:
            warns.append(f"{bad} 行 JSON 解析失败（已跳过）")

        msgs: list[Message] = []
        title = None
        pending_calls: dict[str, str] = {}  # callId → name
        first_ts = last_ts = None
        model_seen: Counter[str] = Counter()  # 行级模型计数 → 会话主模型（v0.15）

        for o in lines:
            pd = o.get("providerData") or {}
            if pd.get("model"):
                model_seen[pd["model"]] += 1
            t = o.get("type")
            if t in _SKIP_TYPES:
                continue
            ts = self._ts_of(o)
            iso = to_local_ts(ts)
            if ts is not None:
                first_ts = first_ts or ts
                last_ts = ts if last_ts is None else max(last_ts, ts)
            if t == "ai-title":
                title = o.get("aiTitle") or title
                continue
            if t == "message":
                role = o.get("role")
                blocks = o.get("content") or []
                text = "".join(
                    b.get("text", "") for b in blocks if isinstance(b, dict))
                text = normalize_text(text).strip()
                if not text:
                    continue
                if role == "user":
                    query, ctx = _extract_user_text(text)
                    if ctx:
                        msgs.append(Message(
                            role="note", text=f"[context] {ctx}",
                            timestamp=iso, raw=None))
                    if query:
                        msgs.append(Message(
                            role="user", text=query, timestamp=iso,
                            raw={"model": (o.get("providerData") or {}).get("model")}))
                elif role == "assistant":
                    model = (o.get("providerData") or {}).get("model")
                    msgs.append(Message(
                        role="assistant", text=text, timestamp=iso,
                        raw={"model": model} if model else None))
                continue
            if t == "reasoning":
                # content 是块列表；rawContent 是 JSON 字符串兜底
                blocks = o.get("content")
                rtxt = ""
                if isinstance(blocks, list):
                    rtxt = "".join(
                        b.get("text", "") for b in blocks
                        if isinstance(b, dict))
                elif isinstance(o.get("rawContent"), str):
                    try:
                        rc = json.loads(o["rawContent"])
                        if isinstance(rc, list):
                            rtxt = "".join(
                                b.get("text", "") for b in rc
                                if isinstance(b, dict))
                    except json.JSONDecodeError:
                        rtxt = ""
                rtxt = normalize_text(rtxt).strip()
                if rtxt:
                    msgs.append(Message(
                        role="note", text=f"[reasoning] {rtxt}",
                        timestamp=iso))
                continue
            if t == "function_call":
                name = o.get("name") or "?"
                call_id = o.get("callId") or ""
                pending_calls[call_id] = name
                msgs.append(Message(
                    role="note",
                    text=f"[tool_call] {name}: {_args_preview(o.get('arguments'))}",
                    timestamp=iso,
                    raw={"kind": "tool_call", "tool": name,
                         "detail": _args_preview(o.get("arguments"), 400)}))
                continue
            if t == "function_call_result":
                name = o.get("name") or pending_calls.get(o.get("callId"), "?")
                out = o.get("output")
                out_text = out if isinstance(out, str) else (
                    (out or {}).get("text", "") if isinstance(out, dict) else "")
                out_text = normalize_text(str(out_text)).strip()
                is_err = out_text.lower().startswith("error")
                status = "error" if is_err else (o.get("status") or "completed")
                # 源无结构化错误码：status 恒 completed，错误以 "Error:"
                # 文本前缀呈现（实测 2026-10-06）。输出截断防巨型文件。
                preview = out_text[:400] + ("…" if len(out_text) > 400 else "")
                msgs.append(Message(
                    role="note",
                    text=f"[tool_result] {name}: {status}"
                         f"{'; ' + preview if is_err else ''}",
                    timestamp=iso,
                    raw={"kind": "tool_result", "tool": name,
                         "status": status,
                         "error": preview if is_err else None}))
                continue
            # 其余未知类型：记录一次警告（不中断）
            if t not in ("file-history-snapshot",):
                if len(warns) < 5 and f"未知行类型 {t}" not in warns:
                    warns.append(f"未知行类型 {t}（已跳过）")

        if not msgs:
            warns.append("未解析出任何消息（格式可能已改版）")

        return SessionRecord(
            source=self.id, session_id=rel,
            title=title or f"轨迹会话 {rel.split('/')[-1][:8]}",
            created_at=to_local_ts(first_ts),
            updated_at=to_local_ts(last_ts),
            messages=msgs,
            extra={
                "file": str(f),
                "workspace": rel.split("/")[0],
                "is_subagent": "/subagents/" in rel,
                "origin": "workbuddy-projects-transcript",
                "lossy": bool(warns),
                "warnings": warns,
                # 会话主模型 = 行级 providerData.model 众数（会话中途换模型
                # 时以使用最多的为准；全部分布存 models）（v0.15）
                "model": model_seen.most_common(1)[0][0] if model_seen else None,
                "models": dict(model_seen) or None,
            },
        )
