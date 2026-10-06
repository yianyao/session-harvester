# -*- coding: utf-8 -*-
"""sync 一键同步：采集层的全自动入口。

定位（v0.12，2026-10-06）：把「probe → scan → export → index」多步骤手工
流程收敛为一条幂等命令。人工动作被压缩到两类：
- 本地文件源（workbuddy-transcript / dsh / autoclaw / vscode-copilot）：
  完全零人工，sync 直接扫文件系统；
- 官方导出源（deepseek-export / chatgpt-export / claude-export 等导出型
  插件）：唯一人工动作 = 把导出包丢进 inbox/ 目录，其余自动。

收件箱协议（inbox/ 目录约定）：
- inbox/<文件>.zip|.json   待导入：sync 逐个让导出型 Adapter「竞标」认领
  （复用 detect() 契约做结构校验，不硬编码文件名，不臆测格式）；
- inbox/done/<adapter-id>/ 已导入归档：路径即源声明，每次 sync 重新扫描
  并注入 sources.plugins（内存态，不回写 sources.json）；
- 认领失败的文件留在 inbox/ 原地并在报告中列名——绝不静默丢弃，
  也不猜测归属，由人工决定（删除或补 Adapter）。

幂等性：
- export 文件名由 (时间戳, adapter id, 标题, sid 哈希) 决定，重跑覆盖同名；
- index 为整库重建（个人历史规模毫秒级，与 index 命令同一口径）；
- 新增会话判定 = 重建前后 sid 集合差，用于报告「本次新增 N 条」。
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from .adapters import PLUGIN_IDS, load_sources
from .exporter import export
from .indexing import index_exports

INBOX_DONE = "done"


# ---- 收件箱：识别与归档 ----

def _iter_inbox_files(inbox_dir: Path) -> list[Path]:
    """待导入候选：inbox 顶层的 zip/json 文件（不递归，done/ 自动排除）。"""
    if not inbox_dir.is_dir():
        return []
    return sorted(p for p in inbox_dir.iterdir()
                  if p.is_file() and p.suffix.lower() in (".zip", ".json"))


def _detect_plugin(path: Path) -> tuple[str | None, str]:
    """让导出型 Adapter 逐个竞标认领文件。

    仅单文件型源（claims_files=True，契约字段）参与竞标——目录型源
    （*_raw 的 detail_*.json 目录）的 detect() 可能经默认候选回退在
    别处返回 OK，若放行会错误认领陌生文件（v0.12 实测 yuanbao-raw
    认领任意 json，已加 claims_files=False 拦截）。
    返回 (认领的 plugin id 或 None, 说明)。以首个 detect()==OK 为准；
    结构校验失败的一律不认领——契约铁律：绝不臆测解析。
    """
    last_err = "无可认领的适配器"
    for pid, cls in PLUGIN_IDS.items():
        if not getattr(cls, "claims_files", True):
            last_err = f"{pid}: 目录型源，不参与单文件竞标"
            continue
        try:
            ad = cls(paths=[path])
            rep = ad.detect()
        except Exception as e:  # noqa: BLE001 - 单适配器异常=不认领
            last_err = f"{pid}: detect 异常: {e}"
            continue
        if rep.status == "OK":
            return pid, rep.detail
        last_err = f"{pid}: {rep.status} {rep.detail}"
    return None, last_err


def harvest_inbox(inbox_dir: Path, verbose: bool = False) -> dict:
    """收件箱收割：识别 → 归档到 done/<adapter-id>/。

    返回 {"archived": [{file, plugin, dest}], "unclaimed": [{file, reason}]}。
    """
    inbox_dir = Path(inbox_dir)
    result: dict = {"archived": [], "unclaimed": []}
    for f in _iter_inbox_files(inbox_dir):
        pid, detail = _detect_plugin(f)
        if pid is None:
            result["unclaimed"].append({"file": str(f), "reason": detail})
            if verbose:
                print(f"  [收件箱] 未认领: {f.name}（{detail}）")
            continue
        dest_dir = inbox_dir / INBOX_DONE / pid
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f.name
        if dest.exists():
            # 同名归档已存在：内容相同视为重复导入跳过；否则加时间戳保留两份
            if dest.read_bytes() == f.read_bytes():
                f.unlink()
                result["archived"].append({"file": str(f), "plugin": pid,
                                           "dest": str(dest), "duplicate": True})
                if verbose:
                    print(f"  [收件箱] 重复导入已跳过: {f.name}")
                continue
            dest = dest_dir / (dest.stem + "__"
                               + datetime.now().strftime("%Y%m%d%H%M%S")
                               + dest.suffix)
        shutil.move(str(f), str(dest))
        result["archived"].append({"file": str(f), "plugin": pid,
                                   "dest": str(dest), "duplicate": False})
        if verbose:
            print(f"  [收件箱] 已认领归档: {f.name} -> {pid}/")
    return result


def inbox_plugins(inbox_dir: Path) -> list[dict]:
    """归档目录 -> sources.plugins 条目（路径即源声明）。

    done/<adapter-id>/ 下的 zip/json 逐个重新 detect 校验：归档文件若被
    人为改坏，跳过并在 verbose 下提示，不拖垮整次同步。
    """
    inbox_dir = Path(inbox_dir)
    done = inbox_dir / INBOX_DONE
    plugins: list[dict] = []
    if not done.is_dir():
        return plugins
    for sub in sorted(done.iterdir()):
        if not sub.is_dir() or sub.name not in PLUGIN_IDS:
            continue
        for f in sorted(sub.iterdir()):
            if not f.is_file() or f.suffix.lower() not in (".zip", ".json"):
                continue
            pid, _ = _detect_plugin(f)
            if pid is None:
                print(f"  [warn] 归档文件无法通过结构校验，已跳过: {f}")
                continue
            plugins.append({"id": pid, "paths": [str(f)]})
    return plugins


# ---- 数据库 sid 快照（新增判定） ----

def _db_sids(db: Path) -> set[str]:
    if not Path(db).exists():
        return set()
    con = sqlite3.connect(str(db))
    try:
        return {r[0] for r in con.execute("SELECT sid FROM sessions")}
    except sqlite3.DatabaseError:
        return set()
    finally:
        con.close()


# ---- 主流程 ----

def run_sync(root: Path = Path("."),
             sources_path: Path | str = "sources.json",
             db_path: Path | str = "harvester.db",
             inbox_dir: Path | str | None = None,
             exports_dir: Path | str | None = None,
             include_notes: bool = True,
             only: set[str] | None = None,
             verbose: bool = False) -> dict:
    """一键同步：收件箱收割 -> 全量导出 -> 整库重建索引 -> 差异报告。

    各路径默认相对 root。include_notes 默认 True——工具步骤记录藏在
    note 消息的 raw 里，过滤 note 会让重建后的 steps 表变空（G1/G2/G4
    报告失去数据源，v0.12 真机实测回归），分析口径必须包含。
    only: 限定启用的 adapter id（None=全部），测试或仅需导出型源时传入
    以避免触碰本机其他数据源。
    返回完整统计 dict（cli 负责渲染）。
    """
    root = Path(root)
    inbox_dir = Path(inbox_dir) if inbox_dir else root / "inbox"
    exports_dir = Path(exports_dir) if exports_dir else root / "exports"
    db_path = Path(db_path)

    # 1. 收件箱
    harvested = harvest_inbox(inbox_dir, verbose=verbose)

    # 2. 合并数据源声明（本地源照旧读 sources.json；导出源来自收件箱归档）
    sources = load_sources(sources_path)
    plugins = inbox_plugins(inbox_dir)
    merged = dict(sources)
    merged["plugins"] = list(sources.get("plugins", []) or []) + plugins

    # 3. 导出（全量；文件名确定性 => 幂等覆盖）
    before = _db_sids(db_path)
    manifest = export(exports_dir, selection=None, include_notes=include_notes,
                      verbose=verbose, sources=merged, only=only)

    # 4. 索引（整库重建，与 index --from 同口径）
    stats = index_exports(exports_dir, db_path, verbose=verbose)
    after = _db_sids(db_path)

    new_sids = sorted(after - before)
    gone_sids = sorted(before - after)
    return {
        "inbox": harvested,
        "plugins": plugins,
        "export": {"count": manifest.get("count", 0),
                   "message": manifest.get("message")},
        "index": stats,
        "sessions_total": stats.get("sessions", len(after)),
        "new": new_sids,
        "gone": gone_sids,
        "db": str(db_path),
        "exports_dir": str(exports_dir),
    }
