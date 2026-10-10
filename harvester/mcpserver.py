# -*- coding: utf-8 -*-
"""MCP server（stdio, newline-delimited JSON-RPC 2.0）——零第三方依赖。

对标 ai-hist-mcp：把本套件的历史索引暴露为 MCP 工具，任意支持 MCP 的
agent（Claude Code / Cursor / AutoClaw / VS Code 等）可在运行时直查
会话历史。接入方式见 README「MCP 接入」节。

工具集（**10 个**）：
- 会话面（本地纲要/打包，4 个）：
  - list_sessions()                       → 纲要（no/source/title/date/preview）
  - search_history(query, limit, source)  → FTS5 全文检索（需先 index）
  - read_session(no, turn)                → 分层读取（turn=all/N/last）
  - pack_context(nos, tokens, question)   → 交接包
- 进化数据面（v0.45，6 个；**与 HTTP `/api/*` 同源**，见 `TOOL_SOURCES`）：
  - topic_list()                          → 主题注册表 + 每条主题的链数（= /api/topics）
  - topic_export(topic_id)                → 主题结构化包（= `topic export` 的 JSON 出口）
  - chain_read(topic_id)                  → 该主题的 chain 长文结构化（= /api/topic/<id>/chain）
  - suggest_list(status?)                 → 建议台账（suggestions_meta.db 的 key→status）
  - cards_list(limit?)                    → 卡片清单（frontmatter 摘要，不校验、不读正文）
  - artifacts_list(sid?, limit?)          → 产物清单（元数据＋体量；正文用 show_artifacts）

**为什么要这 6 个**：此前 D/E 两层数据（主题/链/建议/卡片/产物）Agent 要消费
只能绕道 HTTP 服务或 JSON 文件——而"Agent 随手可用自己进化的数据"正是本项目的
目的。它们全部**只读**，且**复用** `apiserve` / `topicexport` 的现成查询，不自造字段
（additive 红线：MCP 返回的 dict 必须与 HTTP 出口同源，见 `tests/test_v45_mcp_tools.py`）。

实现说明：MCP stdio 传输 = 每行一个 JSON-RPC 消息。只实现本套件需要的
最小方法集：initialize / notifications/* / tools/list / tools/call / ping。

已知限制：数据快照语义——会话纲要缓存建于首次调用并长驻，server 运行期间
新入库的会话不可见（整库重建口径：重跑 sync 后重启 server 即可）；而
主题/链/建议/卡片/产物这 6 个工具**每次调用现读**（无缓存）。
"""

from __future__ import annotations

import json
import sqlite3
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

#: 工具 → 数据来源（漂移门的依据，**不进 MCP 线上报文**）：
#: "local" = 本地纲要/打包；"/api/..." = 与 HTTP 端点**同源同载荷**。
#: `tests/test_v45_mcp_tools.py` 双向核对：这里的名字必须与 `_tools_manifest()`
#: 完全一致，且每个 `/api/...` 都必须在 `apiserve.py` 里真实存在。
TOOL_SOURCES: dict[str, str] = {
    "list_sessions": "local",
    "search_history": "local",
    "read_session": "local",
    "pack_context": "local",
    "topic_list": "/api/topics",
    "topic_export": "local",
    "chain_read": "/api/topic",
    "suggest_list": "/api/reports/agents",
    "cards_list": "/api/cards",
    "artifacts_list": "local",
}


class HarvesterMcpServer:
    def __init__(self, sources_path: str | None = None, db_path: str | None = None,
                 *, topics_meta: str | None = None,
                 chain_root: str | None = None,
                 cards_root: str | None = None,
                 artifacts_meta: str | None = None,
                 suggestions_meta: str | None = None):
        self._sources = load_sources(sources_path) if sources_path else {}
        self._db = Path(db_path) if db_path else None
        self._cache: dict | None = None   # (adapters, items, reports)
        # v0.45：进化数据面（主题/链/建议/卡片/产物）的路径配置。
        # 缺省与 `api-serve` 的缺省保持一致（chain_root 落在知识库 topics/）。
        self._topics_meta = Path(topics_meta) if topics_meta else None
        self._chain_root = (Path(chain_root) if chain_root
                            else Path.home() / ".workbuddy" / "knowledge" / "topics")
        self._cards_root = Path(cards_root) if cards_root else None
        self._artifacts_meta = Path(artifacts_meta) if artifacts_meta else None
        self._suggestions_meta = (Path(suggestions_meta) if suggestions_meta
                                  else None)

    # ---- 通用小件 ----
    def _need_db(self) -> sqlite3.Connection:
        """只读连接；库不存在就 fail loud（不返回半个空结果）。"""
        if not self._db or not self._db.exists():
            raise ValueError("索引库不存在。先运行: python -m harvester index "
                             "（或 sync/update）")
        return sqlite3.connect(f"file:{self._db}?mode=ro", uri=True)

    @staticmethod
    def _json_text(obj) -> str:
        """机读出口：稳定序列化（键序固定、UTF-8 不转义）。"""
        return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)

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

    # ---- 进化数据面（v0.45；与 HTTP /api/* 同源，见 TOOL_SOURCES） ----
    def tool_topic_list(self, arguments: dict) -> str:
        """主题注册表 + 每条主题的链数（载荷与 `/api/topics` 逐字段一致）。"""
        from .apiserve import api_topics
        con = self._need_db()
        try:
            data = api_topics(con, self._db, self._topics_meta, self._chain_root)
        finally:
            con.close()
        return self._json_text(data)

    def tool_topic_export(self, arguments: dict) -> str:
        """主题结构化包（与 CLI `topic export --out` 的 JSON 出口同一函数）。"""
        from .topicexport import render_topic_json, topic_bundle
        if not self._topics_meta:
            raise ValueError("未配置主题注册表（启动时给 --topics-meta）")
        bundle = topic_bundle(self._topics_meta, self._db, arguments["topic_id"],
                              chain_root=self._chain_root)
        return render_topic_json(bundle)

    def tool_chain_read(self, arguments: dict) -> str:
        """主题的 chain 长文结构化（= `/api/topic/<id>/chain`，多链时含 chains[]）。"""
        from .apiserve import api_topic_chain
        con = self._need_db()
        try:
            doc = api_topic_chain(con, self._db, arguments["topic_id"],
                                  self._topics_meta, self._chain_root)
        finally:
            con.close()
        if doc is None:
            raise ValueError(f"主题 {arguments['topic_id']!r} 没有可读的 chain"
                             "（检查 --topics-meta / --chain-root，或该主题确实未写链）")
        return self._json_text(doc)

    def tool_suggest_list(self, arguments: dict) -> str:
        """建议台账：`suggestions_meta.db` 的 建议句 → 状态（adopted/rejected）。"""
        from .suggestmeta import load_statuses
        statuses = load_statuses(self._suggestions_meta)
        want = arguments.get("status")
        rows = [{"key": k, "status": v} for k, v in sorted(statuses.items())
                if not want or v == want]
        counts: dict[str, int] = {}
        for v in statuses.values():
            counts[v] = counts.get(v, 0) + 1
        return self._json_text({
            "meta": str(self._suggestions_meta) if self._suggestions_meta else None,
            "counts": counts, "returned": len(rows), "entries": rows})

    def tool_cards_list(self, arguments: dict) -> str:
        """卡片清单（frontmatter 摘要）——只列不校验，正文另行读取。"""
        from .cards import list_cards
        if not self._cards_root:
            raise ValueError("未配置卡片目录（启动时给 --cards-root）")
        rows = list_cards(self._cards_root)
        limit = int(arguments.get("limit", 100))
        return self._json_text({
            "root": str(self._cards_root), "total": len(rows),
            "returned": min(limit, len(rows)), "cards": rows[:limit]})

    def tool_artifacts_list(self, arguments: dict) -> str:
        """产物清单（元数据＋体量，不含正文）。按 sid 取正文用 show_artifacts。"""
        from .artifacts import list_artifacts
        meta = self._artifacts_meta
        if not meta:
            raise ValueError("未配置产物库（启动时给 --artifacts-meta）")
        limit = int(arguments.get("limit", 200))
        rows = list_artifacts(meta, limit=limit, sid=arguments.get("sid"))
        return self._json_text({
            "meta": str(meta), "returned": len(rows), "artifacts": rows})

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
            {"name": "topic_list", "description":
                "主题注册表：id/名称/成员数/关键词/该主题有几条 chain（与 HTTP "
                "/api/topics 同源）",
             "inputSchema": {"type": "object", "properties": {}}},
            {"name": "topic_export", "description":
                "主题结构化包（harvester.topic/1）：成员、分诊、链锚点、素材与统计",
             "inputSchema": {"type": "object", "required": ["topic_id"],
                             "properties": {"topic_id": {"type": "string"}}}},
            {"name": "chain_read", "description":
                "读某主题的 chain 长文结构化（多链时含 chains[]；无链则报错）",
             "inputSchema": {"type": "object", "required": ["topic_id"],
                             "properties": {"topic_id": {"type": "string"}}}},
            {"name": "suggest_list", "description":
                "建议台账：建议句 → adopted/rejected（可只取某状态）",
             "inputSchema": {"type": "object", "properties": {
                 "status": {"type": "string",
                            "enum": ["pending", "adopted", "rejected"]}}}},
            {"name": "cards_list", "description":
                "卡片清单（frontmatter 摘要：id/标题/类型/置信度/锚点数）",
             "inputSchema": {"type": "object", "properties": {
                 "limit": {"type": "integer", "default": 100}}}},
            {"name": "artifacts_list", "description":
                "产物清单（元数据＋体量，不含正文；可按 sid 过滤）",
             "inputSchema": {"type": "object", "properties": {
                 "sid": {"type": "string"},
                 "limit": {"type": "integer", "default": 200}}}},
        ]

    def _dispatch(self, name: str, arguments: dict) -> str:
        handlers = {
            "list_sessions": self.tool_list_sessions,
            "search_history": self.tool_search_history,
            "read_session": self.tool_read_session,
            "pack_context": self.tool_pack_context,
            "topic_list": self.tool_topic_list,
            "topic_export": self.tool_topic_export,
            "chain_read": self.tool_chain_read,
            "suggest_list": self.tool_suggest_list,
            "cards_list": self.tool_cards_list,
            "artifacts_list": self.tool_artifacts_list,
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


def serve(sources_path: str | None, db_path: str | None, **kwargs) -> int:
    """stdio 主循环入口。kwargs 透传进化数据面的路径配置（见类签名）。"""
    HarvesterMcpServer(sources_path, db_path, **kwargs).serve()
    return 0
