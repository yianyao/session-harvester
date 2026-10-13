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

#: 导出清单文件名。`indexing.index_exports` 按这个名字排除清单本身——
#: 两处口径必须一致（那边是既有字面量，本常量供 export/prune 使用）。
MANIFEST_NAME = "export_manifest.json"

#: 从 export json 头部嗅 sid 时读取的字节数。sid 字段在本工具自己写出的
#: json 里位于最前几行（source / session_id / title ...），8 KB 足够；
#: 嗅不到时**回退整文件解析**（绝不当成「这个文件没有 sid」）。
_SID_SNIFF_BYTES = 8192


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
           select_sids: set[str] | None = None,
           scanned: tuple | None = None,
           skip_sids: set[str] | None = None) -> dict:
    """scanned（v0.46，additive 可选）：调用方**已经扫过**的
    `(adapters_list, items, reports)` 三元组，给 `run_update` 这类"先自己扫一遍
    挑 sid、再进来导出"的增量入口复用——否则同一轮会把每个源扫两次
    （真机实测第二遍 ~10 s，占 update 剩余耗时的大头）。None = 自己扫（既有行为）。

    skip_sids（v0.49，additive 可选）：**抑制中的 sid**（`{source}:{session_id}`
    库口径），命中的会话**不导出**，并在返回值里以 `skipped_suppressed` 报告
    （提案 §2.2 挂点 A：从源头不产生文件）。None / 空集 = 不过滤（既有行为）。
    """
    if scanned is None:
        adapters_list, items, reports = scan_all(verbose=verbose,
                                                 sources=sources, only=only)
    else:
        adapters_list, items, reports = scanned
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
    skipped_suppressed: list[str] = []
    if skip_sids:
        # 抑制拦截（v0.49 挂点 A）：从源头就不产生文件——光删库/删旧文件
        # 挡不住下次 sync，因为库内容完全由 exports 目录决定。
        kept: list[dict] = []
        for it in chosen:
            sid = f"{it['adapter']}:{it['session_id']}"
            (skipped_suppressed.append(sid) if sid in skip_sids
             else kept.append(it))
        chosen = kept

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
        # v0.49 additive：被抑制拦下的 sid（正常导出时为空列表）
        "skipped_suppressed": sorted(skipped_suppressed),
    }
    Path(outdir).mkdir(parents=True, exist_ok=True)
    (Path(outdir) / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8", newline="\n")
    return manifest


# ---- 抑制会话的旧副本清理（v0.49，提案 §2.2 挂点 B） ----

def file_sid(p: Path) -> str | None:
    """从单个 export json 读出库口径 sid（`{source}:{session_id}`）。

    **为什么不能靠文件名**（提案 §1 已查证）：文件名只有 6 位短哈希
    `{stamp}__{adapter}__{slug}__{sid6}`，不含完整 sid，也不含 source——
    清某个 sid 的副本只能读内容（或查 manifest，而 manifest 条目里同样
    没有 sid 字段）。

    口径必须与 `indexing._index_json_file` **逐字一致**（否则会出现
    「索引认为属于 A、清理认为属于 B」的静默错配）：先读头部 8 KB 嗅
    `source` / `session_id` 两个字段（本工具写出的 json 把它们放在最前），
    嗅不到就**整文件解析兜底**；两者都拿不到 → None（= 不是会话文件/损坏）。
    """
    try:
        with p.open("r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(_SID_SNIFF_BYTES)
    except OSError:
        return None
    src = re.search(r'"source"\s*:\s*"((?:[^"\\]|\\.)*)"', head)
    sid = re.search(r'"session_id"\s*:\s*"((?:[^"\\]|\\.)*)"', head)
    if src and sid:
        try:
            return (f"{json.loads('"' + src.group(1) + '"')}:"
                    f"{json.loads('"' + sid.group(1) + '"')}")
        except json.JSONDecodeError:
            pass  # 头部嗅探失败 → 走整文件兜底，绝不当成「没有 sid」
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or "messages" not in data:
        return None
    return f"{data.get('source', '?')}:{data.get('session_id', p.stem)}"


def _prune_manifest(outdir: Path, removed: set[str]) -> tuple[int, str | None]:
    """从 `export_manifest.json` 里摘掉已删文件对应的条目。

    条目里没有 sid，但有 `file`（相对 `.md` 路径）——按同 stem 的 `.json`
    与 `removed`（相对 posix 路径集合）比对。返回 (摘掉条数, 错误说明)；
    清单不存在 = 没东西可摘（0, None）；清单损坏 → **如实回报**而不是静默当作已清。
    """
    mp = Path(outdir) / MANIFEST_NAME
    if not mp.is_file():
        return 0, None
    try:
        man = json.loads(mp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return 0, f"{type(e).__name__}: {e}"
    entries = man.get("exported") if isinstance(man, dict) else None
    if not isinstance(entries, list):
        return 0, "清单结构不是 {exported: [...]}"
    kept = [e for e in entries
            if Path(str(e.get("file", ""))).with_suffix(".json").as_posix()
            not in removed]
    n = len(entries) - len(kept)
    if n:
        man["exported"] = kept
        man["count"] = len(kept)
        mp.write_text(json.dumps(man, ensure_ascii=False, indent=2),
                      encoding="utf-8", newline="\n")
    return n, None


def prune_suppressed(outdir: Path, suppressed, dry_run: bool = False) -> dict:
    """清掉抑制会话在 exports 目录里的**旧副本**（`.json` + 同名 `.md`）。

    **为什么必须有这一步**（提案 §1 已查证）：`sync` 每次整库重建，索引来源
    是 exports 目录里的 `*.json`——光在导出步拦住新文件是不够的，**上一轮
    留下的旧文件还在那里**，下次索引照样把它建回库里。这就是「抑制了却
    又回来」的真正成因。

    `dry_run=True` 只报告将删什么（**一字节不写**），CLI 的默认预演走这条。
    返回：
    - `scanned`   扫到的 json 数（不含清单本身）；
    - `deleted`   命中并（将要/已经）删掉的 [{sid, json, md}]（相对路径）；
    - `unreadable` 读不出 sid 的文件（**列出来，不假装扫过**）；
    - `manifest_removed` 从清单里摘掉的条目数；`manifest_error` 摘不动的原因。
    """
    outdir = Path(outdir)
    result: dict = {"scanned": 0, "deleted": [], "unreadable": [],
                    "manifest_removed": 0, "manifest_error": None,
                    "dry_run": bool(dry_run), "suppressed": len(set(suppressed))}
    targets = set(suppressed)
    if not targets or not outdir.is_dir():
        return result
    removed: set[str] = set()
    for p in sorted(outdir.rglob("*.json")):
        if p.name == MANIFEST_NAME:
            continue
        result["scanned"] += 1
        sid = file_sid(p)
        rel_json = p.relative_to(outdir).as_posix()
        if sid is None:
            result["unreadable"].append(rel_json)
            continue
        if sid not in targets:
            continue
        md = p.with_suffix(".md")
        rel_md = (md.relative_to(outdir).as_posix() if md.is_file() else None)
        if not dry_run:
            p.unlink()
            if md.is_file():
                md.unlink()
        removed.add(rel_json)
        result["deleted"].append({"sid": sid, "json": rel_json, "md": rel_md})
    if removed and not dry_run:
        n, err = _prune_manifest(outdir, removed)
        result["manifest_removed"] = n
        result["manifest_error"] = err
    return result
