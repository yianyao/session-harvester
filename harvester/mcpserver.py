# -*- coding: utf-8 -*-
"""MCP server（stdio, newline-delimited JSON-RPC 2.0）——零第三方依赖。

对标 ai-hist-mcp：把本套件的历史索引暴露为 MCP 工具，任意支持 MCP 的
agent（Claude Code / Cursor / AutoClaw / VS Code 等）可在运行时直查
会话历史。接入方式见 README「MCP 接入」节。

工具集（4 个）：
- list_sessions()                       → 纲要（no/source/title/date/preview）
- search_history(query, limit, source)  → FTS5 全文检索（需先 index）
- read_session(no, turn)                → 分层读取（turn=all/N/last）
- pack_context(nos, tokens, question)   → 交接包

实现说明：MCP stdio 传输 = 每行一个 JSON-RPC 消息。只实现本套件需要的
最小方法集：initialize / notifications/* / tools/list / tools/call / ping。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import __version__
from .adapters import load_sources
from .exporter import parse_selection
from .indexing import fts5_available, search, sessions_in_db
from .outline import scan_all
from .pack import build_pack
from .reader import render_read

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "session-harvester", "version": __version__}


class HarvesterMcpServer:
    def __init__(self, sources_path: str | None = None, db_path: str | None = None):
        self._sources = load_sources(sources_path) if sources_path else {}
        self._db = Path(db_path) if db_path else None
        self._cache: dict | None = None   # (adapters, items, reports)

    # ---- 数据后端 ----
    def _backend(self) -> tuple[dict, list[dict]]:
        # 数据快照语义：缓存建于首次调用并长驻——server 运行期间新入库的
        # 会话不可见（索引库本身是整库重建口径，重跑 sync 后重启 server 即可）。
        if self._cache is None:
            adapters_list, items, _rep = scan_all(sources=self._sources)
            self._cache = ({ad.id: ad for ad in adapters_list}, items)
        return self._cache

    def _load_by_no(self, no: int):
        adapters, items = self._backend()
        for it in items:
            if it["no"] == no:
                return adapters[it["adapter"]].load_session(it["session_id"]), it
        raise ValueError(f"纲要中无序号 {no}（当前共 {len(items)} 条，"
                         f"如纲要过期请重新 scan）")

    # ---- 工具实现（返回文本内容） ----
    def tool_list_sessions(self, arguments: dict) -> str:
        _, items = self._backend()
        limit = int(arguments.get("limit", 100))
        out = ["| no | source | date | title | msgs | preview |",
               "|---:|---|---|---|---:|---|"]
        for it in items[:limit]:
            date = (it.get("created_at") or "")[:10]
            title = " ".join((it.get("title") or "").split())[:50]
            prev = " ".join((it.get("preview") or "").split())[:50]
            out.append(f"| {it['no']} | {it['adapter']} | {date} | "
                       f"{title} | {it.get('message_count') or '-'} | {prev} |")
        return "\n".join(out)

    def tool_search_history(self, arguments: dict) -> str:
        if not self._db or not self._db.exists():
            return "索引库不存在。先运行: python -m harvester index --all（或 --from exports）"
        rows = search(self._db, arguments["query"],
                      limit=int(arguments.get("limit", 10)),
                      source=arguments.get("source"))
        if not rows:
            return "无匹配结果。"
        out = [f"共 {len(rows)} 条命中（库内 {sessions_in_db(self._db)} 会话）：", ""]
        for r in rows:
            out.append(f"- [{r['source']}] {r['title']}")
            out.append(f"  {r['snippet']}")
        return "\n".join(out)

    def tool_read_session(self, arguments: dict) -> str:
        rec, _it = self._load_by_no(int(arguments["no"]))
        turn = arguments.get("turn", 1)
        text, n_turns = render_read(rec, turn)
        return text + f"\n\n(共 {n_turns} 个回合；--turn N 逐级下钻，turn=all 全量)"

    def tool_pack_context(self, arguments: dict) -> str:
        spec = arguments["nos"]
        _, items = self._backend()
        nos, dropped = parse_selection(str(spec), len(items))
        if dropped:
            nos_txt = f"\n\n（注意：序号 {dropped} 越界，已忽略。当前共 {len(items)} 个会话。）"
        else:
            nos_txt = ""
        # build_pack 期望 (record, 纲要序号)；_load_by_no 返回 (rec, item)
        recs = [(self._load_by_no(n)[0], n) for n in nos]
        return build_pack(recs,
                          total_budget=int(arguments.get("tokens", 2000)),
                          question=arguments.get("question")) + nos_txt

    # ---- MCP 协议 ----
    def _tools_manifest(self) -> list[dict]:
        return [
            {"name": "list_sessions", "description":
                "列出全部已索引会话纲要（no/来源/日期/标题/概要）",
             "inputSchema": {"type": "object", "properties": {
                 "limit": {"type": "integer", "default": 100}}}},
            {"name": "search_history", "description":
                "FTS5 全文检索历史会话（先运行 harvester index 建库）",
             "inputSchema": {"type": "object", "required": ["query"],
                             "properties": {
                                 "query": {"type": "string"},
                                 "limit": {"type": "integer", "default": 10},
                                 "source": {"type": "string"}}}},
            {"name": "read_session", "description":
                "按纲要序号读会话；turn=1..N 逐回合下钻，turn=all 全量",
             "inputSchema": {"type": "object", "required": ["no"],
                             "properties": {
                                 "no": {"type": "integer"},
                                 "turn": {"type": "string"}}}},
            {"name": "pack_context", "description":
                "把若干会话打包为 token 预算内的上下文交接包",
             "inputSchema": {"type": "object", "required": ["nos"],
                             "properties": {
                                 "nos": {"type": "string",
                                         "description": '如 "3,5-9"'},
                                 "tokens": {"type": "integer", "default": 2000},
                                 "question": {"type": "string"}}}},
        ]

    def _dispatch(self, name: str, arguments: dict) -> str:
        handlers = {
            "list_sessions": self.tool_list_sessions,
            "search_history": self.tool_search_history,
            "read_session": self.tool_read_session,
            "pack_context": self.tool_pack_context,
        }
        fn = handlers.get(name)
        if fn is None:
            raise ValueError(f"未知工具: {name!r}")
        return fn(arguments or {})

    def handle(self, msg: dict) -> dict | None:
        method = msg.get("method")
        mid = msg.get("id")
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO}}
        if method == "ping":
            return {"jsonrpc": "2.0", "id": mid, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": mid,
                    "result": {"tools": self._tools_manifest()}}
        if method == "tools/call":
            params = msg.get("params") or {}
            try:
                text = self._dispatch(params.get("name"),
                                      params.get("arguments") or {})
                return {"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": text}],
                    "isError": False}}
            except KeyError as e:
                return self._tool_error(mid, f"工具参数或数据缺失: {e}")
            except (ValueError, IndexError) as e:
                return self._tool_error(mid, str(e))
            except Exception as e:  # noqa: BLE001 - 工具错误以 MCP isError 返回
                return self._tool_error(mid, f"工具执行失败: {e}")
        if mid is not None:  # 未知请求方法
            return {"jsonrpc": "2.0", "id": mid, "error":
                    {"code": -32601, "message": f"method not found: {method}"}}
        return None  # notification，无需回复

    @staticmethod
    def _tool_error(mid, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": message}], "isError": True}}

    def serve(self) -> None:
        """主循环：逐行读请求，逐行写响应。EOF 退出。"""
        if self._db is not None and not self._db.exists():
            print("[harvester-mcp] 提示: 索引库不存在，search_history 不可用;"
                  " 先运行 harvester index", file=sys.stderr)
        if not fts5_available():
            print("[harvester-mcp] 警告: 当前 SQLite 无 FTS5，search 不可用",
                  file=sys.stderr)
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            resp = self.handle(msg)
            if resp is not None:
                sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                sys.stdout.flush()


def serve(sources_path: str | None, db_path: str | None) -> int:
    HarvesterMcpServer(sources_path, db_path).serve()
    return 0
