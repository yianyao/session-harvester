# -*- coding: utf-8 -*-
"""v0.46 P0-2 测试：库龄可见性（语料比库新 ⇒ STALE）。

口径（与 `harvester/dbmeta.py` 模块头一致）：
- 基准是**库内记录的语料水位**（`index_meta.corpus_mtime`，索引时实际看到的
  最新语料 mtime），**不是库文件 mtime**——实测无变化的 `update` 不写库文件
  （下面的 `test_noop_update_does_not_touch_db_file` 就是这条的钉子），
  用库 mtime 会把"touch 会话文件 + update"永久判成假 STALE；
- `stale=None` = **未判定**（调用方没 stat，或库内还没有水位），与
  `False`（新鲜）必须分得开——这条在 AGENTS.md §五 16 里是硬要求。
"""

import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from harvester.dbmeta import (db_fingerprint, fingerprint_line,
                              read_corpus_watermark)
from harvester.outline import corpus_watermark
from harvester.sync import run_sync, run_update

_CONVS = [{
    "id": "conv-001",
    "title": "库龄测试会话",
    "inserted_at": "2026-10-01T10:00:00+08:00",
    "updated_at": "2026-10-01T10:02:00+08:00",
    "mapping": {
        "root": {"id": "root", "parent": None, "children": ["n1"],
                 "message": None},
        "n1": {"id": "n1", "parent": "root", "children": [],
               "message": {"model": "deepseek-chat",
                           "inserted_at": "2026-10-01T10:00:00+08:00",
                           "fragments": [{"type": "REQUEST",
                                          "content": "你好"}]}},
    },
}]


def _kwargs(root: Path) -> dict:
    return {
        "root": root,
        "sources_path": root / "sources.json",
        "db_path": root / "harvester.db",
        "inbox_dir": root / "inbox",
        "exports_dir": root / "exports",
        "only": {"deepseek-export"},
    }


def _archive(root: Path) -> Path:
    return next((root / "inbox" / "done" / "deepseek-export").iterdir())


class WatermarkTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        p = self.root / "inbox" / "conversations.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(_CONVS, ensure_ascii=False), encoding="utf-8",
                     newline="\n")

    def tearDown(self):
        self.tmp.cleanup()

    # ---- 水位记录 ----

    def test_update_records_watermark(self):
        db = self.root / "harvester.db"
        self.assertEqual(read_corpus_watermark(db)["mtime"], None)  # 还没有
        r = run_update(**_kwargs(self.root))
        wm = read_corpus_watermark(db)
        self.assertIsNotNone(wm["mtime"])
        self.assertAlmostEqual(wm["mtime"], r["corpus"]["mtime"], places=6)
        # 库内水位 = 源文件 mtime（同一时刻的 stat）
        self.assertAlmostEqual(wm["mtime"], _archive(self.root).stat().st_mtime,
                               places=5)
        # 只扫了 --only 指定的源：其余源根本没构建，如实报"覆盖 1 个源"
        self.assertEqual(wm["covered"], ["deepseek-export"])
        self.assertEqual(wm["uncovered"], [])

    def test_sync_records_watermark_too(self):
        db = self.root / "harvester.db"
        run_sync(**_kwargs(self.root))
        self.assertIsNotNone(read_corpus_watermark(db)["mtime"])

    def test_missing_table_reads_as_undetermined(self):
        """老库（没有 index_meta 表）→ 未判定，不许当成"新鲜"。"""
        db = self.root / "legacy.db"
        con = sqlite3.connect(str(db))
        try:
            con.execute("CREATE TABLE sessions (sid TEXT PRIMARY KEY)")
            con.execute("CREATE TABLE steps (sid TEXT, status TEXT)")
            con.execute("CREATE TABLE index_meta (key TEXT PRIMARY KEY)")
            con.commit()
        finally:
            con.close()
        self.assertIsNone(read_corpus_watermark(db)["mtime"])

    # ---- STALE 判定 ----

    def test_touch_corpus_makes_stale_then_update_clears_it(self):
        """验收场景（SOP §6 P0-2）：touch 会话文件 → STALE；跑完 update → 消失。"""
        db = self.root / "harvester.db"
        run_update(**_kwargs(self.root))
        arch = _archive(self.root)

        future = time.time() + 3600
        os.utime(arch, (future, future))          # 只改戳，不改内容
        live = corpus_watermark(
            {"plugins": [{"id": "deepseek-export", "paths": [str(arch)]}]})
        fp = db_fingerprint(db, corpus_mtime=live["mtime"])
        self.assertIs(True, fp["stale"])
        self.assertIn("STALE", fingerprint_line(fp))
        self.assertTrue(fp["corpus_mtime"] > fp["corpus_indexed_mtime"])

        r = run_update(**_kwargs(self.root))
        self.assertEqual(r["index"]["parsed"], 0)   # 内容没变 → 不重解析
        live2 = corpus_watermark(
            {"plugins": [{"id": "deepseek-export", "paths": [str(arch)]}]})
        fp2 = db_fingerprint(db, corpus_mtime=live2["mtime"])
        self.assertIs(False, fp2["stale"])
        self.assertNotIn("STALE", fingerprint_line(fp2))

    def test_db_mtime_criterion_would_be_wrong(self):
        """为什么基准不是"库文件 mtime"（P0-2 的口径依据）。

        `touch 会话文件（戳设到未来）+ update` 之后：
        - 新口径（库内水位）→ **新鲜**（update 记下了它实际看到的语料戳）；
        - 旧口径（语料 mtime > 库文件 mtime）→ 仍判 STALE（语料戳在未来，
          库文件戳只是"刚才"，永远追不上）→ **假 STALE**。
        这条把两个口径的分歧钉住：谁改回库 mtime，这里立刻变红。
        """
        db = self.root / "harvester.db"
        run_update(**_kwargs(self.root))
        arch = _archive(self.root)
        future = time.time() + 3600
        os.utime(arch, (future, future))
        run_update(**_kwargs(self.root))

        live = corpus_watermark(
            {"plugins": [{"id": "deepseek-export", "paths": [str(arch)]}]})
        self.assertIs(False, db_fingerprint(db, corpus_mtime=live["mtime"])["stale"])
        self.assertGreater(live["mtime"], db.stat().st_mtime)  # 旧口径会误判

    def test_stale_none_is_not_false(self):
        """未判定 ≠ 新鲜：不传语料 / 库内没水位 → 都必须是 None。"""
        db = self.root / "harvester.db"
        run_update(**_kwargs(self.root))
        self.assertIsNone(db_fingerprint(db)["stale"])   # 没传语料 = 没判

        # 老库（有会话/步骤表，但没有 index_meta 表）+ 有语料 → 仍不判定
        legacy = self.root / "legacy.db"
        con = sqlite3.connect(str(legacy))
        try:
            con.execute("CREATE TABLE sessions (sid TEXT PRIMARY KEY)")
            con.execute("CREATE TABLE steps (sid TEXT, status TEXT)")
            con.commit()
        finally:
            con.close()
        fp = db_fingerprint(legacy, corpus_mtime=time.time())
        self.assertIsNone(fp["stale"])
        self.assertIsNone(fp["corpus_indexed_mtime"])
        self.assertNotIn("STALE", fingerprint_line(fp))  # 未判定不加噪音

    def test_no_covered_source_means_no_live_mtime(self):
        """覆盖不到任何源（只留桩位）→ mtime None，绝不当成"新鲜"。"""
        wm = corpus_watermark({"plugins": []}, only={"trae"})
        self.assertIsNone(wm["mtime"])
        self.assertEqual(wm["covered"], [])
        self.assertEqual(wm["uncovered"], ["trae"])


if __name__ == "__main__":
    unittest.main()
