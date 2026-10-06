# -*- coding: utf-8 -*-
"""分层读取（RetroLens 式 ls → read → read --turn 下钻模式）。

纲要从 outline 来（等价 ls）；read 按序号读单会话（等价 read）；
--turn N 只取第 N 个回合（等价 read --turn）——不整包倾倒，
供 agent 或人按需下钻，控制上下文占用。

回合(turn)定义：从一条 user 消息开始，到下一条 user 消息之前的全部
user/assistant/note 消息。
"""

from __future__ import annotations

from .models import SessionRecord, normalize_text


def split_turns(rec: SessionRecord) -> list[list]:
    """把会话消息切成回合列表。前置 system/note 归入第一个回合。"""
    turns: list[list] = []
    current: list = []
    seen_user = False
    for m in rec.messages:
        if m.role == "user" and seen_user:
            turns.append(current)
            current = [m]
        else:
            if m.role == "user":
                seen_user = True
            current.append(m)
    if current:
        turns.append(current)
    return turns


def render_turn(turn: list, index: int, total: int) -> str:
    """渲染单个回合。"""
    lines = [f"### Turn {index}/{total}", ""]
    for m in turn:
        ts = f" · {m.timestamp}" if m.timestamp else ""
        lines.append(f"**[{m.role}]{ts}**")
        lines.append("")
        lines.append(normalize_text(m.text).rstrip())
        lines.append("")
    return "\n".join(lines)


def render_session_header(rec: SessionRecord) -> list[str]:
    return [
        f"# {normalize_text(rec.title)}",
        "",
        f"- source: {rec.source}",
        f"- session_id: {rec.session_id}",
        f"- created_at: {rec.created_at or '-'} | updated_at: {rec.updated_at or '-'}",
        f"- turns: {len(split_turns(rec))} | messages: {len(rec.messages)}",
        "",
    ]


def render_read(rec: SessionRecord, turn: int | str) -> tuple[str, int]:
    """按 read 语义渲染。turn: 1-based 序号 / 'all' / 'last'。

    返回 (text, n_turns)。turn 越界抛 IndexError。
    """
    turns = split_turns(rec)
    if not turns:
        return "(该会话无对话消息)", 0
    if turn == "all":
        body = "\n".join(render_turn(t, i + 1, len(turns))
                         for i, t in enumerate(turns))
        return "\n".join(render_session_header(rec)) + body, len(turns)
    if turn == "last":
        idx = len(turns)
    else:
        idx = int(turn)
        if not 1 <= idx <= len(turns):
            raise IndexError(f"turn {idx} 越界: 会话共 {len(turns)} 个回合")
    text = ("\n".join(render_session_header(rec))
            + render_turn(turns[idx - 1], idx, len(turns)))
    return text, len(turns)
