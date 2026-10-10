# -*- coding: utf-8 -*-
"""v0.45 MCP 消费面测试（B 组）——**本文件不依赖 PyYAML**（另有
`test_v45_mcp_chain_tools.py` 承载需要读 chain frontmatter 的两条）。

三件事：
1. **工具清单 ↔ 后端能力漂移门**：`TOOL_SOURCES` 与 `_tools_manifest()` 双向一致，
   且每个声明为 `/api/...` 的来源都必须在 `apiserve.py` 里**真实存在**
   （从源码 AST 抽，不跑服务、不重构分派）。
2. **MCP ↔ HTTP 同源对账**：`topic_list` / `cards_list` 的载荷必须与
   `apiserve` 同名函数逐字段一致（保证 MCP 侧**没有二次加工**）。
3. 元测试：漂移检查器对三类输入（工具数不足 / 两向不一致 / 幽灵端点）**必须报错**
   ——否则这道门是恒真的。

判据都能说清何时会红：删掉一个 MCP 工具、把某个工具标成不存在的端点、
或让 MCP 自己加工 HTTP 载荷，对应断言即红。
"""
from __future__ import annotations

import ast
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester import mcpserver as mcp
from harvester.artifacts import ensure_artifacts_db
from harvester.indexing import SCHEMA, index_session
from harvester.models import Message, SessionRecord
from harvester.suggestmeta import set_status
from harvester.topics import add_members, ensure_topics_db, register_topic

REPO = Path(__file__).resolve().parents[1]

CARD = """\
---
id: kc-20261010-001-demo
title: 示例卡片
type: technique
tags: [叙事, 节奏]
confidence: high
anchors:
  - session_id: "src:s1"
    turn: 1
---

正文。
"""


def api_capabilities(src: str | None = None) -> set[str]:
    """从 `apiserve.py` 源码里抽 `/api/...` 路径字面量（AST 口径）。

    为什么用 AST 而不是正则：正则会把注释与文档里的路径也算进去；
    为什么不引入 `ROUTES` 常量表：那要重排 30+ 端点的 `if/elif` 分派，
    对只读服务是无谓的变更风险（additive 优先）。
    """
    if src is None:
        src = (REPO / "harvester" / "apiserve.py").read_text(encoding="utf-8")
    caps: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            if v.startswith("/api/"):
                caps.add(v.split("(")[0].rstrip("/"))
    return caps


def manifest_problems(names: list[str], sources: dict[str, str],
                      caps: set[str], min_tools: int = 10) -> list[str]:
    """漂移检查器（可对合成输入调用，便于元测试）。"""
    out: list[str] = []
    if len(names) < min_tools:
        out.append(f"工具数不足：{len(names)} < {min_tools}")
    for n in sorted(set(names) - set(sources)):
        out.append(f"清单里有、来源表里没有：{n}")
    for n in sorted(set(sources) - set(names)):
        out.append(f"来源表里有、清单里没有：{n}")
    for n, src in sorted(sources.items()):
        if src == "local":
            continue
        if src.rstrip("/") not in caps:
            out.append(f"{n} 声明来源 {src}，但 apiserve 里没有这个端点")
    return out


class TestToolManifestDrift(unittest.TestCase):

    def setUp(self):
        self.srv = mcp.HarvesterMcpServer()
        self.names = [t["name"] for t in self.srv._tools_manifest()]
        self.caps = api_capabilities()

    def test_capability_extraction_is_sane(self):
        """抽取本身要 sanity：抽不到就永远绿（v0.44 元测试的同一教训）。"""
        self.assertGreaterEqual(len(self.caps), 10, self.caps)
        for must in ("/api/meta", "/api/topics", "/api/cards", "/api/keywords",
                     "/api/triage", "/api/reports/errors"):
            self.assertIn(must, self.caps)

    def test_manifest_matches_source_table(self):
        self.assertEqual(manifest_problems(self.names, mcp.TOOL_SOURCES,
                                           self.caps), [])

    def test_new_six_tools_are_present(self):
        """六件新工具一个都不能少（防止"因为抽不到而永远绿"）。"""
        for name in ("topic_list", "topic_export", "chain_read",
                     "suggest_list", "cards_list", "artifacts_list"):
            self.assertIn(name, self.names)

    def test_dispatch_covers_every_manifest_tool(self):
        """清单里的每个工具都必须真的有 handler（否则是"说了不做"）。

        这里只证明"名字能被分派"：缺参数抛 KeyError/ValueError 属正常，
        但**不许**出现"未知工具"。
        """
        for name in self.names:
            try:
                self.srv._dispatch(name, {})
            except ValueError as e:
                self.assertNotIn("未知工具", str(e), name)
            except (KeyError, IndexError):
                pass

    def test_checker_can_fail(self):
        """元测试：三类漂移都必须被检查器抓住。"""
        caps = {"/api/topics"}
        ten = {f"t{i}": "local" for i in range(10)}
        # ① 工具数不足
        self.assertTrue(any("工具数不足" in p for p in manifest_problems(
            ["a", "b"], {"a": "local", "b": "local"}, caps)))
        # ② 两向不一致（各一小例）
        self.assertTrue(any("清单里有、来源表里没有" in p
                            for p in manifest_problems(
                                [*ten, "ghost"], ten, caps)))
        self.assertTrue(any("来源表里有、清单里没有" in p
                            for p in manifest_problems(
                                list(ten), {**ten, "ghost": "local"}, caps)))
        # ③ 幽灵端点
        self.assertTrue(any("没有这个端点" in p for p in manifest_problems(
            list(ten), {**ten, "t0": "/api/ghost"}, caps)))
        # 反向对照：全合规时必须是空（否则门是恒红的）
        self.assertEqual(manifest_problems(
            list(ten), {**ten, "t0": "/api/topics"}, caps), [])


class TestEvolutionTools(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "harvester.db"
        con = sqlite3.connect(str(self.db))
        con.executescript(SCHEMA)
        index_session(con, SessionRecord(
            source="src", session_id="s1", title="甲会话",
            created_at="2026-01-01 10:00:00", updated_at="2026-01-01 10:00:00",
            messages=[Message(role="user", text="丁樾瘫坐在椅子上回气。"),
                      Message(role="assistant", text="收到。")]))
        con.commit()
        con.close()
        self.topics = ensure_topics_db(root / "topics_meta.db")
        tid = register_topic(self.topics, "主题甲", keywords=["节奏"])
        add_members(self.topics, tid, ["src:s1"], evidence="测试")
        self.cards = root / "cards"
        self.cards.mkdir()
        (self.cards / "kc-demo.md").write_text(CARD, encoding="utf-8")
        (self.cards / "README.md").write_text("说明，不该出现\n",
                                              encoding="utf-8")
        self.artifacts = root / "artifacts_meta.db"
        ensure_artifacts_db(self.artifacts)
        acon = sqlite3.connect(str(self.artifacts))
        acon.execute("INSERT INTO artifacts (sid, seq, ts, tool, file_path, "
                     "old_text, new_text) VALUES (?,?,?,?,?,?,?)",
                     ("src:s1", 0, "2026-01-01T10:00:00", "Edit", "a.md",
                      "old", "new content"))
        acon.commit()
        acon.close()
        self.sugg = root / "suggestions_meta.db"
        set_status(self.sugg, "第一条建议。", "adopted")
        set_status(self.sugg, "第二条建议。", "rejected")
        self.srv = mcp.HarvesterMcpServer(
            None, str(self.db), topics_meta=str(self.topics),
            chain_root=str(root / "chains"), cards_root=str(self.cards),
            artifacts_meta=str(self.artifacts),
            suggestions_meta=str(self.sugg))

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def _call(self, name: str, args: dict | None = None):
        return json.loads(self.srv._dispatch(name, args or {}))

    def _ro(self):
        """只读连接（**带 row_factory**：HTTP 层就是这么建的，别少这一步）。"""
        con = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        return con

    def test_topic_list_matches_http_payload(self):
        """同源对账：MCP 的 topic_list 必须与 `apiserve.api_topics` 逐字段一致。"""
        from harvester.apiserve import api_topics
        con = self._ro()
        try:
            http = api_topics(con, self.db, self.topics,
                              Path(self.tmp.name) / "chains")
        finally:
            con.close()
        self.assertEqual(self._call("topic_list"), http)
        self.assertEqual(len(http["topics"]), 1)

    def test_suggest_list_reports_ledger_and_filters(self):
        d = self._call("suggest_list")
        self.assertEqual(d["counts"], {"adopted": 1, "rejected": 1})
        self.assertEqual(d["returned"], 2)
        only = self._call("suggest_list", {"status": "adopted"})
        self.assertEqual([e["key"] for e in only["entries"]], ["第一条建议。"])

    def test_cards_list_skips_index_readme_and_matches_http_count(self):
        from harvester.apiserve import api_cards
        d = self._call("cards_list")
        self.assertEqual(d["total"], 1)              # README.md 不算卡片
        self.assertEqual(d["cards"][0]["id"], "kc-20261010-001-demo")
        self.assertEqual(d["cards"][0]["anchor_count"], 1)
        con = self._ro()
        try:
            http = api_cards(con, self.db, self.cards, {})
        finally:
            con.close()
        self.assertEqual(d["total"], http["summary"]["cards"])

    def test_artifacts_list_gives_sizes_not_bodies(self):
        d = self._call("artifacts_list")
        row = d["artifacts"][0]
        self.assertEqual((row["sid"], row["tool"]), ("src:s1", "Edit"))
        self.assertEqual((row["old_chars"], row["new_chars"]), (3, 11))
        self.assertNotIn("new_text", row)            # 列表不带正文

    def test_tools_that_need_config_fail_loud(self):
        """没配路径时必须报错（不是返回空表让人误以为"没有数据"）。"""
        bare = mcp.HarvesterMcpServer(None, str(self.db))
        for name, args in (("topic_export", {"topic_id": "tp-x"}),
                           ("cards_list", {}),
                           ("artifacts_list", {})):
            with self.assertRaises(ValueError, msg=name):
                bare._dispatch(name, args)

    def test_unknown_tool_is_error(self):
        with self.assertRaises(ValueError):
            self.srv._dispatch("no_such_tool", {})

    def test_tools_call_wraps_in_mcp_envelope(self):
        resp = self.srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                "params": {"name": "topic_list", "arguments": {}}})
        self.assertFalse(resp["result"]["isError"])
        self.assertEqual(json.loads(resp["result"]["content"][0]["text"]),
                         self._call("topic_list"))


if __name__ == "__main__":
    unittest.main()
