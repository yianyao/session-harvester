# -*- coding: utf-8 -*-
"""会话零散分诊（v0.30）——把"这轮对话是不是只为取信息"落成可复核的规则。

用户 2026-10-10 提出的判据（**原话**）：
> 「很多都是问词的释义、翻译，或者是要求某个查询。如果请求里只要求查询但没
>   提到有分析提炼整理归纳之类，而且同一轮对话前后内容可能大相径庭，那这个
>   对话的目的多半就只是为了获取信息而不是整合信息吧？」

落成两条**可判定**的信号 + 一条归属信号：

- `lookup`：首条 user 命中查询类词（是什么/含义/释义/翻译/英文/近义词/出处/
  多少钱/推荐/怎么用…）且**不命中**整合类词（分析/提炼/整理/归纳/总结/优化/
  建议/设计/改写/评估/对比/方案/规范/大纲/写作/生成/评审/检查/推演…）。
- `incoherent`：多轮时，**相邻 user 回合的会话主题不连贯**（字符 bigram
  Jaccard 均值 < 阈值）——"同一轮对话前后内容大相径庭"的量化形式。
- `topic_hint`：标题/首条是否命中某个已注册主题的关键词（→ 该归主题，不是零散）。

判定：
    高置信零散 = lookup 且 **单轮** 且 无主题归属 且 非创作素材
    待定      = lookup 且 多轮（连贯度只作报告信号，见下）
    疑似归主题 = 命中主题关键词
    创作素材型 = 命中创作素材词（看着像查词，实为写作找料）
    实质会话  = 命中整合类词 / 非查询型

**为什么"多轮不连贯"没有进高置信**：字符 bigram 连贯度在中文短句上区分力弱
——"查一下方差公式 / 那标准差怎么算" 实测只有 0.067（两个相关追问也可能零
重叠），拿它做判据会大批误伤。故它只作为**报告信号**输出，供人/Agent 判断；
高置信只保留最稳的一条：单轮 + 纯查询 + 非素材 + 无主题。

**工具只产判定与理由，登记与否由 plan 决定**（红线：语义判断不落进代码）。

两条出口，不要混用：

- `render_triage` → **人读报告**，每类最多 60 条（长了没人看）；
- `dump_triage`   → **机器读全量**（`topic-consolidate --triage-json`），
  供 Agent 填 plan。**分诊报告是截断的，照它填 plan 必漏尾部条目**
  （而且漏了没人发现——报告只写"另有 N 条"）。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

#: 查询类意图（只取信息）
LOOKUP_WORDS = (
    "是什么", "什么意思", "啥意思", "含义", "释义", "解释", "翻译", "英文",
    "近义词", "反义词", "读音", "怎么读", "出处", "来源", "多少钱", "价格",
    "报价", "推荐", "怎么用", "如何使用", "怎么办", "查询", "查一下", "查查",
    "介绍一下", "是什么梗", "区别", "对比一下", "有几种", "哪些", "为什么",
    "怎么解", "求解", "是多少", "叫什么", "在哪", "何时",
)

#: 创作素材/文本生产类意图——**看着像"查词"，实际在为写作找料**
#: （真库反例：交通锥材质、形容车祸的短语、描写皱眉动作、表示注视的词汇……
#:  这些是《吾好梦中救人》的素材检索，不是零散闲聊。用户判据只给"查询 vs
#:  整合"两档时，这一类会被误判成零散，故单列。）
CRAFT_WORDS = (
    "描写", "词汇", "词语", "用词", "措辞", "措词", "近义词",
    "反义词", "同义词", "动词", "形容词", "名词", "短语", "句式",
    "台词", "语气", "口吻", "称呼", "神态", "微表情", "举止",
    # "动作"：v0.36 收紧 M1 关键词后补进来的——实测「擦眼镜的动作过程」「感谢时
    # 简单而轻微的动作」这类**写作找料**提问既不含别的 CRAFT 词、也不含查询词，
    # 于是落进 `substantive/非查询型表述` 兜底档 → **从分诊报告里彻底消失**
    # （报告只列 noise_high/noise_maybe/craft_material/topic_hint）。
    # 它排在"查询型零散"之前，与 H64 的判据同向：看着像查词、实为写作找料。
    "动作",
    "意象", "比喻", "修辞", "象征", "伏笔", "铺垫",
    "语病", "病句", "错别字", "润色", "人设", "小传", "大纲", "章纲",
    "梗概", "情节", "桥段", "细节", "场景", "氛围",
)

#: 整合/生产类意图（要产出东西，不是取信息）
INTEGRATE_WORDS = (
    "分析", "提炼", "整理", "归纳", "总结", "优化", "建议", "设计", "改写",
    "评估", "评审", "对比分析", "方案", "规范", "大纲", "章纲", "写作", "生成",
    "检查", "校验", "重构", "计划", "梳理", "拆分", "实现", "开发", "调试",
    "修复", "推演", "论证", "校对", "润色", "扩写", "缩写", "编写", "产出",
    "报告", "表格", "脚本", "代码", "流程", "清单",
)

#: 相邻回合主题不连贯的阈值（字符 bigram Jaccard 均值）
INCOHERENT_BELOW = 0.15


def _grams(s: str, n: int = 2) -> set[str]:
    clean = "".join((s or "").split())
    return {clean[i:i + n] for i in range(len(clean) - n + 1)}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def intent_of(text: str) -> tuple[bool, bool, bool]:
    """返回 (查询型, 整合型, 创作素材型)。三类都不排斥；判定时看优先级。"""
    t = text or ""
    lookup = any(w in t for w in LOOKUP_WORDS)
    integrate = any(w in t for w in INTEGRATE_WORDS)
    craft = any(w in t for w in CRAFT_WORDS)
    return lookup, integrate, craft


def coherence(turns: list[str]) -> float:
    """相邻 user 回合的主题连贯度（bigram Jaccard 均值）；单轮 → 1.0。"""
    if len(turns) < 2:
        return 1.0
    sims = [_jaccard(_grams(turns[i]), _grams(turns[i + 1]))
            for i in range(len(turns) - 1)]
    return sum(sims) / len(sims) if sims else 1.0


def classify(first_user: str, turns: list[str],
             topic_hint: str | None = None) -> dict:
    """给单条会话出判定。turns = 该会话全部 user 原文（时间序）。

    优先级：命中主题关键词 > 整合诉求 > **创作素材** > 查询型零散 > 其他。
    「创作素材」必须排在"查询型零散"之前——它的表象是查词，但目的是写作。
    """
    lookup, integrate, craft = intent_of(first_user)
    coh = coherence(turns)
    n = len(turns)
    if topic_hint:
        verdict, reason = "topic_hint", f"命中主题关键词：{topic_hint}"
    elif integrate:
        verdict, reason = "substantive", "含整合/生产类诉求"
    elif craft:
        verdict = "craft_material"
        reason = "创作素材型检索（看着像查词，实为写作找料）"
    elif lookup and n == 1:
        verdict = "noise_high"
        reason = "只要求查询、无整合诉求；单轮查询"
    elif lookup:
        verdict = "noise_maybe"
        reason = (f"查询型且多轮（连贯度 {coh:.2f}，仅作参考）："
                  "可能是一串相关查询，需人判断")
    else:
        verdict, reason = "substantive", "非查询型表述"
    return {"verdict": verdict, "reason": reason, "lookup": lookup,
            "integrate": integrate, "craft": craft, "turns": n,
            "coherence": round(coh, 3)}


def _topic_keyword_index(meta_path: Path) -> list[tuple[str, str]]:
    """[(关键词, 主题名)]，用于"疑似归主题"的机械提示（不是判定）。"""
    if not meta_path or not Path(meta_path).is_file():
        return []
    import json
    con = sqlite3.connect(f"file:{Path(meta_path)}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    out: list[tuple[str, str]] = []
    try:
        for r in con.execute("SELECT name, keywords FROM topics"):
            for k in json.loads(r["keywords"] or "[]"):
                if isinstance(k, str) and len(k) >= 2:
                    out.append((k, r["name"]))
    finally:
        con.close()
    # 长关键词优先，避免"对比"这类短词抢走命中
    return sorted(set(out), key=lambda kv: -len(kv[0]))


def triage(db_path: Path, meta_path: Path | None = None,
           max_turns: int = 3, limit: int | None = None,
           include_deep: bool = False) -> dict:
    """对"不属于任何主题的会话"分诊。

    `max_turns`：**零散判定**的适用范围（user 回合 ≤ 该值）。深会话默认不进池，
    因为多轮打磨容易被"前后不连贯"之类的弱信号误判成零散。

    `include_deep=True`（v0.39）：**把深会话也捞出来，但单列成两类**，永不参与零散
    判定——`deep_topic_hint`（机械命中现有主题 → 归位候选）/ `deep_unassigned`（无命中）。
    为什么需要：`max_turns=3` 的副作用是**深会话永远进不了归位视野**——真库实测
    池子 505 → 821（+316，其中 53 条已机械命中现有主题却从未归位）。改默认值会让
    噪声判定被多轮会话污染，故用"单列 + 只提示"来补这个洞。
    """
    import json
    known: set[str] = set()
    kw_index: list[tuple[str, str]] = []
    if meta_path and Path(meta_path).is_file():
        mcon = sqlite3.connect(f"file:{Path(meta_path)}?mode=ro", uri=True)
        mcon.row_factory = sqlite3.Row
        try:
            for r in mcon.execute("SELECT members FROM topics"):
                known |= {m["sid"] for m in json.loads(r["members"] or "[]")}
        finally:
            mcon.close()
        kw_index = _topic_keyword_index(Path(meta_path))

    only = __import__("harvester.consolidate", fromlist=["noise_sids"]) \
        .noise_sids(meta_path) if meta_path else set()

    con = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows: list[dict] = []
    try:
        # ① **一次**全扫描统计各会话的 user 回合数。
        #    上一版是 `SELECT ... (SELECT COUNT(*) FROM messages m WHERE
        #    m.sid=s.sid AND m.role='user') nu FROM sessions s`：**每个会话一次
        #    相关子查询**，而 messages 是 FTS5 虚拟表、sid 是 UNINDEXED（没有可用
        #    索引）→ 每个会话都整表扫一遍 FTS5。真库实测 1964 个会话让分诊跑到
        #    **529 秒**（v0.33 实测）；改成一次扫描后见 `docs/HANDOFF-v0.33-next.md`。
        nu_by_sid: dict[str, int] = {}
        for r in con.execute("SELECT sid FROM messages WHERE role='user'"):
            nu_by_sid[r["sid"]] = nu_by_sid.get(r["sid"], 0) + 1
        cand = [r for r in con.execute(
            "SELECT sid, source, title, created_at FROM sessions")
            if 1 <= nu_by_sid.get(r["sid"], 0)
            and (include_deep or nu_by_sid.get(r["sid"], 0) <= max_turns)
            and r["sid"] not in known and r["sid"] not in only]
        # ② 正文只取**候选会话**的，且分块 IN——不把全库 user 正文拉进内存
        #    （`sid` 无索引，IN 仍是全扫描，但分块后只扫 1~2 遍）
        by_sid: dict[str, list[str]] = {}
        sids = sorted(c["sid"] for c in cand)
        for i in range(0, len(sids), 400):
            chunk = sids[i:i + 400]
            marks = ",".join("?" * len(chunk))
            for r in con.execute(
                    f"SELECT sid, raw FROM messages WHERE role='user' "
                    f"AND sid IN ({marks}) ORDER BY sid, rowid", chunk):
                by_sid.setdefault(r["sid"], []).append(r["raw"] or "")
        for r in cand:
            turns = [t for t in by_sid.get(r["sid"], []) if t.strip()]
            if not turns:
                continue
            hint = None
            probe = f"{r['title'] or ''} {turns[0][:120]}"
            for kw, name in kw_index:
                if kw in probe:
                    hint = name
                    break
            v = classify(turns[0], turns, topic_hint=hint)
            if nu_by_sid.get(r["sid"], 0) > max_turns:
                # 深会话：**只按主题命中分层，永不参与零散判定**（多轮打磨被
                # "前后不连贯"之类的弱信号误判成零散的风险由这条口径挡掉）
                v = {**v,
                     "verdict": "deep_topic_hint" if hint else "deep_unassigned",
                     "reason": (f"深会话（user 回合 {nu_by_sid[r['sid']]} > "
                                f"{max_turns}），机械命中主题关键词：{hint}"
                                if hint else
                                f"深会话（user 回合 {nu_by_sid[r['sid']]} > "
                                f"{max_turns}），未命中任何主题关键词")}
            rows.append({"sid": r["sid"], "source": r["source"],
                         "title": (r["title"] or "")[:44],
                         "created_at": (r["created_at"] or "")[:10],
                         # 首条 user 原文（截断）：机械关键词提示会误命中，
                         # 末尾填 plan 的人/Agent 需要原文才能复核（尤其是
                         # 标题看不出内容的那些）
                         "first_user": turns[0][:200],
                         **v})
    finally:
        con.close()
    if limit:
        rows = rows[:limit]
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    return {"rows": rows, "counts": counts, "scanned": len(rows),
            "max_turns": max_turns, "include_deep": include_deep}


def render_triage(t: dict) -> str:
    label = {"noise_high": "高置信零散", "noise_maybe": "待定（查询型但成串）",
             "topic_hint": "疑似归主题", "craft_material": "创作素材型",
             "substantive": "实质会话",
             "deep_topic_hint": "深会话·疑似归主题",
             "deep_unassigned": "深会话·未归主题"}
    L = ["# 零散分诊报告（noise triage）", "",
         f"- 扫描范围：不属于任何主题、且 user 回合 ≤ {t['max_turns']} 的会话"
         + ("；**含深会话**（单列两类，不参与零散判定）"
            if t.get("include_deep") else ""),
         f"- 扫描 {t['scanned']} 条；判定分布："
         + "、".join(f"{label[k]} {v}" for k, v in
                     sorted(t["counts"].items(), key=lambda kv: -kv[1])),
         "- 判据：查询型意图（无整合诉求）+ 单轮或前后不连贯 → 只为取信息",
         "- **本报告只给判定与理由，登记与否由 plan 决定**", ""]
    for v in ("noise_high", "noise_maybe", "craft_material", "topic_hint",
              "deep_topic_hint", "deep_unassigned"):
        sub = [r for r in t["rows"] if r["verdict"] == v]
        if not sub:
            continue
        L += [f"## {label[v]}（{len(sub)} 条）", ""]
        for r in sub[:60]:
            L.append(f"- `{r['sid']}` （{r['created_at']}）{r['title']}"
                     f" ｜ {r['reason']}")
        if len(sub) > 60:
            L.append(f"- …另有 {len(sub) - 60} 条")
        L.append("")
    return "\n".join(L)


def dump_triage(t: dict) -> dict:
    """分诊结果 → JSON 可序列化结构（**全量 rows，绝不截断**）。

    为什么必须另开一个出口：`render_triage` 每类只印 60 条，而"Agent 读包填
    plan"要看到**每一条** sid —— 照人读报告填 plan 的唯一后果是尾部条目静默
    漏掉（报告里只留一句"另有 N 条"），且漏掉的不会报错。
    """
    return {"scanned": t["scanned"], "max_turns": t["max_turns"],
            "counts": dict(t["counts"]),
            "rows": [dict(r) for r in t["rows"]]}


def render_brief(t: dict, only: str | None = None, chars: int = 60) -> str:
    """一行一条的分诊简报（填 plan 时逐条判断用；制表符分隔，便于再加工）。

    与人读报告（`render_triage`，每类截断 60 条）和机读 JSON（`dump_triage`）
    的分工：这一份是**给人/Agent 顺着看一遍**的全量形态——判定、sid、日期、
    机械命中的主题、首条原文各占一列，不截断条数（这也是它当初被写成一次性脚本
    `docs/reports/flatten-triage.py` 的原因，v0.35 收回工具本体）。

    `only`：只列某一类判定（`noise_high` / `craft_material` / `topic_hint` /
    `noise_maybe` / `substantive`）；缺省或 `*` 列全部。
    """
    rows = t["rows"] if not only or only == "*" else \
        [r for r in t["rows"] if r["verdict"] == only]
    L = [f"# 分诊简报：{len(rows)} 条"
         + (f"（只列 {only}）" if only and only != "*" else "")
         + "；全量判定计数 " + "、".join(
             f"{k} {v}" for k, v in sorted(t["counts"].items(),
                                           key=lambda kv: -kv[1])),
         "# verdict\tsid\tcreated\thint\tfirst_user"]
    for r in rows:
        hint = (r["reason"].replace("命中主题关键词：", "")
                if r["verdict"] == "topic_hint" else "")
        txt = " ".join((r.get("first_user") or "").split())[:chars]
        L.append(f"{r['verdict']}\t{r['sid']}\t{r['created_at']}\t{hint}\t{txt}")
    return "\n".join(L) + "\n"
