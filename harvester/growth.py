# -*- coding: utf-8 -*-
"""个人成长总线（P2 #12，2026-10-26 用户「第3项开工」）。

一句话：**按月看"我这段时间在推进什么、踩坑有没有变少"**，四个数列——
活跃主题 / 新卡 / 错误率 / 采纳的班规。

三条设计纪律（都在返回值里显式写出来，不靠读代码猜）：

1. **每个数字都能复算**：`sources` 写清取自哪本账的哪个字段，`definitions`
   写清口径。页面/CLI 都原样透出，不二次解释。
2. **不声称因果**。采纳班规前后错误率变化，可能来自换了工具、换了任务类型、
   样本变少……本模块只把两件事并排放在同一张月表上，`honest` 里明写
   "这是趋势不是因果"。要判断因果得另做对照（本项目没有那个条件）。
3. **薄数据如实说薄**：某月会话太少（<5）时月度比率不可比，标记 `thin`；
   没有采纳记录的月份 `rules_adopted=0` 而不是省略——省略会让人以为没算。

只读：不写任何库、不落盘（`--out` 是调用方的事）。纯标准库。
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = "growth/1"

#: 一个月会话少于此数，月度比率（错误率）不可比——不是"低"，是"样本不够"。
THIN_SESSIONS = 5

_CARD_CREATED = re.compile(r"^created:\s*[\"']?(\d{4}-\d{2})", re.M)

DEFINITIONS = {
    "sessions": "该月新建的会话数（sessions.created_at 的月份）",
    "steps": "这些会话里的工具步数（steps 按 sid 归月）",
    "errors": "这些步里出错的条数（steps.error 非空）",
    "err_per_session": "错误步数 ÷ 会话数（不是百分比：一个月 20 个会话错 4 步 = 0.20）",
    "active_topics": "该月有成员会话在活动的主题数（topics.members ∩ 该月会话）",
    "new_cards": "该月新建的知识卡片数（卡片 frontmatter 的 created）",
    "rules_adopted": "该月被采纳为班规的条数（suggestions_meta.adopted_at）",
}

SOURCES = {
    "sessions": "harvester.db · sessions.created_at",
    "steps": "harvester.db · steps.sid / steps.error",
    "errors": "harvester.db · steps.error（非空即算一条错误步）",
    "err_per_session": "由上两行现算（errors ÷ sessions），不另存一份",
    "active_topics": "topics_meta.db · topics.members（JSON 列表）",
    "new_cards": "knowledge/cards/*.md · frontmatter created（正则读，不引 PyYAML）",
    "rules_adopted": "suggestions_meta.db · suggestion_status.adopted_at",
}


def _month(s: str | None) -> str:
    """'2026-10-05 08:38:51' → '2026-10'。空值返回空串（调用方过滤）。"""
    t = (s or "").strip()
    return t[:7] if len(t) >= 7 and t[4] == "-" else ""


def _ro(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect("file:%s?mode=ro" % Path(db).as_posix(), uri=True)
    con.row_factory = sqlite3.Row
    return con


def _card_months(cards_root: Path | None) -> dict[str, int]:
    """卡片创建月分布。只读 frontmatter 的 created 行（正则，容忍引号）。"""
    out: dict[str, int] = {}
    if not cards_root or not Path(cards_root).is_dir():
        return out
    for p in sorted(Path(cards_root).glob("*.md")):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        m = _CARD_CREATED.search(text[:2000])
        if m:
            out[m.group(1)] = out.get(m.group(1), 0) + 1
    return out


def _adopted_months(sug: Path | None) -> dict[str, int]:
    out: dict[str, int] = {}
    if not sug or not Path(sug).is_file():
        return out
    con = _ro(Path(sug))
    try:
        rows = con.execute(
            "SELECT adopted_at FROM suggestion_status "
            "WHERE adopted_at IS NOT NULL AND adopted_at != ''").fetchall()
    except sqlite3.DatabaseError:
        return out
    finally:
        con.close()
    for r in rows:
        m = _month(r["adopted_at"])
        if m:
            out[m] = out.get(m, 0) + 1
    return out


def build(db: Path, *, topics_meta: Path | None = None,
          cards_root: Path | None = None,
          suggestions_meta: Path | None = None,
          months: int = 12) -> dict:
    """算月度总线。返回成长总线/1（机器读）。

    只读：三本账都以 `mode=ro` 打开；不写不删。
    """
    dbp = Path(db)
    if not dbp.is_file():
        raise FileNotFoundError("索引库不存在：%s（先跑 harvester index）" % dbp)
    if months <= 0:
        months = 12

    con = _ro(dbp)
    try:
        srows = con.execute(
            "SELECT sid, created_at FROM sessions").fetchall()
        step_rows = con.execute(
            "SELECT sid, error FROM steps").fetchall()
    finally:
        con.close()

    sid_month: dict[str, str] = {}
    sess_by_month: dict[str, int] = {}
    for r in srows:
        m = _month(r["created_at"])
        if not m:
            continue
        sid_month[r["sid"]] = m
        sess_by_month[m] = sess_by_month.get(m, 0) + 1
    steps_by_month: dict[str, int] = {}
    errs_by_month: dict[str, int] = {}
    for r in step_rows:
        m = sid_month.get(r["sid"])
        if not m:
            continue
        steps_by_month[m] = steps_by_month.get(m, 0) + 1
        if (r["error"] or "").strip():
            errs_by_month[m] = errs_by_month.get(m, 0) + 1

    # 活跃主题：主题成员里有任何一条会话落在该月，即算该月活跃
    act_by_month: dict[str, int] = {}
    topics_n = 0
    if topics_meta and Path(topics_meta).is_file():
        tcon = _ro(Path(topics_meta))
        try:
            trows = tcon.execute("SELECT id, members FROM topics").fetchall()
        except sqlite3.DatabaseError:
            trows = []
        finally:
            tcon.close()
        for r in trows:
            topics_n += 1
            try:
                mem = json.loads(r["members"] or "[]")
            except ValueError:
                continue
            # members 是**对象列表**（每项 {sid, evidence}），不是纯 sid 列表
            # ——早期版本按字符串列表写会 TypeError: unhashable type: 'dict'
            # （2026-10-26 真库实测踩到）。两种形态都容忍。
            sids = [x.get("sid") if isinstance(x, dict) else x for x in mem]
            mset = {sid_month.get(s) for s in sids if sid_month.get(s)}
            for m in mset:
                act_by_month[m] = act_by_month.get(m, 0) + 1

    cards_by_month = _card_months(cards_root)
    rules_by_month = _adopted_months(suggestions_meta)

    all_months = sorted(set(sess_by_month) | set(cards_by_month)
                        | set(rules_by_month) | set(act_by_month))
    keep = all_months[-months:] if months and len(all_months) > months else all_months

    out_months = []
    for m in keep:
        n = sess_by_month.get(m, 0)
        e = errs_by_month.get(m, 0)
        out_months.append({
            "month": m,
            "sessions": n,
            "steps": steps_by_month.get(m, 0),
            "errors": e,
            "err_per_session": round(e / n, 3) if n else None,
            "active_topics": act_by_month.get(m, 0),
            "new_cards": cards_by_month.get(m, 0),
            "rules_adopted": rules_by_month.get(m, 0),
            "thin": n < THIN_SESSIONS,
        })

    honest = [
        "这是**趋势**，不是因果：采纳班规前后错误率变了，也可能只是换了工具、"
        "换了任务类型、或者样本变少。要判因果得做对照，本项目没有那个条件。",
        "错误率按「错误步数 ÷ 会话数」算，不是百分比；会话长的月份天然更容易"
        "多错几步，所以它比「错误数」更能反映密度，但也不是完美指标。",
        "活跃主题按「该月有成员会话活动」计——一个主题只要有一条会话在那月，"
        "就算活跃，不代表它推进了很多。",
    ]
    thin = [x["month"] for x in out_months if x["thin"]]
    if thin:
        honest.append("会话数 < %d 的月份（%s）比率不可比：样本不够，读作"
                      "「看不出来」比读作「变好了/变差了」更诚实。"
                      % (THIN_SESSIONS, "、".join(thin)))

    return {
        "schema": SCHEMA,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "db": str(dbp),
        "months_requested": months,
        "topics_total": topics_n,
        "months": out_months,
        "totals": {
            "sessions": sum(x["sessions"] for x in out_months),
            "errors": sum(x["errors"] for x in out_months),
            "new_cards": sum(x["new_cards"] for x in out_months),
            "rules_adopted": sum(x["rules_adopted"] for x in out_months),
        },
        "definitions": DEFINITIONS,
        "sources": SOURCES,
        "honest": honest,
    }


def render_md(g: dict) -> str:
    """人读版：一张月表 + 口径 + 诚实的边界。"""
    L = ["# 个人成长总线（按月）", "",
         "> 生成于 %s｜db=%s｜看 %d 个月"
         % (g["generated_at"], g["db"], g["months_requested"]),
         "> **这是趋势，不是因果**：只把四件事并排放在同一张月表上，"
         "不替你做归因判断。", ""]
    L.append("| 月份 | 会话 | 工具步 | 错误 | 每会话错误 | 活跃主题 | 新卡 | 采纳班规 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for x in g["months"]:
        rate = "—" if x["err_per_session"] is None else "%.2f" % x["err_per_session"]
        mark = "（样本少）" if x["thin"] else ""
        L.append("| %s | %d | %d | %d | %s | %d | %d | %d%s |"
                 % (x["month"], x["sessions"], x["steps"], x["errors"], rate,
                    x["active_topics"], x["new_cards"], x["rules_adopted"], mark))
    t = g["totals"]
    L += ["", "合计：会话 %d｜错误 %d｜新卡 %d｜采纳班规 %d"
          % (t["sessions"], t["errors"], t["new_cards"], t["rules_adopted"]), ""]
    L.append("## 口径（每个数字怎么算的）")
    for k, v in g["definitions"].items():
        L.append("- **%s**：%s（来源：%s）" % (k, v, g["sources"].get(k, "—")))
    L += ["", "## 别把这张表读过头"]
    for h in g["honest"]:
        L.append("- " + h)
    L.append("")
    return "\n".join(L)
