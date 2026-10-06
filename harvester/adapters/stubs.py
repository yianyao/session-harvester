# -*- coding: utf-8 -*-
"""暂不可实现的 Adapter 桩位。

本文件遵循项目铁律：不硬猜、不臆测解析逻辑。
每个桩位都基于 2026-10-05 对本机的真实探测结论：
- 桌面版聊天应用（DeepSeek/元宝/豆包/通义）均为 Electron/WebView2 壳，
  本地只有 Chromium 缓存（IndexedDB/LevelDB），会话正文本体在服务端，
  本地缓存不完整且格式无公开承诺 → 纯本地解析不可靠，暂不实现。
- 可行路径是"浏览器自动化 + 用户登录态"调取服务端会话列表
  （或未来官方开放 API），届时继承 WebChatStub 实现即可。

新增实现时的接入方式：
1. 在本文件为对应类去掉 NotImplementedError，改为真实实现；
2. 将其在 registry（adapters/__init__.py）中的注册位置从 STUBS 移到 ACTIVE；
3. detect() 已内置路径探测，无需改动。
"""

from __future__ import annotations

import os
from pathlib import Path

from ..models import SessionRecord
from .base import BaseAdapter, DetectReport

APPDATA_ROAMING = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
APPDATA_LOCAL = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))


class WebChatStub(BaseAdapter):
    """网页端/客户端 Chat 的通用桩位基类。

    子类只需声明 id/name/category/paths，detect() 自动完成存在性检查。
    接入方案（按优先级）：
    a) 官方数据导出（若产品提供账户级导出功能）——最优，零风控；
       参考 deepseek-export / chatgpt-export / claude-export 的实装方式；
    b) 官方开放 API（若产品未来提供会话历史接口）——稳定；
    c) 浏览器自动化（Playwright 等）带用户登录态访问会话列表页逐页提取
       —— 兜底方案，脆弱（页面改版即失效），需要用户自行承担账号风控风险。
    """

    #: 本机候选安装/数据路径，detect 时做存在性检查。
    #: 类属性为默认值；可由 build_adapters 注入实例属性覆盖（sources.json 的 paths 配置）。
    paths: list[Path] = []
    #: 检测到路径时的说明（本地缓存的实际情况）
    found_detail: str = ""
    #: 对应 weblogin.PRODUCTS 的产品 id；None 表示无网页登录流程
    login_product_id: str | None = None

    def __init__(self, paths: list[str | Path] | None = None):
        # probe 在其他机器上发现的路径可经 sources.json 注入，保证跨设备可用
        if paths:
            self.paths = [Path(p) for p in paths]

    def login_state(self) -> dict | None:
        """登录态探测结果（见 weblogin 模块）；无对应产品时返回 None。"""
        if not self.login_product_id:
            return None
        from ..weblogin import PRODUCTS, check_product
        product = next((p for p in PRODUCTS if p.id == self.login_product_id), None)
        return check_product(product) if product else None

    def detect(self) -> DetectReport:
        found = [str(p) for p in self.paths if p.exists()]
        if not found:
            return DetectReport(self.id, self.name, "MISSING", "本机未检测到安装痕迹")
        hints = [
            "会话数据存于服务端：若产品提供账户级数据导出，优先走官方导出"
            "（参照 deepseek-export / chatgpt-export / claude-export 实装）",
            "否则需浏览器自动化带登录态提取（兜底，见 WebChatStub 文档）",
        ]
        ls = self.login_state()
        if ls:
            hints.insert(0, f"登录态: {ls['status']}（{ls['detail']}）"
                            f"——可用 python -m harvester weblogin check/prepare 处理")
        return DetectReport(
            self.id, self.name, "STUB",
            f"已检测到本机数据目录，但{self.found_detail}暂无法本地提取。",
            hints=hints,
        )

    def list_sessions(self) -> list[dict]:
        raise NotImplementedError(
            f"[{self.id}] {self.NOT_IMPL_REASON}"
        )

    def load_session(self, session_id: str) -> SessionRecord:
        raise NotImplementedError(
            f"[{self.id}] {self.NOT_IMPL_REASON}"
        )


NOT_IMPL_SUFFIX = (
    "该产品的会话正文本体在服务端，本地仅有 Chromium 缓存，"
    "无公开 API，纯本地解析不可靠（项目铁律：不硬猜）。"
    "接入方案：官方 API 或浏览器自动化带登录态（见 WebChatStub 文档）。"
)


class DeepSeekDesktopStub(WebChatStub):
    id = "deepseek-desktop"
    name = "DeepSeek 桌面版"
    category = "网页/客户端Chat"
    paths = [APPDATA_ROAMING / "@deepseek-ai" / "dsh-desktop"]
    found_detail = "其 Partitions/IndexedDB 仅为 WebView 渲染缓存，"
    login_product_id = "deepseek-desktop"
    NOT_IMPL_REASON = NOT_IMPL_SUFFIX


class YuanbaoStub(WebChatStub):
    id = "yuanbao"
    name = "腾讯元宝"
    category = "网页/客户端Chat"
    paths = [
        APPDATA_LOCAL / "com.tencent.yuanbao",
        APPDATA_LOCAL / "Yuanbao",
    ]
    found_detail = "其 EBWebView 目录仅为 WebView2 缓存，"
    login_product_id = "yuanbao"
    NOT_IMPL_REASON = NOT_IMPL_SUFFIX


class DoubaoStub(WebChatStub):
    id = "doubao"
    name = "豆包"
    category = "网页/客户端Chat"
    paths = [APPDATA_LOCAL / "Doubao", APPDATA_ROAMING / "Doubao"]
    found_detail = "本机未发现明确的会话存储目录，"
    login_product_id = "doubao"
    NOT_IMPL_REASON = NOT_IMPL_SUFFIX


class QianwenStub(WebChatStub):
    id = "qianwen"
    name = "通义千问"
    category = "网页/客户端Chat"
    paths = [APPDATA_LOCAL / "Qianwen"]
    found_detail = "其 User Data 目录仅为 Chromium 配置缓存，"
    login_product_id = "qianwen"
    NOT_IMPL_REASON = NOT_IMPL_SUFFIX


class TraeStub(WebChatStub):
    id = "trae"
    name = "Trae (CN)"
    category = "IDE助手"
    paths = [APPDATA_ROAMING / "Trae CN"]
    found_detail = (
        "其 workspaceStorage/state.vscdb 中 chat.ChatSessionStore.index 为空、"
        "无 icubeAiChat 会话键，会话记录疑似存于服务端，"
    )
    NOT_IMPL_REASON = NOT_IMPL_SUFFIX


class AutoClawUserStub(WebChatStub):
    """AutoClaw 是用户自有 Agent；-official 桌面版已实装（autoclaw.py）。
    若用户另有自部署实例/其他版本，数据位置需用户提供后接入。"""

    id = "autoclaw-custom"
    name = "AutoClaw 自部署实例"
    category = "Agent"
    paths = []
    found_detail = "未提供自部署实例的数据位置，"
    NOT_IMPL_REASON = (
        "需要用户提供数据存放位置与格式后接入；"
        "提供后可参照 autoclaw.py 的实现快速适配。"
    )
