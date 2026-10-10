# -*- coding: utf-8 -*-
"""chain 长文审计（v0.31）——把"每次写 chain 都要跑的两道门"做成工具。

背景：这两道门此前是 `docs/reports/` 下的一次性脚本，但它们**不是一次性需求**
——每写一条 chain 都要跑，而且抓到过真错：引文被压缩改写、锚点挂错 sid
（note 与原文完全对不上）、"五十章"这种原文里没有的数字。链条校验器
`chain-validate` 只管结构（sid 在成员内、turn 越界），**管不了引文真伪与锚点语义**。

两道门：
1. **引文逐字门**（`quotes`）：正文所有 `「」` 必须能在**成员会话 raw 或标题**
   里找到（归一空白与 Markdown 强调标记；多段省略号引用逐段比对）。
   未命中 = 不合格。
2. **锚点语义门**（`anchors`）：把每个节点的 `note` 与它那一回合的 raw 原文
   并排列出，并给一条**机械**告警——note 里的中文词在原文中**零出现**时标
   「疑似错配」。这条只是提示，最终判断仍由人/Agent 做（工具不判语义）。

只读：索引库按 `mode=ro` 打开。
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

_QUOTE_RE = re.compile(r"「([^」]{6,})」")
_H1_RE = re.compile(r"^#\s+(.+)$", re.M)
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]{2,}")
_STRIP = ("*", "`")

#: note 写"跨会话关系"时，本来就不该与本回合原文有交集
#: （真库误报实例："同日第三处重发同一句请求"、"同一疑问跨端复问"）
#: —— 命中这些标记的 note 不做零重叠告警。
_RELATIONAL = ("重发", "复问", "重贴", "同日", "同稿", "跨端", "同句",
               "第三处", "第二处", "另一次", "再次提交", "同标题")


def _norm(s: str) -> str:
    s = (s or "").replace("\u3000", "").replace("\xa0", "")
    for ch in _STRIP:
        s = s.replace(ch, "")
    return re.sub(r"\s+", "", s)


def _members_of(fm: dict) -> list[str]:
    m = fm.get("members")
    if isinstance(m, list):
        return [str(x) for x in m if x]
    return []


def _load(path: Path) -> dict:
    from .topicchain import load_chain
    return load_chain(path)


def _member_text(con: sqlite3.Connection, sids: list[str]) -> tuple[str, str]:
    """返回（成员正文归一串、成员标题归一串）。"""
    blobs, titles = [], []
    for sid in sids:
        r = con.execute("SELECT sid, title FROM sessions WHERE sid=? OR "
                        "session_id=? LIMIT 1", (sid, sid)).fetchone()
        if r is None:
            continue
        titles.append(r["title"] or "")
        for m in con.execute("SELECT raw FROM messages WHERE sid=?",
                             (r["sid"],)):
            blobs.append(m["raw"] or "")
    return _norm("\n".join(blobs)), _norm("\n".join(titles))


def audit_quotes(path: Path, con: sqlite3.Connection,
                 members: list[str]) -> dict:
    """引文逐字审计：返回 {total, exact, partial, misses[]}。"""
    body = _load(path)["body"]
    text, titles = _member_text(con, members)
    quotes = _QUOTE_RE.findall(body)
    exact = partial = 0
    misses: list[str] = []
    for q in quotes:
        core = _norm(q.rstrip("……。，、；：！？ "))
        if core in text or core in titles:
            exact += 1
            continue
        segs = [_norm(s) for s in re.split(r"……|\.\.\.", core)
                if len(_norm(s)) >= 6]
        if segs and all(s in text or s in titles for s in segs):
            exact += 1
            continue
        longest = max(segs, key=len) if segs else core
        if longest in text or longest in titles:
            partial += 1
            continue
        misses.append(q)
    return {"total": len(quotes), "exact": exact, "partial": partial,
            "misses": misses, "ok": not misses}


def audit_anchors(path: Path, con: sqlite3.Connection, preview: int = 90) -> dict:
    """锚点语义审计：逐节点列出 note 与 raw，并给"零重叠"机械告警。"""
    fm = _load(path)["fm"]
    rows, suspicious = [], []
    for st in fm.get("anchors") or []:
        if not isinstance(st, dict):
            continue
        for nd in st.get("nodes") or []:
            sid, turn = nd.get("sid"), nd.get("turn")
            note = nd.get("note") or ""
            r = con.execute("SELECT sid FROM sessions WHERE sid=? OR "
                            "session_id=? LIMIT 1", (sid, sid)).fetchone()
            body = ""
            if r is not None and isinstance(turn, int) and turn >= 1:
                u = con.execute(
                    "SELECT raw FROM messages WHERE sid=? AND role='user' "
                    "ORDER BY rowid LIMIT 1 OFFSET ?",
                    (r["sid"], turn - 1)).fetchone()
                body = (u["raw"] if u else "") or ""
            flat = re.sub(r"\s+", " ", body)
            # 重叠判据用 note 的 **CJK 2-gram**（不是整段连续串）：note 常是
            # "丁樾回气的动作描写"这种自拟短语，整段当然不在原文里，但
            # "丁樾""回气"在——用整段判会把正确节点全判成错配（首版即如此）。
            note_cjk = "".join(_CJK_RUN.findall(note))
            grams = {note_cjk[i:i + 2] for i in range(len(note_cjk) - 1)}
            hit = sorted(g for g in grams if g in body)
            row = {"stage": st.get("stage"), "sid": sid, "turn": turn,
                   "note": note, "raw": flat[:preview],
                   "overlap": hit,
                   "relational": any(w in note for w in _RELATIONAL)}
            rows.append(row)
            if grams and not hit and not row["relational"]:
                suspicious.append(row)
    return {"nodes": rows, "suspicious": suspicious,
            "ok": not suspicious}


def audit_chain(chain_path: Path, db_path: Path, quotes: bool = True,
                anchors: bool = True) -> dict:
    chain_path, db_path = Path(chain_path), Path(db_path)
    d = _load(chain_path)
    fm = d["fm"] if isinstance(d["fm"], dict) else {}
    members = _members_of(fm)
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        out = {"chain": str(chain_path), "topic": fm.get("topic"),
               "topic_id": fm.get("topic_id"), "members": len(members)}
        if quotes:
            out["quotes"] = audit_quotes(chain_path, con, members)
        if anchors:
            out["anchors"] = audit_anchors(chain_path, con)
    finally:
        con.close()
    ok = True
    if quotes and not out["quotes"]["ok"]:
        ok = False
    if anchors and not out["anchors"]["ok"]:
        ok = False
    out["ok"] = ok
    return out


def render_audit(r: dict, list_limit: int = 20) -> str:
    L = [f"# chain 审计：{r.get('topic') or r['chain']}", "",
         f"- 产物：`{r['chain']}`｜成员 {r['members']} 个"]
    if "quotes" in r:
        q = r["quotes"]
        L += ["", f"## 引文逐字门（{'通过' if q['ok'] else '不合格'}）", "",
              f"- 「」引文 {q['total']} 条：逐字 {q['exact']}、"
              f"部分（省略号多段）{q['partial']}、未命中 {len(q['misses'])}"]
        for m in q["misses"][:list_limit]:
            L.append(f"  - 未命中：「{m[:60]}」")
        if len(q["misses"]) > list_limit:
            L.append(f"  - …另有 {len(q['misses']) - list_limit} 条")
    if "anchors" in r:
        a = r["anchors"]
        L += ["", f"## 锚点语义门（{'未见零重叠' if a['ok'] else '有疑似错配'}）",
              "", f"- 节点 {len(a['nodes'])} 个；notes 与原文**零重叠** "
              f"{len(a['suspicious'])} 个（机械告警，需人/Agent 复核）"]
        for row in a["suspicious"][:list_limit]:
            L.append(f"  - `{row['sid']}` #{row['turn']}　note：{row['note'][:40]}"
                     f"　｜ 原文：{row['raw'][:60]}")
    L += ["", "---", "",
          "两道门都只做**机械核对**：引文能不能逐字找到、note 与原文有没有"
          "字符交集。语义对不对仍由人/Agent 判断（工具不判语义）。"]
    return "\n".join(L)
