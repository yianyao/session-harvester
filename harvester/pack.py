# -*- coding: utf-8 -*-
"""跨 agent 上下文交接包（ai-hist `pack` 命令的移植）。

`harvester pack --select "3,5-9" --tokens 2000` 产出一个 token 预算内的
Markdown 上下文包：每会话 = 标题 + 元信息 + 预算内截取的对话回合。
用途：把"这个话题我们聊过什么"塞给另一个 agent（或 teammate）接续工作，
不需要它重新检索全部历史。

token 估算（零依赖启发式，误差 ±30% 内够用）：
  CJK 字符 ≈ 1 token/字；ASCII ≈ 4 字符/token（参考各类 tokenizer 经验值）。
预算分配：预算按会话均分；会话内按回合顺序填充，放不下的回合截断并标注。
"""

from __future__ import annotations

from pathlib import Path

from .models import SessionRecord, normalize_text
from .reader import split_turns


def est_tokens(text: str) -> int:
    """粗略 token 估算：CJK 计 1/字，其余计 1/4 字符。"""
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff"
              or "\u3000" <= c <= "\u303f")
    return cjk + (len(text) - cjk) // 4 + 1


def _render_pack_session(rec: SessionRecord, budget: int) -> tuple[str, int]:
    """渲染单会话段落，遵守 token 预算。返回 (text, used)。"""
    turns = split_turns(rec)
    header = (
        f"## {normalize_text(rec.title)}\n\n"
        f"- source: {rec.source} | date: {(rec.created_at or '')[:10]}\n"
        f"- session_id: {rec.session_id} | turns: {len(turns)}\n\n"
    )
    used = est_tokens(header)
    body: list[str] = []
    truncated = False
    for i, t in enumerate(turns):
        seg = "\n".join(
            f"[{m.role}] {normalize_text(m.text).strip()}" for m in t)
        seg_tokens = est_tokens(seg) + 4
        if used + seg_tokens > budget:
            remaining = budget - used
            if remaining > 40:  # 还有余量则截断本回合塞进去
                seg = seg[: remaining * 4]
                body.append(seg + "\n[…截断]")
                used = budget
            truncated = True
            break
        body.append(f"#### Turn {i + 1}\n{seg}\n")
        used += seg_tokens
    note = ""
    if truncated:
        note = (f"\n> [!] 预算内仅含前 {len(body)}/{len(turns)} 个回合，"
                f"完整内容: harvester read <序号> --turn all\n")
    return header + "\n".join(body) + note, used


def build_pack(recs: list[tuple[SessionRecord, int]],  # (record, outline_no)
               total_budget: int = 2000,
               question: str | None = None) -> str:
    """构建交接包。recs: [(record, 纲要序号), ...]。"""
    lines = [
        "# 上下文交接包（session-harvester pack）",
        "",
        f"- 会话数: {len(recs)} | 预算: ~{total_budget} tokens",
        "- 用途: 供另一个 agent/协作者接续工作；各会话为节选，",
        "  完整内容用 `harvester read <序号>` 获取。",
        "",
    ]
    per = max(200, total_budget // max(1, len(recs)))
    used_total = 0
    for rec, no in recs:
        seg, used = _render_pack_session(rec, per)
        lines.append(f"---\n\n### [{no}] 会话（预算 {per}）\n")
        lines.append(seg)
        used_total += used
    if question:
        lines.append("---\n\n## 接续任务\n")
        lines.append(normalize_text(question).rstrip())
        lines.append("")
    lines.insert(2, f"- 实际占用: ~{used_total} tokens\n")
    return "\n".join(lines)


def write_pack(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    return path
