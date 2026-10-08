# -*- coding: utf-8 -*-
"""主题注册表（T1 MVP，v0.22）——T 轨的确定性一半。

topics_meta.db 是独立 meta 库（suggestions_meta.db 范式），采集库
harvester.db 只读（红线 §5.1：schema 冻结，新状态类数据一律写 meta 库）。

口径：
- topic = 语义边界由用户裁决的主题（名称 + 关键词 + 成员会话证据）；
  自动聚类只产候选（T5），不当权威——本模块即权威。
- title_chain（T1 唯一产物档）：成员会话标题按 created_at 排序去重
  （每标题带首现时间）+ 月度分布；产物挂 db_fingerprint，同库快照
  重跑 byte 级一致（可复现红线）。
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .dbmeta import db_fingerprint

_SCHEMA = """
CREATE TABLE IF NOT EXISTS topics (
    id       TEXT PRIMARY KEY,
    name     TEXT NOT NULL UNIQUE,
    keywords TEXT NOT NULL DEFAULT '[]',
    members  TEXT NOT NULL DEFAULT '[]',
    created  TEXT NOT NULL
);
"""


def ensure_topics_db(meta_path: Path) -> Path:
    """建库建表（幂等），返回路径。"""
    meta_path = Path(meta_path)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(meta_path))
    try:
        con.executescript(_SCHEMA)
        con.commit()
    finally:
        con.close()
    return meta_path


def _con(meta_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(meta_path))
    con.row_factory = sqlite3.Row
    return con


def register_topic(meta_path: Path, name: str, keywords: list[str] | None = None,
                   note: str | None = None) -> str:
    """注册主题。id 自动编号 tp-YYYYMMDD-NNN（同日重名即幂等返回旧 id）。"""
    meta_path = Path(meta_path)
    ensure_topics_db(meta_path)
    con = _con(meta_path)
    try:
        row = con.execute("SELECT id FROM topics WHERE name=?", (name,)).fetchone()
        if row:
            return row["id"]
        today = time.strftime("%Y%m%d")
        n = con.execute("SELECT COUNT(*) FROM topics WHERE id LIKE ?",
                        (f"tp-{today}-%",)).fetchone()[0]
        tid = f"tp-{today}-{n + 1:03d}"
        con.execute(
            "INSERT INTO topics (id, name, keywords, members, created) "
            "VALUES (?,?,?,?,?)",
            (tid, name, json.dumps(keywords or [], ensure_ascii=False),
             json.dumps([], ensure_ascii=False), time.strftime("%Y-%m-%d %H:%M")))
        con.commit()
        return tid
    finally:
        con.close()


def add_members(meta_path: Path, topic_id: str, sids: list[str],
                evidence: str = "") -> int:
    """加成员（幂等，逐 sid 记证据）。返回实际新增数。"""
    con = _con(meta_path)
    try:
        row = con.execute("SELECT members FROM topics WHERE id=?",
                          (topic_id,)).fetchone()
        if row is None:
            raise KeyError(f"主题不存在: {topic_id}")
        members = json.loads(row["members"])
        have = {m["sid"] for m in members}
        added = 0
        for sid in sids:
            if sid in have:
                continue
            members.append({"sid": sid, "evidence": evidence})
            have.add(sid)
            added += 1
        con.execute("UPDATE topics SET members=? WHERE id=?",
                    (json.dumps(members, ensure_ascii=False), topic_id))
        con.commit()
        return added
    finally:
        con.close()


def remove_member(meta_path: Path, topic_id: str, sid: str) -> None:
    con = _con(meta_path)
    try:
        row = con.execute("SELECT members FROM topics WHERE id=?",
                          (topic_id,)).fetchone()
        if row is None:
            raise KeyError(f"主题不存在: {topic_id}")
        members = [m for m in json.loads(row["members"]) if m["sid"] != sid]
        con.execute("UPDATE topics SET members=? WHERE id=?",
                    (json.dumps(members, ensure_ascii=False), topic_id))
        con.commit()
    finally:
        con.close()


def list_topics(meta_path: Path) -> list[dict]:
    if not Path(meta_path).is_file():
        return []
    con = _con(meta_path)
    try:
        rows = con.execute("SELECT id, name, keywords, members, created "
                           "FROM topics ORDER BY id").fetchall()
        return [{"id": r["id"], "name": r["name"],
                 "keywords": json.loads(r["keywords"]),
                 "members": len(json.loads(r["members"])),
                 "created": r["created"]} for r in rows]
    finally:
        con.close()


def show_topic(meta_path: Path, topic_id: str) -> dict:
    con = _con(meta_path)
    try:
        row = con.execute("SELECT id, name, keywords, members, created "
                          "FROM topics WHERE id=?", (topic_id,)).fetchone()
        if row is None:
            raise KeyError(f"主题不存在: {topic_id}")
        return {"id": row["id"], "name": row["name"],
                "keywords": json.loads(row["keywords"]),
                "members": json.loads(row["members"]),
                "created": row["created"]}
    finally:
        con.close()


def title_chain(meta_path: Path, topic_id: str,
                db: Path | None = None,
                con: sqlite3.Connection | None = None) -> dict:
    """标题时间序链（T1 产物）：成员会话标题按 created_at 排序去重
    （每标题带首现时间）+ 月度分布 + 库快照指纹。

    con：外部连接（mode=ro + authorizer 口径）；缺省自开只读连接。
    成员 sid 不在索引库 → 进 missing（不静默丢）。
    """
    topic = show_topic(meta_path, topic_id)
    sids = [m["sid"] for m in topic["members"]]
    if con is None and db is not None:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        own = True
    else:
        own = False
    rows: list = []
    missing: list[str] = []
    fp = None
    try:
        for sid in sids:
            r = con.execute(
                "SELECT sid, session_id, title, created_at FROM sessions "
                "WHERE sid=? OR session_id=? ORDER BY CASE WHEN sid=? "
                "THEN 0 ELSE 1 END LIMIT 1", (sid, sid, sid)).fetchone()
            if r is None:
                missing.append(sid)
                continue
            rows.append({"sid": r["sid"], "title": r["title"] or "",
                         "created_at": r["created_at"] or ""})
        # 指纹在连接存活期间计算
        fp = db_fingerprint(db, con=con)
    finally:
        if own:
            con.close()
    # 标题去重：同标题只留最早首现
    rows.sort(key=lambda r: r["created_at"])
    seen: dict[str, dict] = {}
    monthly: dict[str, int] = {}
    for r in rows:
        if r["title"] and r["title"] not in seen:
            seen[r["title"]] = r
        if r["created_at"]:
            monthly[r["created_at"][:7]] = monthly.get(r["created_at"][:7], 0) + 1
    out_rows = [{"title": t, "first_seen": r["created_at"], "sid": r["sid"]}
                for t, r in sorted(seen.items(),
                                   key=lambda kv: kv[1]["created_at"])]
    return {"topic": {"id": topic["id"], "name": topic["name"],
                      "keywords": topic["keywords"]},
            "members": len(sids), "rows": out_rows,
            "monthly": dict(sorted(monthly.items())),
            "missing": missing, "db_fingerprint": fp}


def render_title_chain(chain: dict) -> str:
    """chain dict → 确定性 Markdown 产物（byte 级可复现）。"""
    t = chain["topic"]
    lines = [f"# 主题时间线：{t['name']}", ""]
    lines.append(f"- 主题 {t['id']}｜关键词 {('、'.join(t['keywords'])) or '（无）'}"
                 f"｜成员 {chain['members']} 会话")
    if chain.get("missing"):
        lines.append(f"- 未入索引库成员 {len(chain['missing'])} 个"
                     "（已列名，不静默丢）")
    fp = chain.get("db_fingerprint")
    if fp:
        from .dbmeta import fingerprint_line
        lines.append(f"- 库快照：{fingerprint_line(fp)}")
    lines += ["", "## 标题演进链（时间序去重，每标题带首现时间）", ""]
    for i, r in enumerate(chain["rows"], 1):
        lines.append(f"{i}. `{r['first_seen'][:10]}` {r['title']}（{r['sid']}）")
    lines += ["", "## 月度分布", ""]
    for m, n in chain["monthly"].items():
        lines.append(f"- {m}: {n}")
    if chain.get("missing"):
        lines += ["", "## 未命中索引库的成员", ""]
        for sid in chain["missing"]:
            lines.append(f"- {sid}")
    return "\n".join(lines).rstrip() + "\n"


# ---- T2：五档分层时间线（SOP-T2）-------------------------------------

#: 档位与名义字符预算（PLAN §4 T2：极粗~2K/粗~10K/中~30K；
#: fine/artifact 选会话后按需拉取，无名义预算）。
LEVELS = ("title", "coarse", "mid", "fine", "artifact")
LEVEL_BUDGETS: dict[str, int] = {"title": 2000, "coarse": 10000, "mid": 30000}
#: 单条消息渲染上限（防单条长消息独占档位预算）
_PER_MSG_CAP = {"coarse": 400, "mid": 800}


def _cut(s: str, cap: int) -> str:
    s = (s or "").replace("\r", "")
    return s if len(s) <= cap else s[:cap] + "…"


def _open_ro(db: Path | None, con: sqlite3.Connection | None):
    if con is not None:
        return con, False
    if db is None:
        raise ValueError("需要 db 或 con 之一")
    c = sqlite3.connect(f"file:{Path(db)}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c, True


def _member_rows(con: sqlite3.Connection, sids: list[str]) -> tuple[list, list]:
    """成员 sid → 索引库会话行（created_at 序）+ 未命中清单。"""
    rows: list = []
    missing: list[str] = []
    for sid in sids:
        r = con.execute(
            "SELECT sid, session_id, title, created_at FROM sessions "
            "WHERE sid=? OR session_id=? ORDER BY CASE WHEN sid=? "
            "THEN 0 ELSE 1 END LIMIT 1", (sid, sid, sid)).fetchone()
        if r is None:
            missing.append(sid)
            continue
        rows.append({"sid": r["sid"], "title": r["title"] or "",
                     "created_at": r["created_at"] or ""})
    rows.sort(key=lambda r: r["created_at"])
    return rows, missing


def timeline(meta_path: Path, topic_id: str,
             db: Path | None = None,
             con: sqlite3.Connection | None = None,
             level: str = "title",
             sid: str | None = None,
             artifacts_db: Path | None = None,
             max_chars: int | None = None) -> dict:
    """五档时间线（全部走 raw，H3）。title 档即 title_chain。

    fine/artifact 为按需档：必须给 sid（且是主题成员）；artifact 另需
    artifacts_db（先跑 `artifacts extract --sid`）。
    """
    if level not in LEVELS:
        raise ValueError(f"level={level!r} 不在 {list(LEVELS)}")
    topic = show_topic(meta_path, topic_id)
    member_sids = [m["sid"] for m in topic["members"]]
    base = {"topic": {"id": topic["id"], "name": topic["name"],
                      "keywords": topic["keywords"]},
            "level": level, "members": len(member_sids),
            "budget": (max_chars if max_chars is not None
                       else LEVEL_BUDGETS.get(level))}
    if level == "title":
        chain = title_chain(meta_path, topic_id, db=db, con=con)
        chain.update({"level": "title", "budget": base["budget"]})
        return chain

    if level in ("fine", "artifact"):
        if not sid:
            raise ValueError(f"level={level} 为按需档：必须 --sid 选定会话")
        if sid not in member_sids:
            raise ValueError(f"sid={sid} 不是主题 {topic_id} 的成员")
        base["sid"] = sid
        c, own = _open_ro(db, con)
        try:
            base["db_fingerprint"] = db_fingerprint(db, con=c)
        finally:
            if own:
                c.close()
        if level == "fine":
            from .drafting import render_transcript
            transcript, tmeta = render_transcript(
                db, sid, max_chars=max_chars or 24000)
            base["transcript"] = transcript
            base["transcript_meta"] = tmeta
            return base
        # artifact 档
        if artifacts_db is None:
            raise ValueError("level=artifact 需要 artifacts_db"
                             "（先跑 artifacts extract --sid <sid>）")
        from .artifacts import show_artifacts
        base["rows"] = show_artifacts(artifacts_db, sid)
        return base

    # coarse / mid：成员会话级，从索引库 messages（raw）取
    c, own = _open_ro(db, con)
    try:
        sess, missing = _member_rows(c, member_sids)
        base["missing"] = missing
        base["db_fingerprint"] = db_fingerprint(db, con=c)
        if level == "coarse":
            rows = []
            for s in sess:
                msgs = c.execute(
                    "SELECT role, raw FROM messages WHERE sid=? ORDER BY rowid",
                    (s["sid"],)).fetchall()
                fu = next((m["raw"] for m in msgs if m["role"] == "user"), "")
                la = next((m["raw"] for m in reversed(msgs)
                           if m["role"] == "assistant"), "")
                rows.append({**s, "first_user": _cut(fu, _PER_MSG_CAP["coarse"]),
                             "last_asst": _cut(la, _PER_MSG_CAP["coarse"])})
        else:  # mid：user 消息序（会话时间序 → 会话内 rowid 序）
            rows = []
            for s in sess:
                for m in c.execute(
                        "SELECT ts, raw FROM messages WHERE sid=? "
                        "AND role='user' ORDER BY rowid", (s["sid"],)):
                    rows.append({"sid": s["sid"], "title": s["title"],
                                 "created_at": s["created_at"],
                                 "ts": m["ts"] or "",
                                 "text": _cut(m["raw"] or "",
                                              _PER_MSG_CAP["mid"])})
    finally:
        if own:
            c.close()
    base["rows"] = rows
    return base


def _budget_join(blocks: list[str], budget: int | None,
                 tail: list[str]) -> tuple[str, int]:
    """按预算拼接正文块；超预算即停并附提示。tail 为预算外固定尾节。

    首块超预算时按剩余预算截断该块（保底 100 字符），保证正文
    不因单条超长块爆档。返回 (text, shown)。
    """
    if budget is None:
        return "\n".join(blocks + tail).rstrip() + "\n", len(blocks)
    body: list[str] = []
    used = 0
    shown = 0
    for b in blocks:
        cost = len(b) + 1
        if used + cost > budget:
            remain = budget - used
            if remain >= 100:
                body.append(b[:remain] + "…（本条因预算截断）")
                shown += 1
            note = (f"---\n（预算 {budget} 字符内展示 {shown}/"
                    f"{len(blocks)} 条；其余用 --sid 或 --max-chars 调档查看）")
            return "\n".join(body + [note] + tail).rstrip() + "\n", shown
        body.append(b)
        used += cost
        shown += 1
    return "\n".join(body + tail).rstrip() + "\n", shown


def render_timeline(d: dict) -> str:
    """timeline dict → 确定性 Markdown（各档预算不爆，SOP-T2 验收）。"""
    t = d["topic"]
    level = d["level"]
    head = [f"# 主题时间线：{t['name']}（{level} 档）", "",
            f"- 主题 {t['id']}｜成员 {d['members']} 会话"]
    if d.get("missing"):
        head.append(f"- 未入索引库成员 {len(d['missing'])} 个")
    fp = d.get("db_fingerprint")
    if fp:
        from .dbmeta import fingerprint_line
        head.append(f"- 库快照：{fingerprint_line(fp)}")
    tail: list[str] = []
    if level == "title":
        from .dbmeta import fingerprint_line as _fl  # noqa: F401
        return render_title_chain(d)
    budget = d.get("budget")
    if level == "coarse":
        blocks = []
        for r in d["rows"]:
            blocks.append(
                f"## {r['created_at'][:10]} {r['title']}（{r['sid']}）\n\n"
                f"**首 user**：{r['first_user'] or '（无）'}\n\n"
                f"**末 assistant**：{r['last_asst'] or '（无）'}")
    elif level == "mid":
        blocks = []
        for r in d["rows"]:
            blocks.append(f"## {r['created_at'][:10]} {r['title']}"
                          f"｜{r['ts']}（{r['sid']}）\n\n{r['text']}")
    elif level == "fine":
        blocks = [d["transcript"]]
    else:  # artifact：同文件版本序（时间序即版本序，H19）
        by_file: dict[str, list] = {}
        for r in d["rows"]:
            by_file.setdefault(r["file_path"] or "（无路径）", []).append(r)
        blocks = []
        for f, rs in by_file.items():
            parts = [f"## {f}（{len(rs)} 版）"]
            for r in rs:
                parts.append(
                    f"- #{r['seq']} `{r['ts'] or '-'}` {r['tool']}\n"
                    f"  - 旧：{_cut(r['old_text'] or '（创建）', 200)}\n"
                    f"  - 新：{_cut(r['new_text'] or '', 200)}")
            blocks.append("\n".join(parts))
        if not d["rows"]:
            head.append("- 该会话尚无产物提取记录")
            tail.append("\n## 下一步\n\n先跑："
                        "`python -m harvester artifacts extract --sid "
                        f"{d.get('sid', '')}`")
    text, _shown = _budget_join(blocks, budget, tail)
    return "\n".join(head) + "\n\n" + text


_PACKET_TMPL = """# 蒸馏包：主题时间线 → 思维链长文草稿（T2/T3）

你是链长文起草 Agent。读完本包全部内容后产出**一篇** topic-chain 长文草稿。

## 任务指令

1. 主题：{tname}（{tid}）；下方为该主题成员会话的 {level} 档时间线；
2. 正文按阶段分组："怎么想的→怎么变的→为什么"，每节点挂锚点
   `{{sid, turn}}`（turn 按 reader.split_turns 权威口径）；
3. 不确定的内容宁可标注待补，不得编造锚点或演进关系；
4. 产出的长文（非卡片）交人工复核后存 `knowledge/topics/chain-<topic>.md`，
   由独立校验器校验（锚点必须可回溯、阶段必须有成员证据）。

## 时间线（{level} 档）

{body}

## 产出要求

- 长文 frontmatter：topic / members / anchors(stages) /
  generated_from(db_fingerprint={fp}) / prompt_version；
- 本包层级预算：{budget}{trunc}
"""


def build_topic_packet(db: Path, meta_path: Path, topic_id: str,
                       level: str = "coarse",
                       sid: str | None = None,
                       artifacts_db: Path | None = None,
                       max_chars: int | None = None) -> str:
    """主题蒸馏包：复用 draft 分层预算（H5 的 24K 单会话预算不直接复用），
    会话原文区 = 该主题 {level} 档时间线（预算内截断已标注）。"""
    d = timeline(meta_path, topic_id, db=db, level=level, sid=sid,
                 artifacts_db=artifacts_db, max_chars=max_chars)
    budget = d.get("budget")
    body_budget = None if budget is None else max(budget - 600, 500)
    if level == "title":
        body = render_title_chain(d)
    else:
        body = render_timeline({**d, "budget": body_budget})
    fp = d.get("db_fingerprint") or {}
    return _PACKET_TMPL.format(
        tname=d["topic"]["name"], tid=d["topic"]["id"], level=level,
        body=body, fp=json.dumps(fp, ensure_ascii=False, sort_keys=True),
        budget=budget if budget is not None else "按需（无名义预算）",
        trunc=("——时间线已按预算截断，细节用 fine/artifact 档按需拉取"
               if level in ("coarse", "mid") else ""))
