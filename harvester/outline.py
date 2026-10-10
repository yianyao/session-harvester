# -*- coding: utf-8 -*-
"""扫描与纲要生成。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .adapters import build_adapters
from .adapters.base import BaseAdapter, DetectReport


def scan_all(verbose: bool = False,
             sources: dict | None = None,
             only: set[str] | None = None,
             ) -> tuple[list[BaseAdapter], list[dict], list[DetectReport]]:
    """扫描全部 Adapter。返回 (active_adapters, outline_items, reports)。

    sources: discovery.probe 产出的配置（可选），用于覆盖各 Adapter 默认路径。
    only: 限定启用的 adapter id 集合（None=全部，透传 build_adapters）。
    outline_items 每项含 no（纲要序号，export 时用）、adapter、session_id 等字段。
    """
    adapters = build_adapters(sources, only=only)
    items: list[dict] = []
    reports: list[DetectReport] = []
    no = 1
    for ad in adapters:
        try:
            rep = ad.detect()
        except Exception as e:  # noqa: BLE001 - 单个 Adapter 故障不拖垮整体
            rep = DetectReport(ad.id, ad.name, "MISSING", f"detect 异常: {e}")
        reports.append(rep)
        if rep.status != "OK":
            continue
        try:
            sessions = ad.list_sessions()
        except NotImplementedError:
            continue
        except Exception as e:  # noqa: BLE001
            if verbose:
                print(f"[warn] {ad.id} list_sessions 失败: {e}")
            continue
        for s in sessions:
            items.append({
                "no": no,
                "adapter": ad.id,
                "adapter_name": ad.name,
                "category": ad.category,
                "session_id": s["session_id"],
                "title": s.get("title") or "",
                "created_at": s.get("created_at"),
                "updated_at": s.get("updated_at"),
                "message_count": s.get("message_count"),
                "preview": s.get("preview") or "",
            })
            no += 1
    return adapters, items, reports


def render_outline_md(items: list[dict], reports: list[DetectReport]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# 会话纲要（{now}）",
        "",
        f"共 {len(items)} 条可选会话。导出时使用序号（no 列）选择。",
        "",
    ]
    # 按大类分组展示
    by_cat: dict[str, list[dict]] = {}
    for it in items:
        by_cat.setdefault(it["category"], []).append(it)
    for cat, rows in by_cat.items():
        lines.append(f"## {cat}")
        lines.append("")
        lines.append("| no | 来源 | 日期 | 标题 | 条数 | 概要 |")
        lines.append("|---:|---|---|---|---:|---|")
        for r in rows:
            date = (r.get("created_at") or "")[:10]
            mc = r.get("message_count")
            mc = str(mc) if mc is not None else "-"
            title = " ".join((r.get("title") or "").split()).replace("|", "\\|")[:60]
            prev = " ".join((r.get("preview") or "").split()).replace("|", "\\|")[:60]
            lines.append(f"| {r['no']} | {r['adapter_name']} | {date} | {title} | {mc} | {prev} |")
        lines.append("")
    lines.append("## 数据源状态")
    lines.append("")
    lines.append("| 来源 | 状态 | schema | 说明 |")
    lines.append("|---|---|---|---|")
    for rep in reports:
        # v0.45：做过 schema 守卫的源（目前 DSH）在这里显示版本/指纹/结论——
        # 上游改版时第一眼就能看到是"守卫没过"还是"没数据"。
        if getattr(rep, "schema_ok", None) is None:
            sch = "—"
        else:
            sch = (f"v{rep.schema_version or '?'} "
                   f"{'ok' if rep.schema_ok else '**不匹配**'}"
                   f"（{rep.schema_fingerprint or 'n/a'}）")
        lines.append(f"| {rep.name} ({rep.adapter_id}) | {rep.status} | "
                     f"{sch} | {rep.detail} |")
    lines.append("")
    return "\n".join(lines)


def write_outline(outdir: Path, items: list[dict], reports: list[DetectReport]) -> dict:
    outdir.mkdir(parents=True, exist_ok=True)
    md = outdir / "outline.md"
    js = outdir / "outline.json"
    md.write_text(render_outline_md(items, reports), encoding="utf-8", newline="\n")
    js.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": [vars(r) for r in reports],  # vars 已含 hints 字段
        "sessions": items,
    }, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    return {"outline_md": md, "outline_json": js}
