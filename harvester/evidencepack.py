# -*- coding: utf-8 -*-
"""评审包（evidence-pack，P6-6）：一个包，三种消费者。

对应《证据归因与可评审数据面设计-2026-10-22》§3.2 与 `_eng/SOP-attrib-review.md`。

━━ 为什么要有这个东西 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
现有数据面大多是 R1（只有计数，没有根因候选）。设计文档 §3.1 的判断是：
**R1 的数据给再强的 LLM 也评不出东西**，所以"留数据待更好的 LLM"这句
承诺，留的必须是 R2/R3。本模块把「一个数据面现在能不能评」落成**可检验的包**。

━━ 包内固定三段（每段服务一类消费者）━━━━━━━━━━━━━━━━━━━━━━━━━
① 判据头  口径 / 时间窗 / 库快照（含 STALE）/ **本包等级 R?** / 覆盖度 / 边界
          → 评审者先知道"边界在哪"，否则会拿 2% 的覆盖率当全库结论；
② 证据体  逐条：现象 + 计数 + **原文锚点**（`sid#seq`）+ 归因候选（why/fix/置信）
          → 给评审者（人或 Agent）足够的、可回溯的原料；
③ 判定槽  每条一行，`pattern_key` 已填、裁决四列留空
          → 人裁决后**原样回填台账**。

**③ 是"可评审"的定义性特征**：没有判定槽的包不是评审包，是报告。
（`TestVerdictSlot` 钉住；变异"删掉判定槽"必须变红。）

━━ 三条设计纪律 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. **等级由内容算出来，不写死**：R1 有计数无根因；R2 有根因候选；
   R3 = R2 且有回填通道（本仓 = `attrib-review` 可写台账）。
   写死的等级会随数据变化而说谎——那正是本工程要防的事。
2. **覆盖度必须现算并写进包头**（设计 §3.3）：`artifacts` 只覆盖 dsh 源 ≈2%，
   任何产物类数据面都得把这句话写在脸。
3. **只读**：本模块只读 `harvester.db`（`mode=ro`，走 `errstats.ro_con`），
   绝不写库；判定走既有台账通道（`attrib-review`），不在包里造第二条写路径。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .attrib import (ATTRIB_VERDICTS, MAX_ANCHORS,
                     collect_attributions, load_rules)
from .chainstats import collect as collect_chains
from .behstats import collect_skill_invocations
from .dbmeta import db_fingerprint
from .errstats import (clean_error_sample, collect_errors_from_db,
                       pattern_stats)

#: 本包支持的 kind。**`artifact` 故意不在内**——产物面只登记 dsh 源
#: （`artifacts.py:8` 自陈，实测 ≈2% 覆盖率），把它打进"评审包"会让
#: 评审者以为拿到了全库产物。它归 P6-8（覆盖度诚实）单独处理。
KINDS = ("errors", "skills", "chains", "attrib")

#: 判定槽的四列（`pattern_key` 由本包预填，这四列必须留给裁决者）。
SLOT_COLS = ("verdict", "note", "reviewer", "reviewed_at")

#: 等级（设计 §3.1）。R1 及以下**不得**标"可评审"。
LEVELS = ("R0", "R1", "R2", "R3")

JSON_SCHEMA = "evidence-pack/1"


def _fp(db: Path) -> dict:
    """库快照（含 STALE）。复用 `dbmeta.db_fingerprint`，不自己数。"""
    try:
        return db_fingerprint(db)
    except Exception:  # pragma: no cover - 库异常时仍要出包（标"未知"而非 0）
        return {"sessions": None, "steps": None, "errors": None,
                "db_mtime": None, "stale": None}


def _backfill_available() -> bool:
    """回填通道是否存在（= R3 的那一半）。

    只查**真解析器**里有没有 `attrib-review` 子命令——不试写台账
    （本模块只读；"能不能写"用"命令在不在"判定，与 `AGENTS.md` §三 3 同口径）。
    """
    try:
        from .cli import build_parser
        for act in build_parser()._actions:      # noqa: SLF001
            choices = getattr(act, "choices", None)
            if choices and "attrib-review" in choices:
                return True
    except Exception:  # pragma: no cover
        return False
    return False


def level_of(entries: list[dict], backfill: bool) -> str:
    """按**内容**定等级（不写死），并给出人读理由。"""
    if not entries:
        return "R0"          # 没有结构化条目 = 只有原文
    if not any(e.get("attrib") for e in entries):
        return "R1"          # 有计数与锚点，但无根因候选
    return "R3" if backfill else "R2"


def level_reason(entries: list[dict], level: str, backfill: bool) -> str:
    if level == "R0":
        return "窗口内没有可结构化的条目（只有原文，不可评审）"
    if level == "R1":
        return ("有条目计数与原文锚点，但**没有根因候选**——"
                "这种数据给再强的模型也评不出根因（设计 §3.1）")
    if level == "R2":
        return "有条目 + 原文锚点 + 归因候选（why/fix/置信）"
    return ("有条目 + 原文锚点 + 归因候选 + **回填通道**"
            "（`attrib-review` 可把裁决落进 attribations 台账）"
            if backfill else "R2（回填通道不可用）")


def _entry(key: str, title: str, count: int, anchors: list[dict],
           extra: dict | None = None, attrib: dict | None = None) -> dict:
    return {"pattern_key": key, "title": title, "count": int(count),
            "anchors": anchors[:MAX_ANCHORS], "extra": extra or {},
            "attrib": attrib}


def _anchor(sid: str, seq, text: str) -> dict:
    return {"sid": sid, "seq": int(seq or 0),
            "text": clean_error_sample(text or "", 160)}


def _anchors_from(samples, pick_text) -> list[dict]:
    """`pattern_stats` 的 samples 是 **(sid, seq, error) 三元组**（实测），
    不是 dict——这里两种形态都接住，免得上游哪天改形态就静默出空锚点。"""
    out = []
    for s in samples or []:
        if isinstance(s, (tuple, list)) and len(s) >= 2:
            out.append(_anchor(str(s[0]), s[1],
                               pick_text(s[2] if len(s) > 2 else "")))
        elif isinstance(s, dict):
            out.append(_anchor(str(s.get("sid", "")), s.get("seq"),
                               pick_text(s.get("error") or s.get("text") or "")))
        if len(out) >= MAX_ANCHORS:
            break
    return out


def _ts_cutoff(since_days: float | None) -> str:
    """与 report-*/triage 同口径的截止串（无窗口 → 空串=不过滤）。"""
    if not since_days:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S",
                         time.localtime(time.time() - since_days * 86400))


# ---------------------------------------------------------------- 取数（四类）
def _entries_errors(db, since_days, rules, meta, top) -> tuple[list[dict], dict]:
    errors, emeta = collect_errors_from_db(db, since_days=since_days)
    by_pat = pattern_stats(errors)
    atts = collect_attributions(errors, rules)
    # ⚠ `by_pattern[pat]` 是**整行**（class/count/tools/anchors/rule/rule_ids），
    # 命中规则在 `rule` 键下——不是行本身。取错键会得到"有归因但 rule_id=None"
    # 这种**看起来有、实际空**的条目（2026-10-22 实测踩到，已加对账门）。
    apat = (atts or {}).get("by_pattern") or {}
    rows = []
    for pat, st in sorted(by_pat.items(), key=lambda kv: -kv[1]["count"])[:top]:
        cand = (apat.get(pat) or {}).get("rule")
        rows.append(_entry(
            pat, pat, st["count"],
            _anchors_from(st.get("samples"), clean_error_sample),
            extra={"class": st.get("class") or "unclassified",
                   "given_up": st.get("given_up", 0)},
            attrib=cand))
    cov = {"covered": len({e.get("sid") for e in errors if e.get("sid")}),
           "basis": "窗口内有错误步骤的会话"}
    return rows, {"errors_meta": emeta, "coverage": cov,
                  "attrib_summary": (atts or {}).get("summary") or {}}


def _entries_attrib(db, since_days, rules, meta, top) -> tuple[list[dict], dict]:
    rows, info = _entries_errors(db, since_days, rules, meta, top)
    from .attrib import load_verdict_map
    vmap = load_verdict_map(meta) if meta else {}
    for r in rows:
        a = r.get("attrib")
        if a:
            v = vmap.get((r["pattern_key"], a.get("rule_id", "")))
            r["extra"]["reviewed"] = (v or {}).get("verdict") or ""
        else:
            r["extra"]["reviewed"] = ""
            r["extra"]["rule_gap"] = True
    return rows, info


def _entries_skills(db, since_days, rules, meta, top) -> tuple[list[dict], dict]:
    inv = collect_skill_invocations(Path(db))
    cutoff = _ts_cutoff(since_days)
    if cutoff:
        inv = [it for it in inv if str(it.get("ts") or "") >= cutoff]
    agg: dict[str, dict] = {}
    for it in inv:
        name = str(it.get("skill") or "（未命名）")
        cell = agg.setdefault(name, {"count": 0, "sids": set(), "bad": 0,
                                     "samples": []})
        cell["count"] += 1
        if it.get("sid"):
            cell["sids"].add(it["sid"])
        if str(it.get("status") or "") not in ("", "success", "ok"):
            cell["bad"] += 1
        if len(cell["samples"]) < MAX_ANCHORS:
            cell["samples"].append(_anchor(
                str(it.get("sid") or ""), it.get("seq"),
                str(it.get("error") or it.get("args") or "")))
    rows = []
    for name, cell in sorted(agg.items(), key=lambda kv: -kv[1]["count"])[:top]:
        rows.append(_entry(name, f"Skill：{name}", cell["count"],
                           cell["samples"],
                           extra={"sessions": len(cell["sids"]),
                                  "非成功调用": cell["bad"]}))
    cov = {"covered": len({it.get("sid") for it in inv if it.get("sid")}),
           "basis": "窗口内有 Skill 调用的会话"}
    return rows, {"coverage": cov}


def _chain_rows(group, kind: str, label: str, top: int) -> list[dict]:
    """chainstats 的四组都是**元组**（实测）：
    `long=(sid, 步数)`、`streaks=(sid, 工具, 次数, 最长)`、
    `loops=(sid, 序列, 次数, ?)`、`spins=(sid, 工具, 次数)`。
    它们**不提供 seq**，所以锚点是**会话级**（seq=0）——这一点必须写在边界里，
    不能伪装成能跳到具体回合。"""
    rows = []
    for item in (group or [])[:top]:
        if not isinstance(item, (tuple, list)) or len(item) < 2:
            continue
        sid = str(item[0] or "")
        if kind == "long":
            tool, count = "（整会话）", int(item[1] or 0)
            extra = {"chain_kind": kind, "steps": int(item[1] or 0)}
        else:
            tool = str(item[1] or "")
            count = int(item[2] or 0) if len(item) > 2 else 0
            extra = {"chain_kind": kind}
            if len(item) > 3:
                extra["detail"] = item[3]
        rows.append(_entry(
            f"{kind}:{sid}:{tool}", f"{label}：{tool}", count,
            [_anchor(sid, 0, "")], extra=extra))
    return rows


def _entries_chains(db, since_days, rules, meta, top) -> tuple[list[dict], dict]:
    d = collect_chains(Path(db), since_days=since_days, top=top)
    rows: list[dict] = []
    for kind, label in (("long", "长回合"), ("streaks", "同工具连击"),
                        ("loops", "序列循环"), ("spins", "空转")):
        rows += _chain_rows(d.get(kind), kind, label, top)
    rows.sort(key=lambda r: -r["count"])
    cov = {"covered": int(d.get("sessions") or 0),
           "basis": "有步骤序列的会话"}
    return rows[:top], {"coverage": cov}


_BUILDERS = {"errors": _entries_errors, "attrib": _entries_attrib,
             "skills": _entries_skills, "chains": _entries_chains}


def build_pack(kind: str, db: Path, since_days: float | None = None,
               rules: list[dict] | None = None, meta: Path | None = None,
               top: int = 20) -> dict:
    """组装评审包（只读）。`kind` 不在 `KINDS` 内 → `ValueError`（fail loud）。"""
    if kind not in KINDS:
        raise ValueError(f"不支持的 kind：{kind}（可选：{'|'.join(KINDS)}）")
    db = Path(db)
    rules = rules if rules is not None else load_rules()
    entries, info = _BUILDERS[kind](db, since_days, rules, meta, top)
    fp = _fp(db)
    backfill = _backfill_available() and kind in ("errors", "attrib")
    level = level_of(entries, backfill)
    cov = info.get("coverage") or {}
    total = fp.get("sessions") or 0
    covered = int(cov.get("covered") or 0)
    pct = (covered / total * 100.0) if total else 0.0
    return {
        "schema": JSON_SCHEMA,
        "kind": kind,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "window": (f"最近 {since_days:g} 天" if since_days else "全库（无时间窗）"),
        "since_days": since_days,
        "db": {"path": str(db), **fp},
        "level": level,
        "level_reason": level_reason(entries, level, backfill),
        "backfill": backfill,
        "coverage": {"covered": covered, "total": total, "pct": round(pct, 1),
                     "basis": cov.get("basis", ""),
                     "note": ("**本包只覆盖上述会话**；覆盖度由库现算，"
                              "不是估计值。拿部分覆盖的结果当全库结论是"
                              "本工程明令禁止的事。")},
        "entries": entries,
        "slot": {"cols": ["pattern_key", *SLOT_COLS],
                 "verdicts": list(ATTRIB_VERDICTS),
                 "rows": [{"pattern_key": e["pattern_key"],
                           "verdict": "", "note": "", "reviewer": "",
                           "reviewed_at": ""} for e in entries]},
        "boundaries": _boundaries(kind, info),
    }


def _boundaries(kind: str, info: dict) -> list[str]:
    """本包的**边界声明**（诚实：没拿到的东西要写出来，别让评审者以为有）。"""
    out = [
        "归因候选是**机器判断，会错**（`candidate=true`）；判定只进台账，"
        "本包不含任何已定论。",
        "`artifact` 类**不在本包**：产物面只登记 dsh 源（实测 ≈2%），"
        "打进评审包等于拿 2% 冒充全库（归 P6-8）。",
    ]
    if kind == "skills":
        out.append("Skill 面当前**没有归因候选**——所以它是 R1："
                   "可以计数与回原文，但评不出根因。")
    if kind == "chains":
        out.append("链型面**没有归因候选**（同上，R1）；"
                   "而且链型判定阈值在 `chainstats` 里是项目自定义口径，"
                   "评审时请连同阈值一起看。")
        out.append("**链型锚点是会话级的**（`sid#0`）：`chainstats` 不提供具体 "
                   "seq，所以从这里跳不到确切回合——要看原文得先按锚点进会话，"
                   "再在会话里找该工具/序列。这不装成能精确跳转。")
    if kind in ("errors", "attrib"):
        asum = info.get("attrib_summary") or {}
        if asum:
            out.append(f"归因覆盖：{asum.get('errors_attributed', 0)} / "
                       f"{asum.get('errors_total', 0)} 条错误；"
                       f"规则命中 {asum.get('rules_hit', 0)} / "
                       f"{asum.get('rules_total', 0)} 条。"
                       "**未归因 ≠ 没问题**，它只说明规则表还没覆盖到。")
    return out


# ---------------------------------------------------------------- 两个出口
def _fmt_anchors(anchors: list[dict]) -> str:
    return "、".join(f"`{a['sid']}#{a['seq']}`" for a in anchors) or "（无锚点）"


def render_md(pack: dict) -> str:
    """人读出口：可直接贴给任意 Agent 评审的 Markdown。**不截断**。"""
    db = pack["db"]
    L = []
    L.append(f"# 评审包 · {pack['kind']}（{pack['level']}）")
    L.append("")
    L.append("## ① 判据头（先读这一段：边界在这里）")
    L.append("")
    L.append(f"- 包类型：`{pack['kind']}`　生成：{pack['generated_at']}")
    L.append(f"- 时间窗：{pack['window']}")
    L.append(f"- 库快照：{db.get('sessions')} 会话 / {db.get('steps')} 步骤 / "
             f"{db.get('errors')} 错误 @ {db.get('db_mtime')}")
    stale = db.get("stale")
    if stale is True:
        L.append("- **⚠ STALE：语料比库新**——本包数字可能漏掉尚未收割的会话。")
    elif stale is False:
        L.append("- 新鲜度：语料与库一致（非 STALE）")
    else:
        L.append("- 新鲜度：**未知**（读不到语料水位）")
    L.append(f"- **本包等级：{pack['level']}**——{pack['level_reason']}")
    cov = pack["coverage"]
    L.append(f"- 覆盖度：**{cov['covered']} / {cov['total']} 会话"
             f"（{cov['pct']}%）**，口径「{cov['basis']}」")
    L.append(f"  - {cov['note']}")
    for b in pack["boundaries"]:
        L.append(f"- 边界：{b}")
    L.append("")
    L.append(f"## ② 证据体（{len(pack['entries'])} 条；每条都给原文锚点）")
    L.append("")
    if not pack["entries"]:
        L.append("- （窗口内没有条目——空包也是结论：这段时间没有可评审的现象）")
    for i, e in enumerate(pack["entries"], 1):
        L.append(f"### {i}. {e['title']}")
        L.append("")
        L.append(f"- 计数：**x{e['count']}**　键：`{e['pattern_key']}`")
        ex = e.get("extra") or {}
        if ex:
            bits = [f"{k}={v}" for k, v in ex.items() if v not in ("", None)]
            if bits:
                L.append("- 事实字段：" + "；".join(bits))
        L.append(f"- 原文锚点：{_fmt_anchors(e['anchors'])}")
        a = e.get("attrib")
        if a:
            fact_cls = (e.get("extra") or {}).get("class") or ""
            cls_note = ""
            if a.get("class") and fact_cls and a["class"] != fact_cls:
                cls_note = (f"　**类别不一致**：归因记 {a['class']}，"
                            f"事实层仍记 {fact_cls}（两个口径都留着，别只信一边）")
            L.append(f"- 归因候选：`{a.get('rule_id')}`"
                     f"（置信 {a.get('confidence') or '未标'}，"
                     f"类别 {a.get('class')}；**candidate=true，机器判断会错**）"
                     + cls_note)
            L.append(f"  - 根因：{a.get('why')}")
            L.append(f"  - 应对：{a.get('fix')}")
        else:
            L.append("- 归因：**未归因**（机器未匹配任何规则；"
                     "**不是「没问题」**）")
        L.append("")
    L.append("## ③ 判定槽（每条一行；`pattern_key` 已填，裁决四列留空）")
    L.append("")
    L.append(f"允许的裁决值：{' / '.join(pack['slot']['verdicts'])}"
             "（`revise` = 方向对但 why/fix 需改，note 写怎么改）")
    L.append("")
    L.append("| pattern_key | " + " | ".join(SLOT_COLS) + " |")
    L.append("|---|" + "---|" * len(SLOT_COLS))
    for r in pack["slot"]["rows"]:
        L.append("| `" + r["pattern_key"].replace("|", "\\|") + "` |"
                 + "  |" * len(SLOT_COLS))
    L.append("")
    if pack["backfill"]:
        L.append("回填（判定 → 台账，唯一写路径）：")
        L.append("")
        L.append("```bash")
        L.append("python -m harvester attrib-review --pattern '<pattern_key>' "
                 "--rule '<rule_id>' --verdict <accepted|rejected|revise> "
                 "--note '<怎么改/为什么>'")
        L.append("```")
        L.append("")
        L.append("裁过的归因**不再进候选**（同 `card_review` 语义）；"
                 "被移出的落「已裁决」段，不会静默消失。")
    else:
        L.append("**本包没有回填通道**：这条数据面还没有台账/动作通道，"
                 "判定只能留在本文件里（这也是它不能标「可评审」的原因）。")
    return "\n".join(L) + "\n"


def render_json(pack: dict) -> str:
    """机读出口：与 md **不共用实现**，结构即契约（带 schema）。"""
    return json.dumps(pack, ensure_ascii=False, indent=1, sort_keys=False) + "\n"