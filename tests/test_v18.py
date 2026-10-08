# -*- coding: utf-8 -*-
"""v0.18 api-serve 决策产物端点测试：triage / reports×4 / cards。

红线延续 test_v17（只读三重锁）：
- 新端点全部复用调用方的 ro 连接——monkeypatch sqlite3.connect 的
  结构性守卫扩展到 v2 全路径（triage 内部三处查询、reports 的
  errstats/toolstats/behstats、cards 的锚点校验）；
- /api/cards 未配 --cards-root → 400（fail loud）、目录不存在 → 404；
- samples 元组在 API 层转 {sid, seq, error} 对象，JSON 形状稳定；
- 访问面钉死：cards_root 只认启动参数，不接受 URL 指定目录。
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from harvester import apiserve
from harvester.agent_suggest import build_suggestion_entries
from harvester.indexing import SCHEMA, index_session
from harvester.models import Message, SessionRecord

_ERR = "String to replace not found: X"  # 两条同文本 → 同归一 pattern


def _fixture_db(tmp: Path) -> Path:
    """3 会话 + Skill 调用对 + Edit 错误对（同 pattern ×2，跨会话）。"""
    db = tmp / "v18_test.db"
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)
    recs = [
        SessionRecord(source="src", session_id="aaa", title="错误样例会话A",
                      created_at="2026-10-07 10:00:00",
                      updated_at="2026-10-07 10:00:00",
                      messages=[Message(role="user", text="改一下文件"),
                                Message(role="assistant", text="好的")]),
        SessionRecord(source="src", session_id="bbb", title="错误样例会话B",
                      created_at="2026-10-06 10:00:00",
                      updated_at="2026-10-06 10:00:00",
                      messages=[Message(role="user", text="再改一个"),
                                Message(role="assistant", text="收到")]),
        SessionRecord(source="other-src", session_id="ccc",
                      title="无工具遥测会话",
                      created_at="2026-10-05 10:00:00",
                      updated_at="2026-10-05 10:00:00",
                      messages=[Message(role="user", text="纯聊天")]),
    ]
    for rec in recs:
        index_session(con, rec)
    steps = [
        # Skill 调用两相（G4 画像 / triage C 节素材）
        ("src:aaa", 0, "2026-10-07 10:00:01", "Skill", "call", "", "",
         json.dumps({"skill": "wechat-article-search", "args": "查饼干"})),
        ("src:aaa", 1, "2026-10-07 10:00:02", "Skill", "result", "ok", "",
         "{}"),
        # Edit call/result 错误对（G1 统计 / G2 三分类 / triage A 节素材）
        ("src:aaa", 4, "2026-10-07 10:00:03", "Edit", "call", "", "", ""),
        ("src:aaa", 5, "2026-10-07 10:00:04", "Edit", "result", "error",
         _ERR, ""),
        ("src:bbb", 0, "2026-10-06 10:00:03", "Edit", "call", "", "", ""),
        ("src:bbb", 1, "2026-10-06 10:00:04", "Edit", "result", "error",
         _ERR, ""),
    ]
    for row in steps:
        con.execute("INSERT INTO steps (sid, seq, ts, tool, phase, status, "
                    "error, detail) VALUES (?,?,?,?,?,?,?,?)", row)
    con.commit()
    con.close()
    return db


def _fixture_cards(tmp: Path) -> Path:
    """一张合规卡（锚点 session_id=aaa 真实存在）+ 一张缺字段卡。"""
    root = tmp / "cards"
    root.mkdir(exist_ok=True)
    (root / "kc-20261007-0001-ok.md").write_text(
        "---\n"
        "id: kc-20261007-0001-ok\n"
        "title: 先读再改\n"
        "type: pitfall\n"
        "tags: []\n"
        'anchors: [{session_id: "aaa", turn: null}]\n'
        "evidence: |\n"
        "  String to replace not found: X\n"
        "  Edit 报 old_string not found\n"
        "confidence: 0.8\n"
        "created: 2026-10-07\n"
        "---\n"
        "## 现象\n\nedit 报 old_string not found\n\n"
        "## 做法\n\n先 Read 再 Edit\n",
        encoding="utf-8", newline="\n")
    (root / "kc-20261007-0002-bad.md").write_text(
        "---\n"
        "id: kc-20261007-0002-bad\n"
        "type: pitfall\n"
        "---\n正文\n",
        encoding="utf-8", newline="\n")
    return root


class TestNewEndpoints(unittest.TestCase):
    """纯函数层：形状语义 + ro 连接守卫（v2 全路径）。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = _fixture_db(Path(cls.tmp.name))
        cls.cards = _fixture_cards(Path(cls.tmp.name))
        cls.con = apiserve.open_ro(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        cls.tmp.cleanup()

    # ---- triage ----

    def test_triage_shape(self):
        r = apiserve.api_triage(self.con, self.db, self.cards, {})
        self.assertEqual(r["api_version"], 1)
        self.assertEqual(r["n_sessions"], 3)
        # A 节：同 pattern ×2（min_count=2 默认），cutoff=None → 全新
        self.assertEqual(len(r["new_patterns"]), 1)
        p = r["new_patterns"][0]
        self.assertEqual(p["pattern"], _ERR)
        self.assertEqual(p["count"], 2)
        self.assertEqual(p["class"], "tool_interface")
        # 样例已转对象（JSON 形状稳定），不再是无名元组
        for s in p["samples"]:
            self.assertEqual(set(s), {"sid", "seq", "error"})
        self.assertTrue(p["known_card"])  # 卡片正文含该错误文本
        # C 节：Skill 候选
        sk = {s["skill"]: s for s in r["skills"]}
        self.assertIn("wechat-article-search", sk)
        self.assertEqual(sk["wechat-article-search"]["n_calls"], 1)
        self.assertEqual(sk["wechat-article-search"]["sample"]["sid"],
                         "src:aaa")
        # D 节：高信号会话（aaa 4 步 > bbb 2 步 > ccc 0 步）
        self.assertGreaterEqual(len(r["hot_sessions"]), 2)
        self.assertEqual(r["hot_sessions"][0]["sid"], "src:aaa")

    def test_triage_window_and_min_count(self):
        # min_count=3 → pattern 达标数不足，A/B 节皆空
        r = apiserve.api_triage(self.con, self.db, self.cards,
                                {"min_count": "3"})
        self.assertEqual(r["new_patterns"], [])
        # days=30：窗口含 10-06/10-07 错误 → A 节仍出；days 覆盖不到 10-05
        r2 = apiserve.api_triage(self.con, self.db, self.cards,
                                 {"days": "30"})
        self.assertEqual(len(r2["new_patterns"]), 1)
        self.assertIsNotNone(r2["cutoff"])

    def test_reports_errors_shape(self):
        d = apiserve.api_reports_errors(self.con, self.db, {})
        self.assertEqual(d["api_version"], 1)
        self.assertEqual(d["meta"]["error_count"], 2)
        self.assertEqual(d["by_class"]["tool_interface"], 2)
        # 两处错误都落在各自会话的最后一个 step（ratio=1.0 → 收尾桶）
        self.assertEqual(d["by_bucket"].get("收尾"), 2)
        self.assertEqual(sum(d["by_bucket"].get(k, 0) for k in
                             ("开场", "中途", "收尾")), 2)
        self.assertEqual(len(d["patterns"]), 1)
        p = d["patterns"][0]
        self.assertEqual(p["count"], 2)
        self.assertEqual(set(p["samples"][0]), {"sid", "seq", "error"})

    def test_reports_tools_shape(self):
        d = apiserve.api_reports_tools(self.con, self.db, {})
        self.assertEqual(d["api_version"], 1)
        tools = {t["tool"]: t for t in d["tools"]}
        self.assertEqual(tools["Edit"]["calls"], 2)
        self.assertEqual(tools["Edit"]["error"], 2)
        self.assertAlmostEqual(tools["Edit"]["fail_rate"], 1.0)
        # Skill 有 call+result 两步 → calls=1、success=1、零失败
        self.assertEqual(tools["Skill"]["calls"], 1)
        self.assertEqual(tools["Skill"]["success"], 1)
        self.assertEqual(tools["Skill"]["error"], 0)
        # flow：2 次失败、无重试 → 全记放弃
        self.assertEqual(d["flow"], {"retried": 0, "given_up": 2,
                                     "errors": 2})
        self.assertIn("src", d["by_source"])
        self.assertEqual(d["by_source"]["src"]["tools"][0]["tool"], "Edit")

    def test_reports_skills_shape(self):
        d = apiserve.api_reports_skills(self.con, self.db, {})
        self.assertEqual(d["api_version"], 1)
        self.assertEqual(d["n_invocations"], 1)
        self.assertEqual(d["skills"][0]["skill"], "wechat-article-search")
        self.assertEqual(d["skills"][0]["ok"], 1)
        self.assertEqual(d["skills"][0]["sids"], 1)
        self.assertTrue(d["skills"][0]["anchors"])

    def test_reports_agents_shape(self):
        # min_count=2：old_string 模板聚合 2 次达标
        d = apiserve.api_reports_agents(self.con, self.db,
                                        {"min_count": "2"})
        self.assertEqual(d["api_version"], 1)
        self.assertEqual(d["min_count"], 2)
        self.assertEqual(len(d["entries"]), 1)
        e = d["entries"][0]
        self.assertEqual(e["title"],
                         "Edit/Write 前必须先 Read 目标文件最新内容。")
        self.assertEqual(e["total"], 2)
        for s in e["samples"]:
            self.assertEqual(set(s), {"sid", "seq", "error"})
        # 默认 min_count=3：无达标条目；该 pattern 虽未达标但已被模板
        # 吞并（consumed），按口径不进 leftover——leftover 只收
        # "未命中任何模板"的模式
        d2 = apiserve.api_reports_agents(self.con, self.db, {})
        self.assertEqual(d2["entries"], [])
        self.assertEqual(d2["leftover"], [])

    # ---- sessions error_count（v2.1 additive） ----

    def test_sessions_error_count(self):
        """error_count 与 steps.status='error' 的 GROUP BY 对账；
        只加字段不改既有字段（v1 兼容）。"""
        d = apiserve.api_sessions(self.con, self.db,
                                  {"limit": "10", "offset": "0"})
        self.assertEqual(d["total"], 3)
        base = {"sid", "source", "title", "category", "model",
                "created_at", "updated_at"}
        for it in d["items"]:  # 既有字段一个不少
            self.assertTrue(base <= set(it))
            self.assertIn("error_count", it)
        cnt = {it["sid"]: it["error_count"] for it in d["items"]}
        self.assertEqual(cnt["src:aaa"], 1)
        self.assertEqual(cnt["src:bbb"], 1)
        self.assertEqual(cnt["other-src:ccc"], 0)
        # q 过滤后仍带 error_count（附加发生在分页切片之后）
        d3 = apiserve.api_sessions(self.con, self.db,
                                   {"q": "改一下文件", "limit": "10"})
        self.assertEqual(d3["items"][0]["error_count"], 1)
        # 越界分页：空切片不报错
        d4 = apiserve.api_sessions(self.con, self.db,
                                   {"limit": "2", "offset": "5"})
        self.assertEqual(d4["items"], [])

    # ---- cards ----

    def test_cards_shape(self):
        d = apiserve.api_cards(self.con, self.db, self.cards, {})
        self.assertEqual(d["api_version"], 1)
        s = d["summary"]
        self.assertEqual(s["cards"], 2)
        # v0.19：ok 卡带警告（锚点 turn: null + evidence 两行均不在会话
        # 原文——fixture 的 raw 为 JSON 形态，逐字匹配天然失配）→ 落入
        # warn；warn 按卡计数；引文核对 additive 字段生效
        self.assertEqual(s["ok"], 0)
        self.assertEqual(s["warn"], 1)
        self.assertEqual(s["error"], 1)       # bad 卡缺 title/anchors/...
        self.assertEqual(s["anchor_checked"], 1)
        self.assertEqual(s["anchor_misses"], 0)
        self.assertEqual(s["evidence_checked"], 2)
        self.assertEqual(s["evidence_misses"], 2)

    def test_cards_unconfigured_and_missing(self):
        with self.assertRaises(ValueError):
            apiserve.api_cards(self.con, self.db, None, {})
        with self.assertRaises(FileNotFoundError):
            apiserve.api_cards(self.con, self.db,
                               Path(self.tmp.name) / "nope", {})

    # ---- 结构性守卫：v2 全路径复用调用方连接 ----

    def test_v2_paths_use_caller_con_only(self):
        """v2 守卫（对应 v0.17.1 P2 修复的结构性保证）：monkeypatch
        sqlite3.connect 后，triage/reports×4/cards 全部端点必须照常工作
        ——任何一处自开连接都会在此抛 AssertionError。"""
        real_connect = sqlite3.connect

        def _guard(*args, **kwargs):
            raise AssertionError("v2 端点查询路径自开了新连接，"
                                 "authorizer 覆盖出现缺口")

        sqlite3.connect = _guard
        try:
            r = apiserve.api_triage(self.con, self.db, self.cards, {})
            self.assertEqual(r["api_version"], 1)
            self.assertEqual(
                apiserve.api_reports_errors(self.con, self.db,
                                            {})["meta"]["error_count"], 2)
            tools = apiserve.api_reports_tools(self.con, self.db, {})
            self.assertEqual(tools["flow"]["errors"], 2)
            self.assertEqual(
                apiserve.api_reports_skills(self.con, self.db,
                                            {})["n_invocations"], 1)
            agents = apiserve.api_reports_agents(self.con, self.db,
                                                 {"min_count": "2"})
            self.assertEqual(len(agents["entries"]), 1)
            cards = apiserve.api_cards(self.con, self.db, self.cards, {})
            self.assertEqual(cards["summary"]["anchor_checked"], 1)
            # v2.1：sessions 分页切片的 error_count IN 查询同受守卫约束
            sess = apiserve.api_sessions(self.con, self.db, {})
            self.assertEqual(sess["items"][0]["error_count"], 1)
        finally:
            sqlite3.connect = real_connect


class TestHttpV2(unittest.TestCase):
    """HTTP 薄壳：新端点路由 / 400/404 语义 / 形状。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = _fixture_db(Path(cls.tmp.name))
        cls.cards = _fixture_cards(Path(cls.tmp.name))
        import http.server
        cls.httpd = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            apiserve.make_handler(cls.db, None, cls.cards))
        cls.port = cls.httpd.socket.getsockname()[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever,
                                      daemon=True)
        cls.thread.start()
        # 未配置 cards_root 的实例（/api/cards → 400）
        cls.httpd2 = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            apiserve.make_handler(cls.db, None, None))
        cls.port2 = cls.httpd2.socket.getsockname()[1]
        cls.thread2 = threading.Thread(target=cls.httpd2.serve_forever,
                                       daemon=True)
        cls.thread2.start()

    @classmethod
    def tearDownClass(cls):
        for h in (cls.httpd, cls.httpd2):
            h.shutdown()
            h.server_close()  # 收干净 listening socket，防 ResourceWarning
        cls.tmp.cleanup()

    def _get(self, path: str, port: int | None = None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port or self.port}{path}")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def test_triage_http(self):
        status, body = self._get("/api/triage?days=30&min_count=2")
        self.assertEqual(status, 200)
        self.assertEqual(body["api_version"], 1)
        self.assertEqual(len(body["new_patterns"]), 1)

    def test_reports_http(self):
        for path, key in [("/api/reports/errors?days=30", "by_class"),
                          ("/api/reports/tools", "tools"),
                          ("/api/reports/skills", "skills"),
                          ("/api/reports/agents?min_count=2", "entries")]:
            status, body = self._get(path)
            self.assertEqual(status, 200, path)
            self.assertEqual(body["api_version"], 1, path)
            self.assertIn(key, body, path)

    def test_cards_http_200(self):
        status, body = self._get("/api/cards")
        self.assertEqual(status, 200)
        self.assertEqual(body["summary"]["cards"], 2)

    def test_cards_400_when_unconfigured(self):
        status, body = self._get("/api/cards", port=self.port2)
        self.assertEqual(status, 400)
        self.assertIn("--cards-root", body["error"])

    def test_unknown_route_still_404(self):
        status, _ = self._get("/api/reports/nope")
        self.assertEqual(status, 404)


class TestSuggestParity(unittest.TestCase):
    """数据层/渲染层同构：build_suggestion_entries 与 build_suggestions
    必须消费同一构建结果（防止两出口口径漂移）。"""

    def test_entries_equal_render_source(self):
        errors = [{"sid": "s:a", "seq": 1, "ts": "t", "tool": "Edit",
                   "error": _ERR, "source": "src",
                   "class": "tool_interface", "pattern": _ERR,
                   "bucket": "开场"},
                  {"sid": "s:b", "seq": 2, "ts": "t", "tool": "Edit",
                   "error": _ERR, "source": "src",
                   "class": "tool_interface", "pattern": _ERR,
                   "bucket": "中途"}]
        built = build_suggestion_entries(errors, min_count=2)
        self.assertEqual(len(built["entries"]), 1)
        self.assertEqual(built["entries"][0]["total"], 2)
        self.assertEqual(built["leftover"], [])
        # 渲染层不重复计数：markdown 里出现"实测 2 次"
        from harvester.agent_suggest import build_suggestions
        md = build_suggestions(errors, min_count=2)
        self.assertIn("实测 2 次", md)


if __name__ == "__main__":
    unittest.main()
