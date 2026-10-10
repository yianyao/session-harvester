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
  python -m harvester sync [--root .] [--db] [--inbox] [--exports] [--no-notes] [-v]
      一键同步：收件箱收割 → 全量导出 → 整库重建索引（幂等，可挂定时）
  python -m harvester aggregate [--from exports/|--all] [--out corpus.md]
      聚合会话为语料（蒸馏第 1 步的机械部分，带 SRC 溯源锚点）
  python -m harvester report-tools [--sources] [--db] [--since 30d] [--out report.md]
      工具调用/失败率统计（steps 表结构化口径，含重试/放弃率）
  python -m harvester report-errors [--db] [--since 30d] [--out report.md]
      错误三分类（env/tool_interface/context）+ 开场/中途/收尾分桶（G2）
  python -m harvester report-skill [--db] [--skill <名>] [--out report.md]
      Skill 行为画像：按 skill 聚合调用/触发任务/调用后行为链（G4）
  python -m harvester suggest-agents [--db] [--min-count 3] [--out 文件] [--meta 路径]
      从错误模式生成 AGENTS.md 候选条目（建议池，人工审阅后并入）（G2 闭环）
  python -m harvester suggest-status --meta 路径 --key <key> --status pending|adopted|rejected
      建议池审阅结论落库（独立 meta 库 suggestion_status 表，不碰采集库）
  python -m harvester triage [--db] [--since 7d] [--cards-root 目录] [--min-count 2] [--out 文件]
  python -m harvester draft --sid <sid> [--type pitfall] [--out 文件] [--max-chars 24000]
      蒸馏队列：新会话确定性初筛（新错误pattern/旧坑重现/Skill行为链/高信号会话）
  python -m harvester cards validate --root 目录 [--db]
      知识卡片 §8 规范校验（frontmatter 完整性 + 锚点有效性）（G3）
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
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from .adapters import PLUGIN_IDS, build_adapters, load_sources
from .discovery import probe_and_write
from .distill import KB_DEFAULT_ROOT, corpus_from_exports, corpus_from_sources, kb_init, kb_stats
from .exporter import export, parse_selection
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
                 include_notes=not args.no_notes, verbose=args.verbose)
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
    print(f"本次新增 {len(r['new'])} 个会话"
          + (f"（移除 {len(r['gone'])} 个）" if r["gone"] else ""))
    for sid in r["new"][:10]:
        print(f"  + {sid}")
    if len(r["new"]) > 10:
        print(f"  ... 其余 {len(r['new']) - 10} 条略")
    print("后续: harvester report-tools / report-errors / report-skill / cards new "
          "均直接消费该索引库")
    return 0


def cmd_update(args) -> int:
    root = Path(args.root)
    r = run_update(root=root, sources_path=root / args.sources,
                   db_path=root / args.db,
                   inbox_dir=root / args.inbox,
                   exports_dir=root / args.exports,
                   include_notes=not args.no_notes, verbose=args.verbose)
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
    for sid in r["new"][:10]:
        print(f"  + {sid}")
    for sid in r["updated"][:10]:
        print(f"  ~ {sid}（updated_at 变化，重导）")
    if len(r["new"]) + len(r["updated"]) > 20:
        print("  ... 其余略")
    print("增量口径：只获取未获取的（新 sid / updated_at 变化）；"
          "全量对账请用 sync")
    return 0


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
    from .mcpserver import serve
    return serve(args.sources, args.db)


def cmd_api_serve(args) -> int:
    """只读 HTTP JSON API（apiserve.run 自带自检与安全守卫）。"""
    from .apiserve import run
    chain_root = (Path(args.chain_root) if args.chain_root
                  else Path.home() / ".workbuddy" / "knowledge" / "topics")
    return run(Path(args.db), port=args.port, host=args.host,
               token=args.token,
               cards_root=Path(args.cards_root) if args.cards_root else None,
               suggestions_meta=Path(args.suggestions_meta)
               if args.suggestions_meta else None,
               topics_meta=Path(args.topics_meta)
               if args.topics_meta else None,
               chain_root=chain_root,
               keywords_meta=Path(args.keywords_meta)
               if getattr(args, "keywords_meta", None) else None)


def cmd_report_tools(args) -> int:
    """工具调用/失败率统计。

    两种口径（--db 给定且 steps 表有数据时用结构化，否则 note 扫描）：
    - 结构化（推荐）：index 时写入的 steps 表——支持重试/放弃率；
    - note 扫描：从数据源实时加载，按 [tool_call]/[tool_result] 标记汇总。
    """
    from .toolstats import (collect_stats, collect_stats_from_db,
                            render_report)
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
                report = render_report(stats, flow=flow)
                report += render_source_table(
                    collect_source_stats(dbp, since_days=args.since))
                report += render_model_table(
                    collect_model_stats(dbp, since_days=args.since))
                _emit_report(report, args)
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

    report = render_report(collect_stats(_records()))
    _emit_report(report, args)
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
    """错误三分类报告（G2 确定性一半）：env/tool_interface/context + 位置分桶。"""
    from .errstats import collect_errors_from_db, render_report
    dbp = Path(args.db)
    if not dbp.is_file():
        print(f"错误: 索引库不存在: {dbp}（先运行 harvester index）",
              file=sys.stderr)
        return 2
    errors, meta = collect_errors_from_db(dbp, since_days=args.since)
    _emit_report(render_report(errors, meta), args)
    return 0


def cmd_report_chains(args) -> int:
    """工具链失败翼报告（P2-1）：长回合/连击/循环/空转/高步 Top N。"""
    from .chainstats import collect, render_report
    dbp = Path(args.db)
    if not dbp.is_file():
        print(f"错误: 索引库不存在: {dbp}（先运行 harvester index）",
              file=sys.stderr)
        return 2
    d = collect(dbp, since_days=args.since, top=args.top)
    _emit_report(render_report(d, top=args.top), args)
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
    return 0


def cmd_topic_consolidate(args) -> int:
    """主题梳理流水线（v0.26）：出梳理包 / 执行 plan / 看零散登记。

    确定性一半 + Agent 一半：工具只按显式规则缩小候选范围并确定性执行，
    语义归组一律来自人填的 plan（不写死主题）。
    """
    from .consolidate import (NOISE_MAX_CHARS, apply_plan, build_plan_packet,
                              list_noise, load_plan)
    from .noisetriage import render_triage, triage
    meta = Path(args.meta)
    if args.triage or args.triage_out:
        # 零散分诊（v0.30）：按"只要求查询 / 无整合诉求"判"取信息 vs 整合信息"
        t = triage(Path(args.db) if args.db else Path("harvester.db"), meta,
                   max_turns=args.triage_max_turns)
        text = render_triage(t)
        if args.triage_out:
            out = Path(args.triage_out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8", newline="\n")
            print(f"[topic-consolidate] 分诊报告已写入: {out}"
                  f"（扫描 {t['scanned']} 条；"
                  + "、".join(f"{k} {v}" for k, v in
                              sorted(t["counts"].items(),
                                     key=lambda kv: -kv[1])) + "）",
                  file=sys.stderr)
        else:
            print(text)
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
    _emit_report(render_report(s, top=args.top), args)
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
    return 0


def cmd_report_skill(args) -> int:
    """Skill 行为画像（G4 确定性主干）：按 skill 聚合调用/行为链/触发任务。"""
    from .behstats import collect_skill_invocations, render_skill_report
    dbp = Path(args.db)
    if not dbp.is_file():
        print(f"错误: 索引库不存在: {dbp}（先运行 harvester index）",
              file=sys.stderr)
        return 2
    invocations = collect_skill_invocations(dbp)
    _emit_report(render_skill_report(invocations, skill_filter=args.skill,
                                     min_calls=args.min_calls),
                 args)
    return 0


def cmd_suggest_status(args) -> int:
    """建议池状态落库（suggestion_status 表，独立 meta 库，不碰采集库）。"""
    from .suggestmeta import set_status
    try:
        set_status(Path(args.meta), args.key, args.status)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2
    print(f"已记录: {args.key} -> {args.status}（{args.meta}）")
    return 0


def cmd_cards(args) -> int:
    """知识卡片校验（G3 确定性一半）：§8 frontmatter 规范 + 锚点有效性。"""
    from .cards import render_report, validate_cards
    root = Path(args.root)
    if not root.is_dir():
        print(f"错误: 卡片目录不存在: {root}", file=sys.stderr)
        return 2
    dbp = Path(args.db) if args.db else None
    results, summary = validate_cards(root, dbp)
    report = render_report(results, summary, root)
    _emit_report(report, args)
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
                             turn=args.turn, dupe_warnings=dupes)
    except (KeyError, ValueError) as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2
    print(f"脚手架已生成: {path}")
    for w in dupes:
        print(f"⚠️ {w}")
    print("下一步: 补全 evidence（原会话证据原文）与正文，然后 "
          "cards validate --root ... --db ...")
    return 0


def cmd_topic(args) -> int:
    """主题注册表（T1 MVP，v0.22）：独立 meta 库 topics_meta.db。"""
    from .topics import (add_members, delete_topic, list_topics, merge_topics,
                         register_topic, remove_member, rename_topic,
                         show_topic, title_chain)
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


def cmd_artifacts(args) -> int:
    """产物提取（T0-②，v0.22）：独立 meta 库 artifacts_meta.db。"""
    from .artifacts import ensure_artifacts_db, extract_session, show_artifacts
    meta = ensure_artifacts_db(Path(args.meta))
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
    """报告统一出口：库快照产物头（v0.19）+ 落盘/打印。
    产物头仅在 args 带 --db 且库文件存在时写入（report-traces 等
    非库报告自动跳过）；只加行不改报告正文。"""
    dbp = getattr(args, "db", None)
    if dbp and Path(dbp).is_file():
        from .dbmeta import db_fingerprint, fingerprint_line
        try:
            report = (f"> {fingerprint_line(db_fingerprint(Path(dbp)))}\n\n"
                      + report)
        except sqlite3.Error:
            pass  # 指纹失败不阻断报告产出
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8", newline="\n")
        print(f"报告已写入 {out}")
    else:
        print(report, end="")


def cmd_export_analysis(args) -> int:
    """export-analysis 统一导出器（P1-4）：五类分析产物，md/JSON 同源。"""
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
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8", newline="\n")
        print(f"已写入 {out}（kind={args.kind} format={args.format}）")
    else:
        print(text, end="")
    return 0


def main(argv=None) -> int:
    # Windows 原版解释器默认 cp936，report 输出含 emoji/特殊 Unicode 标题重定向
    # 到文件/管道时会 UnicodeEncodeError——统一 UTF-8（DSH 等已全局 UTF-8 则无感）。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:  # 非 TextIOWrapper（如测试桩）
            pass
    p = argparse.ArgumentParser(prog="harvester", description="AI 会话历史提取与分类导出工具")
    sub = p.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("probe", help="固化探测：扫描本机数据源签名并生成 sources.json")
    pp.add_argument("--out", default="sources.json")
    pp.add_argument("-v", "--verbose", action="store_true")
    pp.set_defaults(func=cmd_probe)

    ps = sub.add_parser("scan", help="扫描数据源并生成纲要")
    ps.add_argument("--sources", default="sources.json", help="probe 产出的路径配置")
    ps.add_argument("--out", default="outline", help="纲要输出目录")
    ps.add_argument("-v", "--verbose", action="store_true")
    ps.set_defaults(func=cmd_scan)

    pe = sub.add_parser("export", help="按纲要序号导出会话")
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

    pg = sub.add_parser("aggregate", help="聚合会话为语料")
    pg.add_argument("--from", dest="from_dir", help="export 产物目录（读 *.json）")
    pg.add_argument("--all", action="store_true", help="直接从全部数据源实时聚合")
    pg.add_argument("--sources", default="sources.json")
    pg.add_argument("--out", default="corpus.md")
    pg.add_argument("--with-notes", action=argparse.BooleanOptionalAction,
                    default=True,
                    help="包含工具调用/思考等非对话条目（默认开；--no-with-notes 关闭）")
    pg.set_defaults(func=cmd_aggregate)
    psy = sub.add_parser("sync", help="一键同步：收件箱收割 -> 导出 -> 重建索引（幂等）")
    psy.add_argument("--root", default=".", help="项目根目录（其余相对路径的基准）")
    psy.add_argument("--sources", default="sources.json", help="数据源声明（相对 root）")
    psy.add_argument("--db", default="harvester.db", help="索引库路径（相对 root）")
    psy.add_argument("--inbox", default="inbox", help="收件箱目录（相对 root）")
    psy.add_argument("--exports", default="exports", help="导出目录（相对 root）")
    psy.add_argument("--no-notes", action="store_true",
                     help="导出时排除 note 角色（默认包含——工具步骤藏在 "
                          "note 消息里，排除会导致 steps 表为空、分析失效）")
    psy.add_argument("-v", "--verbose", action="store_true")
    psy.set_defaults(func=cmd_sync)

    pu = sub.add_parser("update",
                        help="增量同步：收件箱收割 -> 只导出新/变更会话 -> 重建索引")
    pu.add_argument("--root", default=".", help="项目根目录（其余相对路径的基准）")
    pu.add_argument("--sources", default="sources.json", help="数据源声明（相对 root）")
    pu.add_argument("--db", default="harvester.db", help="索引库路径（相对 root）")
    pu.add_argument("--inbox", default="inbox", help="收件箱目录（相对 root）")
    pu.add_argument("--exports", default="exports", help="导出目录（相对 root）")
    pu.add_argument("--no-notes", action="store_true",
                    help="导出时排除 note 角色（默认包含，理由同 sync）")
    pu.add_argument("-v", "--verbose", action="store_true")
    pu.set_defaults(func=cmd_update)

    prt = sub.add_parser("report-tools",
                         help="工具调用/失败率统计（steps 表或 tool note 汇总）")
    prt.add_argument("--sources", default="sources.json")
    prt.add_argument("--db", help="索引库路径；steps 表有数据时用结构化口径"
                                 "（含重试/放弃率）")
    prt.add_argument("--since", type=float, default=None, metavar="DAYS",
                     help="只统计最近 N 天（按 steps.ts，近似口径）")
    prt.add_argument("--out", help="报告输出路径（缺省打印到 stdout）")
    prt.add_argument("-v", "--verbose", action="store_true")
    prt.set_defaults(func=cmd_report_tools)

    pre = sub.add_parser("report-errors",
                         help="错误三分类报告（env/tool_interface/context "
                              "+ 开场/中途/收尾分桶，steps 表）")
    pre.add_argument("--db", default="harvester.db", help="索引库路径")
    pre.add_argument("--since", type=float, default=None, metavar="DAYS",
                     help="只统计最近 N 天")
    pre.add_argument("--out", help="报告输出路径（缺省打印到 stdout）")
    pre.set_defaults(func=cmd_report_errors)

    prc = sub.add_parser("report-chains",
                         help="工具链失败翼报告（长回合/同工具连击/"
                              "序列循环/空转/高步 Top N，steps 表）")
    prc.add_argument("--db", default="harvester.db", help="索引库路径")
    prc.add_argument("--since", type=float, default=None, metavar="DAYS",
                     help="只统计最近 N 天（按 steps.ts，近似口径）")
    prc.add_argument("--top", type=int, default=10,
                     help="高步会话 Top N（默认 10）")
    prc.add_argument("--out", help="报告输出路径（缺省打印到 stdout）")
    prc.set_defaults(func=cmd_report_chains)

    psj = sub.add_parser("report-skill-join",
                         help="skill 进化 join：chain 时间线锚点 × "
                              "skill 调用锚点交叉表（T4）")
    psj.add_argument("--chain", required=True, help="chain 长文路径")
    psj.add_argument("--db", default="harvester.db", help="索引库路径")
    psj.add_argument("--skill", default=None,
                     help="只交叉指定 skill 名（缺省全部）")
    psj.add_argument("--margin-days", type=float, default=0.0,
                     dest="margin_days",
                     help="stage 时间窗向后放宽天数（默认 0）")
    psj.add_argument("--out", help="报告输出路径（缺省打印到 stdout）")
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
    pcon.add_argument("--triage-max-turns", dest="triage_max_turns", type=int,
                      default=3,
                      help="分诊范围：user 回合数 ≤ 该值的会话（默认 3）")
    pcon.set_defaults(func=cmd_topic_consolidate)

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
    pk.add_argument("--out", help="报告输出路径（缺省打印到 stdout）")
    pk.set_defaults(func=cmd_keywords)

    psa = sub.add_parser("suggest-agents",
                         help="从错误模式生成 AGENTS.md 候选条目"
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
    pss.set_defaults(func=cmd_suggest_status)

    pcd = sub.add_parser("cards",
                         help="知识卡片校验（§8 frontmatter 规范 + 锚点有效性）")
    pcdsub = pcd.add_subparsers(dest="action", required=True)
    pcap = pcdsub.add_parser("validate", help="校验卡片目录")
    pcap.add_argument("--root", required=True, help="卡片库根目录")
    pcap.add_argument("--db", help="索引库路径（给定时校验锚点 session_id）")
    pcap.add_argument("--out", help="报告输出路径（缺省打印到 stdout）")
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
    pcn.set_defaults(func=cmd_cards_new)

    prs = sub.add_parser(
        "report-skill",
        help="Skill 行为画像（G4）：按 skill 聚合调用/触发任务/行为链")
    prs.add_argument("--db", default="harvester.db", help="索引库路径")
    prs.add_argument("--skill", help="只看这一个 skill（输出全量调用深挖清单）")
    prs.add_argument("--min-calls", dest="min_calls", type=int, default=0,
                     help="样本量阈值：calls<阈值的 skill 标 low-sample"
                          "（默认 0=不标）")
    prs.add_argument("--out", help="报告输出路径（缺省打印到 stdout）")
    prs.set_defaults(func=cmd_report_skill)

    ptp = sub.add_parser(
        "topic",
        help="主题注册表（T1/T2）：register/add/remove/merge/rename/delete/"
             "list/show/export/chain/pack")
    ptp.add_argument("cmd", choices=["register", "add", "remove", "merge",
                                     "rename", "delete", "keywords", "list",
                                     "show", "export", "chain", "pack"])
    ptp.add_argument("--meta", default="topics_meta.db",
                     help="主题 meta 库路径（默认 topics_meta.db）")
    ptp.add_argument("--name", help="register：主题名称；rename：新名称")
    ptp.add_argument("--keywords", help="register：逗号分隔关键词")
    ptp.add_argument("--id", dest="id_",
                     help="add/remove/merge/show/export/chain：主题 id")
    ptp.add_argument("--from", dest="from_",
                     help="merge：逗号分隔源主题 id（成员并入 --id 后删除）")
    ptp.add_argument("--keep-sources", action="store_true",
                     help="merge：保留源主题行（缺省删除）")
    ptp.add_argument("--sids", help="add：逗号分隔成员 sid 列表")
    ptp.add_argument("--evidence", default="",
                     help="add：成员证据说明（如关键词命中口径）")
    ptp.add_argument("--sid", help="remove/fine/artifact 档：成员 sid")
    ptp.add_argument("--db", default="harvester.db",
                     help="chain/pack/export：索引库路径（只读）")
    ptp.add_argument("--chain-root", dest="chain_root", default=None,
                     help="export：chain 正式位目录（合并各链锚点用）")
    ptp.add_argument("--out", help="chain/pack：产物输出路径（缺省打印）")
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
        help="topic-chain 长文校验（T3）：frontmatter 必填项 + 锚点可回溯"
             "（独立校验器，非 §8 卡片校验）")
    pcv.add_argument("file", help="chain 长文路径（chain-<topic>.md）")
    pcv.add_argument("--meta", default="topics_meta.db",
                     help="主题 meta 库路径（默认 topics_meta.db）")
    pcv.add_argument("--db", default="harvester.db",
                     help="索引库路径（只读，锚点回溯核对）")
    pcv.add_argument("--out", help="报告输出路径（缺省打印到 stdout）")
    pcv.set_defaults(func=cmd_chain_validate)

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
        help="蒸馏队列（T1）：新错误pattern/旧坑重现/Skill行为链/高信号会话")
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
             "md/JSON 同源同口径（去重键=normalize_error）")
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
    pea.set_defaults(func=cmd_export_analysis)

    pdr = sub.add_parser(
        "draft",
        help="蒸馏包（T2）：会话原文+卡片规范+指令 → 自包含 md 喂 Agent")
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

    pmc = sub.add_parser("mcp-serve", help="MCP stdio server（agent 直查历史）")
    pmc.add_argument("--sources", default="sources.json")
    pmc.add_argument("--db", default="harvester.db", help="search 用的索引库")
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
                     help="topic-chain 长文目录（可选；默认 "
                          "~/.workbuddy/knowledge/topics）")
    pap.add_argument("--keywords-meta", dest="keywords_meta",
                     default=None,
                     help="关键词 meta 库路径（可选；启用 /api/keywords）")
    pap.set_defaults(func=cmd_api_serve)

    args = p.parse_args(argv)
    return args.func(args)


def run() -> None:
    """console_scripts 入口（pyproject [project.scripts]）。

    `main()` 返回 int，而 console script 包装器**不会**把返回值当退出码
    （只调函数、不 sys.exit）——直接用 `harvester.cli:main` 会让所有非零
    退出码静默变成 0。故这里显式 sys.exit。
    """
    sys.exit(main())


if __name__ == "__main__":
    sys.exit(main())
