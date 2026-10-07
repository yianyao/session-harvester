# -*- coding: utf-8 -*-
"""AGENTS.md 条目自动建议（suggest-agents 子命令）——G2 闭环最后一公里。

对应《轨迹分析工具设计方案》G2：错误三分类之后，产出可直接并入
`~/.dsh/AGENTS.md` 的**候选条目**（每条附锚点与原文证据）。

纪律（与设计方案一致）：
- 只产出建议文件，**绝不直接修改 AGENTS.md**——人工审阅后自行并入；
  AGENTS.md 的权威定义是"真实踩过的坑"，机器建议必须过人眼；
- 每条建议必须附锚点（sid#seq）与错误原文，脱离证据的条目视为缺陷；
- 聚合口径：按**模板**聚合（一个模板吞并所有命中它的错误模式），
  模板总次数达到 --min-count 才出条目；
- 未命中任何模板的模式进"待人工归因"节——给证据不给结论。

条目格式对齐 AGENTS.md 现有风格：编号 + 加粗规则句 +（实测次数与锚点）。
"""

from __future__ import annotations

import re

from .errstats import pattern_stats

# 模板表：正则（对归一模式或错误原文匹配）→ (规则句, 做法说明)。
# 每条模板都源自本库 steps 表真实踩过的错误，不是通用建议。
_TEMPLATES: list[tuple[str, str, str]] = [
    (r"tool_permission_revoked",
     "权限被撤销的工具不要原地重试。",
     "出现 tool_permission_revoked（read/ls/find 等）说明用户已撤销授权，"
     "重试只会重复失败；先向用户说明意图请求重新授权，或改走无权限要求的路径。"),
    (r"sandbox",
     "沙箱拦截不等于命令有错。",
     "sandbox-center 拦截时先判断命令是否真的需要越界；确需越界时显式申请"
     "（dangerouslyDisableSandbox 走用户批准），否则改写为无越界写法。"),
    (r"Win32|SetNamedSecurityInfo",
     "写 ACL 失败是环境权限问题，不要反复重试同一命令。",
     "grantWrite 报 Win32 5（拒绝访问）时先检查目录所有权/占用，"
     "或换目标路径；同一命令重复撞墙只会刷失败率。"),
    (r"fetch failed|socket hang up",
     "网络类失败先重试一次再下结论。",
     "web fetch / socket 报错多为瞬时故障（代理/网络抖动），"
     "直接放弃会把假阴性当结论留档。"),
    (r"browser_instance_unknown|browser_cancelled",
     "browser 工具报实例失效时先重建实例再继续。",
     "browser_instance_unknown / browser_cancelled 说明浏览器会话已失效，"
     "重试原调用无意义。"),
    (r"exceeds maximum allowed tokens",
     "工具输出超限先收窄再取，不要一次性拉全量。",
     "result exceeds maximum allowed tokens 说明返回体过大被宿主截断；"
     "加 head_limit/分页/offset，或让结果落盘后按需读片段。"),
    (r"String to replace not found|old_string|file changed since|"
     r"not been read|Found N matches|replace_all",
     "Edit/Write 前必须先 Read 目标文件最新内容。",
     "old_string 不匹配 / file changed since read / not been read / "
     "多处匹配都源于拿着过期记忆改文件；先重读，多处匹配用 replace_all。"),
    (r"exceeds maxDepth",
     "子代理嵌套深度受 maxDepth 限制，深任务改平铺编排。",
     "subagent depth exceeds maxDepth 时不要继续嵌套，回到主代理分层调度。"),
    (r"out of range",
     "read 的 offset 要先确认文件行数，不要盲猜。",
     "offset out of range 说明行号是猜的；先读尾部或用检索定位再取段。"),
    (r"file no longer exists|File not found",
     "引用记忆里的旧路径前先确认目标仍在。",
     "file no longer exists / File not found 说明目标已被删除或改名；"
     "写/改前先探存在性，失败后不要用同一路径重试。"),
    (r"skill_asset_invalid",
     "技能包引用的资产先校验存在性再运行。",
     "skill_asset_invalid 说明技能声明的资产路径失效；"
     "技能安装/更新后先跑资产校验。"),
]


def _hits(creg: re.Pattern, pattern: str, samples: list) -> bool:
    """模板是否命中该模式：归一模式串或任一样本原文前 200 字符。"""
    if creg.search(pattern):
        return True
    return any(creg.search(raw[:200]) for _sid, _seq, raw in samples)


def build_suggestion_entries(errors: list[dict], min_count: int = 3,
                             max_items: int = 15) -> dict:
    """结构化建议构建（build_suggestions 的纯数据层，API 直接消费）。

    返回 {"entries": [...], "leftover": [...], "min_count": n}：
    - entries 按 total 降序、截 max_items，每项 {title, body, total,
      samples: [(sid, seq, raw)]}；
    - leftover 为未命中任何模板的 (pattern, stats) 列表，按 count 降序
      （stats 含 class/count/tools(set)/samples）——"待人工归因"节与
      前端报告页均消费此结构。
    """
    stats = pattern_stats(errors)
    entries: list[dict] = []
    consumed: set[str] = set()
    for reg, title, body in _TEMPLATES:
        creg = re.compile(reg, re.IGNORECASE)
        total, samples = 0, []
        for pat, d in stats.items():
            if _hits(creg, pat, d["samples"]):
                total += d["count"]
                consumed.add(pat)
                samples += d["samples"]
        if total >= min_count and samples:
            entries.append({"title": title, "body": body, "total": total,
                            "samples": samples})
    entries.sort(key=lambda e: -e["total"])
    entries = entries[:max_items]
    leftover = [(pat, d) for pat, d in stats.items() if pat not in consumed]
    leftover.sort(key=lambda kv: -kv[1]["count"])
    return {"entries": entries, "leftover": leftover, "min_count": min_count}


def build_suggestions(errors: list[dict], min_count: int = 3,
                      max_items: int = 15) -> str:
    """从归类后的错误生成 AGENTS.md 候选条目 Markdown（数据层见
    build_suggestion_entries；本函数只做渲染，保证两种出口同构）。"""
    built = build_suggestion_entries(errors, min_count=min_count,
                                     max_items=max_items)
    entries, leftover = built["entries"], built["leftover"]

    lines = [
        "# AGENTS.md 条目建议（机器产出，人工审阅后并入）",
        "",
        "> 来源：harvester steps 表错误三分类；每条附真实锚点与原文证据。",
        "> 纪律：本文件只是建议池，**不自动写入 AGENTS.md**；并入时按",
        "> AGENTS.md 现有编号顺延，并清理被取代的同类旧条目。",
        "",
    ]
    if not entries:
        lines += [f"（无达标模式：所有模板聚合次数均未达到 --min-count "
                  f"{min_count}。）", ""]
    for i, e in enumerate(entries, 1):
        sid, seq, raw = e["samples"][0]
        raw_short = raw[:100].replace("\n", " ").replace("`", "'")
        lines += [
            f"{i}. **{e['title']}**",
            f"   {e['body']}",
            f"   实测 {e['total']} 次，证据：`{sid}#{seq}`「{raw_short}」",
            "",
        ]
    if leftover:
        lines += ["## 待人工归因（未命中模板，给证据不给结论）", ""]
        for pat, d in leftover[:10]:
            lines.append(f"- **x{d['count']}** [{d['class']}] `{pat}`")
            for sid, seq, raw in d["samples"][:2]:
                lines.append(f"      - `{sid}#{seq}` {raw[:140]}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
