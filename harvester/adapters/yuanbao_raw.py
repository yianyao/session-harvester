# -*- coding: utf-8 -*-
"""腾讯元宝 API 原始采集数据 Adapter。

数据来源（2026-10-06 agent-browser 真机采集管线，见 verify/yuanbao_*.py）：
- 登录态页面内 fetch 调元宝官方接口（无需账号密码、无风控改动）：
    POST /api/user/agent/conversation/list    body {limit, offset}（顶层分页！
         嵌套 pagination 会被服务端静默忽略——踩坑实录）
    POST /api/user/agent/conversation/v1/detail body {conversationId}
- 采集产物为目录：list_<n>.json（分页清单）+ detail_<会话id>.json

格式核验状态（真机 1223 会话样本核验，2026-10-06）：
- detail 顶层：{id, title, sessionTitle, chatModelId, modelId, agentName,
  firstRepliedAt, lastRepliedAt(epoch 秒), convs[], ...}
- convs[]: {id, conversationId, speaker, createTime(epoch 秒), index,
  speechesV2[], ...}；speaker 取值 'human' | 'ai'（侦察报告写的 'user' 有误，
  以真机为准）
- speechesV2[]: {speechType, content[]}；speechType 实测：
  text / search_with_text / deep_search / deep_search_agent / multimodal
- content[] 块 type 实测（12 种，v0.23 全量 1223 个 detail 复核）：
    text            正文 {msg}（human 附件+正文同轮时 pdf 块在前）
    searchGuid      联网搜索引用 {title:"引用 N 篇资料…", docs:[{index,docId,title,uri?}]}
    deepSearch      深度思考 {title:"已深度思考(用时7秒)", contents:[{type:'text',msg}]}
                    —— contents[].msg 即思考过程正文（2.1 蒸馏核心语料）
    deepSearchAgent Agent 思考 {title:"已处理", contents:[{type:'text',text|msg}]}
    pdf             附件 {fileName, url, size, mediaId}
    image           图片 {fileName, url, width?, height?}
    prompt_url_card 分享卡片 {desc, coverUrl, iconUrl}
    link_card       链接卡片 {url, content, source, coverUrl, iconUrl}
    step            执行步骤 {msg, subMsg, stage, status}
    drawWithSearchGuid  AI 出图提示词 {prompt, botPrompt, title, ...}
                    —— v0.23 补入，属创作内容，转 note（[draw] 前缀）
    doc_percent     系统通知 {content:"超出字数限制，元宝已阅读93%", fileNumber}
                    —— v0.23 补入，转 note（[notice] 前缀），不参与正文提炼
    （白名单外的类型防御式跳过并警告，绝不臆测；白名单内但无解析分支的
      类型单独告警——那是实现缺口，与源端新增类型成因不同）

解析策略：
- text 块按序拼接为该轮正文；其余块降为 note 消息（[search]/[think]/[file]/…），
  note 紧随其所属轮次之后，顺序即还原后的对话流。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ..models import Message, SessionRecord, normalize_text
from .base import BaseAdapter, DetectReport

#: 采集管线说明（detect STUB 时的行动指引）
HOW_TO_HARVEST = [
    "元宝数据需登录态采集：verify/yuanbao_receiver.py（本地接收）+ verify/yuanbao_list_harvest.js / yuanbao_detail_harvest.py（agent-browser 页面内拉取）",
    "产物目录结构: corpus/yuanbao_raw/{list_<n>.json, detail_<会话id>.json}",
    "在 sources.json 中配置: {\"yuanbao-raw\": {\"paths\": [\"C:/path/to/yuanbao_raw\"]}}",
]

#: 已核验的 content 块类型（其余类型防御式跳过+警告）。
#: v0.23：全量实测 1223 个 detail 文件后补入两种实际产出但原先漏列的类型
#: （用户 2026-10-09 裁决）：
#:   drawWithSearchGuid —— AI 出图的完整绘图提示词（prompt/botPrompt），
#:                        属**创作内容**，纳入并转 note，不得丢弃；
#:   doc_percent        —— 系统通知（如"超出字数限制，元宝已阅读93%"），
#:                        纳入白名单但按 notice 处理，不参与正文提炼。
KNOWN_BLOCK_TYPES = {
    "text", "searchGuid", "deepSearch", "deepSearchAgent",
    "pdf", "image", "prompt_url_card", "link_card", "step",
    "drawWithSearchGuid", "doc_percent",
}

#: 兼容旧名（此前该集合无引用，属"注释承诺但未实现"；现为唯一来源）。
_KNOWN_BLOCK_TYPES = KNOWN_BLOCK_TYPES

#: 白名单内但**不产出正文**的系统通知类块（显式记录，避免被误当内容）。
_NOTICE_BLOCK_TYPES = {"doc_percent"}


def _norm_time(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        if v > 1e11:  # epoch 毫秒
            v = v / 1000.0
        if v > 1e9:   # epoch 秒
            return datetime.fromtimestamp(v).astimezone().isoformat()
        return None
    s = str(v).strip()
    return s or None


def _block_text(b: dict) -> str:
    """text 类块的正文提取：兼容 msg / text 两种键（deepSearchAgent 用 text）。"""
    for k in ("msg", "text"):
        v = b.get(k)
        if isinstance(v, str) and v:
            return normalize_text(v)
    return ""


def _conv_blocks(conv: dict) -> tuple[list[Message], list[str]]:
    """单条 conv（一个对话轮）→ 消息列表。返回 (messages, warnings)。

    顺序语义：保持块在源数据中的出现顺序（human 的 pdf 附件块在 text 前、
    ai 的 searchGuid/deepSearch 块在正文 text 前），非 text 块作为 note
    消息就地插入；连续 text 块合并为一条 role 消息。
    """
    warns: list[str] = []
    speaker = conv.get("speaker")
    if speaker not in ("human", "ai"):
        return [], [f"未知 speaker={speaker!r}（conv id={conv.get('id')}）"]
    role = "user" if speaker == "human" else "assistant"
    ts = _norm_time(conv.get("createTime"))

    out: list[Message] = []
    body: list[str] = []

    def flush_body() -> None:
        if body:
            out.append(Message(role=role,
                               text=normalize_text("\n".join(body)),
                               timestamp=ts, raw={"kind": role}))
            body.clear()

    for speech in conv.get("speechesV2", []) or []:
        if not isinstance(speech, dict):
            continue
        for b in speech.get("content", []) or []:
            if not isinstance(b, dict):
                continue
            btype = b.get("type")
            if btype == "text":
                t = _block_text(b)
                if t:
                    body.append(t)
                continue
            flush_body()
            if btype == "searchGuid":
                docs = b.get("docs") or []
                lines = [f"[search] {b.get('title') or '联网搜索'}"]
                lines += [f"  {d.get('index')}. {d.get('title')}"
                          for d in docs if isinstance(d, dict)]
                out.append(Message(role="note",
                                   text=normalize_text("\n".join(lines)),
                                   timestamp=ts, raw={"kind": "note"}))
            elif btype in ("deepSearch", "deepSearchAgent"):
                lines = [f"[think] {b.get('title') or '思考过程'}"]
                for sub in b.get("contents", []) or []:
                    if isinstance(sub, dict):
                        st = _block_text(sub)
                        if st:
                            lines.append(st)
                if len(lines) > 1:
                    out.append(Message(role="note",
                                       text=normalize_text("\n".join(lines)),
                                       timestamp=ts, raw={"kind": "note"}))
            elif btype == "pdf":
                out.append(Message(role="note", text=normalize_text(
                    f"[file] {b.get('fileName')} ({b.get('size')} bytes)"),
                    timestamp=ts, raw={"kind": "note"}))
            elif btype == "image":
                out.append(Message(role="note", text=normalize_text(
                    f"[image] {b.get('fileName')} {b.get('url') or ''}"),
                    timestamp=ts, raw={"kind": "note"}))
            elif btype == "prompt_url_card":
                out.append(Message(role="note", text=normalize_text(
                    f"[card] {b.get('desc') or '分享卡片'} "
                    f"{b.get('coverUrl') or ''}"),
                    timestamp=ts, raw={"kind": "note"}))
            elif btype == "link_card":
                out.append(Message(role="note", text=normalize_text(
                    f"[link] {b.get('content') or '链接卡片'} "
                    f"{b.get('url') or ''} ({b.get('source') or ''})"),
                    timestamp=ts, raw={"kind": "note"}))
            elif btype == "step":
                sub = f" / {b['subMsg']}" if b.get("subMsg") else ""
                out.append(Message(role="note", text=normalize_text(
                    f"[step] {b.get('msg')}{sub} ({b.get('status')})"),
                    timestamp=ts, raw={"kind": "note"}))
            elif btype == "drawWithSearchGuid":
                # AI 出图提示词（创作内容）：prompt 优先，botPrompt 兜底。
                pr = (b.get("prompt") or b.get("botPrompt") or "").strip()
                if pr:
                    out.append(Message(
                        role="note",
                        text=normalize_text(
                            f"[draw] {b.get('title') or 'AI 出图提示词'}\n{pr}"),
                        timestamp=ts, raw={"kind": "note"}))
                else:
                    warns.append(f"drawWithSearchGuid 无 prompt/botPrompt"
                                 f"（conv id={conv.get('id')}）")
            elif btype == "doc_percent":
                # 系统通知（非用户内容）：显式成 note 并标注，不参与正文提炼。
                c = (b.get("content") or "").strip()
                if c:
                    out.append(Message(
                        role="note",
                        text=normalize_text(f"[notice] {c}"),
                        timestamp=ts, raw={"kind": "notice"}))
            elif btype in KNOWN_BLOCK_TYPES:
                # 白名单内但本版无专用分支：显式告警（而非静默落入"未知块"，
                # 二者成因不同——这里是实现缺口，不是源端新增类型）。
                warns.append(f"白名单块 type={btype!r} 尚无解析分支"
                             f"（conv id={conv.get('id')}）")
            else:
                warns.append(
                    f"未知块 type={btype!r}（conv id={conv.get('id')}），"
                    f"键: {sorted(b.keys())}")
    flush_body()
    if not out:
        warns.append(f"conv id={conv.get('id')} 无可提取内容")
    return out, warns


def _parse_detail(j: dict) -> tuple[list[Message], list[str], dict]:
    """detail JSON → (messages, warnings, meta)。"""
    warns: list[str] = []
    convs = j.get("convs")
    if not isinstance(convs, list):
        return [], [f"convs 不可用，顶层键: {sorted(j.keys())[:12]}"], {}
    convs = [c for c in convs
             if isinstance(c, dict) and not (c.get("hideConv") or c.get("skipConv"))]
    convs.sort(key=lambda c: c.get("index") or 0)
    messages: list[Message] = []
    for c in convs:
        msgs, w = _conv_blocks(c)
        messages.extend(msgs)
        warns.extend(w)
    meta = {
        "models": [m for m in dict.fromkeys(
            (j.get("chatModelId"), j.get("modelId"))) if m],
        "agent_name": j.get("agentName"),
    }
    return messages, warns, meta


class YuanbaoRawAdapter(BaseAdapter):
    id = "yuanbao-raw"
    claims_files = False  # 目录型源（detail_*.json），禁止收件箱单文件认领
    name = "腾讯元宝 API 原始采集"
    category = "网页/客户端Chat"
    candidate_paths = ("corpus/yuanbao_raw",)

    def __init__(self, paths: list[str | Path] | None = None):
        self.paths = [Path(p) for p in (paths or [])]
        self._detail_dir: Path | None = None
        self._list_meta: dict[str, dict] | None = None
        self._parse_warns: list[str] = []

    # ---- 定位 ----
    def _find_dir(self) -> Path | None:
        if self._detail_dir is not None:
            return self._detail_dir
        for base in self.paths:
            if base.is_dir() and list(base.glob("detail_*.json")):
                self._detail_dir = base
                return base
        # 默认候选：相对 cwd（index 在项目根运行时即 corpus/yuanbao_raw）
        for cand in self.candidate_paths:
            p = Path(cand)
            if p.is_dir() and list(p.glob("detail_*.json")):
                self._detail_dir = p
                return p
        return None

    def _ensure_list_meta(self) -> dict[str, dict]:
        """list_<n>.json 合并为 {会话id: 纲要}。"""
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
                for c in j.get("conversations", []) or []:
                    if isinstance(c, dict) and c.get("id"):
                        meta[c["id"]] = c
        self._list_meta = meta
        return meta

    # ---- 协议实现 ----
    def detect(self) -> DetectReport:
        d = self._find_dir()
        if d is None:
            return DetectReport(
                self.id, self.name, "STUB" if self.paths else "MISSING",
                "未找到元宝采集目录（需含 detail_*.json）", hints=HOW_TO_HARVEST)
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
            cid = p.stem[len("detail_"):]
            m = meta.get(cid, {})
            items.append({
                "session_id": cid,
                "title": m.get("title") or f"会话 {cid[:8]}",
                "created_at": _norm_time(m.get("firstRepliedAt")),
                "updated_at": _norm_time(m.get("lastRepliedAt")),
                "message_count": None,   # 纲要阶段不读 detail 正文
                "preview": (m.get("subTitle") or m.get("lastReplyContent")
                            or "").replace("\n", " ")[:80],
            })
        return items

    def load_session(self, session_id: str) -> SessionRecord:
        d = self._find_dir()
        if d is None:
            raise KeyError(f"元宝采集目录不存在: {session_id}")
        p = d / f"detail_{session_id}.json"
        if not p.is_file():
            raise KeyError(f"会话不存在: {session_id}")
        j = json.loads(p.read_text(encoding="utf-8"))
        messages, warns, meta = _parse_detail(j)
        if warns:
            self._parse_warns.extend(warns[:5])
        m = self._ensure_list_meta().get(session_id, {})
        return SessionRecord(
            source=self.id,
            session_id=session_id,
            title=j.get("title") or m.get("title") or f"会话 {session_id[:8]}",
            created_at=_norm_time(m.get("firstRepliedAt")
                                  or j.get("firstRepliedAt")),
            updated_at=_norm_time(m.get("lastRepliedAt")
                                  or j.get("lastRepliedAt")),
            messages=messages,
            extra={
                "models": meta.get("models", []),
                "agent_name": meta.get("agent_name"),
                "lossy": bool(warns),
                "warnings": warns,
            })
