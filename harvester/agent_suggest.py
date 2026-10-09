# -*- coding: utf-8 -*-
"""AGENTS.md 条目自动建议（suggest-agents 子命令）——G2 闭环最后一公里。

对应《轨迹分析工具设计方案》G2：错误三分类之后，产出可直接并入
`~/.dsh/AGENTS.md` 的**候选条目**（每条附锚点与原文证据）。

纪律（与设计方案一致）：
- 只产出建议文件，**绝不直接修改 AGENTS.md**——人工审阅后自行并入；
  AGENTS.md 的权威定义是"真实踩过的坑"，机器建议必须过人眼；
- 每条建议必须附锚点（sid#seq）与错误原文，脱离证据的条目视为缺陷；
- 聚合口径（P1-2 修订）：先按**模板**收集（一个模板吞并所有命中它的
  错误模式），再把 pattern 集有交集的模板条目**按根因合并**（计数按
  并集相加、samples 合并去重、title 取 calls 最高者）——同根因全局只出
  一条主建议（H15）；合并后总次数达 --min-count 才出条目；
- 未命中任何模板的模式进"待人工归因"节——给证据不给结论。

条目格式对齐 AGENTS.md 现有风格：编号 + 加粗规则句 +（实测次数与锚点）。
"""

from __future__ import annotations

import re

from .errstats import clean_error_sample, pattern_stats

# 模板表：正则（对归一模式或错误原文匹配）→ (规则句, 做法说明, owner)。
# 每条模板都源自本库 steps 表真实踩过的错误，不是通用建议。
# owner（v0.19 additive）：建议的修复责任方，三值——
#   harness=上游 harness 本身（自动重试/自动重建/分页等平台能力）；
#   tool=工具实现本身；workflow=使用方（agent/人）改用法即可消。
_TEMPLATES: list[tuple[str, str, str, str]] = [
    (r"tool_permission_revoked",
     "权限被撤销的工具不要原地重试。",
     "出现 tool_permission_revoked（read/ls/find 等）说明用户已撤销授权，"
     "重试只会重复失败；先向用户说明意图请求重新授权，或改走无权限要求的路径。",
     "harness"),
    (r"sandbox",
     "沙箱拦截不等于命令有错。",
     "sandbox-center 拦截时先判断命令是否真的需要越界；确需越界时显式申请"
     "（dangerouslyDisableSandbox 走用户批准），否则改写为无越界写法。",
     "harness"),
    (r"Win32|SetNamedSecurityInfo",
     "写 ACL 失败是环境权限问题，不要反复重试同一命令。",
     "grantWrite 报 Win32 5（拒绝访问）时先检查目录所有权/占用，"
     "或换目标路径；同一命令重复撞墙只会刷失败率。",
     "harness"),
    (r"fetch failed|socket hang up",
     "网络类失败先重试一次再下结论。",
     "web fetch / socket 报错多为瞬时故障（代理/网络抖动），"
     "直接放弃会把假阴性当结论留档。",
     "harness"),
    (r"browser_instance_unknown|browser_cancelled",
     "browser 工具报实例失效时先重建实例再继续。",
     "browser_instance_unknown / browser_cancelled 说明浏览器会话已失效，"
     "重试原调用无意义。",
     "harness"),
    (r"exceeds maximum allowed tokens",
     "工具输出超限先收窄再取，不要一次性拉全量。",
     "result exceeds maximum allowed tokens 说明返回体过大被宿主截断；"
     "加 head_limit/分页/offset，或让结果落盘后按需读片段。",
     "harness"),
    (r"String to replace not found|old_string|file changed since|"
     r"not been read|Found N matches|replace_all",
     "Edit/Write 前必须先 Read 目标文件最新内容。",
     "old_string 不匹配 / file changed since read / not been read / "
     "多处匹配都源于拿着过期记忆改文件；先重读，多处匹配用 replace_all。",
     "workflow"),
    (r"exceeds maxDepth",
     "子代理嵌套深度受 maxDepth 限制，深任务改平铺编排。",
     "subagent depth exceeds maxDepth 时不要继续嵌套，回到主代理分层调度。",
     "harness"),
    (r"out of range",
     "read 的 offset 要先确认文件行数，不要盲猜。",
     "offset out of range 说明行号是猜的；先读尾部或用检索定位再取段。",
     "workflow"),
    (r"file no longer exists|File not found",
     "引用记忆里的旧路径前先确认目标仍在。",
     "file no longer exists / File not found 说明目标已被删除或改名；"
     "写/改前先探存在性，失败后不要用同一路径重试。",
     "workflow"),
    (r"skill_asset_invalid",
     "技能包引用的资产先校验存在性再运行。",
     "skill_asset_invalid 说明技能声明的资产路径失效；"
     "技能安装/更新后先跑资产校验。",
     "workflow"),
]


def _hits(creg: re.Pattern, pattern: str, samples: list) -> bool:
    """模板是否命中该模式：归一模式串或任一样本原文前 200 字符。"""
    if creg.search(pattern):
        return True
    return any(creg.search(raw[:200]) for _sid, _seq, raw in samples)


def _collect_template_groups(
        stats: dict[str, dict]) -> list[dict]:
    """每模板收集命中 patterns：[{tpl, title, body, owner, pats: {pat: d}}]。

    pats 值为 pattern_stats 的条目（count/given_up/samples）。
    """
    groups = []
    for ti, (reg, title, body, owner) in enumerate(_TEMPLATES):
        creg = re.compile(reg, re.IGNORECASE)
        pats = {pat: d for pat, d in stats.items()
                if _hits(creg, pat, d["samples"])}
        if pats:
            groups.append({"tpl": ti, "title": title, "body": body,
                           "owner": owner, "pats": pats})
    return groups


def _merge_overlapping(groups: list[dict]) -> list[dict]:
    """同根因合并（P1-2，SOP-P1-2 第 2 条）：

    两模板条目覆盖的 pattern 集有交集即视为同一根因，连通分量式合并
    （A∩B、B∩C → A/B/C 一组；防"同根因多条建议"，H15）。
    合并组：pats 取并集（计数按并集相加，每 pattern 只计一次，不重复
    计）；title/body/owner 取组内自身 total 最高的成员（"title 取 calls
    最高者"；自身 total 于扩充并集前定格，同数按模板声明序 tie-break，
    确定性）。
    """
    out: list[dict] = []
    for g in groups:
        overlap = [m for m in out if set(m["pats"]) & set(g["pats"])]
        if not overlap:
            out.append(dict(g))
            continue
        # 成员自身 total 在并集扩充前定格（base 随后要承载并集）
        members = overlap + [g]
        best = max(members,
                   key=lambda m: (sum(d["count"] for d in m["pats"].values()),
                                  -m["tpl"]))
        base = overlap[0]
        for m in overlap[1:]:
            for pat, d in m["pats"].items():
                base["pats"].setdefault(pat, d)
            out.remove(m)
        for pat, d in g["pats"].items():
            base["pats"].setdefault(pat, d)
        base["title"], base["body"], base["owner"] = (
            best["title"], best["body"], best["owner"])
        base["tpl"] = best["tpl"]
    return out


def root_key_assign(pairs: list[tuple[str, list[int]]]) -> dict[str, int | None]:
    """把 (pattern, 命中模板 id 列表) 分配到根因组（P1-2，triage A 节用）。

    命中同一 pattern 的多个模板经并查集连通 → 同组，组 id = 组内最小
    模板 id；未命中任何模板的 pattern 组 id = None（各自独立，互不同组）。
    返回 {pattern: group_id}。
    """
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]  # 路径压缩
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            lo, hi = sorted((ra, rb))
            parent[hi] = lo  # 小 id 作根 → 组 id 恒为分量内最小模板 id

    hit_map = {pat: hits for pat, hits in pairs if hits}
    for hits in hit_map.values():
        for t in hits:
            find(t)
        for t in hits[1:]:
            union(hits[0], t)
    return {pat: (find(hits[0]) if hits else None) for pat, hits in pairs}


def build_suggestion_entries(errors: list[dict], min_count: int = 3,
                             max_items: int = 15,
                             statuses: dict[str, str] | None = None) -> dict:
    """结构化建议构建（build_suggestions 的纯数据层，API 直接消费）。

    statuses（v0.19 additive，可选）：{key: status} 映射（key=title，
    来自独立 meta 库 suggestion_status 表）；缺省/未记录 → "pending"。
    P1-2（v0.22）：同根因模板条目合并——pattern 集有交集的条目合并为
    一条（计数按并集相加不重复计、samples 合并去重、title 取 calls 最高
    者），min_count 在合并后判定；H15 验收 = "Edit 前置"根因全局只出
    1 条主建议。
    返回 {"entries": [...], "leftover": [...], "min_count": n}：
    - entries 每项 {title, body, total, owner, unresolved_count,
      samples: [(sid, seq, raw)]}；按 unresolved_count 降序、再按 total
      降序排序（v0.19：未解决维度优先——待修清单是 unresolved_count>=1 的
      条目，unresolved_count==0 的自愈条目降级为"观察区"，由消费方拆分），
      截 max_items；
    - leftover 为未命中任何模板的 (pattern, stats) 列表，按 count 降序
      （stats 含 class/count/tools(set)/samples/given_up）——"待人工归因"
      节与前端报告页均消费此结构。
    """
    stats = pattern_stats(errors)
    groups = _merge_overlapping(_collect_template_groups(stats))
    consumed: set[str] = set()
    entries: list[dict] = []
    for g in groups:
        consumed |= set(g["pats"])
        total = sum(d["count"] for d in g["pats"].values())
        unresolved = sum(d.get("given_up", 0) for d in g["pats"].values())
        if total < min_count:
            continue
        samples, seen = [], set()
        for d in g["pats"].values():
            for s in d["samples"]:
                if s not in seen:
                    seen.add(s)
                    samples.append(s)
        entries.append({"title": g["title"], "body": g["body"],
                        "total": total, "owner": g["owner"],
                        "unresolved_count": unresolved, "samples": samples})
    for e in entries:
        e["status"] = (statuses or {}).get(e["title"], "pending")
    entries.sort(key=lambda e: (-e["unresolved_count"], -e["total"]))
    entries = entries[:max_items]
    leftover = [(pat, d) for pat, d in stats.items() if pat not in consumed]
    leftover.sort(key=lambda kv: (-kv[1]["count"], -kv[1].get("given_up", 0)))
    return {"entries": entries, "leftover": leftover, "min_count": min_count}


def build_suggestions(errors: list[dict], min_count: int = 3,
                      max_items: int = 15,
                      statuses: dict[str, str] | None = None) -> str:
    """从归类后的错误生成 AGENTS.md 候选条目 Markdown（数据层见
    build_suggestion_entries；本函数只做渲染，保证两种出口同构）。"""
    built = build_suggestion_entries(errors, min_count=min_count,
                                     max_items=max_items, statuses=statuses)
    entries, leftover = built["entries"], built["leftover"]

    lines = [
        "# AGENTS.md 条目建议（机器产出，人工审阅后并入）",
        "",
        "> 来源：harvester steps 表错误三分类；每条附真实锚点与原文证据。",
        "> 口径：未解决 = 错误后同会话同工具无再次调用（放弃）；"
        "未解决>=1 进待修清单，其余（已自愈）降级观察区。",
        "> 纪律：本文件只是建议池，**不自动写入 AGENTS.md**；并入时按",
        "> AGENTS.md 现有编号顺延，并清理被取代的同类旧条目。",
        "",
    ]
    todo = [e for e in entries if e["unresolved_count"] >= 1]
    watch = [e for e in entries if e["unresolved_count"] == 0]
    lines += ["## 待修清单（未解决 >= 1，优先处理）", ""]
    if not todo:
        lines += [f"（无：所有达标条目均已自愈。）", ""]
    for i, e in enumerate(todo, 1):
        sid, seq, raw = e["samples"][0]
        raw_short = clean_error_sample(raw)
        lines += [
            f"{i}. **{e['title']}**（owner: {e['owner']}"
            f"｜status: {e['status']}）",
            f"   {e['body']}",
            f"   实测 {e['total']} 次｜未解决 {e['unresolved_count']} 次，"
            f"证据：`{sid}#{seq}`「{raw_short}」",
            "",
        ]
    lines += ["## 观察区（未解决 = 0，已自愈，暂不动 AGENTS.md）", ""]
    if not watch:
        lines += ["（无。）", ""]
    for i, e in enumerate(watch, 1):
        sid, seq, raw = e["samples"][0]
        raw_short = clean_error_sample(raw)
        lines += [
            f"{i}. **{e['title']}**（owner: {e['owner']}）",
            f"   实测 {e['total']} 次｜未解决 0 次，"
            f"证据：`{sid}#{seq}`「{raw_short}」",
            "",
        ]
    if leftover:
        lines += ["## 待人工归因（未命中模板，给证据不给结论）", ""]
        for pat, d in leftover[:10]:
            unsolved = d.get("given_up", 0)
            lines.append(f"- **x{d['count']}** [{d['class']}] `{pat}`"
                         + (f"（未解决 {unsolved}）" if unsolved else ""))
            for sid, seq, raw in d["samples"][:2]:
                lines.append(f"      - `{sid}#{seq}` {raw[:140]}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
