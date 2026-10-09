# -*- coding: utf-8 -*-
"""自动聚类候选推荐器（topic-candidates 子命令）——T5。

SOP §SOP-T5：标题 n-gram + 任务签名（首条 user 消息归一 + 工具序列
top-k）产**候选**——推荐器，**绝不改注册表权威**（topics_meta 只读，
人工裁决后再 topic register）。

通用性（用户红线）：不写死任何主题/会话/skill——特征全部从库内数据
计算；已注册成员按 --topics-meta 注册表动态排除（候选聚焦未入题会话）。

算法（确定性，无第三方依赖）：
1. 特征：title 字符 bigram 集 / 首条 user raw 归一（去空白截 200 字）
   bigram 集 / steps 工具频次 top-k 集（导出型源无 steps → 特征为空）；
2. 相似度：三特征 Jaccard 加权融合，特征缺一侧时该权重退出归一
   （导出源与工具源可比）；
3. 候选对：bigram 倒排索引，仅共享 gram 的对细算（复杂度可控）；
4. 聚类：种子贪心（按相似对度数降序选种子，吸入与种子 sim≥阈值
   且未分配的会话）——避免连通分量的传递成链（H39 去重实测教训）。
"""

from __future__ import annotations

import sqlite3
import re
from collections import Counter
from pathlib import Path

# 建议关键词的默认停用 gram（中文通用虚词组合，非主题词；
# 可用 --stopwords 追加/覆盖口径）
_DEFAULT_STOP = {
    "的话", "一个", "没有", "不是", "自己", "出来", "这个", "什么",
    "可以", "我们", "就是", "还是", "一下", "时候", "问题", "怎么",
    "这样", "不能", "现在", "已经", "这个", "还有", "知道", "觉得",
}

_WEIGHTS = {"title_bg": 0.5, "fu_bg": 0.3, "tools": 0.2}

# 建议关键词只保留纯 CJK/字母 gram（排除时间戳/标点/数字碎片）
_KEYWORD_OK = re.compile(r"[\u4e00-\u9fffA-Za-z]{2,}")


def _bigrams(s: str) -> set[str]:
    s = "".join((s or "").split())
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else set()


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def task_signature(db: Path, sid: str, top_tools: int = 3) -> dict:
    """单会话任务签名：首条 user raw 归一 + 工具序列 top-k。

    单会话口径（测试与调试用）；批量路径见 `_load_features`（一次查询取全会话，
    避免逐 sid 建连的 N+1）。
    """
    con = sqlite3.connect(f"file:{Path(db)}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT raw FROM messages WHERE sid=? AND role='user'"
            " ORDER BY rowid LIMIT 1", (sid,)).fetchone()
        first_user = "".join((row[0] or "").split())[:200] if row else ""
        tools = [r[0] for r in con.execute(
            "SELECT tool FROM steps WHERE sid=?", (sid,))]
    finally:
        con.close()
    top = [t for t, _ in Counter(tools).most_common(top_tools)]
    return {"first_user_norm": first_user, "top_tools": top}


def _load_features(db: Path, sids: list[str], top_tools: int) -> dict:
    con = sqlite3.connect(f"file:{Path(db)}?mode=ro", uri=True)
    try:
        titles = dict(con.execute("SELECT sid, title FROM sessions").fetchall())
        firsts = {sid: "" for sid in sids}
        for sid, raw in con.execute(
                "SELECT sid, raw FROM messages WHERE role='user'"
                " ORDER BY rowid"):
            if sid in firsts and not firsts[sid]:
                firsts[sid] = "".join((raw or "").split())[:200]
        tools: dict[str, Counter] = {sid: Counter() for sid in sids}
        for sid, tool in con.execute("SELECT sid, tool FROM steps"):
            if sid in tools and tool:
                tools[sid][tool] += 1
    finally:
        con.close()
    feats = {}
    for sid in sids:
        feats[sid] = {
            "title_bg": _bigrams(titles.get(sid) or ""),
            "fu_bg": _bigrams(firsts.get(sid) or ""),
            "tools": {t for t, _ in tools.get(sid, Counter())
                      .most_common(top_tools)},
            "title": titles.get(sid) or "",
        }
    return feats


def _similarity(fa: dict, fb: dict, weights: dict) -> float:
    num = den = 0.0
    for key, w in weights.items():
        a, b = fa[key], fb[key]
        if not a or not b:
            continue  # 特征缺一侧 → 该权重退出（归一）
        num += w * _jaccard(a, b)
        den += w
    return num / den if den else 0.0


def _registered_sids(topics_meta: Path | None) -> set[str]:
    if not topics_meta or not Path(topics_meta).is_file():
        return set()
    con = sqlite3.connect(f"file:{Path(topics_meta)}?mode=ro", uri=True)
    try:
        import json
        out: set[str] = set()
        for (members,) in con.execute("SELECT members FROM topics"):
            for m in json.loads(members or "[]"):
                out.add(m["sid"] if isinstance(m, dict) else m)
        return out
    finally:
        con.close()


def build_candidates(db: Path, min_sim: float = 0.35, min_size: int = 2,
                     top_tools: int = 3, weights: dict | None = None,
                     topics_meta: Path | None = None,
                     stopwords: set[str] | None = None) -> dict:
    """产候选簇（只读索引库与注册表；绝不写库）。"""
    w = dict(_WEIGHTS)
    if weights:
        w.update(weights)
    con = sqlite3.connect(f"file:{Path(db)}?mode=ro", uri=True)
    try:
        sids = [r[0] for r in con.execute("SELECT sid FROM sessions")]
    finally:
        con.close()
    reg = _registered_sids(topics_meta)
    sids = [s for s in sids if s not in reg]
    feats = _load_features(db, sids, top_tools)

    # 倒排：title/fu bigram 任一共享 → 候选对
    inv: dict[str, list[str]] = {}
    for sid, f in feats.items():
        for g in f["title_bg"] | f["fu_bg"]:
            inv.setdefault(g, []).append(sid)
    pair_keys: set[tuple[str, str]] = set()
    for members in inv.values():
        if len(members) < 2 or len(members) > 200:  # 超高频 gram 无判别力
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                pair_keys.add((a, b) if a < b else (b, a))
    sims: list[tuple[float, str, str]] = []
    degree: Counter = Counter()
    for a, b in pair_keys:
        s = _similarity(feats[a], feats[b], w)
        if s >= min_sim:
            sims.append((s, a, b))
            degree[a] += 1
            degree[b] += 1

    # 种子贪心：度数降序选种子（平局按 sid 序保证确定性）
    assigned: set[str] = set()
    clusters: list[dict] = []
    sim_map: dict[tuple[str, str], float] = {(a, b): s for s, a, b in sims}
    for seed in sorted(degree, key=lambda x: (-degree[x], x)):
        if seed in assigned:
            continue
        members = [(1.0, seed)]
        assigned.add(seed)
        for s, a, b in sorted(sims, reverse=True):
            other = a if b == seed else (b if a == seed else None)
            if other is None or other in assigned:
                continue
            if sim_map.get((a, b) if a < b else (b, a), 0) >= min_sim:
                members.append((s, other))
                assigned.add(other)
        members.sort(key=lambda x: (-x[0], x[1]))
        if len(members) >= min_size:
            stop = _DEFAULT_STOP | (stopwords or set())
            grams: Counter = Counter()
            for _, sid in members:
                for g in feats[sid]["title_bg"] | feats[sid]["fu_bg"]:
                    if g in stop or not _KEYWORD_OK.fullmatch(g):
                        continue  # 日期/标点/数字碎片无主题判别力
                    grams[g] += 1
            clusters.append({
                "seed": seed, "seed_title": feats[seed]["title"],
                "members": [{"sid": sid, "title": feats[sid]["title"],
                             "sim": round(s, 3)} for s, sid in members],
                "suggested_keywords": [g for g, _ in
                                       grams.most_common(5)],
            })
    clusters.sort(key=lambda c: (-len(c["members"]), c["seed"]))
    return {"clusters": clusters, "total_sessions": len(sids),
            "excluded_registered": len(reg), "min_sim": min_sim,
            "min_size": min_size, "weights": w,
            "pairs_evaluated": len(pair_keys)}


def render_candidates(r: dict) -> str:
    lines = [
        "# 自动聚类候选（T5 推荐器——**只产候选，不改注册表**）",
        "",
        f"- 范围：{r['total_sessions']} 个未注册会话"
        f"（已排除 {r['excluded_registered']} 个注册成员）｜"
        f"min_sim={r['min_sim']}｜min_size={r['min_size']}"
        f"｜候选对 {r['pairs_evaluated']}",
        "- 候选准确率由人工判定：认可后用 `topic register --name "
        "--keywords --sids` 入注册表（本报告不代做）",
        "",
    ]
    if not r["clusters"]:
        lines += ["（无候选簇：阈值内没有成簇会话）", ""]
    for i, c in enumerate(r["clusters"], 1):
        lines += [
            f"## 候选簇 {i}｜{len(c['members'])} 会话｜"
            f"种子：{c['seed_title'] or c['seed']}",
            "",
            f"- 建议关键词：{'、'.join(c['suggested_keywords']) or '—'}",
        ]
        lines += [f"- `{m['sid']}` sim={m['sim']}｜{m['title']}"
                  for m in c["members"]]
        lines.append("")
    return "\n".join(lines)
