# -*- coding: utf-8 -*-
"""chain 长文审计（v0.31）——把"每次写 chain 都要跑的两道门"做成工具。

背景：这两道门此前是 `docs/reports/` 下的一次性脚本，但它们**不是一次性需求**
——每写一条 chain 都要跑，而且抓到过真错：引文被压缩改写、锚点挂错 sid
（note 与原文完全对不上）、"五十章"这种原文里没有的数字。链条校验器
`chain-validate` 只管结构（sid 在成员内、turn 越界），**管不了引文真伪与锚点语义**。

两道门（v0.31）+ 一个覆盖区块（v0.45）：
1. **引文逐字门**（`quotes`）：正文所有 `「」` 必须能在**成员会话 raw 或标题**
   里找到（归一空白与 Markdown 强调标记；多段省略号引用逐段比对）。
   未命中 = 不合格。
2. **锚点语义门**（`anchors`）：把每个节点的 `note` 与它那一回合的 raw 原文
   并排列出，并给一条**机械**告警——note 里的中文词在原文中**零出现**时标
   「疑似错配」。这条只是提示，最终判断仍由人/Agent 做（工具不判语义）。
3. **证据覆盖**（`coverage`，v0.45）：量出**有多少成员根本没有 turn 级锚点**
   （只有标题级证据），并按 H40 口径把"无锚点的重复会话"与"无锚点的独立代表"
   分开——后者才是真缺口。`chain-validate` 只要求每阶段 ≥1 节点，看不出这个差。

三道门都只做机械核对，都不判"哪段论证关键"。

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

#: 正文锚点写法（与 CHAIN-AUTHOR-SPEC §2.3 一致）：`（{sid, turn N}）`，
#: turn 可以是区间 `turn 5-7`（真库实例：352ba755 的蝴蝶结尾五轮）；
#: 另一支是**简写** `（同会话 turn N）` / `（turn N）`——它不带 sid，归属见
#: `_anchor_pairs`。
_FULL_ANCHOR_RE = re.compile(
    r"\{\s*(?P<sid>[A-Za-z0-9][\w:.\-]*)\s*,\s*turn\s*(?P<a>\d+)"
    r"(?:\s*[-~～]\s*(?P<b>\d+))?\s*\}")
#: 扫描单元：要么一个完整锚点，要么一个括号组（组内可能藏着完整锚点或简写）
_SCAN_RE = re.compile(
    r"\{\s*[A-Za-z0-9][\w:.\-]*\s*,\s*turn\s*\d+(?:\s*[-~～]\s*\d+)?\s*\}"
    r"|[（(][^）)\n]*[）)]")
#: 括号组内的回合引用（简写组里可能一次点多个：真库实例
#: `（同会话 turn 10，先在 turn 9 交代了第四章的章节定位）`）
_GROUP_TURN_RE = re.compile(r"turn\s*(\d+)(?:\s*[-~～]\s*(\d+))?")
#: 一个括号组算"简写引用"的门槛：出现 turn 编号
_GROUP_IS_SHORT_RE = re.compile(r"(?:同会话|同一会话)?\s*turn\s*\d")
#: 区间最多展开多少回合（防写错成 `turn 1-999` 时炸内存/刷屏）
_RANGE_CAP = 50
#: ASCII 双引号引用（本仓库首条 chain 写于规范 §5 之前，全文 200+ 处都用 `"…"`）
_ASCII_MIN_LEN = 2


def _ascii_quotes(body: str) -> list[str]:
    """按出现顺序**成对**取 ASCII 双引号引用。

    为什么不用正则：`"这段好不好"，而是拆成三问："老人与……"` 这条里第一个
    引用只有 5 个字，写 `"([^"]{6,})"` 的正则会跳过它的开引号，转而把
    "闭引号 → 下一个开引号"之间的正常叙述当成引用——真库实测 82 条里
    二十多条是这么来的**假未命中**。成对扫描（奇数位片段即引用）不会错位；
    末尾落单的引号丢弃（不配对就无法判定边界）。
    """
    out: list[str] = []
    for line in body.split("\n"):
        parts = line.split('"')
        for i in range(1, len(parts) - 1, 2):
            if len(parts[i].strip()) >= _ASCII_MIN_LEN:
                out.append(parts[i])
    return out

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


def _anchor_pairs(body: str) -> dict:
    """解析正文锚点：完整 `{sid, turn N}` 与简写 `（同会话 turn N）`。

    **简写必须按"行内最近的前一个完整锚点"归属**，不能拿 `turn N` 去全库
    匹配：真库踩过——`c503406a` 的 t4 在正文里其实已被替换掉不再引用，
    但全库存在别的 "turn 4"，于是被判成"简写引用过"，把**漏引**粉饰成引用。
    按**行**（不是按空行分段）切，是因为正文的编号列表每项占一行、"同会话
    turn N" 常与它的完整锚点同行；按空行切会把上一项的 sid 误当本项的上文
    （真库实例：阶段二第 3 条的"同会话 turn 9/10"被判成无 sid 可归属）。
    """
    full: list[tuple[str, int]] = []
    short: list[tuple[str | None, int]] = []

    def _span(a: str, b: str | None) -> range:
        s = int(a)
        e = int(b) if b else s
        return range(s, min(e, s + _RANGE_CAP - 1) + 1)

    for para in re.split(r"\n+", body):
        last_sid: str | None = None
        for m in _SCAN_RE.finditer(para):
            chunk = m.group(0)
            hits = list(_FULL_ANCHOR_RE.finditer(chunk))
            if hits:                    # 完整锚点（可能带 `（…）` 包裹）
                for f in hits:
                    last_sid = f.group("sid")
                    full.extend((last_sid, t)
                                for t in _span(f.group("a"), f.group("b")))
                continue
            grp = chunk.strip("（）()")
            if not _GROUP_IS_SHORT_RE.search(grp):
                continue                # 不是回合引用（如"（据标题）"）
            for tm in _GROUP_TURN_RE.finditer(grp):
                short.extend((last_sid, t)
                             for t in _span(tm.group(1), tm.group(2)))
    return {"full": full, "short": short}


def _session_row(con: sqlite3.Connection, sid: str):
    """成员键（sid 或 session_id）→ sessions 行；查不到返回 None。"""
    return con.execute("SELECT sid, session_id, title, created_at FROM sessions "
                       "WHERE sid=? OR session_id=? "
                       "ORDER BY CASE WHEN sid=? THEN 0 ELSE 1 END LIMIT 1",
                       (sid, sid, sid)).fetchone()


def _resolve_members(con: sqlite3.Connection, keys: list[str]) -> dict:
    """批量解析成员键 → {键: 行|None}。**一次 IN 查询**，不逐成员扫表（H69）。"""
    keys = [k for k in keys if k]
    if not keys:
        return {}
    ph = ",".join("?" * len(keys))
    rows = con.execute(
        f"SELECT sid, session_id, title, created_at FROM sessions "
        f"WHERE sid IN ({ph}) OR session_id IN ({ph})",
        (*keys, *keys)).fetchall()
    by_sid = {r["sid"]: r for r in rows}
    by_session: dict = {}
    for r in sorted(rows, key=lambda r: r["sid"]):     # 同 session_id 取 sid 序首个
        by_session.setdefault(r["session_id"], r)
    return {k: (by_sid.get(k) or by_session.get(k)) for k in keys}


def _member_messages(con: sqlite3.Connection, sids: list[str]) -> dict:
    """批量取成员全部消息（role, raw），按 rowid 序；**一次扫描**（H69）。

    引文门要全角色（引文可能出自 assistant），证据覆盖只用 role=='user'
    （H40 的去重口径与 turn 编号都以 user raw 为准），故一次取回按角色分流。
    """
    sids = [s for s in sids if s]
    out: dict[str, list] = {s: [] for s in sids}
    for i in range(0, len(sids), 200):
        chunk = sids[i:i + 200]
        ph = ",".join("?" * len(chunk))
        for r in con.execute(f"SELECT sid, role, raw FROM messages "
                             f"WHERE sid IN ({ph}) ORDER BY rowid", chunk):
            out[r["sid"]].append(r)
    return out


def _member_text(con: sqlite3.Connection, members: list[str]) -> tuple[str, str]:
    """返回（成员正文归一串、成员标题归一串）。members 为 frontmatter 的成员键
    （可能是 sid，也可能是 session_id —— `_resolve_members` 统一解析）。"""
    rows = _resolve_members(con, members)
    sids = list(dict.fromkeys(r["sid"] for r in rows.values() if r is not None))
    msgs = _member_messages(con, sids)
    blobs = [m["raw"] or "" for sid in sids for m in msgs.get(sid, [])]
    titles = [(rows[k]["title"] or "") for k in members if rows.get(k)]
    return _norm("\n".join(blobs)), _norm("\n".join(titles))


def _match_quotes(spans: list[str], text: str) -> dict:
    """逐字比对一批引用：`text` 是成员 raw/标题归一串。"""
    exact = partial = 0
    misses: list[str] = []
    for q in spans:
        core = _norm(q.rstrip("……。，、；：！？ "))
        if core in text:
            exact += 1
            continue
        segs = [_norm(s) for s in re.split(r"……|\.\.\.", core)
                if len(_norm(s)) >= 6]
        if segs and all(s in text for s in segs):
            exact += 1
            continue
        longest = max(segs, key=len) if segs else core
        if longest in text:
            partial += 1
            continue
        misses.append(q)
    return {"total": len(spans), "exact": exact, "partial": partial,
            "misses": misses}


def audit_quotes(path: Path, con: sqlite3.Connection, members: list[str],
                 ascii_quotes: bool = False) -> dict:
    """引文逐字审计：返回 {total, exact, partial, misses[], ok, …}。

    `ascii_quotes=True` 时额外核 **ASCII 双引号** `"…"` 引用。为什么要有这个
    开关：本仓库首条 chain 写于 CHAIN-AUTHOR-SPEC §5 之前，正文 212 处引用
    全用 ASCII 引号、`「」` 只有 5 处且都是界面标签（view「主题」tab 之类，
    长度 <6 被本门忽略）——**这道门对它是空转的**，而"0 条未命中"看上去
    跟"全部通过"一模一样。故本函数显式返回 `vacuous`，报告里必须说出来。
    """
    body = _load(path)["body"]
    text, titles = _member_text(con, members)
    cjk = _QUOTE_RE.findall(body)
    ascii_spans = _ascii_quotes(body)
    r = _match_quotes(cjk, text + "\u0000" + titles)
    r["vacuous"] = not cjk and bool(ascii_spans)
    r["ascii_total"] = len(ascii_spans)
    r["ascii_checked"] = bool(ascii_quotes)
    r["ascii_misses"] = []
    if ascii_quotes:
        a = _match_quotes(ascii_spans, text + "\u0000" + titles)
        r["ascii_misses"] = a["misses"]
        r["ascii_exact"] = a["exact"]
        r["ascii_partial"] = a["partial"]
    r["ok"] = not r["misses"] and not r["ascii_misses"]
    return r


def audit_anchors(path: Path, con: sqlite3.Connection, preview: int = 90) -> dict:
    """锚点语义审计：逐节点列出 note 与 raw，并给"零重叠"机械告警。

    重叠判据的素材 = 该回合 raw **＋ 所属会话标题**：note 写"肯定性标题：
    节奏把控佳""动词精准度润色视角"这类**标题级**依据是允许的（规范 §3
    要求标题级证据显式标注），只比 raw 会把它们全判成错配（真库 5 条告警里
    3 条属这一类），告警一多就没人看了。
    """
    fm = _load(path)["fm"]
    rows, suspicious = [], []
    for st in fm.get("anchors") or []:
        if not isinstance(st, dict):
            continue
        for nd in st.get("nodes") or []:
            sid, turn = nd.get("sid"), nd.get("turn")
            note = nd.get("note") or ""
            r = _session_row(con, sid)
            body = ""
            if r is not None and isinstance(turn, int) and turn >= 1:
                u = con.execute(
                    "SELECT raw FROM messages WHERE sid=? AND role='user' "
                    "ORDER BY rowid LIMIT 1 OFFSET ?",
                    (r["sid"], turn - 1)).fetchone()
                body = (u["raw"] if u else "") or ""
            title = (r["title"] or "") if r is not None else ""
            flat = re.sub(r"\s+", " ", body)
            # 重叠判据用 note 的 **CJK 2-gram**（不是整段连续串）：note 常是
            # "丁樾回气的动作描写"这种自拟短语，整段当然不在原文里，但
            # "丁樾""回气"在——用整段判会把正确节点全判成错配（首版即如此）。
            note_cjk = "".join(_CJK_RUN.findall(note))
            grams = {note_cjk[i:i + 2] for i in range(len(note_cjk) - 1)}
            hay = body + "\u0000" + title
            hit = sorted(g for g in grams if g in hay)
            row = {"stage": st.get("stage"), "sid": sid, "turn": turn,
                   "note": note, "raw": flat[:preview], "title": title[:40],
                   "overlap": hit,
                   "relational": any(w in note for w in _RELATIONAL)}
            rows.append(row)
            if grams and not hit and not row["relational"]:
                suspicious.append(row)
    return {"nodes": rows, "suspicious": suspicious,
            "ok": not suspicious}


def audit_coverage(path: Path, con: sqlite3.Connection,
                   preview: int = 60) -> dict:
    """证据覆盖（v0.45）：哪些成员**没有** turn 级锚点，以及每阶段几个节点。

    为什么要有这一门：`chain-validate` 只要求"每个 stage 至少 1 个节点"，
    它**不关心 55 个成员里有几个真被引到**。读者的默认读法是"整篇 30 个锚点，
    所以都有证据"——真库首条 chain 实测 55 成员里只有 27 个有锚点，
    其余 28 个只有**标题级**证据。本函数把这个差值显式量出来。

    口径（显式写死，勿凭印象）：
    - **成员覆盖** = `anchors[].nodes[].sid` 中属于 `members` 的去重个数
      ÷ `members` 个数。
    - 分母含**被去重并入的重复会话**（H40：锚点不迁移、同一稿只计一次证），
      故报告同时给出现算的 `reps` / `duplicates`；**评判缺口要看
      "无锚点的独立代表数"**，无锚点的重复会话单列（它们本来就无须挂锚点）。
    - 代表/重复**现算**（`dedupe_items`，H40 口径 bigram-Jaccard ≥0.90），
      不采信 frontmatter 抄来的 `dedup.reps/duplicates`：chain 是历史快照，
      它的成员集可能与当前主题不同（真库实测主题 411 / chain 55）。
      两者不一致时并列印出来，不做静默取舍。
    - **阶段覆盖** = 每阶段节点数；0 即该阶段只有标题级证据。
    - `unknown` = 锚点里出现、但不属于 `members` 的 sid（`chain-validate`
      也会判错，这里再列一次，防"改了成员没改锚点"静默通过）。
    - `missing` = `members` 里在索引库查不到的键。

    本区块**不影响退出码**（与锚点语义门同属"要人/Agent 看"的提示）。
    """
    from .topicprep import _grams, dedupe_items

    fm = _load(path)["fm"]
    members = _members_of(fm)
    stage_nodes: list[dict] = []
    nodes: list[dict] = []
    for st in fm.get("anchors") or []:
        if not isinstance(st, dict):
            continue
        ns = [nd for nd in (st.get("nodes") or []) if isinstance(nd, dict)]
        stage_nodes.append({"stage": st.get("stage"), "nodes": len(ns)})
        nodes += [{"stage": st.get("stage"), **nd} for nd in ns]
    anchored: list[str] = []
    for nd in nodes:
        sid = str(nd.get("sid") or "")
        if sid and sid not in anchored:
            anchored.append(sid)
    member_set = set(members)
    anchored_members = [s for s in anchored if s in member_set]
    unknown = [s for s in anchored if s not in member_set]

    rows = _resolve_members(con, members)
    sids = list(dict.fromkeys(r["sid"] for r in rows.values() if r is not None))
    msgs = _member_messages(con, sids)
    items, info = [], {}
    for key in members:
        r = rows.get(key)
        real = r["sid"] if r is not None else key
        users = [m["raw"] or "" for m in msgs.get(real, [])
                 if m["role"] == "user"]
        info[key] = {"sid": real, "title": (r["title"] or "") if r else "",
                     "created_at": (r["created_at"] or "") if r else "",
                     "in_index": r is not None, "user_turns": len(users),
                     "first_user": re.sub(r"\s+", " ", users[0])[:preview]
                     if users else ""}
        items.append({"sid": real, "title": info[key]["title"],
                      "created_at": info[key]["created_at"],
                      "turns": len(users), "grams": _grams("".join(users))})

    d = dedupe_items(items)
    rep_of = {}
    for cl in d["clusters"]:
        for sid in cl[1:]:
            rep_of[sid] = cl[0]
    anchored_set = set(anchored_members)
    uncovered = []
    for key in members:
        real = info[key]["sid"]
        if real in anchored_set or key in anchored_set:
            continue
        row = {"sid": real, "kind": "dup" if real in rep_of else "rep",
               "rep": rep_of.get(real), **{k: v for k, v in info[key].items()
                                           if k != "sid"}}
        uncovered.append(row)
    uncovered.sort(key=lambda r: (r["created_at"], r["sid"]))
    reps_gap = [r for r in uncovered if r["kind"] == "rep"]
    dup_gap = [r for r in uncovered if r["kind"] == "dup"]
    dedup_fm = fm.get("dedup") if isinstance(fm.get("dedup"), dict) else {}

    # ── 正文锚点 ↔ frontmatter 锚点 双向对账 ────────────────────────────────
    # 为什么要对账：证据覆盖按 **frontmatter** 统计（view 的节点与点击直达都
    # 来自它），而**正文**才是论证发生的地方。真库首条 chain 实测两个方向都
    # 不一致：正文引了 `22087e50 turn 2`、`2d12c33a turn 6-7` 却没进
    # frontmatter → 这些引用在 view 里不可点、覆盖统计也看不到它们。
    body = _load(path)["body"]
    pairs = _anchor_pairs(body)
    body_set = set(pairs["full"])
    short_set = set(pairs["short"])
    fm_pairs = [(str(nd.get("sid") or ""), nd.get("turn")) for nd in nodes
                if isinstance(nd.get("turn"), int)]
    fm_set = set(fm_pairs)
    body_only = sorted({p for p in body_set if p not in fm_set},
                       key=lambda p: (p[0], p[1]))
    fm_only = sorted({p for p in fm_set if p not in body_set},
                     key=lambda p: (p[0], p[1]))
    # frontmatter 有、正文没有整写——分"段落内确有简写引用该 sid 的 turn"
    # （软，需人工确认）与"完全没用过"（硬违规，规范 §2.5 要求每项都被用到）。
    fm_unused, fm_shorthand = [], []
    for sid, turn in fm_only:
        (fm_shorthand if (sid, turn) in short_set else fm_unused).append(
            {"sid": sid, "turn": turn})
    short_unresolved = sorted({t for sid, t in pairs["short"] if not sid})

    return {
        "members": len(members), "anchored_members": len(anchored_members),
        "nodes": len(nodes), "uncovered": uncovered,
        "uncovered_reps": reps_gap, "uncovered_dups": dup_gap,
        "unknown": unknown, "missing": [k for k in members
                                        if not info[k]["in_index"]],
        "stages": stage_nodes,
        "empty_stages": [s["stage"] for s in stage_nodes if not s["nodes"]],
        "reps": d["reps"], "duplicates": d["duplicates"],
        "fm_reps": dedup_fm.get("reps"), "fm_duplicates": dedup_fm.get("duplicates"),
        "no_user_text": d["no_user_text"],
        "body_pairs": len(body_set), "fm_pairs": len(fm_set),
        "body_only": [{"sid": s, "turn": t} for s, t in body_only],
        "fm_only": [{"sid": s, "turn": t} for s, t in fm_only],
        "fm_unused": fm_unused, "fm_shorthand": fm_shorthand,
        "shorthand_unresolved": short_unresolved,
        "ok": True}


def audit_chain(chain_path: Path, db_path: Path, quotes: bool = True,
                anchors: bool = True, coverage: bool = True,
                quotes_ascii: bool = False) -> dict:
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
            out["quotes"] = audit_quotes(chain_path, con, members,
                                         ascii_quotes=quotes_ascii)
        if anchors:
            out["anchors"] = audit_anchors(chain_path, con)
        if coverage:
            out["coverage"] = audit_coverage(chain_path, con)
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
    # list_limit=0 表示**不限**（三个区块一致；缺省 20 条，超出时显式印"另有 N 条"，
    # 免得"摘要被当全量"——H70 的同一坑）
    lim = list_limit if list_limit and list_limit > 0 else None
    L = [f"# chain 审计：{r.get('topic') or r['chain']}", "",
         f"- 产物：`{r['chain']}`｜成员 {r['members']} 个"]
    if "quotes" in r:
        q = r["quotes"]
        head = "不合格" if not q["ok"] else ("**空转**" if q.get("vacuous")
                                            else "通过")
        L += ["", f"## 引文逐字门（{head}）", "",
              f"- 「」引文 {q['total']} 条：逐字 {q['exact']}、"
              f"部分（省略号多段）{q['partial']}、未命中 {len(q['misses'])}"]
        if q.get("vacuous"):
            L += ["- **本门空转**：正文没有「」引文（本文件用 ASCII 双引号 `\"…\"`"
                  f"，共 {q['ascii_total']} 处）→ 上述 0 未命中**不代表引用已核过**。"
                  "加 `--quotes-ascii` 可逐字核这些引用。"]
        if q.get("ascii_checked"):
            L += [f"- ASCII 双引号引用 {q['ascii_total']} 条：逐字 "
                  f"{q.get('ascii_exact', 0)}、部分 {q.get('ascii_partial', 0)}、"
                  f"未命中 {len(q['ascii_misses'])}"]
            for m in (q["ascii_misses"] if lim is None
                      else q["ascii_misses"][:lim]):
                L.append(f"  - 未命中：\"{m[:60]}\"")
        for m in (q["misses"] if lim is None else q["misses"][:lim]):
            L.append(f"  - 未命中：「{m[:60]}」")
        if lim is not None and len(q["misses"]) > lim:
            L.append(f"  - …另有 {len(q['misses']) - lim} 条")
    if "anchors" in r:
        a = r["anchors"]
        L += ["", f"## 锚点语义门（{'未见零重叠' if a['ok'] else '有疑似错配'}）",
              "", f"- 节点 {len(a['nodes'])} 个；notes 与原文**零重叠** "
              f"{len(a['suspicious'])} 个（机械告警，需人/Agent 复核）"]
        for row in (a["suspicious"] if lim is None else a["suspicious"][:lim]):
            L.append(f"  - `{row['sid']}` #{row['turn']}　note：{row['note'][:40]}"
                     f"　｜ 原文：{row['raw'][:60]}")
        if lim is not None and len(a["suspicious"]) > lim:
            L.append(f"  - …另有 {len(a['suspicious']) - lim} 条")
        L.append("  - （重叠判据含**会话标题**：note 写标题级依据不算错配；"
                 "本门不判语义）")
    if "coverage" in r:
        c = r["coverage"]
        pct = (100.0 * c["anchored_members"] / c["members"]) if c["members"] else 0.0
        L += ["", "## 证据覆盖（informational，不影响退出码）", "",
              f"- 成员 {c['members']} 个：**有 turn 级锚点的成员 "
              f"{c['anchored_members']} 个（{pct:.1f}%）**；无锚点 "
              f"{len(c['uncovered'])} 个",
              f"- 节点 {c['nodes']} 个｜去重（H40 现算）：代表 {c['reps']}、"
              f"重复并入 {c['duplicates']}"
              f"（frontmatter 抄的 {c['fm_reps']}/{c['fm_duplicates']}"
              + ("，一致" if (c["fm_reps"] == c["reps"]
                             and c["fm_duplicates"] == c["duplicates"])
                 else "，**不一致，以现算为准**") + "）",
              f"- **无锚点的独立代表 {len(c['uncovered_reps'])} 个（真缺口）**"
              f"；无锚点的重复会话 {len(c['uncovered_dups'])} 个"
              f"（H40：锚点不迁移，本就无须各自挂）",
              "- 阶段节点数：" + "｜".join(
                  f"{s['stage']} {s['nodes']}" for s in c["stages"])
              + ("" if not c["empty_stages"]
                 else "　**零节点阶段：" + "、".join(c["empty_stages"]) + "**"),
              f"- 锚点里不属于 members 的 sid：{len(c['unknown'])}"
              f"｜索引库中查不到的成员：{len(c['missing'])}",
              f"- 正文锚点 {c['body_pairs']} 个 `{{sid, turn}}`｜"
              f"frontmatter 节点 {c['nodes']} 个｜"
              f"**正文引了而 frontmatter 没有 {len(c['body_only'])} 个**"
              f"（view 里不可点、上面的覆盖统计也漏）｜"
              f"frontmatter 有而正文未整写 {len(c['fm_only'])} 个",
              "", "未覆盖的**独立代表**（真缺口）——下列 `首 user` 是"
              "**预览**，判定须 `topic turns --full`：", ""]
        for row in c["uncovered_reps"][:lim]:
            L.append(f"  - `{row['sid']}`（{row['user_turns']} 回合｜"
                     f"{row['created_at'][:10]}）{row['title']}"
                     f"　｜ T1：{row['first_user'][:60]}")
        if lim is not None and len(c["uncovered_reps"]) > lim:
            L.append(f"  - …另有 {len(c['uncovered_reps']) - lim} 条"
                     f"（`--list-limit 0` 看全量）")
        if c["uncovered_dups"]:
            L += ["", "未覆盖的**重复会话**（已并入代表，无需挂锚点）：", ""]
            for row in c["uncovered_dups"][:lim]:
                L.append(f"  - `{row['sid']}` → 代表 `{row['rep']}`"
                         f"（{row['user_turns']} 回合｜{row['created_at'][:10]}）")
            if lim is not None and len(c["uncovered_dups"]) > lim:
                L.append(f"  - …另有 {len(c['uncovered_dups']) - lim} 条")
        for sid in c["unknown"][:lim]:
            L.append(f"  - 非成员锚点：`{sid}`")
        for sid in c["missing"][:lim]:
            L.append(f"  - 不在索引库：`{sid}`")
        if c["body_only"]:
            L += ["", "**正文引了、frontmatter 缺登记**（补进 `anchors` 即可，"
                      "无需判断）：", ""]
            for row in (c["body_only"] if lim is None else c["body_only"][:lim]):
                L.append(f"  - `{row['sid']}` turn {row['turn']}")
            if lim is not None and len(c["body_only"]) > lim:
                L.append(f"  - …另有 {len(c['body_only']) - lim} 个")
        if c["fm_unused"]:
            L += ["", "**frontmatter 有、正文完全没写 `turn N`**"
                      "（规范 §2.5 要求每个节点都在正文被用到）：", ""]
            for row in (c["fm_unused"] if lim is None else c["fm_unused"][:lim]):
                L.append(f"  - `{row['sid']}` turn {row['turn']}")
        if c["fm_shorthand"]:
            L += ["", "frontmatter 有、正文**简写**引用"
                      "（`同会话 turn N` 之类，需人工确认）：", ""]
            for row in (c["fm_shorthand"] if lim is None
                        else c["fm_shorthand"][:lim]):
                L.append(f"  - `{row['sid']}` turn {row['turn']}")
        if c["shorthand_unresolved"]:
            L += ["", "正文里有 `（turn N）` 简写但**同一段没有前置完整锚点**"
                      "（无法归属 sid，需人工确认）：", "",
                  "  - turn " + "、".join(str(t)
                                           for t in c["shorthand_unresolved"])]
    L += ["", "---", "",
          "三道门都只做**机械核对**：引文能不能逐字找到、note 与原文有没有"
          "字符交集、哪些成员没有锚点。语义对不对——某段论证是不是"
          "关键、该不该补锚点——仍由人/Agent 判断（工具不判语义）。"]
    return "\n".join(L)
