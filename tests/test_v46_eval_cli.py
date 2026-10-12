# -*- coding: utf-8 -*-
"""v0.46 测试：`harvest-eval` / `ledger-sync` / `suggest-status` 的 **CLI 层**。

补的缺口（子代理在收尾回报里如实标注："`harvest-eval` 的退出码分层由你（主线）
覆盖，我没有 CLI 测试"）：模块层 38 例已由 `tests/test_v46_harvest_eval.py` 钉住，
但**命令行的三分退出码**（0 正常 / 2 参数或库缺失）与**写库动作**（dry-run 不写、
--apply 才写、幂等）没人测——这两类恰恰是"用起来才会踩"的地方。

每条断言都能说清何时会红：
- 缺库/格式非法必须回 2（退化成抛异常或回 0 都会红）；
- `ledger-sync` dry-run 必须**一行都不写**（写了一点就红）；
- 重复 `--apply` 必须幂等（再建行就红）。
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester.cli import main
from harvester.indexing import SCHEMA
from harvester.suggestmeta import load_records, set_status

_ERR = "bash boom: command not found"
_TOPICS_DDL = """
CREATE TABLE topics (id TEXT PRIMARY KEY, name TEXT, keywords TEXT,
                     members TEXT, created TEXT);
CREATE TABLE sessions_noise (sid TEXT PRIMARY KEY, reason TEXT, created TEXT);
"""


def _db_with_errors(root: Path) -> Path:
    """夹具库：1 个会话，前窗 2 条错（未自愈）+ 后窗 1 条错（自愈）。"""
    db = root / "h.db"
    con = sqlite3.connect(str(db))
    try:
        con.executescript(SCHEMA)
        con.execute(
            "INSERT INTO sessions (sid, source, session_id, title, category,"
            " created_at, updated_at, file, model) VALUES (?,?,?,?,?,?,?,?,?)",
            ("dsh:s1", "dsh", "s1", "夹具", None, "2026-10-05 09:00:00",
             "2026-10-05 09:00:00", None, None))
        rows = [  # (seq, ts, status, error)
            (0, "2026-10-05 10:00:00", "error", _ERR),
            (1, "2026-10-06 11:00:00", "error", _ERR),
            (2, "2026-10-11 12:00:00", "error", _ERR),
            (3, "2026-10-11 12:05:00", "completed", None),   # 同会话后续成功
        ]
        for seq, ts, status, err in rows:
            con.execute("INSERT INTO steps VALUES (?,?,?,?,?,?,?,?)",
                        ("dsh:s1", seq, ts, "bash", "result", status, err, None))
        con.commit()
    finally:
        con.close()
    return db


class HarvestEvalCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def test_missing_db_returns_2(self):
        rc = main(["harvest-eval", "--db", str(self.root / "nope.db"),
                   "--meta", str(self.root / "m.db")])
        self.assertEqual(rc, 2)          # 不是抛异常、也不是 0

    def test_bad_since_format_returns_2(self):
        db = _db_with_errors(self.root)
        meta = self.root / "m.db"
        rc = main(["harvest-eval", "--db", str(db), "--meta", str(meta),
                   "--since", "2026/10/05"])       # 斜杠格式必须被拒
        self.assertEqual(rc, 2)
        # 合法格式（含 HH:MM:SS）才放行
        self.assertEqual(main(["harvest-eval", "--db", str(db), "--meta", str(meta),
                               "--since", "2026-10-08 16:00:00",
                               "--out", str(self.root / "r.md")]), 0)

    def test_pattern_note_drives_evaluation_and_report_lands(self):
        """`note` 里的 `pattern:` 生效：前窗 2 条命中 → 报告里能看到样本数。"""
        db = _db_with_errors(self.root)
        meta = self.root / "m.db"
        set_status(meta, "旧路径规则。", "adopted",
                   adopted_at="2026-10-08 16:00:00", note=f"pattern: {_ERR}")
        out = self.root / "eval.md"
        rc = main(["harvest-eval", "--db", str(db), "--meta", str(meta),
                   "--out", str(out)])
        self.assertEqual(rc, 0)
        text = out.read_text(encoding="utf-8")
        self.assertIn("自进化评估", text)
        self.assertIn("旧路径规则。", text)
        # 前窗 2 条（pattern 命中）；若无 `pattern:` 桥则命中 0，这行会变
        self.assertIn("2 条 / 未解决", text)
        self.assertIn("不下结论", text)          # 样本不足，不许编结论

    def test_topic_rows_are_skipped_not_evaluated(self):
        db = _db_with_errors(self.root)
        meta = self.root / "m.db"
        set_status(meta, "topic:tp-1", "adopted",
                   adopted_at="2026-10-08 16:00:00")
        out = self.root / "eval.md"
        main(["harvest-eval", "--db", str(db), "--meta", str(meta),
              "--out", str(out)])
        text = out.read_text(encoding="utf-8")
        self.assertIn("主题级台账 1 条已跳过", text)
        self.assertNotIn("| topic:tp-1 |", text)   # 不进评估表


class LedgerSyncCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.topics = self.root / "topics_meta.db"
        con = sqlite3.connect(str(self.topics))
        try:
            con.executescript(_TOPICS_DDL)
            con.execute("INSERT INTO topics VALUES (?,?,?,?,?)",
                        ("tp-1", "小说", "k", '["a"]', "2026-10-08 15:55"))
            con.execute("INSERT INTO sessions_noise VALUES (?,?,?)",
                        ("s1", "噪声", "2026-10-08 15:55"))
            con.commit()
        finally:
            con.close()
        self.meta = self.root / "suggestions_meta.db"

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def test_dry_run_writes_nothing_then_apply_is_idempotent(self):
        rc = main(["ledger-sync", "--meta", str(self.meta),
                   "--topics-meta", str(self.topics)])
        self.assertEqual(rc, 0)
        self.assertEqual(load_records(self.meta), {})      # dry-run 一行不写
        self.assertEqual(main(["ledger-sync", "--meta", str(self.meta),
                               "--topics-meta", str(self.topics),
                               "--apply"]), 0)
        rec = load_records(self.meta)
        self.assertEqual(list(rec), ["topic:tp-1"])
        self.assertEqual(rec["topic:tp-1"]["adopted_at"], "2026-10-08 15:55:00")
        self.assertIn("小说", rec["topic:tp-1"]["note"])
        again = main(["ledger-sync", "--meta", str(self.meta),
                      "--topics-meta", str(self.topics), "--apply"])
        self.assertEqual(again, 0)
        self.assertEqual(len(load_records(self.meta)), 1)   # 幂等

    def test_empty_registry_is_not_an_error(self):
        empty = self.root / "empty.db"
        sqlite3.connect(str(empty)).close()
        rc = main(["ledger-sync", "--meta", str(self.meta),
                   "--topics-meta", str(empty)])
        self.assertEqual(rc, 0)
        self.assertEqual(load_records(self.meta), {})


class SuggestStatusCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.meta = Path(self.tmp.name) / "m.db"

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def test_bad_adopted_at_returns_2_and_writes_nothing(self):
        rc = main(["suggest-status", "--meta", str(self.meta), "--key", "规则。",
                   "--status", "adopted", "--adopted-at", "2026-1-2"])
        self.assertEqual(rc, 2)                 # 非零填充必须被拒
        self.assertEqual(load_records(self.meta), {})
        self.assertEqual(main(["suggest-status", "--meta", str(self.meta),
                               "--key", "规则。", "--status", "adopted",
                               "--adopted-at", "2026-10-08",
                               "--note", "pattern: some err"]), 0)
        rec = load_records(self.meta)["规则。"]
        self.assertEqual(rec["adopted_at"], "2026-10-08")
        self.assertEqual(rec["note"], "pattern: some err")


if __name__ == "__main__":
    unittest.main()
