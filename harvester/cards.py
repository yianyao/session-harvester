# -*- coding: utf-8 -*-
"""知识卡片库（cards 子命令）——G3 的确定性一半。

对应《轨迹分析工具设计方案》§8：卡片 = 带 YAML frontmatter 的 Markdown，
frontmatter 规范冻结字段 id/title/type/tags/anchors/evidence/confidence/
created。设计把 cards extract（调 LLM 生成卡片）划入可抛弃的提炼层——
本模块只实装确定性部分 **validate**：锚点有效性 + 证据/字段完整性校验，
让冻结的规范至少有一个机器消费者。

与 kb/playbook 的归一决策（2026-10-06）：
- cards 目录 = 机器可校验的**候选池**（suggest-agents / 人工摘录产出）；
- ~/.workbuddy/knowledge（kb-init/playbook）= 人工维护的**主库**；
- 卡片通过 validate 后由人工并入主库——两套形态一池一库，不再双轨。
"""

from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

try:  # PyYAML 为可选依赖：有则完整解析，无则降级逐行提取标量
    import yaml  # type: ignore
    _HAS_YAML = True
except ImportError:  # pragma: no cover
    _HAS_YAML = False

REQUIRED_TYPES = {"insight", "pitfall", "workflow"}
_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _split_top(s: str, sep: str = ",") -> list[str]:
    """按顶层分隔符切分（忽略引号内与 {}/[] 嵌套内的 sep）。"""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    quote: str | None = None
    for ch in s:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch in "{[":
            depth += 1
            buf.append(ch)
        elif ch in "}]":
            depth -= 1
            buf.append(ch)
        elif ch == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf)
    if tail.strip():
        parts.append(tail)
    return parts


def _coerce_scalar(v: str):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return v


def _parse_flow_seq(s: str) -> list | None:
    """解析 YAML 行内流式序列 [{k: v, ...}, ...] / [a, b]。

    只覆盖卡片 frontmatter 实际出现的形态（session_id 值内含冒号，
    取第一个冒号为键分隔）；解析失败返回 None，调用方保留原字符串。
    存在意义：PyYAML 缺席时锚点校验仍必须可执行——降级不等于放弃校验。
    """
    inner = s.strip()[1:-1].strip()
    if not inner:
        return []
    items: list = []
    for part in _split_top(inner):
        part = part.strip()
        if part.startswith("{") and part.endswith("}"):
            d: dict = {}
            for kv in _split_top(part[1:-1]):
                if ":" not in kv:
                    return None
                k, _, v = kv.partition(":")
                d[k.strip()] = _coerce_scalar(v)
            items.append(d)
        else:
            items.append(_coerce_scalar(part))
    return items


def _parse_frontmatter(text: str) -> tuple[dict | None, str]:
    """返回 (frontmatter dict 或 None, 正文)。无 frontmatter 返回 (None, 全文)。"""
    m = _FM_RE.match(text)
    if not m:
        return None, text
    raw = m.group(1)
    if _HAS_YAML:
        try:
            data = yaml.safe_load(raw)
            return (data if isinstance(data, dict) else None), text[m.end():]
        except yaml.YAMLError:
            return None, text[m.end():]
    # 降级：顶层 "key: value" 标量；行内 [..] 流式序列做最小解析
    # （anchors: [{session_id: "...", turn: N}, ...]），保证无 PyYAML
    # 时锚点校验照常执行。
    data: dict = {}
    for line in raw.splitlines():
        km = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if km and not line.startswith((" ", "-", "\t")):
            value = km.group(2).strip()
            if value.startswith("[") and value.endswith("]"):
                parsed = _parse_flow_seq(value)
                data[km.group(1)] = parsed if parsed is not None else value
            else:
                data[km.group(1)] = value
    return data, text[m.end():]


def validate_card(path: Path) -> tuple[list[str], list[str], dict | None]:
    """校验单卡。返回 (errors, warnings, fm)。"""
    errors: list[str] = []
    warns: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ["非 UTF-8 编码"], [], None
    fm, body = _parse_frontmatter(text)
    if fm is None:
        return ["frontmatter 缺失或不可解析（§8 规范要求 YAML 头）"], [], None
    for field in ("id", "title", "type", "anchors", "evidence",
                  "confidence", "created"):
        if field not in fm or fm[field] in ("", None, "[]", "{}"):
            errors.append(f"缺少必填字段 {field}")
    ctype = str(fm.get("type", ""))
    if ctype and ctype not in REQUIRED_TYPES:
        errors.append(f"type={ctype!r} 不在 {sorted(REQUIRED_TYPES)}")
    anchors = fm.get("anchors")
    if anchors is not None and not isinstance(anchors, list):
        warns.append("anchors 非列表形态（§8 要求 [{session_id, ...}] 列表）")
    if not str(fm.get("evidence", "")).strip() and "evidence" in fm:
        errors.append("evidence 为空（规范：必须带证据原文）")
    if not body.strip():
        warns.append("正文为空")
    try:
        conf = float(fm.get("confidence", -1))
        if not 0.0 <= conf <= 1.0:
            errors.append(f"confidence={conf} 超出 0.0-1.0")
    except (TypeError, ValueError):
        if fm.get("confidence") is not None:
            errors.append(f"confidence={fm.get('confidence')!r} 不是数值")
    return errors, warns, fm


def _anchor_known(sid: str, db: Path | None) -> bool | None:
    """锚点 session_id 是否在索引库中。无库时返回 None（跳过校验）。"""
    if db is None or not db.is_file():
        return None
    con = sqlite3.connect(str(db))
    try:
        row = con.execute("SELECT 1 FROM sessions WHERE session_id=? OR sid=?",
                          (sid, sid)).fetchone()
        return row is not None
    except sqlite3.OperationalError:
        return None
    finally:
        con.close()


def validate_cards(root: Path, db: Path | None = None
                   ) -> tuple[list[dict], dict]:
    """校验目录下全部卡片。返回 (results, summary)。

    results 每项 {path, errors, warnings}；summary 含 ok/warn/error 计数与
    anchor_misses（锚点不在索引库的卡片数）。
    """
    results: list[dict] = []
    n_ok = n_warn = n_err = 0
    anchor_miss = 0
    anchor_checked = 0
    for p in sorted(root.rglob("*.md")):
        if p.name.upper() in ("INDEX.md", "README.md"):
            continue
        errors, warns, fm = validate_card(p)
        # 锚点校验：fm 里有 anchors 且形态可读时逐个查库
        if fm and db is not None:
            anchors = fm.get("anchors")
            if isinstance(anchors, list):
                for a in anchors:
                    sid = a.get("session_id") if isinstance(a, dict) else None
                    if sid:
                        anchor_checked += 1
                        known = _anchor_known(str(sid), db)
                        if known is False:
                            anchor_miss += 1
                            errors.append(f"锚点 session_id={sid} 不在索引库")
                        elif known is None:
                            warns.append(f"锚点 {sid} 未能校验（无索引库）")
        rel = str(p.relative_to(root))
        results.append({"path": rel, "errors": errors, "warnings": warns})
        if errors:
            n_err += 1
        elif warns:
            n_warn += 1
        else:
            n_ok += 1
    summary = {"cards": len(results), "ok": n_ok, "warn": n_warn,
               "error": n_err, "anchor_checked": anchor_checked,
               "anchor_misses": anchor_miss}
    return results, summary


def render_report(results: list[dict], summary: dict, root: Path) -> str:
    lines = [f"# 知识卡片校验报告（{root}）", ""]
    if not results:
        lines += ["目录下没有卡片。产出方式：", "",
                  "- suggest-agents 的建议池条目人工扩写后落卡；",
                  "- 或按 §8 规范手写（frontmatter: id/title/type/tags/"
                  "anchors/evidence/confidence/created）。", ""]
        return "\n".join(lines)
    lines += [
        f"- 卡片 {summary['cards']}：通过 {summary['ok']}｜警告 "
        f"{summary['warn']}｜错误 {summary['error']}",
        f"- 锚点校验 {summary['anchor_checked']} 个，"
        f"未命中索引库 {summary['anchor_misses']} 个", "",
    ]
    for r in results:
        if r["errors"] or r["warnings"]:
            lines.append(f"## {r['path']}")
            for e in r["errors"]:
                lines.append(f"- [错误] {e}")
            for w in r["warnings"]:
                lines.append(f"- [警告] {w}")
            lines.append("")
    if summary["error"]:
        lines.append("结论：存在错误卡片（不满足 §8 冻结规范），"
                     "修复前不得并入主库。")
    elif summary["warn"]:
        lines.append(f"结论：无错误，但 {summary['warn']} 张卡片带警告"
                     "——复核警告项后再并入主库。")
    else:
        lines.append("结论：全部卡片满足 §8 规范，可并入主库。")
    return "\n".join(lines).rstrip() + "\n"


# ---- 卡片脚手架（cards new 子命令） ----

_TYPE_HEADINGS = {
    "pitfall": ("## 现象", "## 做法"),
    "workflow": ("## 适用场景", "## 步骤"),
    "insight": ("## 结论", "## 依据"),
}
_SLUG_RE = re.compile(r"[^A-Za-z0-9]+")


def scaffold_card(db: Path, sid_query: str, out_dir: Path,
                  ctype: str = "pitfall", title: str | None = None,
                  turn: int | None = None) -> Path:
    """从索引库会话生成卡片脚手架（frontmatter 锚点真实、evidence 留白）。

    sid_query 匹配 sessions.sid 或 session_id。id 自动编号
    kc-YYYYMMDD-NNNN（扫描 out_dir 既有卡片取号）。 ctype 必须在
    REQUIRED_TYPES 内。
    """
    if ctype not in REQUIRED_TYPES:
        raise ValueError(f"type={ctype!r} 不在 {sorted(REQUIRED_TYPES)}")
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT sid, session_id, title, created_at, source FROM sessions "
            "WHERE sid=? OR session_id=? ORDER BY CASE WHEN sid=? "
            "THEN 0 ELSE 1 END LIMIT 1", (sid_query, sid_query, sid_query)
        ).fetchone()
    finally:
        con.close()
    if row is None:
        raise KeyError(f"索引库中找不到会话: {sid_query}")
    session_id = row["session_id"] or row["sid"]
    title = title or row["title"] or "（补标题）"
    today = time.strftime("%Y%m%d")
    created = time.strftime("%Y-%m-%d")
    # id 自动编号：扫描既有卡片
    out_dir.mkdir(parents=True, exist_ok=True)
    max_n = 0
    for p in out_dir.rglob(f"kc-{today}-*.md"):
        m = re.search(rf"^kc-{today}-(\d+)", p.name)
        if m:
            max_n = max(max_n, int(m.group(1)))
    slug = _SLUG_RE.sub("-", title).strip("-").lower()[:28].strip("-") or "card"
    card_id = f"kc-{today}-{max_n + 1:04d}-{slug}"
    turn_part = str(turn) if turn is not None else ""
    anchors = (f"[{{session_id: \"{session_id}\","
               f" turn: {turn_part or 'null'}}}]")
    h1, h2 = _TYPE_HEADINGS[ctype]
    text = (
        "---\n"
        f"id: {card_id}\n"
        f"title: {title}\n"
        f"type: {ctype}\n"
        "tags: []\n"
        f"anchors: {anchors}\n"
        "evidence: |\n"
        "  <粘贴原会话证据原文，一行起，缩进两格>\n"
        "confidence: 0.5\n"
        f"created: {created}\n"
        "---\n"
        f"{h1}\n\n<待补：这条记录说明了什么问题/结论>\n\n"
        f"{h2}\n\n<待补：怎么做/依据是什么>\n\n"
        f"来源：`{row['sid']}#{turn or ''}`（{row['source']}），"
        f"由 `cards new` 脚手架生成，evidence 与正文人工补全后跑 "
        f"`cards validate`。\n"
    )
    path = out_dir / f"{card_id}.md"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path
