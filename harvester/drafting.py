# -*- coding: utf-8 -*-
"""draft 蒸馏包：T2 草稿卡的确定性准备层。

定位（v0.14，2026-10-06）：流水线 T0 sync → T1 triage → **T2 草稿** →
T3 人工终审。T2 分两半：

- 本模块 = 确定性一半：把"会话原文 + 卡片规范 + 指令"打包成自包含
  markdown 蒸馏包，任何 Agent（WorkBuddy / DSH / 人）拿到即可开工，
  不必再手工拼上下文。纯标准库，不内置 LLM 调用（套件零依赖、可离线
  复跑的定位不破；草稿质量取决于喂的料，蒸馏包就是"喂料"的固化形态）。
- Agent 一半：读包产草稿卡，落 `cards_pending/`（confidence 0.3），
  `cards validate` 照跑——草稿永远不直接并入主库（纪律与
  suggest-agents 不自动改 AGENTS.md 同源）。
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from .cards import REQUIRED_TYPES, _TYPE_HEADINGS

_MAX_CHARS = 24000   # 会话原文默认预算（约 6-8k tokens）

_CARD_SPEC = """\
frontmatter 必填字段（§8 冻结规范，全部必填）：
- id: 文件名去 .md（kc-YYYYMMDD-NNNN-<slug>）
- title / type / tags: type 取 {types}；tags 自拟
- anchors: [{{session_id: "<下面会话的 session_id>", turn: <回合序号或 null>}}]
- evidence: |- 从会话原文**逐字**摘录的证据（不得改写、不得编造）
- confidence: 0.3（草稿固定值）
- created: 今日日期
正文骨架（type 对应）：
- pitfall: "## 现象" + "## 做法"
- workflow: "## 适用场景" + "## 步骤"
- insight: "## 结论" + "## 依据"
"""


def _fetch_session(db: Path, sid_query: str) -> sqlite3.Row:
    """取会话行（优先精确 sid，其次 session_id）。"""
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT sid, source, session_id, title FROM sessions "
            "WHERE sid=? ORDER BY 1 LIMIT 1", (sid_query,)).fetchone()
        if row is None:
            row = con.execute(
                "SELECT sid, source, session_id, title FROM sessions "
                "WHERE session_id=? ORDER BY 1 LIMIT 1",
                (sid_query,)).fetchone()
        if row is None:
            raise KeyError(f"索引库中找不到会话: {sid_query}")
        return row
    finally:
        con.close()


def render_transcript(db: Path, sid_query: str,
                      max_chars: int = _MAX_CHARS) -> tuple[str, dict]:
    """渲染单会话蒸馏原文（消息 + 工具步骤遥测），带 `sid#序号` 锚点。

    消息锚点用 rowid 序，步骤锚点用 steps.seq（与 report-* 口径一致）。
    返回 (transcript_text, meta)；meta 含 sid/source/session_id/title/
    n_msgs/n_steps/truncated。文本超预算时从尾部截断并标注。
    """
    db = Path(db)
    row = _fetch_session(db, sid_query)
    sid = row["sid"]
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        msgs = con.execute(
            "SELECT rowid AS rn, role, ts, text, raw FROM messages "
            "WHERE sid=? ORDER BY rowid", (sid,)).fetchall()
        steps = con.execute(
            "SELECT seq, tool, phase, status, error, detail FROM steps "
            "WHERE sid=? ORDER BY seq", (sid,)).fetchall()
    finally:
        con.close()

    head = (f"# 会话原文：{row['title'] or '（无标题）'}\n"
            f"- 锚点根: `{sid}`（session_id=`{row['session_id']}`，"
            f"来源 {row['source']}）\n"
            f"- 消息 {len(msgs)} 条 / 工具步骤 {len(steps)} 步\n")
    body: list[str] = []
    for m in msgs:
        anchor = f"{sid}#{m['rn']}"
        text = (m["text"] or m["raw"] or "").strip()
        if not text:
            continue
        body.append(f"[{anchor}] [{m['role']}] {text}")
    for s in steps:
        anchor = f"{sid}#{s['seq']}"
        if s["phase"] == "call":
            arg = (s["detail"] or "").replace("\n", " ")[:160]
            body.append(f">> [{anchor}] {s['tool']} call: {arg}")
        else:
            err = s["error"] or s["status"] or ""
            body.append(f"<< [{anchor}] {s['tool']} {s['status']}: "
                        f"{err[:160]}")
    truncated = False
    transcript = "\n\n".join(body)
    if len(transcript) > max_chars:
        transcript = transcript[:max_chars]
        cut = transcript.rfind("\n\n")
        if cut > 0:
            transcript = transcript[:cut]
        truncated = True
        transcript += (f"\n\n<…原文超预算截断，完整内容用 "
                       f"`python -m harvester read {row['session_id']}` 查看>")
    meta = {"sid": sid, "source": row["source"],
            "session_id": row["session_id"], "title": row["title"] or "",
            "n_msgs": len(msgs), "n_steps": len(steps),
            "truncated": truncated}
    return head + "\n" + transcript + "\n", meta


def build_distill_packet(db: Path, sid_query: str,
                         ctype: str = "pitfall",
                         max_chars: int = _MAX_CHARS) -> str:
    """产出自包含蒸馏包 markdown（指令 + 规范 + 会话原文 + 产出要求）。"""
    if ctype not in REQUIRED_TYPES:
        raise ValueError(f"type={ctype!r} 不在 {sorted(REQUIRED_TYPES)}")
    transcript, meta = render_transcript(db, sid_query, max_chars)
    today = time.strftime("%Y%m%d")
    h1, h2 = _TYPE_HEADINGS[ctype]
    return (
        "# 蒸馏包：会话原文 → 知识卡片草稿（T2）\n\n"
        "你是卡片起草 Agent。读完本包全部内容后产出**一张**知识卡草稿。\n\n"
        "## 任务指令\n\n"
        f"1. 卡片类型：`{ctype}`；通读下方会话原文，提炼本会话最值得沉淀"
        "的一个坑/方法/结论；\n"
        "2. evidence 必须从会话原文**逐字**摘录（保持锚点所在原文不变），"
        f"正文引用锚点形如 `{meta['sid']}#<序号>`；\n"
        "3. 不确定的内容宁可留待补，不得编造证据或锚点；\n"
        f"4. 产出单个 md 文件写入 `cards_pending/`，frontmatter 按下方规范，"
        f"confidence 固定 0.3；正文按骨架填 {h1} / {h2} 两节；\n"
        "5. 纪律：草稿永远不直接并入主库——`cards validate --root "
        "cards_pending --db <库>` 通过后仍由人工终审。\n\n"
        "## 卡片规范\n\n"
        f"{_CARD_SPEC.format(types='/'.join(sorted(REQUIRED_TYPES)))}\n"
        "## 会话原文\n\n"
        f"{transcript}\n"
        "## 产出要求\n\n"
        f"- 文件名：`kc-{today}-NNNN-<slug>.md`（NNNN 按当日已有卡片顺延，"
        "slug 取标题短语）\n"
        f"- anchors 的 session_id 用 `{meta['session_id']}`"
        f"（adapter 级 id，validate 会查库确认真实存在）\n"
        f"- 本包截断状态: {'是——卡内如需更长证据请另行 read 原文'
                         if meta['truncated'] else '否'}\n"
    )


def write_packet(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    return path
