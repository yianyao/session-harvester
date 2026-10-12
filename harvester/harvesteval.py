# -*- coding: utf-8 -*-
"""自进化评估闭环（harvest-eval 子命令）——SOP P1-4。

回答的问题是："规则并进去之后，**那一类错误**到底有没有变好？"现在只有
"人在环路的半自动"：`suggest-agents` 产出建议、人裁决进 `AGENTS.md`，然后就
没人回头核对效果了。本模块对每条 **adopted** 建议做分界时刻 T 前后各 N 天的
A/B，给出**三值判定 + 样本量**，让"退休 / 回滚"有数据依据。

纪律（写进代码与文档，勿混）：
- **工具只评估，绝不自动改 `AGENTS.md`**。这里的"自"= 评估自动化，
  不是改动自动化：退休/回滚/保持都只是**建议**，改不改由人决定；
- **不下结论 ≠ 无变化**。样本不足、没有分界日期一律写清楚原因与样本量，
  不许把"没数据"说成"没变化"（那正是噪声墙的来源）；
- 只读 `harvester.db`（`file:...?mode=ro` 只读连接）。

口径（A/B，全部可复算）：
- 分界时刻 T = 该条建议的 `adopted_at`；没有就用调用方给的 `since`；
  两者都没有 → 该条 `不下结论（无分界日期）`，如实列出，**不**落到"无变化"；
- 前窗口 `[T - N 天, T)`、后窗口 `[T, T + N 天)`（左闭右开；ts 为
  `'YYYY-MM-DD HH:MM:SS'` 字符串，零填充保证字典序 == 时间序，与 errstats 同口径）；
- key ↔ 错误的匹配：key 与错误的 pattern 都用 `errstats.normalize_error` 归一后
  比较（相等，或长串包含短串）——key 可以是错误 pattern（leftover 采纳），
  也可以是建议句原文（条目采纳）。**建议句原文与错误 pattern 天然不啮合**，
  所以台账 `note` 里可写 `pattern: <错误 pattern 原文>`（语法见
  `parse_note_patterns`；**可写多条，取并集**）：写了就用它匹配
  （`match_source="note.pattern"`），没写才退回用 key 匹配
  （`match_source="key"`）；note 的 pattern 命中 0 条时照旧如实报样本 0 /
  不下结论，并在理由里点明；
- `topic:<id>` 命名空间的台账行（P2-5 人账：主题注册表照抄的登记留痕）**整体跳过**，
  单列 `skipped_topic`：主题不是规则、天然没有错误 pattern，逐条评估只会产出
  "不下结论（样本不足 0/0）"的噪音；
- **未解决率** = 窗口内 `resolved == False` 的错误数 / 窗口内命中错误数
  （resolved 口径见 errstats：错误后同会话同工具是否再次调用）；
- **复发率** = 窗口内"含该 pattern 的会话"里出现 **>= 2 次** 的会话占比；
- 判定**以未解决率为主要判据**（复发率只报告、不参与判定，避免两个指标
  各指一个方向时口径含糊）：相对变化 < 10% → 无变化，下降 >= 10% → 改善，
  上升 >= 10% → 变差；未解决率从 0 变成 >0 记 +100%（"从无到有"）；
- **样本门槛优先于变化幅度**：两窗口各自命中错误数 < 5，或合计 < 20 →
  `不下结论（样本不足）`（哪怕未解决率翻了 10 倍——样本不足时那个数字不可信）。
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from .errstats import collect_errors_from_db, normalize_error
from .suggestmeta import TOPIC_PREFIX, load_records, validate_stamp

#: A/B 窗口：分界前后各多少天（缺省 7 天）
DEFAULT_WINDOW_DAYS = 7
#: 样本门槛：每个窗口至少多少条命中错误
DEFAULT_MIN_PER_WINDOW = 5
#: 样本门槛：两个窗口合计至少多少条
DEFAULT_MIN_TOTAL = 20
#: 判定阈值：相对变化达到多少才算改善/变差
DEFAULT_THRESHOLD = 0.10

VERDICT_IMPROVED = "改善"
VERDICT_UNCHANGED = "无变化"
VERDICT_WORSENED = "变差"
VERDICT_INCONCLUSIVE = "不下结论"

#: 展示/排序用的固定序（变差最需要看，放最前；确定性输出）
_VERDICT_ORDER = (VERDICT_WORSENED, VERDICT_UNCHANGED, VERDICT_IMPROVED,
                  VERDICT_INCONCLUSIVE)

#: 判定 → 处置建议（P1-4 验收第 ③ 条：防 AGENTS.md 膨胀成噪声墙）
_ACTIONS = {
    VERDICT_UNCHANGED: "该规则可退休（防 AGENTS.md 膨胀成噪声墙）",
    VERDICT_WORSENED: "建议回滚该规则",
    VERDICT_IMPROVED: "保持",
    VERDICT_INCONCLUSIVE: "样本不足，暂不处置",
}

#: 短串包含式匹配的最小长度：比这更短的 pattern（如 'sandbox'）反向包含在
#: 建议句里时，误命中代价大于收益，故不采用。
_MIN_FUZZY = 8

#: 台账 note 里的机读标记前缀（H31 口径的桥，语法见 parse_note_pattern）
_NOTE_PATTERN_PREFIX = "pattern:"

_REASON_NO_BOUNDARY = "无分界日期"
_REASON_FEW_SAMPLES = "样本不足"
#: note 里写了 pattern 却一条都没命中时的附加说明（如实说，不硬凑样本）
_NOTE_MISS = "note 里的 pattern 未命中任何错误"


def parse_note_patterns(note: str | None) -> list[str]:
    """从台账 `note` 里解析「这句建议针对哪些错误 pattern」的机读标记（可多条）。

    为什么需要它（H31 口径的桥）：台账 key 是**建议句原文**（如
    「Edit/Write 前必须先 Read 目标文件最新内容。」），而匹配要拿**错误
    pattern**去比对；两者只有在 key 恰好就是 pattern 时才啮合。要保持
    key 语义不变，就得给台账一个"这句话针对哪个 pattern"的字段——就用
    `note`（additive，不改 key、不改表结构）。

    语法（**唯一权威，README/SOP 照抄这一种**）：

        pattern: <错误 pattern 原文>

    在 note 里写一行；**可写多条**（换行或 `;` / `；` 分隔），评估时取并集
    ——一条规则常覆盖同一根因下的多个 pattern（如"改文件前先 Read"覆盖
    `file has not been read` / `file changed since it was read` /
    `old_string was not found` 等兄弟模式），只认一条会**系统性少算样本**。
    多条建议用换行分隔：值里含 `;`/`；` 时会被当分隔符切开（容错，不报错）。

    解析：先按换行切、每行再按 `;`/`；` 切片段，凡以 `pattern:` 开头
    （大小写不敏感、允许前导空白）且冒号后有内容的片段都收；顺序保留。
    容错：没有该片段 / 值空 / note 不是字符串 → 空列表（当"没写"）。
    """
    if not isinstance(note, str):
        return []
    out: list[str] = []
    for line in note.splitlines():
        for piece in re.split(r"[;；]", line):
            s = piece.strip()
            if s.lower().startswith(_NOTE_PATTERN_PREFIX):
                value = s[len(_NOTE_PATTERN_PREFIX):].strip()
                if value and value not in out:
                    out.append(value)
    return out


def parse_note_pattern(note: str | None) -> str | None:
    """只取 note 里的**第一条** `pattern:`（单条场景的便捷入口）。"""
    found = parse_note_patterns(note)
    return found[0] if found else None


def _parse_stamp(stamp: str) -> datetime | None:
    """解析 'YYYY-MM-DD' 或 'YYYY-MM-DD HH:MM:SS'；解析不了返回 None。

    库里的 `adopted_at` 可能被手工改坏，所以这里**不抛**：解析不了就当
    "没有分界日期"如实列出（写路径的严格校验在 suggestmeta.validate_stamp）。
    """
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(stamp, fmt)
        except (ValueError, TypeError):
            continue
    return None


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _shift(stamp: str, days: int) -> str | None:
    """分界时刻前后移 days 天（分量形式，便于字典序比较）。解析不了 → None。"""
    dt = _parse_stamp(stamp)
    return None if dt is None else _fmt(dt + timedelta(days=days))


def resolve_boundary(adopted_at: str | None,
                     since: str | None) -> tuple[str | None, str | None]:
    """定分界时刻 T：优先 `adopted_at`，回退 `since`，都没有 → (None, None)。

    返回 (T, 来源)，来源 ∈ {"adopted_at", "since", None}——报告里要写清
    这条结论是按哪个时刻切的，否则读者无法复算。
    """
    for stamp, src in ((adopted_at, "adopted_at"), (since, "since")):
        if stamp and _parse_stamp(stamp) is not None:
            return _fmt(_parse_stamp(stamp)), src
    return None, None


def matches_key(key: str, pattern: str) -> bool:
    """key 是否命中该错误 pattern（同用 `normalize_error` 归一后比较）。

    两条判据：① 归一后相等；② 长串包含短串（>= `_MIN_FUZZY` 字符）——
    归一模式常是更长真实错误的前缀（如 `old_string was not found in <q>`
    与带尾巴的变体），只判相等会漏。短 pattern 不做反向包含，防误命中。
    """
    nk = normalize_error(key)
    if not nk or not pattern:
        return False
    if nk == pattern:
        return True
    short, long_ = sorted((nk, pattern), key=len)
    return len(short) >= _MIN_FUZZY and short in long_


def hit_errors(errors: list[dict], key: str | list[str]) -> list[dict]:
    """全时段命中该 key（或 key 列表，**取并集、不重复计**）的错误（窗口切分前）。

    一条规则覆盖多个 pattern 时，同一批错误只应算一次——所以这里是"逐条错误
    看它是否命中任一 pattern"，而不是把各 pattern 的命中列表拼起来。
    """
    keys = [key] if isinstance(key, str) else list(key)
    return [e for e in errors
            if any(matches_key(k, e.get("pattern") or "") for k in keys)]


def _in_window(ts: str | None, lo: str, hi: str) -> bool:
    """ts ∈ [lo, hi)（字符串字典序；ts 缺失 → 不算）。"""
    if not ts:
        return False
    return (lo is None or ts >= lo) and (hi is None or ts < hi)


def window_stats(hits: list[dict], lo: str, hi: str) -> dict:
    """窗口 [lo, hi) 内的样本量与两个比率（纯函数，便于单独钉口径）。

    返回 {"errors", "sessions", "unresolved", "unresolved_rate",
    "recurrence_rate"}；窗口内没有错误时两个比率都是 None（**不是 0**：
    "没数据"与"没有未解决/没有复发"是两回事）。
    """
    win = [e for e in hits if _in_window(e.get("ts"), lo, hi)]
    by_sid = Counter(e["sid"] for e in win)
    recurred = sum(1 for n in by_sid.values() if n >= 2)
    unresolved = sum(1 for e in win if not e.get("resolved", True))
    return {
        "errors": len(win),
        "sessions": len(by_sid),
        "unresolved": unresolved,
        "unresolved_rate": (unresolved / len(win)) if win else None,
        "recurrence_rate": (recurred / len(by_sid)) if by_sid else None,
    }


def relative_change(before: float | None, after: float | None) -> float | None:
    """相对变化 (后-前)/前；前为 0 时记 0（仍为 0）或 +1（从无到有）。

    约定写进文档：0 → 非 0 视为 +100%（新出现的失败比"比例上升"更严重），
    免得除零后又悄悄退回"无变化"。
    """
    if before is None or after is None:
        return None
    if before == 0:
        return 0.0 if after <= 0 else 1.0
    return (after - before) / before


def judge(before: dict, after: dict, threshold: float = DEFAULT_THRESHOLD,
          min_per_window: int = DEFAULT_MIN_PER_WINDOW,
          min_total: int = DEFAULT_MIN_TOTAL) -> tuple[str, str]:
    """三值判定 +（不下结论时）原因短语。

    **样本门槛先于幅度判定**：这是本模块最重要的一条——不放门槛，任何
    一两条的噪声都能刷出"改善 300%"。
    """
    nb, na = before["errors"], after["errors"]
    if nb < min_per_window or na < min_per_window or nb + na < min_total:
        return VERDICT_INCONCLUSIVE, (
            f"{_REASON_FEW_SAMPLES}（前 {nb} / 后 {na} 条，合计 {nb + na}；"
            f"门槛：各 >= {min_per_window} 且合计 >= {min_total}）")
    d = relative_change(before["unresolved_rate"], after["unresolved_rate"])
    text = (f"未解决率 {_pct(before['unresolved_rate'])} → "
            f"{_pct(after['unresolved_rate'])}（{d:+.0%}）")
    # 阈值**含等号**（下降 >= 10% 即改善）。比率是浮点除法的结果，恰好 ±10%
    # 时可能算出 -0.09999999999999998（实测：100 条里 50→45 未解决），直接比较
    # 会把"恰好 10%"误判成"无变化"——故向阈值内侧放宽 1e-9（两侧都用 `- eps`，
    # 让浮点误差朝哪边都算命中）。
    eps = 1e-9
    if d <= -(threshold - eps):
        return VERDICT_IMPROVED, text
    if d >= threshold - eps:
        return VERDICT_WORSENED, text
    return VERDICT_UNCHANGED, f"{text}，在 ±{threshold:.0%} 内"


def _pct(rate: float | None) -> str:
    return "—" if rate is None else f"{rate * 100:.1f}%"


def evaluate_entry(key: str, errors: list[dict], adopted_at: str | None = None,
                   since: str | None = None, note: str | None = None,
                   window_days: int = DEFAULT_WINDOW_DAYS,
                   min_per_window: int = DEFAULT_MIN_PER_WINDOW,
                   min_total: int = DEFAULT_MIN_TOTAL,
                   threshold: float = DEFAULT_THRESHOLD) -> dict:
    """对一条 adopted 建议做 T 前后 A/B（纯逻辑，不碰 I/O）。

    返回一行结论：key / 分界(及来源) / 匹配目标(及来源) / 前后窗口统计 /
    判定与理由 / 处置建议。

    匹配目标：`note` 里解析出 `pattern:` 就用它（`match_source="note.pattern"`，
    这是建议句原文 key 与错误 pattern 之间的桥；**可写多条，取并集**），否则用
    key 本身（`match_source="key"`，旧行为，不许回归）。
    """
    if window_days < 1:
        raise ValueError(f"window_days={window_days} 必须 >= 1")
    boundary, source = resolve_boundary(adopted_at, since)
    note_patterns = parse_note_patterns(note)
    targets = note_patterns or [key]
    hits = hit_errors(errors, targets)
    # note 写了 pattern 却一条都没命中：结论照旧，但理由里要说清（否则读者
    # 会以为"库里就是没这类错误"，而实际是标记写错了）
    miss = bool(note_patterns) and not hits
    suffix = f"；{_NOTE_MISS}" if miss else ""
    row = {"key": key, "adopted_at": adopted_at, "boundary": boundary,
           "boundary_source": source, "targets": targets,
           "target": " ｜ ".join(targets),
           "match_source": "note.pattern" if note_patterns else "key",
           "note_patterns": note_patterns, "pattern_miss": miss,
           "pattern_hits": len(hits),
           "before": None, "after": None, "delta": None,
           "verdict": VERDICT_INCONCLUSIVE, "reason_kind": None,
           "reason": "", "verdict_label": VERDICT_INCONCLUSIVE,
           "action": _ACTIONS[VERDICT_INCONCLUSIVE]}
    if boundary is None:
        row["reason_kind"] = _REASON_NO_BOUNDARY
        row["reason"] = _REASON_NO_BOUNDARY + suffix
        row["verdict_label"] = f"{VERDICT_INCONCLUSIVE}（{_REASON_NO_BOUNDARY}）"
        return row
    row["before"] = window_stats(hits, _shift(boundary, -window_days), boundary)
    row["after"] = window_stats(hits, boundary, _shift(boundary, window_days))
    row["delta"] = relative_change(row["before"]["unresolved_rate"],
                                   row["after"]["unresolved_rate"])
    verdict, reason = judge(row["before"], row["after"], threshold,
                            min_per_window, min_total)
    row["verdict"], row["reason"] = verdict, reason + suffix
    if verdict == VERDICT_INCONCLUSIVE:
        row["reason_kind"] = _REASON_FEW_SAMPLES
        row["verdict_label"] = f"{verdict}（{_REASON_FEW_SAMPLES}）"
    else:
        row["verdict_label"] = verdict
    row["action"] = _ACTIONS[verdict]
    return row


def evaluate_all(records: dict[str, dict], errors: list[dict],
                 since: str | None = None,
                 window_days: int = DEFAULT_WINDOW_DAYS,
                 min_per_window: int = DEFAULT_MIN_PER_WINDOW,
                 min_total: int = DEFAULT_MIN_TOTAL,
                 threshold: float = DEFAULT_THRESHOLD) -> dict:
    """对台账里全部 adopted 建议做评估（纯逻辑；records 来自 load_records）。

    pending / rejected **不评估**（没并入 AGENTS.md 的规则谈不上效果），
    但条目数如实报在 `skipped` 里——"没评估"与"评估了且没变化"必须能分开。

    `topic:<id>` 命名空间（P2-5 人账：主题注册表照抄进来的登记留痕）**整体跳过**
    并单列 `skipped_topic`：主题不是规则、天然没有错误 pattern，逐条跑只会产出
    一堆"不下结论（样本不足 0/0）"，把报告淹成噪音。`adopted_total` 的口径是
    "**可评估**的 adopted 条数"（不含跳过项）。
    """
    entries = []
    skipped_topic = 0
    for key in sorted(records):
        if key.startswith(TOPIC_PREFIX):        # 登记留痕，不是可评估的规则
            skipped_topic += 1
            continue
        rec = records[key]
        if rec.get("status") != "adopted":
            continue
        entries.append(evaluate_entry(
            key, errors, adopted_at=rec.get("adopted_at"), since=since,
            note=rec.get("note"), window_days=window_days,
            min_per_window=min_per_window, min_total=min_total,
            threshold=threshold))
    entries.sort(key=lambda r: (_VERDICT_ORDER.index(r["verdict"]), r["key"]))
    counts = {v: sum(1 for r in entries if r["verdict"] == v)
              for v in _VERDICT_ORDER}
    return {"window_days": window_days, "threshold": threshold,
            "min_per_window": min_per_window, "min_total": min_total,
            "since": since, "entries": entries, "counts": counts,
            "adopted_total": len(entries),
            "skipped": len(records) - len(entries) - skipped_topic,
            "skipped_topic": skipped_topic}


def load_errors(db: Path) -> tuple[list[dict], dict]:
    """只读打开 harvester.db，复用 errstats 取全部错误（含 resolved/pattern）。"""
    con = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    try:
        con.row_factory = sqlite3.Row
        return collect_errors_from_db(db, con=con)
    finally:
        con.close()


def run_eval(db: Path, meta_db: Path, since: str | None = None,
             window_days: int = DEFAULT_WINDOW_DAYS,
             min_per_window: int = DEFAULT_MIN_PER_WINDOW,
             min_total: int = DEFAULT_MIN_TOTAL,
             threshold: float = DEFAULT_THRESHOLD) -> dict:
    """I/O 入口：只读采集库 + 读建议台账 → evaluate_all 的结果结构。

    `since` 是 CLI 传进来的分界回退值——格式非法当场 ValueError
    （由 CLI 层转成退出码 2），不静默吞掉。
    """
    if since is not None:
        validate_stamp(since, "since")
    db = Path(db)
    records = load_records(meta_db)
    errors, meta = load_errors(db)
    result = evaluate_all(records, errors, since=since, window_days=window_days,
                          min_per_window=min_per_window, min_total=min_total,
                          threshold=threshold)
    result["db"] = str(db)
    result["meta_db"] = str(meta_db)
    result["errors_total"] = meta["error_count"]
    return result


def _md(text: str) -> str:
    """表格单元转义：key 里带 `|` 会把 Markdown 表格切碎（错误原文里常见）。"""
    return (text or "").replace("|", "\\|").replace("\n", " ")


def _window_cell(stat: dict | None) -> str:
    if stat is None:
        return "—"
    return (f"{stat['errors']} 条 / 未解决 {_pct(stat['unresolved_rate'])}"
            f" / 复发 {_pct(stat['recurrence_rate'])}"
            f"（{stat['sessions']} 会话）")


def render_report(result: dict) -> str:
    """人读出口：Markdown（与 evaluate_all 同一份纯数据，人读/机读不分叉）。"""
    counts = result["counts"]
    no_boundary = sum(1 for r in result["entries"]
                      if r["reason_kind"] == _REASON_NO_BOUNDARY)
    few = sum(1 for r in result["entries"]
              if r["reason_kind"] == _REASON_FEW_SAMPLES)
    lines = [
        "# 自进化评估（harvest-eval）",
        "",
        f"- 采集库：{result.get('db', '?')}（**只读**）｜台账："
        f"{result.get('meta_db', '?')}｜库内错误 {result.get('errors_total', '?')} 条",
        f"- A/B：分界时刻 T 前后各 **{result['window_days']}** 天"
        f"（前 [T-N, T)、后 [T, T+N)）；阈值 ±{result['threshold']:.0%}"
        f"（以**未解决率**为主判据，复发率只报告）",
        f"- 样本门槛：两窗口各 >= {result['min_per_window']} 条**且**合计 >= "
        f"{result['min_total']} 条，否则一律「不下结论（样本不足）」",
        f"- 分界回退 `since`：{result.get('since') or '（未给）'}",
        f"- adopted 建议 **{result['adopted_total']}** 条"
        f"（未采纳/已否决 {result['skipped']} 条不评估）："
        + " ｜ ".join(f"{v} {counts[v]}" for v in _VERDICT_ORDER),
        f"- 主题级台账 {result.get('skipped_topic', 0)} 条已跳过"
        f"（`{TOPIC_PREFIX}` 命名空间是登记留痕，不是可评估的规则）",
        f"- 不下结论细目：无分界日期 {no_boundary} 条 / 样本不足 {few} 条",
        "",
        "| key | 分界 | 前(错误数/未解决率/复发率) | 后(错误数/未解决率/复发率) "
        "| 判定 | 处置建议 |",
        "|---|---|---|---|---|---|",
    ]
    for r in result["entries"]:
        lines.append("| {} | {} | {} | {} | {} | {} |".format(
            _md(r["key"]), r["boundary"] or "—", _window_cell(r["before"]),
            _window_cell(r["after"]), _md(r["verdict_label"]),
            _md(r["action"])))
    lines += [
        "", "## 判定 → 处置建议（口径固定）", "",
        "| 判定 | 处置建议 |", "|---|---|",
    ]
    for v in _VERDICT_ORDER:
        lines.append(f"| {v} | {_ACTIONS[v]} |")
    lines += [
        "", "## 逐条理由", "",
    ]
    for r in result["entries"]:
        where = ("匹配目标 `note.pattern`" if r["match_source"] == "note.pattern"
                 else "匹配目标 key 本身")
        lines.append(f"- `{_md(r['key'])}`：{r['verdict_label']}——"
                     f"{_md(r['reason'])}；{where}：`{_md(r['target'])}`，"
                     f"全时段命中 {r['pattern_hits']} 条")
    lines += [
        "", "## 纪律", "",
        "- **本工具只评估，绝不自动改 `AGENTS.md`**：「自」= 评估自动化，"
        "不是改动自动化；退休/回滚/保持都要人工决定后手动改。",
        "- 「不下结论」**不等于**「无变化」：样本不足或没有分界日期时如实写明，"
        "宁可空着也不下判断。",
        "- 台账 key 是**建议句原文**（H31）时，靠 `note` 里的 "
        "`pattern: <错误 pattern 原文>`（**可多条，换行分隔，取并集**）接上"
        "错误数据（语法见 `harvesteval.parse_note_patterns`）；没写这一行、"
        "而 key 又与任何错误 pattern 都不吻合时，样本恒为 0。",
        "- 口径：未解决率 = 窗口内 `resolved == False` 的错误占比；"
        "复发率 = 窗口内含该 pattern 的会话里出现 >= 2 次的会话占比"
        "（resolved 口径见 `errstats` 模块 docstring）。",
    ]
    return "\n".join(lines) + "\n"
