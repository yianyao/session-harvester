# -*- coding: utf-8 -*-
"""v0.33 测试：分诊的批量取正文路径（分块 IN）。

背景：`triage()` 原来对**每个会话**发一次相关子查询去数 user 回合，而
`messages` 是 FTS5 虚拟表、`sid` 列 `UNINDEXED`（无可用索引）→ 每个会话
整表扫一遍 FTS5。真库（1964 会话 / 62899 消息）实测 **529 秒**。

改法：① 一次全扫描统计回合数；② 正文只取候选会话，按 400 一块发 IN。

**为什么这个文件测的是 400 这个边界**：分块逻辑写错（切片错位、参数个数与
占位符不匹配）时，超出一块的候选会**静默少返回**——不报错，只是 plan 少写
一批 sid。故用一个超过块大小的夹具把边界钉住。
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester.indexing import SCHEMA, index_session
from harvester.models import Message, SessionRecord
from harvester.noisetriage import triage
from harvester.topics import ensure_topics_db

CHUNK = 400          # 与 noisetriage 里的分块大小一致
N_OVER = CHUNK + 10  # 跨过一整块，逼出第二次 IN


def _seed(dirpath: Path, n_single: int, n_deep: int = 0,
          n_empty: int = 0) -> tuple[Path, Path]:
    db = dirpath / "h.db"
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)

    def add(sid: str, texts: list[str]) -> None:
        index_session(con, SessionRecord(
            source="yuanbao-raw", session_id=sid, title=sid,
            created_at="2026-01-01 10:00:00", updated_at="2026-01-01 10:00:00",
            messages=[Message(role="user", text=t) for t in texts]))

    for i in range(n_single):
        add(f"s{i:04d}", [f"“差额{i}”的英文表达"])          # 单轮查词 → 参与分诊
    for i in range(n_deep):
        add(f"d{i:04d}", [f"查一下第{j}个问题" for j in range(4)])  # 深会话 → 排除
    for i in range(n_empty):
        add(f"e{i:04d}", [])                                # 无 user 消息 → 排除
    con.commit()
    con.close()
    return db, ensure_topics_db(dirpath / "topics_meta.db")


class TestBatchFetch(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def test_all_candidates_are_returned_across_chunk_boundary(self):
        db, meta = _seed(self.dir, N_OVER)
        t = triage(db, meta, max_turns=3)
        sids = {r["sid"] for r in t["rows"]}
        self.assertEqual(len(sids), N_OVER)
        for i in (0, CHUNK - 1, CHUNK, N_OVER - 1):     # 块尾/块首两侧
            self.assertIn(f"yuanbao-raw:s{i:04d}", sids, i)
        self.assertEqual(t["counts"]["noise_high"], N_OVER)
        self.assertEqual(t["scanned"], N_OVER)

    def test_counts_sum_equals_scanned(self):
        db, meta = _seed(self.dir, 6, n_deep=2, n_empty=2)
        t = triage(db, meta, max_turns=3)
        self.assertEqual(sum(t["counts"].values()), t["scanned"])
        self.assertEqual(t["scanned"], 6)              # 深会话与空会话都不参与

    def test_sessions_without_user_messages_are_excluded(self):
        """LEFT JOIN 改单扫描后，0 回合的会话不得混进来（曾靠 `<= max_turns` 兜）。"""
        db, meta = _seed(self.dir, 3, n_empty=4)
        t = triage(db, meta, max_turns=3)
        self.assertNotIn("yuanbao-raw:e0000", {r["sid"] for r in t["rows"]})

    def test_deep_sessions_excluded_until_max_turns_raised(self):
        db, meta = _seed(self.dir, 2, n_deep=3)
        self.assertNotIn("yuanbao-raw:d0000",
                         {r["sid"] for r in triage(db, meta, max_turns=3)["rows"]})
        self.assertIn("yuanbao-raw:d0000",
                      {r["sid"] for r in triage(db, meta, max_turns=8)["rows"]})


if __name__ == "__main__":
    unittest.main()
