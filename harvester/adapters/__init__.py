# -*- coding: utf-8 -*-
"""Adapter 注册表。

ACTIVE: 已实测可完整提取；STUBS: 留接口待实现。
新增实装 Adapter 时，把它从 STUBS 移入 ACTIVE 即可。

build_adapters(sources) 接收 discovery.probe 产出的 sources.json 配置，
实现"探测结果驱动扫描"：换设备时先 probe 再 scan，无需改代码。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .autoclaw import AutoClawAdapter
from .base import BaseAdapter
from .deepseek_export import DeepSeekExportAdapter
from .dsh import DshAdapter
from .official_export import ChatGptExportAdapter, ClaudeExportAdapter
from .stubs import (
    AutoClawUserStub,
    DeepSeekDesktopStub,
    DoubaoStub,
    QianwenStub,
    TraeStub,
    YuanbaoStub,
)
from .vscode_copilot import VscodeCopilotAdapter
from .workbuddy import WorkBuddyAdapter
from .workbuddy_transcript import WorkBuddyTranscriptAdapter
from .yuanbao_raw import YuanbaoRawAdapter
from .qianwen_raw import QianwenRawAdapter
from .doubao_raw import DoubaoRawAdapter

ACTIVE: list[type[BaseAdapter]] = [
    WorkBuddyAdapter,
    WorkBuddyTranscriptAdapter,
    VscodeCopilotAdapter,
    AutoClawAdapter,
    DshAdapter,
    DeepSeekExportAdapter,
    ChatGptExportAdapter,
    ClaudeExportAdapter,
    YuanbaoRawAdapter,
    QianwenRawAdapter,
    DoubaoRawAdapter,
]

STUBS: list[type[BaseAdapter]] = [
    DeepSeekDesktopStub,
    YuanbaoStub,
    DoubaoStub,
    QianwenStub,
    TraeStub,
    AutoClawUserStub,
]

#: 各 ACTIVE Adapter 支持的 sources 配置键（与 discovery.build_sources 对应）
_PARAM_MAP: dict[str, dict[str, str]] = {
    "workbuddy": {"workspace_root": "workspace_root"},
    "vscode-copilot": {"global_storage": "global_storage"},
    "autoclaw": {"search_bases": "search_bases"},
    "deepseek-export": {"paths": "paths"},
    "chatgpt-export": {"paths": "paths"},
    "claude-export": {"paths": "paths"},
    "workbuddy-transcript": {"projects_root": "projects_root"},
    "dsh": {"sessions_root": "sessions_root"},
    "yuanbao-raw": {"paths": "paths"},
    "qianwen-raw": {"paths": "paths"},
    "doubao-raw": {"paths": "paths"},
}


def build_adapters(sources: dict | None = None,
                   only: set[str] | None = None) -> list[BaseAdapter]:
    """构建全部 Adapter 实例。

    sources 两种配置入口（可并存，按 id 去重，plugins 优先）：
    1. 顶层键（probe 产出或手写）：{"<adapter-id>": {键值对}}，经 _PARAM_MAP 注入；
    2. plugins 节（ai-hist 式源插件声明，显式非默认）：
       {"plugins": [{"id": "deepseek-export", "paths": ["..."]}]}
       仅 PLUGIN_IDS 中的 id 有效——不认识的 id 报错列出可选值，不静默忽略。
    only: 限定构建的 adapter id 集合（None=全部）。sync/测试等只需
    导出型源时用，避免触碰本机其他数据源。
    """
    sources = sources or {}
    adapters: list[BaseAdapter] = []
    seen: set[str] = set()

    def _want(pid: str) -> bool:
        return only is None or pid in only

    for entry in sources.get("plugins", []) or []:
        if not isinstance(entry, dict) or "id" not in entry:
            continue
        pid = entry["id"]
        if pid not in PLUGIN_IDS:
            raise ValueError(
                f"sources.json plugins 中未知源插件 id: {pid!r}。"
                f"可选值: {sorted(PLUGIN_IDS)}")
        if not _want(pid) or pid in seen:
            continue
        adapters.append(PLUGIN_IDS[pid](**_plugin_kwargs(entry)))
        seen.add(pid)

    for cls in ACTIVE:
        if cls.id in seen or not _want(cls.id):
            continue
        kwargs = {}
        cfg = sources.get(cls.id, {})
        for cfg_key, param in _PARAM_MAP.get(cls.id, {}).items():
            if cfg_key in cfg:
                kwargs[param] = cfg[cfg_key]
        adapters.append(cls(**kwargs))
        seen.add(cls.id)
    for cls in STUBS:
        if cls.id in seen or not _want(cls.id):
            continue
        # 桩位支持注入 paths：probe 在其他机器发现的数据目录经 sources.json 生效，
        # 否则换设备后桩位一律 MISSING、probe 结果形同虚设。
        stub_cfg = sources.get(cls.id, {})
        paths = stub_cfg.get("paths") if isinstance(stub_cfg, dict) else None
        adapters.append(cls(paths=paths) if paths else cls())
        seen.add(cls.id)
    return adapters


#: 源插件注册表：sources.json plugins 节可声明的导出文件型 Adapter。
#: 新增时在此登记并保证其 __init__ 接受 paths 参数。
PLUGIN_IDS: dict[str, type[BaseAdapter]] = {
    "deepseek-export": DeepSeekExportAdapter,
    "chatgpt-export": ChatGptExportAdapter,
    "claude-export": ClaudeExportAdapter,
    "yuanbao-raw": YuanbaoRawAdapter,
    "qianwen-raw": QianwenRawAdapter,
    "doubao-raw": DoubaoRawAdapter,
}


def _plugin_kwargs(entry: dict) -> dict:
    kwargs = {}
    if entry.get("paths"):
        kwargs["paths"] = entry["paths"]
    return kwargs


def load_sources(path) -> dict:
    """读取 probe 产出的 sources.json（v0.51 S5：缺失/损坏分流）。

    缺失 → 空表回退（各源走默认路径，stderr 一行提示，不算错误）；
    损坏（JSON/编码解析失败、顶层非对象）→ ValueError fail loud——
    配置文件坏掉绝不能静默当"没配置"用（原则③：坏库不许当空库用）。
    """
    p = Path(path)
    if not p.is_file():
        print(f"[load_sources] 未找到 {p}，各源走默认路径回退（不算错误）",
              file=sys.stderr)
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError(
            f"sources.json 损坏（JSON/编码解析失败）: {p} ({e})") from e
    if not isinstance(data, dict):
        raise ValueError(
            f"sources.json 顶层应为对象，实为 {type(data).__name__}: {p}")
    return data
