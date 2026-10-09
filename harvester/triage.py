# -*- coding: utf-8 -*-
"""triage 蒸馏队列：新会话入库后的确定性初筛（T1，零语义判断）。

定位（v0.13，2026-10-06）：知识卡的瓶颈不是生成而是"人翻找"。triage 在
sync 之后对索引库做纯规则初筛，产出一份"值得做卡"的候选队列——机器做
排队，人做判断，Agent 做草稿（T2 = `harvester draft` 蒸馏包 +
`cards_pending/` 草稿卡，见 docs/CARD_WORKFLOW.md）。

四类候选（全部确定性规则，可复跑）：
1. 新错误 pattern：归一化 pattern **首次出现于窗口内**且窗口内 >=min_count
   次 —— 最高优先（新坑最值得沉淀）；
2. 旧坑重现：pattern 早于窗口已存在，但窗口内再次出现 >=min_count 次
   —— 次优先（检查旧卡/建议池是否该更新）；
3. Skill 行为链候选：窗口内有成功调用且行为链非空的 skill —— workflow
   卡素材（behstats 逻辑复用）；
4. 高信号会话 Top N：msgs + 3*steps 打分（工具密集=蒸馏价值高），
   供人工泛读。

去重诚实声明：对新 pattern 只做两级机械去重 —— (a) 首次出现时间判定
（老坑不冒充新坑）；(b) 可选 --cards-root 把候选 pattern/skill 名与已有
卡片正文做归一化子串比对，命中则标记"疑似已有卡"。语义级"是否已有
等价卡片"判断不属于 T1 能力，留给人/T2。

窗口口径：ts 为 'YYYY-MM-DD HH:MM:SS' 字典序即时间序（与 report-* 一致）；
会话窗口按 sessions.updated_at 过滤（各源均为 ISO 系文本，同秒边界处
'T' 与空格分隔的差异可忽略）。
"""

from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

from .agent_suggest import _TEMPLATES, root_key_assign
from .behstats import collect_skill_invocations
from .errstats import collect_errors_from_db

_CARDS_CAP = 20        # 旧坑重现列表上限
_SAMPLE_CAP = 3        # 每个候选的锚点样例数


def _cutoff(since_days: float | None) -> str | None:
    if since_days is None:
        return None
    return time.strftime("%Y-%m-%d %H:%M:%S",
                         time.localtime(time.time() - since_days * 86400))


def _norm_for_match(text: str) -> str:
    """卡片去重用的宽松归一：小写 + 折叠空白。"""
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _load_card_texts(cards_root: Path | None) -> list[str]:
    if cards_root is None:
        return []
    root = Path(cards_root)
    if not root.is_dir():
        return []
    texts = []
    for p in sorted(root.rglob("*.md")):
        try:
            texts.append(_norm_for_match(p.read_text(encoding="utf-8")))
        except OSError:
            continue
    return texts


def collect_triage(db: Path, since_days: float | None = None,
                   cards_root: Path | None = None,
                   min_count: int = 2, top_n: int = 10,
                   con: sqlite3.Connection | None = None) -> dict:
    """产出蒸馏候选队列。返回 dict（render_triage 消费）。

    con：外部连接（api-serve 传入 mode=ro + authorizer 连接），供内部
    错误扫描 / skill 扫描 / 高信号会话查询三处复用——缺省自开普通连接
    （CLI 兼容）。传入时 db 参数仅透传给子 collector 的签名（同样忽略）。
    """
    db = Path(db)
    cutoff = _cutoff(since_days)

    # ---- 1/2. 错误 pattern（全史取 first_seen，窗口内取计数与样例）----
    errors, meta = collect_errors_from_db(db, since_days=None, con=con)
    by_pattern: dict[str, dict] = {}
    for e in errors:
        p = by_pattern.setdefault(e["pattern"], {
            "pattern": e["pattern"], "class": e["class"], "tools": set(),
            "first_seen": e["ts"] or "", "window": [],
        })
        if (e["ts"] or "") < p["first_seen"]:
            p["first_seen"] = e["ts"] or ""
        p["tools"].add(e["tool"])
        if cutoff is None or (e["ts"] or "") >= cutoff:
            p["window"].append(e)

    card_texts = _load_card_texts(cards_root)

    def _mark(p: dict) -> dict:
        # 与 skill 侧同口径：pattern 先做宽松归一（lower+折叠空白）再
        # 与卡片文本比对——此前 pattern 未归一，含大写的错误消息永远
        # 判不中已有卡（v0.18 修正）。
        known = any(_norm_for_match(p["pattern"]) in t for t in card_texts)
        # P1-2 additive：命中根因模板 id（连通分量分组渲染在
        # render_triage；模板表唯一权威 = agent_suggest._TEMPLATES）
        cregs = [(i, re.compile(reg, re.IGNORECASE))
                 for i, (reg, _t, _b, _o) in enumerate(_TEMPLATES)]
        hits = [i for i, creg in cregs
                if creg.search(p["pattern"])
                or any(creg.search(e["error"][:200]) for e in p["window"])]
        return {
            "pattern": p["pattern"], "class": p["class"],
            "tools": sorted(p["tools"]),
            "first_seen": p["first_seen"],
            "count": len(p["window"]),
            "samples": [(e["sid"], e["seq"], e["error"])
                        for e in p["window"][:_SAMPLE_CAP]],
            "known_card": known,
            "templates": hits,
        }

    new_patterns, old_patterns = [], []
    for p in by_pattern.values():
        if len(p["window"]) < min_count or not p["pattern"]:
            continue
        is_new = cutoff is None or p["first_seen"] >= cutoff
        (new_patterns if is_new else old_patterns).append(_mark(p))
    new_patterns.sort(key=lambda x: -x["count"])
    old_patterns.sort(key=lambda x: -x["count"])
    old_patterns = old_patterns[:_CARDS_CAP]

    # ---- 3. Skill 行为链候选 ----
    invocations = [i for i in collect_skill_invocations(db, con=con)
                   if cutoff is None or (i["ts"] or "") >= cutoff]
    skills: dict[str, dict] = {}
    for i in invocations:
        if not i["skill"]:
            continue
        s = skills.setdefault(i["skill"], {
            "skill": i["skill"], "n_calls": 0, "n_ok": 0,
            "sources": set(), "sample": None, "chain": [],
        })
        s["n_calls"] += 1
        s["sources"].add(i["source"])
        if i["status"] == "ok" and s["sample"] is None:
            s["sample"] = (i["sid"], i["seq"])
        if i["status"] == "ok" and i["after_tools"] and not s["chain"]:
            s["chain"] = i["after_tools"]
        if i["status"] == "ok":
            s["n_ok"] += 1
    skill_items = []
    for s in sorted(skills.values(), key=lambda x: -x["n_calls"]):
        known = any(s["skill"].lower() in t for t in card_texts)
        skill_items.append({
            "skill": s["skill"], "n_calls": s["n_calls"], "n_ok": s["n_ok"],
            "sources": sorted(s["sources"]), "sample": s["sample"],
            "chain": s["chain"], "known_card": known,
        })

    # ---- 4. 高信号会话 ----
    own = con is None
    if own:
        con = sqlite3.connect(str(db))
        con.row_factory = sqlite3.Row
    try:
        # 相关子查询写法（per-session COUNT）在大库上是 O(会话数 × 全表)，
        # 1946 会话实测 170s；改为 GROUP BY 预聚合 JOIN（各表单次全扫），
        # 结果等价、毫秒级（v0.18 修正）。
        sql = """
            SELECT se.sid, se.source, se.title,
              COALESCE(m.msgs, 0) AS msgs,
              COALESCE(m.notes, 0) AS notes,
              COALESCE(st.steps, 0) AS steps
            FROM sessions se
            LEFT JOIN (SELECT sid, COUNT(*) AS msgs,
                       SUM(role='note') AS notes
                       FROM messages GROUP BY sid) m ON m.sid = se.sid
            LEFT JOIN (SELECT sid, COUNT(*) AS steps
                       FROM steps GROUP BY sid) st ON st.sid = se.sid
        """
        params: list = []
        if cutoff is not None:
            sql += " WHERE se.updated_at >= ?"
            params.append(cutoff)
        sql += (" ORDER BY (COALESCE(m.msgs, 0) + 3 * COALESCE(st.steps, 0))"
                " DESC LIMIT ?")
        params.append(top_n)
        hot = [dict(r) | {"score": r["msgs"] + 3 * r["steps"]}
               for r in con.execute(sql, params).fetchall()]
        n_sessions = con.execute(
            "SELECT COUNT(*) FROM sessions").fetchone()[0]
    finally:
        if own:
            con.close()

    return {
        "cutoff": cutoff, "db": str(db),
        "error_count_window": meta["error_count"] if cutoff is None else
        sum(len(p["window"]) for p in by_pattern.values()),
        "new_patterns": new_patterns,
        "old_patterns": old_patterns,
        "skills": skill_items,
        "hot_sessions": hot,
        "n_sessions": n_sessions,
        # None=未配置 --cards-root；0=已配置但目录为空（二者 UI 表现不同）
        "cards_scanned": len(card_texts) if cards_root is not None else None,
    }


def render_triage(r: dict) -> str:
    """渲染蒸馏队列为 markdown。"""
    lines = ["# 蒸馏队列（triage）", ""]
    scope = "全库" if r["cutoff"] is None else f"窗口 ≥ {r['cutoff']}"
    lines.append(f"- 索引库: `{r['db']}`（{r['n_sessions']} 会话）")
    lines.append(f"- 口径: {scope}；已有卡片比对: "
                 f"{'未启用' if r['cards_scanned'] is None else str(r['cards_scanned']) + ' 个文件'}")
    lines.append("")

    # ---- A 节：按根因组分块渲染（P1-2）----
    # 同根因（连通分量，root_key_assign）的 pattern 合并展示为一组，
    # 组头注明归并口径；未命中模板的 pattern 保持独立块（原样式）。
    lines.append("## A. 新错误 pattern（优先做 pitfall 卡）")
    if not r["new_patterns"]:
        lines.append("窗口内无首次出现的错误 pattern。")
    key_of = root_key_assign([(p["pattern"], p.get("templates", []))
                              for p in r["new_patterns"]])
    titles = {i: t for i, (_r_, t, _b, _o) in enumerate(_TEMPLATES)}
    grouped: dict[int | None, list[dict]] = {}
    for p in r["new_patterns"]:
        grouped.setdefault(key_of[p["pattern"]], []).append(p)
    for gid, pats in grouped.items():
        multi = gid is not None and len(pats) > 1
        if multi:
            total = sum(p["count"] for p in pats)
            lines.append(f"\n### 同根因组：{titles[gid]}"
                         f"（{len(pats)} 个 pattern 归并 · 共 {total} 次）")
        for p in pats:
            dup = " ⚠️疑似已有卡" if p["known_card"] else ""
            if multi:
                lines.append(f"\n#### x{p['count']} `{p['pattern']}`{dup}")
            else:
                lines.append(f"\n### {p['pattern']}{dup}")
            lines.append(f"- 类别 {p['class']} · 工具 {'/'.join(p['tools'])}"
                         f" · 窗口内 {p['count']} 次 · 首现 {p['first_seen']}")
            for sid, seq, err in p["samples"]:
                lines.append(f"- 锚点 `{sid}#{seq}`: {err[:120]}")
            sid0 = p["samples"][0][0] if p["samples"] else ""
            lines.append(f"- 动作: `python -m harvester cards new --sid "
                         f"{sid0} --type pitfall`")
    lines.append("")

    lines.append("## B. 旧坑重现（检查旧卡/建议池是否要更新）")
    if not r["old_patterns"]:
        lines.append("无。")
    for p in r["old_patterns"]:
        lines.append(f"- {p['pattern']}（{p['class']}，窗口内 "
                     f"{p['count']} 次，首现 {p['first_seen']}）")
    lines.append("")

    lines.append("## C. Skill 行为链候选（workflow 卡素材）")
    if not r["skills"]:
        lines.append("窗口内无 skill 调用。")
    for s in r["skills"]:
        dup = " ⚠️疑似已有卡" if s["known_card"] else ""
        sample = f"`{s['sample'][0]}#{s['sample'][1]}`" if s["sample"] else "—"
        chain = " → ".join(s["chain"]) if s["chain"] else "（链为空）"
        lines.append(f"- **{s['skill']}**{dup}：{s['n_calls']} 次调用"
                     f"（成功 {s['n_ok']}）· 来源 {'/'.join(s['sources'])}"
                     f" · 成功样例 {sample}")
        lines.append(f"  - 行为链: {chain}")
    lines.append("")

    lines.append("## D. 高信号会话 Top（泛读排期用）")
    if not r["hot_sessions"]:
        lines.append("无会话。")
    for h in r["hot_sessions"]:
        lines.append(f"- [{h['score']}] {h['sid']} · {h['title'][:40]}"
                     f"（{h['msgs']} 消息 / {h['steps']} 步 / "
                     f"{h['notes']} note）")
    lines.append("")
    lines.append("---")
    lines.append("纪律提醒：本队列是建议池，不是成品——机器只排队，"
                 "判断与 evidence 由人补全，`cards validate` 通过后才并入主库。")
    return "\n".join(lines) + "\n"
