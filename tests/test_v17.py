# -*- coding: utf-8 -*-
"""v0.17 api-serve 测试：只读三重锁 / 启动自检 / 端点语义 / 安全守卫。

口径红线（对账 ADAPTER_CONTRACT）：
- 只读 = mode=ro + authorizer 白名单双保险，INSERT 必须被内核拒绝；
- 关键字必须走 indexing.search()（bigram 生效），中文 2 字词 100% 命中；
- model 为 NULL 的会话在 facets 归"（未知）"桶，不假装可筛；
- 非回环 host 必须配 --token，否则拒绝启动（防裸奔）。
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
from harvester.indexing import SCHEMA, index_session, search
from harvester.models import Message, SessionRecord

_TMP = Path(__file__).parent / "_tmp"


def _record(sid_suffix: str, title: str, msgs: list[Message],
            source: str = "src", model: str | None = None,
            updated: str = "2026-10-07 10:00:00") -> SessionRecord:
    """sid 由索引层派生为 f"{source}:{session_id}"。"""
    return SessionRecord(
        source=source, session_id=sid_suffix, title=title,
        created_at=updated, updated_at=updated, messages=msgs,
        extra={} if model is None else {"model": model})


def _fixture_db(tmp: Path) -> Path:
    db = tmp / "api_test.db"
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)
    recs = [
        _record("aaa", "中文检索饼干会话", [
            Message(role="user", text="帮我查一下饼干的中文做法"),
            Message(role="assistant", text="好的，饼干做法如下"),
            Message(role="note", text="[tool] read"),
        ], model="glm-5.3-flash"),
        _record("bbb", "英文 only session", [
            Message(role="user", text="hello world about cookies"),
            Message(role="assistant", text="sure thing"),
        ], source="other-src", updated="2026-10-05 08:00:00"),
        _record("ccc", "无模型信息会话", [
            Message(role="user", text="另一条饼干相关提问"),
        ]),
    ]
    for rec in recs:
        index_session(con, rec)
    # skill 调用 fixture：call+result 两相（behstats 口径）
    con.execute("INSERT INTO steps (sid, seq, ts, tool, phase, status, "
                "error, detail) VALUES (?,?,?,?,?,?,?,?)",
                ("src:aaa", 0, "2026-10-07 10:00:01", "Skill", "call",
                 "", "", json.dumps({"skill": "wechat-article-search",
                                     "args": "查饼干"})))
    con.execute("INSERT INTO steps (sid, seq, ts, tool, phase, status, "
                "error, detail) VALUES (?,?,?,?,?,?,?,?)",
                ("src:aaa", 1, "2026-10-07 10:00:02", "Skill", "result",
                 "ok", "", "{}"))
    con.commit()
    con.close()
    return db


class TestReadonly(unittest.TestCase):
    """红线 1：mode=ro + authorizer 双保险。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _fixture_db(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_insert_denied_by_ro_and_authorizer(self):
        con = apiserve.open_ro(self.db)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                con.execute("INSERT INTO sessions (sid) VALUES ('x')")
        finally:
            con.close()

    def test_pragma_write_denied(self):
        con = apiserve.open_ro(self.db)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                con.execute("PRAGMA journal_mode=DELETE")
        finally:
            con.close()

    def test_read_still_works(self):
        con = apiserve.open_ro(self.db)
        try:
            n = con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            self.assertEqual(n, 3)
        finally:
            con.close()


class TestSelfCheck(unittest.TestCase):
    """红线 2：自检 fail loud。"""

    def test_good_db_passes(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            db = _fixture_db(Path(tmp.name))
            self.assertEqual(apiserve.self_check(db), [])
        finally:
            tmp.cleanup()

    def test_missing_column_fails_loud(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            db = _fixture_db(Path(tmp.name))
            con = sqlite3.connect(str(db))
            con.execute("ALTER TABLE sessions DROP COLUMN model")  # 3.35+
            con.commit()
            con.close()
            problems = apiserve.self_check(db)
            self.assertTrue(any("model" in p for p in problems),
                            f"应报缺列，实际: {problems}")
        finally:
            tmp.cleanup()

    def test_host_guard(self):
        # 非回环 + 无 token → 拒绝；有 token → 通过；回环恒通过
        self.assertIsNotNone(apiserve._check_host("0.0.0.0", None))
        self.assertIsNone(apiserve._check_host("0.0.0.0", "secret"))
        self.assertIsNone(apiserve._check_host("127.0.0.1", None))


class TestEndpoints(unittest.TestCase):
    """端点语义（纯函数层，连接由测试给）。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = _fixture_db(Path(cls.tmp.name))
        cls.con = apiserve.open_ro(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        cls.tmp.cleanup()

    def test_meta(self):
        meta = apiserve.api_meta(self.con, self.db)
        self.assertEqual(meta["api_version"], 1)
        self.assertTrue(meta["readonly"])
        self.assertEqual(meta["sessions"], 3)

    def test_facets_model_unknown_bucket(self):
        facets = apiserve.api_facets(self.con, self.db)
        models = {m["name"]: m["count"] for m in facets["models"]}
        self.assertEqual(models.get("（未知）"), 2)  # bbb/ccc 均无模型信息
        self.assertEqual(models.get("glm-5.3-flash"), 1)
        skills = {s["name"]: s["count"] for s in facets["skills"]}
        self.assertEqual(skills.get("wechat-article-search"), 1)

    def test_sessions_filter_and_bigram_cjk(self):
        # 中文 2 字词必命中（bigram 路径生效）
        out = apiserve.api_sessions(self.con, self.db, {"q": "饼干"})
        self.assertEqual(out["total"], 2)
        sids = {r["sid"] for r in out["items"]}
        self.assertIn("src:aaa", sids)
        self.assertIn("src:ccc", sids)
        # 纯 SQL 筛选：来源 + 时间窗
        out2 = apiserve.api_sessions(self.con, self.db, {
            "source": "other-src", "since": "2026-10-06"})
        self.assertEqual(out2["total"], 0)
        out3 = apiserve.api_sessions(self.con, self.db, {
            "source": "other-src", "since": "2026-10-04"})
        self.assertEqual(out3["total"], 1)

    def test_sessions_skill_filter(self):
        out = apiserve.api_sessions(self.con, self.db, {
            "skill": "wechat-article-search"})
        self.assertEqual(out["total"], 1)
        self.assertEqual(out["items"][0]["sid"], "src:aaa")

    def test_q_and_skill_paths_use_caller_con_only(self):
        """P2 守卫：q（FTS）与 skill 扫描必须复用调用方的 ro 连接，
        不得自开普通读写连接——authorizer 全路径覆盖的结构性保证。"""
        real_connect = sqlite3.connect

        def _guard(*args, **kwargs):
            raise AssertionError("api 查询路径自开了新连接，"
                                 "authorizer 覆盖出现缺口")

        sqlite3.connect = _guard
        try:
            out = apiserve.api_sessions(self.con, self.db, {"q": "饼干"})
            self.assertEqual(out["total"], 2)
            facets = apiserve.api_facets(self.con, self.db)
            self.assertTrue(facets["skills"])
        finally:
            sqlite3.connect = real_connect

    def test_search_external_con_semantics(self):
        """search() 传外部连接：结果与自开连接一致，且不关闭外部连接。"""
        hits_ro = search(self.db, "饼干", con=self.con)
        hits_own = search(self.db, "饼干")
        self.assertEqual({h["sid"] for h in hits_ro},
                         {h["sid"] for h in hits_own})
        # 外部连接仍可用（未被 search 误关）
        n = self.con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        self.assertEqual(n, 3)

    def test_meta_schema_fingerprint(self):
        """meta 带 schema_fingerprint（设计稿 §3 承诺字段），schema 变
        则指纹变。"""
        meta = apiserve.api_meta(self.con, self.db)
        fp = meta["schema_fingerprint"]
        self.assertEqual(len(fp), 12)
        self.assertEqual(
            apiserve.api_meta(self.con, self.db)["schema_fingerprint"], fp)
        saved = dict(apiserve.EXPECTED_SCHEMA)
        try:
            apiserve.EXPECTED_SCHEMA = {
                **saved, "sessions": saved["sessions"] + ["newcol"]}
            self.assertNotEqual(
                apiserve.api_meta(self.con, self.db)["schema_fingerprint"], fp)
        finally:
            apiserve.EXPECTED_SCHEMA = saved

    def test_limit_clamp_and_pagination(self):
        out = apiserve.api_sessions(self.con, self.db,
                                    {"limit": "99999", "offset": "1"})
        self.assertLessEqual(out["limit"], 200)
        self.assertEqual(out["offset"], 1)

    def test_session_turns_and_turn_content(self):
        sess = apiserve.api_session(self.con, "src:aaa")
        self.assertEqual(sess["n_turns"], 1)  # note 前置归第一回合
        self.assertEqual(sess["turns"][0]["no"], 1)
        turn = apiserve.api_turn(self.con, "src:aaa", 1)
        roles = [m["role"] for m in turn["messages"]]
        self.assertEqual(roles, ["user", "assistant", "note"])
        self.assertIn("饼干", turn["messages"][0]["content"])

    def test_turn_out_of_range_and_unknown_sid(self):
        with self.assertRaises(IndexError):
            apiserve.api_turn(self.con, "src:aaa", 9)
        with self.assertRaises(KeyError):
            apiserve.api_session(self.con, "src:nope")


class TestHttp(unittest.TestCase):
    """HTTP 薄壳：真实端口起停 + token 鉴权 + 405/404。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = _fixture_db(Path(cls.tmp.name))
        cls.token = "secret-token"
        cls.srv = threading.Thread(
            target=apiserve.run,
            args=(cls.db,), kwargs={"port": 0, "host": "127.0.0.1",
                                    "token": cls.token}, daemon=True)
        # run() 里 serve_forever 阻塞；端口 0 需要拿到真实端口——
        # 改用底层方式：直接起 ThreadingHTTPServer 于随机端口
        import http.server
        handler = apiserve.make_handler(cls.db, cls.token)
        cls.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0),
                                                    handler)
        cls.port = cls.httpd.socket.getsockname()[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever,
                                      daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()  # 收干净 listening socket，防 ResourceWarning
        cls.tmp.cleanup()

    def _get(self, path: str, token: str | None = None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        if token:
            req.add_header("X-Token", token)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def test_meta_ok(self):
        status, body = self._get("/api/meta", token=self.token)
        self.assertEqual(status, 200)
        self.assertEqual(body["api_version"], 1)

    def test_401_without_token(self):
        status, body = self._get("/api/meta")
        self.assertEqual(status, 401)

    def test_404_and_405(self):
        status, body = self._get("/api/nope", token=self.token)
        self.assertEqual(status, 404)
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/meta", method="POST",
            data=b"{}")
        req.add_header("X-Token", self.token)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = resp.status
        except urllib.error.HTTPError as e:
            status = e.code
        self.assertEqual(status, 405)

    def test_sid_with_colon(self):
        # sid 形如 src:aaa，URL 路径含冒号须可解析
        status, body = self._get("/api/session/src:aaa", token=self.token)
        self.assertEqual(status, 200)
        self.assertEqual(body["meta"]["sid"], "src:aaa")


if __name__ == "__main__":
    unittest.main()
