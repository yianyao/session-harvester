# -*- coding: utf-8 -*-
"""v0.47 测试：`sync` 收尾自动回收 `keywords_meta.db`（评审 v5 第 7 条 / B10）。

**要做进工具的理由**（项目铁律"能做成工具的别靠人记"）：`keyword_stats` 是
**累积表**——每次 `harvester keywords` 追加一整份 n-gram 表（真库实测 4 次 run =
435,583 行 / 40.2 MB，空闲页只有 VACUUM 能回收）。v0.45 才发现它在涨，靠的就是
"记得手动跑 `keywords-gc`"——这条链注定会忘。

判据（说清何时会红）：
- 默认 `sync` 后 `keyword_runs` 不止 1 行 → 红（默认口径就是"只留最新"）；
- 留下**孤儿统计行**（`keyword_stats.run_id` 指向已删的 run）→ 红
  （比不删更坏：占着空间却读不到——`prune_runs` 的既有断言口径）；
- `keywords_meta=False` 或 `--no-prune-keywords` 时**动了一行** → 红；
- `keep_keyword_runs=N` 没被尊重 → 红；
- 没有 `keywords_meta.db` 时**抛异常/报错**（而非静默跳过）→ 红，因为 sync 是
  可挂定时任务的全流程，收尾动作不该让整轮失败。

夹具走**真的 `run_sync`**（临时库里跑完整的 收割→导出→索引→GC），不 mock 主流程
——只有这样才能同时证明"GC 插在正确位置、且不影响前面几步"。
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester.cli import build_parser
from harvester.kwstats import SCHEMA as KW_SCHEMA
from harvester.sync import run_sync

_CONVS = [{
    "id": "conv-kw",
    "title": "keywords GC 夹具会话",
    "inserted_at": "2026-10-01T10:00:00+08:00",
    "updated_at": "2026-10-01T10:02:00+08:00",
    "mapping": {
        "root": {"id": "root", "parent": None, "children": ["n1"],
                 "message": None},
        "n1": {"id": "n1", "parent": "root", "children": [],
               "message": {"model": "deepseek-chat",
                           "inserted_at": "2026-10-01T10:00:00+08:00",
                           "fragments": [{"type": "REQUEST",
                                          "content": "keywords gc fixture"}]}},
    },
}]


def _make_kw_meta(path: Path, runs: int) -> None:
    """造 `runs` 次 run，每次带 3 条统计行（模拟累积表）。"""
    con = sqlite3.connect(str(path))
    try:
        con.executescript(KW_SCHEMA)
        for i in range(runs):
            cur = con.execute(
                "INSERT INTO keyword_runs(generated_at, params, db_fingerprint)"
                " VALUES (?,?,?)",
                (f"2026-10-0{i + 1}T10:00:00+08:00", "{}", f"fp{i}"))
            rid = cur.lastrowid
            for n, gram in ((2, "夹具"), (2, "会话"), (3, "夹具会")):
                con.execute(
                    "INSERT INTO keyword_stats(run_id, n, gram, freq, doc_freq)"
                    " VALUES (?,?,?,?,?)", (rid, n, gram, 3, 1))
        con.commit()
    finally:
        con.close()


def _runs(path: Path) -> list[int]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return [r[0] for r in con.execute(
            "SELECT id FROM keyword_runs ORDER BY id")]
    finally:
        con.close()


def _orphan_stats(path: Path) -> int:
    """统计行里有多少条指向**已不存在**的 run（必须为 0）。"""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return con.execute(
            "SELECT count(*) FROM keyword_stats s WHERE NOT EXISTS"
            " (SELECT 1 FROM keyword_runs r WHERE r.id = s.run_id)"
        ).fetchone()[0]
    finally:
        con.close()


class SyncKeywordsGcTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        p = self.root / "inbox" / "conversations.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(_CONVS, ensure_ascii=False), encoding="utf-8",
                     newline="\n")
        # 默认 keywords 库位置（root/keywords_meta.db）
        self.kwmeta = self.root / "keywords_meta.db"

    def tearDown(self):
        self.tmp.cleanup()

    def _sync(self, **kw) -> dict:
        return run_sync(root=self.root,
                        sources_path=self.root / "sources.json",
                        db_path=self.root / "harvester.db",
                        inbox_dir=self.root / "inbox",
                        exports_dir=self.root / "exports",
                        only={"deepseek-export"}, **kw)

    def test_default_keeps_only_latest_run_and_no_orphans(self):
        """默认：3 次 run → 只剩最新 1 次，且**没有孤儿统计行**。"""
        _make_kw_meta(self.kwmeta, 3)
        before = _runs(self.kwmeta)
        self.assertEqual(before, [1, 2, 3])
        r = self._sync()
        gc = r["keywords_gc"]
        self.assertFalse(gc["skipped"], gc)
        self.assertEqual(gc["deleted_runs"], 2)
        self.assertEqual(_runs(self.kwmeta), [3])
        self.assertEqual(_orphan_stats(self.kwmeta), 0,
                         "留下了指向已删 run 的孤儿统计行——比不删更坏")
        self.assertTrue(gc["vacuumed"], "删了行就该 VACUUM（自由页只有它能回收）")

    def test_single_run_is_noop_and_does_not_vacuum(self):
        """幂等：只剩 1 次 run 时什么都不删、也不 VACUUM。"""
        _make_kw_meta(self.kwmeta, 1)
        r = self._sync()
        gc = r["keywords_gc"]
        self.assertEqual(gc["deleted_runs"], 0)
        self.assertFalse(gc["vacuumed"])
        self.assertEqual(_runs(self.kwmeta), [1])

    def test_keep_runs_argument_is_respected(self):
        """`keep_keyword_runs=2` → 留最近 2 次（证明不是写死 1）。"""
        _make_kw_meta(self.kwmeta, 3)
        self._sync(keep_keyword_runs=2)
        self.assertEqual(_runs(self.kwmeta), [2, 3])
        self.assertEqual(_orphan_stats(self.kwmeta), 0)

    def test_explicit_false_does_not_touch_the_meta_db(self):
        """`keywords_meta=False` = 明确关闭：**一行都不许动**。"""
        _make_kw_meta(self.kwmeta, 3)
        r = self._sync(keywords_meta=False)
        self.assertTrue(r["keywords_gc"]["skipped"], r["keywords_gc"])
        self.assertEqual(_runs(self.kwmeta), [1, 2, 3])

    def test_missing_meta_is_skipped_not_an_error(self):
        """没有 keywords_meta.db（没跑过 keywords）→ 跳过，且 sync 照常成功。"""
        self.assertFalse(self.kwmeta.exists())
        r = self._sync()
        self.assertTrue(r["keywords_gc"]["skipped"], r["keywords_gc"])
        self.assertIn("keywords_meta.db", r["keywords_gc"]["reason"])
        self.assertGreater(r["sessions_total"], 0, "sync 本体必须照常完成")

    def test_custom_meta_path_is_honored(self):
        """`keywords_meta=<别的路径>` 时回收的是**那一个**，不是默认位置。"""
        other = self.root / "sub" / "kw.db"
        other.parent.mkdir(parents=True, exist_ok=True)
        _make_kw_meta(other, 2)
        _make_kw_meta(self.kwmeta, 2)
        r = self._sync(keywords_meta=other)
        self.assertFalse(r["keywords_gc"]["skipped"])
        self.assertEqual(_runs(other), [2])
        self.assertEqual(_runs(self.kwmeta), [1, 2], "默认位置不该被碰")


class SyncCliFlagsTest(unittest.TestCase):
    """CLI 层：参数与默认值（README↔CLI 漂移门只查子命令名，不查参数语义）。"""

    def _parse(self, *argv):
        return build_parser().parse_args(list(argv))

    def test_defaults(self):
        a = self._parse("sync")
        self.assertEqual(a.keywords_meta, "keywords_meta.db")
        self.assertEqual(a.keywords_keep_runs, 1)
        self.assertFalse(a.no_prune_keywords)

    def test_opt_out_and_keep_runs(self):
        a = self._parse("sync", "--no-prune-keywords", "--keywords-keep-runs", "3")
        self.assertTrue(a.no_prune_keywords)
        self.assertEqual(a.keywords_keep_runs, 3)


if __name__ == "__main__":
    unittest.main()
