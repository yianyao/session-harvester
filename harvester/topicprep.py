# -*- coding: utf-8 -*-
"""主题起草准备（v0.32）——把"每写一条 chain 都要跑"的两件事做成工具。

背景（用户 2026-10-10 要求：能力必须成为工具的一部分，不能只是对已有数据的
一次性处理）：这两件事此前是 `docs/reports/` 下的脚本，但**每个新主题、每条新
chain 都要跑**：

1. **会话级去重**（`topic dedupe`，H40 口径）：成员会话的 user raw 拼接后做
   字符 bigram Jaccard；≥0.90 判重复（同稿重贴 / 跨端重发），簇内并入**最早
   代表**；0.80–0.90 为边界带（同阶段迭代，保留、只报告）。chain 的
   frontmatter 必填 `dedup.reps/duplicates` 就来自这里。
2. **回合索引 / raw 全文**（`topic turns`）：chain 锚点只能取真实的 user 回合号
   （1-based，H24：messages 无 seq 列、时序 = rowid），起草时必须先有这份清单；
   引文必须逐字取自 `messages.raw`（`text` 列是检索用 bigram，H3/H38/H52）。

只读：索引库与 meta 库都按 `mode=ro` 打开。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .topics import show_topic

#: H40 裁决口径（勿改）：≥0.90 判重复；0.80–0.90 为边界带，保留
DUP_THRESHOLD = 0.90
BAND_THRESHOLD = 0.80


def _members(meta_path: Path, topic_id: str) -> list[dict]:
    return show_topic(meta_path, topic_id)["members"]


def _session_row(con: sqlite3.Connection, sid: str) -> sqlite3.Row | None:
    return con.execute(
        "SELECT sid, source, session_id, title, created_at, updated_at "
        "FROM sessions WHERE sid=? OR session_id=? "
        "ORDER BY CASE WHEN sid=? THEN 0 ELSE 1 END LIMIT 1",
        (sid, sid, sid)).fetchone()


def _user_raw(con: sqlite3.Connection, sid: str) -> list[str]:
    return [r[0] or "" for r in con.execute(
        "SELECT raw FROM messages WHERE sid=? AND role='user' ORDER BY rowid",
        (sid,))]


def _grams(s: str) -> set[str]:
    clean = "".join((s or "").split())
    return {clean[i:i + 2] for i in range(len(clean) - 1)}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def dedupe_topic(meta_path: Path, db_path: Path, topic_id: str) -> dict:
    """主题成员的会话级去重报告（H40 口径）。"""
    meta_path, db_path = Path(meta_path), Path(db_path)
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        items = []
        for m in _members(meta_path, topic_id):
            r = _session_row(con, m["sid"])
            turns = _user_raw(con, r["sid"]) if r is not None else []
            items.append({
                "sid": r["sid"] if r is not None else m["sid"],
                "title": (r["title"] or "") if r is not None else "",
                "created_at": (r["created_at"] or "") if r is not None else "",
                "turns": len(turns),
                "grams": _grams("".join(turns)),
                "evidence": m.get("evidence") or "",
                "in_index": r is not None})
    finally:
        con.close()

    items.sort(key=lambda x: (x["created_at"], x["sid"]))
    parent = {it["sid"]: it["sid"] for it in items}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    dup_pairs, band_pairs = [], []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = items[i], items[j]
            jac = _jaccard(a["grams"], b["grams"])
            if not a["grams"] or not b["grams"]:
                continue
            if jac >= DUP_THRESHOLD:
                dup_pairs.append((a["sid"], b["sid"], round(jac, 3)))
                ra, rb = find(a["sid"]), find(b["sid"])
                if ra != rb:
                    parent[ra] = rb
            elif jac >= BAND_THRESHOLD:
                band_pairs.append((a["sid"], b["sid"], round(jac, 3)))

    clusters: dict[str, list] = {}
    for it in items:
        clusters.setdefault(find(it["sid"]), []).append(it)
    reps, dups = [], []
    for members in clusters.values():
        members.sort(key=lambda x: (x["created_at"], x["sid"]))
        reps.append(members[0])
        dups.extend(members[1:])
    return {"topic_id": topic_id, "topic": show_topic(meta_path,
                                                      topic_id)["name"],
            "threshold": DUP_THRESHOLD, "band": BAND_THRESHOLD,
            "members": len(items), "reps": len(reps),
            "duplicates": len(dups), "dup_pairs": dup_pairs,
            "band_pairs": band_pairs,
            "clusters": [[m["sid"] for m in v] for v in clusters.values()
                         if len(v) > 1],
            "rep_list": [r["sid"] for r in reps],
            "no_user_text": [it["sid"] for it in items if not it["grams"]]}


def render_dedupe(r: dict) -> str:
    L = [f"# 会话级去重报告：{r['topic']}（{r['topic_id']}）", "",
         f"- 口径：user raw 拼接字符 bigram Jaccard ≥ {r['threshold']:.2f} 判重复"
         f"（H40 裁决；{r['band']:.2f}–{r['threshold']:.2f} 为边界带，保留）",
         f"- 成员 {r['members']} → **独立代表 {r['reps']}**，"
         f"重复并入 {r['duplicates']}；边界带 {len(r['band_pairs'])} 对",
         f"- 无 user 正文（**不可挂锚点**）：{len(r['no_user_text'])} 个",
         "", "## 重复簇（chain 计数以代表为准）", ""]
    for cl in r["clusters"]:
        L.append(f"- 代表 `{cl[0]}` ← 并入 " + "、".join(f"`{s}`" for s in cl[1:]))
    if not r["clusters"]:
        L.append("- （无重复簇）")
    L += ["", "## 独立代表清单", ""]
    for sid in r["rep_list"]:
        L.append(f"- `{sid}`")
    if r["band_pairs"]:
        L += ["", "## 边界带（0.80–0.90，保留为真实迭代）", ""]
        for a, b, j in r["band_pairs"][:40]:
            L.append(f"- {j} `{a}` ↔ `{b}`")
    if r["no_user_text"]:
        L += ["", "## 无 user 正文的成员（不可挂锚点）", ""]
        for sid in r["no_user_text"]:
            L.append(f"- `{sid}`")
    L += ["", "---", "",
          "chain 的 frontmatter 应写 `dedup.reps` / `dedup.duplicates`，"
          "锚点不迁移（重复会话上的锚点仍可回溯，计数归代表）。"]
    return "\n".join(L)


def turns_of(meta_path: Path, db_path: Path, topic_id: str,
             cap: int = 200, full: bool = False) -> dict:
    """成员的 user 回合清单（`full=True` 时给 raw 全文，供逐字引文）。"""
    meta_path, db_path = Path(meta_path), Path(db_path)
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = []
    try:
        for m in _members(meta_path, topic_id):
            r = _session_row(con, m["sid"])
            if r is None:
                rows.append({"sid": m["sid"], "missing": True, "turns": []})
                continue
            turns = []
            for i, raw in enumerate(_user_raw(con, r["sid"]), 1):
                t = raw.replace("\r", "")
                turns.append({"turn": i, "raw": t if full else None,
                              "preview": None if full
                              else re_flat(t)[:cap]})
            rows.append({"sid": r["sid"], "title": r["title"] or "",
                         "created_at": r["created_at"] or "",
                         "source": r["source"], "missing": False,
                         "turns": turns})
    finally:
        con.close()
    return {"topic_id": topic_id,
            "topic": show_topic(meta_path, topic_id)["name"],
            "members": len(rows), "full": full, "cap": cap, "rows": rows}


def re_flat(s: str) -> str:
    return " ".join((s or "").split())


def render_turns(t: dict) -> str:
    mode = "raw 全文（逐字引文用）" if t["full"] else "首若干字符预览"
    L = [f"# 回合索引：{t['topic']}（{t['topic_id']}）", "",
         f"- 成员 {t['members']} 会话；turn = user 消息 1-based 序号"
         f"（H24：messages 无 seq 列，时序 = rowid）",
         f"- 文本来源：`messages.raw`（**不是 text 列**，H3/H38/H52）｜{mode}", ""]
    for row in t["rows"]:
        if row.get("missing"):
            L += [f"## {row['sid']}", "", "（不在索引库）", ""]
            continue
        L.append(f"## {row['created_at'][:10]}｜{row['title']}｜{row['sid']}"
                 f"｜user 回合 {len(row['turns'])}")
        for tn in row["turns"]:
            body = tn["raw"] if t["full"] else tn["preview"]
            L.append(f"- T{tn['turn']} {body}")
        L.append("")
    if not t["full"]:
        L += ["---", "",
              "需要逐字引文时用 `--full` 取 raw 全文"
              "（引文必须与 raw 逐字一致，写完用 `chain-audit` 复核）。"]
    return "\n".join(L)
