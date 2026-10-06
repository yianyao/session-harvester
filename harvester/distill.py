# -*- coding: utf-8 -*-
"""蒸馏套件：聚合语料 + 知识库脚手架（原 session-knowledge-distill skill 的机械部分）。

职责边界（与 skill 的分工）：
- 本模块（Python）：聚合语料、建知识库骨架、盘点统计——一切可确定性执行的步骤；
- skill（agent）：通读语料后的归类、话题增补、反馈台账整理——语义判断步骤。

语料锚点格式（供溯源，skill 纪律要求）：
  <!-- SRC: <adapter> | <session_id> | <title> | <date> -->
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .adapters import load_sources
from .models import normalize_text
from .outline import scan_all

KB_DEFAULT_ROOT = Path.home() / ".workbuddy" / "knowledge"

INDEX_TEMPLATE = """---
title: 个人会话知识库总索引
updated: {today}
---

# 个人会话知识库总索引

## 话题表

| 文件 | 话题 | period | updated |
|---|---|---|---|

## 维护方式

- 增量更新流程见 skill `session-knowledge-distill`（聚合用 `python -m harvester aggregate`）
- 话题文件三节结构：理解轨迹 / 当前完整思路 / 未决与线索
"""

FEEDBACK_TEMPLATE = """---
title: 工具与 Skill 反馈台账
updated: {today}
---

# 工具与 Skill 反馈台账

每条含：来源会话、内容、状态（已回灌 / 待回灌 / 观察）。

## 宿主工具

## 外部工具

## 自建 skill
"""


# ---------- 聚合 ----------

def corpus_from_sources(sources: dict | None, out_path: Path,
                        include_notes: bool = False) -> dict:
    """从全部可用数据源实时聚合所有会话为单一语料文件。"""
    adapters_list, items, _reports = scan_all(sources=sources)
    adapters = {ad.id: ad for ad in adapters_list}  # 复用 scan_all 的实例
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n_files = 0
    total_chars = 0
    sections: list[str] = []
    for it in items:
        ad = adapters.get(it["adapter"])
        if ad is None:
            continue
        try:
            rec = ad.load_session(it["session_id"])
        except NotImplementedError:
            continue
        except Exception as e:  # noqa: BLE001 - 单条失败不拖垮聚合
            sections.append(f"<!-- WARN: {it['session_id']} 加载失败: {e} -->\n")
            continue
        date = (rec.created_at or rec.updated_at or "")[:10]
        head = (f"<!-- SRC: {rec.source} | {rec.session_id} | {rec.title} | {date} -->\n\n"
                f"# {rec.title}\n\n")
        body_parts = [head]
        for m in rec.messages:
            if not include_notes and m.role not in ("user", "assistant"):
                continue
            ts = f" · {m.timestamp}" if m.timestamp else ""
            body_parts.append(f"## [{m.role}]{ts}\n\n{normalize_text(m.text).rstrip()}\n\n")
        sec = "\n".join(body_parts)
        sections.append(sec)
        n_files += 1
        total_chars += len(sec)
    header = (f"# 会话语料（{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}）\n\n"
              f"- 来源文件数：{n_files}\n- 总字符数：{total_chars}\n\n---\n\n")
    out_path.write_text(header + "\n".join(sections),
                        encoding="utf-8", newline="\n")
    return {"files": n_files, "chars": total_chars, "out": str(out_path)}


def corpus_from_exports(exports_root: Path, out_path: Path) -> dict:
    """从 export 产物目录聚合（读各会话的 .json 结构化文件）。"""
    jsons = sorted(Path(exports_root).rglob("*.json"))
    jsons = [p for p in jsons if p.name != "export_manifest.json"]
    sections: list[str] = []
    n_files = 0
    total_chars = 0
    for p in jsons:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            sections.append(f"<!-- WARN: {p} 解析失败: {e} -->\n")
            continue
        date = (data.get("created_at") or "")[:10]
        head = (f"<!-- SRC: {data.get('source')} | {data.get('session_id')} | "
                f"{data.get('title')} | {date} | export:{p.name} -->\n\n"
                f"# {data.get('title')}\n\n")
        parts = [head]
        for m in data.get("messages", []):
            if m.get("role") not in ("user", "assistant"):
                continue
            ts = f" · {m.get('timestamp')}" if m.get("timestamp") else ""
            parts.append(f"## [{m['role']}]{ts}\n\n{normalize_text(m.get('text', '')).rstrip()}\n\n")
        sec = "\n".join(parts)
        sections.append(sec)
        n_files += 1
        total_chars += len(sec)
    header = (f"# 会话语料（export 聚合，{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}）\n\n"
              f"- 来源文件数：{n_files}\n- 总字符数：{total_chars}\n\n---\n\n")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(header + "\n".join(sections),
                        encoding="utf-8", newline="\n")
    return {"files": n_files, "chars": total_chars, "out": str(out_path)}


# ---------- 知识库 ----------

def kb_init(root: Path) -> dict:
    """建立知识库骨架。绝不覆盖已存在的文件（幂等，可重复执行）。"""
    root = Path(root)
    created, skipped = [], []
    (root / "topics").mkdir(parents=True, exist_ok=True)
    (root / "feedback").mkdir(parents=True, exist_ok=True)
    targets = {
        root / "INDEX.md": INDEX_TEMPLATE.format(
            today=datetime.now().strftime("%Y-%m-%d")),
        root / "feedback" / "tools-and-skills-feedback.md": FEEDBACK_TEMPLATE.format(
            today=datetime.now().strftime("%Y-%m-%d")),
    }
    for path, content in targets.items():
        if path.exists():
            skipped.append(str(path))
        else:
            path.write_text(content, encoding="utf-8", newline="\n")
            created.append(str(path))
    return {"created": created, "skipped": skipped, "root": str(root)}


def kb_stats(root: Path) -> dict:
    """知识库盘点：话题文件、INDEX 一致性、反馈台账条数。

    INDEX 解析只认「表格行中的干净文件名 token」（正则 fullmatch），
    避免把正文里的引用误判为缺失文件。
    """
    root = Path(root)
    topics_dir = root / "topics"
    index = root / "INDEX.md"
    md_name = re.compile(r"[\w\-/]+\.md")
    topic_files = sorted(p.name for p in topics_dir.glob("*.md")) if topics_dir.is_dir() else []
    referenced: list[str] = []
    if index.is_file():
        for line in index.read_text(encoding="utf-8").splitlines():
            if not line.startswith("|"):
                continue
            for token in line.split("|"):
                token = token.strip().strip("`").strip()
                if md_name.fullmatch(token) and token != "INDEX.md":
                    referenced.append(token)
    index_rows = len(referenced)
    missing = [t for t in referenced
               if not (root / t).is_file() and not (topics_dir / t).is_file()]
    fb = root / "feedback" / "tools-and-skills-feedback.md"
    fb_items = 0
    if fb.is_file():
        fb_items = sum(1 for ln in fb.read_text(encoding="utf-8").splitlines()
                       if ln.startswith("- ") or ln.startswith("### "))
    return {"topic_files": len(topic_files), "index_rows": index_rows,
            "missing_files": missing, "feedback_items": fb_items}
