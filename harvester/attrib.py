# -*- coding: utf-8 -*-
"""归因层（attrib 子命令）——把"计数"变成"根因候选 + 应对候选"。

对应《证据归因与可评审数据面设计-2026-10-22》§2 与
`_eng/SOP-attrib-review.md` §3（纪律）、`_eng/TODO-attrib-review-P6.md` P6-1..P6-4。

━━ 三层必须分开（SOP A1，绝不可混）━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
① 事实 fact          机器算得出、零判断   → 计数 / 锚点 / 交叉表   【errstats 已有】
② 归因 attribution   机器出候选、**会错** → "这条属于规则缺口"     【本模块】
③ 裁决 verdict       只有人能下           → "确实该补这条规则"     【attribations 台账】
本模块只做 ②，**永不产出 ③**。每条归因显式带 `candidate=True` 与
可机械核验的证据（错误串原文 + 次数 + `sid#seq` 锚点），抄 chainaudit 的
引文硬门先例（chainaudit.py:31 ≥6 字 / 194-237 三态）。

━━ 为什么规则放数据文件（SOP A2）━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
`errstats._RULES`（errstats.py:52-91）**全是英文短语正则**，而 DSH 抛
`FS_*` 大写下划线码 → 语义相同、字面不相交 → 落进 `unclassified`（实测 52%）。
修法**不是**改 `_RULES`（那又是把领域知识写死进代码），而是加一层数据驱动的
归因表 `harvester/data/attrib-rules.yaml`：新 Harness 接入时改 YAML、
有人重审，不改 .py。

━━ 本层与 errstats 的关系（additive，不改既有行为）━━━━━━━━━━━━━━━━━
`collect_errors_from_db` 的 `class` 字段仍由 `errstats.classify_error` 单点
产出，**本模块不覆盖它**。归因是**叠加**在事实之上的一层：对每条错误尝试
匹配归因规则，命中则给出 `rule_id/class/why/fix/confidence` 候选。
`attrib` 只读，且**不参与 report-errors 的计数**——报告数字口径不变，
归因缺口另有出口（P6-4 复核实测收益）。

━━ 人读 / 机读两个出口绝不共用（TODO P6-2 门禁、SOP §3.3）━━━━━━━━━
项目栽过：分诊报告为篇幅每类只印 60 条、尾部写"另有 N 条"，而下游要的是
**全量 sid**，照报告抄会**静默漏掉尾部且不报错**。故本模块把两个出口写成
**两条独立函数、不共用实现**（`render_json` / `render_brief`），并用会红的
测试钉住"给 brief 加截断必红"。
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

from .errstats import CLASSES, clean_error_sample

#: 随包分发的归因规则表（本项目自定义数据文件）。定位方式同
#: kwstats.DEFAULT_STOPWORDS（`__file__` 相对，随包分发，不依赖 CWD）。
DEFAULT_RULES = (Path(__file__).resolve().parent / "data"
                 / "attrib-rules.yaml")

#: 四要素（SOP A3）——缺任一 → 加载即失败。非四要素但建议填的：
#: confidence / verified（见 YAML 头注释）。
REQUIRED_FIELDS = ("match", "class", "why", "fix")

#: 置信度取值。缺省 medium（未标 = 尚未人工评估过置信，别默认 high）。
CONFIDENCES = ("high", "medium", "low")

#: 归因候选的证据锚点上限（人读/机读都给同一份锚点，只是渲染形态不同）。
MAX_ANCHORS = 5


def _yaml():
    """PyYAML 缺失时给**项目约定**的清晰报错（体例同 planseed._yaml）。

    规则表是 YAML；**不提供降级解析**——块结构静默误读比直接报错危险
    （README「依赖边界」同口径）。注意：`attrib` 因此需要 PyYAML，
    与 `topic chain` 同类；但它**不是** report-errors 的前置（本层 additive）。
    """
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - venv 有 PyYAML
        raise RuntimeError(
            "attrib 的规则表读写需要 PyYAML（venv 解释器，见 README「依赖边界」）；"
            "不提供降级解析——归因规则读错比报错危险") from exc
    return yaml


class RuleError(ValueError):
    """规则表不合规（四要素缺失等）。**fail loud**：加载即抛，不静默跳过。"""


def load_rules(path: Path | None = None) -> list[dict]:
    """读归因规则表并校验。**四要素缺任一即抛 RuleError**（SOP A3）。

    返回 rules 列表，每项已补 `confidence`（缺省 medium）。文件不存在
    → fail loud（缺规则表时静默返回空会让"归因缺口"凭空消失）。
    """
    p = Path(path) if path else DEFAULT_RULES
    if not p.is_file():
        raise RuleError(f"归因规则表不存在: {p}（--rules 可指定）")
    yaml = _yaml()
    try:
        doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - YAML 语法错也是 fail loud
        raise RuleError(f"{p}: YAML 解析失败: {exc}") from exc
    if not isinstance(doc, dict):
        raise RuleError(f"{p}: 顶层必须是 mapping（含 version/rules）")
    rules = doc.get("rules")
    if not isinstance(rules, list) or not rules:
        raise RuleError(f"{p}: rules 必须是非空列表")

    seen: set[str] = set()
    out: list[dict] = []
    for i, r in enumerate(rules):
        where = f"{p}: rules[{i}]"
        if not isinstance(r, dict):
            raise RuleError(f"{where} 必须是 mapping")
        rid = r.get("id")
        if not rid or not isinstance(rid, str):
            raise RuleError(f"{where} 缺 id（规则标识）")
        if rid in seen:
            raise RuleError(f"{where} 规则 id 重复: {rid}")
        seen.add(rid)
        # 四要素：缺任一 → fail loud（这是 A3 的全部要点）
        for field in REQUIRED_FIELDS:
            if field not in r or r[field] in (None, "", [], {}):
                raise RuleError(
                    f"{where}（id={rid}）缺四要素之「{field}」——"
                    f"SOP A3：缺任一不许进决策层，故加载即失败")
        matches = r["match"]
        if not isinstance(matches, list) or not matches:
            raise RuleError(f"{where}（id={rid}）match 必须是非空列表")
        for m in matches:
            if not isinstance(m, str) or not m.strip():
                raise RuleError(f"{where}（id={rid}）match 含空/非字符串项: {m!r}")
        if r["class"] not in CLASSES:
            raise RuleError(
                f"{where}（id={rid}）class={r['class']!r} 不在 {list(CLASSES)}"
                f"——复用既有四分类，不另造分类体系")
        conf = r.get("confidence", "medium")
        if conf not in CONFIDENCES:
            raise RuleError(
                f"{where}（id={rid}）confidence={conf!r} 不在 {list(CONFIDENCES)}")
        out.append({
            "id": rid,
            "class": r["class"],
            "match": list(matches),
            "why": str(r["why"]),
            "fix": str(r["fix"]),
            "confidence": conf,
            "verified": r.get("verified"),
        })
    return out


def _compile(rules: list[dict]) -> list[tuple[dict, list[re.Pattern[str]]]]:
    """规则 → 已编译正则（大小写不敏感）。非法正则 fail loud（别静默不匹配）。"""
    out = []
    for r in rules:
        regs = []
        for pat in r["match"]:
            try:
                regs.append(re.compile(pat, re.IGNORECASE))
            except re.error as exc:
                raise RuleError(
                    f"规则 {r['id']} 的 match 正则非法: {pat!r} — {exc}") from exc
        out.append((r, regs))
    return out


def attribute(error_text: str, compiled: list[tuple[dict, list[re.Pattern[str]]]]
              ) -> dict | None:
    """对单条错误串做归因。命中返回**候选**（dict），未命中返回 None。

    规则序即优先级（同 errstats._RULES 口径）：首个命中即返回，不累加
    ——多条规则命中同一串时，把后续当"另有所指"会让人误以为有两种病。
    返回结构里 `candidate=True` 是刻意的：**这是候选，不是裁决**（A1）。
    """
    text = error_text or ""
    for r, regs in compiled:
        for reg in regs:
            if reg.search(text):
                return {
                    "rule_id": r["id"], "class": r["class"],
                    "why": r["why"], "fix": r["fix"],
                    "confidence": r["confidence"], "verified": r.get("verified"),
                    "matched": reg.pattern, "candidate": True,
                }
    return None


def collect_attributions(errors: list[dict], rules: list[dict]) -> dict:
    """按**归一模式**聚合归因（P6-2 的原料）。

    errors 来自 `errstats.collect_errors_from_db`（事实层，含 pattern/class/
    sid/seq/error）。返回：
      {"by_pattern": {pattern: {...}}, "summary": {...}}

    每个 pattern 条目含：class（事实层口径）、count、given_up、tools、
    anchors [(sid, seq, 错误原文)]、rule（命中的归因候选或 None）。
    **未命中的模式 `rule=None`**，由渲染层显式标"未归因"——不许静默吞掉
    （errstats 模块 docstring 同口径）。
    """
    compiled = _compile(rules)
    agg: dict[str, dict] = {}
    for e in errors:
        pat = e.get("pattern") or ""
        row = agg.setdefault(pat, {
            "pattern": pat, "class": e.get("class") or "unclassified",
            "count": 0, "given_up": 0, "tools": set(),
            "anchors": [], "rule": None, "rule_ids": set(),
        })
        row["count"] += 1
        if e.get("tool"):
            row["tools"].add(e["tool"])
        if not e.get("resolved", True):
            row["given_up"] += 1
        if len(row["anchors"]) < MAX_ANCHORS:
            row["anchors"].append({
                "sid": e.get("sid"), "seq": e.get("seq"),
                # 证据原文走**共享口径** clean_error_sample：截掉 Edit 类失败
                # 尾部附带的工具回显（用户文档内容），否则证据锚点会被
                # 大段源码/正文污染（errstats 模块 docstring 已定义此口径，
                # 建议池与 G1 报告同源——这里必须同源，不许各写一份）。
                # **不截断**（max_len 缺省 None）：截断由展示层决定，
                # 人读出口不允许截断（见 render_brief）。
                "error": clean_error_sample(e.get("error") or ""),
            })
        hit = attribute(e.get("error") or "", compiled)
        if hit:
            row["rule_ids"].add(hit["rule_id"])
            # 同模式内以首条命中的规则为准（同模式 = 同归一形态，规则应一致；
            # 不一致说明归一过度，记下全部 id 供复核，见 rule_ids_conflict）
            if row["rule"] is None:
                row["rule"] = hit
    # 固化：set → 排序列表；补冲突标记
    out: dict[str, dict] = {}
    for pat, row in agg.items():
        ids = sorted(row.pop("rule_ids"))
        row["tools"] = sorted(row["tools"])
        row["rule_ids"] = ids
        row["rule_ids_conflict"] = len(ids) > 1
        out[pat] = row
    attributed = sum(1 for r in out.values() if r["rule"])
    n_attr = sum(r["count"] for r in out.values() if r["rule"])
    n_total = sum(r["count"] for r in out.values())
    summary = {
        "patterns_total": len(out),
        "patterns_attributed": attributed,
        "patterns_unattributed": len(out) - attributed,
        "errors_total": n_total,
        "errors_attributed": n_attr,
        "errors_unattributed": n_total - n_attr,
        "rules_total": len(rules),
        "rules_hit": len({r["rule"]["rule_id"] for r in out.values() if r["rule"]}),
    }
    return {"by_pattern": out, "summary": summary}


# ---- 归因台账（P6-3）：抄 card_review 结构 --------------------------------
#
# 语义照 cards.card_review（cards.py:31-34 REVIEW_VERDICTS、218-313）：
# **人裁过的归因，下次不再进候选**（人裁过的不许再骚扰）。
# 表 attribations 与 card_review **同构**（列名/类型一致，见 P6-3 门禁
# "结构对账"），只把 card_file→pattern_key、warn_key→rule_id：
# pattern_key = 归一模式（errstats.normalize_error 产物）
# rule_id     = 归因规则 id（AR-00x）。两者共同定位"一条候选"。
#
# 为什么键是 (pattern_key, rule_id) 而不是 pattern_key 单列：
# 同一个模式将来可能换规则（补了更准的规则）——换规则 = 换了候选内容，
# 应当重新问人，而不是被旧裁决静默压掉。
#
# verdict 语义（本项目自定义，**与 card_review 的取值刻意不同**）：
#   accepted 接受该归因（这条候选是对的，可据此补规则/改用法）
#   rejected 驳回（归因错了，机器判断不成立）
#   revise   要改（方向对但 why/fix 需修正，note 里写怎么改）
ATTRIB_VERDICTS = ("accepted", "rejected", "revise")

_ATTRIB_SCHEMA = """
CREATE TABLE IF NOT EXISTS attribations (
    pattern_key TEXT NOT NULL,
    rule_id     TEXT NOT NULL,
    class       TEXT NOT NULL,
    verdict     TEXT NOT NULL,
    note        TEXT,
    reviewed_at TEXT NOT NULL,
    reviewed_by TEXT,
    PRIMARY KEY (pattern_key, rule_id)
)
"""


def ensure_attrib_db(meta: Path) -> Path:
    """建归因台账库/表（幂等）。"""
    meta = Path(meta)
    meta.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(meta))
    try:
        con.executescript(_ATTRIB_SCHEMA)
        con.commit()
    finally:
        con.close()
    return meta


def load_verdict_map(meta: Path | None) -> dict[tuple[str, str], dict]:
    """{(pattern_key, rule_id): 裁决行}。库/表不存在 → 空映射
    （不过滤，行为与"没台账"完全一致——不加台账的用户零感知，同 card_review）。"""
    if meta is None or not Path(meta).is_file():
        return {}
    con = sqlite3.connect(str(meta))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT pattern_key, rule_id, class, verdict, note, reviewed_at, "
            "reviewed_by FROM attribations").fetchall()
    except sqlite3.OperationalError:  # 表未建
        return {}
    finally:
        con.close()
    return {(str(r["pattern_key"]), str(r["rule_id"])): dict(r) for r in rows}


def list_verdicts(meta: Path) -> list[dict]:
    """台账明细（`attrib review --list`）。库/表不存在 → 空。"""
    if not Path(meta).is_file():
        return []
    con = sqlite3.connect(str(meta))
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(
            "SELECT pattern_key, rule_id, class, verdict, note, reviewed_at, "
            "reviewed_by FROM attribations ORDER BY pattern_key, rule_id")]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def record_verdict(meta: Path, pattern_key: str, rule_id: str, cls: str,
                   verdict: str = "accepted", note: str = "",
                   reviewed_by: str = "") -> int:
    """落裁决（UPSERT：改判 = 覆盖）。返回写入条数。

    verdict 必须在 ATTRIB_VERDICTS 内；pattern_key/rule_id 空则幂等返回 0
    （不静默写脏行）。**这是本工程唯一的写路径**，且只写台账库（meta），
    绝不碰 harvester.db（红线 #1）。
    """
    if verdict not in ATTRIB_VERDICTS:
        raise ValueError(f"verdict={verdict!r} 不在 {list(ATTRIB_VERDICTS)}")
    if not pattern_key or not rule_id:
        return 0
    ensure_attrib_db(meta)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    con = sqlite3.connect(str(meta))
    try:
        con.execute(
            "INSERT INTO attribations (pattern_key, rule_id, class, verdict, "
            "note, reviewed_at, reviewed_by) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(pattern_key, rule_id) DO UPDATE SET "
            "class=excluded.class, verdict=excluded.verdict, "
            "note=excluded.note, reviewed_at=excluded.reviewed_at, "
            "reviewed_by=excluded.reviewed_by",
            (pattern_key, rule_id, cls, verdict, note or None, now,
             reviewed_by or None))
        con.commit()
        return 1
    finally:
        con.close()


def filter_reviewed(result: dict, meta: Path | None) -> dict:
    """把人已裁决过的归因候选移出候选区（同 card_review「人裁过的不许再烦人」）。

    **只移"已裁决的候选"，不动事实**：事实层计数（count）不变，被移出的
    条目落到 `reviewed` 段并在 summary 里计数——否则"处理过"看起来像
    "数据变少了"（静默失效型错误）。返回新 dict，不改入参。
    """
    vm = load_verdict_map(meta)
    if not vm:
        out = dict(result)
        out["reviewed"] = []
        return out
    kept: dict[str, dict] = {}
    reviewed: list[dict] = []
    for pat, row in result["by_pattern"].items():
        rule = row.get("rule")
        if rule and (pat, rule["rule_id"]) in vm:
            reviewed.append({**row, "verdict": vm[(pat, rule["rule_id"])]})
        else:
            kept[pat] = row
    out = {"by_pattern": kept, "reviewed": reviewed,
           "summary": dict(result["summary"])}
    out["summary"]["patterns_reviewed"] = len(reviewed)
    out["summary"]["errors_reviewed"] = sum(r["count"] for r in reviewed)
    return out


# ---- 两个出口：绝不共用实现（见模块 docstring）----------------------------

#: 机读 schema 版本。字段增删须同步本值与 tests，让消费方有版本可依。
JSON_SCHEMA = "attrib/1"


def render_json(result: dict, errors_meta: dict | None = None,
                db_path: str = "", generated_at: str = "",
                snapshot: dict | None = None) -> str:
    """**机读出口**：全量 + schema + 口径头，可被程序消费（导出包原料）。

    与 render_brief **不共用实现**（P6-2 门禁）：这条不做任何截断、不做
    美化排版，只保证字段齐全；人读形态改版不影响本出口。
    """
    payload = {
        "schema": JSON_SCHEMA,
        "generated_at": generated_at or time.strftime("%Y-%m-%d %H:%M:%S"),
        "db": db_path,
        "notes": {
            "layer": "attribution（归因候选，**会错**，非裁决）",
            "raw_column": "口径一律 steps.error 原文；messages.raw 为会话原文",
            "candidate_semantics": "rule 字段是候选；裁决在 attribations 台账",
        },
        "snapshot": snapshot or {},
        "errors_meta": errors_meta or {},
        "summary": result["summary"],
        "reviewed": result.get("reviewed", []),
        "patterns": [result["by_pattern"][k]
                     for k in sorted(result["by_pattern"])],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _fmt_anchors(anchors: list[dict]) -> str:
    """锚点行（人读形态）。**不截断**错误原文——见模块 docstring 的教训。"""
    if not anchors:
        return "      （无锚点）"
    return "\n".join(
        f"      - `{a['sid']}#{a['seq']}` {a['error']}" for a in anchors)


def render_brief(result: dict, errors_meta: dict | None = None,
                 db_path: str = "", generated_at: str = "",
                 snapshot: dict | None = None) -> str:
    """**人读出口**：一行一条、**不截断**、带 why/fix 和锚点。

    与 render_json **不共用实现**。**绝不加 `[:N]` 截断**——项目栽过
    "报告只印 60 条 + 尾部另有 N 条"导致下游静默漏数据；有一条会红的测试
    钉住"给本函数加截断必红"（tests/test_attrib.py）。
    输出按 count 降序（人先看量级大的），但**全量**输出，不设 max_detail。
    """
    s = result["summary"]
    gen = generated_at or time.strftime("%Y-%m-%d %H:%M:%S")
    L = [
        "# 归因候选清单（attrib --brief）",
        "",
        f"> 生成于 {gen}｜索引库 {db_path or '?'}",
        "> 归因是**候选**（机器判断，会错）；裁决请回填 attribations 台账。",
        "> 口径：错误原文取自 steps.error；分类沿用 errstats 四分类"
        "（本项目自定义分类，见 docs/TERMINOLOGY.md §2.2）。",
        "",
    ]
    if snapshot:
        L += ["> 库快照：" + "；".join(f"{k}={v}" for k, v in snapshot.items()
                                       if v), ""]
    L += [
        f"- 模式 {s['patterns_total']} 个"
        f"（已归因 {s['patterns_attributed']} / 未归因 {s['patterns_unattributed']}）",
        f"- 错误 {s['errors_total']} 条"
        f"（已归因 {s['errors_attributed']} / 未归因 {s['errors_unattributed']}）",
        f"- 规则 {s['rules_total']} 条，命中 {s['rules_hit']} 条",
    ]
    if errors_meta:
        L.append(f"- 事实层口径：错误步骤 {errors_meta.get('error_count', '?')} / "
                 f"总步骤 {errors_meta.get('total_steps', '?')}，"
                 f"涉及 {errors_meta.get('sessions', '?')} 会话")
    if s.get("patterns_reviewed"):
        L.append(f"- 已人裁移出候选 {s['patterns_reviewed']} 个模式"
                 f"（{s.get('errors_reviewed', 0)} 条；事实计数不变，见文末台账段）")
    L.append("")

    rows = sorted(result["by_pattern"].values(),
                  key=lambda r: (-r["count"], r["pattern"]))
    L += ["## 归因候选（按量级降序；全量，不截断）", ""]
    for r in rows:
        rule = r.get("rule")
        tag = (f"[{rule['class']}] 规则 {rule['rule_id']}"
               f"（置信 {rule['confidence']}）" if rule else "[未归因]")
        L.append(f"### x{r['count']} {tag}")
        L.append(f"    模式：{r['pattern']}")
        L.append(f"    工具：{'/'.join(r['tools']) or '（无）'}"
                 f"　未解决：{r['given_up']}")
        if rule:
            L.append(f"    根因候选（why）：{rule['why']}")
            L.append(f"    应对候选（fix）：{rule['fix']}")
            if rule.get("verified"):
                L.append(f"    人核日期：{rule['verified']}")
        else:
            L.append("    根因候选（why）：（未归因——无规则命中，"
                     "需人工归因后补规则）")
            L.append("    应对候选（fix）：（待补规则给出）")
        if r.get("rule_ids_conflict"):
            L.append(f"    ⚠ 同模式命中多条规则：{','.join(r['rule_ids'])}"
                     f"——归一可能过度，请人工复核")
        L.append("    证据锚点：")
        L.append(_fmt_anchors(r["anchors"]))
        L.append("")
    reviewed = result.get("reviewed") or []
    if reviewed:
        L += ["## 已裁决（人裁过，不再进候选）", ""]
        for r in sorted(reviewed, key=lambda x: -x["count"]):
            v = r.get("verdict") or {}
            L.append(f"- x{r['count']} `{r['pattern']}` → "
                     f"{v.get('rule_id')} **{v.get('verdict')}**"
                     f"（{v.get('reviewed_at', '?')}"
                     f"{'，' + v['reviewed_by'] if v.get('reviewed_by') else ''}）"
                     + (f"　note: {v['note']}" if v.get("note") else ""))
        L.append("")
    return "\n".join(L).rstrip() + "\n"