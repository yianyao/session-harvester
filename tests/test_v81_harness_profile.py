# -*- coding: utf-8 -*-
"""v0.81 P1-2 report-harness（Harness 体检）测试。

口径（与 harnessstats docstring 一一对应）：
1. 每源一行画像：会话/消息/步骤/失败率/代表工具/代表 skill/最近活跃；
2. 失败率分母 = result 相步骤数；**无 result 相的源 fail_rate=None
   而非 0**（「没有数据」与「没有错误」是两件事）；
3. telemetry=有任何工具步骤；paired=有 result 相——只落 call 相的源
   （如 vscode-copilot）不是无遥测；
4. 空 skill 名归一为「（未识别）」，不许渲染出裸「×2」；
5. 排序 = 会话数降序；总量 = 各行之和。

验收含合成注入（SOP §5.6 防恒真）：合成了 30 连错的 harness 必须
以正确失败率报出。
"""

from __future__ import annotations

import http.server
import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from harvester import apiserve
from harvester.harnessstats import TOP_N, collect, render_report
from harvester.indexing import SCHEMA, index_session
from harvester.models import Message, SessionRecord


def make_db(path: Path, sources: dict[str, dict]) -> None:
    """建临时索引库。

    sources: 源名 -> {
        "sessions": [{"sid", "messages", "model", "created_at",
                      "updated_at", "steps": [(tool, phase, status,
                      detail), ...]}]
    }
    """
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE sessions (sid TEXT PRIMARY KEY, source TEXT,"
                " session_id TEXT, title TEXT, category TEXT,"
                " created_at TEXT, updated_at TEXT, file TEXT, model TEXT)")
    con.execute("CREATE TABLE messages (sid TEXT, role TEXT, ts TEXT,"
                " text TEXT, raw TEXT)")
    con.execute("CREATE TABLE steps (sid TEXT, seq INTEGER, ts TEXT,"
                " tool TEXT, phase TEXT, status TEXT, error TEXT,"
                " detail TEXT)")
    for src, spec in sources.items():
        for sess in spec["sessions"]:
            sid = sess["sid"]
            con.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?)",
                        (sid, src, sid.split(":", 1)[-1], "t", "cat",
                         sess.get("created_at", "2026-10-01 10:00:00"),
                         sess.get("updated_at", "2026-10-01 10:00:00"),
                         "f", sess.get("model")))
            for _ in range(sess.get("messages", 0)):
                con.execute("INSERT INTO messages VALUES (?,?,?,?,?)",
                            (sid, "user", "2026-10-01 10:00:00", "hi", ""))
            for i, st in enumerate(sess.get("steps", []), 1):
                tool, phase, status = st[0], st[1], st[2]
                detail = st[3] if len(st) > 3 else None
                con.execute("INSERT INTO steps VALUES (?,?,?,?,?,?,?,?)",
                            (sid, i, "2026-10-01 10:00:00", tool, phase,
                             status,
                             "boom" if status == "error" else None, detail))
    con.commit()
    con.close()


def steps_pairs(n: int, tool: str = "Bash") -> list[tuple]:
    """n 组 call+result 成对（正常形态）。"""
    out: list[tuple] = []
    for _ in range(n):
        out += [(tool, "call", None), (tool, "result", "ok")]
    return out


def spin_session(n: int, tool: str = "Write") -> list[tuple]:
    """n 个连续 error result（首错+重试全败，失败率 100% 的注入）。"""
    return [(tool, "result", "error") for _ in range(n)]


class TestHarnessProfile(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    # ---- ① 合成注入：30 连错的 harness 必须报出且失败率正确 ----

    def test_synth_spin_source_reported(self):
        db = self.dir / "t.db"
        make_db(db, {
            "synth-harness": {"sessions": [
                {"sid": "synth-harness:s1", "messages": 5, "steps":
                 spin_session(30), "updated_at": "2026-10-02 09:00:00"},
                {"sid": "synth-harness:s2", "messages": 3,
                 "steps": steps_pairs(4, "Read")},
            ]},
            "quiet-harness": {"sessions": [
                {"sid": "quiet-harness:q1", "messages": 9,
                 "steps": steps_pairs(10, "Grep")},
            ]},
        })
        d = collect(db)
        by = {s["name"]: s for s in d["sources"]}
        self.assertIn("synth-harness", by)
        s = by["synth-harness"]
        self.assertEqual(s["sessions"], 2)
        self.assertEqual(s["messages"], 8)
        # 30 error result + 4 Read 对（4 call + 4 result）= 38 步
        self.assertEqual(s["steps"], 38)
        self.assertEqual(s["errors"], 30)
        self.assertEqual(s["results"], 34)          # 分母 = result 相
        self.assertAlmostEqual(s["fail_rate"], round(30 / 34, 4), places=4)
        self.assertTrue(s["telemetry"])
        self.assertTrue(s["paired"])
        # 代表工具 = Write（30 个 result 行）与 Read（4 行 call），Write 居首
        self.assertEqual(s["top_tools"][0]["tool"], "Write")
        self.assertEqual(s["top_tools"][0]["steps"], 30)
        self.assertEqual(s["last_active"], "2026-10-02 09:00:00")
        # 排序：会话数降序（synth 2 > quiet 1）
        self.assertEqual([x["name"] for x in d["sources"]],
                         ["synth-harness", "quiet-harness"])
        # 总量 = 各行之和
        self.assertEqual(d["totals"]["sessions"], 3)
        self.assertEqual(d["totals"]["steps"], 38 + 20)
        self.assertAlmostEqual(d["totals"]["fail_rate"],
                               round(30 / (34 + 10), 4), places=4)

    # ---- ② 无遥测源：fail_rate=None（不是 0），telemetry=False ----

    def test_chat_platform_no_telemetry_is_none(self):
        db = self.dir / "t.db"
        make_db(db, {"yuanbao-raw": {"sessions": [
            {"sid": "yuanbao-raw:y1", "messages": 42, "steps": []},
        ]}})
        d = collect(db)
        s = d["sources"][0]
        self.assertEqual(s["steps"], 0)
        self.assertIsNone(s["fail_rate"])       # 没有数据 ≠ 没有错误
        self.assertFalse(s["telemetry"])
        self.assertFalse(s["paired"])
        self.assertEqual(s["top_tools"], [])
        self.assertIsNone(d["totals"]["fail_rate"])
        # 报告文案必须区分「无遥测」，不许把 None 渲染成 0%
        rep = render_report(d)
        self.assertIn("—（无遥测）", rep)
        self.assertNotIn("0.0%", rep)

    # ---- ③ 只落 call 相的源：telemetry=True 但 paired=False ----

    def test_call_only_source_not_counted_as_no_telemetry(self):
        db = self.dir / "t.db"
        make_db(db, {"vscode-copilot": {"sessions": [
            {"sid": "vscode-copilot:c1", "messages": 3,
             "steps": [("copilot_readFile", "call", None)] * 5},
        ]}})
        d = collect(db)
        s = d["sources"][0]
        self.assertEqual(s["steps"], 5)
        self.assertTrue(s["telemetry"])
        self.assertFalse(s["paired"])           # 没有 result 相 → 不可算
        self.assertIsNone(s["fail_rate"])
        self.assertIn("—（无结果相）", render_report(d))

    # ---- ④ skill 画像：Top N + 空名归一 + 超量报数 ----

    def test_skill_profile_top_n_and_unknown_label(self):
        db = self.dir / "t.db"
        sess = {"sid": "dsh:sk", "messages": 1,
                "steps": steps_pairs(2, "Read")}
        for k in range(TOP_N + 2):              # 7 个不同 skill（超 Top5）
            sess["steps"].append(
                ("Skill", "call", None,
                 json.dumps({"skill": f"skill-{k}"})))
            sess["steps"].append(("Skill", "result", "ok"))
        # 两个解析不出名字的调用点（detail 无 skill 字段）→ 「（未识别）」
        # 给 2 次：与 7 个命名 skill（各 1 次）比计数更高，必然进 Top5，
        # 断言才钉得住「空名不许原样出现」。
        for _ in range(2):
            sess["steps"].append(("Skill", "call", None, json.dumps({})))
            sess["steps"].append(("Skill", "result", "ok"))
        make_db(db, {"dsh": {"sessions": [sess]}})
        d = collect(db)
        s = d["sources"][0]
        self.assertEqual(len(s["top_skills"]), TOP_N)
        self.assertEqual(s["skills_total"], TOP_N + 4)   # 7 命名 + 2 未识别
        names = [x["skill"] for x in s["top_skills"]]
        self.assertNotIn("", names)              # 空名不许原样出现
        self.assertEqual(names[0], "（未识别）")  # 计数最高，排第一
        self.assertEqual(s["top_skills"][0]["count"], 2)
        rep = render_report(d)
        self.assertIn("（未识别）", rep)

    # ---- ⑤ 窗口：--since 只留新会话 ----

    def test_since_window_excludes_old(self):
        db = self.dir / "t.db"
        make_db(db, {
            "old-h": {"sessions": [
                {"sid": "old-h:o1", "messages": 1, "steps": steps_pairs(3),
                 "updated_at": "2020-01-01 00:00:00"}]},
            "new-h": {"sessions": [
                {"sid": "new-h:n1", "messages": 1, "steps": steps_pairs(3),
                 "updated_at": "2999-01-01 00:00:00"}]},
        })
        d = collect(db, since_days=1.0)
        self.assertEqual([s["name"] for s in d["sources"]], ["new-h"])
        self.assertEqual(d["totals"]["sessions"], 1)
        # 全库口径：两个源都在
        self.assertEqual(len(collect(db)["sources"]), 2)

    # ---- ⑥ 空库：诚实空态 ----

    def test_empty_db_honest_empty(self):
        db = self.dir / "t.db"
        make_db(db, {})
        d = collect(db)
        self.assertEqual(d["sources"], [])
        self.assertEqual(d["totals"]["sessions"], 0)
        self.assertIn("（窗口内没有会话）", render_report(d))


def _api_fixture_db(tmp: Path) -> Path:
    """真 schema 夹具库（两个源：一个有遥测且带错误，一个纯聊天）。"""
    db = tmp / "h_api.db"
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)
    index_session(con, SessionRecord(
        source="agent-x", session_id="a1", title="t",
        created_at="2026-10-01 10:00:00", updated_at="2026-10-02 10:00:00",
        messages=[Message(role="user", text="u1"),
                  Message(role="assistant", text="a1")]))
    con.execute("UPDATE sessions SET model='m1' WHERE sid='agent-x:a1'")
    for seq, (phase, status) in enumerate(
            [("call", None), ("result", "ok"), ("call", None),
             ("result", "error")], 1):
        con.execute("INSERT INTO steps VALUES (?,?,?,?,?,?,?,?)",
                    ("agent-x:a1", seq, "2026-10-01 10:00:00", "Bash",
                     phase, status, "boom" if status == "error" else None,
                     ""))
    index_session(con, SessionRecord(
        source="chat-y", session_id="c1", title="t",
        created_at="2026-10-01 10:00:00", updated_at="2026-10-02 10:00:00",
        messages=[Message(role="user", text="u1")]))
    con.commit()
    con.close()
    return db


class TestHarnessApiEndpoint(unittest.TestCase):
    """/api/harness 端点（真 HTTP server，与既有 api 测试同式）。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = _api_fixture_db(Path(cls.tmp.name))
        cls.httpd = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), apiserve.make_handler(cls.db, None, None))
        cls.port = cls.httpd.socket.getsockname()[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever,
                                      daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def _get(self, path: str):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return (resp.status, resp.headers.get("Content-Type", ""),
                        resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:  # noqa: F841 (保留分支供排查)
            return e.code, "", e.read().decode("utf-8")

    def test_harness_endpoint_shape(self):
        status, ctype, body = self._get("/api/harness")
        self.assertEqual(status, 200)
        self.assertIn("application/json", ctype)
        d = json.loads(body)
        self.assertEqual(d["api_version"], apiserve.API_VERSION)
        self.assertIn("db_fingerprint", d)
        by = {s["name"]: s for s in d["sources"]}
        self.assertEqual(set(by), {"agent-x", "chat-y"})
        ax = by["agent-x"]
        self.assertEqual(ax["sessions"], 1)
        self.assertEqual(ax["steps"], 4)
        self.assertEqual(ax["results"], 2)
        self.assertEqual(ax["errors"], 1)
        self.assertAlmostEqual(ax["fail_rate"], 0.5, places=4)
        self.assertTrue(ax["paired"])
        self.assertEqual(ax["top_tools"][0]["tool"], "Bash")
        cy = by["chat-y"]
        self.assertIsNone(cy["fail_rate"])       # 纯聊天 ≠ 0 失败
        self.assertFalse(cy["telemetry"])
        # 排序 = 会话数降序
        self.assertEqual([s["name"] for s in d["sources"]],
                         ["agent-x", "chat-y"])

    def test_harness_days_param(self):
        status, _, body = self._get("/api/harness?days=0.0001")
        self.assertEqual(status, 200)
        d = json.loads(body)
        # 窗口极窄：两个源的会话都老于窗口 → 空态而非报错
        self.assertEqual(d["sources"], [])
        self.assertEqual(d["window"]["since_days"], 0.0001)


if __name__ == "__main__":
    unittest.main()
