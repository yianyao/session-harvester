# -*- coding: utf-8 -*-
"""v0.74 chain-audit 复核台账：语义门告警人裁过的不许再烦人。

钉住三件事（用户裁决 2026-10-15）：
1. record_audit_review 落账（节点级键 sid+turn）后，audit_chain 不再把
   该节点列入疑似错配（additive 计数 reviewed_accepted；ok 按未裁决项算）；
2. 不传 review_meta = 旧行为，结果无任何新键；
3. show_reviewed=True 连同显示；台账 UPSERT 改判。
需要 PyYAML（chain frontmatter 解析无降级，H9）。
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester.chainaudit import (_node_key, audit_chain, list_audit_reviews,
                                  load_audit_review, record_audit_review)
from harvester.indexing import SCHEMA

_CHAIN = """---
topic: 测试主题
topic_id: tp-1
members: [dsh:a]
anchors:
  - stage: 阶段一
    nodes:
      - {sid: dsh:a, turn: 1, note: "完全无关的笔记词汇表"}
---

# 正文
"""


def _mk_db(path: Path):
    con = sqlite3.connect(str(path))
    con.executescript(SCHEMA)
    con.execute("INSERT INTO sessions (sid, source, session_id, title) "
                "VALUES ('dsh:a', 'dsh', 'a', '标题')")
    con.execute("INSERT INTO messages (sid, role, ts, text, raw) "
                "VALUES ('dsh:a', 'user', '2026-10-06 10:00:00', "
                "'深蓝的夜空下', '深蓝的夜空下')")
    con.commit()
    con.close()


from harvester.topicchain import _HAS_YAML


@unittest.skipUnless(_HAS_YAML, "需要 PyYAML（chain 解析无降级）")
class TestChainReviewLedger(unittest.TestCase):

    def _fixture(self, tmp: str):
        root = Path(tmp)
        db = root / "h.db"
        _mk_db(db)
        chain = root / "chain-t.md"
        chain.write_text(_CHAIN, encoding="utf-8", newline="\n")
        return root, db, chain

    def test_node_key_shape(self):
        self.assertEqual(_node_key("dsh:a", 3), "node:dsh:a#turn:3")

    def test_recorded_node_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, db, chain = self._fixture(tmp)
            r = audit_chain(chain, db)
            self.assertEqual(len(r["anchors"]["suspicious"]), 1)
            self.assertFalse(r["anchors"]["ok"])
            self.assertNotIn("reviewed_accepted", r["anchors"])
            meta = root / "topics_meta.db"
            record_audit_review(meta, chain.name,
                                [_node_key("dsh:a", 1)],
                                verdict="accepted", note="跨会话关系")
            r = audit_chain(chain, db, review_meta=meta)
            self.assertEqual(r["anchors"]["suspicious"], [])
            self.assertTrue(r["anchors"]["ok"])
            self.assertEqual(r["anchors"]["reviewed_accepted"], 1)
            self.assertTrue(r["ok"])   # 裁决后锚点门清零：quotes 门本就通过 → 顶层 ok 恢复 True（硬门不受影响）
            r = audit_chain(chain, db, review_meta=meta, show_reviewed=True)
            self.assertEqual(len(r["anchors"]["suspicious"]), 1)
            # 不传 review_meta：旧行为
            r = audit_chain(chain, db)
            self.assertEqual(len(r["anchors"]["suspicious"]), 1)
            self.assertNotIn("reviewed_accepted", r["anchors"])

    def test_upsert_and_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, db, chain = self._fixture(tmp)
            meta = root / "topics_meta.db"
            record_audit_review(meta, chain.name, [_node_key("dsh:a", 1)])
            record_audit_review(meta, chain.name, [_node_key("dsh:a", 1)],
                                verdict="fixed", note="note 已改写")
            rows = list_audit_reviews(meta, chain.name)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["verdict"], "fixed")
            self.assertEqual(load_audit_review(meta, chain.name),
                             {_node_key("dsh:a", 1)})
            self.assertEqual(load_audit_review(None, chain.name), set())


if __name__ == "__main__":
    unittest.main()
