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
- index：`sync` 为整库重建（与 index 命令同一口径）；`update` 为增量
  （v0.46 P0-1，只索引本次写出的 json，2026-10-12 实测全量 274.7 s ->
  增量秒级）；
- 新增会话判定 = 重建前后 sid 集合差，用于报告「本次新增 N 条」。
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from .adapters import PLUGIN_IDS, load_sources
from .dbmeta import record_corpus_watermark
from .exporter import export
from .indexing import index_exports, index_incremental
from .outline import corpus_watermark, scan_all

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


def _db_updated(db: Path) -> dict[str, str | None]:
    """库中 sid -> updated_at 高水位（增量判定用；updated_at 与导出同源同值）。"""
    if not Path(db).exists():
        return {}
    con = sqlite3.connect(str(db))
    try:
        return {r[0]: r[1] for r in
                con.execute("SELECT sid, updated_at FROM sessions")}
    except sqlite3.DatabaseError:
        return {}
    finally:
        con.close()


def source_gone(high: dict[str, str | None], items: list[dict],
                reports: list, only: set[str] | None = None) -> tuple[list[str], list[str]]:
    """源侧已消失的 sid（索引行该清）与"源可疑、保守不清理"的源 id。

    v0.46（P0-1）gone 判定。**两条安全闸**（宁可留脏行，也不误删）：
    1. 只处理本次**真的扫到且 detect()==OK** 的源——源整体不可用（未装 /
       MISSING / STUB / 被 `only` 挡掉）时一律不碰它的历史行；
    2. 某个源**一条都没列出来**时跳过该源（列 0 条通常是"读不到"而不是
       "用户删光了"），并把源 id 回报给调用方打警告。

    已知口径差异（故意）：`update` 以**源**为准（gone 清行），
    `sync`/`index --from` 以**exports 归档**为准（会把这些会话重新建回来）。
    归档文件永不删除——所以 update 清掉的行是可恢复的，不是数据丢失。
    """
    seen = {f"{it['adapter']}:{it['session_id']}" for it in items}
    ok = {rep.adapter_id for rep in reports if rep.status == "OK"}
    by_source: dict[str, list[str]] = {}
    for sid in high:
        src = sid.split(":", 1)[0]
        by_source.setdefault(src, []).append(sid)
    gone: list[str] = []
    protected: list[str] = []
    for src, sids in sorted(by_source.items()):
        if only is not None and src not in only:
            continue
        if src not in ok:
            continue
        listed = [s for s in seen if s.split(":", 1)[0] == src]
        if not listed:
            protected.append(src)
            continue
        gone.extend(s for s in sids if s not in seen)
    return sorted(gone), sorted(protected)


# ---- 主流程 ----

def run_sync(root: Path = Path("."),
             sources_path: Path | str = "sources.json",
             db_path: Path | str = "harvester.db",
             inbox_dir: Path | str | None = None,
             exports_dir: Path | str | None = None,
             include_notes: bool = True,
             only: set[str] | None = None,
             verbose: bool = False,
             keywords_meta: Path | str | None = None,
             keep_keyword_runs: int = 1) -> dict:
    """一键同步：收件箱收割 -> 全量导出 -> 整库重建索引 -> 差异报告。

    各路径默认相对 root。include_notes 默认 True——工具步骤记录藏在
    note 消息的 raw 里，过滤 note 会让重建后的 steps 表变空（G1/G2/G4
    报告失去数据源，v0.12 真机实测回归），分析口径必须包含。
    only: 限定启用的 adapter id（None=全部），测试或仅需导出型源时传入
    以避免触碰本机其他数据源。
    keywords_meta: `keywords_meta.db` 路径（默认 `root/keywords_meta.db`；
    **传 `False` 可关闭收尾 GC**）。
    keep_keyword_runs: 收尾 GC 保留的 run 数（默认 1，见 `_gc_keywords`）。
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
    # 全量重建也是"库已跟上语料"的证据 → 同样落水位（sync 自己扫一遍源）
    wm = corpus_watermark(merged, only=only)
    record_corpus_watermark(db_path, wm)

    # 5. 收尾 GC keywords_meta（B10：别再靠人记得跑 keywords-gc）
    kw = _gc_keywords(root, keywords_meta, keep_keyword_runs)

    new_sids = sorted(after - before)
    gone_sids = sorted(before - after)
    return {
        "inbox": harvested,
        "plugins": plugins,
        "export": {"count": manifest.get("count", 0),
                   "message": manifest.get("message")},
        "index": stats,
        "corpus": wm,
        "keywords_gc": kw,
        "sessions_total": stats.get("sessions", len(after)),
        "new": new_sids,
        "gone": gone_sids,
        "db": str(db_path),
        "exports_dir": str(exports_dir),
    }


def _gc_keywords(root: Path, keywords_meta: Path | str | None,
                  keep: int) -> dict:
    """`sync` 收尾回收 `keywords_meta.db`（v0.47 / 评审 v5 第 7 条）。

    **为什么做进 sync 而不是留一条"记得手动跑 keywords-gc"**：`keyword_stats`
    是**累积表**——每次 `keywords` 追加一整份 n-gram 表（真库实测 4 次 run =
    435,583 行 / 40.2 MB，只有 VACUUM 能回收空闲页）。靠人记 = 库会一直涨
    （v0.45 就是这么涨到 40.2 MB 才发现）。GC 是**幂等**的：库里只剩 1 个 run 时
    它什么都不删、也不 VACUUM（`prune_runs` 保证），所以接在 sync 后面没有副作用。

    **`keep=1` 的依据**：`keyword_stats` 里存的是该次 run 的**全量** n-gram 词表
    （435,583 行），而读路径（`load_stats` / 报告 / API）**只读最新一次 run**；
    保留多个历史 run 不提供任何能力，只把体积乘 N。要留历史请显式传更大的
    `keep`。

    **诚实边界**：这不改变 `keywords_meta.db` 的**口径**（仍是最新一次统计），
    只是别再让无人读的旧 run 无限堆积。`False` = 明确不回收（调用方自己管）。
    失败不抛：GC 是收尾动作，不该让整个 sync 报错（返回的 dict 里如实记原因）。
    """
    if keywords_meta is False:      # 显式关闭
        return {"skipped": True, "reason": "调用方显式关闭（keywords_meta=False）"}
    meta = Path(keywords_meta) if keywords_meta else root / "keywords_meta.db"
    if not meta.is_file():
        return {"skipped": True, "reason": f"没有 {meta.name}（尚未跑过 keywords）"}
    from .kwstats import prune_runs
    try:
        r = prune_runs(meta, keep=keep, vacuum=True)
    except (ValueError, sqlite3.Error) as exc:   # 收尾动作不宜让 sync 整体失败
        return {"skipped": True, "reason": f"{type(exc).__name__}: {exc}"}
    return {"skipped": False, "meta": str(meta), "keep": keep, **r}


def run_update(root: Path = Path("."),
               sources_path: Path | str = "sources.json",
               db_path: Path | str = "harvester.db",
               inbox_dir: Path | str | None = None,
               exports_dir: Path | str | None = None,
               include_notes: bool = True,
               only: set[str] | None = None,
               verbose: bool = False) -> dict:
    """增量同步（v0.22，用户裁决 2026-10-09）：只获取未获取的。

    收件箱收割 -> 库水位对比 -> 仅导出新 sid / updated_at 变化的会话
    -> **只索引本次导出的 json**（v0.46 P0-1；此前是每次整库重建）。

    库即状态：水位 = sessions(sid, updated_at)，与导出 json 同源同值，
    字符串直比即可靠，不建独立水位文件。
    - 新 sid => 导出；
    - updated_at 与库不同 => 重导（续聊场景；库侧缺失时视为变更，宁多勿漏）；
    - 其余跳过——不全量重取历史轨迹，也不重解析。
    首跑无库 = 天然全量。sync 保持全量语义，作为对账/rebuild 基线。
    各路径默认相对 root，参数与 run_sync 一致；返回统计 dict。
    """
    root = Path(root)
    inbox_dir = Path(inbox_dir) if inbox_dir else root / "inbox"
    exports_dir = Path(exports_dir) if exports_dir else root / "exports"
    db_path = Path(db_path)

    # 1. 收件箱（与 sync 同协议：AI CHAT 官方导出包丢 inbox 即自动收割）
    harvested = harvest_inbox(inbox_dir, verbose=verbose)

    # 2. 数据源声明合并（与 run_sync 同逻辑）
    sources = load_sources(sources_path)
    plugins = inbox_plugins(inbox_dir)
    merged = dict(sources)
    merged["plugins"] = list(sources.get("plugins", []) or []) + plugins

    # 3. 水位对比：只挑未获取 / 已变更的会话
    high = _db_updated(db_path)
    adapters, items, reports = scan_all(verbose=verbose, sources=merged, only=only)
    # 语料水位在**扫描时刻**测（= 本次 update 真正消费到的语料状态），
    # 索引成功后写进库当基准；此后语料再变 → 报告显 STALE。
    wm = corpus_watermark(adapters=adapters)
    wanted: set[str] = set()
    new_sids: list[str] = []
    updated_sids: list[str] = []
    for it in items:
        sid = f"{it['adapter']}:{it['session_id']}"  # 与 indexing 入库口径一致
        src_u = it.get("updated_at")
        if sid not in high:
            wanted.add(sid)
            new_sids.append(sid)
        elif src_u and src_u != high.get(sid):
            wanted.add(sid)
            updated_sids.append(sid)

    # 4. 选择性导出（空集则跳过，连 scan 都省）；复用第 3 步的扫描结果
    #    （v0.46：此前 export 会再扫一遍全源，真机实测这第二遍 ~10 s）
    if wanted:
        manifest = export(exports_dir, selection=None,
                          include_notes=include_notes, verbose=verbose,
                          sources=merged, only=only, select_sids=wanted,
                          scanned=(adapters, items, reports))
        export_count = manifest.get("count", 0)
        export_msg = manifest.get("message")
    else:
        manifest, export_count, export_msg = {}, 0, None

    # 5. 索引（v0.46 P0-1：只索引本次写出的 json + 清掉源侧消失的行）
    idx_files = [e["file"] for e in manifest.get("exported", [])]
    gone, protected = source_gone(high, items, reports, only=only)
    stats = index_incremental(exports_dir, db_path, idx_files,
                              drop_sids=gone, verbose=verbose)
    record_corpus_watermark(db_path, wm)

    return {
        "mode": "incremental",
        "inbox": harvested,
        "plugins": plugins,
        "export": {"count": export_count, "message": export_msg},
        "index": stats,
        "corpus": wm,
        "sessions_total": stats.get("sessions", len(high) + len(new_sids)),
        "new": new_sids,
        "updated": updated_sids,
        "unchanged": len(items) - len(wanted),
        "gone": gone,
        "gone_protected": protected,
        "db": str(db_path),
        "exports_dir": str(exports_dir),
    }
