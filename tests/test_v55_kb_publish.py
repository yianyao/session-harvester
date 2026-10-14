# -*- coding: utf-8 -*-
"""kb-publish：项目正本 → WorkBuddy 知识库镜像位（v0.66，用户裁决 2026-10-14）。

背景：此前卡片/chain 的"正本"放在 WorkBuddy 私有目录——迁移项目等于丢数据，
被用户定性为"哪门子道理"。裁决后：正本=仓内 knowledge/（随项目走），
WorkBuddy 位只作发布镜像。本门钉住发布语义的四条红线：
  ① 逐文件"缺失或正本较新才覆盖"（幂等：重跑 copied=0）；
  ② **永不删除**镜像位里的多余文件（镜像位归 WorkBuddy 管，只喂不收）；
  ③ 只碰 cards/ 与 topics/ 两个子目录（feedback/handoffs 等主程序自有物不动）；
  ④ 正本缺失 fail loud（FileNotFoundError），子目录缺失如实上报不装成功。
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from harvester.cli import build_parser
from harvester.distill import PUBLISH_SUBDIRS, publish_master


def _touch(p: Path, text: str = "x", mtime: float | None = None) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


class PublishMasterTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.master = self.base / "knowledge"
        self.mirror = self.base / "mirror"

    def tearDown(self):
        self._tmp.cleanup()

    def test_copies_cards_and_topics_then_idempotent(self):
        _touch(self.master / "cards" / "a.md", "card-a")
        _touch(self.master / "topics" / "chain-t.md", "chain")
        rep = publish_master(self.master, self.mirror)
        self.assertEqual(rep["cards"]["copied"], 1)
        self.assertEqual(rep["topics"]["copied"], 1)
        self.assertEqual((self.mirror / "cards" / "a.md").read_text(
            encoding="utf-8"), "card-a")
        # 幂等：正本没变，重跑 = 0 复制
        rep2 = publish_master(self.master, self.mirror)
        self.assertEqual(rep2["cards"], {"copied": 0, "skipped": 1})
        self.assertEqual(rep2["topics"], {"copied": 0, "skipped": 1})

    def test_newer_master_overwrites_stale_mirror(self):
        old = time.time() - 3600
        _touch(self.master / "cards" / "a.md", "v2")           # 新
        _touch(self.mirror / "cards" / "a.md", "v1", mtime=old)  # 旧
        rep = publish_master(self.master, self.mirror)
        self.assertEqual(rep["cards"]["copied"], 1)
        self.assertEqual((self.mirror / "cards" / "a.md").read_text(
            encoding="utf-8"), "v2")

    def test_never_deletes_extra_files_in_mirror(self):
        """镜像位里正本没有的文件必须原样保留（只喂不收）。"""
        _touch(self.master / "cards" / "a.md", "card-a")
        extra = _touch(self.mirror / "cards" / "human-made.md", "keep-me")
        publish_master(self.master, self.mirror)
        self.assertTrue(extra.is_file(), "镜像位多余文件被删——违反只喂不收")
        self.assertEqual(extra.read_text(encoding="utf-8"), "keep-me")

    def test_only_touches_cards_and_topics(self):
        """feedback/handoffs 是 WorkBuddy 主程序自己的东西，发布永不碰。"""
        _touch(self.master / "cards" / "a.md", "card-a")
        host_own = _touch(self.mirror / "feedback" / "f.json", "{}")
        publish_master(self.master, self.mirror)
        self.assertTrue(host_own.is_file())
        self.assertFalse((self.mirror / "feedback").is_symlink())

    def test_missing_master_fails_loud_and_missing_subdir_reported(self):
        with self.assertRaises(FileNotFoundError):
            publish_master(self.base / "no-such-master", self.mirror)
        # 正本在、但只有 cards/ 没有 topics/ → 如实上报，不装成功
        _touch(self.master / "cards" / "a.md", "card-a")
        rep = publish_master(self.master, self.mirror)
        self.assertEqual(rep["missing"], ["topics"])
        self.assertEqual(rep["cards"]["copied"], 1)

    def test_publish_subdirs_frozen(self):
        """PUBLISH_SUBDIRS 只许是 cards+topics：多一个就可能碰主程序自有目录。"""
        self.assertEqual(tuple(sorted(PUBLISH_SUBDIRS)), ("cards", "topics"))

    def test_cli_wiring(self):
        p = build_parser()
        ns = p.parse_args(["kb-publish", "--root", "k", "--dest", "d"])
        self.assertEqual(ns.root, "k")
        self.assertEqual(ns.dest, "d")
        self.assertTrue(callable(ns.func))
        # 缺省值：正本=仓根相对 knowledge/，镜像=WorkBuddy 知识库
        ns2 = p.parse_args(["kb-publish"])
        self.assertEqual(ns2.root, "knowledge")
        self.assertIn(".workbuddy", ns2.dest)


if __name__ == "__main__":
    unittest.main()
