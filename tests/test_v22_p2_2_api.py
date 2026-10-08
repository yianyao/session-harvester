# -*- coding: utf-8 -*-
"""v0.22 P2-2 只读端点测试：/api/keywords（SOP-P2-2）。

验收口径：
- 读 keywords_meta 最新 run，query n/limit 可调，additive
  （api_version=1 不动，红线 §5.2），挂 db_fingerprint；
- 未配置 keywords_meta 的实例 → 空表 + hint（降级不炸）；
- 通用性（用户红线）：fixture 用中性数据，不绑定任何主题/skill。
"""
from __future__ import annotations

import http.server
import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from harvester import apiserve
from harvester.indexing import SCHEMA
from harvester.kwstats import build_stats


def _fixture_db(tmp: Path) -> Path:
    db = tmp / "p22_api.db"
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)
    con.executescript(
        "CREATE TABLE IF NOT EXISTS messages("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, sid TEXT, role TEXT,"
        " ts TEXT, text TEXT, raw TEXT);")
    for i, raw in enumerate(["叙事节奏与氛围", "叙事节奏再探", "技法打磨"]):
        con.execute("INSERT INTO messages(sid, role, ts, text, raw)"
                    " VALUES (?,?,?,?,?)",
                    (f"src:s{i}", "user", f"2026-06-1{i} 10:00:00",
                     "", raw))
    con.commit()
    con.close()
    return db


class TestKeywordsApi(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.db = _fixture_db(root)
        cls.meta = root / "keywords_meta.db"
        build_stats(cls.db, cls.meta, ns=[2], role="user")
        cls.httpd = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            apiserve.make_handler(cls.db, None, None,
                                  keywords_meta=cls.meta))
        cls.port = cls.httpd.socket.getsockname()[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever,
                                      daemon=True)
        cls.thread.start()
        # 未配置 keywords_meta 的实例
        cls.httpd2 = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), apiserve.make_handler(cls.db, None, None))
        cls.port2 = cls.httpd2.socket.getsockname()[1]
        cls.thread2 = threading.Thread(target=cls.httpd2.serve_forever,
                                       daemon=True)
        cls.thread2.start()

    @classmethod
    def tearDownClass(cls):
        for h in (cls.httpd, cls.httpd2):
            h.shutdown()
            h.server_close()
        cls.tmp.cleanup()

    def _get(self, path: str, port: int | None = None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port or self.port}{path}")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def test_keywords_rows(self):
        status, body = self._get("/api/keywords?n=2&limit=10")
        self.assertEqual(status, 200)
        self.assertEqual(body["api_version"], 1)
        self.assertIn("db_fingerprint", body)
        grams = {r["gram"]: r["freq"] for r in body["rows"]}
        # raw 口径：叙事×2 / 节奏×2 / 其余各 1
        self.assertEqual(grams.get("叙事"), 2)
        self.assertEqual(grams.get("节奏"), 2)
        self.assertEqual(grams.get("氛围"), 1)
        self.assertIn("run", body)

    def test_keywords_unconfigured_hint(self):
        status, body = self._get("/api/keywords", port=self.port2)
        self.assertEqual(status, 200)
        self.assertEqual(body["rows"], [])
        self.assertIn("hint", body)

    def test_keywords_limit(self):
        status, body = self._get("/api/keywords?n=2&limit=1")
        self.assertEqual(status, 200)
        self.assertEqual(len(body["rows"]), 1)
        self.assertEqual(body["limit"], 1)


if __name__ == "__main__":
    unittest.main()
