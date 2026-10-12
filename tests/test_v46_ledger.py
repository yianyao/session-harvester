# -*- coding: utf-8 -*-
"""v0.46 P2-5 测试：人账（建议句 + 主题级裁决都留痕）。

背景：台账只记"建议句"的裁决，而项目已登记 14 个主题、处置过 163 条零散，
这些裁决**没有留痕**（H101/§6 P2-5）。本文件钉住三件事：
1. `topic:` 命名空间**与建议句分开对账**——主题天然不在建议池里，
   混算会让 14 个主题全部误报成"台账陈旧"（首版就是这么错的，本文件抓住）；
2. `ledger-sync` 的派生/回填是**幂等且不覆盖人写的东西**；
3. `coverage` 把人账拆成"已采纳的陈旧（不算欠账，交 harvest-eval）"与
   "其他陈旧（应清账）"——两者的处置完全不同，混在一起会误导。
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester.agent_suggest import coverage_report, render_coverage
from harvester.suggestmeta import (backfill_adopted_at, load_records,
                                   registry_counts, set_status, sync_rows,
                                   topic_rows)

_TOPICS_DDL = """
CREATE TABLE topics (id TEXT PRIMARY KEY, name TEXT, keywords TEXT,
                     members TEXT, created TEXT);
CREATE TABLE sessions_noise (sid TEXT PRIMARY KEY, reason TEXT, created TEXT);
"""


def _topics_db(tmp: Path, rows=None) -> Path:
    """主题注册表夹具：created 用**无秒**格式（真库就是 'YYYY-MM-DD HH:MM'）。"""
    p = tmp / "topics_meta.db"
    con = sqlite3.connect(str(p))
    try:
        con.executescript(_TOPICS_DDL)
        con.executemany("INSERT INTO topics VALUES (?,?,?,?,?)",
                        rows if rows is not None else [
                            ("tp-1", "小说", "k", '["a"]', "2026-10-08 15:55"),
                            ("tp-2", "采集", "k", '["b"]', "2026-10-10 09:01:30"),
                        ])
        con.executemany("INSERT INTO sessions_noise VALUES (?,?,?)",
                        [("s1", "噪声", "2026-10-08 15:55"),
                         ("s2", "噪声", "2026-10-08 15:56")])
        con.commit()
    finally:
        con.close()
    return p


class TopicRowsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_derive_rows_and_normalize_minute_stamp(self):
        """注册表的 'HH:MM'（无秒）必须补成台账口径 'HH:MM:SS'。

        这条会红的情形：直接把 created 原样塞进 adopted_at —— `set_status`
        的严格校验会当场 ValueError（真机第一次跑 ledger-sync --apply 就撞上了）。
        """
        rows = topic_rows(_topics_db(self.root))
        self.assertEqual([r["key"] for r in rows], ["topic:tp-1", "topic:tp-2"])
        self.assertEqual(rows[0]["adopted_at"], "2026-10-08 15:55:00")
        self.assertEqual(rows[1]["adopted_at"], "2026-10-10 09:01:30")  # 已有秒
        self.assertEqual(rows[0]["status"], "adopted")
        self.assertIn("小说", rows[0]["note"])

    def test_missing_registry_is_empty_not_error(self):
        self.assertEqual(topic_rows(self.root / "nope.db"), [])
        self.assertEqual(topic_rows(None), [])
        self.assertEqual(registry_counts(None), {"topics": 0, "noise": 0})

    def test_registry_counts(self):
        self.assertEqual(registry_counts(_topics_db(self.root)),
                         {"topics": 2, "noise": 2})


class SyncRowsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.meta = self.root / "suggestions_meta.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_dry_run_writes_nothing_then_apply_is_idempotent(self):
        rows = topic_rows(_topics_db(self.root))
        dry = sync_rows(self.meta, rows)
        self.assertEqual(len(dry["created"]), 2)
        self.assertEqual(load_records(self.meta), {})       # dry-run 不建库
        sync_rows(self.meta, rows, apply=True)
        self.assertEqual(len(load_records(self.meta)), 2)
        again = sync_rows(self.meta, rows, apply=True)
        self.assertEqual(again["created"], [])
        self.assertEqual(again["unchanged"], ["topic:tp-1", "topic:tp-2"])

    def test_never_overwrites_human_adopted_at_or_note(self):
        """人写下的分界与说明**永不被派生值覆盖**（这是本工具的安全边界）。"""
        set_status(self.meta, "topic:tp-1", "adopted",
                   adopted_at="2026-01-01 00:00:00", note="人手写的说明")
        before = load_records(self.meta)["topic:tp-1"]
        sync_rows(self.meta, topic_rows(_topics_db(self.root)), apply=True)
        after = load_records(self.meta)["topic:tp-1"]
        self.assertEqual(after["adopted_at"], before["adopted_at"])
        self.assertEqual(after["note"], "人手写的说明")

    def test_backfill_uses_ledger_updated_at(self):
        """存量"已采纳但没分界" → 用台账自己的 `updated_at` 回填（只补 NULL）。

        **修正过的 flaky 断言（v0.47）**：原版断言
        `rec["adopted_at"] == rec["updated_at"]`——但 `backfill_adopted_at(apply=True)`
        内部会 `set_status(...)`，那**同时把 `updated_at` 刷成"此刻"**。于是
        "写裁决"与"回填"跨过一秒边界时，`adopted_at`（旧 updated_at）≠ `updated_at`（新）
        → **偶发红**。它此前一直没被看见，直到 v0.47 的基线门把套件跑了两遍
        （见 `tests/test_v48_test_baseline.py`），命中边界的概率上来才暴露。

        现在改成：**先冻结回填时刻的 `updated_at`**，断言回填值等于它，
        并如实断言"回填把 `updated_at` 推进了"（这才是那条刷新的真实语义）。
        这样任何一秒跑都确定性通过，而且仍然钉住"取的是台账自己的时间"。
        """
        set_status(self.meta, "s1", "adopted")           # 未显式给日期
        set_status(self.meta, "s2", "rejected")
        con = sqlite3.connect(str(self.meta))
        try:                                              # 造出"存量"形态
            con.execute("UPDATE suggestion_status SET adopted_at=NULL "
                        "WHERE key='s1'")
            con.commit()
        finally:
            con.close()
        frozen = load_records(self.meta)["s1"]["updated_at"]
        dry = backfill_adopted_at(self.meta)
        self.assertEqual(dry["filled"], ["s1"])
        self.assertIsNone(load_records(self.meta)["s1"]["adopted_at"])
        backfill_adopted_at(self.meta, apply=True)
        rec = load_records(self.meta)["s1"]
        self.assertEqual(rec["adopted_at"], frozen,
                         "回填必须取**台账自己的** updated_at（不是今天）")
        self.assertGreaterEqual(rec["updated_at"], frozen,
                                "回填本身是一次写库，updated_at 只前进")
        self.assertEqual(backfill_adopted_at(self.meta)["filled"], [])  # 幂等


class CoverageNamespaceTest(unittest.TestCase):
    """`topic:` 必须与建议句**分开对账**（首版混算 → 14 主题全成"陈旧"）。"""

    def test_topic_keys_are_not_reported_as_stale(self):
        entries = [{"title": "建议句 A"}, {"title": "建议句 B"}]
        statuses = {"建议句 A": "adopted",
                    "topic:tp-1": "adopted", "topic:tp-2": "adopted"}
        cov = coverage_report(entries, statuses)
        self.assertEqual(cov["undecided"], ["建议句 B"])
        self.assertEqual(cov["stale"], [])                  # 主题不算陈旧
        self.assertEqual(cov["topic_keys"], ["topic:tp-1", "topic:tp-2"])
        self.assertEqual(cov["ledger"], 1)                  # 台账只数建议句
        self.assertEqual(cov["ledger_counts"], {"adopted": 1})  # 主题不混算
        text = render_coverage(cov)
        self.assertIn("主题级台账：**2** 条", text)
        self.assertNotIn("topic:tp-1\n- topic:tp-2\n- topic:tp-3", text)

    def test_stale_adopted_vs_stale_other_have_different_verdicts(self):
        entries = [{"title": "在池里的建议"}]
        statuses = {"在池里的建议": "pending",
                    "已采纳但退休了": "adopted",
                    "否决后退休了": "rejected"}
        cov = coverage_report(entries, statuses)
        self.assertEqual(cov["stale_adopted"], ["已采纳但退休了"])
        self.assertEqual(cov["stale_other"], ["否决后退休了"])
        text = render_coverage(cov)
        self.assertIn("疑似规则生效", text)      # adopted 的陈旧：交 harvest-eval
        self.assertIn("应清账", text)            # 其他陈旧：清账

    def test_accounts_block_shows_three_books(self):
        cov = coverage_report([{"title": "A"}], {"A": "pending"},
                              accounts={"topics": 14, "noise": 163,
                                        "topics_meta": "topics_meta.db"})
        text = render_coverage(cov)
        self.assertIn("主题注册表：**14** 个", text)
        self.assertIn("零散登记：**163** 条", text)
        self.assertIn("117 候选簇", text)        # 不可回溯项显式写明


if __name__ == "__main__":
    unittest.main()
