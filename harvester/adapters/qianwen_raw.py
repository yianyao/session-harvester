# -*- coding: utf-8 -*-
"""通义千问 API 原始采集数据 Adapter。

数据来源（2026-10-06 CDP 登录态采集管线，见 verify/cdp_driver.py +
verify/qianwen_detail_harvest.py）：
- 页面内 fetch 官方接口（无需账号密码）：
    POST /api/v2/session/page/list  body {next_token?}（游标分页，have_next_page）
    GET  /api/v1/session/msg/list?session_id=...&page_size=100（have_next_page
         时以 pos=<最后一条 pos> 续拉）
    公共查询串: biz_id=ai_qwen&chat_client=h5&device=pc&fr=pc&pr=qwen&ut=<uuid>&la=zh-CN
- 产物：corpus/qianwen_raw/{list_<n>.json, detail_<sid>.json}
  detail = {session_id, rounds, list: [轮...]}

格式核验状态（真机 117 会话 / 469 轮全量核验，2026-10-06）：
- 轮: {user_type:0, request_messages[], response_messages[], created_at(epoch ms),
  create_time(ISO), error_code, pos}
- request_messages mime_type 实测: text/plain(正文 content) / doc/url(附件
  meta_data.resource_infos[{file_name,file_format,file_size}]) / image/url /
  text/hidden(纯元数据，跳过)
- response_messages mime_type 实测(8 种):
    multi_load/iframe  正文（content 即回答全文）
    plan_cot/post      思考/规划（content 非空时为思考正文 → [think]）
    bar/workflow       工作流步骤（meta_data.multi_load[] 中 type=bar_thinking
                       的 content.{title,body} 为分步思考 → [think]）
    signal/post        信号元数据（跳过）
    bar/progress       进度条（跳过）
    bar/iframe         来源栏（sources 通常空，跳过）
    paa/iframe         推荐追问（噪声，跳过）
    survey/card        问卷卡（跳过）
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ..models import Message, SessionRecord, normalize_text
from .base import BaseAdapter, DetectReport

HOW_TO_HARVEST = [
    "千问数据需登录态采集：verify/cdp_driver.py（CDP 驱动）+ verify/qianwen_detail_harvest.py",
    "chrome 启动: chrome.exe --remote-debugging-port=9333 --remote-allow-origins=* --user-data-dir=<持久目录>，后台任务托住进程",
    "产物目录结构: corpus/qianwen_raw/{list_<n>.json, detail_<会话id>.json}",
    "在 sources.json 中配置: {\"qianwen-raw\": {\"paths\": [\"C:/path/to/qianwen_raw\"]}}",
]


def _norm_time(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        if v > 1e12:   # epoch 毫秒
            v = v / 1000.0
        if v > 1e9:    # epoch 秒
            return datetime.fromtimestamp(v).astimezone().isoformat()
        return None
    s = str(v).strip()
    return s or None


def _note(text: str, ts: str | None) -> Message:
    return Message(role="note", text=normalize_text(text), timestamp=ts,
                   raw={"kind": "note"})


def _round_messages(round_: dict, warns: list[str]) -> list[Message]:
    """一问一答轮 → 消息序列（附件 note → user → think note → assistant）。"""
    out: list[Message] = []
    ts = _norm_time(round_.get("created_at"))
    if round_.get("error_code"):
        warns.append(f"error_code={round_['error_code']} "
                     f"({round_.get('error_msg') or ''}) round pos={round_.get('pos')}")

    # ---- 请求侧 ----
    user_text: list[str] = []
    for b in round_.get("request_messages", []) or []:
        mt = b.get("mime_type")
        if mt == "text/plain":
            if (b.get("content") or "").strip():
                user_text.append(b["content"])
        elif mt in ("doc/url", "image/url"):
            for r in (b.get("meta_data") or {}).get("resource_infos", []) or []:
                tag = "file" if mt == "doc/url" else "image"
                size = r.get("file_size")
                out.append(_note(
                    f"[{tag}] {r.get('file_name')}"
                    + (f" ({size} bytes)" if size else ""), ts))
        # text/hidden 等纯元数据块跳过
    if user_text:
        out.append(Message(role="user",
                           text=normalize_text("\n".join(user_text)),
                           timestamp=ts, raw={"kind": "user"}))

    # ---- 响应侧 ----
    think_parts: list[str] = []
    answer_parts: list[str] = []
    for b in round_.get("response_messages", []) or []:
        mt = b.get("mime_type")
        if mt == "multi_load/iframe":
            if (b.get("content") or "").strip():
                answer_parts.append(b["content"])
        elif mt == "plan_cot/post":
            if (b.get("content") or "").strip():
                think_parts.append(b["content"])
        elif mt == "bar/workflow":
            for step in (b.get("meta_data") or {}).get("multi_load", []) or []:
                if step.get("type") == "bar_thinking":
                    c = step.get("content") or {}
                    title, body = c.get("title"), c.get("body")
                    if body or title:
                        think_parts.append(
                            f"{title}：{body}" if title and body else (body or title or ""))
        # signal/post / bar/progress / bar/iframe / paa/iframe / survey/card 跳过
        # 未知类型静默容忍（响应侧块类型噪声多，已在文档记录全部已知类型）
    if think_parts:
        out.append(_note("[think] " + normalize_text("\n\n".join(think_parts)), ts))
    if answer_parts:
        out.append(Message(role="assistant",
                           text=normalize_text("\n".join(answer_parts)),
                           timestamp=ts, raw={"kind": "assistant"}))
    return out


def _parse_detail(j: dict) -> tuple[list[Message], list[str]]:
    warns: list[str] = []
    rounds = j.get("list") or []
    rounds = [r for r in rounds if isinstance(r, dict)]
    rounds.sort(key=lambda r: r.get("created_at") or 0)
    messages: list[Message] = []
    for r in rounds:
        messages.extend(_round_messages(r, warns))
    return messages, warns


class QianwenRawAdapter(BaseAdapter):
    id = "qianwen-raw"
    claims_files = False  # 目录型源（detail_*.json），禁止收件箱单文件认领
    name = "通义千问 API 原始采集"
    category = "网页/客户端Chat"
    candidate_paths = ("corpus/qianwen_raw",)

    def __init__(self, paths: list[str | Path] | None = None):
        self.paths = [Path(p) for p in (paths or [])]
        self._detail_dir: Path | None = None
        self._list_meta: dict[str, dict] | None = None
        self._parse_warns: list[str] = []

    def _find_dir(self) -> Path | None:
        if self._detail_dir is not None:
            return self._detail_dir
        for base in self.paths:
            if base.is_dir() and list(base.glob("detail_*.json")):
                self._detail_dir = base
                return base
        for cand in self.candidate_paths:
            p = Path(cand)
            if p.is_dir() and list(p.glob("detail_*.json")):
                self._detail_dir = p
                return p
        return None

    def _ensure_list_meta(self) -> dict[str, dict]:
        if self._list_meta is not None:
            return self._list_meta
        meta: dict[str, dict] = {}
        d = self._find_dir()
        if d:
            pages = sorted(d.glob("list_*.json"),
                           key=lambda p: int(p.stem.split("_")[1])
                           if p.stem.split("_")[1].isdigit() else 0)
            for p in pages:
                try:
                    j = json.loads(p.read_text(encoding="utf-8"))
                except Exception as e:  # noqa: BLE001
                    self._parse_warns.append(f"{p.name}: {e}")
                    continue
                for s in j.get("list", []) or []:
                    if isinstance(s, dict) and s.get("session_id"):
                        meta[s["session_id"]] = s
        self._list_meta = meta
        return meta

    def detect(self) -> DetectReport:
        d = self._find_dir()
        if d is None:
            return DetectReport(
                self.id, self.name, "STUB" if self.paths else "MISSING",
                "未找到千问采集目录（需含 detail_*.json）", hints=HOW_TO_HARVEST)
        n = len(list(d.glob("detail_*.json")))
        return DetectReport(self.id, self.name, "OK",
                            f"{d}（{n} 个 detail 文件）", session_count=n)

    def list_sessions(self) -> list[dict]:
        d = self._find_dir()
        if d is None:
            return []
        meta = self._ensure_list_meta()
        items = []
        for p in sorted(d.glob("detail_*.json")):
            sid = p.stem[len("detail_"):]
            m = meta.get(sid, {})
            items.append({
                "session_id": sid,
                "title": m.get("title") or f"会话 {sid[:8]}",
                "created_at": _norm_time(m.get("created_at")),
                "updated_at": _norm_time(m.get("updated_at")),
                "message_count": None,
                "preview": "",
            })
        return items

    def load_session(self, session_id: str) -> SessionRecord:
        d = self._find_dir()
        if d is None:
            raise KeyError(f"千问采集目录不存在: {session_id}")
        p = d / f"detail_{session_id}.json"
        if not p.is_file():
            raise KeyError(f"会话不存在: {session_id}")
        j = json.loads(p.read_text(encoding="utf-8"))
        messages, warns = _parse_detail(j)
        if warns:
            self._parse_warns.extend(warns[:5])
        m = self._ensure_list_meta().get(session_id, {})
        return SessionRecord(
            source=self.id,
            session_id=session_id,
            title=m.get("title") or f"会话 {session_id[:8]}",
            created_at=_norm_time(m.get("created_at")),
            updated_at=_norm_time(m.get("updated_at")),
            messages=messages,
            extra={
                "topic_id": m.get("topic_id"),
                "lossy": bool(warns),
                "warnings": warns,
            })
