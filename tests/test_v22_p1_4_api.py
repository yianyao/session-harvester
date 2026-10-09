# -*- coding: utf-8 -*-
"""v0.22 P1-4 /api/export-analysis 端点测试。

SOP-P1-4：五类 kind；format=md → text/markdown 响应，format=json →
machineWrap 头 JSON；kind 非法 → 400；sessions sid 不存在 → 404。
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
from harvester.indexing import SCHEMA, index_session
from harvester.models import Message, SessionRecord

ERR_A = ("cannot edit C:/w/a.md: file changed since it was read"
         " — re-read the file, then retry")
ERR_B = ("cannot edit C:/w/b.md: file changed since it was read"
         " — re-read the file, then retry")


def _fixture_db(tmp: Path) -> Path:
    db = tmp / "p14_api.db"
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)
    for sid, title in [("s1", "会话一"), ("s2", "会话二")]:
        index_session(con, SessionRecord(
            source="src", session_id=sid, title=title,
            created_at="2026-06-10 10:00:00", updated_at="2026-06-10 10:00:00",
            messages=[Message(role="user", text="u1"),
                      Message(role="assistant", text="a1")]))
    # raw 列直接置原文（H38：raw 恒为原文；导出全文必须走 raw）
    con.execute("UPDATE messages SET raw='原文 ' || sid WHERE role='user'")
    for sid, seq, err in (("src:s1", 1, ERR_A), ("src:s1", 2, ERR_B),
                          ("src:s2", 1, ERR_A)):
        con.execute("INSERT INTO steps VALUES (?,?,?,?,?,?,?,?)",
                    (sid, seq, "2026-06-10 10:00:00", "edit", "call",
                     None, None, ""))
        con.execute("INSERT INTO steps VALUES (?,?,?,?,?,?,?,?)",
                    (sid, seq + 100, "2026-06-10 10:00:00", "edit",
                     "result", "error", err, ""))
    con.commit()
    con.close()
    return db


class TestExportAnalysisApi(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = _fixture_db(Path(cls.tmp.name))
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
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return (resp.status, resp.headers.get("Content-Type", ""),
                        resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type", ""), \
                e.read().decode("utf-8")

    def test_json_out_has_machine_wrap(self):
        status, ctype, body = self._get(
            "/api/export-analysis?kind=errors&format=json")
        self.assertEqual(status, 200)
        self.assertIn("application/json", ctype)
        obj = json.loads(body)
        self.assertEqual(obj["kind"], "analysis-errors")
        self.assertEqual(obj["api_version"], 1)
        for key in ("generated_at", "db_fingerprint", "dedup", "hint", "data"):
            self.assertIn(key, obj)

    def test_md_out_is_markdown(self):
        status, ctype, body = self._get("/api/export-analysis?kind=errors")
        self.assertEqual(status, 200)
        self.assertIn("text/markdown", ctype)
        self.assertIn("错误三分类报告", body)

    def test_sessions_dedup_and_md_fulltext(self):
        status, _, body = self._get(
            "/api/export-analysis?kind=sessions&format=json")
        self.assertEqual(status, 200)
        data = json.loads(body)["data"]
        self.assertEqual(data["n_sessions"], 2)
        target = [g for g in data["patterns_dedup"]
                  if "file changed" in g["pattern"]]
        self.assertEqual(len(target), 1)  # a/b 路径占位后同键
        self.assertEqual(target[0]["count"], 3)
        # md 出口走 raw 全文
        _, _, md = self._get("/api/export-analysis?kind=sessions")
        self.assertIn("原文 src:s1", md)
        self.assertNotIn("大 回", md)

    def test_bad_kind_400(self):
        status, _, body = self._get(
            "/api/export-analysis?kind=nope&format=json")
        self.assertEqual(status, 400)
        self.assertIn("error", json.loads(body))

    def test_missing_sid_404(self):
        status, _, body = self._get(
            "/api/export-analysis?kind=sessions&format=json"
            "&sids=src:none")
        self.assertEqual(status, 404)
        self.assertIn("error", json.loads(body))

    def test_all_kinds_200(self):
        for kind in ("tools", "errors", "skills", "triage"):
            status, _, _ = self._get(
                f"/api/export-analysis?kind={kind}&format=json")
            self.assertEqual(status, 200, kind)


if __name__ == "__main__":
    unittest.main()
