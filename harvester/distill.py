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
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .models import normalize_text
from .outline import scan_all

KB_DEFAULT_ROOT = Path.home() / ".workbuddy" / "knowledge"

#: 正本（master）缺省位置：**项目仓内的 knowledge/**（2026-10-14 用户裁决：
#: 项目必须不绑定宿主、可迁移——蒸馏产物是本项目产的，正本随项目走；
#: KB_DEFAULT_ROOT 从"正本"降级为"发布镜像位"，由 publish_master 单向喂）。
MASTER_DEFAULT_ROOT = Path("knowledge")

#: 发布镜像要覆盖的子目录。只这两个：cards=知识卡片、topics=主题 chain 长文。
#: feedback/handoffs 等是 WorkBuddy 主程序自己的东西，发布**永不碰**。
PUBLISH_SUBDIRS = ("cards", "topics")


def publish_master(root: Path, dest: Path) -> dict:
    """把项目正本（knowledge/）单向发布到 WorkBuddy 知识库镜像位。

    语义（安全优先）：
    - 只复制 `PUBLISH_SUBDIRS` 里列出的子目录，逐文件"缺失或正本较新才覆盖"
      （shutil.copy2 保留 mtime，靠 mtime 比较判新旧）；
    - **永不删除**镜像位里的多余文件——镜像位归 WorkBuddy 管，本命令只喂不收；
    - 幂等：正本没变时重跑 = 0 复制，输出可对账（copied/skipped 逐目录计数）。
    返回 {"cards": {"copied": n, "skipped": m}, "topics": {...},
          "missing": [没找到的子目录]}。
    """
    root, dest = Path(root), Path(dest)
    if not root.is_dir():
        raise FileNotFoundError(f"正本目录不存在: {root}")
    report: dict = {"missing": []}
    for sub in PUBLISH_SUBDIRS:
        src_dir = root / sub
        if not src_dir.is_dir():
            report["missing"].append(sub)
            continue
        dst_dir = dest / sub
        dst_dir.mkdir(parents=True, exist_ok=True)
        copied = skipped = 0
        for src in sorted(src_dir.rglob("*")):
            if not src.is_file():
                continue
            dst = dst_dir / src.relative_to(src_dir)
            if dst.is_file() and dst.stat().st_mtime >= src.stat().st_mtime:
                skipped += 1
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied += 1
        report[sub] = {"copied": copied, "skipped": skipped}
    return report

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

def _stream_out(out_path: Path, header_fn, body_iter) -> None:
    """逐会话流式落盘（v0.51 S6，审计 c2 内存峰值项）。

    body 逐段写临时文件（内存峰值=单会话，不再整体攒 sections 大列表）；
    body 流完才经 header_fn 取头部（来源文件数/总字符数在流中累计，必须
    后算）；最后 header + 流式拷贝组装正式文件，临时文件即用即删。"""
    tmp = out_path.with_name(out_path.name + ".part")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            for sec in body_iter:
                f.write(sec)
                f.write("\n")
        with open(out_path, "w", encoding="utf-8", newline="\n") as out, \
                open(tmp, "r", encoding="utf-8") as src:
            out.write(header_fn())
            shutil.copyfileobj(src, out)
    finally:
        tmp.unlink(missing_ok=True)


def corpus_from_sources(sources: dict | None, out_path: Path,
                        include_notes: bool = False) -> dict:
    """从全部可用数据源实时聚合所有会话为单一语料文件。"""
    adapters_list, items, _reports = scan_all(sources=sources)
    adapters = {ad.id: ad for ad in adapters_list}  # 复用 scan_all 的实例
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n_files = 0
    total_chars = 0

    def body_iter():
        nonlocal n_files, total_chars
        for it in items:
            ad = adapters.get(it["adapter"])
            if ad is None:
                continue
            try:
                rec = ad.load_session(it["session_id"])
            except NotImplementedError:
                continue
            except Exception as e:  # noqa: BLE001 - 单条失败不拖垮聚合
                yield f"<!-- WARN: {it['session_id']} 加载失败: {e} -->\n"
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
            n_files += 1
            total_chars += len(sec)
            yield sec

    def header_fn():
        return (f"# 会话语料（{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}）\n\n"
                f"- 来源文件数：{n_files}\n- 总字符数：{total_chars}\n\n---\n\n")

    _stream_out(out_path, header_fn, body_iter())
    return {"files": n_files, "chars": total_chars, "out": str(out_path)}


def corpus_from_exports(exports_root: Path, out_path: Path) -> dict:
    """从 export 产物目录聚合（读各会话的 .json 结构化文件）。"""
    jsons = sorted(Path(exports_root).rglob("*.json"))
    jsons = [p for p in jsons if p.name != "export_manifest.json"]
    n_files = 0
    total_chars = 0

    def body_iter():
        nonlocal n_files, total_chars
        for p in jsons:
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                yield f"<!-- WARN: {p} 解析失败: {e} -->\n"
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
            n_files += 1
            total_chars += len(sec)
            yield sec

    def header_fn():
        return (f"# 会话语料（export 聚合，{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}）\n\n"
                f"- 来源文件数：{n_files}\n- 总字符数：{total_chars}\n\n---\n\n")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    _stream_out(out_path, header_fn, body_iter())
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
