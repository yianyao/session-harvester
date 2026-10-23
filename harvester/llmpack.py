# -*- coding: utf-8 -*-
"""LLM 打包（`llm-pack`，第④b步）：把「现在判断不了的东西」连证据一起交给 LLM。

用户 2026-10-23 的原则：「数据在、LLM 可能不足 → 要给数据导出接口」。
`export-analysis` 是**人读/机读的数据导出**，`evidence-pack` 是**评审包**；
本模块补第三种用途：**给 LLM 干的活**——每条待判项 = 事实 + 现有候选 + 判定槽，
LLM 只负责"复核候选 + 填槽"，事实层不许它改。

━━ 三条纪律 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. **只读**：库全程 `mode=ro`；裁决只能回填既有台账（`attrib-review`），
   本包不造第二条写路径。
2. **判定槽必须自洽**：`slot.entries` 与 `items` 一一对应（`--validate` 会红），
   否则 LLM 交回来的东西对不上号。
3. **没有证据的项不入包**：`facts` 里必须有可回溯的东西（错误串/工具名/审计文案），
   只给计数的行等于让 LLM 猜（R1 数据给再强的 LLM 也评不出东西）。
"""
from __future__ import annotations

import json
from pathlib import Path

from .attrib import (ATTRIB_VERDICTS, FACES, attribute_face,
                     collect_attributions, load_rules)
from .dbmeta import db_fingerprint
from .errstats import collect_errors_from_db
from .toolstats import collect_stats_from_db

#: 包格式版本（`--validate` 认这一个；改结构就升版本，别让旧包悄悄混进来）。
SCHEMA = "llm-pack/1"

#: 判定槽的列（LLM 只填后四列；前两列是身份，回填时用来对号）。
SLOT_COLUMNS = ("item_id", "subject", "verdict", "rule_id", "class", "note")

#: 支持自动收集的面。chains 仍缺（它要 chain 根目录里的文件，不是数据库里的行），
#: 见 README「已知未做」；skills 已走**结构化函数**（behstats），不从文本里 parsing。
AUTO_FACES = ("errors", "tools", "skills")

HINT = ("本包给 LLM 用：每条 = 事实（不许改）+ 现有归因候选（可能错）+ 一个空的判定槽。"
        "请逐条复核候选，把结论填进 slot.entries 的 verdict/rule_id/class/note；"
        "拿不准就填 unknown 并在 note 写缺少什么证据——**不要编根因**。")


def build_pack(db, faces=("errors",), limit: int = 40, rules=None,
               meta: Path | None = None) -> dict:
    """收集待判项。`faces` 只能是 `AUTO_FACES` 的子集（其它面 → ValueError）。"""
    dbp = Path(db)
    want = tuple(faces) or ("errors",)
    bad = [f for f in want if f not in AUTO_FACES]
    if bad:
        raise ValueError(f"faces 不支持自动收集: {bad}（可选 {list(AUTO_FACES)}）")
    bad2 = [f for f in want if f not in FACES]
    if bad2:
        raise ValueError(f"未知面: {bad2}（可选 {list(FACES)}）")
    if limit <= 0:
        raise ValueError("limit 必须为正数")

    rules = rules if rules is not None else load_rules()
    items: list[dict] = []

    if "errors" in want:
        errors, _ = collect_errors_from_db(dbp)
        agg = collect_attributions(errors, rules)
        rows = sorted(agg["by_pattern"].values(), key=lambda r: -r["count"])
        for i, r in enumerate(rows[:limit], 1):
            items.append({
                "item_id": "E-%03d" % i,
                "face": "errors",
                "subject": r["pattern"],
                "facts": {"count": r["count"], "given_up": r["given_up"],
                          "tools": sorted(r["tools"]),
                          "anchors": r["anchors"][:3]},
                "attrib": r["rule"],
            })

    if "tools" in want:
        stats, _ = collect_stats_from_db(dbp)
        rows = sorted((s for s in stats.items() if s[1].error > 0),
                      key=lambda kv: -kv[1].error)
        for i, (name, s) in enumerate(rows[:limit], 1):
            items.append({
                "item_id": "T-%03d" % i,
                "face": "tools",
                "subject": name,
                "facts": {"calls": s.calls, "error": s.error,
                          "fail_rate": round(s.fail_rate, 4),
                          "given_up": s.given_up},
                "attrib": attribute_face(name, rules, face="tools"),
            })

    if "skills" in want:
        # 结构化取数（behstats 扫 steps 表，含配对状态与行为链），**不 parse 文本**：
        # 用户批准的 G2 路线就是"先接结构函数"。证据三件：调用数、样本锚点、
        # 行为链——只给一个 skill 名的行等于让 LLM 猜（R1）。
        items.extend(_skill_items(dbp, rules, limit))

    entry_of = {it["item_id"]: it for it in items}
    return {
        "schema": SCHEMA,
        "generated_at": _now(),
        "db_fingerprint": db_fingerprint(dbp),
        "hint": HINT,
        "faces": list(want),
        "n_items": len(items),
        "items": items,
        "ask": ("逐条复核 items[].attrib（why/fix/confidence 都是**候选**）。"
                "判定词只用 %s。事实字段（facts）不许改。" % "/".join(ATTRIB_VERDICTS)),
        "slot": {
            "columns": list(SLOT_COLUMNS),
            "entries": [{"item_id": it["item_id"], "subject": it["subject"],
                         "verdict": "", "rule_id": it["attrib"]["rule_id"]
                         if it["attrib"] else "",
                         "class": it["attrib"]["class"] if it["attrib"] else "",
                         "note": ""} for it in items],
        },
        "readback": {
            # `attrib-review` 是**逐条**命令（没有批量导入）——别在这里假装有。
            "command": ("python -m harvester attrib-review --pattern <subject> "
                        "--rule <rule_id> --verdict <verdict> --note <note>"),
            "one_per_entry": True,
            "verdicts": list(ATTRIB_VERDICTS),
            "note": ("每条判定槽对应一条命令（pattern 用 items[].subject）；"
                     "裁决只进 `attribations` 台账（三层分离：事实/候选/裁决），"
                     "包本身不回写任何数据。"),
            "items_by_id": list(entry_of),
        },
    }


def _skill_items(db, rules, limit: int) -> list[dict]:
    """skill 面：按调用聚合，只给**有证据**的行（锚点 + 行为链 + 计数）。

    `subject` 刻意只写事实（调用/成功/失败 + 是否低样本），**不写「待鉴定」**
    ——那是判断，判断归 LLM/人填进槽里。低样本线复用 `toolstats.RANK_MIN_CALLS`
    （同一个"样本不足 20 不下结论"口径，避免第二套数字）。
    """
    from .behstats import collect_skill_invocations
    from .toolstats import OK_STATUS, RANK_MIN_CALLS

    invs = collect_skill_invocations(Path(db))
    agg: dict[str, dict] = {}
    for i in invs:
        if not i.get("skill"):
            continue
        a = agg.setdefault(i["skill"], {
            "skill": i["skill"], "calls": 0, "ok": 0, "err": 0,
            "other": 0, "sources": set(), "anchor": None, "chain": []})
        a["calls"] += 1
        st = i.get("status") or ""
        if st in OK_STATUS:
            a["ok"] += 1
            if a["anchor"] is None:
                a["anchor"] = {"sid": i["sid"], "seq": i["seq"], "probe": "skill 成功调用"}
                a["chain"] = list(i.get("after_tools") or [])
        elif st in ("error", "failed"):
            a["err"] += 1
        else:
            a["other"] += 1
        a["sources"].add(i.get("source") or "?")

    rows = sorted(agg.values(), key=lambda a: (-a["calls"], a["skill"]))
    out: list[dict] = []
    for n, a in enumerate(rows[:limit], 1):
        low = a["calls"] < RANK_MIN_CALLS
        subject = ("skill %s｜调用 %d 次｜成功 %d｜失败 %d%s"
                   % (a["skill"], a["calls"], a["ok"], a["err"],
                      "（低样本）" if low else ""))
        out.append({
            "item_id": "S-%03d" % n,
            "face": "skills",
            "subject": subject,
            "facts": {"skill": a["skill"], "calls": a["calls"], "ok": a["ok"],
                      "error": a["err"], "other": a["other"],
                      "low_sample": low,
                      "sources": sorted(a["sources"]),
                      "chain": a["chain"][:6],
                      "anchors": [a["anchor"]] if a["anchor"] else []},
            "attrib": attribute_face(subject, rules, face="skills"),
        })
    return out


def _now() -> str:
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def validate_pack(pack: dict) -> list[str]:
    """结构校验（`--validate` 用）。返回问题列表；空 = 合格。

    校验的是**回填能不能对上号**，不是判定内容对不对（那只有人能判）。
    """
    p: list[str] = []
    if not isinstance(pack, dict):
        return ["包必须是 JSON 对象"]
    if pack.get("schema") != SCHEMA:
        p.append(f"schema 必须是 {SCHEMA}，实际 {pack.get('schema')!r}")
    items = pack.get("items")
    slot = pack.get("slot") or {}
    entries = slot.get("entries")
    if not isinstance(items, list):
        return p + ["items 必须是列表"]
    if not isinstance(entries, list):
        return p + ["slot.entries 必须是列表"]
    if len(entries) != len(items):
        p.append(f"slot.entries {len(entries)} 条 ≠ items {len(items)} 条")
    ids = [it.get("item_id") for it in items]
    if len(set(ids)) != len(ids):
        p.append("items 的 item_id 有重复")
    subj = {it.get("item_id"): it.get("subject") for it in items}
    if list(slot.get("columns") or []) != list(SLOT_COLUMNS):
        p.append(f"slot.columns 必须是 {list(SLOT_COLUMNS)}")
    for i, e in enumerate(entries):
        iid = e.get("item_id")
        if not isinstance(e, dict):
            p.append(f"slot.entries[{i}] 必须是对象")
            continue
        if iid not in subj:
            p.append(f"slot.entries[{i}].item_id={iid!r} 不在 items 里（回填会丢）")
            continue
        if e.get("subject") != subj[iid]:
            p.append(f"slot.entries[{i}] 的 subject 与 items 不一致（对不上号）")
        v = e.get("verdict")
        if v not in ("", None) and v not in ATTRIB_VERDICTS:
            p.append(f"slot.entries[{i}].verdict={v!r} 不在 {list(ATTRIB_VERDICTS)}")
    return p


def render_json(pack: dict) -> str:
    return json.dumps(pack, ensure_ascii=False, indent=2) + "\n"