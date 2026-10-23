# -*- coding: utf-8 -*-
"""错误三分类统计（report-errors 子命令）——G2 的确定性一半。

对应《轨迹分析工具设计方案》§6 report-errors 规格：
- 错误三分类：env / tool_interface / context（正则映射，规则序即优先级）；
- 按轨迹位置分桶：开场 / 中途 / 收尾（以错误步骤 seq 在全会话 steps 中的
  相对位置近似，steps 无用户轮次边界）。

三分类语义（写入代码与文档，勿混）：
【N1 术语标注】env/tool_interface/context/unclassified 是**本项目自定义分类**
（行业无直接对应；官方可观测性惯例参考 OpenTelemetry SemConv 的
`error.type`/`exception.type`——custom 值须由使用方文档化，本项目文档化处
= docs/TERMINOLOGY.md §2.2），UI 与文档凡出现须带此标注：
- env            环境问题：沙箱/权限撤销/OS/网络/宿主限制——agent 无法自愈；
- tool_interface 工具用法问题：参数错、先改后读、盲猜行号——改用法即消；
- context        目标状态与预期不符：文件已删/资产失效——需重新侦察目标；
- unclassified   未命中任何规则，报告中显式列出（不许静默吞掉）。

模式归一：把同类错误的差异部分（路径/引号内容/数字）替换为占位符后聚类，
使 "cannot edit a.md" 与 "cannot edit b.py" 聚成同一模式。

失败后续行为口径定义（v0.19 固化，toolstats/建议池/排行共用，勿再各说各话）：
- 错误步骤：steps 表 phase='result' 且 status='error' 的行（错误只出现在
  result 行；phase='call' 行 status 恒为 NULL，统计 calls 时必须过滤）；
- 重试（retried）：某错误步骤之后，同会话（sid）内同一工具（tool）再次
  出现 phase='call' 行 → 该错误记为已重试（自愈候选）；
- 放弃（given_up）：某错误步骤之后，同会话内该工具再无调用 → 记为未解决
  （unresolved）。given_up = errors - retried；
- 近似性声明：steps 表无用户轮次边界，"再次调用"不区分是否人为决策；
  --since_days 截断处跨界的重试对会漏配对（截断使后续 call 不可见，
  重试被误记为放弃），属已知近似，跨期对比时两侧用同一窗口；
- 落点：collect_errors_from_db 为每条错误附 resolved 布尔字段；
  pattern_stats 聚合出每模式 given_up 计数；建议池（agent_suggest）与
  G1 工具行（toolstats.ToolStats.given_up）消费该维度。
"""

from __future__ import annotations

import re
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

from .dbmeta import ro_con

CLASSES = ("env", "tool_interface", "context", "unclassified")

# 分类规则：列表序 = 判定优先级。先 env 后 context 是刻意的——
# "old_string was not found in <path>" 必须先被 tool_interface 的 old_string
# 规则截住，不能落进 context 的 "not found"。
_RULES: list[tuple[str, list[str]]] = [
    ("env", [
        r"tool_permission_revoked",
        r"sandbox",
        r"Win32|SetNamedSecurityInfo",
        r"Permission to use",
        r"fetch failed|socket hang up|ECONN|ETIMEDOUT|\bnetwork\b",
        r"web fetch",
        r"timeout|timed out",
        r"browser_instance_unknown|browser_cancelled",
        r"host_bridge_declared_error",
        r"exceeds maximum allowed tokens",
        r"LOCKED|EACCES|EPERM|permission denied|access denied",
        r"secret_detected",  # 安全护栏拦截（artifact_secret_detected）
        r"拒绝访问",
    ]),
    ("tool_interface", [
        r"String to replace not found|old_string",
        r"file changed since it was read|has not been read|not been read yet",
        r"Found \d+ matches|replace_all",
        r"match_not_found",  # workspace_edit_match_not_found 等编辑类
        r"out of range",
        r"exceeds max|exceeds maxDepth",
        r"Edit error on",
        r"(?<!skill_asset_)invalid|unsupported|malformed",
        r"regex parse error",  # grep 正则写错
        r"multiple_in_progress",  # checklist 状态冲突（用法时序）
        r"No files were searched",  # rg 过滤器把目标全滤掉了
    ]),
    ("context", [
        r"file no longer exists|File not found|does not exist|不存在",
        r"not found|找不到|os error 2",  # 目标缺失（edit 老串已被上层规则截住）
        r"skill_asset",  # 资产失效/不可执行
        r"obscured",  # 页面元素被遮挡（目标状态变了）
        r"unknown job",  # 引用的后台任务已不存在
        r"filesystem_not_found",
        r"IO error",  # rg IO 错误（路径失效/不可达）
        r"plan_changes_requested",  # 用户要求改方案（目标变了）
    ]),
]

_COMPILED = [(cls, [re.compile(p, re.IGNORECASE) for p in pats])
             for cls, pats in _RULES]

# 模式归一用
_PATH_RE = re.compile(r"[A-Za-z]:[\\/][^\s\"']+")
_QUOTED_RE = re.compile(r"\"[^\"]*\"|'[^']*'")
_NUM_RE = re.compile(r"\d+")
_WS_RE = re.compile(r"\s+")


def classify_error(text: str) -> str:
    """返回错误三分类。**类别只有一个来源：归因层**（v0.80）。

    口径变更（用户 2026-10-23 批准）：原先只有 `_RULES` 三条英文短语正则，
    DSH 抛的 `FS_*` 大写下划线码与它们字面不相交 → 一律 `unclassified`
    （实测 84 次的 FS_STALE_VERSION 也落在这里）。归因层
    `harvester/data/attrib-rules.yaml` 已覆盖 187/189 种错误串、且每条都带
    `why`/`fix`，所以类别改由它出：**同一份规则既出类别又出根因/应对**，
    报告页与蒸馏队列因此只有一个口径。

    归因层不可用时（缺 PyYAML / 规则表缺失）退回老的三条正则，再不行才
    `unclassified`——降级只影响"类别精度"，不影响任何计数，也不静默。
    """
    rules = _attrib_rules()
    if rules:
        try:
            from . import attrib
            hit = attrib.attribute_face(text, rules, face="errors")
        except Exception:  # noqa: BLE001 - 归因层坏掉不该让采集/报告崩
            hit = None
        if hit and hit.get("class"):
            return hit["class"]
    for cls, regs in _COMPILED:
        for reg in regs:
            if reg.search(text):
                return cls
    return "unclassified"


_ATTRIB_RULES: list[dict] | None = None
_ATTRIB_TRIED = False


def _attrib_rules() -> list[dict] | None:
    """懒加载归因规则（**函数内 import**：attrib 反向依赖本模块，顶部 import 会成环）。

    失败返回 None（降级到老正则），缓存一次——不要在每条错误上重读 YAML。
    """
    global _ATTRIB_RULES, _ATTRIB_TRIED
    if _ATTRIB_TRIED:
        return _ATTRIB_RULES
    _ATTRIB_TRIED = True
    try:
        from . import attrib
        _ATTRIB_RULES = attrib.load_rules()
    except Exception:  # noqa: BLE001 - 缺 PyYAML/规则表 → 降级，不阻断报告
        _ATTRIB_RULES = None
    return _ATTRIB_RULES


def attrib_hit(text: str) -> dict | None:
    """类别 + 根因 + 应对（蒸馏队列要 why/fix 给"为什么/怎么修"）。"""
    rules = _attrib_rules()
    if not rules:
        return None
    try:
        from . import attrib
        return attrib.attribute_face(text, rules, face="errors")
    except Exception:  # noqa: BLE001
        return None


def normalize_error(text: str) -> str:
    """把同类错误的差异部分替换为占位符，产出聚类键。"""
    s = text.splitlines()[0] if text else ""
    s = re.sub(r"^Error:\s*", "", s)
    s = _QUOTED_RE.sub("<q>", _PATH_RE.sub("<path>", s))
    s = _NUM_RE.sub("N", s)
    s = _WS_RE.sub(" ", s).strip()
    return s[:100]


#: 工具回显标记：Edit 类失败原文常把 old_string/new_string 的用户文档内容
#: 附在错误文本尾部，污染证据展示（v0.20 G1 修正经验，P1-2 提为共享口径）。
_ECHO_MARKERS = ("String:", "old_string was", "Input:")


def clean_error_sample(raw: str, max_len: int | None = None) -> str:
    """错误原文 → 单行证据引用（P1-2 共享口径，建议池/G1 报告同源）。

    - 截掉工具回显（_ECHO_MARKERS 起的用户文档内容，错误本体只在前段）；
    - 空白（含换行）折叠为单空格；
    - 缺省**不截断**（全文展示交给消费方；max_len 供表格类展示传截断值）。
    """
    s = raw or ""
    for marker in _ECHO_MARKERS:
        if marker in s:
            s = s.split(marker)[0]
            break
    s = _WS_RE.sub(" ", s).strip()
    if max_len is not None:
        s = s[:max_len]
    return s


def _bucket(ratio: float) -> str:
    if ratio <= 1 / 3:
        return "开场"
    if ratio <= 2 / 3:
        return "中途"
    return "收尾"


def collect_errors_from_db(db: Path, since_days: float | None = None,
                           con: sqlite3.Connection | None = None
                           ) -> tuple[list[dict], dict]:
    """从 steps 表取全部错误步骤并归类。

    返回 (errors, meta)：errors 每项含 sid/seq/ts/tool/error/class/pattern/
    bucket/resolved/model（P1-3 additive：model NULL 归"（未知）"）；
    meta = {"total_steps": n, "error_count": n, "sessions": n,
    "since": cutoff 或 None}。resolved（v0.19 additive）：该错误之后同会话
    同工具是否有再次调用（True=重试/自愈候选，False=放弃/未解决），口径见
    模块 docstring。
    --since_days 只统计最近 N 天（ts 为 'YYYY-MM-DD HH:MM:SS' 字符串，
    字典序即时间序）。
    con：外部连接（api-serve 传入 mode=ro + authorizer 连接，使 steps
    全表扫描同样处于内核级只读之下）；缺省自开 mode=ro 连接（缺库
    fail loud，v0.51 S3）。传入时 db 参数被忽略。
    """
    cutoff = None
    if since_days is not None:
        cutoff = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(time.time() - since_days * 86400))
    con, own = ro_con(db, con)
    try:
        if cutoff:
            rows = con.execute(
                "SELECT st.sid, st.seq, st.ts, st.tool, st.error, s.source, "
                "s.model "
                "FROM steps st LEFT JOIN sessions s ON st.sid = s.sid "
                "WHERE st.status=? AND st.ts>=? ORDER BY st.sid, st.seq",
                ("error", cutoff)).fetchall()
            total = con.execute(
                "SELECT COUNT(*) FROM steps WHERE ts>=?", (cutoff,)).fetchone()[0]
            call_rows = con.execute(
                "SELECT sid, tool, seq FROM steps WHERE phase='call' AND ts>=? "
                "ORDER BY sid, seq", (cutoff,)).fetchall()
        else:
            rows = con.execute(
                "SELECT st.sid, st.seq, st.ts, st.tool, st.error, s.source, "
                "s.model "
                "FROM steps st LEFT JOIN sessions s ON st.sid = s.sid "
                "WHERE st.status=? ORDER BY st.sid, st.seq", ("error",)).fetchall()
            total = con.execute("SELECT COUNT(*) FROM steps").fetchone()[0]
            call_rows = con.execute(
                "SELECT sid, tool, seq FROM steps WHERE phase='call' "
                "ORDER BY sid, seq").fetchall()
        max_seq = dict(con.execute(
            "SELECT sid, MAX(seq) FROM steps GROUP BY sid").fetchall())
        # v0.23 #14：skill 维度归因——取全会话的 call 相行，按 seq 序求
        # "该错误发生时活跃的 skill"（最近一次 Skill 类调用的 skill 名）。
        if cutoff:
            skill_rows = con.execute(
                "SELECT sid, seq, tool, detail FROM steps "
                "WHERE phase='call' AND ts>=? ORDER BY sid, seq",
                (cutoff,)).fetchall()
        else:
            skill_rows = con.execute(
                "SELECT sid, seq, tool, detail FROM steps "
                "WHERE phase='call' ORDER BY sid, seq").fetchall()
    finally:
        if own:
            con.close()
    # 重试判定索引：sid → {tool: [call seq 升序]}（同一连接内取，守卫约束）
    calls_by_sid: dict[str, dict[str, list[int]]] = defaultdict(
        lambda: defaultdict(list))
    for c in call_rows:
        calls_by_sid[c["sid"]][c["tool"]].append(c["seq"])

    def _resolved(sid: str, tool: str, seq: int) -> bool:
        seqs = calls_by_sid.get(sid, {}).get(tool, [])
        # seqs 升序；二分找是否存在 > seq 的调用
        lo, hi = 0, len(seqs)
        while lo < hi:
            mid = (lo + hi) // 2
            if seqs[mid] > seq:
                hi = mid
            else:
                lo = mid + 1
        return lo < len(seqs)

    # v0.23 #14：skill 维度索引（call 相行 → sid → [(seq, skill)]）
    skill_by_sid = _build_skill_index(skill_rows)
    errors = []
    for r in rows:
        hi = max_seq.get(r["sid"], 0) or 1
        ratio = r["seq"] / hi
        errors.append({
            "sid": r["sid"], "seq": r["seq"], "ts": r["ts"],
            "tool": r["tool"], "error": r["error"] or "",
            "source": r["source"] or "?",
            # P1-3：model 列（NULL 归"（未知）"，与 facets 口径一致）
            "model": r["model"] or "（未知）",
            "class": classify_error(r["error"] or ""),
            "pattern": normalize_error(r["error"] or ""),
            "bucket": _bucket(ratio),
            "resolved": _resolved(r["sid"], r["tool"], r["seq"]),
            # v0.23 #14：活跃 skill（无则 None）——只增字段，不动既有键
            "skill": _active_skill(skill_by_sid, r["sid"], r["seq"], r["tool"]),
        })
    meta = {"total_steps": total, "error_count": len(errors),
            "sessions": len({e["sid"] for e in errors}), "since": cutoff}
    return errors, meta


def _skill_name_of(tool: str, detail: str | None) -> str | None:
    """从 call 相行取 skill 名；非 Skill 类调用返回 None。

    **必须先按 SKILL_TOOLS 白名单过滤**：`_skill_name_from_detail` 对任意
    detail 都会尝试解析，且其键回退链含 `command`（为 skill 自身的
    command 形态服务）——若不过滤，pwsh/Bash 的 `{"command": "icacls ..."}`
    会被当成 skill 名（真实库实测过：出现 "icacls tests\\... | dsh" 这种
    "skill"）。白名单与 behstats.collect_skill_invocations 的判定同源。
    """
    try:
        from .behstats import SKILL_TOOLS, _skill_name_from_detail
    except ImportError:  # pragma: no cover - 理论上不会发生
        return None
    if tool not in SKILL_TOOLS | {"skill_read_active", "skill_run_asset"}:
        return None
    name, _rest = _skill_name_from_detail(tool, detail)
    return name or None


def _build_skill_index(skill_rows) -> dict[str, list[tuple[int, str]]]:
    """sid → [(call_seq, skill_name)] 升序。"""
    out: dict[str, list[tuple[int, str]]] = {}
    for r in skill_rows:
        name = _skill_name_of(r["tool"], r["detail"])
        if name:
            out.setdefault(r["sid"], []).append((r["seq"], name))
    for v in out.values():
        v.sort()
    return out


def _active_skill(index: dict[str, list[tuple[int, str]]], sid: str,
                  seq: int, tool: str) -> str | None:
    """错误步骤发生时"活跃"的 skill。

    若错误本身出自 Skill 调用，取它自己；否则取该会话内**最近一次**
    Skill 调用（seq ≤ 错误 seq）的 skill 名——近似口径：skill 一旦载入
    即视为持续生效至会话结束。无 skill 参与则为 None。
    """
    own = index.get(sid) or []
    if not own:
        return None
    if (tool or "") == "Skill":
        for cseq, name in own:
            if cseq == seq:
                return name
    best: str | None = None
    for cseq, name in own:
        if cseq <= seq:
            best = name
        else:
            break
    return best


def pattern_stats(errors: list[dict]) -> dict[str, dict]:
    """按归一模式聚类：pattern → {class, count, tools, samples[(sid,seq,text)],
    given_up, first_seen}。given_up（v0.19 additive）：模式内未解决（放弃）
    次数 = resolved=False 的错误数；旧调用方传入无 resolved 字段的错误时按
    resolved=True（自愈）处理，不误报未解决。
    first_seen（v0.80 additive）：该模式在**本次输入里**最早出现的时刻
    （ts 字典序即时间序）。用途：区分"老坑复发"与"窗口内新坑"——新意
    与次数是两件事，1 次的新坑也值得做卡（用户 2026-10-23 批准的方案 B）。
    """
    out: dict[str, dict] = {}
    for e in errors:
        p = out.setdefault(e["pattern"], {
            "class": e["class"], "count": 0, "tools": set(), "samples": [],
            "given_up": 0, "first_seen": e.get("ts") or ""})
        p["count"] += 1
        p["tools"].add(e["tool"])
        ts = e.get("ts") or ""
        if ts and (not p["first_seen"] or ts < p["first_seen"]):
            p["first_seen"] = ts
        if not e.get("resolved", True):
            p["given_up"] += 1
        if len(p["samples"]) < 3:
            p["samples"].append((e["sid"], e["seq"], e["error"]))
    return out


def cross_stats(errors: list[dict]) -> list[dict]:
    """P1-3 交叉表：数据源（harness）× model × 错误类别 → 计数行。

    输入为 collect_errors_from_db 产出的 errors（class 已由 classify_error
    单点口径归类，本函数不再二次分类）。每行含 source/model/四类计数/total，
    按 total 降序、source/model 升序排列——首行即"哪个 harness 的哪类坑
    最多"。聚合与 by_class/by_source 对账恒等（消费方应校验 sum(total)）。
    """
    agg: dict[tuple[str, str], dict[str, int]] = {}
    for e in errors:
        key = (e["source"], e.get("model") or "（未知）")
        row = agg.setdefault(key, {c: 0 for c in CLASSES})
        row[e["class"]] += 1
    rows = [{"source": src, "model": model, **counts, "total": sum(counts.values())}
            for (src, model), counts in agg.items()]
    rows.sort(key=lambda r: (-r["total"], r["source"], r["model"]))
    return rows


def cross_stats_by_skill(errors: list[dict]) -> list[dict]:
    """v0.23 #14 交叉表：活跃 skill × 数据源（harness）× 错误类别。

    与 cross_stats 同构，但把 model 换成 skill——回答"哪个 skill 在哪个
    harness 上出哪类错误"（用户的五类实体里，skill 是原交叉表唯一缺的一维）。
    `skill=None` 的错误（无 skill 参与的步骤）归"（无 skill）"，不丢数；
    sum(total) 恒等于 cross_stats 的 sum(total)（同一 errors 输入）。
    """
    agg: dict[tuple[str, str], dict[str, int]] = {}
    for e in errors:
        key = (e.get("skill") or "（无 skill）", e["source"])
        row = agg.setdefault(key, {c: 0 for c in CLASSES})
        row[e["class"]] += 1
    rows = [{"skill": sk, "source": src, **counts,
             "total": sum(counts.values())}
            for (sk, src), counts in agg.items()]
    rows.sort(key=lambda r: (-r["total"], r["skill"], r["source"]))
    return rows


def _render_attrib_line(entry: dict | None) -> str:
    """把一条模式的归因候选渲染成一行（P6-5）。

    **不截断 why/fix**：它们是要照着做的东西，截断等于让人按半句话行动。
    未命中规则（entry 为 None）显式返回"未归因"——**不许静默省略**：
    省略会让人以为"这行没有问题"，而事实是"机器没认出来"（A1 三层分离）。
    """
    if not entry or not entry.get("rule"):
        return "      - 归因：**未归因**（机器未匹配任何规则；不是「没问题」）"
    r = entry["rule"]
    conf = {"high": "高", "medium": "中", "low": "低"}.get(
        r.get("confidence"), r.get("confidence") or "未标")
    # 归因类别与事实层类别不一致时显式标出：事实层计数**未被覆盖**（additive），
    # 但在报告里让人看见"这条其实是用法病、只是旧规则没认出来"是有用信号。
    cls_note = ""
    if r.get("class") and r.get("class") != entry.get("class"):
        cls_note = f"；归因类别 {r['class']}（事实层仍记 {entry.get('class')}）"
    return (f"      - 归因候选 `{r.get('rule_id')}`（置信 {conf}{cls_note}）"
            f"：{r.get('why')}　→ 应对：{r.get('fix')}")


def reviewable_line(n: int, attributions: dict | None) -> str:
    """本报告面的可评审等级（P6-7）。**判据只有一份**：`reviewable.py`。

    `n` = 本窗口错误条目数；`attributions` = `attrib.collect_attributions`
    的返回（None = 归因层未启用）。**没归因就只能是 R1，绝不因"报告很长"
    而升级**——等级由内容算，不由篇幅算。
    """
    from .reviewable import assess
    if attributions is None:
        cand = 0
        note = "归因层未启用：本面只能数、不能评"
    else:
        s = attributions.get("summary") or {}
        cand = int(s.get("errors_attributed") or 0)
        note = (f"归因覆盖 {cand}/{s.get('errors_total', n)} 条"
                if cand else "规则全未命中：有计数、无根因候选")
    lv = assess(entries=n, candidates=cand, note=note)
    return lv.render()


def _attrib_header(attributions: dict | None) -> str:
    """报告头的归因状态声明（P6-5）。三态必须能分开，不许含糊。

    - None            归因层未启用 → 明说"本报告不含归因"（不是"全部未归因"）；
    - 已启用但零命中   明说"0 条命中"，让人知道规则表没起作用；
    - 正常            给出已归因/未归因的确切条数。
    """
    if attributions is None:
        return ("- 归因：**本报告不含归因**（归因层未启用：规则表不可用或无 PyYAML）"
                "；模式行只给事实层聚类，**不代表「未归因」**")
    s = attributions.get("summary") or {}
    tot = s.get("errors_total", 0)
    got = s.get("errors_attributed", 0)
    rules = s.get("rules_hit", 0)
    rules_tot = s.get("rules_total", 0)
    return (f"- 归因（**候选**，机器判断会错；裁决请回填 attribations 台账）："
            f"已归因 {got} / {tot} 条，未归因 {tot - got} 条；"
            f"规则命中 {rules} / {rules_tot} 条")


def render_report(errors: list[dict], meta: dict,
                  max_detail: int = 12,
                  attributions: dict | None = None) -> str:
    """渲染 Markdown 报告：三分类分布 + 位置分桶 + 模式明细（带锚点）。

    `attributions`（P6-5，可选）是 `attrib.collect_attributions` 的返回：
      - **None** = 归因层未启用（如无 PyYAML）。此时报告**一行归因都不印**，
        并在头部显式声明"本报告不含归因"——绝不把"规则没载入"渲染成
        "未归因"，那是把系统故障说成数据结论（本模块 docstring 同口径）。
      - 传入 dict 时：每个模式行带根因候选（why）+ 应对（fix）+ 置信；
        未命中的行显式标"未归因"。

    errstats **不 import attrib**（attrib 反过来 import 本模块，且事实层必须
    独立于归因层）：归因数据一律由调用方传入。
    """
    title = "错误三分类报告"
    if meta.get("since"):
        title += f"（最近 {meta['since']} 起）"
    if not errors:
        return (f"# {title}\n\n{reviewable_line(0, attributions)}\n\n"
                f"steps 表无错误记录（总步骤 {meta.get('total_steps', 0)}）。\n")
    by_class: dict[str, int] = defaultdict(int)
    by_bucket: dict[str, int] = defaultdict(int)
    for e in errors:
        by_class[e["class"]] += 1
        by_bucket[e["bucket"]] += 1
    n = len(errors)
    lines = [
        f"# {title}", "",
        reviewable_line(n, attributions), "",
        f"- 错误步骤 {n} / 总步骤 {meta['total_steps']}"
        f"（失败率 {n / max(meta['total_steps'], 1) * 100:.1f}%），"
        f"涉及 {meta['sessions']} 个会话", "",
        _attrib_header(attributions), "",
        "## 三分类分布", "",
        "> 类别为本项目自定义分类（行业无直接对应；官方惯例参考 "
        "OpenTelemetry error.type，见 docs/TERMINOLOGY.md §2.2）", "",
        "| 类别 | 次数 | 占比 | 语义 |",
        "|---|---:|---:|---|",
        "| env | %d | %.0f%% | 环境：沙箱/权限/OS/网络/宿主限制，agent 无法自愈 |" % (
            by_class["env"], by_class["env"] / n * 100),
        "| tool_interface | %d | %.0f%% | 用法：参数错/先改后读/盲猜行号，改用法即消 |" % (
            by_class["tool_interface"], by_class["tool_interface"] / n * 100),
        "| context | %d | %.0f%% | 目标状态：文件已删/资产失效，需重新侦察 |" % (
            by_class["context"], by_class["context"] / n * 100),
        "| unclassified | %d | %.0f%% | 未命中规则，需人工归因后补规则 |" % (
            by_class["unclassified"], by_class["unclassified"] / n * 100),
        "",
        "## 轨迹位置分桶", "",
        "| 开场（前 1/3） | 中途（中 1/3） | 收尾（后 1/3） |",
        "|---:|---:|---:|",
        "| %d | %d | %d |" % (by_bucket["开场"], by_bucket["中途"],
                              by_bucket["收尾"]),
        "",
        "- 口径：错误步骤 seq 在全会话 steps 中的相对位置（无用户轮次边界，"
        "近似桶）。开场高发=环境/权限未就绪；收尾高发=收尾动作踩坑。",
    ]
    # 按 Agent（数据源）分布：steps 只来自 agent harness 会话
    by_src: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for e in errors:
        by_src[e["source"]][e["class"]] += 1
    if len(by_src) > 1:
        lines += [
            "",
            "## 按 Agent（数据源）分布",
            "",
            "| 数据源 | env | tool_interface | context | unclassified |",
            "|---|---:|---:|---:|---:|",
        ]
        for src in sorted(by_src, key=lambda s: -sum(by_src[s].values())):
            d = by_src[src]
            lines.append("| %s | %d | %d | %d | %d |" % (
                src, d["env"], d["tool_interface"], d["context"],
                d["unclassified"]))
        lines += [
            "",
            "- 说明：数据源即 Agent Harness 身份（如 workbuddy-transcript=WorkBuddy、"
            "dsh=DSH、autoclaw、vscode-copilot）；Chat 平台会话无工具遥测，不产生错误步骤。",
        ]
    # P1-3 交叉表：数据源（harness）× model × 错误类别
    cross = cross_stats(errors)
    lines += [
        "",
        "## 数据源 × model × 错误类别交叉表",
        "",
        "| 数据源 | model | env | tool_interface | context | unclassified | 合计 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for r in cross:
        lines.append("| %s | %s | %d | %d | %d | %d | %d |" % (
            r["source"], r["model"], r["env"], r["tool_interface"],
            r["context"], r["unclassified"], r["total"]))
    top = cross[0]
    worst_cls = max(CLASSES, key=lambda c: top[c])
    lines += [
        "",
        f"- 最多坑组合：**{top['source']} × {top['model']}**（{top['total']} 条，"
        f"以 {worst_cls} 为主）；model 列\"（未知）\"= 会话未携带模型信息"
        "（导出型源范围外，见 H29 口径）。",
    ]
    # v0.23 #14：skill × 数据源 × 错误类别（用户五类实体里 skill 原缺一维）
    sk_cross = cross_stats_by_skill(errors)
    lines += [
        "",
        "## 活跃 skill × 数据源 × 错误类别交叉表",
        "",
        "| skill | 数据源 | env | tool_interface | context | unclassified | 合计 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for r in sk_cross:
        lines.append("| %s | %s | %d | %d | %d | %d | %d |" % (
            r["skill"], r["source"], r["env"], r["tool_interface"],
            r["context"], r["unclassified"], r["total"]))
    n_noskill = sum(r["total"] for r in sk_cross if r["skill"] == "（无 skill）")
    lines += [
        "",
        f"- 口径：skill = 该错误步骤发生时**最近一次 Skill 类调用**载入的技能"
        f"（skill 一旦载入即视为持续生效至会话结束）；\"（无 skill）\""
        f"{n_noskill} 条 = 会话内无 skill 参与。合计与上表恒等（同一 errors 输入）。",
    ]
    # 模式明细，按 class 分组、count 降序
    stats = pattern_stats(errors)
    order = sorted(stats.items(), key=lambda kv: (-kv[1]["count"], kv[0]))
    by_pat = (attributions or {}).get("by_pattern") or {}
    lines += ["", "## 错误模式明细（归一聚类，带锚点）", ""]
    for cls in CLASSES:
        items = [(p, d) for p, d in order if d["class"] == cls]
        if not items:
            continue
        lines.append(f"### {cls}")
        for pat, d in items[:max_detail]:
            tools = "/".join(sorted(d["tools"]))
            unsolved = d.get("given_up", 0)
            lines.append(f"- **x{d['count']}** [{tools}] `{pat}`"
                         + (f"（未解决 {unsolved}）" if unsolved else ""))
            if attributions is not None:
                lines.append(_render_attrib_line(by_pat.get(pat)))
            for sid, seq, raw in d["samples"][:2]:
                lines.append(f"      - `{sid}#{seq}` {raw[:140]}")
        if len(items) > max_detail:
            lines.append(f"- （其余 {len(items) - max_detail} 个模式从略）")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
