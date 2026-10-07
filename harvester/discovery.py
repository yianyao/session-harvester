# -*- coding: utf-8 -*-
"""本机数据源发现引擎（probe 命令的实现）。

设计目标：把"探测哪台机器上装了哪些 AI 会话数据源"固化为可重复执行的规则，
而不是每次现场手写。规则基于 2026-10-05 实测归纳的文件系统签名：

1. VS Code 家族    : <app>/User/globalStorage/state.vscdb
                     （Code 及其分叉 Cursor/Trae/CodeBuddy 均适用）
2. AutoClaw 家族   : <app>/accounts/<hash>/runtime/runtime.sqlite
                     （表 work_sessions 存在才判定）
3. WebView 壳聊天  : <app>/EBWebView/（WebView2）或
                     <app>/Partitions/ + <app>/Local Storage/（Electron）
4. WorkBuddy 工作区: <root>/*/.workbuddy/memory/

输出 Finding 列表；能映射到已注册 Adapter 的写入 sources.json 供 scan 使用，
不能映射的归入 _unknown（含证据），为后续实装提供起点。

跨平台说明：根路径目前取 Windows 标准位置（%APPDATA% / %LOCALAPPDATA%），
其他平台按同样签名规则补充 roots 即可。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .adapters.base import open_ro_sqlite

APPDATA_ROAMING = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
APPDATA_LOCAL = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
WORKBUDDY_ROOT = Path.home() / "WorkBuddy"

#: 产品名 → adapter id 的名称匹配表（子串匹配，大小写不敏感）
NAME_MATCHERS: list[tuple[str, str]] = [
    ("yuanbao", "yuanbao"),
    ("deepseek", "deepseek-desktop"),
    ("doubao", "doubao"),
    ("qianwen", "qianwen"),
    ("tongyi", "qianwen"),
    ("trae", "trae"),
    ("autoclaw", "autoclaw-custom"),
]


@dataclass
class Finding:
    path: str
    kind: str                 # vscode-family / autoclaw-family / webview-shell / workbuddy-workspace
    evidence: str
    matched_adapter: str | None = None
    note: str = ""


def _match_name(*names: str) -> str | None:
    low = " ".join(names).lower()
    for key, adapter_id in NAME_MATCHERS:
        if key in low:
            return adapter_id
    return None


#: 顶层噪声目录（系统组件，非聊天应用）
NOISE_TOP_DIRS = {"microsoft", "packages", "windows", "temp",
                  "connecteddevicesplatform", "comms", "crashdumps"}


def _probe_vscdb(db: Path) -> str:
    """探测 state.vscdb 中的会话证据（只读，失败不抛）。"""
    evidence = []
    try:
        con = open_ro_sqlite(db)  # 宿主进程持库（WAL）时自动退化临时副本
        try:
            keys = [r[0] for r in con.execute("SELECT key FROM ItemTable").fetchall()]
        finally:
            con.close()  # 查询异常时也确保关闭
        if "chat.ChatSessionStore.index" in keys:
            evidence.append("chat.ChatSessionStore.index")
        if any(k.startswith("icubeAiChat") for k in keys):
            evidence.append("icubeAiChat keys")
    except Exception as e:  # noqa: BLE001
        return f"state.vscdb 读取失败: {e}"
    return "; ".join(evidence) if evidence else "state.vscdb 存在，但未发现会话键"


def _probe_runtime_db(db: Path) -> bool:
    try:
        con = open_ro_sqlite(db)
        try:
            n = con.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='work_sessions'"
            ).fetchone()[0]
        finally:
            con.close()
        return n > 0
    except Exception:  # noqa: BLE001
        return False


def _classify_app_dir(app: Path, findings: list[Finding]) -> bool:
    """对单个候选应用目录按签名规则分类。命中任一规则返回 True。"""
    # VS Code 家族
    vscdb = app / "User" / "globalStorage" / "state.vscdb"
    if vscdb.is_file():
        ev = _probe_vscdb(vscdb)
        adapter = "vscode-copilot" if app.name == "Code" else _match_name(app.name)
        note = ("官方 VS Code" if adapter == "vscode-copilot"
                else (f"VS Code 分叉 {app.name}，映射到桩位" if adapter
                      else f"VS Code 分叉 {app.name}，未实装"))
        findings.append(Finding(
            path=str(vscdb), kind="vscode-family",
            evidence=ev, matched_adapter=adapter, note=note,
        ))
        return True
    # AutoClaw 家族：accounts/*/runtime/runtime.sqlite 且含 work_sessions
    acc_dir = app / "accounts"
    if acc_dir.is_dir():
        dbs = list(acc_dir.glob("*/runtime/runtime.sqlite"))
        if dbs and any(_probe_runtime_db(d) for d in dbs):
            findings.append(Finding(
                path=str(acc_dir), kind="autoclaw-family",
                evidence=f"runtime.sqlite 含 work_sessions（{len(dbs)} 个账号库）",
                matched_adapter="autoclaw",
            ))
            return True
    # WebView 壳聊天
    is_webview2 = (app / "EBWebView").is_dir()
    is_electron_shell = (app / "Partitions").is_dir() and (app / "Local Storage").is_dir()
    if is_webview2 or is_electron_shell:
        # 产品名可能在父目录（如 @deepseek-ai/dsh-desktop）
        adapter = _match_name(app.name, app.parent.name)
        shell = "WebView2" if is_webview2 else "Electron"
        findings.append(Finding(
            path=str(app), kind="webview-shell",
            evidence=f"{shell} 壳，本地无结构化会话库",
            matched_adapter=adapter,
            note="会话正文本体在服务端" if adapter else "未知聊天类应用，待人工确认",
        ))
        return True
    return False


def scan_machine(verbose: bool = False) -> list[Finding]:
    findings: list[Finding] = []
    roots = [APPDATA_ROAMING, APPDATA_LOCAL]

    def _report_hit() -> None:
        if verbose and findings:
            f = findings[-1]
            tag = f.matched_adapter or "UNKNOWN"
            print(f"  [found] {f.kind} {f.path} -> {tag} ({f.evidence})")

    for root in roots:
        if not root.is_dir():
            continue
        seen: set[str] = set()
        # 深度 2 扫描：部分产品把数据放在 <product>/<sub>/ 下（如 @deepseek-ai/dsh-desktop）
        for top in sorted(root.iterdir()):
            if not top.is_dir() or top.name.lower() in NOISE_TOP_DIRS:
                continue
            try:
                subdirs = [d for d in sorted(top.iterdir()) if d.is_dir()]
            except OSError:  # 系统保护目录（如 Application Data）拒绝访问，跳过
                continue
            candidates = [top] + subdirs
            for app in candidates:
                if str(app) in seen:
                    continue
                if _classify_app_dir(app, findings):
                    seen.add(str(app))
                    _report_hit()
                    break  # 命中父目录后不再扫其子目录

    # WorkBuddy 工作区
    if WORKBUDDY_ROOT.is_dir():
        n_ws = sum(1 for d in WORKBUDDY_ROOT.iterdir()
                   if d.is_dir() and (d / ".workbuddy" / "memory").is_dir())
        if n_ws:
            findings.append(Finding(
                path=str(WORKBUDDY_ROOT), kind="workbuddy-workspace",
                evidence=f"{n_ws} 个工作区含每日日志",
                matched_adapter="workbuddy",
            ))
            _report_hit()
    return findings


def build_sources(findings: list[Finding]) -> dict:
    """把 Finding 转成 build_adapters 可用的 sources 配置。"""
    sources: dict = {}
    unknown: list[dict] = []
    for f in findings:
        if f.matched_adapter == "workbuddy":
            sources.setdefault("workbuddy", {})["workspace_root"] = f.path
        elif f.matched_adapter == "vscode-copilot":
            # f.path = .../User/globalStorage/state.vscdb → 取其父目录 globalStorage
            sources.setdefault("vscode-copilot", {})["global_storage"] = \
                str(Path(f.path).parent)
        elif f.matched_adapter == "autoclaw":
            sources.setdefault("autoclaw", {}).setdefault("search_bases", []).append(
                str(Path(f.path).parent))  # accounts/ 的父目录
        elif f.matched_adapter:
            sources.setdefault(f.matched_adapter, {}).setdefault("paths", []).append(f.path)
        else:
            unknown.append({"path": f.path, "kind": f.kind,
                            "evidence": f.evidence, "note": f.note})
    if unknown:
        sources["_unknown"] = unknown
    return sources


def probe_and_write(out_path: Path, verbose: bool = False) -> tuple[list[Finding], dict]:
    findings = scan_machine(verbose=verbose)
    sources = build_sources(findings)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(sources, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    return findings, sources
