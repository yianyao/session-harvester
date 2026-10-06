# -*- coding: utf-8 -*-
"""官方数据导出文件 Adapter（ChatGPT / Claude）。

战略定位（2026-10-05 用户确认）：纯服务端产品首选官方导出通道——
无账号密码、无 token、无浏览器自动化、零风控；weblogin 三级流程仅作兜底。

格式核验状态（2026-10-05，多源交叉核验一致）：

ChatGPT（Settings → Data controls → Export data，邮件发 ZIP）：
- conversations.json 为顶层数组，每元素一个会话；
- 会话字段：title / create_time / update_time（Unix 秒，float，可空）/
  conversation_id（或 id）/ mapping（DAG）/ current_node（当前叶子节点）；
- mapping 为节点字典：{node_id: {id, message(可 null), parent, children}}；
- message：{author:{role: user|assistant|system|tool},
  content:{content_type: 'text'(parts:[str]) | 'code'(text) |
  'multimodal_text'(parts 混合)}, create_time, metadata}；
- 重构：从 current_node 沿 parent 回溯到根再反转——这是用户实际看到的
  分支；mapping 遍历会混入已废弃分支（社区解析器最常见错误）；
- 根节点 message 为 null；system 角色为隐藏上下文，跳过；
  metadata.is_visually_hidden_from_conversation 的节点跳过。

Claude（Settings → Privacy/Account → Export data，邮件发 ZIP）：
- conversations.json 为顶层数组（无分支 DAG，线性数组即渲染顺序）；
- 会话字段：uuid / name / created_at / updated_at（ISO 8601）/ model /
  chat_messages；
- 消息：{sender: 'human'|'assistant', text(str), content:[{type,text}],
  created_at}。

两家的顶层结构均只接受"数组"或 {"conversations": [...]} 两种已核验形态；
其余形态抛 ValueError → detect 降级 STUB，绝不臆测解析。
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from ..models import Message, SessionRecord, normalize_text, to_local_ts
from .base import BaseAdapter, DetectReport

EXPORT_FILENAME = "conversations.json"

HOW_TO_EXPORT_COMMON = [
    "拿到 ZIP 后可直接指定 conversations.json 路径、ZIP 路径或解压目录",
    '在 sources.json 配置: {"<adapter-id>": {"paths": ["C:/path/to/conversations.json"]}}',
]


def locate_export_file(candidates: list[Path],
                       filename: str = EXPORT_FILENAME) -> tuple[Path | None, str | None]:
    """在候选路径中定位导出 JSON（支持目录 / zip / 直接文件）。

    返回 (文件路径或 None, zip 内成员名或 None)。
    """
    for p in candidates:
        if not p.exists():
            continue
        if p.is_dir():
            f = p / filename
            if f.is_file():
                return f, None
        elif p.suffix.lower() == ".zip":
            try:
                with zipfile.ZipFile(p) as z:
                    for n in z.namelist():
                        if n.endswith(filename):
                            return p, n
            except zipfile.BadZipFile:
                continue
        elif p.is_file():
            return p, None
    return None, None


def load_export_json(file: Path, zip_member: str | None,
                     read_text) -> tuple[object, list[str]]:
    """读取导出 JSON（兼容 zip 内成员）。返回 (data, warnings)。"""
    warns: list[str] = []
    if zip_member:
        with zipfile.ZipFile(file) as z:
            raw = z.read(zip_member).decode("utf-8", errors="replace")
        warns.append(f"从 ZIP 成员读取: {zip_member}")
    else:
        raw = read_text(file)
    try:
        return json.loads(raw), warns
    except json.JSONDecodeError as e:
        raise ValueError(f"导出文件不是合法 JSON: {e}") from e


def extract_conversations(data) -> tuple[list[dict], list[str]]:
    """提取会话数组。仅接受两种已核验顶层形态，其余拒绝。"""
    if isinstance(data, list):
        return data, []
    if isinstance(data, dict):
        convs = data.get("conversations")
        if isinstance(convs, list):
            return convs, []
        return [], [f"顶层对象无 conversations 数组，现有键: {sorted(data.keys())}"]
    return [], [f"顶层类型 {type(data).__name__} 无法识别"]


def conv_id(conv: dict, fallback: str, warns: list[str]) -> str | None:
    for k in ("uuid", "conversation_id", "id"):
        v = conv.get(k)
        if isinstance(v, (str, int)) and str(v).strip():
            return str(v)
    warns.append(f"会话[{fallback}] 缺少 id 键，已跳过")
    return None


# ================= ChatGPT =================

CHATGPT_HINTS = HOW_TO_EXPORT_COMMON + [
    "获取导出: ChatGPT 网页/桌面端 → Settings → Data controls → Export data",
]


def _chatgpt_content_text(content: dict, warns: list[str], ctx: str) -> str:
    """按 content_type 提取正文。未核验类型跳过并警告。"""
    if not isinstance(content, dict):
        return ""
    ct = content.get("content_type")
    parts = content.get("parts")
    if ct == "text" and isinstance(parts, list):
        return normalize_text("\n".join(p for p in parts
                                        if isinstance(p, str) and p))
    if ct == "code" and isinstance(content.get("text"), str):
        return normalize_text(content["text"])
    if ct == "multimodal_text" and isinstance(parts, list):
        # parts 混合 str 与资源指针对象：只取 str（图片指针留在 raw）
        return normalize_text("\n".join(p for p in parts
                                        if isinstance(p, str) and p))
    warns.append(f"{ctx} 未核验的 content_type={ct!r}，已跳过")
    return ""


def _chatgpt_walk(conv: dict) -> tuple[list[Message], list[str], bool]:
    """从 current_node 沿 parent 回溯重构可见线程（leaf→root，调用方反转）。

    返回 (messages_leaf_to_root, warnings, structural_lossy)。
    """
    warns: list[str] = []
    mapping = conv.get("mapping")
    if not isinstance(mapping, dict) or not conv.get("current_node"):
        return [], ([f"会话缺少 mapping/current_node，现有键: {sorted(conv.keys())}"]
                    if not isinstance(mapping, dict) else ["current_node 缺失"]), True
    msgs: list[Message] = []
    node_id = conv["current_node"]
    visited: set[str] = set()
    while node_id and node_id in mapping and node_id not in visited:
        visited.add(node_id)
        node = mapping[node_id]
        node_id = node.get("parent") if isinstance(node, dict) else None
        msg = node.get("message") if isinstance(node, dict) else None
        if not isinstance(msg, dict):
            continue  # 根节点/结构节点：message 为 null
        meta = msg.get("metadata") or {}
        if isinstance(meta, dict) and meta.get("is_visually_hidden_from_conversation"):
            continue
        author = msg.get("author") or {}
        role = author.get("role") if isinstance(author, dict) else None
        if role == "system":
            continue  # 隐藏上下文，跳过
        text = _chatgpt_content_text(msg.get("content") or {}, warns,
                                     f"node[{len(visited)}]")
        if not text:
            continue
        ts = to_local_ts(msg.get("create_time"))
        if role in ("user", "assistant"):
            msgs.append(Message(role=role, text=text, timestamp=ts, raw=msg))
        elif role == "tool":
            msgs.append(Message(role="note", text=f"[tool]\n{text}",
                                timestamp=ts, raw=msg))
        else:
            warns.append(f"未核验 role={role!r}，已跳过")
    return msgs, warns, False


class ChatGptExportAdapter(BaseAdapter):
    id = "chatgpt-export"
    name = "ChatGPT 官方导出文件"
    category = "网页/客户端Chat"

    def __init__(self, paths: list[str | Path] | None = None):
        self._candidates = [Path(p) for p in (paths or [])]
        self._file: Path | None = None
        self._zip_member: str | None = None
        self._convs: list[dict] | None = None
        self._parse_warns: list[str] = []

    def _ensure_parsed(self) -> list[dict]:
        if self._convs is not None:
            return self._convs
        self._convs, self._parse_warns = [], []
        f, member = locate_export_file(self._candidates)
        if f is None:
            return self._convs
        self._file, self._zip_member = f, member
        data, warns = load_export_json(f, member, self._read_text)
        self._parse_warns.extend(warns)
        convs, w2 = extract_conversations(data)
        self._parse_warns.extend(w2)
        self._convs = [c for c in convs if isinstance(c, dict)]
        return self._convs

    def detect(self) -> DetectReport:
        f, _ = locate_export_file(self._candidates)
        if f is None:
            return DetectReport(
                self.id, self.name, "STUB",
                "未配置导出文件路径。此 Adapter 消费 ChatGPT 官方数据导出，"
                "无需账号密码与浏览器自动化。",
                hints=CHATGPT_HINTS)
        try:
            convs = self._ensure_parsed()
        except ValueError as e:
            return DetectReport(self.id, self.name, "STUB",
                                f"导出文件存在但结构无法验证: {e}",
                                hints=["将文件样例（前 100 行）提供给维护者以补全解析"])
        if not convs and self._parse_warns:
            return DetectReport(self.id, self.name, "STUB",
                                f"文件结构不匹配已知形态: {'; '.join(self._parse_warns[:2])}",
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
            items.append({
                "session_id": cid,
                "title": conv.get("title") or f"会话 {str(cid)[:8]}",
                "created_at": to_local_ts(conv.get("create_time")),
                "updated_at": to_local_ts(conv.get("update_time")),
                "message_count": None, "preview": "",
            })
        return items

    def load_session(self, session_id: str) -> SessionRecord:
        convs = self._ensure_parsed()
        for i, conv in enumerate(convs):
            if conv_id(conv, str(i), []) != session_id:
                continue
            msgs_leaf_root, warns, lossy = _chatgpt_walk(conv)
            if lossy:
                warns.insert(0, "会话结构未通过已核验形态校验，消息为空")
            return SessionRecord(
                source=self.id, session_id=session_id,
                title=conv.get("title") or f"会话 {str(session_id)[:8]}",
                created_at=to_local_ts(conv.get("create_time")),
                updated_at=to_local_ts(conv.get("update_time")),
                messages=list(reversed(msgs_leaf_root)),
                extra={"lossy": bool(warns), "warnings": warns,
                       "model": (conv.get("default_model_slug"))},
            )
        raise KeyError(f"ChatGPT 导出中不存在会话: {session_id}")


# ================= Claude =================

CLAUDE_HINTS = HOW_TO_EXPORT_COMMON + [
    "获取导出: claude.ai → 设置 → Privacy → Export data（ZIP 链接发至邮箱，7 天有效）",
]


def _claude_messages(conv: dict) -> tuple[list[Message], list[str], bool]:
    """chat_messages 线性数组即渲染顺序，直接迭代。"""
    warns: list[str] = []
    chat = conv.get("chat_messages")
    if not isinstance(chat, list):
        return [], [f"会话缺少 chat_messages 数组，现有键: {sorted(conv.keys())}"], True
    msgs: list[Message] = []
    for i, m in enumerate(chat):
        if not isinstance(m, dict):
            warns.append(f"消息[{i}] 不是对象，已跳过")
            continue
        sender = m.get("sender")
        if sender == "human":
            role = "user"
        elif sender == "assistant":
            role = "assistant"
        else:
            warns.append(f"消息[{i}] 未核验 sender={sender!r}，已跳过")
            continue
        text = m.get("text")
        if not (isinstance(text, str) and text.strip()):
            content = m.get("content")
            if isinstance(content, list):
                text = normalize_text("\n".join(
                    b.get("text") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                    and isinstance(b.get("text"), str)))
            else:
                text = ""
        if not text.strip():
            continue
        msgs.append(Message(role=role, text=normalize_text(text),
                            timestamp=to_local_ts(m.get("created_at")),
                            raw=m))
    return msgs, warns, False


class ClaudeExportAdapter(BaseAdapter):
    id = "claude-export"
    name = "Claude 官方导出文件"
    category = "网页/客户端Chat"

    def __init__(self, paths: list[str | Path] | None = None):
        self._candidates = [Path(p) for p in (paths or [])]
        self._file: Path | None = None
        self._zip_member: str | None = None
        self._convs: list[dict] | None = None
        self._parse_warns: list[str] = []

    def _ensure_parsed(self) -> list[dict]:
        if self._convs is not None:
            return self._convs
        self._convs, self._parse_warns = [], []
        f, member = locate_export_file(self._candidates)
        if f is None:
            return self._convs
        self._file, self._zip_member = f, member
        data, warns = load_export_json(f, member, self._read_text)
        self._parse_warns.extend(warns)
        convs, w2 = extract_conversations(data)
        self._parse_warns.extend(w2)
        self._convs = [c for c in convs if isinstance(c, dict)]
        return self._convs

    def detect(self) -> DetectReport:
        f, _ = locate_export_file(self._candidates)
        if f is None:
            return DetectReport(
                self.id, self.name, "STUB",
                "未配置导出文件路径。此 Adapter 消费 Claude 官方数据导出，"
                "无需账号密码与浏览器自动化。",
                hints=CLAUDE_HINTS)
        try:
            convs = self._ensure_parsed()
        except ValueError as e:
            return DetectReport(self.id, self.name, "STUB",
                                f"导出文件存在但结构无法验证: {e}",
                                hints=["将文件样例（前 100 行）提供给维护者以补全解析"])
        if not convs and self._parse_warns:
            return DetectReport(self.id, self.name, "STUB",
                                f"文件结构不匹配已知形态: {'; '.join(self._parse_warns[:2])}",
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
            preview = ""
            chat = conv.get("chat_messages")
            if isinstance(chat, list):
                for m in chat:
                    if isinstance(m, dict) and m.get("sender") == "human":
                        t = m.get("text") or ""
                        if t.strip():
                            preview = t.replace("\n", " ")[:80]
                            break
            items.append({
                "session_id": cid,
                "title": conv.get("name") or f"会话 {str(cid)[:8]}",
                "created_at": to_local_ts(conv.get("created_at")),
                "updated_at": to_local_ts(conv.get("updated_at")),
                "message_count": None, "preview": preview,
            })
        return items

    def load_session(self, session_id: str) -> SessionRecord:
        convs = self._ensure_parsed()
        for i, conv in enumerate(convs):
            if conv_id(conv, str(i), []) != session_id:
                continue
            msgs, warns, lossy = _claude_messages(conv)
            if lossy:
                warns.insert(0, "会话结构未通过已核验形态校验，消息为空")
            return SessionRecord(
                source=self.id, session_id=session_id,
                title=conv.get("name") or f"会话 {str(session_id)[:8]}",
                created_at=to_local_ts(conv.get("created_at")),
                updated_at=to_local_ts(conv.get("updated_at")),
                messages=msgs,
                extra={"lossy": bool(warns), "warnings": warns,
                       "model": conv.get("model")},
            )
        raise KeyError(f"Claude 导出中不存在会话: {session_id}")
