# -*- coding: utf-8 -*-
"""统一数据模型：所有 Adapter 的输出都必须收敛到这两个结构。

字段约束：
- 时间戳一律 ISO 8601 字符串（含时区或 UTC 标记均可，导出时只取 YYYY-MM）。
- text 为纯文本；结构化原文保留在 raw（dict）中，可为 None。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


def normalize_text(s: str | None) -> str:
    """输出统一 LF；源数据可能携带 CRLF。所有模块共用此实现。"""
    return (s or "").replace("\r\n", "\n")


_EPOCH_SEC_RE = re.compile(r"\d{10}(\.\d+)?$")
_EPOCH_MS_RE = re.compile(r"\d{13}$")


def to_local_ts(v) -> "str | None":
    """时间戳统一到本地时区，输出 '%Y-%m-%d %H:%M:%S'。

    纲要按日期分目录，若各源混用 UTC/本地口径，同一天的会话会被分到
    两个目录（差一天）。规则：
    - epoch 秒/毫秒（int/float/纯数字串）→ 按本地时区渲染；
    - ISO 8601 带 Z 或偏移 → 换算到本地时区；
    - ISO 8601 无时区 → 视为本地时间（不硬猜来源时区），仅统一格式；
    - 其余（无法解析）→ 原样返回，交由上层兜底。
    """
    if v is None:
        return None
    if isinstance(v, (int, float)):
        if v > 1e11:  # epoch 毫秒
            v = v / 1000.0
        return datetime.fromtimestamp(v).strftime("%Y-%m-%d %H:%M:%S")
    s = str(v).strip()
    if not s:
        return None
    if _EPOCH_MS_RE.fullmatch(s):
        return datetime.fromtimestamp(int(s) / 1000).strftime("%Y-%m-%d %H:%M:%S")
    if _EPOCH_SEC_RE.fullmatch(s):
        return datetime.fromtimestamp(float(s)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return s  # 无法解析：不硬猜，原样透传
    if dt.tzinfo is None:
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class Message:
    """单条消息。

    role 取值约定（跨 Adapter 统一）:
      user / assistant / system / note
    note 用于工具调用、附件等非对话内容，导出时可按需过滤。
    """

    role: str
    text: str
    timestamp: str | None = None
    raw: dict[str, Any] | None = None


@dataclass
class SessionRecord:
    """一个完整会话。"""

    source: str              # adapter id，如 "autoclaw"
    session_id: str          # 源内唯一 ID
    title: str               # 会话标题/概要
    created_at: str | None = None
    updated_at: str | None = None
    messages: list[Message] = field(default_factory=list)
    # 源端附加信息（cwd、模型名等），导出时写入 frontmatter
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        # 标题可能取自消息正文（如 Copilot 首条消息），统一归一化为 LF
        self.title = normalize_text(self.title)

    @property
    def message_count(self) -> int:
        return sum(1 for m in self.messages if m.role in ("user", "assistant"))

    @property
    def preview(self) -> str:
        """纲要用的一句话概要：优先第一条 user 消息前 80 字。"""
        for m in self.messages:
            if m.role == "user" and m.text:
                return " ".join(m.text.split())[:80]
        return " ".join((self.title or "").split())[:80]
