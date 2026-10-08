# -*- coding: utf-8 -*-
"""v0.22 T0 text-raw 契约防回归测试（SOP-T0-1，H3）。

契约：messages.text 是 bigram 索引改写文本（如"又来 来了 ！"），
原文在 raw。一切统计/提取必须 raw 优先、text 兜底。

防回归方式：构造 text 与 raw 内容刻意不同的行（text 为干扰文本），
断言所有既定读取路径的产物只含 raw 内容——任何未来改回"直接读 text"
的实现都会在此红：
- harvester.cards._session_content（G3 引文核对的语料路径）
- harvester.apiserve._session_messages（read/API 的消息内容路径）
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester import apiserve
from harvester.cards import _session_content
from harvester.indexing import SCHEMA, index_session
from harvester.models import Message, SessionRecord

_RAW_TEXT = "这是原文证据句子 Alpha beta gamma"
# bigram 索引改写形态的干扰文本（刻意与原文无公共子串）
_BIGRAM_TEXT = "这是 干扰 改写 文本 ！！"


def _fixture_db(tmp: Path) -> Path:
    db = tmp / "t0_test.db"
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)
    rec = SessionRecord(
        source="src", session_id="aaa", title="T",
        created_at="2026-10-08 10:00:00", updated_at="2026-10-08 10:00:00",
        messages=[Message(role="user", text="问"),
                  Message(role="assistant", text=_RAW_TEXT)])
    index_session(con, rec)
    # 模拟 bigram 索引改写：直接改写 text 列，raw 保持原文
    con.execute("UPDATE messages SET text=? WHERE role='assistant'",
                (_BIGRAM_TEXT,))
    con.commit()
    con.close()
    return db


class TestTextRawContract(unittest.TestCase):
    """契约红线：raw 优先，text 只是兜底（H3 实测 6079 条 user 消息
    为 bigram 形态——统计若走 text 结果全错）。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = _fixture_db(Path(cls.tmp.name))
        cls.con = apiserve.open_ro(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        cls.tmp.cleanup()

    def test_session_content_reads_raw(self):
        got = _session_content("aaa", self.db, con=self.con)
        self.assertIn(_RAW_TEXT, got)
        self.assertNotIn(_BIGRAM_TEXT, got)

    def test_api_messages_read_raw(self):
        _msgs, contents, _ts = apiserve._session_messages(self.con, "src:aaa")
        joined = "\n".join(contents)
        self.assertIn(_RAW_TEXT, joined)
        self.assertNotIn(_BIGRAM_TEXT, joined)


if __name__ == "__main__":
    unittest.main()
