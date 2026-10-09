# -*- coding: utf-8 -*-
"""按用户选择导出会话：大类=数据来源分类，小类=日期（YYYY-MM）。

目录布局：
<outdir>/<category>/<YYYY-MM>/<YYYYMMDD-HHMM>__<adapter>__<slug>__<sid6>.md
  sid6 = session_id 的 6 位短哈希，防同分钟+同标题覆盖；同名 .json（结构化原文）

并生成 <outdir>/export_manifest.json 记录本次导出清单。
"""

from __future__ import annotations

import json
import hashlib
import re
from datetime import datetime
from pathlib import Path

from .models import SessionRecord, normalize_text
from .outline import scan_all


def parse_selection(spec: str, total: int) -> tuple[list[int], list[int]]:
    """解析选择表达式："3,5-9,12"。

    返回二元组 (picked, dropped)：picked 为排序后的有效序号（1-based，去重），
    dropped 为越界被忽略的序号。
    """
    picked: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d+)-(\d+)", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a > b:  # 反向区间按正序处理（7-5 等价 5-7），而非静默返回空
                a, b = b, a
            picked.update(range(a, b + 1))
        elif part.isdigit():
            picked.add(int(part))
        else:
            raise ValueError(f"无法解析选择项: {part!r}")
    out = sorted(n for n in picked if 1 <= n <= total)
    dropped = sorted(picked - set(out))
    return out, dropped


def _date_dir(rec: SessionRecord) -> str:
    ts = rec.created_at or rec.updated_at or ""
    m = re.match(r"(\d{4}-\d{2})", ts)
    if m:
        return m.group(1)
    # 非标准时间戳：交由各 Adapter 归一化，此处兜底 unknown
    return "unknown-date"


def _stamp(rec: SessionRecord) -> str:
    ts = (rec.created_at or rec.updated_at or "").replace("T", " ")
    dt = ts[:16].replace(":", "").replace("-", "").replace(" ", "")
    # 兜底用本地时间（与纲要日期口径一致，避免混排差一天）
    return dt if dt else datetime.now().strftime("%Y%m%d%H%M")


def _sid_suffix(session_id: str, width: int = 6) -> str:
    """session_id 的短哈希后缀：防同分钟+同标题的文件名互相覆盖。"""
    return hashlib.sha1(session_id.encode("utf-8")).hexdigest()[:width]


def render_session_md(rec: SessionRecord) -> str:
    lines = [
        "---",
        f'source: {rec.source}',
        f'session_id: "{rec.session_id}"',
        f'title: {json.dumps(rec.title, ensure_ascii=False)}',
        f'created_at: {rec.created_at or "null"}',
        f'updated_at: {rec.updated_at or "null"}',
        f'messages: {rec.message_count}',
    ]
    for k, v in rec.extra.items():
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
    lines += ["---", "", normalize_text(f"# {rec.title}"), ""]
    for m in rec.messages:
        ts = f" · {m.timestamp}" if m.timestamp else ""
        lines.append(f"## [{m.role}]{ts}")
        lines.append("")
        lines.append(normalize_text(m.text).rstrip())
        lines.append("")
    return "\n".join(lines)


def export(outdir: Path, selection: str | None = None,
           include_notes: bool = False, verbose: bool = False,
           sources: dict | None = None,
           only: set[str] | None = None,
           select_sids: set[str] | None = None) -> dict:
    adapters_list, items, reports = scan_all(verbose=verbose, sources=sources,
                                             only=only)
    if not items:
        return {"exported": [], "message": "无可导出会话（各数据源均不可用或为空）"}

    exported: list[dict] = []
    skipped_stub = [r for r in reports if r.status == "STUB"]
    if selection:
        nos, dropped = parse_selection(selection, len(items))
        if dropped:
            print(f"[warn] 以下序号越界已忽略: {dropped}")
        chosen = [it for it in items if it["no"] in set(nos)]
    else:
        chosen = items
    if select_sids is not None:
        # 增量模式（v0.22 update）：只导出调用方挑选的 sid（None=不过滤）。
        # sid 为库口径 "{source}:{session_id}"（与 sessions 表/run_sync 报告一致）
        chosen = [it for it in chosen
                  if f"{it['adapter']}:{it['session_id']}" in select_sids]

    adapters = {ad.id: ad for ad in adapters_list}  # 复用 scan_all 已构建的实例，不重复建
    for it in chosen:
        ad = adapters[it["adapter"]]
        try:
            rec = ad.load_session(it["session_id"])
        except NotImplementedError as e:
            print(f"[skip] {it['no']} {it['adapter_name']}: {e}")
            continue
        except Exception as e:  # noqa: BLE001 - 单会话失败不拖垮整批导出
            print(f"[skip] {it['no']} {it['adapter_name']}: 加载失败: {e}")
            continue
        if not include_notes:
            rec.messages = [m for m in rec.messages if m.role in ("user", "assistant")]
        cat = ad.category
        date_dir = _date_dir(rec)
        fname = (f"{_stamp(rec)}__{ad.id}__{ad.slugify(rec.title)}"
                 f"__{_sid_suffix(rec.session_id)}")
        d = Path(outdir) / cat / date_dir
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{fname}.md").write_text(render_session_md(rec), encoding="utf-8", newline="\n")
        (d / f"{fname}.json").write_text(
            json.dumps({
                "source": rec.source, "session_id": rec.session_id,
                "title": rec.title, "created_at": rec.created_at,
                "updated_at": rec.updated_at, "extra": rec.extra,
                "messages": [{"role": m.role, "text": normalize_text(m.text),
                              "timestamp": m.timestamp,
                              **({"raw": m.raw} if isinstance(m.raw, dict) else {})}
                             for m in rec.messages],
            }, ensure_ascii=False, indent=2),
            encoding="utf-8", newline="\n")
        exported.append({"no": it["no"], "category": cat, "date_dir": date_dir,
                         "file": f"{cat}/{date_dir}/{fname}.md",
                         "title": rec.title, "message_count": rec.message_count})
        if verbose:
            print(f"[ok] {it['no']} -> {cat}/{date_dir}/{fname}.md")

    manifest = {
        "exported_at": datetime.now().astimezone().isoformat(),  # 本地时区，含偏移量
        "count": len(exported),
        "exported": exported,
        "stub_sources": [vars(r) for r in skipped_stub],  # vars 已含 hints 字段
    }
    Path(outdir).mkdir(parents=True, exist_ok=True)
    (Path(outdir) / "export_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8", newline="\n")
    return manifest
