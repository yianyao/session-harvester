# -*- coding: utf-8 -*-
"""分诊结果 → plan 草稿（v0.35）——把「读包填 plan」的机械一半做成工具。

**为什么有这个模块**（用户 2026-10-10 第 1 点要求，第二轮强调）：v0.33/v0.34 两轮
都是「跑分诊 → 人/Agent 逐条判断 → 写 plan → apply」，但中间那步每次都被写成
`docs/reports/make-plan-*.py` 一次性脚本。而它**每轮都要重跑**（下次采集、下次
起草还会重来）→ 按项目铁律必须进工具本体，`docs/reports/` 只留**逐轮的判断**。

分工（红线不变：语义判断不落进代码）：

    分诊 JSON（工具产）  +  judgment.yaml（人/Agent 逐轮写）  →  plan.yaml（工具产）
                                                                   ↓
                                                    topic-consolidate --apply

`judgment.yaml` 就是原来一次性脚本里那些清单的可复核形态：

    version: 1
    new_topics:                       # 要新建的主题（可选）
      - {key: M1, name: 创作素材与背景检索, keywords: [描写, 用词]}
    craft_topic: new:M1               # craft_material 整类归这里（也可以是 tp-xxx）
    overrides:                        # 逐条改判：sid → tp-xxx / new:KEY
      qianwen-raw:abc: tp-20261008-010
    noise:                            # 登记零散（**必须显式列出**，不自动登记）
      - yuanbao-raw:xyz
    skip:                             # 本轮不动（显式记录，和"忘了"区分开）
      - {sid: yuanbao-raw:def, why: 语义两可}

机械部分（本模块）：把 `topic_hint` 的机械命中映射成主题 **id**、把
`craft_material` 整类归位、把未被 assign 的主题列进 `keep`（完整性）、逐 sid
交叉校验（重复/不存在/指向未知主题全部 fail loud）、按判定类出**覆盖统计**。

`topic_hint` 只是关键词机械命中的**提示**：它会把小说正文判进"梦境"类、把近义词
提问判进素材类。故 overrides 是常用手段，不要指望映射本身有语义。
"""
from __future__ import annotations

import json
from pathlib import Path

from .consolidate import PLAN_VERSION, validate_plan
from .topics import list_topics


def load_triage(path: Path) -> dict:
    """读 `--triage-json` 产物。结构不对时 fail loud（别静默产出空 plan）。"""
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(d, dict) or not isinstance(d.get("rows"), list):
        raise ValueError(f"{path}: 不是分诊 JSON（缺 rows）——"
                         "用 `topic-consolidate --triage-json <路径>` 生成")
    for r in d["rows"]:
        for k in ("sid", "verdict"):
            if k not in r:
                raise ValueError(f"{path}: 分诊行缺字段 {k}: {r!r}")
    return d


def _yaml():
    """PyYAML 缺失时给**项目约定**的清晰报错（H9），而不是裸 ImportError。"""
    try:
        import yaml
    except ImportError as exc:                       # pragma: no cover
        raise RuntimeError(
            "plan 的 YAML 读写需要 PyYAML（venv 解释器，H9）；"
            "不提供降级解析——plan 读错比报错危险") from exc
    return yaml


def load_judgment(path: Path | None) -> dict:
    """读逐轮判断文件（可缺省 = 只做机械映射）。"""
    if not path:
        return {}
    yaml = _yaml()
    d = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(d, dict):
        raise ValueError("judgment 必须是 mapping（见 README「分诊 → plan」）")
    for key in ("overrides", "noise", "skip", "new_topics"):
        v = d.get(key)
        if v is not None and not isinstance(v, (list, dict)):
            raise ValueError(f"judgment.{key} 必须是列表或 mapping: {type(v)}")
    return d


def dump_plan(plan: dict) -> str:
    """plan → YAML 文本（UTF-8 不转义；键序按构造顺序，便于 diff）。"""
    yaml = _yaml()
    return yaml.safe_dump(plan, allow_unicode=True, sort_keys=False,
                          default_flow_style=False, width=100)


def _sid_of(entry) -> str:
    return entry["sid"] if isinstance(entry, dict) else entry


def _hint_name(row: dict) -> str:
    """从 `reason` 里取机械命中的**主题名**。

    必须按标记切分、不能整句 `replace`：深会话（v0.39）的 reason 是
    "深会话（user 回合 8 > 3），机械命中主题关键词：<名>"，整句替换会把前缀
    一起当成主题名（本函数的测试当场抓到过）。
    """
    reason = row.get("reason") or ""
    marker = "命中主题关键词："
    if marker in reason:
        return reason.split(marker, 1)[1].strip()
    return ""


def build_seed(triage: dict, topics: list[dict] | None = None,
               judgment: dict | None = None,
               craft_topic: str | None = None) -> tuple[dict, dict]:
    """分诊结果 + 判断 → (plan, stats)。

    `stats` 按判定类给出 assign/noise/skip/unhandled 计数——**unhandled 是要看见
    的**（缺省会把 substantive 与 noise_maybe 留在原地，这是"未处理"，不是"通过"）。

    plan 由本函数保证能过 `validate_plan`（完整性 + 重复检查），meta 相关的
    检查（sid 是否已是别主题成员）留到 `--apply` 时做。
    """
    topics = list(topics if topics is not None else list_topics(Path("topics_meta.db")))
    j = dict(judgment or {})
    if craft_topic:
        j["craft_topic"] = craft_topic
    craft = j.get("craft_topic")

    new_topics = list(j.get("new_topics") or [])
    new_keys = {n.get("key") for n in new_topics}
    for n in new_topics:
        if not n.get("key") or not n.get("name"):
            raise ValueError(f"judgment.new_topics 条目缺 key/name: {n}")

    name2id = {t["name"]: t["id"] for t in topics}
    have = {t["id"] for t in topics}
    overrides = dict(j.get("overrides") or {})
    noise = [_sid_of(x) for x in (j.get("noise") or [])]
    skip = [_sid_of(x) for x in (j.get("skip") or [])]

    rows = triage["rows"]
    known = {r["sid"] for r in rows}
    for label, sids in (("overrides", list(overrides)), ("noise", noise),
                        ("skip", skip)):
        ghosts = [s for s in sids if s not in known]
        if ghosts:
            raise ValueError(f"[judgment] {label} 里有 {len(ghosts)} 个 sid 不在"
                             f"分诊结果里: {ghosts[:3]}（打错字？分诊已重跑？）")
    for a, b, la, lb in ((set(overrides), set(noise), "overrides", "noise"),
                         (set(overrides), set(skip), "overrides", "skip"),
                         (set(noise), set(skip), "noise", "skip")):
        both = sorted(a & b)
        if both:
            raise ValueError(f"[judgment] 同一 sid 同时出现在 {la} 与 {lb}: "
                             f"{both[:3]}（自相矛盾）")

    def resolve(target: str, sid: str) -> str:
        """返回 bucket key：已有主题 = id；新建 = 'new:KEY'。"""
        if target.startswith("new:"):
            key = target[4:]
            if key not in new_keys:
                raise ValueError(f"[judgment] {sid} 指向未在 new_topics 里定义的 "
                                 f"key: {key}")
            return target
        if target not in have:
            raise ValueError(f"[judgment] {sid} 指向不存在的主题: {target}")
        return target

    buckets: dict[str, list[str]] = {}
    stats: dict[str, dict] = {}
    unhandled: dict[str, list[str]] = {}

    def bump(verdict: str, kind: str) -> None:
        s = stats.setdefault(verdict, {"assign": 0, "noise": 0, "skip": 0,
                                       "unhandled": 0})
        s[kind] += 1

    noise_set, skip_set = set(noise), set(skip)
    for r in rows:
        sid, v = r["sid"], r["verdict"]
        stats.setdefault(v, {"assign": 0, "noise": 0, "skip": 0, "unhandled": 0})
        if sid in overrides:
            buckets.setdefault(resolve(overrides[sid], sid), []).append(sid)
            bump(v, "assign")
        elif sid in noise_set:
            bump(v, "noise")
        elif sid in skip_set:
            bump(v, "skip")
        elif v in ("topic_hint", "deep_topic_hint"):
            name = _hint_name(r)
            tid = name2id.get(name)
            if not tid:
                raise ValueError(f"[triage] {sid} 命中的主题名不在注册表: {name!r}"
                                 "（主题被改名/删除了？用 overrides 改判）")
            buckets.setdefault(tid, []).append(sid)
            bump(v, "assign")
        elif v == "craft_material" and craft:
            buckets.setdefault(resolve(craft, sid), []).append(sid)
            bump(v, "assign")
        else:
            bump(v, "unhandled")
            unhandled.setdefault(v, []).append(sid)

    for v, sids in unhandled.items():
        stats[v]["unhandled_sids"] = sids

    assign = []
    for key in sorted(buckets):
        entry: dict = {"sids": sorted(set(buckets[key])),
                       "evidence": j.get("evidence")
                       or "plan-seed：分诊判定 + judgment 逐条复核"}
        if key.startswith("new:"):
            entry["new"] = key[4:]
        else:
            entry["target"] = key
        assign.append(entry)

    used = {k for k in buckets if not k.startswith("new:")}
    keep = [{"id": t["id"], "reason": "plan-seed：本轮无 assign（保留）"}
            for t in sorted(topics, key=lambda t: t["id"]) if t["id"] not in used]
    plan = {"version": PLAN_VERSION, "new_topics": new_topics, "renames": [],
            "groups": [], "assign": assign,
            "noise": [{"sid": s, "reason": "plan-seed：judgment 显式登记为纯取信息"}
                      for s in sorted(noise_set)],
            "keep": keep}
    errs = validate_plan(plan, have)
    if errs:
        raise ValueError("生成的 plan 未过校验（这是工具缺陷，请报出）: "
                         + "; ".join(errs))
    stats["_total"] = {"rows": len(rows), "assign": sum(
        len(a["sids"]) for a in assign), "noise": len(noise_set),
        "skip": len(skip_set)}
    return plan, stats


def render_seed_stats(stats: dict) -> str:
    """覆盖统计（**unhandled 必须显式可见**：未处理 ≠ 通过）。"""
    total = stats.get("_total", {})
    L = [f"# plan-seed 覆盖统计：分诊 {total.get('rows', 0)} 条 → "
         f"assign {total.get('assign', 0)} / noise {total.get('noise', 0)} / "
         f"skip {total.get('skip', 0)}",
         "# 判定类\tassign\tnoise\tskip\tunhandled"]
    for v in sorted(k for k in stats if not k.startswith("_")):
        s = stats[v]
        L.append(f"# {v}\t{s['assign']}\t{s['noise']}\t{s['skip']}\t"
                 f"{s['unhandled']}")
    left = sum(stats[v]["unhandled"] for v in stats if not v.startswith("_"))
    if left:
        L.append(f"# 注意：{left} 条未归置（unhandled）——"
                 "多为 substantive / noise_maybe，属**本轮不动**，不是通过")
    return "\n".join(L) + "\n"


def unhandled_sids(stats: dict, verdict: str) -> list[str]:
    """某判定类里未被归置的 sid（`--require-covered` 报错时逐条列出）。"""
    return list(stats.get(verdict, {}).get("unhandled_sids") or [])
