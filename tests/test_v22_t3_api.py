# -*- coding: utf-8 -*-
"""v0.22 T3 只读端点测试（SOP-T3 第 4 步）。

验收口径（PLAN §4 T3）：
- /api/topics：注册表 + 簇统计，additive（api_version=1 不动，红线 §5.2），
  挂 db_fingerprint；
- /api/topic/<id>/chain：chain 文档结构化（frontmatter + 正文），挂
  db_fingerprint；文档缺失 → 404；
- 未配置 topics_meta 的实例 → /api/topics 返回空表 + hint（降级不炸）。
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

import yaml

from harvester import apiserve
from harvester.indexing import SCHEMA, index_session
from harvester.models import Message, SessionRecord
from harvester.topics import add_members, ensure_topics_db, register_topic


def _fixture_db(tmp: Path) -> Path:
    db = tmp / "t3_api.db"
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)
    for sid, title in [("s1", "节奏初探"), ("s2", "氛围铺陈")]:
        index_session(con, SessionRecord(
            source="src", session_id=sid, title=title,
            created_at="2026-06-10 10:00:00", updated_at="2026-06-10 10:00:00",
            messages=[Message(role="user", text="u1")]))
    con.commit()
    con.close()
    return db


def _chain_doc(topic_id: str) -> str:
    fm = {"topic": "叙事节奏", "topic_id": topic_id,
          "members": ["src:s1", "src:s2"],
          "anchors": [{"stage": "阶段一",
                       "nodes": [{"sid": "src:s1", "turn": 1, "note": "x"}]}],
          "generated_from": {"db_mtime": "2026-10-08 15:48:53"},
          "prompt_version": "t3-pack-test"}
    return ("---\n"
            + yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)
            + "---\n\n# 思维链正文\n\n阶段一的叙述。\n")


class TestTopicsApi(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.db = _fixture_db(root)
        cls.meta = ensure_topics_db(root / "topics_meta.db")
        cls.tid = register_topic(cls.meta, "叙事节奏", keywords=["节奏"])
        add_members(cls.meta, cls.tid, ["src:s1", "src:s2"])
        cls.chain_root = root / "topics"
        cls.chain_root.mkdir()
        (cls.chain_root / "chain-叙事节奏.md").write_text(
            _chain_doc(cls.tid), encoding="utf-8", newline="\n")
        import http.server
        cls.httpd = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            apiserve.make_handler(cls.db, None, None,
                                  topics_meta=cls.meta,
                                  chain_root=cls.chain_root))
        cls.port = cls.httpd.socket.getsockname()[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever,
                                      daemon=True)
        cls.thread.start()
        # 未配置 topics_meta 的实例
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

    def test_topics_registry(self):
        status, body = self._get("/api/topics")
        self.assertEqual(status, 200)
        self.assertEqual(body["api_version"], 1)
        self.assertIn("db_fingerprint", body)
        self.assertEqual(len(body["topics"]), 1)
        t = body["topics"][0]
        self.assertEqual(t["id"], self.tid)
        self.assertEqual(t["name"], "叙事节奏")
        self.assertEqual(t["members_count"], 2)

    def test_topics_hint_when_unconfigured(self):
        status, body = self._get("/api/topics", port=self.port2)
        self.assertEqual(status, 200)
        self.assertEqual(body["topics"], [])
        self.assertIn("hint", body)

    def test_topic_chain_document(self):
        status, body = self._get(f"/api/topic/{self.tid}/chain")
        self.assertEqual(status, 200)
        self.assertEqual(body["api_version"], 1)
        self.assertIn("db_fingerprint", body)
        self.assertEqual(body["fm"]["topic_id"], self.tid)
        self.assertIn("思维链正文", body["body"])
        self.assertIn("chain_path", body)

    def test_topic_chain_missing_404(self):
        status, body = self._get("/api/topic/tp-none/chain")
        self.assertEqual(status, 404)
        self.assertIn("error", body)


if __name__ == "__main__":
    unittest.main()
