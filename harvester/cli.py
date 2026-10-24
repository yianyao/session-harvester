# -*- coding: utf-8 -*-
"""命令行入口。

用法：
  python -m harvester probe [--out sources.json] [-v]
      固化探测：扫描本机文件系统签名，发现所有 AI 会话数据源（含未知候选）
  python -m harvester scan [--sources sources.json] [--out DIR] [-v]
      扫描数据源，输出会话纲要（outline.md / outline.json）
  python -m harvester export --select "3,5-9" [--out DIR] [--all] [--with-notes] [-v]
      按纲要序号导出；--all 导出全部
  python -m harvester adapters [--sources sources.json]
      仅显示各数据源探测状态（STUB 类附带登录态）
  python -m harvester weblogin check|init-config|prepare <product>
      网页/客户端 Chat 登录态三级流程（探测→账号配置→浏览器引导登录）
  python -m harvester sync [--root .] [--db] [--inbox] [--exports] [--no-notes]
                           [--keywords-keep-runs N] [--no-prune-keywords] [-v]
      一键同步：收件箱收割 → 全量导出 → 整库重建索引 → 收尾回收 keywords_meta
      （幂等，可挂定时；GC 见 keywords-gc，--no-prune-keywords 可关闭）
  python -m harvester aggregate [--from exports/|--all] [--out corpus.md]
      聚合会话为语料（蒸馏第 1 步的机械部分，带 SRC 溯源锚点）
  python -m harvester report-tools [--sources] [--db] [--since N天] [--out report.md]
      工具调用/失败率统计（steps 表结构化口径，含重试/放弃率）
  python -m harvester report-errors [--db] [--since N天] [--out report.md]
                            [--rules 规则表.yaml] [--no-attrib]
      错误三分类（env/tool_interface/context）+ 开场/中途/收尾分桶（G2）；
      每个模式行带归因**候选**（根因/应对/置信），未命中显式标「未归因」；
      --no-attrib 只要事实层，--rules 换规则表（缺省用随包 attrib-rules.yaml）
  python -m harvester attrib [--db] [--rules 规则表.yaml] [--json 文件]
                            [--brief 文件] [--since N天] [--meta 台账库]
      归因层：错误 → 根因候选 + 应对候选（人读/机读两出口不共用实现）
  python -m harvester attrib-review [--meta 台账库] [--list]
                            [--pattern 模式] [--rule 规则id] [--class 分类]
                            [--verdict accepted|rejected|revise]
                            [--note 备注] [--by 裁决人]
      归因台账（B 档：人裁决进 attribations，裁过的不再进候选；只写台账，不碰采集库）
  python -m harvester evidence-pack [--kind errors|skills|chains|attrib]
                            [--db] [--since N天] [--top N]
                            [--out 包.md] [--json 包.json] [--meta 台账库]
      评审包（P6-6）：判据头 + 证据体 + **判定槽**三段；等级 R0–R3 由内容算
      （有归因候选且可回填=R3，只有计数=R1）；没有判定槽的包不是评审包，是报告
  python -m harvester report-skill [--db] [--skill <名>] [--out report.md]
      Skill 行为画像：按 skill 聚合调用/触发任务/调用后行为链（G4）
  python -m harvester suggest-agents [--db] [--min-count 3] [--out 文件] [--meta 路径]
      从错误模式生成 AGENTS.md 候选条目（建议池，人工审阅后并入）（G2 闭环）
  python -m harvester suggest-status --meta 路径 --key <key> --status pending|adopted|rejected
      建议池审阅结论落库（独立 meta 库 suggestion_status 表，不碰采集库）
  python -m harvester triage [--db] [--since N天] [--cards-root 目录] [--min-count 2] [--out 文件]
  python -m harvester draft --sid <sid> [--type pitfall] [--out 文件] [--max-chars 24000]
      蒸馏队列：新会话确定性初筛（新错误pattern/旧坑重现/Skill行为链/高信号会话）
  python -m harvester cards validate --root 目录 [--db] [--topics-meta 库]
      知识卡片 §8 规范校验（frontmatter 完整性 + 锚点有效性）（G3）；
      给了 --topics-meta 时追加「卡片 ↔ 主题注册表」一致性核对
  python -m harvester cards new --sid <sid> --root 目录 [--type pitfall] [--turn N]
      从索引库会话生成卡片脚手架（evidence 留白，人工补全后 validate）
  python -m harvester kb-init [--root ~/.workbuddy/knowledge]
      建知识库骨架（幂等，不覆盖既有文件）
  python -m harvester kb-stats [--root ...]
      知识库盘点（话题文件数 / INDEX 一致性 / 台账条数）
  python -m harvester index [--from exports/|--all] [--db harvester.db]
                            [--deepseek-file conversations.json]
      构建 FTS5 全文索引（search 与 MCP search_history 的前置）
  python -m harvester search "<query>" [--db] [-n 20] [--source <id>]
      全文检索历史会话
  python -m harvester read <序号> [--sources] [--turn N|all|last]
      分层读取：按序号读会话，--turn 逐回合下钻（RetroLens 式）
  python -m harvester pack --select "3,5-9" [--tokens 2000] [--question "..."]
      产出跨 agent 上下文交接包（ai-hist pack 式）
  python -m harvester mcp-serve [--sources] [--db]
      MCP stdio server：任意 agent 运行时直查历史（list/search/read/pack）
  python -m harvester suppress list [--meta topics_meta.db]
      会话抑制（v0.49）：列出已抑制会话（谁 / 何时 / 为什么）
  python -m harvester suppress candidates [--db harvester.db] [--meta topics_meta.db]
                                         [--min-jaccard 0.92]
      抑制候选清单（v0.52，只读）：重复会话 + 零 user 轮次；人按标题裁决、
      复制命令，不用背 sid
  python -m harvester suppress add --sid <sid> --reason "..." [--apply]
      抑制会话：删**仓内副本**（库内三张表的行 + exports 副本；三张表 = 会话/消息/工具步），不删采集源；
      默认 dry-run。此后 sync/update 在导出步拦、索引侧兜底，不再入库
  python -m harvester suppress remove --sid <sid> [--apply]
      取消抑制（不恢复数据；语料还在时下次 sync 会重新导出入库）
  python -m harvester suppress prune-exports [--apply]
      按抑制表清 exports 里的旧副本（默认 dry-run）
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

from .adapters import PLUGIN_IDS, build_adapters, load_sources
from .discovery import probe_and_write
from .distill import (KB_DEFAULT_ROOT, MASTER_DEFAULT_ROOT,
                      corpus_from_exports, corpus_from_sources, kb_init,
                      kb_stats)
from .exporter import export, parse_selection
# 评估口径的唯一来源（CLI 只引用，不写字面量——防两处漂移）
from .harvesteval import DEFAULT_MIN_PER_WINDOW as _EVAL_MIN_PER_WINDOW
from .harvesteval import DEFAULT_MIN_TOTAL as _EVAL_MIN_TOTAL
from .harvesteval import DEFAULT_THRESHOLD as _EVAL_DEFAULT_THRESHOLD
from .harvesteval import DEFAULT_WINDOW_DAYS as _EVAL_DEFAULT_WINDOW
from .indexing import fts5_available, index_exports, index_sources, search, sessions_in_db
from .outline import scan_all, write_outline
from .pack import build_pack, write_pack
from .reader import render_read
from .sync import run_sync, run_update
from .weblogin import PRODUCTS, check_all, init_config, prepare


def cmd_probe(args) -> int:
    findings, sources = probe_and_write(Path(args.out), verbose=args.verbose)
    print(f"发现 {len(findings)} 条数据源线索 -> {args.out}")
    for f in findings:
        tag = f.matched_adapter or "UNKNOWN"
        print(f"  [{f.kind}] {f.path}")
        print(f"      匹配: {tag} | 证据: {f.evidence} | {f.note}")
    if "_unknown" in sources:
        print(f"\n注意: {len(sources['_unknown'])} 条未知候选已记录在 sources.json 的 _unknown 节，")
        print("待确认产品后可按 README 契约实装 Adapter。")
    print(f"\n下一步: python -m harvester scan --sources {args.out}")
    return 0


def cmd_scan(args) -> int:
    sources = load_sources(args.sources)
    _, items, reports = scan_all(verbose=args.verbose, sources=sources)
    outdir = Path(args.out)
    paths = write_outline(outdir, items, reports)
    print(f"共 {len(items)} 条可选会话。")
    for rep in reports:
        mark = {"OK": "[+] ", "STUB": "[~] ", "MISSING": "[-] "}[rep.status]
        print(f"  {mark}{rep.name} ({rep.adapter_id}): {rep.detail}")
        for h in rep.hints:
            print(f"        - {h}")
    print(f"纲要已写入: {paths['outline_md']}")
    if items:
        print("下一步: python -m harvester export --select <序号> --out <目录>")
    return 0


def cmd_export(args) -> int:
    outdir = Path(args.out)
    selection = None if args.all else args.select
    if not args.all and not selection:
        print("错误: 需要 --select <序号表达式> 或 --all", file=sys.stderr)
        return 2
    sources = load_sources(args.sources)
    try:
        manifest = export(outdir, selection=selection,
                          include_notes=args.with_notes, verbose=args.verbose,
                          sources=sources)
    except ValueError as e:  # --select 表达式无法解析
        print(f"错误: {e}", file=sys.stderr)
        return 2
    if manifest.get("message"):
        print(manifest["message"])
        return 1
    print(f"已导出 {manifest['count']} 个会话 -> {outdir}")
    for e in manifest["exported"][:20]:
        print(f"  {e['no']:>4} {e['file']}")
    if len(manifest["exported"]) > 20:
        print(f"  ... 其余 {len(manifest['exported']) - 20} 条见 export_manifest.json")
    return 0


def cmd_adapters(args) -> int:
    sources = load_sources(args.sources)
    for ad in build_adapters(sources):
        try:
            rep = ad.detect()
            print(f"{rep.status:<7} {rep.name} ({rep.adapter_id}): {rep.detail}")
            for h in rep.hints:
                print(f"        - {h}")
        except Exception as e:  # noqa: BLE001
            print(f"ERROR   {ad.name} ({ad.id}): {e}")
    return 0


def cmd_weblogin(args) -> int:
    if args.action == "check":
        results = check_all()
        for r in results:
            icon = {"LIKELY_LOGGED_IN": "[已登录*]", "NOT_LOGGED_IN": "[未登录] ",
                    "LOCKED": "[锁定?]   ", "UNKNOWN": "[无法判定]"}[r["status"]]
            print(f"{icon} {r['product']}: {r['detail']}")
            for p in r["profiles"]:
                if p["cookies"]:
                    print(f"        {p['profile']}: {p['cookies']} 枚 cookie")
        print("\n(* 基于本地 cookie 存在性判定，无法离线验证会话有效性)")
        print("流程: 未登录 -> weblogin init-config 填账号 -> weblogin prepare <product>")
        return 0
    if args.action == "init-config":
        path = init_config(Path(args.out))
        print(f"账号配置模板已生成: {path}")
        print("注意: 当前为预留接口——prepare 采用人工登录，暂不读取此文件；")
        print("      未来实现自动登录时才会消费（届时建议改用系统 keyring）。")
        print("安全提示: 文件为明文，仅存本机，已列入 .gitignore，勿提交仓库/网盘。")
        return 0
    if args.action == "prepare":
        return prepare(args.product)


def cmd_aggregate(args) -> int:
    out = Path(args.out)
    if args.from_dir:
        stats = corpus_from_exports(Path(args.from_dir), out)
        mode = f"export 目录 {args.from_dir}"
    elif args.all:
        stats = corpus_from_sources(load_sources(args.sources), out,
                                    include_notes=args.with_notes)
        mode = "全部数据源实时聚合"
    else:
        print("错误: 需要 --from <exports目录> 或 --all", file=sys.stderr)
        return 2
    print(f"聚合完成（{mode}）：{stats['files']} 个会话，{stats['chars']} 字符 -> {stats['out']}")
    return 0


def cmd_sync(args) -> int:
    root = Path(args.root)
    r = run_sync(root=root, sources_path=root / args.sources,
                 db_path=root / args.db,
                 inbox_dir=root / args.inbox,
                 exports_dir=root / args.exports,
                 include_notes=not args.no_notes, verbose=args.verbose,
                 keywords_meta=(False if args.no_prune_keywords
                                else root / args.keywords_meta),
                 keep_keyword_runs=args.keywords_keep_runs,
                 suppress_meta=(False if args.no_suppress
                                else root / args.suppress_meta))
    inbox = r["inbox"]
    for a in inbox["archived"]:
        tag = "重复跳过" if a.get("duplicate") else "已认领"
        print(f"  [收件箱-{tag}] {Path(a['file']).name} -> {a['plugin']}/")
    for u in inbox["unclaimed"]:
        print(f"  [收件箱-未认领] {Path(u['file']).name}（留在原地，请人工处理）")
        print(f"      原因: {u['reason']}")
    if r["export"]["message"]:
        print(f"  [导出] {r['export']['message']}")
    else:
        print(f"  [导出] {r['export']['count']} 个会话 -> {r['exports_dir']}")
    if r["plugins"]:
        by: dict[str, int] = {}
        for p in r["plugins"]:
            by[p["id"]] = by.get(p["id"], 0) + 1
        print("  [导出源] " + ", ".join(f"{k}×{v}" for k, v in sorted(by.items())))
    st = r["index"]
    print(f"  [索引] {st['sessions']} 会话 / {st['messages']} 消息 -> {r['db']}"
          + (f"（跳过损坏 {st['skipped']} 文件）" if st.get("skipped") else ""))
    kw = r.get("keywords_gc") or {}
    if kw.get("skipped"):
        if args.verbose:
            print(f"  [keywords GC] 未执行：{kw.get('reason', '')}")
    else:
        print(f"  [keywords GC] 保留最近 {kw['keep']} 次 run"
              f"（keep={kw['kept']}）；删除 run {kw['deleted_runs']} 个 / "
              f"统计行 {kw['deleted_stats']} 条"
              f"{'，已 VACUUM' if kw['vacuumed'] else ''}")
    print(f"本次新增 {len(r['new'])} 个会话"
          + (f"（移除 {len(r['gone'])} 个）" if r["gone"] else ""))
    for sid in r["new"][:10]:
        print(f"  + {sid}")
    if len(r["new"]) > 10:
        print(f"  ... 其余 {len(r['new']) - 10} 条略")
    sup = r.get("suppress") or {}
    if sup.get("count"):
        pr = sup.get("pruned") or {}
        print(f"  [抑制] 抑制中 {sup['count']} 条：导出拦下 {sup.get('export_skipped', 0)}"
              f" / 索引兜底跳过 {sup.get('index_skipped', 0)}"
              f" / 清掉 exports 旧副本 {len(pr.get('deleted') or [])}"
              f"（清单摘掉 {pr.get('manifest_removed', 0)} 条）")
        for sid in sup["sids"][:5]:
            print(f"      - {sid}")
        if len(sup["sids"]) > 5:
            print(f"      ... 其余 {len(sup['sids']) - 5} 条略（suppress list）")
    print("后续: harvester report-tools / report-errors / report-skill / cards new "
          "均直接消费该索引库")
    return 0


def cmd_update(args) -> int:
    root = Path(args.root)
    r = run_update(root=root, sources_path=root / args.sources,
                   db_path=root / args.db,
                   inbox_dir=root / args.inbox,
                   exports_dir=root / args.exports,
                   include_notes=not args.no_notes, verbose=args.verbose,
                   suppress_meta=(False if args.no_suppress
                                  else root / args.suppress_meta))
    inbox = r["inbox"]
    for a in inbox["archived"]:
        tag = "重复跳过" if a.get("duplicate") else "已认领"
        print(f"  [收件箱-{tag}] {Path(a['file']).name} -> {a['plugin']}/")
    for u in inbox["unclaimed"]:
        print(f"  [收件箱-未认领] {Path(u['file']).name}（留在原地，请人工处理）")
        print(f"      原因: {u['reason']}")
    if r["export"]["message"]:
        print(f"  [导出] {r['export']['message']}")
    else:
        print(f"  [导出] {r['export']['count']} 个会话"
              f"（新增 {len(r['new'])} / 更新 {len(r['updated'])}"
              f" / 未变跳过 {r['unchanged']}）-> {r['exports_dir']}")
    if r["plugins"]:
        by: dict[str, int] = {}
        for p in r["plugins"]:
            by[p["id"]] = by.get(p["id"], 0) + 1
        print("  [导出源] " + ", ".join(f"{k}×{v}" for k, v in sorted(by.items())))
    st = r["index"]
    print(f"  [索引] {st['sessions']} 会话 / {st['messages']} 消息 -> {r['db']}"
          + (f"（跳过损坏 {st['skipped']} 文件）" if st.get("skipped") else ""))
    print(f"  [索引增量] 重解析 {st.get('parsed', 0)} 个文件"
          f"（写入 {st.get('messages_parsed', 0)} 条消息）"
          f" / 清掉 {st.get('dropped', 0)} 个已消失会话"
          + (f" / 清单缺文件 {len(st['missing'])}" if st.get("missing") else ""))
    print(f"  [清点] 新增 {len(r['new'])} / 更新 {len(r['updated'])}"
          f" / 未变 {r['unchanged']} / 源已消失 {len(r['gone'])}")
    for sid in r["new"][:10]:
        print(f"  + {sid}")
    for sid in r["updated"][:10]:
        print(f"  ~ {sid}（updated_at 变化，重导）")
    if r.get("gone"):
        print("  已清出索引（exports 归档保留，全量 sync 会按归档重建）: "
              + ", ".join(r["gone"][:5]) + (" ..." if len(r["gone"]) > 5 else ""))
    for src in r.get("gone_protected", []):
        print(f"      [warn] {src} 本次一条会话都没列出——按「读不到」处理，"
              "未清理其历史行（避免误删）")
    sup = r.get("suppress") or {}
    if sup.get("count"):
        pr = sup.get("pruned") or {}
        print(f"  [抑制] 抑制中 {sup['count']} 条：导出拦下 {sup.get('export_skipped', 0)}"
              f" / 并进 drop_sids {sup.get('drop_sids', 0)} 条"
              f"（本次共清掉 {r['index'].get('dropped', 0)} 个 sid 的行，含源侧消失）"
              f" / 清掉 exports 旧副本 {len(pr.get('deleted') or [])}")
    _print_db_age(Path(r["db"]), r.get("corpus") or {})
    if len(r["new"]) + len(r["updated"]) > 20:
        print("  ... 其余略")
    print("增量口径：只获取未获取的（新 sid / updated_at 变化）、"
          "只索引本次写出的 json；全量对账请用 sync")
    return 0


def _print_db_age(db: Path, wm: dict) -> None:
    """库龄三行（v0.46 P0-2）：库文件时刻 / 语料最新 / 入库水位 + 谁更新。

    与产物头同一判据（`dbmeta` 的 `index_meta` 水位），所以"命令行说新鲜"
    与"报告头不显 STALE"永远一致。
    """
    from .dbmeta import read_corpus_watermark
    stored = read_corpus_watermark(db)
    try:
        db_str = time.strftime("%Y-%m-%d %H:%M:%S",
                               time.localtime(db.stat().st_mtime))
    except OSError:
        db_str = "（库文件不存在）"
    print(f"  [库龄] 库文件 {db_str}")
    if wm:
        print(f"      语料最新 {wm.get('mtime_str') or '—'}"
              f"（覆盖 {len(wm.get('covered') or [])} 源 / {wm.get('files', 0)} 文件；"
              f"未覆盖 {'/'.join(wm.get('uncovered') or []) or '无'}）")
    if stored["mtime"]:
        print("      入库水位 "
              + time.strftime("%Y-%m-%d %H:%M:%S",
                              time.localtime(stored["mtime"]))
              + "（本库索引时实际看到的语料时刻）")
    else:
        print("      入库水位 —（本库还没有水位记录，跑一次 update 即建立）")
    if wm and stored["mtime"]:
        # 逐源指出"谁比库新"——真机上正在写入的活会话会让语料永远比库新，
        # 这时能一眼看出是哪条源，而不是笼统一句"库旧了"。
        newer = [f"{k}（{v}）"
                 for k, v in (wm.get("by_source") or {}).items()
                 if (wm.get("by_source_epoch") or {}).get(k, 0) > stored["mtime"]]
        if newer:
            print("      [STALE] 以下源比入库水位新，建议再跑一次 update: "
                  + "；".join(newer))
        else:
            print("      [新鲜] 所有已覆盖源的语料都不晚于入库水位")


def cmd_kb_init(args) -> int:
    r = kb_init(Path(args.root))
    for c in r["created"]:
        print(f"  [新建] {c}")
    for s in r["skipped"]:
        print(f"  [跳过-已存在] {s}")
    print(f"知识库根目录: {r['root']}")
    return 0


def cmd_kb_stats(args) -> int:
    s = kb_stats(Path(args.root))
    print(f"话题文件: {s['topic_files']} 个；INDEX 表行: {s['index_rows']}；"
          f"台账条目: {s['feedback_items']}")
    if s["missing_files"]:
        print(f"[!] INDEX 引用但文件缺失: {s['missing_files']}")
        return 1
    print("INDEX 与文件一致。")
    return 0


def cmd_kb_publish(args) -> int:
    """把项目正本（knowledge/）单向发布到 WorkBuddy 知识库镜像位。

    方向纪律（2026-10-14 用户裁决）：正本=仓内 knowledge/（随项目迁移）；
    镜像位=WorkBuddy 主程序资料库的读取位置，只喂不收、永不删除镜像里
    的多余文件。正本改了重跑本命令即可，镜像位不手工维护。
    """
    from .distill import publish_master
    try:
        rep = publish_master(Path(args.root), Path(args.dest),
                             dry_run=bool(args.dry_run))
    except FileNotFoundError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2
    if args.dry_run:
        print("[预演] 下面只是算数，**一个文件都没写**（确认后去掉 --dry-run 再跑）")
    for sub in ("cards", "topics"):
        if sub in rep:
            r = rep[sub]
            print(f"{sub}: 发布 {r['copied']} 个文件"
                  f"（{r['skipped']} 个已是最新跳过）")
    if rep["missing"]:
        print(f"[!] 正本里没有这些子目录（跳过）: {', '.join(rep['missing'])}")
    print(f"镜像位: {Path(args.dest).resolve()}")
    return 0


def cmd_index(args) -> int:
    if not fts5_available():
        print("错误: 当前 Python 的 SQLite 不含 FTS5（罕见；请换官方构建的解释器）",
              file=sys.stderr)
        return 1
    db = Path(args.db)
    # 通用官方导出文件入口：--export-file <id>=<路径>（可多次）
    plugins: list[dict] = []
    for spec in (args.export_file or []):
        pid, sep, path = spec.partition("=")
        if not sep or pid not in PLUGIN_IDS:
            print(f"错误: --export-file 格式应为 <id>=<路径>，id 可选值: "
                  f"{sorted(PLUGIN_IDS)}；收到: {spec!r}", file=sys.stderr)
            return 2
        plugins.append({"id": pid, "paths": [path]})
    if args.deepseek_file:
        plugins.append({"id": "deepseek-export", "paths": [args.deepseek_file]})
    if plugins:
        sources = load_sources(args.sources)
        sources["plugins"] = sources.get("plugins", []) + plugins
        stats = index_sources(sources, db, include_notes=args.with_notes,
                              verbose=args.verbose)
    elif args.from_dir:
        stats = index_exports(Path(args.from_dir), db, verbose=args.verbose)
    elif args.all:
        stats = index_sources(load_sources(args.sources), db,
                              include_notes=args.with_notes,
                              verbose=args.verbose)
    else:
        print("错误: 需要 --from <exports目录> 或 --all 或 "
              "--export-file <id>=<路径>", file=sys.stderr)
        return 2
    print(f"索引完成: {stats['sessions']} 会话 / {stats['messages']} 消息 -> {stats['db']}")
    print(f"检索: python -m harvester search \"<关键词>\" --db {db}")
    return 0


def cmd_search(args) -> int:
    db = Path(args.db)
    if not db.exists():
        print(f"错误: 索引库不存在: {db}（先运行 harvester index）", file=sys.stderr)
        return 2
    rows = search(db, args.query, limit=args.limit,
                  source=args.source)
    if not rows:
        print("无匹配结果。")
        return 0
    print(f"{len(rows)} 条命中（库内 {sessions_in_db(db)} 会话）:\n")
    for r in rows:
        print(f"[{r['sid']}] {r['title']}")
        print(f"    role={r['role']} | {r['snippet']}")
    print("\n读取完整会话: python -m harvester read <纲要序号>  "
          "(序号见 harvester scan)")
    return 0


def cmd_read(args) -> int:
    sources = load_sources(args.sources)
    adapters_list, items, _ = scan_all(sources=sources)
    adapters = {ad.id: ad for ad in adapters_list}
    try:
        nos, dropped = parse_selection(str(args.no), len(items))
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2
    if dropped or not nos:
        print(f"错误: 序号 {args.no} 无效（当前共 {len(items)} 条，"
              f"如纲要过期请重新 scan）", file=sys.stderr)
        return 2
    it = next(x for x in items if x["no"] == nos[0])
    try:
        rec = adapters[it["adapter"]].load_session(it["session_id"])
    except Exception as e:  # noqa: BLE001 - 纲要过期/源损坏时给提示而非 traceback
        print(f"错误: 会话加载失败: {e}", file=sys.stderr)
        return 1
    try:
        text, n_turns = render_read(rec, args.turn)
    except (IndexError, ValueError) as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
    print(text)
    if args.turn != "all":
        print(f"\n(共 {n_turns} 个回合: --turn 1..{n_turns} / last / all)")
    return 0


def cmd_pack(args) -> int:
    sources = load_sources(args.sources)
    adapters_list, items, _ = scan_all(sources=sources)
    adapters = {ad.id: ad for ad in adapters_list}
    try:
        nos, dropped = parse_selection(args.select, len(items))
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2
    if dropped:
        print(f"[warn] 越界序号已忽略: {dropped}")
    if not nos:
        print("错误: 选择结果为空", file=sys.stderr)
        return 2
    recs = []
    for n in nos:
        it = next(x for x in items if x["no"] == n)
        try:
            recs.append((adapters[it["adapter"]].load_session(it["session_id"]), n))
        except Exception as e:  # noqa: BLE001 - 单条失败不拖垮整个交接包
            print(f"[warn] 序号 {n} 加载失败已跳过: {e}")
    if not recs:
        print("错误: 所选会话全部加载失败", file=sys.stderr)
        return 1
    content = build_pack(recs, total_budget=args.tokens, question=args.question)
    out = write_pack(Path(args.out), content)
    print(f"交接包已写入: {out}（~{args.tokens} tokens，{len(recs)} 个会话）")
    print("用途: 直接粘贴给另一个 agent 作为接续上下文。")
    return 0


def cmd_mcp_serve(args) -> int:
    """MCP stdio server（v0.45 起含进化数据面 6 工具）。"""
    from .mcpserver import serve
    return serve(args.sources, args.db,
                 topics_meta=args.topics_meta,
                 chain_root=args.chain_root,
                 cards_root=args.cards_root,
                 artifacts_meta=args.artifacts_meta,
                 suggestions_meta=args.suggestions_meta)


def cmd_api_serve(args) -> int:
    """只读 HTTP JSON API（apiserve.run 自带自检与安全守卫）。"""
    from .apiserve import run
    db = Path(args.db)
    # v0.81 P0-3：chain-root / reviews-meta 的缺省一律**相对 db 解析**
    # （不再相对 cwd）——从别的目录起服就静默丢链文件/丢台账，
    # 页面显示"尚无 chain 长文"的假空态，是最难被发现的那类失效。
    chain_root = (Path(args.chain_root) if args.chain_root
                  else db.resolve().parent / "knowledge" / "topics")
    reviews_meta = (Path(args.reviews_meta) if args.reviews_meta
                    else db.resolve().parent / "cards_meta.db")
    traces_root = (Path(args.traces_root) if args.traces_root
                   else Path.home() / ".workbuddy" / "traces")
    # v0.81 P1-4：库龄判定开关（默认关——要 stat 一遍语料盘）。开起来后
    # /api/meta 的指纹才带真 stale（True/False），不开就恒 None（没判）。
    want_wm = bool(getattr(args, "corpus_watermark", False))
    return run(db, port=args.port, host=args.host,
               token=args.token,
               cards_root=Path(args.cards_root) if args.cards_root else None,
               suggestions_meta=Path(args.suggestions_meta)
               if args.suggestions_meta else None,
               topics_meta=Path(args.topics_meta)
               if args.topics_meta else None,
               chain_root=chain_root,
               keywords_meta=Path(args.keywords_meta)
               if getattr(args, "keywords_meta", None) else None,
               reviews_meta=reviews_meta,
               reports_dir=Path(args.reports_dir)
               if getattr(args, "reports_dir", None) else None,
               traces_root=traces_root, want_watermark=want_wm)


def cmd_report_tools(args) -> int:
    """工具调用/失败率统计。

    两种口径（--db 给定且 steps 表有数据时用结构化，否则 note 扫描）：
    - 结构化（推荐）：index 时写入的 steps 表——支持重试/放弃率；
    - note 扫描：从数据源实时加载，按 [tool_call]/[tool_result] 标记汇总。
    """
    from .toolstats import (collect_stats, collect_stats_from_db,
                            render_report, tool_rows)
    flow = None
    if args.db:
        dbp = Path(args.db)
        if dbp.is_file():
            import sqlite3 as _s
            con = _s.connect(str(dbp))
            try:
                has = con.execute(
                    "SELECT COUNT(*) FROM steps").fetchone()[0]
            except _s.OperationalError:
                has = 0
            finally:
                con.close()
            if has:
                from .toolstats import (collect_model_stats, collect_source_stats,
                                        render_model_table, render_source_table)
                stats, flow = collect_stats_from_db(dbp,
                                                    since_days=args.since)
                by_source = collect_source_stats(dbp, since_days=args.since)
                by_model = collect_model_stats(dbp, since_days=args.since)
                report = render_report(stats, flow=flow)
                report += render_source_table(by_source)
                report += render_model_table(by_model)
                _emit_report(report, args)
                if args.json:
                    _emit_json(args.json, "report-tools/1", {
                        "basis": "steps",
                        "flow": flow,
                        "tools": tool_rows(stats),
                        "sources": _group_rows(by_source),
                        "models": _group_rows(by_model),
                    }, dbp=dbp)
                return 0
        print(f"[warn] {args.db} 无 steps 数据，回退 note 扫描口径",
              file=sys.stderr)

    adapters_list, items, _reports = scan_all(sources=load_sources(args.sources))
    adapters = {ad.id: ad for ad in adapters_list}

    def _records():
        for it in items:
            ad = adapters[it["adapter"]]
            try:
                yield ad.load_session(it["session_id"])
            except Exception as e:  # noqa: BLE001 - 单会话失败不拖垮统计
                if args.verbose:
                    print(f"  [skip] {it['adapter']}#{it['session_id'][:12]}: {e}",
                          file=sys.stderr)

    stats = collect_stats(_records())
    _emit_report(render_report(stats), args)
    if args.json:
        _emit_json(args.json, "report-tools/1",
                   {"basis": "notes", "tools": tool_rows(stats)},
                   dbp=Path(args.db) if args.db else None)
    return 0


def cmd_report_traces(args) -> int:
    """OTel trace 工具统计（耗时/失败率/取消，来自 ~/.workbuddy/traces）。"""
    from .tracestats import collect, render_report
    stats, gen, summary = collect(Path(args.root))
    report = render_report(stats, gen, summary)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8", newline="\n")
        print(f"报告已写入 {out}")
    else:
        print(report, end="")
    return 0


def cmd_report_errors(args) -> int:
    """错误三分类报告（G2 确定性一半）：env/tool_interface/context + 位置分桶。

    P6-5：模式行接归因候选（why/fix/置信）。归因是**叠加层**——
    三分类计数**仍是事实层原口径**（不因归因改写），归因只补"为什么/怎么办"。
    归因不可用时**不降级成"未归因"**：报告头明说"不含归因"，
    然后把报告照常给出（report-errors 不因归因层故障而不可用）。
    """
    from .errstats import collect_errors_from_db, render_report
    dbp = Path(args.db)
    if not dbp.is_file():
        print(f"错误: 索引库不存在: {dbp}（先运行 harvester index）",
              file=sys.stderr)
        return 2
    errors, meta = collect_errors_from_db(dbp, since_days=args.since)
    attributions = None
    if not getattr(args, "no_attrib", False):
        attributions = _load_attributions(errors, getattr(args, "rules", None))
    _emit_report(render_report(errors, meta, attributions=attributions), args)
    if args.json:
        _emit_json(args.json, "report-errors/1",
                   {"errors": errors, "meta": meta,
                    "attributions": attributions}, dbp=dbp)
    return 0


def _load_attributions(errors: list, rules_path) -> dict | None:
    """尽力加载归因层；**任何失败都不静默**（返回 None 时由报告头声明）。

    为什么不 `raise`：`report-errors` 的职责是出事实报告，归因是 additive
    增强（attrib.py docstring 同口径）。但"没启用"必须让人看见——
    所以失败原因打到 stderr，且报告头会写"本报告不含归因"。
    """
    from .attrib import RuleError, collect_attributions, load_rules
    try:
        rules = load_rules(Path(rules_path) if rules_path else None)
    except (RuleError, RuntimeError) as exc:
        print(f"提示: 归因层未启用（{exc}）；本次报告不含归因。",
              file=sys.stderr)
        return None
    try:
        return collect_attributions(errors, rules)
    except (RuleError, ValueError) as exc:      # 规则编译错等
        print(f"提示: 归因层未启用（{exc}）；本次报告不含归因。",
              file=sys.stderr)
        return None


def cmd_report_chains(args) -> int:
    """工具链失败模式报告（P2-1）：长回合/连击/循环/空转/高步 Top N。"""
    from .chainstats import collect, render_report
    dbp = Path(args.db)
    if not dbp.is_file():
        print(f"错误: 索引库不存在: {dbp}（先运行 harvester index）",
              file=sys.stderr)
        return 2
    d = collect(dbp, since_days=args.since, top=args.top)
    _emit_report(render_report(d, top=args.top), args)
    if args.json:
        _emit_json(args.json, "report-chains/1", d, dbp=dbp)
    return 0


def cmd_report_harness(args) -> int:
    """Harness 体检报告（v0.81 P1-2）：每数据源一行画像。"""
    from .harnessstats import collect, render_report
    dbp = Path(args.db)
    if not dbp.is_file():
        print(f"错误: 索引库不存在: {dbp}（先运行 harvester index）",
              file=sys.stderr)
        return 2
    d = collect(dbp, since_days=args.since)
    _emit_report(render_report(d), args)
    if args.json:
        _emit_json(args.json, "report-harness/1", d, dbp=dbp)
    return 0


def cmd_report_daily(args) -> int:
    """每日数据速览（P5-⑤，用户 2026-10-16 批准）：一次产出五段 md。

    §4-5 验收：--out 一次产出含五段（队列四数字 + tools/errors/chains 摘要）
    的 md。**只读聚合**：不写库、不改库、不写建议台账。
    四队列与 view 工作台（/api/overview）**同源**——同一批主仓函数、同一组
    参数（collect_triage(min_count=2, top_n=10)、api_reports_agents 缺省
    min_count=3/max_items=15、run_eval 缺省窗口），本命令是出报告时刻的
    快照；工作台是实时现算，数字若有出入以工作台为准（md 里如实注明）。
    """
    from datetime import datetime
    dbp = Path(args.db)
    if not dbp.is_file():
        print(f"错误: 索引库不存在: {dbp}（先运行 harvester index）",
              file=sys.stderr)
        return 2
    meta_p = Path(args.meta) if args.meta else None
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    L = ["# 每日数据速览（report-daily）", "",
         f"> 生成于 {stamp}｜tools/errors/chains 窗口=最近 {args.since} 天"
         f"｜索引库 {dbp}",
         "> **只读聚合**：本命令不写库不改库；四队列与 view 工作台同源"
         "（同模块同函数同参数），但本文件是快照——以工作台实时现算为准。",
         ""]
    con = sqlite3.connect(f"file:{dbp.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row  # 与 view open_ro 对齐（errstats 等
    # 接收外部 con 的函数按 Row 取列；cmd_report_* 直连版各自内部已设）
    try:
        # —— 一、四队列（与 view 工作台同源） ——
        from . import suggestmeta as _sm
        from .apiserve import api_reports_agents
        from .triage import collect_triage
        from .harvesteval import VERDICT_INCONCLUSIVE, run_eval

        records = _sm.load_records(meta_p)
        decide = sum(1 for r in records.values()
                     if r.get("status") == "pending")
        agents = api_reports_agents(con, dbp, {}, meta_db=meta_p)
        repair = sum(1 for e in agents["entries"]
                     if (e.get("unresolved_count") or 0) >= 1)
        tri = collect_triage(dbp, cards_root=args.cards_root,
                             min_count=2, top_n=10, con=con)
        distill = len(tri.get("new_patterns") or [])
        ev = run_eval(dbp, meta_p)
        verify = sum(1 for e in ev.get("entries") or []
                     if e.get("verdict") == VERDICT_INCONCLUSIVE)
        # P6-7：本面是**聚合面**，等级取"最弱一环"——四队列是计数、
        # 三份摘要分别是工具/错误/链型计数，**没有一个分节带根因候选**
        # （本命令不加载归因规则，错误段因此也不会是 R2/R3）。
        # G2 机读出口（P9 扩面）：与本段文本**同一批变量**，不重算。
        jdata: dict = {"queues": {"decide": decide, "repair": repair,
                                  "distill": distill, "verify": verify}}
        from .reviewable import assess as _assess
        L[5:5] = [_assess(entries=decide + repair + distill + verify,
                          candidates=0,
                          note="聚合面：四队列与三份摘要都是计数，无根因候选"
                          ).render(), ""]
        L += ["## 一、四队列（待办速览）", "",
              "| 队列 | 数字 | 口径来源（与 view 工作台同源） |",
              "|---|---|---|",
              f"| 待裁决 | {decide} | suggestmeta.load_records[status=pending] |",
              f"| 待修补 | {repair} | "
              "api_reports_agents.entries[unresolved_count>=1] |",
              f"| 待蒸馏 | {distill} | "
              "triage.collect_triage.new_patterns（min_count=2, top_n=10） |",
              f"| 待验证 | {verify} | "
              f"harvesteval.run_eval[verdict={VERDICT_INCONCLUSIVE}] |", "",
              "> 队列明细与「下一步动作」在 view 工作台（每条可点开处理）；"
              "建议台账"
              + (f"（{meta_p}）不存在时本段按空账计，属诚实空态。" if
                 (meta_p and not meta_p.is_file()) else "缺失时同。"), ""]

        # —— 二、工具调用摘要 ——
        from .toolstats import collect_stats_from_db, render_report as rr_tools
        try:
            has = con.execute("SELECT COUNT(*) FROM steps").fetchone()[0]
        except sqlite3.OperationalError:
            has = 0
        L += ["## 二、工具调用摘要（最近 %.g 天）" % args.since, ""]
        if not has:
            L += ["（库内无 steps 数据——先运行 `harvester index`；"
                  "本段诚实留空，不编数字。）", ""]
        else:
            stats, flow = collect_stats_from_db(dbp, since_days=args.since)
            L += [rr_tools(stats, flow=flow), ""]
            from .toolstats import tool_rows as _tool_rows
            jdata["tools"] = {"basis": "steps", "flow": flow,
                              "tools": _tool_rows(stats)}

        # —— 三、错误分类摘要 ——
        from .errstats import collect_errors_from_db, render_report as rr_err
        L += ["## 三、错误分类摘要（最近 %.g 天）" % args.since, ""]
        errors, emeta = collect_errors_from_db(dbp, since_days=args.since,
                                               con=con)
        jdata["errors"] = {"errors": errors, "meta": emeta}
        if emeta.get("error_count", 0) == 0:
            L += ["（窗口内无错误步骤——诚实空态。）", ""]
        else:
            L += [rr_err(errors, emeta), ""]

        # —— 四、工具链失败模式摘要 ——
        from .chainstats import collect as ch_collect
        from .chainstats import render_report as rr_chain
        L += ["## 四、工具链失败模式摘要（最近 %.g 天）" % args.since, ""]
        cd = ch_collect(dbp, since_days=args.since, top=args.top)
        jdata["chains"] = cd
        L += [rr_chain(cd, top=args.top), ""]

        # —— 五、账目 ——
        L += ["## 五、账目", "",
              f"- 生成命令：`harvester report-daily --db {args.db}"
              f" --meta {args.meta or '-'} --since {args.since}`",
              f"- 建议台账：{meta_p if (meta_p and meta_p.is_file()) else '（不存在，队列按空账计）'}",
              f"- 卡片目录（蒸馏比对）：{args.cards_root or '（未给）'}",
              "- 本文件为只读聚合快照；写库动作一律不在此命令里。",
              ""]
    finally:
        con.close()
    _emit_report("\n".join(L), args)
    if getattr(args, "json", None):
        _emit_json(args.json, "report-daily/1", jdata, dbp=dbp)
    return 0


def cmd_report_skill_join(args) -> int:
    """T4 skill 进化 join：chain 时间线锚点 × skill 调用锚点交叉表。"""
    from .skilljoin import build_cross, render_cross
    dbp = Path(args.db)
    if not dbp.is_file():
        print(f"错误: 索引库不存在: {dbp}（先运行 harvester index）",
              file=sys.stderr)
        return 2
    if not Path(args.chain).is_file():
        print(f"错误: chain 长文不存在: {args.chain}", file=sys.stderr)
        return 2
    d = build_cross(Path(args.chain), dbp, skill=args.skill,
                    margin_days=args.margin_days)
    _emit_report(render_cross(d), args)
    if getattr(args, "json", None):
        _emit_json(args.json, "report-skill-join/1", d, dbp=dbp)
    return 0


def cmd_topic_consolidate(args) -> int:
    """主题梳理流水线（v0.26）：出梳理包 / 执行 plan / 看零散登记。

    确定性一半 + Agent 一半：工具只按显式规则缩小候选范围并确定性执行，
    语义归组一律来自人填的 plan（不写死主题）。
    """
    from .consolidate import (NOISE_MAX_CHARS, apply_plan, build_plan_packet,
                              list_noise, load_plan)
    from .noisetriage import dump_triage, render_brief, render_triage, triage
    meta = Path(args.meta)
    if args.plan_seed:
        # 分诊 JSON → plan 草稿（v0.35）：机械映射 + 完整性校验在工具里，
        # 逐轮的语义判断走 --judgment 文件（红线：判断不落进代码）
        from .planseed import (build_seed, dump_plan, load_judgment,
                               load_triage, render_seed_stats, unhandled_sids)
        from .topics import list_topics
        try:
            triage_d = load_triage(Path(args.plan_seed))
            judgment = load_judgment(Path(args.judgment) if args.judgment else None)
            plan, stats = build_seed(triage_d, list_topics(meta), judgment,
                                     craft_topic=args.craft_topic)
        except (ValueError, RuntimeError) as exc:
            print(f"[错误] {exc}", file=sys.stderr)
            return 2
        text = render_seed_stats(stats) + dump_plan(plan)
        if args.seed_out:
            out = Path(args.seed_out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8", newline="\n")
            print(f"[topic-consolidate] plan 草稿已写入: {out}"
                  f"（assign {stats['_total']['assign']} 条 / "
                  f"noise {stats['_total']['noise']} 条）", file=sys.stderr)
        else:
            print(text)
        for v in (args.require_covered.split(",") if args.require_covered else []):
            v = v.strip()
            left = unhandled_sids(stats, v)
            if left:
                print(f"[错误] 判定类 {v} 仍有 {len(left)} 条未归置（--require-covered）: "
                      + ", ".join(left[:5]) + (" …" if len(left) > 5 else ""),
                      file=sys.stderr)
                return 2
        return 0
    if (args.triage or args.triage_out or args.triage_json
            or args.triage_brief is not None):
        # 零散分诊（v0.30）：按"只要求查询 / 无整合诉求"判"取信息 vs 整合信息"
        t = triage(Path(args.db) if args.db else Path("harvester.db"), meta,
                   max_turns=args.triage_max_turns,
                   include_deep=args.triage_deep)
        wrote = False
        if args.triage_out:
            out = Path(args.triage_out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(render_triage(t), encoding="utf-8", newline="\n")
            print(f"[topic-consolidate] 分诊报告已写入: {out}", file=sys.stderr)
            wrote = True
        if args.triage_json:
            # 全量出口（不截断）：给人读的报告每类只 60 条，照它填 plan 会漏
            import json as _json
            out = Path(args.triage_json)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(_json.dumps(dump_triage(t), ensure_ascii=False,
                                       indent=1),
                           encoding="utf-8", newline="\n")
            print(f"[topic-consolidate] 分诊 JSON 已写入: {out}"
                  f"（{len(t['rows'])} 行，全量不截断）", file=sys.stderr)
            wrote = True
        if args.triage_brief is not None:
            only = args.triage_brief
            text = render_brief(t, None if only == "*" else only,
                                chars=args.brief_chars)
            if args.triage_brief_out:
                out = Path(args.triage_brief_out)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(text, encoding="utf-8", newline="\n")
                print(f"[topic-consolidate] 分诊简报已写入: {out}", file=sys.stderr)
                wrote = True
            else:
                print(text)
                wrote = True
        summary = (f"[topic-consolidate] 分诊扫描 {t['scanned']} 条；"
                   + "、".join(f"{k} {v}" for k, v in
                               sorted(t["counts"].items(),
                                      key=lambda kv: -kv[1])))
        if wrote:
            print(summary, file=sys.stderr)
        else:
            print(render_triage(t))
        return 0
    if args.noise_list:
        rows = list_noise(meta)
        print(f"零散会话登记 {len(rows)} 条（meta 库 sessions_noise）")
        for r in rows:
            print(f"- {r['sid']}  {r['created']}  {r['reason']}")
        return 0
    if args.apply:
        plan = load_plan(Path(args.apply))
        r = apply_plan(meta, plan, dry_run=not args.yes,
                       snap_dir=Path(args.snap_dir) if args.snap_dir else None)
        if not r["ok"]:
            for e in r["errors"]:
                print(f"[错误] {e}", file=sys.stderr)
            return 2
        if not r["applied"]:
            print("[dry-run] 校验通过，未写库。加 --yes 执行。")
            p = r["preview"]
            print(f"  将新建 {p['new_topics']} 个类目、改名 {p['renames']} 个、"
                  f"并入 {len(p['groups'])} 组、舍弃 {p['discard']} 个、"
                  f"零散登记 {p['noise']} 条、保留 {p['keep']} 个")
            for g in p["groups"]:
                print(f"    {g['target']} ← {g['from']} 个源")
            return 0
        print(f"[topic-consolidate] 已执行；备份 {r['backup']}")
        print(f"[topic-consolidate] 主题数 {r['topics_after']}；"
              f"新增零散登记 {r['noise_added']} 条")
        for t in r["topics"]:
            print(f"  {t['id']} 成员{t['members']:>4}  {t['name']}")
        return 0
    # 缺省：出梳理包
    dbp = Path(args.db) if args.db else None
    root = Path(args.chain_root) if args.chain_root else None
    packet = build_plan_packet(
        meta, db_path=dbp, chain_root=root,
        noise_max_chars=(args.noise_max_chars
                         if args.noise_max_chars else NOISE_MAX_CHARS))
    if args.plan_out:
        out = Path(args.plan_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(packet, encoding="utf-8", newline="\n")
        print(f"[topic-consolidate] 梳理包已写入: {out}"
              f"（{len(packet)} 字符）", file=sys.stderr)
    else:
        print(packet)
    return 0


def cmd_deadcode_scan(args) -> int:
    """死代码扫描（v0.37）——把每轮收尾的手检做成工具（见 `deadcode.py`）。

    缺省**只提示**（启发式必然有误报）；加 `--fail-on-found` 才以退出码 1 判失败，
    供 CI/套件按需当门用。
    """
    from .deadcode import (DEFAULT_ROOTS, found_total, render_deadcode, scan,
                           unstable_total)
    roots = tuple(args.roots) if args.roots else DEFAULT_ROOTS
    report = scan(roots=roots, report_root=args.report_root)
    text = render_deadcode(report)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8", newline="\n")
        print(f"[deadcode-scan] 报告已写入: {out}", file=sys.stderr)
    else:
        print(text)
    n = found_total(report)
    # v0.45：扫描期间被改动的文件，其结论不可信——**不能当死代码，也不能假装通过**
    # （退出码 3＝"未执行/不确定"，与 regress 的三分口径一致）。成因见 deadcode.py。
    u = unstable_total(report)
    if u:
        print(f"[deadcode-scan] {len(report['unstable'])} 个文件在扫描期间被改动 → "
              f"{u} 条发现不可信（并发编辑会假红）；请等写入结束重跑",
              file=sys.stderr)
        if args.fail_on_found and not n:
            return 3
    if n and args.fail_on_found:
        print(f"[deadcode-scan] 发现 {n} 条（--fail-on-found）", file=sys.stderr)
        return 1
    return 0


def cmd_regress(args) -> int:
    """端到端回归语料（v0.43）——从空库跑完整条链路，逐项核对关键数字。

    **本命令不读真库**：全部在 `tempfile` 里用语料造临时索引库 + 临时 meta 库
    （`indexing.SCHEMA` + `index_session`），跑完即删。没有 `--db` / `--meta`
    参数是**故意的**——给一个"顺手指向真库"的入口，等于给这条铁律留后门。

    退出码：0 全通过 / 1 有断言失败 / 3 **未执行**（前置缺失，不是通过）。
    """
    from .regress import (CORPUS_VARIANTS, EXIT_FAIL, EXIT_NOT_RUN, EXIT_OK,
                          corpus_by_name, dump_json, render_report, run_regress)
    if args.corpus not in CORPUS_VARIANTS:
        print(f"错误: 未知语料 {args.corpus!r}（可选："
              + "、".join(sorted(CORPUS_VARIANTS)) + "）", file=sys.stderr)
        return 2
    r = run_regress(Path(args.temp_base) if args.temp_base else Path.cwd(),
                    corpus=corpus_by_name(args.corpus),
                    keep_temp=args.keep_temp)
    text = render_report(r)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8", newline="\n")
        print(f"[regress] 人读报告已写入: {out}", file=sys.stderr)
    if args.json:
        jout = Path(args.json)
        jout.parent.mkdir(parents=True, exist_ok=True)
        jout.write_text(dump_json(r), encoding="utf-8", newline="\n")
        print(f"[regress] 机读结果已写入: {jout}"
              f"（{r['assertions']} 条断言，失败 {r['failed_assertions']}）",
              file=sys.stderr)
    if not args.out and not args.json:
        print(text)
    elif args.list:
        print(text)
    summary = (f"[regress] {r['status_cn']}｜步骤：通过 {r['counts']['pass']} / "
               f"失败 {r['counts']['fail']} / 未执行 {r['counts']['not_run']} / "
               f"不适用 {r['counts']['not_applicable']}｜断言 "
               f"{r['assertions']} 条，失败 {r['failed_assertions']} 条"
               + (f"｜临时目录 {r['temp_dir']}" if r.get("temp_dir") else ""))
    print(summary, file=sys.stderr)
    for s in r["steps"]:
        if s["status"] != "pass":
            print(f"  - {s['step']}：{s['failed'] or s['why']}",
                  file=sys.stderr)
    return {"pass": EXIT_OK, "fail": EXIT_FAIL,
            "not_run": EXIT_NOT_RUN}[r["status"]]


def cmd_topic_candidates(args) -> int:
    """T5 自动聚类候选推荐器：标题 n-gram + 任务签名产候选。

    **只产候选，不改注册表**（topics_meta 只读，人工裁决后再
    topic register）；范围/特征全数据驱动，不写死任何主题。"""
    from .candidates import build_candidates, render_candidates
    dbp = Path(args.db)
    if not dbp.is_file():
        print(f"错误: 索引库不存在: {dbp}（先运行 harvester index）",
              file=sys.stderr)
        return 2
    r = build_candidates(dbp, min_sim=args.min_sim, min_size=args.min_size,
                         top_tools=args.top_tools,
                         topics_meta=Path(args.topics_meta)
                         if args.topics_meta else None)
    _emit_report(render_candidates(r), args)
    return 0


def _kw_stopword_paths(args) -> list[Path]:
    """keywords 的停用词表链（v0.23）。

    默认启用随包分发的通用表（`kwstats.DEFAULT_STOPWORDS`），用户用
    `--stopwords PATH`（可多次）叠加私有表；`--no-stopwords` 完全关闭。
    返回顺序：默认表在前、用户表在后（叠加语义，与顺序无关）。
    """
    from .kwstats import DEFAULT_STOPWORDS
    paths: list[Path] = []
    if not getattr(args, "no_stopwords", False):
        paths.append(DEFAULT_STOPWORDS)
    for p in (getattr(args, "stopwords", None) or []):
        paths.append(Path(p))
    return paths


def cmd_keywords(args) -> int:
    """P2-2 n-gram 关键词统计：只统计 messages.raw（H3 契约/H38），
    落 keywords_meta.db（runs+stats），可出报告。范围 --sid/--topic
    数据驱动（任何主题可用，不写死）。"""
    from .consolidate import noise_sids
    from .kwstats import build_stats, render_report
    dbp = Path(args.db)
    if not dbp.is_file():
        print(f"错误: 索引库不存在: {dbp}（先运行 harvester index）",
              file=sys.stderr)
        return 2
    ns = [x.strip() for x in str(args.n).split(",") if x.strip()]
    if not ns or any(not x.isdigit() or int(x) < 2 for x in ns):
        print(f"错误: --n 需为 >=2 的档位列表（如 2,3），收到: {args.n}",
              file=sys.stderr)
        return 2
    tm = Path(args.topics_meta) if args.topics_meta else None
    s = build_stats(dbp, Path(args.meta), ns=[int(x) for x in ns],
                    role=args.role,
                    sids=list(args.sid) if args.sid else None,
                    topic_ids=list(args.topic) if args.topic else None,
                    topics_meta=tm,
                    stopwords_paths=_kw_stopword_paths(args),
                    # 已判定零散的会话不参与统计（v0.26；未登记时该集合为空，
                    # 行为与接线前一致）
                    exclude_sids=noise_sids(tm))
    print(f"统计完成: 消息 {s['total_msgs']} 条 → "
          f"{args.meta}（run 追加）")
    if getattr(args, "keep_runs", None):
        from .kwstats import prune_runs
        pr = prune_runs(Path(args.meta), keep=args.keep_runs,
                        vacuum=bool(args.vacuum))
        print(f"[keywords] GC：保留最近 {args.keep_runs} 次 run，"
              f"删除 run {pr['deleted_runs']} 个 / 统计行 {pr['deleted_stats']} 条"
              f"{'，已 VACUUM' if pr['vacuumed'] else ''}",
              file=sys.stderr)
    _emit_report(render_report(s, top=args.top), args)
    return 0


def cmd_keywords_gc(args) -> int:
    """keywords_meta.db 垃圾回收（v0.45）：保留最近 N 次 run，可 VACUUM。

    为什么单独给一个入口（而不只在 `keywords --keep-runs` 上）：GC 是**定期动作**
    ——库会随每次 keywords 线性膨胀（真库 4 次 run = 435,583 行 / 40.2 MB），
    使用者需要"现在就回收"而不必先跑一次新统计。
    """
    from .kwstats import prune_runs
    meta = Path(args.meta)
    if not meta.is_file():
        print(f"错误: meta 库不存在: {meta}", file=sys.stderr)
        return 2
    before = meta.stat().st_size
    try:
        r = prune_runs(meta, keep=args.keep_runs, vacuum=bool(args.vacuum))
    except ValueError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    after = meta.stat().st_size
    print(f"[keywords-gc] 保留最近 {args.keep_runs} 次 run"
          f"（keep={r['kept']}）；删除 run {r['deleted_runs']} 个 / "
          f"统计行 {r['deleted_stats']} 条"
          f"{'，已 VACUUM' if r['vacuumed'] else '（未删则跳过 VACUUM）'}；"
          f"体积 {before / 1048576:.1f} MB → {after / 1048576:.1f} MB")
    return 0


def cmd_suggest_agents(args) -> int:
    """从错误模式生成 AGENTS.md 候选条目（只产出建议文件，不直接改 AGENTS.md）。"""
    from .agent_suggest import build_suggestions
    from .errstats import collect_errors_from_db
    from .suggestmeta import load_statuses
    dbp = Path(args.db)
    if not dbp.is_file():
        print(f"错误: 索引库不存在: {dbp}（先运行 harvester index）",
              file=sys.stderr)
        return 2
    errors, _meta = collect_errors_from_db(dbp, since_days=args.since)
    if not errors:
        print("steps 表无错误记录，无建议可产出。")
        return 0
    statuses = load_statuses(Path(args.meta)) if args.meta else None
    content = build_suggestions(errors, min_count=args.min_count,
                                max_items=args.max, statuses=statuses)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8", newline="\n")
    print(f"建议已写入: {out}")
    print("纪律: 请人工审阅后自行并入 AGENTS.md（本工具绝不直接改它）。")
    if getattr(args, "coverage", False):
        from .agent_suggest import build_suggestion_entries, coverage_report
        from .agent_suggest import render_coverage
        from .suggestmeta import registry_counts
        entries = build_suggestion_entries(errors, min_count=args.min_count,
                                           max_items=args.max,
                                           statuses=statuses)["entries"]
        # 人账总览（v0.46 P2-5）：另两本账的行数从主题注册表读，缺库即 0
        tmeta = getattr(args, "topics_meta", None)
        accounts = None
        if tmeta:
            counts = registry_counts(Path(tmeta))
            accounts = dict(counts, topics_meta=str(tmeta))
        cov = coverage_report(entries, statuses or {}, accounts=accounts)
        target = Path(args.coverage_out) if getattr(args, "coverage_out",
                                                    None) else None
        if target:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(render_coverage(cov), encoding="utf-8",
                              newline="\n")
            print(f"覆盖核对已写入: {target}")
        else:
            print(render_coverage(cov))
    return 0


def cmd_report_skill(args) -> int:
    """Skill 行为画像（G4 确定性主干）：按 skill 聚合调用/行为链/触发任务。"""
    from .behstats import (collect_skill_invocations, render_skill_report,
                           skill_summary)
    dbp = Path(args.db)
    if not dbp.is_file():
        print(f"错误: 索引库不存在: {dbp}（先运行 harvester index）",
              file=sys.stderr)
        return 2
    invocations = collect_skill_invocations(dbp)
    _emit_report(render_skill_report(invocations, skill_filter=args.skill,
                                     min_calls=args.min_calls),
                 args)
    if args.json:
        # 与文本路径**同一个过滤条件**与**同一个聚合函数**（skill_summary）
        picked = ([i for i in invocations if i["skill"] == args.skill]
                  if args.skill else invocations)
        _emit_json(args.json, "report-skill/1", {
            "filters": {"skill": args.skill, "min_calls": args.min_calls},
            "summary": skill_summary(picked),
            "invocations": picked,
        }, dbp=dbp)
    return 0


def cmd_suggest_status(args) -> int:
    """建议池状态落库（suggestion_status 表，独立 meta 库，不碰采集库）。

    v0.46 additive：可同时记 `--adopted-at`（A/B 评估的分界 T，回填历史采纳日期）
    与 `--note`（人工说明；`harvest-eval` 认 `pattern: <错误pattern>` 那一行）。
    """
    from .suggestmeta import set_status
    try:
        set_status(Path(args.meta), args.key, args.status,
                   adopted_at=getattr(args, "adopted_at", None),
                   note=getattr(args, "note", None))
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2
    extra = []
    if getattr(args, "adopted_at", None):
        extra.append(f"adopted_at={args.adopted_at}")
    if getattr(args, "note", None):
        extra.append("note=已更新")
    print(f"已记录: {args.key} -> {args.status}"
          f"{('（' + '，'.join(extra) + '）') if extra else ''}（{args.meta}）")
    return 0


def cmd_harvest_eval(args) -> int:
    """自进化评估闭环（v0.46 P1-4）：adopted 规则的 A/B 三值判定 + 样本量。

    只读采集库（mode=ro）；报告头带库快照与 STALE 标记。工具**不自动改
    AGENTS.md**——"自"= 评估自动化，退休/回滚由人决定后手动改。
    """
    from .harvesteval import render_report, run_eval
    dbp = Path(args.db)
    if not dbp.is_file():
        print(f"错误: 索引库不存在: {dbp}（先运行 harvester update）",
              file=sys.stderr)
        return 2
    try:
        result = run_eval(dbp, Path(args.meta), since=args.since,
                          window_days=args.window_days,
                          min_per_window=args.min_per_window,
                          min_total=args.min_total,
                          threshold=args.threshold)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2
    _emit_report(render_report(result), args)
    return 0


def cmd_ledger_sync(args) -> int:
    """人账同步（v0.46 P2-5）：把**主题注册表**的既有裁决照抄进建议台账。

    只做两件幂等的事：缺 `topic:<id>` 就建、已采纳但缺 `adopted_at` 就补；
    **绝不覆盖**人已写下的 `adopted_at` / `note`，也**不代替人判断**。
    默认 dry-run（只打印差异），要写必须显式 `--apply`。
    """
    from .suggestmeta import (backfill_adopted_at, registry_counts, sync_rows,
                              topic_rows)
    rows = topic_rows(Path(args.topics_meta))
    if not rows:
        print(f"主题注册表里没有可入账的主题（{args.topics_meta}）")
        return 0
    result = sync_rows(Path(args.meta), rows, apply=args.apply)
    back = backfill_adopted_at(Path(args.meta), apply=args.apply)
    counts = registry_counts(Path(args.topics_meta))
    print(f"人账同步（{'已写入' if args.apply else 'dry-run，未写入'}）："
          f"新建 {len(result['created'])} / 补分界 {len(result['filled'])}"
          f" / 不动 {len(result['unchanged'])}"
          f"（主题 {counts['topics']}，零散 {counts['noise']}）")
    if back["filled"]:
        print(f"存量已采纳条目补 adopted_at（取自各自台账 updated_at）"
              f"{len(back['filled'])} 条：")
        for key in back["filled"][:10]:
            print(f"  ~ {key}")
    for key in result["created"][:10]:
        print(f"  + {key}")
    for key in result["filled"][:10]:
        print(f"  ~ {key}（补 adopted_at）")
    if (result["would_write"] or back["filled"]) and not args.apply:
        print("确认无误后加 --apply 写入。")
    return 0


def cmd_suppress(args) -> int:
    """会话抑制（v0.49，提案 §2.4）：从**仓内副本**里移除没营养的会话。

    默认 dry-run（与 `topic-consolidate` / `ledger-sync` 同款家规），
    `--apply` 才写。删的是**仓内副本**——库内三张表（会话/消息/工具步）的行 + exports 下的
    `.json`/`.md`；**采集源（DSH/WorkBuddy 自己的会话文件）一字节不动**。

    口径诚实（提案 §5 ⑧ 用户裁决：**不设时限**）：`remove` 本身不恢复数据，
    能不能恢复**只取决于语料是否还在**——语料还在，下次 sync/update 会重新
    导出并入库；语料没了就真没了。文案一律如此，不写任何「N 天内可恢复」。
    """
    from .exporter import prune_suppressed
    from .suppress import (db_row_counts, list_suppressed, suppress,
                           suppressed_sids, unsuppress)
    meta = Path(args.meta)
    sids = list(dict.fromkeys(getattr(args, "sids", None) or []))  # 去重保序

    if args.action == "list":
        rows = list_suppressed(meta)
        if not rows:
            print(f"[suppress] 抑制表为空（{meta}）。")
        else:
            print(f"[suppress] 已抑制 {len(rows)} 条（{meta}）：")
            for r in rows:
                print(f"  - {r['sid']}  {r['created']}"
                      f"  by={r['created_by'] or '-'}"
                      f"  reason={r['reason'] or '-'}")
        print("[suppress] 抑制只作用于**仓内副本**（库内三张表的行 + exports 副本），"
              "采集源不动；取消抑制本身不恢复数据——只要语料还在，"
              "下次 sync/update 会重新导出并入库。")
        return 0

    if args.action == "candidates":
        from .suppress import find_candidates
        dbp = Path(args.db)
        # --min-jaccard（v0.53，用户 2026-10-14 明示）：按 J 下限过滤重复
        # 簇候选。先验参数（0~1），非法就报错退出，不许静默当没给。
        min_j = getattr(args, "min_jaccard", None)
        if min_j is not None and not (0.0 < float(min_j) <= 1.0):
            print(f"[suppress] 错误：--min-jaccard 需在 0~1 之间"
                  f"（收到 {min_j}）。J=两段会话文字的重复度，"
                  "1=逐字相同；如 0.92 = 只列出重复 92% 以上的候选。",
                  file=sys.stderr)
            return 2
        known = suppressed_sids(meta)
        r = find_candidates(dbp, known_sids=known)
        if min_j is not None:
            from .suppress import filter_dup_clusters
            r = filter_dup_clusters(r, float(min_j))
        n_cand = sum(len(c["candidates"]) for c in r["dup_clusters"])
        print(f"[suppress] 候选清单（只读）：扫了 {r['scanned']} 个会话 → "
              f"{len(r['dup_clusters'])} 个重复簇 / {n_cand} 条候选；"
              f"零 user 轮次 {len(r['zero_user'])} 条；"
              f"已在抑制表的 {len(known)} 条已剔除。")
        if min_j is not None:
            print(f"[suppress] 按 J≥{float(min_j):g} 过滤：只列重复度达标"
                  f"的候选（{r['dropped_low_j']} 条低 J 候选未列出；"
                  "「同簇链式重复」没有独立 J 值，始终保留）。")
        print("[suppress] 裁决方式：看标题 → 复制下面的命令（默认 dry-run，"
              "加 --apply 才真删）。拿不准就先在 view 里点开会话看内容——"
              "会话详情页也有「删除（抑制）」按钮，同样不用记 sid。")
        print("[suppress] J 的白话版：两段会话文字的重复度（0~1，1=逐字相同）。"
              "≥0.90 即基本同文——同一对话的多份副本，或得到几乎相同回答的"
              "重复提问；话题相同但内容不同的多次提问 J 会明显更低，不会进候选。")
        for i, c in enumerate(r["dup_clusters"], 1):
            print(f"\n[重复簇 {i}] 代表（保留）：《{c['rep_title'] or '（无标题）'}》"
                  f" {c['rep']}")
            for cd in c["candidates"]:
                jtxt = (f"J={cd['jaccard']}" if cd["jaccard"] is not None
                        else "同簇链式重复")   # 经簇内其他成员 ≥0.90 间接相连
                print(f"  - 《{cd['title'] or '（无标题）'}》 {cd['sid']}  {jtxt}")
                print(f"    python -m harvester suppress add --sid \"{cd['sid']}\""
                      f" --reason \"与 {c['rep'][:60]} 重复（{jtxt}）\"")
        if r["zero_user"]:
            print(f"\n[零 user 轮次] {len(r['zero_user'])} 条"
                  "（库里只有 note/system 消息）：")
            for z in r["zero_user"]:
                print(f"  - 《{z['title'] or '（无标题）'}》 {z['sid']}")
                print(f"    python -m harvester suppress add --sid \"{z['sid']}\""
                      " --reason \"零 user 轮次（仅系统/工具消息）\"")
        if not r["dup_clusters"] and not r["zero_user"]:
            print("[suppress] 没有机械候选——没有要删的就不删，这是好结果。")
        return 0

    if args.action == "remove":
        have = {r["sid"]: r for r in list_suppressed(meta)}
        print(f"[suppress] remove {len(sids)} 个 sid（抑制表现有 {len(have)} 条）"
              + ("——dry-run，未写入" if not args.apply else ""))
        for sid in sids:
            print(f"  - {sid}  " + ("在抑制表里" if sid in have
                                    else "**不在**抑制表里（这次不会删到任何行）"))
        if not args.apply:
            print("[suppress] 加 --apply 才真删抑制表行；取消后要让数据回来，"
                  "得再跑一次 sync（前提是语料还在）。")
            return 0
        n = unsuppress(meta, sids)
        print(f"[suppress] 已取消抑制 {n} 行。")
        print("[suppress] 注意：取消抑制**不会**恢复已删的库内行与 exports 副本。"
              "能不能恢复**只取决于语料是否还在**（DSH/WorkBuddy 自己的会话文件）："
              "语料还在 → 下次 sync/update 会重新导出并入库；语料没了 → 就真没了。"
              "本工具不设恢复时限。")
        return 0

    if args.action == "add":
        dbp = Path(args.db)
        exports = Path(args.exports)
        counts = db_row_counts(dbp, sids)
        preview = prune_suppressed(exports, set(sids), dry_run=True)
        print(f"[suppress] add {len(sids)} 个 sid"
              + ("——dry-run，未写入" if not args.apply else "——写入"))
        for sid in sids:
            c = counts[sid]
            print(f"  - {sid}")
            print(f"      库内行：sessions {c['sessions']} / messages "
                  f"{c['messages']} / steps {c['steps']}"
                  + ("" if c["found"] else "（库内没有这个 sid 的会话行）"))
            if c["title"]:
                print(f"      标题：{c['title']}")
        for e in preview["deleted"]:
            print(f"      exports 旧副本：{e['json']}"
                  + (f" + {e['md']}" if e["md"] else ""))
        if preview["unreadable"]:
            print(f"      [warn] {len(preview['unreadable'])} 个 json 读不出 sid，"
                  "未参与清理："
                  + ", ".join(preview["unreadable"][:3]))
        print(f"      （扫到 {preview['scanned']} 个 json；"
              f"命中旧副本 {len(preview['deleted'])} 个）")
        if not args.apply:
            print("[suppress] dry-run：抑制表、库内行、exports 文件**都未动**；"
                  "确认后加 --apply。")
            return 0
        r = suppress(meta, dbp, sids, reason=args.reason,
                     created_by=args.created_by, exports_dir=exports)
        del_s, del_m, del_p = 0, 0, 0
        for c in r["purged"].values():
            del_s += c["sessions"]
            del_m += c["messages"]
            del_p += c["steps"]
        print(f"[suppress] 已抑制 {len(sids)} 条（抑制表新增 {r['added']} 行）；"
              f"库内已删 sessions {del_s} / messages {del_m} / steps {del_p}；"
              f"exports 已清旧副本 {len(r['pruned']['deleted'])} 个"
              f"（清单摘掉 {r['pruned']['manifest_removed']} 条）")
        if r["pruned"].get("manifest_error"):
            print("      [warn] 导出清单未能更新："
                  f"{r['pruned']['manifest_error']}（下次 export 会整份重写）")
        print("[suppress] 删的是**仓内副本**（库内三张表的行 + exports 下的 "
              ".json/.md），**不删采集源**（DSH/WorkBuddy 自己的会话文件一字节不动）。"
              "此后 sync/update 会在导出步拦住它，不会再入库。")
        return 0

    if args.action == "prune-exports":
        sids_all = suppressed_sids(meta)
        exports = Path(args.exports)
        r = prune_suppressed(exports, sids_all, dry_run=not args.apply)
        print(f"[suppress] prune-exports（{exports}）：抑制表 {len(sids_all)} 条；"
              f"扫到 {r['scanned']} 个 json；命中 {len(r['deleted'])} 个"
              + ("——dry-run，未删任何文件" if not args.apply
                 else f"；已删文件 + 清单摘掉 {r['manifest_removed']} 条"))
        for e in r["deleted"]:
            print(f"  - {e['json']}"
                  + (f" + {e['md']}" if e["md"] else "（没有同名 .md）"))
        if r["unreadable"]:
            print(f"  [warn] {len(r['unreadable'])} 个 json 读不出 sid，"
                  "未参与清理：" + ", ".join(r["unreadable"][:3]))
        if r.get("manifest_error"):
            print(f"  [warn] 导出清单未能更新：{r['manifest_error']}")
        if not args.apply and r["deleted"]:
            print("[suppress] 加 --apply 才真删这些文件。")
        return 0

    print(f"错误: 未知的 suppress 动作 {args.action!r}", file=sys.stderr)
    # argparse 的 required=True 子解析器挡住了这条路；这里留着是为了**手工构造
    # namespace 的调用方**（测试/脚本）也 fail loud，而不是静默什么都不做。
    return 2


def cmd_noise(args) -> int:
    """零散会话登记（P5-⑧，用户 2026-10-16 批准）：判「不归任何主题」的
    会话写进 topics_meta 的 sessions_noise 表。

    与 suppress 的区别（别混）：抑制 = 从**仓内副本**删数据（destructive，
    有确认页）；零散 = 只写 meta 登记表（可逆：删行即撤销），效果是
    candidates / keywords 不再把它们当主题候选簇——会话本身仍在库、仍可检索。
    幂等：已在表里的 sid 跳过。默认 dry-run，--apply 才写（家规同款）。
    """
    from .consolidate import list_noise, register_noise
    meta = Path(args.meta)
    sids = list(dict.fromkeys(getattr(args, "sids", None) or []))  # 去重保序
    have = {r["sid"]: r for r in list_noise(meta)}

    if args.action == "list":
        if not have:
            print(f"[noise] 零散登记表为空（{meta}）。")
        else:
            print(f"[noise] 已登记零散 {len(have)} 条（{meta}）：")
            for r in sorted(have.values(), key=lambda x: x["sid"]):
                print(f"  - {r['sid']}  {r['created']}"
                      f"  reason={r['reason'] or '-'}")
        print("[noise] 零散登记只写 meta 表（可逆：删行即撤销）；"
              "已登记的会话不再进主题候选簇，会话本身仍在库、仍可检索。")
        return 0

    # add（list_noise 只读，缺表也不建表——真写时 register_noise 才建）
    if args.action != "add":
        print(f"错误: 未知的 noise 动作 {args.action!r}", file=sys.stderr)
        # 与 cmd_suppress 尾款同款：argparse 挡住真 CLI，但手工 namespace 的
        # 调用方（测试/脚本）也要 fail loud，而不是静默当 add 跑。
        return 2
    new = [s for s in sids if s not in have]
    print(f"[noise] add {len(sids)} 个 sid（新增 {len(new)}，"
          f"已在表跳过 {len(sids) - len(new)}）"
          + ("——dry-run，未写入" if not args.apply else ""))
    for s in sids:
        print(f"  - {s}  " + ("已在表里（跳过）" if s in have else "将登记"))
    if not args.apply:
        print("[noise] 加 --apply 才写。")
        return 0
    n = register_noise(meta, [{"sid": s,
                               "reason": getattr(args, "reason", "")}
                              for s in new])
    print(f"[noise] 已登记 {n} 条零散会话（{meta}）。")
    return 0


def cmd_cards(args) -> int:
    """知识卡片校验（G3 确定性一半）：§8 frontmatter 规范 + 锚点有效性
    （+ 给了 --topics-meta 时的「卡 ↔ 主题」一致性核对，v0.43）。"""
    from .cards import render_report, validate_cards
    root = Path(args.root)
    if not root.is_dir():
        print(f"错误: 卡片目录不存在: {root}", file=sys.stderr)
        return 2
    dbp = Path(args.db) if args.db else None
    tm = Path(args.topics_meta) if args.topics_meta else None
    if tm is not None and not tm.is_file():
        # fail loud：声明要核对主题却给不出注册表 → 报错退出，不当成
        # "没有主题"（那会把没执行的检查伪装成通过）。
        print(f"错误: 主题注册表不存在: {tm}", file=sys.stderr)
        return 2
    try:
        results, summary = validate_cards(
            root, dbp, topics_meta=tm,
            review_meta=(Path(args.reviews_meta)
                         if getattr(args, "reviews_meta", None) else None),
            show_reviewed=bool(getattr(args, "show_review", False)))
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"错误: 主题核对无法执行: {e}", file=sys.stderr)
        return 2
    report = render_report(results, summary, root)
    _emit_report(report, args)
    if getattr(args, "json", None):
        _emit_json(args.json, "cards-validate/1",
                   {"root": str(root), "summary": summary,
                    "cards": results}, dbp=dbp)
    return 1 if summary["error"] else 0


def cmd_cards_new(args) -> int:
    """从索引库会话生成卡片脚手架（evidence 留白待人工补全）。"""
    from .cards import scaffold_card
    dbp = Path(args.db)
    if not dbp.is_file():
        print(f"错误: 索引库不存在: {dbp}（先运行 harvester index）",
              file=sys.stderr)
        return 2
    try:
        dupes: list[str] = []
        path = scaffold_card(dbp, args.sid, Path(args.root),
                             ctype=args.type, title=args.title,
                             turn=args.turn, dupe_warnings=dupes,
                             dry_run=bool(args.dry_run))
    except (KeyError, ValueError) as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2
    if args.dry_run:
        print(f"[预演] 会写入: {path}（这一个文件；目录与卡片都没动）")
    else:
        print(f"脚手架已生成: {path}")
    for w in dupes:
        print(f"⚠️ {w}")
    print("下一步: 补全 evidence（原会话证据原文）与正文，然后 "
          "cards validate --root ... --db ...")
    return 0


def cmd_cards_index_md(args) -> int:
    """触发词消歧素材（v0.78 §2）：给 AGENTS.md 钩子定稿用。

    只读只列不裁决：触发行清单 + 疑似易混对（归一子串口径）+
    钩子模板。--out 落盘（自动建父目录）或 stdout。机器不出
    「定稿消歧表」——易混是语义问题，人定稿。
    """
    from .cards import build_index_md
    md = build_index_md(Path(args.root))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
        print(f"素材已写入: {out}")
        print("下一步: 人定稿消歧表 → 把「三、钩子模板」整段贴进 AGENTS.md"
              "（改 Select-String 里的目录与占位词）")
    else:
        print(md, end="")
    return 0


def cmd_cards_review(args) -> int:
    """复核台账（v0.74）：人裁过的不许再烦人。

    两种用法：
    - --list：列出台账明细；
    - --file <卡文件名>：把该卡**当前全部警告**一言而决（缺省）或只裁
      --warn 子串命中的警告；verdict=accepted（综述/误报，接受）或
      fixed（已按修复处理）。落账后 validate 不再显示（--show-review 连同显示）。
    """
    from .cards import (_warn_key, card_current_warnings, list_reviews,
                        record_review)
    meta = Path(args.reviews_meta)
    if args.list:
        rows = list_reviews(meta)
        if not rows:
            print("台账为空（还没有任何复核裁决）。")
            return 0
        for r in rows:
            print(f"{r['card_file']}  [{r['verdict']}]  {r['warn_key'][:80]}"
                  + (f"  note={r['note']}" if r.get("note") else ""))
        print(f"共 {len(rows)} 条裁决。")
        return 0
    if not args.file:
        print("错误: 需要 --file <卡文件名>（或 --list 查台账）", file=sys.stderr)
        return 2
    root = Path(args.root)
    card_path = root / args.file
    if not card_path.is_file():
        print(f"错误: 卡片文件不存在: {card_path}", file=sys.stderr)
        return 2
    dbp = Path(args.db) if getattr(args, "db", None) else None
    if dbp is not None and not dbp.is_file():
        print(f"错误: 索引库不存在: {dbp}（引文类警告要查库才能取全）",
              file=sys.stderr)
        return 2
    warns = card_current_warnings(card_path, db=dbp)
    if args.warn:
        subs = tuple(args.warn)
        keys = sorted({_warn_key(w) for w in warns
                       if any(s in w for s in subs)})
    else:
        keys = sorted({_warn_key(w) for w in warns})   # 一言而决：整卡当前警告
    if not keys:
        print(f"{args.file}: 当前没有可裁决的警告（无需落账）。")
        return 0
    n = record_review(meta, args.file, keys, verdict=args.verdict,
                      note=args.note or "", reviewed_by=args.by or "")
    print(f"已落账 {n} 条裁决（{args.verdict}）：{args.file}")
    for k in keys:
        print(f"  - {k[:100]}")
    print("重跑 cards validate 即不再显示这些警告（--show-review 连同显示）。")
    return 0


def cmd_attrib(args) -> int:
    """归因层（P6-2/P6-3）：把"计数"变成"根因候选 + 应对候选"。

    三层分离（SOP A1）：本命令只出**候选**（会错），裁决只进 attribations
    台账。两个出口**不共用实现**（--json 机读全量 / --brief 人读不截断）。
    只读：harvester.db 全程 mode=ro；唯一写路径是 `attrib review` 写台账库。
    """
    from .attrib import (collect_attributions, filter_reviewed, load_rules,
                         render_brief, render_json)
    from .errstats import collect_errors_from_db
    dbp = Path(args.db)
    if not dbp.is_file():
        print(f"错误: 索引库不存在: {dbp}（先运行 harvester index）",
              file=sys.stderr)
        return 2
    errors, meta = collect_errors_from_db(dbp, since_days=args.since)
    try:
        rules = load_rules(Path(args.rules) if args.rules else None)
    except Exception as e:  # noqa: BLE001 - 规则表不合规属"响亮失败"（A3）
        print(f"错误: {e}", file=sys.stderr)
        return 2
    result = collect_attributions(errors, rules)
    meta_p = Path(args.meta) if getattr(args, "meta", None) else None
    result = filter_reviewed(result, meta_p)
    snapshot = {}
    try:
        from .dbmeta import db_fingerprint
        snapshot = {k: v for k, v in db_fingerprint(dbp).items()
                    if k in ("sessions", "messages", "steps", "errors")}
    except Exception:  # noqa: BLE001 - 快照拿不到不阻断归因产出
        snapshot = {}
    gen = time.strftime("%Y-%m-%d %H:%M:%S")
    if args.json:
        text = render_json(result, meta, db_path=str(dbp), generated_at=gen,
                           snapshot=snapshot)
        _write_or_print(text, args.json, "归因机读出口")
    if args.brief:
        text = render_brief(result, meta, db_path=str(dbp), generated_at=gen,
                            snapshot=snapshot)
        _write_or_print(text, args.brief, "归因候选清单")
    if not args.json and not args.brief:
        # 缺省 = 人读出口打到 stdout（与 --brief 同实现，但**不落盘**）
        print(render_brief(result, meta, db_path=str(dbp), generated_at=gen,
                           snapshot=snapshot), end="")
    return 0


def cmd_evidence_pack(args) -> int:
    """评审包（**兼容别名**，P10 起并入 `llm-pack`）。

    用户 2026-10-24 裁决：两条命令一套实现。这里只做三件事——把 `--kind`
    翻译成 llm-pack 的 `--faces`、把 `--top` 翻译成 `--limit`、在 stderr
    说明"已弃用"。取数/渲染/判定槽全部走 `harvester.llmpack`，**不再有
    第二套证据包代码**（漂移就是这么长出来的）。
    """
    from .llmpack import (build_pack as _lp_build, render_json as _lp_json,
                          render_md as _lp_md)
    from .attrib import load_rules
    print("提示: evidence-pack 已并入 llm-pack（本命令保留为兼容别名，"
          "等价于 llm-pack --faces <面> --limit <top>）；新脚本请直接用 llm-pack。",
          file=sys.stderr)
    dbp = Path(args.db)
    if not dbp.is_file():
        print(f"错误: 索引库不存在: {dbp}（先运行 harvester index）",
              file=sys.stderr)
        return 2
    face_of = {"errors": "errors", "attrib": "attrib",
               "skills": "skills", "chains": "chains"}
    if args.kind not in face_of:
        print(f"错误: 不支持的 kind：{args.kind}（可选：{'|'.join(face_of)}）",
              file=sys.stderr)
        return 2
    face = face_of[args.kind]
    chain_root = None
    if face == "chains":
        chain_root = (Path(args.chain_root) if args.chain_root
                      else Path(__file__).resolve().parent.parent
                      / "knowledge" / "topics")
    try:
        pack = _lp_build(dbp, faces=(face,), limit=args.top,
                         rules=load_rules(Path(args.rules) if args.rules else None),
                         meta=Path(args.meta) if args.meta else None,
                         chain_root=chain_root, since_days=args.since)
    except Exception as e:  # noqa: BLE001 - 规则表不合规等属"响亮失败"
        print(f"错误: {e}", file=sys.stderr)
        return 2
    if args.json:
        _write_or_print(_lp_json(pack), args.json, "评审包机读出口")
    md = _lp_md(pack)
    if args.out:
        _write_or_print(md, args.out, "评审包（人读）")
    if not args.out and not args.json:
        print(md, end="")
    return 0


def cmd_llm_pack(args) -> int:
    """打包（第④b步）：给 LLM 的活——评论候选、填判定槽；也是人读评审包。

    只读（库走 mode=ro）；`--validate` 只校验结构，不改任何文件。
    `--out` 出人读 md（与 `--json` 可同给：md 给人、json 给程序）。
    """
    import json as _json
    from .attrib import load_rules
    from .llmpack import build_pack, render_json, render_md, validate_pack
    if args.validate:
        p = Path(args.validate)
        if not p.is_file():
            print(f"错误: 包不存在: {p}", file=sys.stderr)
            return 2
        try:
            pack = _json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001 - 坏 JSON 也是响亮失败
            print(f"错误: 解析失败: {e}", file=sys.stderr)
            return 2
        problems = validate_pack(pack)
        if problems:
            print(f"不合格（{len(problems)} 处）：", file=sys.stderr)
            for x in problems:
                print(f"  - {x}", file=sys.stderr)
            return 1
        print(f"合格：{p}（items {len(pack.get('items') or [])} 条，"
              f"判定槽 {len((pack.get('slot') or {}).get('entries') or [])} 行）")
        return 0

    dbp = Path(args.db)
    if not dbp.is_file():
        print(f"错误: 索引库不存在: {dbp}（先运行 harvester index）",
              file=sys.stderr)
        return 2
    faces = tuple(f.strip() for f in (args.faces or "").split(",") if f.strip())
    try:
        pack = build_pack(dbp, faces=faces, limit=args.limit,
                          rules=load_rules(Path(args.rules) if args.rules else None),
                          meta=Path(args.meta) if args.meta else None,
                          since_days=args.since,
                          chain_root=(Path(args.chain_root)
                                      if getattr(args, "chain_root", None) else None))
    except Exception as e:  # noqa: BLE001 - 规则不合规/面不支持属响亮失败
        print(f"错误: {e}", file=sys.stderr)
        return 2
    md = render_md(pack)
    if args.out:
        _write_or_print(md, args.out, "评审包（人读）")
    if args.json:
        _write_or_print(render_json(pack), args.json, "LLM 包（机读）")
    if not args.json and not args.out:
        print(render_json(pack), end="")
    return 0


def _write_or_print(text: str, out: str, label: str) -> None:
    """写盘或打印。落盘统一 UTF-8 无 BOM + LF（项目纪律）。"""
    if out == "-":
        print(text, end="")
        return
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")
    print(f"{label}已写入 {p}")


def _emit_json(path: str, schema: str, data, dbp=None) -> None:
    """机读出口（G2 结构化层）：schema + generated_at + db_fingerprint + data。

    `data` 必须来自**文本路径同一个聚合函数**（同一个 dict/list 对象或它
    的直接序列化），**不许**另写一套聚合、也不许解析渲染后的文本——那是
    两套口径，数字迟早对不上。形状与 `llm-pack`/`evidence-pack` 一致。

    path == "-" → stdout（此时人读文本不占 stdout，见 `_emit_report`）；
    否则落盘 UTF-8 无 BOM + LF。
    """
    import json as _json
    fp: dict = {}
    if dbp is not None and Path(dbp).is_file():
        try:
            from .dbmeta import db_fingerprint
            fp = db_fingerprint(Path(dbp))
        except Exception:  # noqa: BLE001 - 指纹拿不到不阻断机读出口
            fp = {}
    payload = {
        "schema": schema,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "db_fingerprint": fp,
        "data": data,
    }
    _write_or_print(_json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    path, "机读出口")


def _group_rows(groups: dict) -> list[dict]:
    """`{组名: ToolStats}` 或 `{组名: {工具: ToolStats}}` → 机读行。

    两种形态都取自文本路径已用的聚合函数：`collect_model_stats` 给前者、
    `collect_source_stats` 给后者（按工具再分一层）。calls/success/error
    直接读 ToolStats，不在 JSON 侧重算库；逐工具明细由 `data.tools` 给。
    排序与文本表一致：按调用数降序。
    """
    def _stats_of(group):
        # ToolStats 是普通类（不是 dict），只有按源聚合时外面才是 dict
        return group.values() if isinstance(group, dict) else (group,)

    rows = []
    for name in sorted(groups, key=lambda g: -sum(
            st.calls for st in _stats_of(groups[g]))):
        stats = list(_stats_of(groups[name]))
        calls = sum(st.calls for st in stats)
        errs = sum(st.error for st in stats)
        rows.append({"name": name, "tools": len(stats), "calls": calls,
                     "success": sum(st.success for st in stats),
                     "error": errs,
                     "fail_rate": round(errs / calls, 4) if calls else 0.0})
    return rows


def cmd_attrib_review(args) -> int:
    """归因台账（P6-3）：抄 card_review 语义——人裁过的归因不再进候选。

    --list 列台账；--pattern + --rule 一言而决（verdict 三态）。
    **只写台账库**（meta），绝不碰 harvester.db。
    """
    from .attrib import list_verdicts, record_verdict
    meta = Path(args.meta)
    if args.list:
        rows = list_verdicts(meta)
        if not rows:
            print("台账为空（还没有任何归因裁决）。")
            return 0
        for r in rows:
            print(f"[{r['verdict']}] {r['rule_id']} ({r['class']})  "
                  f"{r['pattern_key']}"
                  + (f"  note={r['note']}" if r.get("note") else ""))
        print(f"共 {len(rows)} 条裁决。")
        return 0
    if not args.pattern or not args.rule:
        print("错误: 需要 --pattern <归一模式> 与 --rule <规则id>"
              "（或 --list 查台账）", file=sys.stderr)
        return 2
    n = record_verdict(meta, args.pattern, args.rule,
                       cls=args.cls or "unclassified", verdict=args.verdict,
                       note=args.note or "", reviewed_by=args.by or "")
    if not n:
        print("未落账（--pattern/--rule 为空）。")
        return 0
    print(f"已落账 {n} 条裁决（{args.verdict}）：{args.rule} ← {args.pattern}")
    print("重跑 attrib 即不再把该候选列进候选区"
          "（事实计数不变，落在「已裁决」段）。")
    return 0


def cmd_topic(args) -> int:
    """主题注册表（T1 MVP，v0.22）：独立 meta 库 topics_meta.db。"""
    from .topics import (add_members, delete_topic, list_topics, merge_topics,
                         register_topic, remove_member, rename_topic,
                         show_topic)
    meta = Path(args.meta)
    if args.cmd == "register":
        kws = [k.strip() for k in (args.keywords or "").split(",")
               if k.strip()]
        tid = register_topic(meta, args.name, keywords=kws)
        print(f"[topic] 已注册: {tid} {args.name}")
        return 0
    if args.cmd == "add":
        sids = [s.strip() for s in (args.sids or "").split(",") if s.strip()]
        n = add_members(meta, args.id_, sids,
                        evidence=args.evidence or "")
        print(f"[topic] {args.id_} 新增成员 {n} 个（重复幂等跳过）")
        return 0
    if args.cmd == "remove":
        remove_member(meta, args.id_, args.sid)
        print(f"[topic] {args.id_} 移除成员 {args.sid}")
        return 0
    if args.cmd == "merge":
        srcs = [s.strip() for s in (args.from_ or "").split(",") if s.strip()]
        if not args.id_ or not srcs:
            print("错误: merge 需要 --id <目标> 与 --from <源,源…>",
                  file=sys.stderr)
            return 2
        r = merge_topics(meta, args.id_, srcs,
                         delete_sources=not args.keep_sources)
        print(f"[topic] 合并入 {r['target']}：新增成员 {r['added']}、"
              f"既有 {r['kept']}；关键词 {('、'.join(r['keywords'])) or '（无）'}")
        if r["deleted"]:
            print(f"[topic] 已删除源主题: {'、'.join(r['deleted'])}")
        else:
            print(f"[topic] 源主题保留: {'、'.join(r['sources'])}")
        return 0
    if args.cmd == "rename":
        if not args.id_ or not args.name:
            print("错误: rename 需要 --id <主题> 与 --name <新名>",
                  file=sys.stderr)
            return 2
        r = rename_topic(meta, args.id_, args.name)
        print(f"[topic] {r['id']} 改名: {r['old_name']} → {r['new_name']}")
        return 0
    if args.cmd == "delete":
        if not args.id_:
            print("错误: delete 需要 --id <主题>", file=sys.stderr)
            return 2
        snap = delete_topic(meta, args.id_)
        print(f"[topic] 已删除 {snap['id']}「{snap['name']}」"
              f"（成员 {len(snap['members'])} 个随行留痕；"
              f"快照请用 --out 或脚本留档）")
        if args.out:
            import json as _json
            outp = Path(args.out)
            outp.parent.mkdir(parents=True, exist_ok=True)
            outp.write_text(_json.dumps(snap, ensure_ascii=False, indent=2),
                            encoding="utf-8", newline="\n")
            print(f"[topic] 快照已写入: {outp}")
        return 0
    if args.cmd == "keywords":
        # 关键词定稿（覆盖式）：合并会把各源的聚类碎片关键词并进来，
        # 去噪属语义判断 → 由人给最终列表，工具只写。
        from .topics import set_keywords
        if not args.id_ or args.keywords is None:
            print("错误: keywords 需要 --id <主题> 与 --keywords \"k1,k2\""
                  "（空串 = 清空）", file=sys.stderr)
            return 2
        kws = [k.strip() for k in args.keywords.split(",") if k.strip()]
        r = set_keywords(meta, args.id_, kws)
        print(f"[topic] {r['id']} 关键词：{len(r['old'])} 个 → "
              f"{len(r['new'])} 个（{'、'.join(r['new']) or '（清空）'}）")
        return 0
    if args.cmd == "dedupe":
        # 会话级去重（H40 口径）：每写一条 chain 都要跑的准备工作
        from .topicprep import dedupe_topic, render_dedupe
        if not args.id_:
            print("错误: dedupe 需要 --id <主题>", file=sys.stderr)
            return 2
        r = dedupe_topic(meta, Path(args.db), args.id_)
        text = render_dedupe(r)
        if args.out:
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8", newline="\n")
            print(f"[topic] 去重报告已写入: {out}（成员 {r['members']} → "
                  f"代表 {r['reps']}，重复 {r['duplicates']}）", file=sys.stderr)
        else:
            print(text)
        return 0
    if args.cmd == "turns":
        # 回合索引 / raw 全文：chain 锚点与逐字引文的唯一来源
        from .topicprep import render_turns, turns_of
        if not args.id_:
            print("错误: turns 需要 --id <主题>", file=sys.stderr)
            return 2
        t = turns_of(meta, Path(args.db), args.id_, cap=args.cap,
                     full=args.full)
        text = render_turns(t)
        if args.out:
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8", newline="\n")
            print(f"[topic] 回合索引已写入: {out}（成员 {t['members']}，"
                  f"{'raw 全文' if t['full'] else '预览'}）", file=sys.stderr)
        else:
            print(text)
        return 0
    if args.cmd == "export":
        # 主题结构化导出（v0.29）：给 Agent/机器读的 topic.json
        from .consolidate import noise_sids
        from .topicexport import render_topic_json, topic_bundle
        dbp = Path(args.db)
        if not args.id_:
            print("错误: export 需要 --id <主题>", file=sys.stderr)
            return 2
        root = Path(args.chain_root) if args.chain_root else None
        bundle = topic_bundle(meta, dbp, args.id_, chain_root=root,
                              noise_sids=noise_sids(meta))
        text = render_topic_json(bundle)
        if args.out:
            outp = Path(args.out)
            outp.parent.mkdir(parents=True, exist_ok=True)
            outp.write_text(text, encoding="utf-8", newline="\n")
            print(f"[topic] 结构化导出已写入: {outp}（{len(text)} 字符，"
                  f"成员 {bundle['topic']['members_count']}、"
                  f"链 {len(bundle['chains'])}）", file=sys.stderr)
        else:
            print(text)
        return 0
    if args.cmd == "md":
        # 主题人读那一半（v0.33）：一页速览（是什么/跨多久/关键转折/结论与未决）
        from .consolidate import noise_sids
        from .topicexport import render_topic_md, topic_bundle
        if not args.id_:
            print("错误: md 需要 --id <主题>", file=sys.stderr)
            return 2
        root = Path(args.chain_root) if args.chain_root else None
        bundle = topic_bundle(meta, Path(args.db), args.id_, chain_root=root,
                              noise_sids=noise_sids(meta))
        text = render_topic_md(bundle, chain_root=root)
        if args.out:
            outp = Path(args.out)
            outp.parent.mkdir(parents=True, exist_ok=True)
            outp.write_text(text, encoding="utf-8", newline="\n")
            print(f"[topic] 人读速览已写入: {outp}（{len(text)} 字符，"
                  f"链 {len(bundle['chains'])}）", file=sys.stderr)
        else:
            print(text)
        return 0
    if args.cmd == "list":
        for t in list_topics(meta):
            print(f"- {t['id']}  {t['name']}  成员 {t['members']}  "
                  f"关键词 {('、'.join(t['keywords'])) or '（无）'}")
        return 0
    if args.cmd == "show":
        d = show_topic(meta, args.id_)
        print(f"id: {d['id']}\nname: {d['name']}\n"
              f"keywords: {d['keywords']}\ncreated: {d['created']}\n"
              f"members ({len(d['members'])}):")
        for m in d["members"]:
            ev = f"  # {m['evidence']}" if m.get("evidence") else ""
            print(f"- {m['sid']}{ev}")
        return 0
    if args.cmd in ("chain", "pack"):
        dbp = Path(args.db)
        if not dbp.is_file():
            print(f"错误: 索引库不存在: {dbp}", file=sys.stderr)
            return 2
        from .topics import build_topic_packet, render_timeline, timeline
        level = getattr(args, "level", "title") or "title"
        max_chars = getattr(args, "max_chars", None)
        adb = (Path(args.artifacts_meta)
               if getattr(args, "artifacts_meta", None) else None)
        if args.cmd == "chain":
            d = timeline(meta, args.id_, db=dbp, level=level,
                         sid=args.sid, artifacts_db=adb,
                         max_chars=max_chars)
            text = render_timeline(d)
            if args.out:
                out = Path(args.out)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(text, encoding="utf-8", newline="\n")
                print(f"[topic] 链产物已写入: {out}", file=sys.stderr)
            else:
                print(text)
            n_rows = len(d.get("rows") or [])
            print(f"[topic] {level} 档 {n_rows} 条"
                  f"｜未命中 {len(d.get('missing') or [])}",
                  file=sys.stderr)
            return 0
        # pack：主题蒸馏包（复用 draft 分层预算）
        packet = build_topic_packet(dbp, meta, args.id_, level=level,
                                    sid=args.sid, artifacts_db=adb,
                                    max_chars=max_chars)
        if args.out:
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(packet, encoding="utf-8", newline="\n")
            print(f"[topic] 蒸馏包已写入: {out}（{level} 档，"
                  f"{len(packet)} 字符）", file=sys.stderr)
        else:
            print(packet)
        return 0
    print("错误: 未知子命令", file=sys.stderr)
    return 2


def cmd_chain_audit(args) -> int:
    """chain 长文审计（v0.31）：引文逐字门 + 锚点语义门 + 证据覆盖（v0.45）。

    与 `chain-validate` 的分工：校验器管**结构**（sid 在成员内、turn 越界），
    本命令管**内容保真与证据覆盖**（引文能否逐字找到、note 与原文有没有交集、
    有多少成员只有标题级证据）。
    """
    from .chainaudit import (audit_chain, record_audit_review,
                             render_audit)
    meta = (Path(args.reviews_meta)
            if getattr(args, "reviews_meta", None) else None)
    if getattr(args, "review_list", False):
        from .chainaudit import list_audit_reviews
        rows = list_audit_reviews(meta, Path(args.file).name)
        if not rows:
            print("[chain-audit] 该 chain 暂无复核裁决。", file=sys.stderr)
        for row in rows:
            print(f"{row['node_key']}  [{row['verdict']}]  "
                  + (row.get("note") or ""))
        return 0
    if getattr(args, "accept", None):
        if meta is None:
            print("错误: --accept 需要 --reviews-meta", file=sys.stderr)
            return 2
        from .chainaudit import _node_key
        keys = []
        for spec in args.accept:
            sid, _sep, turn = spec.rpartition("#")
            if not sid or not turn.isdigit():
                print(f"错误: --accept 格式应为 sid#turn（收到 {spec!r}）",
                      file=sys.stderr)
                return 2
            keys.append(_node_key(sid, int(turn)))
        n = record_audit_review(meta, Path(args.file).name, keys,
                                verdict="accepted",
                                note=getattr(args, "accept_note", "") or "")
        print(f"[chain-audit] 已落账 {n} 个节点裁决（accepted）。"
              "重跑本命令即不再显示这些告警。", file=sys.stderr)
    try:
        r = audit_chain(Path(args.file), Path(args.db),
                        quotes=not args.no_quotes,
                        anchors=not args.no_anchors,
                        coverage=not args.no_coverage,
                        quotes_ascii=args.quotes_ascii,
                        review_meta=meta,
                        show_reviewed=bool(getattr(args, "show_review", False)))
    except RuntimeError as exc:            # 缺 PyYAML 等环境错误：fail loud
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    text = render_audit(r, list_limit=args.list_limit)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8", newline="\n")
        print(f"[chain-audit] 报告已写入: {out}", file=sys.stderr)
    else:
        # `--json -` 时 stdout 归机读出口独占（同 _emit_report 的纪律）
        print(text, file=(sys.stderr if args.json == "-" else sys.stdout))
    if getattr(args, "json", None):
        _emit_json(args.json, "chain-audit/1", r, dbp=Path(args.db))
    bits = []
    if "quotes" in r:
        q = r["quotes"]
        bits.append(f"引文未命中 {len(q['misses'])}"
                    + (f"（**空转**：0 条「」，另 "
                       f"{q['ascii_total']} 处 ASCII 引用未核）"
                       if q.get("vacuous") and not q.get("ascii_checked")
                       else ""))
        if q.get("ascii_checked"):
            bits.append(f"ASCII 引用未命中 {len(q['ascii_misses'])}")
    if "anchors" in r:
        bits.append(f"疑似错配 {len(r['anchors']['suspicious'])}")
    if "coverage" in r:
        c = r["coverage"]
        bits.append(f"成员锚点覆盖 {c['anchored_members']}/{c['members']}"
                    f"（真缺口 {len(c['uncovered_reps'])}）")
    # 退出码口径：引文门是**硬门**（未命中即不合格）；锚点门是启发式，
    # 有已知误报类别（note 写跨会话关系），故只在 --strict 时才让它影响退出码
    hard_fail = ("quotes" in r and not r["quotes"]["ok"])
    soft = ("anchors" in r and not r["anchors"]["ok"])
    if hard_fail or (soft and args.strict):
        print("[chain-audit] 需修｜" + "、".join(bits), file=sys.stderr)
        return 1
    print("[chain-audit] 通过｜" + "、".join(bits)
          + ("（锚点告警需人工复核，未按 --strict 判失败）" if soft else ""),
          file=sys.stderr)
    return 0


def cmd_chain_validate(args) -> int:
    """topic-chain 长文校验（T3，v0.22）：独立校验器，非 §8 卡片校验。"""
    import json as _json

    from .topicchain import validate_chain
    r = validate_chain(args.file, args.meta, args.db)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(_json.dumps(r, ensure_ascii=False, indent=1) + "\n",
                       encoding="utf-8", newline="\n")
        print(f"[chain-validate] 报告已写入: {out}", file=sys.stderr)
    for e in r["errors"]:
        print(f"ERROR: {e}")
    for w in r["warnings"]:
        print(f"WARN: {w}")
    st = r["stats"]
    verdict = "通过" if r["ok"] else "未通过"
    print(f"[chain-validate] {verdict}｜members {st.get('members', 0)}"
          f" / stages {st.get('stages', 0)} / nodes {st.get('nodes', 0)}"
          f"｜errors {len(r['errors'])} warnings {len(r['warnings'])}")
    return 0 if r["ok"] else 1


def cmd_chain_build(args) -> int:
    """链创作包（v0.82）：产出**素材+格式契约**，供 Agent 写 chain 长文。

    它是"生成链"的起点，但**不代笔**：链要求"思想/技法/观念的演变和转折"，
    聚合器算不出来（用户 2026-10-24 裁决：命令出包 + Agent 写 + 命令校验落盘）。
    确定性：不取时钟、不用随机——同库同参数两次输出逐字节一致。
    """
    from .chainbuild import build, render
    try:
        d = build(Path(args.db), Path(args.meta), args.id_,
                  max_chars=args.max_chars)
    except (ValueError, RuntimeError) as exc:
        print(f"[chain-build] 失败: {exc}", file=sys.stderr)
        return 2
    text = render(d)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8", newline="\n")
        print(f"[chain-build] 创作包已写入: {out}（{len(text)} 字符）",
              file=sys.stderr)
    else:
        print(text)
    c = d["counts"]
    print(f"[chain-build] {d['topic']['id']}「{d['topic']['name']}」"
          f"｜成员 {c['members']}｜时间桶 {c['buckets']}"
          f"｜锚点候选 {c['anchor_candidates']} 给出 {c['anchor_shown']}"
          f"{'（预算截断）' if d['budget']['truncated'] else ''}",
          file=sys.stderr)
    print("[chain-build] 下一步：把这份包交给 Agent 写 chain-*.md，"
          "再用 chain-validate / chain-audit / chain-publish 把关",
          file=sys.stderr)
    if getattr(args, "json", None):
        _emit_json(args.json, "chain-build/1", d, dbp=Path(args.db))
    return 0


def cmd_chain_publish(args) -> int:
    """链发布（v0.82）：校验通过才落进知识正本。缺省 dry-run。

    两道既有门都在这里合流：`chain-validate`（结构 + 锚点可回溯）与
    `chain-audit`（引文逐字门）。任一门不过就不许落盘——这是"不许机器
    代笔的未校验文本污染正本"的执行点。
    """
    from .chainbuild import publish
    draft = (Path(args.draft) if getattr(args, "draft", None)
             else Path("exports") / "chains" / ("%s.md" % args.id_))
    try:
        r = publish(draft, Path(args.chain_root), Path(args.meta),
                    Path(args.db), name=None, apply=args.apply,
                    force=args.force, allow_audit=args.allow_audit)
    except (ValueError, RuntimeError) as exc:
        print(f"[chain-publish] 失败: {exc}", file=sys.stderr)
        return 2
    v, a = r["validate"], r["audit"]
    print(f"[chain-publish] validate {'通过' if v['ok'] else '未通过'}"
          f"（stages {(v.get('stats') or {}).get('stages', 0)}"
          f" / nodes {(v.get('stats') or {}).get('nodes', 0)}）"
          f"｜audit {'通过' if a['ok'] else '未通过'}"
          f"（引文未命中 {a['quotes_unmatched']}｜疑似错配 "
          f"{a['anchors_suspicious']}）")
    for e in (v.get("errors") or []):
        print(f"  ERROR: {e}")
    for w in (r.get("warnings") or []) + (v.get("warnings") or []):
        print(f"  WARN: {w}")
    if r["blockers"]:
        for b in r["blockers"]:
            print(f"  BLOCK: {b}")
        print("[chain-publish] 未发布（上面的 BLOCK 全清掉再来）",
              file=sys.stderr)
        return 1
    if r["applied"]:
        print(f"[chain-publish] 已写入知识正本: {r['target']}")
    else:
        print(f"[chain-publish] 预演通过——会写到: {r['target']}")
        print("[chain-publish] 确认无误后加 --apply 真写",
              file=sys.stderr)
    return 0


def cmd_artifacts(args) -> int:
    """产物提取（T0-②，v0.22）：独立 meta 库 artifacts_meta.db。

    P6-8：两个子命令都先印一行**覆盖度自陈**（仅 dsh 源 / 已登记 N 总会话 M），
    `extract` 在提取**之后**印，这样数字反映的是"跑完这条命令"的状态。
    """
    from .artifacts import coverage, ensure_artifacts_db, extract_session, \
        show_artifacts
    meta = ensure_artifacts_db(Path(args.meta))
    # 两个子命令都先印**执行前**的覆盖度：这是最容易滑过去的诚实缺口——
    # `show` 只印"这个 sid 有没有记录"，读者会以为产物面覆盖全库。
    print(coverage(Path(args.db), meta)["line"])
    if args.cmd == "extract":
        dbp = Path(args.db)
        if not dbp.is_file():
            print(f"错误: 索引库不存在: {dbp}", file=sys.stderr)
            return 2
        try:
            n = extract_session(meta, dbp, args.sid)
        except NotImplementedError as e:
            print(f"错误: {e}", file=sys.stderr)
            return 2
        except KeyError as e:
            print(f"错误: {e}", file=sys.stderr)
            return 2
        print(f"[artifacts] {args.sid} 提取 {n} 条 Write/Edit 记录")
        return 0
    # show：版本对照预览
    rows = show_artifacts(meta, args.sid, limit=args.limit)
    if not rows:
        print(f"（{args.sid} 无产物提取记录；先跑 "
              f"`python -m harvester artifacts extract --sid {args.sid}`）")
        return 0
    for r in rows:
        old = (r["old_text"] or "（创建）")[:120]
        new = (r["new_text"] or "")[:120]
        print(f"#{r['seq']} [{r['ts'] or '-'}] {r['tool']} {r['file_path']}")
        print(f"  旧: {old}")
        print(f"  新: {new}")
    return 0


def cmd_triage(args) -> int:
    """蒸馏队列（T1 确定性初筛）：机器排队，人判断，draft 蒸馏包喂 Agent 草稿（T2）。"""
    from .triage import collect_triage, render_triage
    dbp = Path(args.db)
    if not dbp.is_file():
        print(f"错误: 索引库不存在: {dbp}（先运行 harvester index/sync）",
              file=sys.stderr)
        return 2
    r = collect_triage(dbp, since_days=args.since or None,
                       cards_root=Path(args.cards_root)
                       if args.cards_root else None,
                       min_count=args.min_count, top_n=args.top)
    _emit_report(render_triage(r), args)
    n = len(r["new_patterns"]) + len(r["old_patterns"]) + len(r["skills"])
    print(f"\n[ntriage] 候选 {n} 条（新 pattern "
          f"{len(r['new_patterns'])} / 旧坑重现 {len(r['old_patterns'])}"
          f" / skill {len(r['skills'])}），高信号会话 "
          f"{len(r['hot_sessions'])} 条", file=sys.stderr)
    return 0


def cmd_draft(args) -> int:
    """蒸馏包（T2 确定性一半）：会话原文+卡片规范+指令 → 自包含 md。"""
    from .drafting import build_distill_packet, write_packet
    dbp = Path(args.db)
    if not dbp.is_file():
        print(f"错误: 索引库不存在: {dbp}（先运行 harvester index/sync）",
              file=sys.stderr)
        return 2
    try:
        packet = build_distill_packet(dbp, args.sid, ctype=args.type,
                                      max_chars=args.max_chars)
    except KeyError as e:
        print(f"错误: {e.args[0]}", file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2
    if args.out:
        path = write_packet(Path(args.out), packet)
        print(f"[ndraft] 蒸馏包已写入: {path}", file=sys.stderr)
        print("下一步: 把该文件交给任意 Agent 起草卡片 → 草稿落 "
              "cards_pending/ → cards validate --root cards_pending",
              file=sys.stderr)
    else:
        sys.stdout.write(packet)
    return 0


def _emit_report(report: str, args) -> None:
    """报告统一出口：库快照产物头（v0.19）+ 库龄（v0.46 P0-2）+ 落盘/打印。
    产物头仅在 args 带 --db 且库文件存在时写入（report-traces 等
    非库报告自动跳过）；只加行不改报告正文。

    库龄：语料比库新 ⇒ 头部带 STALE 标记。语料 mtime 靠各源的
    `source_mtimes()` 只 stat 取得（不解压）；拿不到就**不判定**
    （stale=None，报告头不加噪音——但 JSON/API 里 null 仍与 false 可区分）。
    """
    dbp = getattr(args, "db", None)
    if dbp and Path(dbp).is_file():
        from .dbmeta import db_fingerprint, fingerprint_line
        from .outline import corpus_watermark
        corpus_mtime = None
        try:
            src = getattr(args, "sources", None) or "sources.json"
            corpus_mtime = corpus_watermark(load_sources(src)).get("mtime")
        except Exception:  # noqa: BLE001 - 库龄判定失败不阻断报告产出
            corpus_mtime = None
        try:
            report = (f"> {fingerprint_line(db_fingerprint(Path(dbp), corpus_mtime=corpus_mtime))}\n\n"
                      + report)
        except sqlite3.Error:
            pass  # 指纹失败不阻断报告产出
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8", newline="\n")
        # `--json -` 时 stdout 归机读出口独占（否则 JSON 后面跟一行人读提示，
        # 整段就无法 json.loads 了）；提示改走 stderr，人仍看得见。
        msg = sys.stderr if getattr(args, "json", None) == "-" else sys.stdout
        print(f"报告已写入 {out}", file=msg)
    elif getattr(args, "json", None) != "-":
        print(report, end="")


def _default_report_path(kind: str, fmt: str) -> Path:
    """`export-analysis --save` 的落点：`<仓>/docs/reports/<kind>-YYYY-MM-DD.<fmt>`。

    这个目录**必须**与 `apiserve.default_reports_dir()` 是同一个——页面
    「报告索引」列的就是那里；写成两个表达式就会悄悄漂开（有门对账）。
    """
    stamp = time.strftime("%Y-%m-%d")
    return (Path(__file__).resolve().parent.parent / "docs" / "reports"
            / f"{kind}-{stamp}.{fmt}")


def cmd_export_analysis(args) -> int:
    """export-analysis 统一导出器（P1-4）：五类分析产物，md/JSON 同源。

    P10 起加 `--save`：不指定 `--out` 时把产物**落到标准报告目录**
    （`<仓>/docs/reports/<kind>-YYYY-MM-DD.<md|json>`）并打印路径——页面
    「导出到服务器」与「报告索引」看的是同一份文件，不用再从浏览器下载一份
    到 Downloads 里当孤儿。
    """
    from .export_analysis import build_analysis, to_json
    dbp = Path(args.db)
    if not dbp.is_file():
        print(f"错误: 索引库不存在: {dbp}（先运行 harvester index）",
              file=sys.stderr)
        return 2
    sids = [s for s in (args.sids or "").split(",") if s] or None
    try:
        obj, md = build_analysis(
            dbp, args.kind, days=args.days, sids=sids,
            min_count=args.min_count, top=args.top,
            cards_root=Path(args.cards_root) if args.cards_root else None)
    except (ValueError, KeyError) as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2
    text = to_json(obj) if args.format == "json" else md
    out_arg = args.out
    if args.save and not out_arg:
        out_arg = str(_default_report_path(args.kind, args.format))
    if out_arg:
        out = Path(out_arg)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8", newline="\n")
        print(f"已写入 {out}（kind={args.kind} format={args.format}）")
    else:
        print(text, end="")
    return 0


def main(argv=None) -> int:
    _force_utf8_streams()
    args = build_parser().parse_args(argv)
    return args.func(args)


def _force_utf8_streams() -> None:
    """Windows 原版解释器默认 cp936，report 输出含 emoji/特殊 Unicode 标题重定向
    到文件/管道时会 UnicodeEncodeError——统一 UTF-8（DSH 等已全局 UTF-8 则无感）。
    v0.75 补 stdin：mcp-serve 逐行读 sys.stdin，GBK 环境下中文 JSON-RPC 请求
    会 UnicodeDecodeError 直接崩（换机即触发，用户裁决可迁移红线）。"""
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:  # 非 TextIOWrapper（如测试桩）
            pass


def build_parser() -> argparse.ArgumentParser:
    """构造完整子命令解析器（**与 `main` 分离**，供测试直接检查参数）。

    为什么抽出来（v0.47）：参数级别的东西（默认值、`--no-*` 开关）此前只能靠
    **真跑一条命令**来验证，于是"参数存在但语义反了"没人测得到
    （`test_v44_doc_cmds.py` 的漂移门只核对**子命令名**）。抽出来后测试可直接
    `build_parser().parse_args(["sync", ...])` 断言默认值与开关——不启子进程、
    不碰真库。纯重构，不改任何命令行为（UTF-8 流适配仍在 `main` 里做，
    不藏进本函数，免得它带上流副作用）。
    """
    p = argparse.ArgumentParser(prog="harvester", description="AI 会话历史提取与分类导出工具")
    sub = p.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("probe", help="固化探测：扫描本机数据源签名并生成 sources.json")
    pp.add_argument("--out", default="sources.json")
    pp.add_argument("-v", "--verbose", action="store_true")
    pp.set_defaults(func=cmd_probe)

    ps = sub.add_parser("scan", help="扫描数据源并生成纲要（纲要=给全部会话编好号的目录清单）")
    ps.add_argument("--sources", default="sources.json", help="probe 产出的路径配置")
    ps.add_argument("--out", default="outline", help="纲要输出目录")
    ps.add_argument("-v", "--verbose", action="store_true")
    ps.set_defaults(func=cmd_scan)

    pe = sub.add_parser("export", help="按纲要序号导出会话（序号见 scan 生成的纲要）")
    pe.add_argument("--select", help='序号表达式，如 "3,5-9"')
    pe.add_argument("--all", action="store_true", help="导出全部可选会话")
    pe.add_argument("--sources", default="sources.json", help="probe 产出的路径配置")
    pe.add_argument("--out", default="exports", help="导出根目录")
    pe.add_argument("--with-notes", action=argparse.BooleanOptionalAction,
                    default=True,
                    help="包含工具调用/思考等非对话条目（默认开；--no-with-notes 关闭）")
    pe.add_argument("-v", "--verbose", action="store_true")
    pe.set_defaults(func=cmd_export)

    pa = sub.add_parser("adapters", help="显示数据源探测状态")
    pa.add_argument("--sources", default="sources.json")
    pa.set_defaults(func=cmd_adapters)

    pw = sub.add_parser("weblogin", help="网页 Chat 登录态三级流程")
    pwsub = pw.add_subparsers(dest="action", required=True)
    pwsub.add_parser("check", help="探测各产品浏览器登录态").set_defaults(func=cmd_weblogin)
    pc = pwsub.add_parser("init-config", help="生成账号密码配置模板")
    pc.add_argument("--out", default="webchat.accounts.json")
    pc.set_defaults(func=cmd_weblogin)
    pp2 = pwsub.add_parser("prepare", help="打开自动化浏览器完成登录（需 playwright）")
    pp2.add_argument("product", choices=[p.id for p in PRODUCTS])
    pp2.set_defaults(func=cmd_weblogin)

    pg = sub.add_parser("aggregate", help="聚合会话为语料（语料=整理成篇的原始文本材料）")
    pg.add_argument("--from", dest="from_dir", help="export 产物目录（读 *.json）")
    pg.add_argument("--all", action="store_true", help="直接从全部数据源实时聚合")
    pg.add_argument("--sources", default="sources.json")
    pg.add_argument("--out", default="corpus.md")
    pg.add_argument("--with-notes", action=argparse.BooleanOptionalAction,
                    default=True,
                    help="包含工具调用/思考等非对话条目（默认开；--no-with-notes 关闭）")
    pg.set_defaults(func=cmd_aggregate)
    psy = sub.add_parser("sync", help="一键同步：收件箱收割（自动捡起官方导出包）-> 导出 -> 重建索引（幂等：重复跑结果不变）")
    psy.add_argument("--root", default=".", help="项目根目录（其余相对路径的基准）")
    psy.add_argument("--sources", default="sources.json", help="数据源声明（相对 root）")
    psy.add_argument("--db", default="harvester.db", help="索引库路径（相对 root）")
    psy.add_argument("--inbox", default="inbox", help="收件箱目录（相对 root）")
    psy.add_argument("--exports", default="exports", help="导出目录（相对 root）")
    psy.add_argument("--no-notes", action="store_true",
                     help="导出时排除 note 角色（默认包含——工具步骤藏在 "
                          "note 消息里，排除会导致 steps 表为空、分析失效）")
    psy.add_argument("--keywords-meta", dest="keywords_meta",
                     default="keywords_meta.db",
                     help="keywords meta 库路径（相对 root）；sync 收尾会回收它"
                          "（只留最近 N 次 run，见 --keywords-keep-runs）")
    psy.add_argument("--keywords-keep-runs", dest="keywords_keep_runs", type=int,
                     default=1, metavar="N",
                     help="sync 收尾保留的 keywords run 数（默认 1：读路径只读最新"
                          "一次，留多个只是把体积乘 N）")
    psy.add_argument("--no-prune-keywords", dest="no_prune_keywords",
                     action="store_true",
                     help="关闭 sync 收尾的 keywords GC（默认开：keyword_stats 是"
                          "累积表，靠人记得手动跑 keywords-gc = 库会一直涨）")
    psy.add_argument("--suppress-meta", dest="suppress_meta",
                     default="topics_meta.db",
                     help="抑制表所在 meta 库（相对 root，默认 topics_meta.db）；"
                          "sync 按它拦下被抑制的会话（导出拦 + 清旧副本 + 索引兜底）")
    psy.add_argument("--no-suppress", dest="no_suppress", action="store_true",
                     help="关闭抑制拦截（默认开：抑制是「此后不许在」的持续声明，"
                          "不拦住的话下次 sync 就会把它建回来）")
    psy.add_argument("-v", "--verbose", action="store_true")
    psy.set_defaults(func=cmd_sync)

    pu = sub.add_parser("update",
                        help="增量同步：收件箱收割（自动捡起官方导出包）-> 只导出新/变更会话 -> 重建索引")
    pu.add_argument("--root", default=".", help="项目根目录（其余相对路径的基准）")
    pu.add_argument("--sources", default="sources.json", help="数据源声明（相对 root）")
    pu.add_argument("--db", default="harvester.db", help="索引库路径（相对 root）")
    pu.add_argument("--inbox", default="inbox", help="收件箱目录（相对 root）")
    pu.add_argument("--exports", default="exports", help="导出目录（相对 root）")
    pu.add_argument("--no-notes", action="store_true",
                    help="导出时排除 note 角色（默认包含，理由同 sync）")
    pu.add_argument("--suppress-meta", dest="suppress_meta",
                    default="topics_meta.db",
                    help="抑制表所在 meta 库（相对 root，默认 topics_meta.db）")
    pu.add_argument("--no-suppress", dest="no_suppress", action="store_true",
                    help="关闭抑制拦截（默认开，理由同 sync）")
    pu.add_argument("-v", "--verbose", action="store_true")
    pu.set_defaults(func=cmd_update)

    prt = sub.add_parser("report-tools",
                         help="工具调用/失败率统计（steps 表或 tool note 汇总）")
    prt.add_argument("--sources", default="sources.json")
    prt.add_argument("--db", help="索引库路径；steps 表有数据时用结构化口径（直接数步骤表的数据；"
                                 "口径=统计的标准；含重试/放弃率）")
    prt.add_argument("--since", type=float, default=None, metavar="DAYS",
                     help="只统计最近 N 天（按步骤时间戳粗算；近似口径=不如逐条精确）")
    prt.add_argument("--out", help="报告输出路径（缺省打印到 stdout）")
    prt.add_argument("--json", metavar="PATH", default=None,
                     help="机读出口（report-tools/1）：schema + db_fingerprint + "
                          "data（与文本同一个聚合函数）；'-'=stdout，缺省仍出文本")
    prt.add_argument("-v", "--verbose", action="store_true")
    prt.set_defaults(func=cmd_report_tools)

    pre = sub.add_parser("report-errors",
                         help="错误三分类报告（env/tool_interface/context "
                              "+ 开场/中途/收尾分桶，steps 表）")
    pre.add_argument("--db", default="harvester.db", help="索引库路径")
    pre.add_argument("--since", type=float, default=None, metavar="DAYS",
                     help="只统计最近 N 天")
    pre.add_argument("--out", help="报告输出路径（缺省打印到 stdout）")
    pre.add_argument("--json", metavar="PATH", default=None,
                     help="机读出口（report-errors/1）：schema + db_fingerprint"
                          " + data（与文本同一个聚合函数）；'-'=stdout，"
                          "缺省仍出文本")
    pre.add_argument("--rules", default=None,
                     help="归因规则表（缺省=随包的 "
                          "harvester/data/attrib-rules.yaml）")
    pre.add_argument("--no-attrib", action="store_true",
                     help="不加载归因层（只要事实层三分类；报告头会明说）")
    pre.set_defaults(func=cmd_report_errors)

    pat = sub.add_parser("attrib",
                         help="归因层（P6）：把错误计数变成根因候选 + 应对候选"
                              "（候选会错，裁决进 attribations 台账）")
    pat.add_argument("--db", default="harvester.db", help="索引库路径（只读）")
    pat.add_argument("--rules", default=None,
                     help="归因规则表（缺省=随包的 "
                          "harvester/data/attrib-rules.yaml）")
    pat.add_argument("--json", metavar="PATH",
                     help="机读出口：全量 + schema，可被程序消费（'-'=stdout）")
    pat.add_argument("--brief", metavar="PATH",
                     help="人读出口：一行一条、**不截断**（'-'=stdout）")
    pat.add_argument("--since", type=float, default=None, metavar="DAYS",
                     help="只统计最近 N 天")
    pat.add_argument("--meta", default="attrib_meta.db",
                     help="归因台账 meta 库（默认 attrib_meta.db；库/表不存在"
                          "=零感知。人已裁决的候选不再列出）")
    pat.set_defaults(func=cmd_attrib)

    patr = sub.add_parser("attrib-review",
                          help="归因台账（P6-3）：人裁过的归因不再进候选"
                               "（抄 card_review 语义）")
    patr.add_argument("--list", action="store_true",
                      help="列出台账明细")
    patr.add_argument("--pattern", help="归一模式（attrib 输出里的「模式：」行）")
    patr.add_argument("--rule", help="归因规则 id（如 AR-001）")
    patr.add_argument("--class", dest="cls", default=None,
                      help="错误分类（写进台账；缺省 unclassified）")
    patr.add_argument("--verdict", default="accepted",
                      choices=["accepted", "rejected", "revise"],
                      help="accepted=接受该归因；rejected=驳回（归因错了）；"
                           "revise=方向对但 why/fix 需改（note 写怎么改）")
    patr.add_argument("--note", help="裁决说明（写进台账）")
    patr.add_argument("--by", help="谁裁决的（写进台账；缺省不猜身份）")
    patr.add_argument("--meta", default="attrib_meta.db",
                      help="归因台账 meta 库（唯一写路径；不碰 harvester.db）")
    patr.set_defaults(func=cmd_attrib_review)

    pep = sub.add_parser("evidence-pack",
                         help="评审包（**已并入 llm-pack，保留兼容别名**）："
                              "判据头 + 证据体 + 判定槽")
    pep.add_argument("--kind", default="errors",
                     choices=["errors", "skills", "chains", "attrib"],
                     help="包类型（errors/attrib 含归因候选=R3；"
                          "skills/chains 无根因=R1）")
    pep.add_argument("--db", default="harvester.db", help="索引库路径（只读）")
    pep.add_argument("--since", type=float, default=None, metavar="DAYS",
                     help="只统计最近 N 天")
    pep.add_argument("--top", type=int, default=20,
                     help="每类最多 N 条（默认 20；判定槽行数 = 条目数）")
    pep.add_argument("--out", help="人读出口（Markdown）输出路径；缺省打印")
    pep.add_argument("--json", metavar="PATH",
                     help="机读出口：与 md **不共用实现**，结构带 schema")
    pep.add_argument("--meta", default=None,
                     help="归因台账 meta 库（只用于标出「已裁决」；不写）")
    pep.add_argument("--chain-root", default=None,
                     help="chain 文件根目录（kind=chains 时用；缺省 knowledge/topics）")
    pep.add_argument("--rules", default=None, help="归因规则表路径（缺省随包那份）")
    pep.set_defaults(func=cmd_evidence_pack)

    plp = sub.add_parser("llm-pack",
                         help="打包：事实 + 归因候选 + **判定槽**，交 LLM 复核；"
                              "也出人读评审包（--out）；只读，不写库")
    plp.add_argument("--db", default="harvester.db", help="索引库路径（只读）")
    plp.add_argument("--faces", default="errors",
                     help="要打包的面，逗号分隔"
                          "（errors,tools,skills,chains,attrib；默认 errors）")
    plp.add_argument("--chain-root", default=None,
                     help="chain 文件根目录（faces 含 chains 时必填，如 knowledge/topics）")
    plp.add_argument("--limit", type=int, default=40,
                     help="每面最多 N 条（默认 40；判定槽行数 = 条目数）")
    plp.add_argument("--since", type=float, default=None, metavar="DAYS",
                     help="只取最近 N 天（errors/attrib/skills 生效）")
    plp.add_argument("--meta", default=None,
                     help="归因台账 meta 库（只标出「已裁决」；不写）")
    plp.add_argument("--rules", default=None, help="归因规则表路径（缺省随包那份）")
    plp.add_argument("--out", metavar="PATH", default=None,
                     help="人读出口（Markdown）输出路径；可与 --json 同给")
    plp.add_argument("--json", metavar="PATH", default=None,
                     help="机读出口（llm-pack/1）输出路径；缺省打印到 stdout")
    plp.add_argument("--validate", metavar="PATH", default=None,
                     help="校验一个已填/未填的包（结构 + 判定槽对号），合格回 0")
    plp.set_defaults(func=cmd_llm_pack)

    prc = sub.add_parser("report-chains",
                         help="工具链失败模式报告（长回合/同工具连击/"
                              "序列循环/空转/高步 Top N，steps 表）")
    prc.add_argument("--db", default="harvester.db", help="索引库路径")
    prc.add_argument("--since", type=float, default=None, metavar="DAYS",
                     help="只统计最近 N 天（按步骤时间戳粗算；近似口径=不如逐条精确）")
    prc.add_argument("--top", type=int, default=10,
                     help="高步会话 Top N（默认 10）")
    prc.add_argument("--out", help="报告输出路径（缺省打印到 stdout）")
    prc.add_argument("--json", metavar="PATH", default=None,
                     help="机读出口（report-chains/1）：schema + db_fingerprint"
                          " + data（与文本同一个聚合函数）；'-'=stdout，"
                          "缺省仍出文本")
    prc.set_defaults(func=cmd_report_chains)

    prh = sub.add_parser("report-harness",
                         help="Harness 体检（每数据源一行画像：会话/消息/"
                              "步骤/失败率/代表工具/代表 skill/最近活跃）")
    prh.add_argument("--db", default="harvester.db", help="索引库路径")
    prh.add_argument("--since", type=float, default=None, metavar="DAYS",
                     help="只统计最近 N 天（按会话更新时间粗算）")
    prh.add_argument("--out", help="报告输出路径（缺省打印到 stdout）")
    prh.add_argument("--json", metavar="PATH", default=None,
                     help="机读出口（report-harness/1）：schema + db_fingerprint"
                          " + data（与文本同一个聚合函数）；'-'=stdout，"
                          "缺省仍出文本")
    prh.set_defaults(func=cmd_report_harness)

    prd = sub.add_parser(
        "report-daily",
        help="每日数据速览（P5-⑤）：四队列 + tools/errors/chains 五段聚合，"
             "只读，--out 落盘供工作区 schedule 每日触发")
    prd.add_argument("--db", default="harvester.db", help="索引库路径")
    prd.add_argument("--meta", default="suggestions_meta.db",
                     help="建议台账 meta 库（decide/verify 队列口径；"
                          "不存在=诚实空态）")
    prd.add_argument("--cards-root",
                     help="已有卡片目录（待蒸馏队列的「疑似已有卡」比对，可省）")
    prd.add_argument("--since", type=float, default=1.0, metavar="DAYS",
                     help="tools/errors/chains 窗口天数（默认 1=当天）")
    prd.add_argument("--top", type=int, default=10,
                     help="高步会话 Top N（默认 10）")
    prd.add_argument("--out", help="报告输出路径（缺省打印到 stdout）")
    prd.add_argument("--json", metavar="PATH", default=None,
                     help="机读出口（report-daily/1）：schema + db_fingerprint"
                          " + data（与文本同一个聚合）；'-'=stdout")
    prd.set_defaults(func=cmd_report_daily)

    psj = sub.add_parser("report-skill-join",
                         help="把某主题长文（chain）的时间线与 skill 调用记录对上号："
                              "交叉表=谁在哪个阶段调了哪个 skill；"
                              "锚点=指向具体会话回合的定位标记（如 s1#3）")
    psj.add_argument("--chain", required=True, help="chain 长文路径（chain=主题蒸馏出的结构化长文）")
    psj.add_argument("--db", default="harvester.db", help="索引库路径")
    psj.add_argument("--skill", default=None,
                     help="只交叉指定 skill 名（缺省全部）")
    psj.add_argument("--margin-days", type=float, default=0.0,
                     dest="margin_days",
                     help="stage 时间窗向后放宽天数（默认 0）")
    psj.add_argument("--out", help="报告输出路径（缺省打印到 stdout）")
    psj.add_argument("--json", metavar="PATH", default=None,
                     help="机读出口（report-skill-join/1）：schema + db_fingerprint + "
                          "data（与文本同一个聚合）；'-'=stdout")
    psj.set_defaults(func=cmd_report_skill_join)

    ptc = sub.add_parser("topic-candidates",
                         help="自动聚类候选推荐（T5）：标题 n-gram + 任务"
                              "签名产候选簇；只产候选不改注册表")
    ptc.add_argument("--db", default="harvester.db", help="索引库路径")
    ptc.add_argument("--topics-meta", dest="topics_meta", default=None,
                     help="主题注册库（只读，用于排除已注册成员）")
    ptc.add_argument("--min-sim", dest="min_sim", type=float, default=0.35,
                     help="成簇相似度阈值（默认 0.35）")
    ptc.add_argument("--min-size", dest="min_size", type=int, default=2,
                     help="最小簇大小（默认 2）")
    ptc.add_argument("--top-tools", dest="top_tools", type=int, default=3,
                     help="任务签名的工具序列 top-k（默认 3）")
    ptc.add_argument("--out", help="报告输出路径（缺省打印到 stdout）")
    ptc.set_defaults(func=cmd_topic_candidates)

    pcon = sub.add_parser(
        "topic-consolidate",
        help="主题梳理流水线（v0.26）：出梳理包（信号表+零散候选+模板）/ "
             "执行人填的 plan（完整性校验 + 文件级原子）/ 看零散登记")
    pcon.add_argument("--meta", default="topics_meta.db",
                      help="主题 meta 库路径（默认 topics_meta.db）")
    pcon.add_argument("--db", default="harvester.db",
                      help="索引库（只读；出梳理包时用于取成员标题与零散候选）")
    pcon.add_argument("--chain-root", dest="chain_root", default=None,
                      help="chain 正式位目录（统计每主题已有几条链）")
    pcon.add_argument("--plan-out", dest="plan_out", default=None,
                      help="梳理包输出路径（缺省打印到 stdout）")
    pcon.add_argument("--apply", default=None,
                      help="执行指定的 plan.yaml（缺省 dry-run，加 --yes 才写库）")
    pcon.add_argument("--yes", action="store_true",
                      help="真的写库（缺省只校验并打印预览）")
    pcon.add_argument("--snap-dir", dest="snap_dir", default=None,
                      help="被舍弃主题的快照输出目录")
    pcon.add_argument("--noise-list", dest="noise_list", action="store_true",
                      help="列出已登记的零散会话")
    pcon.add_argument("--noise-max-chars", dest="noise_max_chars", type=int,
                      default=None,
                      help="梳理包里「零散会话候选」的字符阈值（默认 40；"
                           "调小 = 只挑最窄的一批）")
    pcon.add_argument("--triage", action="store_true",
                      help="零散分诊：按「只要求查询且无整合诉求」判定并打印")
    pcon.add_argument("--triage-out", dest="triage_out", default=None,
                      help="分诊报告输出路径（缺省打印到 stdout）")
    pcon.add_argument("--triage-json", dest="triage_json", default=None,
                      help="分诊结果 JSON 输出路径（**全量 rows，不截断**）；"
                           "填 plan 要用它，别照人读报告（每类只印 60 条）")
    pcon.add_argument("--triage-brief", dest="triage_brief", nargs="?",
                      const="*", default=None, metavar="VERDICT",
                      help="分诊简报：一行一条（verdict/sid/日期/命中主题/首条原文），"
                           "**不截断条数**；可选值只列某一类判定")
    pcon.add_argument("--triage-brief-out", dest="triage_brief_out", default=None,
                      help="分诊简报输出路径（缺省打印；与 --triage-brief 配合）")
    pcon.add_argument("--brief-chars", dest="brief_chars", type=int, default=60,
                      help="简报里首条原文的截断字符数（默认 60）")
    pcon.add_argument("--plan-seed", dest="plan_seed", default=None,
                      help="从分诊 JSON 生成 plan 草稿（机械映射 + 完整性校验）；"
                           "语义判断走 --judgment")
    pcon.add_argument("--judgment", dest="judgment", default=None,
                      help="--plan-seed 的逐轮判断文件（YAML：new_topics/"
                           "craft_topic/overrides/noise/skip）")
    pcon.add_argument("--craft-topic", dest="craft_topic", default=None,
                      help="--plan-seed：craft_material 整类的去处（tp-xxx 或 "
                           "new:KEY）；不给则该类本轮不动")
    pcon.add_argument("--require-covered", dest="require_covered", default=None,
                      help="--plan-seed：逗号分隔的判定类，若其中仍有未归置的 "
                           "sid 则报错退出（如 noise_high）")
    pcon.add_argument("--seed-out", dest="seed_out", default=None,
                      help="--plan-seed 的 plan 输出路径（缺省打印到 stdout）")
    pcon.add_argument("--triage-max-turns", dest="triage_max_turns", type=int,
                      default=3,
                      help="分诊范围：user 回合数 ≤ 该值的会话（默认 3）")
    pcon.add_argument("--triage-deep", dest="triage_deep", action="store_true",
                      help="同时捞出**深会话**（user 回合 > --triage-max-turns），"
                           "单列为 deep_topic_hint / deep_unassigned 两类，"
                           "**永不参与零散判定**（v0.39；不这么做的副作用是深会话"
                           "永远进不了归位视野）")
    pcon.set_defaults(func=cmd_topic_consolidate)

    pdc = sub.add_parser(
        "deadcode-scan",
        help="死代码扫描（AST）：未用 import / 未被引用函数 / 未被引用常量。"
             "名字引用统计覆盖 harvester+tests+harvester-view（H48：只扫 harvester "
             "会把测试用到的 API 误判成死函数），默认只对 harvester/ 报发现")
    pdc.add_argument("--root", action="append", dest="roots", default=None,
                     help="参与名字统计的根目录（可多次；缺省 "
                          "harvester,tests,harvester-view 中存在者）")
    pdc.add_argument("--report-root", dest="report_root", default="harvester",
                     help="只对该根下的文件报 AST 发现（默认 harvester）")
    pdc.add_argument("--fail-on-found", dest="fail_on_found", action="store_true",
                     help="发现任何一条即退出码 1（缺省只提示，因为检查是启发式）")
    pdc.add_argument("--out", default=None, help="报告输出路径（缺省打印到 stdout）")
    pdc.set_defaults(func=cmd_deadcode_scan)

    pgr = sub.add_parser(
        "regress",
        help="端到端回归语料（固定示例数据，专用来重复验证全流程；v0.43）：**从空库**跑 "
             "分诊（含 --triage-deep）→ --plan-seed → --apply dry-run/真写 → "
             "topic export/md → H87 跨进程确定性，逐项核对关键数字。"
             "全程只用临时库（造自 indexing.SCHEMA + index_session），"
             "**真 harvester.db / topics_meta.db 一字节不动**")
    pgr.add_argument("--out", default=None,
                     help="人读报告输出路径（缺省打印到 stdout）")
    pgr.add_argument("--json", default=None,
                     help="机读结果（JSON）输出路径；人读/机读分出口"
                          "（下游是程序就别给人读那份）")
    pgr.add_argument("--keep-temp", dest="keep_temp", action="store_true",
                     help="保留临时库目录（报告里给出路径），缺省跑完即删")
    pgr.add_argument("--temp-base", dest="temp_base", default=None,
                     help="--keep-temp 时临时目录的父目录（缺省当前目录）")
    pgr.add_argument("--list", action="store_true",
                     help="同时把报告打印到 stdout（配合 --out）")
    pgr.add_argument("--corpus", default="default",
                     help="语料变体：default（内置 12 条）/ drift（**故意漂移**："
                          "多一条会话，用来实测「断言失败 → 退出码 1」这条路是通的）")
    pgr.set_defaults(func=cmd_regress)

    pk = sub.add_parser("keywords",
                        help="n-gram 关键词统计（只统计 messages.raw，"
                             "H3 契约；落 keywords_meta.db）")
    pk.add_argument("--db", default="harvester.db", help="索引库路径")
    pk.add_argument("--meta", default="keywords_meta.db",
                    help="关键词 meta 库路径（run 追加式）")
    pk.add_argument("--n", default="2,3",
                    help="n-gram 档位列表，逗号分隔（默认 2,3）")
    pk.add_argument("--role", default="user", choices=["user", "all"],
                    help="消息角色过滤（默认 user；all=全部）")
    pk.add_argument("--sid", action="append",
                    help="限定会话 sid（可重复；缺省=全库）")
    pk.add_argument("--topic", action="append",
                    help="限定主题 id（可重复；从 --topics-meta 注册库"
                         "展开成员，与 --sid 合并去重）")
    pk.add_argument("--topics-meta", dest="topics_meta", default=None,
                    help="主题注册库路径（--topic 展开成员用）")
    pk.add_argument("--stopwords", action="append", default=None,
                    metavar="PATH",
                    help="追加停用词文件（每行一词，# 注释；可多次）。"
                         "默认已启用随包通用表，本项用于叠加私有表"
                         "（如作品人名，由使用者自备）")
    pk.add_argument("--no-stopwords", action="store_true",
                    help="关闭停用词过滤（含默认表），用于复现历史口径")
    pk.add_argument("--top", type=int, default=50,
                    help="报告每档 Top N（默认 50）")
    pk.add_argument("--keep-runs", dest="keep_runs", type=int, default=None,
                    help="GC：统计后只保留最近 N 次 run（含本次），更早的 run "
                         "与其 keyword_stats 一并删除（v0.45；不设则该库只增不减）")
    pk.add_argument("--vacuum", action="store_true",
                    help="GC 后回收磁盘（VACUUM；仅在确有删除时执行）")
    pk.add_argument("--out", help="报告输出路径（缺省打印到 stdout）")
    pk.set_defaults(func=cmd_keywords)

    pkg = sub.add_parser(
        "keywords-gc",
        help="keywords_meta.db 回收（保留最近 N 次 run，可 --vacuum）："
             "keyword_stats 是累积表，不回收会随运行次数线性膨胀")
    pkg.add_argument("--meta", default="keywords_meta.db",
                     help="keywords meta 库路径")
    pkg.add_argument("--keep-runs", dest="keep_runs", type=int, default=1,
                     help="保留最近几次 run（缺省 1；必须 >=1）")
    pkg.add_argument("--vacuum", action="store_true",
                     help="确有删除时执行 VACUUM 回收磁盘")
    pkg.set_defaults(func=cmd_keywords_gc)

    psa = sub.add_parser("suggest-agents",                        help="从错误模式生成 AGENTS.md 候选条目"
                              "（建议池，不直接改 AGENTS.md）")
    psa.add_argument("--db", default="harvester.db", help="索引库路径")
    psa.add_argument("--since", type=float, default=None, metavar="DAYS",
                     help="只统计最近 N 天")
    psa.add_argument("--min-count", type=int, default=3,
                     help="模式出条目的最低实测次数（默认 3）")
    psa.add_argument("--max", type=int, default=15, help="最多条目数")
    psa.add_argument("--out", default="agents_suggestions.md",
                     help="建议文件输出路径")
    psa.add_argument("--meta", default=None,
                     help="建议状态 meta 库路径（suggestion_status 表；"
                          "缺省不读状态，全部按 pending 渲染）")
    psa.add_argument("--coverage", action="store_true",
                     help="追加「建议池 ↔ 台账」覆盖核对（v0.45）：列出待裁决、"
                          "台账陈旧·已采纳（疑似生效，不算欠账）与·其他（应清账），"
                          "另附人账总览（人账=人工登记的四本账）；只对账不代改")
    psa.add_argument("--coverage-out", dest="coverage_out", default=None,
                     help="覆盖核对报告输出路径（缺省打印）")
    psa.add_argument("--topics-meta", dest="topics_meta",
                     default="topics_meta.db",
                     help="主题注册表库路径（--coverage 的人账总览用；"
                          "V0.46 P2-5）")
    psa.set_defaults(func=cmd_suggest_agents)

    pss = sub.add_parser("suggest-status",
                         help="建议池状态落库（pending/adopted/rejected，"
                              "写独立 meta 库，不碰采集库）")
    pss.add_argument("--meta", required=True, help="meta 库路径")
    pss.add_argument("--key", required=True,
                     help="建议条目 key（建议池条目=title，"
                          "待人工归因模式=pattern）")
    pss.add_argument("--status", required=True,
                     choices=["pending", "adopted", "rejected"],
                     help="审阅结论")
    pss.add_argument("--adopted-at", dest="adopted_at", default=None,
                     help="采纳时刻（YYYY-MM-DD 或 'YYYY-MM-DD HH:MM:SS'）；"
                          "harvest-eval 用它当 A/B 分界 T。不传则首次采纳记当前时间、"
                          "已有值保留（只增不减）")
    pss.add_argument("--note", default=None,
                     help="人工说明；推荐写法里含一行 `pattern: <错误pattern>`，"
                          "harvest-eval 会用它做匹配（key 仍是建议句原文）")
    pss.set_defaults(func=cmd_suggest_status)

    pls = sub.add_parser(
        "ledger-sync",
        help="人账同步（v0.46 P2-5）：把主题注册表的既有裁决照抄进建议台账"
             "（topic:<id>），并回填已采纳条目的 adopted_at；默认 dry-run")
    pls.add_argument("--meta", default="suggestions_meta.db",
                     help="建议台账 meta 库路径")
    pls.add_argument("--topics-meta", dest="topics_meta",
                     default="topics_meta.db", help="主题注册表库路径")
    pls.add_argument("--apply", action="store_true",
                     help="真的写入（缺省只打印将要发生的变更）")
    pls.set_defaults(func=cmd_ledger_sync)

    phe = sub.add_parser(
        "harvest-eval",
        help="自进化评估闭环（v0.46 P1-4）：adopted 规则在分界 T 前后的"
             "未解决率/复发率 A/B + 三值判定（改善/无变化/变差/不下结论）")
    phe.add_argument("--db", default="harvester.db", help="索引库路径")
    phe.add_argument("--meta", default="suggestions_meta.db",
                     help="建议台账 meta 库路径")
    phe.add_argument("--since", default=None, metavar="STAMP",
                     help="未记 adopted_at 时的分界回退值"
                          "（YYYY-MM-DD 或 'YYYY-MM-DD HH:MM:SS'）")
    phe.add_argument("--window-days", dest="window_days", type=int,
                     default=_EVAL_DEFAULT_WINDOW,
                     help="前后窗口天数（默认 %(default)s）")
    phe.add_argument("--threshold", type=float, default=_EVAL_DEFAULT_THRESHOLD,
                     help="相对变化阈值（默认 0.10 = ±10%% 内算无变化）")
    phe.add_argument("--min-per-window", dest="min_per_window", type=int,
                     default=_EVAL_MIN_PER_WINDOW,
                     help="样本门槛：每个窗口最少几条错误，达不到一律"
                          "「不下结论（样本不足）」（默认 %d）"
                          % _EVAL_MIN_PER_WINDOW)
    phe.add_argument("--min-total", dest="min_total", type=int,
                     default=_EVAL_MIN_TOTAL,
                     help="样本门槛：两窗口合计最少几条错误（默认 %d）"
                          % _EVAL_MIN_TOTAL)
    phe.add_argument("--out", help="报告输出路径（缺省打印）")
    phe.set_defaults(func=cmd_harvest_eval)

    psp = sub.add_parser(
        "suppress",
        help="会话抑制（v0.49）：把没营养的会话从**仓内副本**里移除"
             "（库内三张表的行 + exports 副本），并保证以后 sync/update 不再入库；"
             "默认 dry-run，--apply 才写。**不删采集源**")
    psub = psp.add_subparsers(dest="action", required=True)

    psl = psub.add_parser("list", help="列出已抑制会话（谁 / 何时 / 为什么）")
    psl.add_argument("--meta", default="topics_meta.db",
                     help="抑制表所在 meta 库（默认 topics_meta.db）")
    psl.set_defaults(func=cmd_suppress)

    pscn = psub.add_parser(
        "candidates",
        help="抑制候选清单（只读，v0.52）：重复会话 + 零 user 轮次；"
             "人按标题裁决、复制命令，不用背 sid")
    pscn.add_argument("--db", default="harvester.db", help="索引库路径")
    pscn.add_argument("--meta", default="topics_meta.db",
                      help="抑制表所在 meta 库（默认 topics_meta.db）")
    pscn.add_argument("--min-jaccard", dest="min_jaccard", type=float,
                      default=None, metavar="0~1",
                      help="只列出重复度 J≥该值的候选（如 0.92）；"
                           "「同簇链式重复」没有独立 J 值，始终保留。"
                           "J=两段会话文字的重复度，1=逐字相同")
    pscn.set_defaults(func=cmd_suppress)

    psa = psub.add_parser("add", help="抑制会话（默认 dry-run，--apply 才写）")
    psa.add_argument("--sid", action="append", dest="sids", required=True,
                     metavar="SID",
                     help="库口径 sid（`source:session_id`，即 search 输出的 "
                          "[sid]）；可重复给多个")
    psa.add_argument("--reason", default="",
                     help="为什么抑制（写进抑制表，供日后复查）")
    psa.add_argument("--by", dest="created_by", default="",
                     help="谁做的（写进抑制表；缺省留空，不猜身份）")
    psa.add_argument("--db", default="harvester.db", help="索引库路径")
    psa.add_argument("--meta", default="topics_meta.db",
                     help="抑制表所在 meta 库（默认 topics_meta.db）")
    psa.add_argument("--exports", default="exports",
                     help="导出产物目录（清该 sid 的旧 .json/.md 副本）")
    psa.add_argument("--apply", action="store_true",
                     help="真的写（缺省只预演：库内行数 / 将删的副本）")
    psa.set_defaults(func=cmd_suppress)

    psr = psub.add_parser(
        "remove",
        help="取消抑制（默认 dry-run）。**不恢复数据**——能否恢复只取决于"
             "语料是否还在，语料还在则下次 sync 会重新导出入库")
    psr.add_argument("--sid", action="append", dest="sids", required=True,
                     metavar="SID", help="要取消抑制的 sid；可重复")
    psr.add_argument("--meta", default="topics_meta.db",
                     help="抑制表所在 meta 库（默认 topics_meta.db）")
    psr.add_argument("--apply", action="store_true",
                     help="真的删抑制表行（缺省只打印）")
    psr.set_defaults(func=cmd_suppress)

    pspru = psub.add_parser(
        "prune-exports",
        help="按抑制表清掉 exports 里的旧副本（**只清孤儿文件**；默认 dry-run）")
    pspru.add_argument("--meta", default="topics_meta.db",
                       help="抑制表所在 meta 库（默认 topics_meta.db）")
    pspru.add_argument("--exports", default="exports",
                       help="导出产物目录")
    pspru.add_argument("--apply", action="store_true",
                       help="真的删（缺省只列出将删的文件）")
    pspru.set_defaults(func=cmd_suppress)

    pns = sub.add_parser(
        "noise",
        help="零散会话登记（P5-⑧）：判「不归任何主题」的会话写进 "
             "sessions_noise，主题候选簇（candidates/keywords）会排除它们；"
             "只写 meta 表、不动会话数据（可逆：删行即撤销）")
    pnsub = pns.add_subparsers(dest="action", required=True)

    pnl = pnsub.add_parser("list", help="列出已登记的零散会话（何时 / 为什么）")
    pnl.add_argument("--meta", default="topics_meta.db",
                     help="零散表所在 meta 库（默认 topics_meta.db）")
    pnl.set_defaults(func=cmd_noise)

    pna = pnsub.add_parser(
        "add", help="登记零散会话（幂等：已在表里的 sid 跳过；"
                    "默认 dry-run，--apply 才写）")
    pna.add_argument("--sid", action="append", dest="sids", required=True,
                     metavar="SID",
                     help="库口径 sid（`source:session_id`，即 search 输出的 "
                          "[sid]）；可重复给多个")
    pna.add_argument("--reason", default="",
                     help="为什么判零散（写进零散表，供日后复查）")
    pna.add_argument("--meta", default="topics_meta.db",
                     help="零散表所在 meta 库（默认 topics_meta.db）")
    pna.add_argument("--apply", action="store_true",
                     help="真的写（缺省只预演：列将登记的 sid / 已在表跳过）")
    pna.set_defaults(func=cmd_noise)

    pcd = sub.add_parser("cards",
                         help="知识卡片校验（§8 frontmatter 规范 + 锚点有效性；"
                          "锚点=指向具体会话回合的定位标记）")
    pcdsub = pcd.add_subparsers(dest="action", required=True)
    pcap = pcdsub.add_parser("validate", help="校验卡片目录")
    pcap.add_argument("--root", required=True, help="卡片库根目录")
    pcap.add_argument("--db", help="索引库路径（给定时校验锚点 session_id）")
    pcap.add_argument("--topics-meta", dest="topics_meta", default=None,
                      help="主题注册表 topics_meta.db（可选；给定时追加"
                           "「卡片声明主题 ↔ 注册表成员资格」一致性核对，"
                           "缺省不做该检查，行为与旧版一致）")
    pcap.add_argument("--reviews-meta", dest="reviews_meta",
                      default="cards_meta.db",
                      help="复核台账 meta 库（默认 cards_meta.db；库/表不存在"
                           "=零感知。人已裁决的警告不再显示——人裁过的不许再烦人）")
    pcap.add_argument("--show-review", dest="show_review", action="store_true",
                      help="连同已裁决警告一起显示（复核留痕用）")
    pcap.add_argument("--out", help="报告输出路径（缺省打印到 stdout）")
    pcap.add_argument("--json", metavar="PATH", default=None,
                      help="机读出口（cards-validate/1）：schema + "
                           "db_fingerprint + data（逐卡结果与文本同源）；"
                           "'-'=stdout")
    pcap.set_defaults(func=cmd_cards)
    pcn = pcdsub.add_parser("new", help="从索引库会话生成卡片脚手架")
    pcn.add_argument("--sid", required=True,
                     help="会话的 sid 或 session_id（search 输出的 [sid]）")
    pcn.add_argument("--root", required=True, help="卡片输出目录")
    pcn.add_argument("--type", default="pitfall", choices=["pitfall", "workflow", "insight"],
                     help="卡片类型（默认 pitfall）")
    pcn.add_argument("--title", help="覆盖标题（默认用会话标题）")
    pcn.add_argument("--turn", type=int, help="锚点回合号（可选）")
    pcn.add_argument("--db", default="harvester.db", help="索引库路径")
    pcn.add_argument("--dry-run", dest="dry_run", action="store_true",
                     help="只算会写到哪个文件，不建目录、不写卡片（页面预演用）")
    pcn.set_defaults(func=cmd_cards_new)
    pcr = pcdsub.add_parser("review",
                            help="复核台账：人裁过的不许再烦人（v0.74）")
    pcr.add_argument("--list", action="store_true",
                     help="列出台账明细（不需 --root）")
    pcr.add_argument("--file",
                     help="卡片文件名（相对 --root；缺省 --warn 时=整卡当前警告一言而决）")
    pcr.add_argument("--root", default=".", help="卡片库根目录（定位 --file 用）")
    pcr.add_argument("--warn", action="append",
                     help="只裁决包含该子串的警告（可多次；缺省=整卡当前警告）")
    pcr.add_argument("--verdict", default="accepted",
                     choices=["accepted", "fixed"],
                     help="accepted=接受（综述/误报）；fixed=已按修复处理"
                          "（默认 accepted；两者都不再烦人，要重开用改判）")
    pcr.add_argument("--note", help="裁决说明（写进台账）")
    pcr.add_argument("--by", help="谁裁决的（写进台账；缺省不猜身份）")
    pcr.add_argument("--db", default="harvester.db",
                     help="索引库路径（计算当前警告用；缺省 harvester.db）")
    pcr.add_argument("--reviews-meta", dest="reviews_meta",
                     default="cards_meta.db", help="复核台账 meta 库")
    pcr.set_defaults(func=cmd_cards_review)
    pci = pcdsub.add_parser(
        "index-md",
        help="触发词消歧素材：给 AGENTS.md 钩子定稿用（v0.78 §2；"
         "只读只列不裁决，机器不定稿）")
    pci.add_argument("--root", required=True, help="卡片库根目录")
    pci.add_argument("--out", help="输出路径（缺省打印到 stdout；"
                     "素材供人定稿，不进任何常驻注入）")
    pci.set_defaults(func=cmd_cards_index_md)

    prs = sub.add_parser(
        "report-skill",
        help="Skill 行为画像：按 skill 聚合调用/触发任务/行为链")
    prs.add_argument("--db", default="harvester.db", help="索引库路径")
    prs.add_argument("--skill", help="只看这一个 skill（输出全量调用深挖清单）")
    prs.add_argument("--min-calls", dest="min_calls", type=int, default=0,
                     help="样本量阈值：calls<阈值的 skill 标 low-sample"
                          "（默认 0=不标）")
    prs.add_argument("--out", help="报告输出路径（缺省打印到 stdout）")
    prs.add_argument("--json", metavar="PATH", default=None,
                     help="机读出口（report-skill/1）：schema + db_fingerprint"
                          " + data（与文本同一个聚合函数）；'-'=stdout，"
                          "缺省仍出文本")
    prs.set_defaults(func=cmd_report_skill)

    ptp = sub.add_parser(
        "topic",
        help="主题注册表（T1/T2）：register/add/remove/merge/rename/delete/"
             "list/show/export/md/chain/pack")
    ptp.add_argument("cmd", choices=["register", "add", "remove", "merge",
                                     "rename", "delete", "keywords", "list",
                                     "show", "export", "md", "dedupe", "turns",
                                     "chain", "pack"])
    ptp.add_argument("--meta", default="topics_meta.db",
                     help="主题 meta 库路径（默认 topics_meta.db）")
    ptp.add_argument("--name", help="register：主题名称；rename：新名称")
    ptp.add_argument("--keywords", help="register：逗号分隔关键词")
    ptp.add_argument("--id", dest="id_",
                     help="add/remove/merge/show/export/md/chain：主题 id")
    ptp.add_argument("--from", dest="from_",
                     help="merge：逗号分隔源主题 id（成员并入 --id 后删除）")
    ptp.add_argument("--keep-sources", action="store_true",
                     help="merge：保留源主题行（缺省删除）")
    ptp.add_argument("--sids", help="add：逗号分隔成员 sid 列表")
    ptp.add_argument("--evidence", default="",
                     help="add：成员证据说明（如按什么标准命中关键词）")
    ptp.add_argument("--sid", help="remove/fine/artifact 档：成员 sid")
    ptp.add_argument("--db", default="harvester.db",
                     help="chain/pack/export：索引库路径（只读）")
    ptp.add_argument("--chain-root", dest="chain_root", default=None,
                     help="export/md：chain 正式位目录（合并各链锚点用；"
                          "md 不给则关键转折/结论/未决标注为「未执行」）")
    ptp.add_argument("--cap", type=int, default=200,
                     help="turns：预览每回合截断字符数（默认 200）")
    ptp.add_argument("--full", action="store_true",
                     help="turns：输出 raw 全文（逐字引文用；体积大）")
    ptp.add_argument("--out", help="chain/pack/md：产物输出路径（缺省打印）")
    ptp.add_argument("--level", default="title",
                     choices=["title", "coarse", "mid", "fine", "artifact"],
                     help="chain/pack 分层：title(极粗~2K)/coarse(粗~10K)/"
                          "mid(中~30K)/fine/artifact（后两档按需，须 --sid）")
    ptp.add_argument("--artifacts-meta", default="artifacts_meta.db",
                     help="artifact 档产物 meta 库路径")
    ptp.add_argument("--max-chars", type=int, default=None,
                     help="覆盖该档默认字符预算")
    ptp.set_defaults(func=cmd_topic)

    pcv = sub.add_parser(
        "chain-validate",
        help="主题长文（chain）校验：frontmatter 必填项 + 锚点可回溯"
             "（锚点=定位标记；可回溯=按标记能找到原话；独立校验器，非 §8 卡片校验）")
    pcv.add_argument("file", help="chain 长文路径（chain-<topic>.md）")
    pcv.add_argument("--meta", default="topics_meta.db",
                     help="主题 meta 库路径（默认 topics_meta.db）")
    pcv.add_argument("--db", default="harvester.db",
                     help="索引库路径（只读，锚点回溯核对）")
    pcv.add_argument("--out", help="报告输出路径（缺省打印到 stdout）")
    pcv.set_defaults(func=cmd_chain_validate)

    pca = sub.add_parser(
        "chain-audit",
        help="chain 长文审计（v0.31/v0.45）：引文逐字门（未命中即不合格）+ "
             "锚点语义门（note 与原文零重叠的机械告警）+ 证据覆盖"
             "（哪些成员只有标题级证据）")
    pca.add_argument("file", help="chain 长文路径（chain-<topic>.md）")
    pca.add_argument("--db", default="harvester.db",
                     help="索引库路径（只读；取成员原文与回合原文）")
    pca.add_argument("--no-quotes", dest="no_quotes", action="store_true",
                     help="跳过引文逐字门")
    pca.add_argument("--no-anchors", dest="no_anchors", action="store_true",
                     help="跳过锚点语义门")
    pca.add_argument("--no-coverage", dest="no_coverage", action="store_true",
                     help="跳过证据覆盖区块（不查成员的锚点覆盖/去重）")
    pca.add_argument("--quotes-ascii", dest="quotes_ascii", action="store_true",
                     help="连 ASCII 双引号 \"…\" 引用一起逐字核（本仓库首条 chain "
                          "只有 ASCII 引用，「」门对它空转；开了即计入硬门）")
    pca.add_argument("--strict", action="store_true",
                     help="锚点告警也让退出码非零（默认只在引文门失败时非零）")
    pca.add_argument("--list-limit", dest="list_limit", type=int, default=20,
                     help="各区块明细最多列几条；0 = 不限（默认 20，超出会显式"
                          "印「另有 N 条」）")
    pca.add_argument("--reviews-meta", dest="reviews_meta",
                     default="topics_meta.db",
                     help="复核台账（存 topics_meta.db 的 audit_review 表；"
                          "库/表不存在=零感知。已裁决节点不再列入疑似错配）")
    pca.add_argument("--show-review", dest="show_review", action="store_true",
                     help="连同已裁决节点一起显示")
    pca.add_argument("--accept", action="append", metavar="SID#TURN",
                     help="一言而决：把该节点的语义门告警落账为 accepted"
                          "（可多次），随后审计即不再显示")
    pca.add_argument("--accept-note", dest="accept_note",
                     help="--accept 的裁决说明")
    pca.add_argument("--review-list", dest="review_list", action="store_true",
                     help="列出该 chain 的复核台账后退出")
    pca.add_argument("--out", help="报告输出路径（缺省打印到 stdout）")
    pca.add_argument("--json", metavar="PATH", default=None,
                     help="机读出口（chain-audit/1）：schema + db_fingerprint + "
                          "data（与文本同一个聚合）；'-'=stdout")
    pca.set_defaults(func=cmd_chain_audit)

    pcb = sub.add_parser(
        "chain-build",
        help="链创作包（v0.82）：为主题产出「素材+格式契约」，供 Agent 写 chain 长文"
             "（不代笔：演变与转折要人/Agent 判断；确定性输出，同库同参数两次一致）")
    pcb.add_argument("--id", dest="id_", required=True, help="主题 id")
    pcb.add_argument("--meta", default="topics_meta.db",
                     help="主题 meta 库路径（默认 topics_meta.db）")
    pcb.add_argument("--db", default="harvester.db",
                     help="索引库路径（只读，取成员首轮原话与轮数）")
    pcb.add_argument("--max-chars", dest="max_chars", type=int, default=None,
                     help="创作包字符预算（默认 72000；超出只砍锚点并报数）")
    pcb.add_argument("--out", help="包输出路径（缺省打印到 stdout）")
    pcb.add_argument("--json", metavar="PATH", default=None,
                     help="机读出口（chain-build/1）：schema + fingerprint + 包数据")
    pcb.set_defaults(func=cmd_chain_build)

    pcp = sub.add_parser(
        "chain-publish",
        help="链发布（v0.82）：chain-validate + chain-audit 全过才落进 "
             "knowledge/topics/；缺省预演，--apply 才写")
    pcp.add_argument("--id", dest="id_", required=True,
                     help="主题 id（草稿位按它定位：exports/chains/<id>.md）")
    pcp.add_argument("--draft", default=None,
                     help="草稿路径（缺省 exports/chains/<id>.md——"
                          "与 VIEW「生成链创作包」给 Agent 的落点一致）")
    pcp.add_argument("--chain-root", dest="chain_root",
                     default="knowledge/topics",
                     help="知识正本目录（默认 knowledge/topics）")
    pcp.add_argument("--meta", default="topics_meta.db",
                     help="主题 meta 库路径（默认 topics_meta.db）")
    pcp.add_argument("--db", default="harvester.db",
                     help="索引库路径（只读，锚点回溯与引文核对）")
    pcp.add_argument("--apply", action="store_true",
                     help="真写入正本（缺省只预演：报会落到哪、过没过）")
    pcp.add_argument("--force", action="store_true",
                     help="目标文件已存在时覆盖（缺省拦下）")
    pcp.add_argument("--allow-audit", dest="allow_audit", action="store_true",
                     help="允许 chain-audit 未过仍发布（只降级为警告；"
                          "引文逐字门不合格时慎用）")
    pcp.set_defaults(func=cmd_chain_publish)

    par = sub.add_parser(
        "artifacts",
        help="产物提取（T0-②）：Write/Edit args 回源提取 → artifacts_meta.db")
    par.add_argument("cmd", choices=["extract", "show"])
    par.add_argument("--sid", required=True, help="会话 sid（source:session_id）")
    par.add_argument("--meta", default="artifacts_meta.db",
                     help="产物 meta 库路径（默认 artifacts_meta.db）")
    par.add_argument("--db", default="harvester.db",
                     help="extract：索引库路径（只读，校验 sid 存在）")
    par.add_argument("--limit", type=int, default=None,
                     help="show：最多展示条数")
    par.set_defaults(func=cmd_artifacts)

    ptt = sub.add_parser(
        "triage",
        help="蒸馏队列（蒸馏=从会话里提炼值得记的知识卡片；机器初筛排队，人来判断）："
             "筛新错误pattern/旧坑重现/Skill行为链/高信号会话")
    ptt.add_argument("--db", default="harvester.db", help="索引库路径")
    ptt.add_argument("--since", type=float, default=7.0, metavar="DAYS",
                     help="窗口天数（默认 7；传 0 表示全库）")
    ptt.add_argument("--cards-root",
                     help="已有卡片目录（做'疑似已有卡'子串比对）")
    ptt.add_argument("--min-count", type=int, default=2,
                     help="pattern 入队的最小出现次数（默认 2）")
    ptt.add_argument("--top", type=int, default=10,
                     help="高信号会话条数（默认 10）")
    ptt.add_argument("--out", help="队列输出路径（缺省打印到 stdout）")
    ptt.set_defaults(func=cmd_triage)

    pea = sub.add_parser(
        "export-analysis",
        help="统一分析导出器（P1-4）：sessions/tools/errors/skills/triage，"
             "md/JSON 两份输出同源同标准（去重键=normalize_error）")
    pea.add_argument("--kind", required=True,
                     choices=["sessions", "tools", "errors", "skills",
                              "triage"],  # 与 export_analysis.KINDS 同步
                     help="导出类别")
    pea.add_argument("--format", default="md", choices=["md", "json"],
                     help="出口格式（同源双出口，默认 md）")
    pea.add_argument("--db", default="harvester.db", help="索引库路径")
    pea.add_argument("--days", type=float, default=None,
                     help="只统计最近 N 天（缺省全库）")
    pea.add_argument("--sids",
                     help="sessions kind：逗号分隔会话 sid 清单"
                          "（缺省=全库含错误步骤的会话）")
    pea.add_argument("--min-count", type=int, default=2,
                     help="triage kind：pattern 入队最小次数（默认 2）")
    pea.add_argument("--top", type=int, default=10,
                     help="triage kind：高信号会话条数（默认 10）")
    pea.add_argument("--cards-root", help="triage kind：既有卡片目录")
    pea.add_argument("--out", help="输出路径（缺省打印到 stdout）")
    pea.add_argument("--save", action="store_true",
                     help="不打印，落盘到标准报告目录 "
                          "docs/reports/<kind>-YYYY-MM-DD.<md|json>"
                          "（与页面「报告索引」同一个目录；给了 --out 时以 --out 为准）")
    pea.set_defaults(func=cmd_export_analysis)

    pdr = sub.add_parser(
        "draft",
        help="蒸馏包：把会话原文+卡片规范+指令合成一份自包含 md，喂给 Agent 起草知识卡片")
    pdr.add_argument("--sid", required=True,
                     help="会话（sid 或 session_id，队列锚点里拿）")
    pdr.add_argument("--type", default="pitfall", choices=["pitfall",
                     "workflow", "insight"], help="目标卡片类型（默认 pitfall）")
    pdr.add_argument("--db", default="harvester.db", help="索引库路径")
    pdr.add_argument("--max-chars", type=int, default=24000,
                     help="会话原文预算字符数（默认 24000）")
    pdr.add_argument("--out", help="包输出路径（缺省打印到 stdout）")
    pdr.set_defaults(func=cmd_draft)

    ptr = sub.add_parser(
        "report-traces",
        help="OTel trace 工具统计（耗时分布/失败率/取消，~/.workbuddy/traces）")
    ptr.add_argument("--root", default=str(
        Path.home() / ".workbuddy" / "traces"),
        help="traces 目录（默认 ~/.workbuddy/traces）")
    ptr.add_argument("--out", help="报告输出路径（缺省打印到 stdout）")
    ptr.set_defaults(func=cmd_report_traces)

    pi = sub.add_parser("kb-init", help="建知识库骨架（幂等）")
    pi.add_argument("--root", default=str(KB_DEFAULT_ROOT))
    pi.set_defaults(func=cmd_kb_init)

    pst = sub.add_parser("kb-stats", help="知识库盘点")
    pst.add_argument("--root", default=str(KB_DEFAULT_ROOT))
    pst.set_defaults(func=cmd_kb_stats)

    ppub = sub.add_parser(
        "kb-publish",
        help="把项目正本 knowledge/ 单向投递给 WorkBuddy 主程序"
             "（它只认自家目录，是四个消费者里唯一需要文件镜像的；"
             "view/Agent/第三方直读正本或走 API/MCP）。只喂不收，"
             "镜像可随时删除重建")
    ppub.add_argument("--root", default=str(MASTER_DEFAULT_ROOT),
                      help="正本根目录（缺省 knowledge，相对仓根跑）")
    ppub.add_argument("--dest", default=str(KB_DEFAULT_ROOT),
                      help="镜像位（缺省 WorkBuddy 知识库位置）")
    ppub.add_argument("--dry-run", dest="dry_run", action="store_true",
                      help="只算会复制几个文件，不建目录、不写镜像位（页面预演用）")
    ppub.set_defaults(func=cmd_kb_publish)

    pix = sub.add_parser("index", help="构建 FTS5 全文索引")
    pix.add_argument("--from", dest="from_dir", help="export 产物目录（读 *.json）")
    pix.add_argument("--all", action="store_true", help="实时扫描全部数据源")
    pix.add_argument("--deepseek-file", help="直接索引一个 DeepSeek 导出 JSON/ZIP")
    pix.add_argument("--export-file", action="append", metavar="ID=路径",
                     help="直接索引一个官方导出文件（可多次），如 "
                          "--export-file chatgpt-export=C:/x/conversations.json "
                          f"；id 可选值: {sorted(PLUGIN_IDS)}")
    pix.add_argument("--sources", default="sources.json")
    pix.add_argument("--db", default="harvester.db", help="索引库路径")
    pix.add_argument("--with-notes", action=argparse.BooleanOptionalAction,
                     default=True,
                     help="含工具调用/思考等非对话条目（默认开；--no-with-notes 关闭）")
    pix.add_argument("-v", "--verbose", action="store_true")
    pix.set_defaults(func=cmd_index)

    pse = sub.add_parser("search", help="全文检索历史会话")
    pse.add_argument("query", help="关键词（多词为 OR 语义）")
    pse.add_argument("--db", default="harvester.db")
    pse.add_argument("-n", "--limit", type=int, default=20)
    pse.add_argument("--source", help="限定来源 adapter id")
    pse.set_defaults(func=cmd_search)

    prd = sub.add_parser("read", help="按纲要序号读会话（--turn 下钻）")
    prd.add_argument("no", help="纲要序号（见 harvester scan）")
    prd.add_argument("--sources", default="sources.json")
    prd.add_argument("--turn", default="1",
                     help="回合号 1..N / last / all（默认 1）")
    prd.set_defaults(func=cmd_read)

    ppk = sub.add_parser("pack", help="产出跨 agent 上下文交接包")
    ppk.add_argument("--select", required=True, help='序号表达式，如 "3,5-9"')
    ppk.add_argument("--tokens", type=int, default=2000, help="token 预算")
    ppk.add_argument("--question", help="附加接续任务说明")
    ppk.add_argument("--sources", default="sources.json")
    ppk.add_argument("--out", default="context_pack.md")
    ppk.set_defaults(func=cmd_pack)

    pmc = sub.add_parser(
        "mcp-serve",
        help="MCP stdio server（agent 直查历史 + 主题/链/建议/卡片/产物）")
    pmc.add_argument("--sources", default="sources.json")
    pmc.add_argument("--db", default="harvester.db", help="search 用的索引库")
    pmc.add_argument("--topics-meta", dest="topics_meta", default=None,
                     help="主题注册 meta 库（启用 topic_list/topic_export/"
                          "chain_read）")
    pmc.add_argument("--chain-root", dest="chain_root", default=None,
                     help="topic-chain 长文目录（缺省 "
                          "knowledge/topics，项目正本）")
    pmc.add_argument("--cards-root", dest="cards_root", default=None,
                     help="卡片目录（启用 cards_list）")
    pmc.add_argument("--artifacts-meta", dest="artifacts_meta", default=None,
                     help="产物 meta 库（启用 artifacts_list）")
    pmc.add_argument("--suggestions-meta", dest="suggestions_meta", default=None,
                     help="建议状态 meta 库（启用 suggest_list）")
    pmc.set_defaults(func=cmd_mcp_serve)

    pap = sub.add_parser(
        "api-serve",
        help="只读 HTTP JSON API（schema 自检；默认 127.0.0.1，"
             "非回环 host 必须 --token）")
    pap.add_argument("--db", default="harvester.db", help="索引库路径")
    pap.add_argument("--port", type=int, default=8765)
    pap.add_argument("--host", default="127.0.0.1",
                     help="默认 127.0.0.1；跨机器暴露须配 --token")
    pap.add_argument("--token", help="启用 X-Token 头鉴权（非回环 host 必配）")
    pap.add_argument("--cards-root", dest="cards_root", default=None,
                     help="知识卡片目录（可选；启用 /api/cards 校验端点，"
                          "访问面启动时钉死，不接受 URL 指定目录）")
    pap.add_argument("--suggestions-meta", dest="suggestions_meta",
                     default=None,
                     help="建议状态 meta 库路径（可选；启用 /api/reports/"
                          "agents 的 status 字段）")
    pap.add_argument("--topics-meta", dest="topics_meta", default=None,
                     help="主题注册 meta 库路径（可选；启用 /api/topics 与 "
                          "/api/topic/<id>/chain 端点）")
    pap.add_argument("--chain-root", dest="chain_root", default=None,
                     help="topic-chain 长文目录（可选；缺省=<db 同目录>/"
                          "knowledge/topics，随项目走不随 cwd）")
    pap.add_argument("--keywords-meta", dest="keywords_meta",
                     default=None,
                     help="关键词 meta 库路径（可选；启用 /api/keywords）")
    pap.add_argument("--reviews-meta", dest="reviews_meta",
                     default=None,
                     help="卡片复核台账 meta 库（/api/cards 据此隐去已裁决警告；"
                          "缺省=<db 同目录>/cards_meta.db；库不存在=零感知。"
                          "人裁过的不许再烦人）")
    pap.add_argument("--traces-root", dest="traces_root", default=None,
                     help="OTel trace 目录（可选；启用 /api/reports/traces，"
                          "缺省=~/.workbuddy/traces）")
    pap.add_argument("--reports-dir", dest="reports_dir", default=None,
                     help="日报目录（可选；启用 /api/report-daily 端点，"
                          "缺省=仓根 docs/reports；文件名白名单 "
                          "daily-YYYY-MM-DD.md）")
    pap.add_argument("--corpus-watermark", dest="corpus_watermark",
                     action="store_true", default=False,
                     help="开启库龄判定（/api/meta 的库快照带真 stale："
                          "语料比入库水位新 ⇒ True）。默认关——要 stat 一遍"
                          "语料盘；不开时 stale 恒 null = **没判**，不是新鲜")
    pap.set_defaults(func=cmd_api_serve)

    return p


def run() -> None:
    """console_scripts 入口（pyproject [project.scripts]）。

    `main()` 返回 int，而 console script 包装器**不会**把返回值当退出码
    （只调函数、不 sys.exit）——直接用 `harvester.cli:main` 会让所有非零
    退出码静默变成 0。故这里显式 sys.exit。
    """
    sys.exit(main())


if __name__ == "__main__":
    sys.exit(main())
