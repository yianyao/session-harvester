# -*- coding: utf-8 -*-
"""DeepSeek 官方导出文件 Adapter。

数据来源：DeepSeek 的「数据导出」流程（网页版 设置 → 数据/隐私 → 导出，
或邮件申请），产出 ZIP/JSON 会话档案。此路径无需账号密码、无需 token、
无风控——完全符合本项目"不硬猜"铁律。

格式核验状态：
A.（2026-10-05 社区解析器交叉核验）顶层数组 [{id, title, create_time, messages:[...]}]
  或 {"conversations": [...]}；消息 content 存在 str / {parts:[...]} / [parts] 三变体。
B.（2026-10-06 真机核验，官方导出 ZIP：user.json + conversations.json，316 会话）
  顶层数组 [{id, title, inserted_at, updated_at(ISO+08:00), mapping}]——无 messages 键；
  mapping 为节点字典 {node_id: {id, parent, children[], message}}，root 节点 message=null；
  message: {model, inserted_at, fragments[]}，无 role 字段，role 由 fragment type 推断：
    REQUEST→user / RESPONSE→assistant / THINK→思考过程(note)
    FILE→用户上传文件 {files:[{file_id,file_name,file_size}]}(note)
    SEARCH|TOOL_SEARCH→联网搜索结果 {results:[{url,title}]}(note)
    TOOL_OPEN→打开网页动作(仅 type 键)(note)
  分支：无 current_node 指针；实测 63/316 会话含分支（重新生成/改写重问）；
  主链选择 = 每个分支点取"子树最新消息时间"最大的 child（即用户当前看到的最新活动链），
  分支点计数记入 extra["branch_points"]，模型集合记入 extra["models"]。

因此本 Adapter 做防御式双形态解析：
- 只接受上述已核验形态，其余抛 ValueError（detect 降级 STUB）；
- 消息逐条解析，无法识别的条目跳过并记入 extra["warnings"]（lossy 不抛异常）；
- mapping 内未知 fragment type 跳过并警告，绝不臆测解析。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..models import Message, SessionRecord, normalize_text
from .base import BaseAdapter, DetectReport
# 文件定位 / ZIP 读取 / 顶层形态校验 / 会话 id 提取与 official_export 同构，直接复用
from .official_export import (
    conv_id,
    extract_conversations,
    load_export_json,
    locate_export_file,
)

#: 获取导出文件的可行动指引（detect 的 STUB hints）
HOW_TO_EXPORT = [
    "获取导出文件: DeepSeek 网页版 → 设置 → 数据/隐私 → 导出数据（或邮件 service@deepseek.com 申请）",
    "拿到 ZIP 解压或直接指定 conversations.json 路径",
    "在 sources.json 中配置: {\"deepseek-export\": {\"paths\": [\"C:/path/to/conversations.json\"]}}",
    "或直接使用: python -m harvester index --deepseek-file <路径>",
]


def _norm_time(v) -> str | None:
    """时间戳归一化：容忍 epoch(ms/s) 与 ISO 字符串。输出本地时区 ISO。"""
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


def _content_text(content) -> tuple[str, list[str]]:
    """消息 content 提取。返回 (text, warnings)。

    已核验变体：str / {parts:[...]} / [part,...]（part 为 str 或 {text:...}）。
    """
    warns: list[str] = []
    if content is None:
        return "", warns
    if isinstance(content, str):
        return normalize_text(content), warns
    if isinstance(content, dict):
        parts = content.get("parts")
        if isinstance(parts, list):
            return _join_parts(parts), warns
        if isinstance(content.get("text"), str):
            return normalize_text(content["text"]), warns
        warns.append(f"未识别的 content 对象键: {sorted(content.keys())}")
        return "", warns
    if isinstance(content, list):
        return _join_parts(content), warns
    warns.append(f"未识别的 content 类型: {type(content).__name__}")
    return "", warns


def _join_parts(parts: list) -> str:
    out = []
    for p in parts:
        if isinstance(p, str) and p:
            out.append(p)
        elif isinstance(p, dict) and isinstance(p.get("text"), str):
            out.append(p["text"])
    return normalize_text("\n".join(out))


def _ds_walk(conv: dict) -> tuple[list[Message], list[str], bool, dict]:
    """官方导出 mapping/fragments 形态（真机核验 2026-10-06）。

    返回 (messages, warnings, lossy, meta)。
    meta: {"models": [...], "branch_points": int}。
    """
    warns: list[str] = []
    meta: dict = {"models": [], "branch_points": 0}
    mapping = conv.get("mapping")
    if not isinstance(mapping, dict) or "root" not in mapping:
        return [], [f"mapping 不可用，会话键: {sorted(conv.keys())}"], True, meta

    def node(nid):
        v = mapping.get(nid)
        return v if isinstance(v, dict) else None

    def children(nid) -> list[str]:
        n = node(nid)
        ch = n.get("children") if n else None
        if not isinstance(ch, list):
            return []
        return [c for c in ch if isinstance(c, str) and c in mapping]

    def msg(nid):
        n = node(nid)
        m = n.get("message") if n else None
        return m if isinstance(m, dict) else None

    # 子树最新消息时间（ISO 字符串字典序即可比较），用于分支点选主链
    latest: dict[str, str] = {}

    def subtree_latest(nid: str, seen: frozenset = frozenset()) -> str:
        if nid in latest:
            return latest[nid]
        if nid in seen:          # 脏数据环兜底，绝不无限递归
            return ""
        m = msg(nid)
        best = str(m.get("inserted_at") or "") if m else ""
        for ch in children(nid):
            t = subtree_latest(ch, seen | {nid})
            if t > best:
                best = t
        latest[nid] = best
        return best

    def sort_key(c: str):
        # 主排序=子树最新时间；并列时数字 id 大者视为更晚（重新生成后追加）
        try:
            return (subtree_latest(c), 0, int(c))
        except ValueError:
            return (subtree_latest(c), 1, 0)

    # root→leaf 走最新活动链（root 自身无消息，不入 thread）
    thread: list[str] = []
    cur, visited = "root", {"root"}
    while True:
        chs = children(cur)
        if len(chs) > 1:
            meta["branch_points"] += 1
            chs = sorted(chs, key=sort_key)
        if not chs:
            break
        nxt = chs[-1]
        if nxt in visited:
            warns.append(f"分支环检测于节点 {nxt}，截断")
            break
        visited.add(nxt)
        thread.append(nxt)
        cur = nxt

    messages: list[Message] = []
    models: list[str] = []
    for i, nid in enumerate(thread):
        m = msg(nid)
        if m is None:
            continue  # 结构节点（root）
        model = m.get("model")
        if isinstance(model, str) and model and model not in models:
            models.append(model)
        ts = _norm_time(m.get("inserted_at"))
        frags = m.get("fragments")
        if not isinstance(frags, list):
            warns.append(f"节点[{i}] fragments 不是数组，已跳过")
            continue
        for j, f in enumerate(frags):
            if not isinstance(f, dict):
                warns.append(f"节点[{i}] fragment[{j}] 不是对象，已跳过")
                continue
            t = f.get("type")
            if t == "REQUEST":
                text = normalize_text(str(f.get("content") or ""))
                if text:
                    messages.append(Message(role="user", text=text,
                                            timestamp=ts, raw=m))
            elif t == "RESPONSE":
                text = normalize_text(str(f.get("content") or ""))
                if text:
                    messages.append(Message(role="assistant", text=text,
                                            timestamp=ts, raw=m))
            elif t == "THINK":
                text = normalize_text(str(f.get("content") or ""))
                if text:
                    messages.append(Message(role="note",
                                            text=f"[think]\n{text}",
                                            timestamp=None, raw=m))
            elif t == "FILE":
                lines = []
                files = f.get("files")
                if isinstance(files, list):
                    for x in files:
                        if isinstance(x, dict):
                            lines.append(f"- {x.get('file_name')}"
                                         f" ({x.get('file_size')} bytes)")
                if lines:
                    messages.append(Message(role="note",
                                            text="[FILE]\n" + "\n".join(lines),
                                            timestamp=None, raw=m))
            elif t in ("SEARCH", "TOOL_SEARCH"):
                lines = []
                results = f.get("results")
                if isinstance(results, list):
                    for x in results:
                        if isinstance(x, dict):
                            lines.append(f"- {x.get('title')} — {x.get('url')}")
                if lines:
                    messages.append(Message(
                        role="note",
                        text=f"[{t}] {len(lines)} 条结果\n" + "\n".join(lines),
                        timestamp=ts, raw=m))
            elif t == "TOOL_OPEN":
                messages.append(Message(role="note",
                                        text="[TOOL_OPEN] 打开网页",
                                        timestamp=ts, raw=m))
            else:
                warns.append(f"节点[{i}] fragment[{j}] 未核验 type={t!r}，已跳过")
    meta["models"] = models
    return messages, warns, False, meta


def _conv_content(conv: dict) -> tuple[list[Message], list[str], bool, dict]:
    """统一分发：形态 B（mapping）优先，形态 A（messages 数组）其次。

    返回 (messages, warnings, lossy, meta)；形态 A 的 meta 为空 dict。
    """
    if isinstance(conv.get("mapping"), dict):
        return _ds_walk(conv)
    msgs, warns, lossy = _iter_conv_messages(conv)
    return msgs, warns, lossy, {}


def _iter_conv_messages(conv: dict) -> tuple[list[Message], list[str], bool]:
    """从单个会话对象提取消息列表。返回 (messages, warnings, lossy)。

    已核验形态 A: conv["messages"] 为消息数组（社区描述形态）。
    未核验形态: 返回空列表 + 警告，绝不臆测。
    """
    warns: list[str] = []
    if "messages" not in conv:
        return [], [f"会话缺少 messages 键，现有键: {sorted(conv.keys())}"], True
    msgs_raw = conv["messages"]
    if not isinstance(msgs_raw, list):
        return [], ["messages 不是数组"], True
    messages: list[Message] = []
    for i, m in enumerate(msgs_raw):
        if not isinstance(m, dict):
            warns.append(f"消息[{i}] 不是对象，已跳过")
            continue
        role = str(m.get("role") or "").lower()
        if role == "user":
            role = "user"
        elif role in ("assistant", "ai", "bot"):
            role = "assistant"
        elif role in ("system",):
            role = "system"
        else:
            warns.append(f"消息[{i}] 未识别 role={m.get('role')!r}，已跳过")
            continue
        text, w = _content_text(m.get("content"))
        for x in w:
            warns.append(f"消息[{i}] {x}")
        if text:
            messages.append(Message(role=role, text=text,
                                    timestamp=_norm_time(m.get("create_time")
                                                         or m.get("timestamp"))))
        # 思考过程（DeepSeek API 文档字段 reasoning_content；仅键存在时提取）
        for rk in ("reasoning_content", "thinking_content"):
            if isinstance(m.get(rk), str) and m[rk].strip():
                messages.append(Message(
                    role="note",
                    text=f"[{rk}]\n{normalize_text(m[rk]).rstrip()}",
                    timestamp=None))
                break
    return messages, warns, False


class DeepSeekExportAdapter(BaseAdapter):
    id = "deepseek-export"
    name = "DeepSeek 官方导出文件"
    category = "网页/客户端Chat"

    def __init__(self, paths: list[str | Path] | None = None):
        self._candidates = [Path(p) for p in (paths or [])]
        self._convs: list[dict] | None = None
        self._parse_warns: list[str] = []

    def _ensure_parsed(self) -> list[dict]:
        if self._convs is not None:
            return self._convs
        self._convs, self._parse_warns = [], []
        f, member = locate_export_file(self._candidates)
        if f is None:
            return self._convs
        data, warns = load_export_json(f, member, self._read_text)
        self._parse_warns.extend(warns)
        convs, w2 = extract_conversations(data)
        self._parse_warns.extend(w2)
        for i, c in enumerate(convs):
            if not isinstance(c, dict):
                self._parse_warns.append(f"会话[{i}] 不是对象，已跳过")
        self._convs = [c for c in convs if isinstance(c, dict)]
        return self._convs

    # ---- 协议实现 ----
    def detect(self) -> DetectReport:
        f = locate_export_file(self._candidates)[0]
        if f is None:
            return DetectReport(
                self.id, self.name, "STUB",
                "未配置导出文件路径。此 Adapter 消费 DeepSeek 官方数据导出，"
                "无需账号密码与浏览器自动化。",
                hints=HOW_TO_EXPORT)
        try:
            convs = self._ensure_parsed()
        except (ValueError, OSError) as e:
            # 契约：detect 不抛异常——JSON 结构无法验证或文件读取失败均降级 STUB
            return DetectReport(self.id, self.name, "STUB",
                                f"导出文件存在但结构无法验证: {e}",
                                hints=["将文件样例（前 100 行）提供给维护者以补全解析"])
        bad = self._parse_warns
        if not convs and bad:
            return DetectReport(self.id, self.name, "STUB",
                                f"文件结构不匹配已知形态: {'; '.join(bad[:2])}",
                                hints=["将文件样例（前 100 行）提供给维护者以补全解析"])
        detail = f"导出文件: {f}"
        if self._parse_warns:
            detail += f"（{len(self._parse_warns)} 条解析警告）"
        return DetectReport(self.id, self.name, "OK", detail,
                            session_count=len(convs))

    def list_sessions(self) -> list[dict]:
        convs = self._ensure_parsed()
        items = []
        for i, conv in enumerate(convs):
            cid = conv_id(conv, str(i), self._parse_warns)
            if cid is None:
                continue
            messages, _w, _lossy, _meta = _conv_content(conv)
            n = sum(1 for m in messages if m.role in ("user", "assistant"))
            preview = next((m.text.replace("\n", " ")[:80]
                            for m in messages if m.role == "user" and m.text), "")
            items.append({
                "session_id": cid,
                "title": conv.get("title") or f"会话 {str(cid)[:8]}",
                "created_at": _norm_time(conv.get("inserted_at")
                                         or conv.get("create_time")
                                         or conv.get("created_at")),
                "updated_at": _norm_time(conv.get("updated_at")
                                         or conv.get("update_time")),
                "message_count": n, "preview": preview,
            })
        return items

    def load_session(self, session_id: str) -> SessionRecord:
        convs = self._ensure_parsed()
        for i, conv in enumerate(convs):
            if conv_id(conv, str(i), []) == session_id:
                messages, warns, structural_lossy, meta = _conv_content(conv)
                if structural_lossy:
                    warns.insert(0, "会话结构未通过已核验形态校验，消息为空")
                extra = {
                    "model": conv.get("model")
                    or (meta.get("models") or [None])[0],
                    "lossy": bool(warns),
                    "warnings": warns,
                }
                if meta:
                    # 仅 mapping 形态携带分支/多模型信息，legacy 形态保持旧键形
                    extra["models"] = meta.get("models") or []
                    extra["branch_points"] = meta.get("branch_points", 0)
                return SessionRecord(
                    source=self.id, session_id=session_id,
                    title=conv.get("title") or f"会话 {str(session_id)[:8]}",
                    created_at=_norm_time(conv.get("inserted_at")
                                          or conv.get("create_time")
                                          or conv.get("created_at")),
                    updated_at=_norm_time(conv.get("updated_at")
                                          or conv.get("update_time")),
                    messages=messages,
                    extra=extra)
        raise KeyError(f"会话不存在: {session_id}")
