# -*- coding: utf-8 -*-
"""打包（`llm-pack`）：把「现在判断不了的东西」连证据一起交给人/LLM 评审。

用户 2026-10-23 的原则：「数据在、LLM 可能不足 → 要给数据导出接口」。
用户 2026-10-24 裁决：**`evidence-pack` 并入 `llm-pack`**（两条命令此前各有一套
取数与渲染，改一边忘另一边）。现在只有这一个打包器：

- 面（`--faces`）：`errors` / `tools` / `skills` / `chains` / `attrib`；
- 窗口（`--since`）与已裁决标注（`--meta`）对所有面生效（不支持的会写在边界里）；
- 两个出口：`--json`（机读，schema=`llm-pack/1`，含判定槽）+ `--out`（人读 md）；
- `evidence-pack` 保留为**兼容别名**（内部走同一打包器，stderr 提示已弃用）。

━━ 三条纪律 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. **只读**：库全程 `mode=ro`；裁决只能回填既有台账（`attrib-review`），
   本包不造第二条写路径。
2. **判定槽必须自洽**：`slot.entries` 与 `items` 一一对应（`--validate` 会红），
   否则交回来的东西对不上号。
3. **没有证据的项不入包**：`facts` 里必须有可回溯的东西（错误串/工具名/审计文案），
   只给计数的行等于让人猜（R1 数据给再强的 LLM 也评不出东西）。
"""
from __future__ import annotations

import json
from pathlib import Path

from .attrib import (ATTRIB_VERDICTS, attribute_face,
                     collect_attributions, load_rules, load_verdict_map)
from .dbmeta import db_fingerprint
from .errstats import collect_errors_from_db
from .toolstats import collect_stats_from_db

#: 包格式版本（`--validate` 认这一个；改结构就升版本，别让旧包悄悄混进来）。
SCHEMA = "llm-pack/1"

#: 判定槽的列（LLM 只填后四列；前两列是身份，回填时用来对号）。
SLOT_COLUMNS = ("item_id", "subject", "verdict", "rule_id", "class", "note")

#: 支持自动收集的面。chains 走**文件审计**（chain 根目录 → audit_chain），
#: 不是库里的行；skills 走**结构化函数**（behstats），两者都不 parse 文本。
#: attrib（P10 合并进本模块）＝错误面 + 规则候选 + 是否已被人工裁决。
AUTO_FACES = ("errors", "tools", "skills", "chains", "attrib")

HINT = ("本包给 LLM 用：每条 = 事实（不许改）+ 现有归因候选（可能错）+ 一个空的判定槽。"
        "请逐条复核候选，把结论填进 slot.entries 的 verdict/rule_id/class/note；"
        "拿不准就填 unknown 并在 note 写缺少什么证据——**不要编根因**。")


def build_pack(db, faces=("errors",), limit: int = 40, rules=None,
               meta: Path | None = None,
               chain_root: Path | str | None = None,
               since_days: float | None = None) -> dict:
    """收集待判项。`faces` 只能是 `AUTO_FACES` 的子集（其它面 → ValueError）。

    `since_days`：只取最近 N 天（`errors`/`attrib`/`skills` 生效；
    `tools`/`chains` 是累计/文件口径，不受窗口影响——边界里写明）。
    `meta`：归因裁决台账（`attrib_meta.db`）；给了就把"已裁决"标在 items 上。
    """
    dbp = Path(db)
    want = tuple(faces) or ("errors",)
    bad = [f for f in want if f not in AUTO_FACES]
    if bad:
        raise ValueError(f"faces 不支持自动收集: {bad}（可选 {list(AUTO_FACES)}）")
    if "chains" in want and not chain_root:
        raise ValueError("faces 含 chains 时必须给 chain_root（chain 文件根目录）")
    if limit <= 0:
        raise ValueError("limit 必须为正数")

    rules = rules if rules is not None else load_rules()
    items: list[dict] = []

    if "errors" in want:
        errors, _ = collect_errors_from_db(dbp, since_days=since_days)
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

    if "attrib" in want:
        # 归因面＝错误面 + 「这条候选有没有被人裁过」。裁决台账只读，
        # 不给就是零感知（同 card_review 口径：没人裁过 = 全部待评）。
        errors, _ = collect_errors_from_db(dbp, since_days=since_days)
        agg = collect_attributions(errors, rules)
        vmap = load_verdict_map(meta)
        rows = sorted(agg["by_pattern"].values(), key=lambda r: -r["count"])
        for i, r in enumerate(rows[:limit], 1):
            rule = r["rule"]
            v = (vmap.get((r["pattern"], (rule or {}).get("rule_id", "")))
                 if rule else None) or {}
            items.append({
                "item_id": "A-%03d" % i,
                "face": "attrib",
                "subject": r["pattern"],
                "facts": {"count": r["count"], "given_up": r["given_up"],
                          "tools": sorted(r["tools"]),
                          "anchors": r["anchors"][:3],
                          "reviewed": v.get("verdict") or "",
                          "rule_gap": rule is None},
                "attrib": rule,
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
        items.extend(_skill_items(dbp, rules, limit, since_days=since_days))

    if "chains" in want:
        items.extend(_chain_items(dbp, rules, limit, Path(chain_root)))

    entry_of = {it["item_id"]: it for it in items}
    fp = db_fingerprint(dbp)
    return {
        "schema": SCHEMA,
        "generated_at": _now(),
        "window": (f"最近 {since_days:g} 天" if since_days else "全库（无时间窗）"),
        "since_days": since_days,
        "db_fingerprint": fp,
        "hint": HINT,
        "faces": list(want),
        "n_items": len(items),
        "items": items,
        "coverage": _coverage(items, want, fp.get("sessions")),
        "boundaries": _boundaries(want, since_days, meta),
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


def _skill_items(db, rules, limit: int, since_days=None) -> list[dict]:
    """skill 面：按调用聚合，只给**有证据**的行（锚点 + 行为链 + 计数）。

    `subject` 刻意只写事实（调用/成功/失败 + 是否低样本），**不写「待鉴定」**
    ——那是判断，判断归 LLM/人填进槽里。低样本线复用 `toolstats.RANK_MIN_CALLS`
    （同一个"样本不足 20 不下结论"口径，避免第二套数字）。
    `since_days`：按调用时刻过滤（与错误面同一窗口口径）。
    """
    from .behstats import collect_skill_invocations
    from .toolstats import OK_STATUS, RANK_MIN_CALLS

    invs = collect_skill_invocations(Path(db))
    if since_days:
        import time as _t
        cutoff = _t.strftime("%Y-%m-%d %H:%M:%S",
                             _t.localtime(_t.time() - since_days * 86400))
        invs = [i for i in invs if str(i.get("ts") or "") >= cutoff]
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


def _chain_items(db, rules, limit: int, chain_root: Path) -> list[dict]:
    """chains 面：**文件审计**（不在库里）——chain 根目录里每份 `.md` 跑一次 audit。

    证据三件（缺一件就不该进包）：
      ① 引文（quotes）：正文引号是否能在库消息里逐字命中、未命中几条；
      ② 锚点（anchors）：`sid#turn` 的可疑清单（= 点开就能核对）；
      ③ 覆盖（coverage）：成员里有多少在库内有原文。
    `subject` 只写事实（成员数/引文命中/可疑锚点/覆盖），**不写「待裁决」**
    ——那是判断，进槽。审计口径复用 `chainaudit.audit_chain`（不另写一套）。

    截断（未文档化的坑，2026-10-23 由测试会话点出后写在这里）：
    `facts.anchors.suspicious` 只留前 5 条、`facts.quotes.misses` 只留前 3 条，
    但**计数是全量**——`anchors.n_suspicious` 是全量可疑数，未命中条数用
    `quotes.total - quotes.hit` 反推，别数清单长度。
    """
    from .chainaudit import audit_chain
    from .topicchain import load_chain

    if not chain_root.is_dir():
        raise ValueError(f"chain_root 不是目录: {chain_root}")
    # **只认真 chain 文件**（frontmatter 有 members 列表）：`knowledge/topics/`
    # 里同时放着 01a/01b 这类"主题文档"，它们没有 members——照单全收会把
    # 「引文 0/0、成员 0」的假条目塞进包（静默无价值，R1 病）。
    chains = []
    for path in sorted(chain_root.glob("*.md")):
        try:
            fm = load_chain(path).get("fm") or {}
        except Exception as e:  # noqa: BLE001 - 单个坏文件不能拖垮整包
            # **缺依赖要响亮（H9）**：`load_chain` 在无 PyYAML 时抛
            # RuntimeError("…需要 PyYAML…")，若也当"不是 chain"吞掉，用户
            # 看到的是「没有真 chain 文件」——指向目录内容，真因却是环境缺库。
            # 这类错误原样上抛，别改写成"目录里没东西"。
            if "pyyaml" in str(e).lower() or "yaml" in str(e).lower():
                raise
            continue
        if isinstance(fm.get("members"), list) and fm["members"]:
            chains.append(path)
    if not chains:
        raise ValueError(f"chain_root 里没有真 chain 文件（frontmatter 带 members）: "
                         f"{chain_root}")
    out: list[dict] = []
    for n, path in enumerate(chains[:limit], 1):
        a = audit_chain(path, Path(db), quotes=True, anchors=True, coverage=True)
        q = a.get("quotes") or {}
        an = a.get("anchors") or {}
        cv = a.get("coverage") or {}
        n_q = int(q.get("total") or 0)
        hit = int(q.get("exact") or 0) + int(q.get("partial") or 0)
        susp = an.get("suspicious") or []
        mem = int(cv.get("members") or a.get("members") or 0)
        anchored = int(cv.get("anchored_members") or 0)
        subject = ("chain %s｜成员 %d｜引文 %d/%d 命中｜可疑锚点 %d｜有锚点成员 %d/%d"
                   % (a.get("topic") or path.stem, mem, hit, n_q, len(susp),
                      anchored, mem))
        out.append({
            "item_id": "C-%03d" % n,
            "face": "chains",
            "subject": subject,
            "facts": {"file": path.name, "topic": a.get("topic"),
                      "members": mem,
                      "quotes": {"total": n_q, "hit": hit,
                                 "ok": bool(q.get("ok")),
                                 "vacuous": bool(q.get("vacuous")),
                                 "misses": (q.get("misses") or [])[:3]},
                      "anchors": {"n_suspicious": len(susp),
                                  "suspicious": susp[:5]},
                      "coverage": {"anchored_members": anchored,
                                   "uncovered_reps": len(
                                       cv.get("uncovered_reps") or []),
                                   "body_only": len(cv.get("body_only") or [])},
                      "audit_ok": bool(a.get("ok"))},
            "attrib": attribute_face(subject, rules, face="chains"),
        })
    return out


def _coverage(items: list[dict], want: tuple, sessions_total) -> dict | None:
    """覆盖度：**只对错误/归因面有意义**（它们来自库里的错误步骤）。

    其它面（tools 累计 / chains 文件 / skills 调用）分母不是"会话"，硬套
    一个百分比就是拿错的数冒充覆盖率——返回 None 并在边界里说明。
    """
    if not ({"errors", "attrib"} & set(want)):
        return None
    sids = set()
    for it in items:
        for a in (it.get("facts") or {}).get("anchors") or []:
            sid = a.get("sid") if isinstance(a, dict) else None
            if sid:
                sids.add(str(sid))
    total = int(sessions_total or 0)
    covered = len(sids)
    pct = (covered / total * 100.0) if total else 0.0
    return {"covered": covered, "total": total, "pct": round(pct, 1),
            "basis": "窗口内有错误步骤的会话",
            "note": ("**本包只覆盖上述会话**；覆盖度由库现算，不是估计值。"
                     "拿部分覆盖的结果当全库结论是本工程明令禁止的事。")}


def _boundaries(want: tuple, since_days, meta) -> list[str]:
    """本包的**边界声明**（诚实：没拿到的东西写出来，别让评审者以为有）。"""
    out = [
        "归因候选是**机器判断，会错**（candidate=true）；判定只进台账，"
        "本包不含任何已定论。",
        "`artifact` 类**不在本包**：产物面只登记 dsh 源（实测个位数百分比），"
        "打进评审包等于拿它冒充全库。",
    ]
    if {"tools", "chains"} & set(want):
        out.append("`tools`（累计调用）与 `chains`（chain 文件审计）**不按 "
                   "`--since` 截窗**：它们的分母不是「这 N 天」，套窗口会算出"
                   "两个对不上的数。")
    if "skills" in want:
        out.append("Skill 面是 R1：能计数、能回原文，但**没有归因候选**，"
                   "评不出根因。")
    if "chains" in want:
        out.append("链型判定阈值是项目自定义口径（`chainstats`），评审时请"
                   "连同阈值一起看；锚点是**会话级**（`sid#0`），跳不到确切回合。")
    if "attrib" in want and meta:
        out.append("`facts.reviewed` 非空 = 这条候选**已经人裁过**（台账只读"
                   "标注，不改变候选内容）。")
    if since_days:
        out.append(f"窗口：最近 {since_days:g} 天；窗口外的老坑不在本包里，"
                   "别据此说「这类问题已经没有了」。")
    return out


def render_md(pack: dict) -> str:
    """人读出口：可直接贴给任意 Agent 评审的 Markdown。**不截断**。"""
    L: list[str] = []
    faces = "、".join(pack.get("faces") or [])
    L.append(f"# 评审包 · {faces}（{pack.get('n_items', 0)} 条）")
    L.append("")
    L.append(f"- 生成时间：{pack.get('generated_at')}")
    L.append(f"- 窗口：{pack.get('window')}")
    fp = pack.get("db_fingerprint") or {}
    L.append(f"- 库快照：会话 {fp.get('sessions')} / 步骤 {fp.get('steps')} / "
             f"错误 {fp.get('errors')}（{fp.get('db_mtime')}）")
    cov = pack.get("coverage")
    if cov:
        L.append(f"- 覆盖：{cov['covered']} / {cov['total']} 个会话"
                 f"（{cov['pct']}%，{cov['basis']}）——**本包不代表全库**")
    L.append("")
    L.append("## 边界（先读这段，别把「没给」当成「没问题」）")
    L.append("")
    for b in pack.get("boundaries") or []:
        L.append(f"- {b}")
    L.append("")
    L.append("## 待判项（事实不许改，只填判定槽）")
    L.append("")
    for it in pack.get("items") or []:
        facts = it.get("facts") or {}
        L.append(f"### {it['item_id']} · {it['subject']}（{it.get('face')}）")
        kv = "；".join(f"{k}={_fmt(v)}" for k, v in facts.items()
                       if k != "anchors")
        if kv:
            L.append(f"- 事实：{kv}")
        anchors = facts.get("anchors") or []
        if anchors:
            L.append("- 锚点：" + "、".join(_fmt_anchor(a) for a in anchors))
        a = it.get("attrib")
        if a:
            L.append(f"- 候选归因：`{a.get('rule_id')}`（{a.get('class')}，"
                     f"置信 {a.get('confidence')}）")
            L.append(f"  - 为什么：{a.get('why')}")
            L.append(f"  - 怎么办：{a.get('fix')}")
        else:
            L.append("- 候选归因：（无——规则还没覆盖到，这本身就是待办）")
        L.append("- 判定槽：待填（verdict / rule_id / class / note 四列）")
        L.append("")
    slot = pack.get("slot") or {}
    L.append("## 判定槽（整表；回填用 `readback.command`）")
    L.append("")
    L.append("| " + " | ".join(slot.get("columns") or []) + " |")
    L.append("|" + "---|" * len(slot.get("columns") or []))
    for e in slot.get("entries") or []:
        # 判定槽的空格必须**真是空的**（渲染成「—」会让"没填"看起来像填了值）
        L.append("| " + " | ".join(str(e.get(c) or "")
                                   for c in slot.get("columns") or []) + " |")
    L.append("")
    rb = pack.get("readback") or {}
    L.append("## 怎么回填")
    L.append("")
    L.append(f"- 逐条命令：`{rb.get('command')}`")
    L.append(f"- {rb.get('note')}")
    L.append("")
    return "\n".join(L)


def _fmt(v) -> str:
    if v is None or v == "":
        return "—"
    if isinstance(v, (list, tuple)):
        return "、".join(str(x) for x in v) or "—"
    return str(v)


def _fmt_anchor(a) -> str:
    if isinstance(a, dict):
        return f"`{a.get('sid')}#{a.get('seq')}`"
    return f"`{a}`"
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


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