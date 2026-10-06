# -*- coding: utf-8 -*-
"""豆包（doubao.com）IM 原始采集数据 Adapter。

数据来源（2026-10-06 CDP app-driven capture 管线，见 verify/doubao_harvest.py）：
- 不重放请求（msToken/a_bogus 签名拒绝，错误 712012002），而是让页面自己发请求、
  CDP Network 域截获响应体：
    POST /im/chain/recent_conv  (cmd 3200) → 会话列表
    POST /im/chain/single       (cmd 3100) → 会话消息（滚动触发更早消息，
                                            按 message_id 去重合并）
- 请求为 IM 信封: {cmd, uplink_body, sequence_id, channel:2, version:"1"}，
  响应业务数据在 downlink_body.pull_recent_conv_chain_downlink_body /
  pull_singe_chain_downlink_body。
- 产物：corpus/doubao_raw/{list_0.json, detail_<会话id>.json}
  detail = {conversation_id, name, create_time, update_time, n_messages, messages}

格式核验状态（真机 3 会话全量核验，2026-10-06）：
- 消息: {user_type: 1=用户 / 2=bot, index_in_conv(str 数字), create_time(epoch 秒 str),
  content_block[], brief(纯文本摘要), thinking_content(实测恒空)}
- content_block block_type 实测(9 种):
    10000 text_block        正文（text 字段；text 空且 summary 非空为占位，跳过）
    10040 thinking_block    思考（仅 finish_title 标题；正文走流式通道不落盘 → [think]）
    10091 elapsed_block     耗时元数据（跳过）
    10082 interaction_ask   澄清问题卡（questions[].title → [ask]）
    10019 file_operation    文件读写（file_name/path → [file-op]）
    10030 artifact          Artifacts 产物（title → [artifact]）
    10025 search_query_result 网搜（summary+queries → [search]）
    2074  creation          生成图片（image key → [image]）
    未知                     警告跳过
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ..models import Message, SessionRecord, normalize_text
from .base import BaseAdapter, DetectReport

HOW_TO_HARVEST = [
    "豆包数据需登录态采集（app-driven capture）：verify/doubao_harvest.py",
    "chrome 启动: chrome.exe --remote-debugging-port=9334 --remote-allow-origins=* --user-data-dir=<持久目录>，后台任务托住进程",
    "不可重放请求：msToken/a_bogus 签名校验拒绝（712012002），必须让页面自发请求后截响应体",
    "产物目录结构: corpus/doubao_raw/{list_0.json, detail_<会话id>.json}",
    "在 sources.json 中配置: {\"doubao-raw\": {\"paths\": [\"C:/path/to/doubao_raw\"]}}",
]

#: block_type → 常量名（供未知类型警告输出）
_BLOCK_NAMES = {
    10000: "text_block",
    10040: "thinking_block",
    10091: "elapsed_block",
    10082: "interaction_ask_block",
    10019: "file_operation_block",
    10030: "artifact_block",
    10025: "search_query_result_block",
    2074: "creation_block",
}


def _norm_time(v) -> str | None:
    """豆包时间为 epoch 秒字符串。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        if v > 1e9:
            return datetime.fromtimestamp(v).astimezone().isoformat()
        return None
    s = str(v).strip()
    if not s:
        return None
    if s.isdigit() and int(s) > 1e9:
        return datetime.fromtimestamp(int(s)).astimezone().isoformat()
    return s


def _note(text: str, ts: str | None) -> Message:
    return Message(role="note", text=normalize_text(text), timestamp=ts,
                   raw={"kind": "note"})


def _msg_messages(m: dict, warns: list[str]) -> list[Message]:
    """一条豆包消息 → 消息序列（块序保真：连续 text 合并，非 text 就地插 note）。"""
    ts = _norm_time(m.get("create_time"))
    is_user = m.get("user_type") == 1
    out: list[Message] = []
    body: list[str] = []

    def flush_body() -> None:
        if body:
            text = normalize_text("\n".join(body))
            if text:
                out.append(Message(role="user" if is_user else "assistant",
                                   text=text, timestamp=ts,
                                   raw={"kind": "user" if is_user else "assistant"}))
            body.clear()

    for b in m.get("content_block") or []:
        if not isinstance(b, dict):
            continue
        bt = b.get("block_type")
        c = b.get("content") or {}
        if bt == 10000:
            tb = c.get("text_block") or {}
            text = (tb.get("text") or "").strip()
            if text:  # text 空但 summary 非空 → 思考占位块，跳过
                body.append(text)
        elif bt == 10040:
            tb = c.get("thinking_block") or {}
            title = (tb.get("finish_title") or tb.get("streaming_title") or "").strip()
            if title:
                flush_body()
                # 豆包思考正文走流式通道不落盘，最终响应仅存标题
                out.append(_note(f"[think] {title}", ts))
        elif bt == 10082:
            qs = [q.get("title", "").strip()
                  for q in c.get("interaction_ask_block", {}).get("questions", []) or []
                  if q.get("title")]
            if qs:
                flush_body()
                out.append(_note("[ask] " + "；".join(qs), ts))
        elif bt == 10019:
            fo = c.get("file_operation_block") or {}
            name = fo.get("file_name") or fo.get("path") or ""
            if name:
                flush_body()
                out.append(_note(f"[file-op] {name}", ts))
        elif bt == 10030:
            ar = c.get("artifact_block") or {}
            if ar.get("title"):
                flush_body()
                out.append(_note(f"[artifact] {ar['title']}", ts))
        elif bt == 10025:
            sq = c.get("search_query_result_block") or {}
            parts = []
            if sq.get("summary"):
                parts.append(sq["summary"])
            qs = sq.get("queries") or []
            if qs:
                parts.append("queries: " + ", ".join(qs))
            if parts:
                flush_body()
                out.append(_note("[search] " + "；".join(parts), ts))
        elif bt == 2074:
            cr = c.get("creation_block") or {}
            imgs = [x.get("image", {}).get("key")
                    for x in cr.get("creations", []) or [] if x.get("image")]
            if imgs:
                flush_body()
                out.append(_note(f"[image] {len(imgs)} 张生成图 "
                                 f"({imgs[0].rsplit('/', 1)[-1]})", ts))
        elif bt in (10091,):
            pass  # 耗时元数据
        else:
            flush_body()
            warns.append(f"未知 block_type={bt} "
                         f"({_BLOCK_NAMES.get(bt, '?')}) msg={m.get('message_id')}")
    flush_body()
    return out


def _parse_detail(j: dict) -> tuple[list[Message], list[str]]:
    warns: list[str] = []
    msgs = [m for m in (j.get("messages") or []) if isinstance(m, dict)]
    try:
        msgs.sort(key=lambda m: int(m.get("index_in_conv") or 0))
    except (TypeError, ValueError):
        warns.append("index_in_conv 排序失败，保持原始顺序")
    out: list[Message] = []
    for m in msgs:
        out.extend(_msg_messages(m, warns))
    return out, warns


class DoubaoRawAdapter(BaseAdapter):
    id = "doubao-raw"
    claims_files = False  # 目录型源（detail_*.json），禁止收件箱单文件认领
    name = "豆包 IM 原始采集"
    category = "网页/客户端Chat"
    candidate_paths = ("corpus/doubao_raw",)

    def __init__(self, paths: list[str | Path] | None = None):
        self.paths = [Path(p) for p in (paths or [])]
        self._detail_dir: Path | None = None
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

    def detect(self) -> DetectReport:
        d = self._find_dir()
        if d is None:
            return DetectReport(
                self.id, self.name, "STUB" if self.paths else "MISSING",
                "未找到豆包采集目录（需含 detail_*.json）", hints=HOW_TO_HARVEST)
        n = len(list(d.glob("detail_*.json")))
        return DetectReport(self.id, self.name, "OK",
                            f"{d}（{n} 个 detail 文件）", session_count=n)

    @staticmethod
    def _header(j: dict, session_id: str) -> dict:
        return {
            "title": j.get("name") or f"会话 {session_id[:8]}",
            "created_at": _norm_time(j.get("create_time")),
            "updated_at": _norm_time(j.get("update_time")),
        }

    def list_sessions(self) -> list[dict]:
        d = self._find_dir()
        if d is None:
            return []
        items = []
        for p in sorted(d.glob("detail_*.json")):
            sid = p.stem[len("detail_"):]
            try:
                j = json.loads(p.read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                self._parse_warns.append(f"{p.name}: {e}")
                continue
            h = self._header(j, sid)
            items.append({
                "session_id": sid,
                "title": h["title"],
                "created_at": h["created_at"],
                "updated_at": h["updated_at"],
                "message_count": j.get("n_messages"),
                "preview": "",
            })
        return items

    def load_session(self, session_id: str) -> SessionRecord:
        d = self._find_dir()
        if d is None:
            raise KeyError(f"豆包采集目录不存在: {session_id}")
        p = d / f"detail_{session_id}.json"
        if not p.is_file():
            raise KeyError(f"会话不存在: {session_id}")
        j = json.loads(p.read_text(encoding="utf-8"))
        messages, warns = _parse_detail(j)
        if warns:
            self._parse_warns.extend(warns[:5])
        h = self._header(j, session_id)
        return SessionRecord(
            source=self.id,
            session_id=session_id,
            title=h["title"],
            created_at=h["created_at"],
            updated_at=h["updated_at"],
            messages=messages,
            extra={
                "lossy": bool(warns),
                "warnings": warns,
                "thinking_note": "豆包思考正文走流式通道不落盘，仅存标题",
            })
