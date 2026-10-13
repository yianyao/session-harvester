# -*- coding: utf-8 -*-
"""v0.52（会话 8）测试：`suppress candidates`——抑制候选清单（**去 sid 化**）。

用户 2026-10-13 原话："我也不可能知道 sid 是哪些啊（这其实也是问题所在，
一般人谁记这个）"——原工作流要人点名 sid，反人类。本功能 = 工具先出
**机械候选**，人按标题/序号裁决。两类候选：
  ① 重复会话：会话级 user-raw bigram Jaccard ≥0.90（H40 冻结口径，
     复用 `topicprep.dedupe_items`，本模块不复刻阈值）；每簇保留
     **created_at 最早**一条为代表，其余为候选；
  ② 零 user 轮次：库里没有任何 role='user' 消息的会话（纯背景噪声）。

钉住的不变式：
1. **只读**：跑候选清单前后采集库字节不变；meta 库缺失也不创建
   （「读路径不建表」既有教训）；
2. 重复簇代表 = 最早创建；J 值来自 dup_pairs 双向可查；
3. 已在抑制表里的 sid 被剔除，不重复提名；
4. CLI 退出码 0，输出含标题 + sid + 复制就绪命令；**绝不自动执行**。
夹具：`indexing.SCHEMA` 真冻结 DDL + 直插（列名不会漂）。
"""
from __future__ import annotations

import argparse
import contextlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester.indexing import SCHEMA
from harvester.suppress import find_candidates


def _mkdb():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    con = sqlite3.connect(f.name)
    con.executescript(SCHEMA)
    return con, Path(f.name)


def _sess(con, sid, title, created):
    con.execute("INSERT INTO sessions(sid,source,session_id,title,category,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (sid, "test", sid.split(":", 1)[-1], title, None,
                 created, created))


def _msg(con, sid, role, raw):
    con.execute("INSERT INTO messages(sid,role,ts,text,raw) "
                "VALUES(?,?,datetime('now'),'',?)", (sid, role, raw))


DUP_TEXT = "这是一段足够长的重复正文，用来让两个会话的 bigram 集合几乎一致。"


class TestFindCandidates(unittest.TestCase):
    def _fixture(self):
        con, path = _mkdb()
        _sess(con, "t:a", "重复会话甲", "2026-01-01 00:00:00")
        _sess(con, "t:b", "重复会话乙", "2026-01-02 00:00:00")
        _sess(con, "t:c", "独立会话丙", "2026-01-03 00:00:00")
        _sess(con, "t:d", "纯噪声丁", "2026-01-04 00:00:00")
        for s in ("t:a", "t:b"):
            _msg(con, s, "user", DUP_TEXT)
            _msg(con, s, "note", "工具输出")
        _msg(con, "t:c", "user", "完全不同的另一段正文，讲的是别的事情。")
        _msg(con, "t:d", "note", "只有系统/工具消息，没有用户说过话。")
        con.commit()
        return con, path

    def test_dup_cluster_and_zero_user(self):
        con, path = self._fixture()
        try:
            before = Path(path).read_bytes()
            r = find_candidates(path)
            self.assertEqual(Path(path).read_bytes(), before)  # 只读
        finally:
            con.close()
        self.assertEqual(r["scanned"], 4)
        self.assertEqual(len(r["dup_clusters"]), 1)
        c = r["dup_clusters"][0]
        self.assertEqual(c["rep"], "t:a")            # 最早创建者为代表
        self.assertEqual(c["rep_title"], "重复会话甲")
        self.assertEqual([x["sid"] for x in c["candidates"]], ["t:b"])
        self.assertIsNotNone(c["candidates"][0]["jaccard"])
        self.assertGreaterEqual(c["candidates"][0]["jaccard"], 0.90)
        self.assertEqual([z["sid"] for z in r["zero_user"]], ["t:d"])

    def test_known_sids_filtered(self):
        con, path = self._fixture()
        try:
            r = find_candidates(path, known_sids={"t:b", "t:d"})
        finally:
            con.close()
        self.assertEqual(r["dup_clusters"], [])      # 唯一候选已被抑制
        self.assertEqual(r["zero_user"], [])

    def test_empty_db(self):
        con, path = _mkdb()
        con.commit()
        try:
            r = find_candidates(path)
        finally:
            con.close()
        self.assertEqual(r, {"dup_clusters": [], "zero_user": [],
                             "scanned": 0})


class TestCli(unittest.TestCase):
    def test_cli_candidates_readonly_and_output(self):
        con, path = TestFindCandidates._fixture(unittest.TestCase())
        con.close()
        meta = Path(tempfile.mkdtemp()) / "topics_meta.db"   # 不存在
        out, err = io.StringIO(), io.StringIO()
        ns = argparse.Namespace(action="candidates", db=str(path),
                                meta=str(meta))
        from harvester.cli import cmd_suppress
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cmd_suppress(ns)
        self.assertEqual(rc, 0)
        self.assertFalse(meta.exists())              # 读路径不建表
        text = out.getvalue()
        self.assertIn("重复会话甲", text)             # 标题可见（人按标题裁决）
        self.assertIn("t:b", text)                   # sid 也在（命令要用）
        self.assertIn("suppress add", text)          # 复制就绪命令
        self.assertIn("零 user 轮次", text)
        self.assertIn("纯噪声丁", text)

    def test_cli_missing_db_clean_report(self):
        out, err = io.StringIO(), io.StringIO()
        ns = argparse.Namespace(action="candidates",
                                db="Z:/definitely/not/here.db",
                                meta="Z:/definitely/not/meta.db")
        from harvester.cli import cmd_suppress
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cmd_suppress(ns)
        self.assertEqual(rc, 0)
        self.assertIn("0", out.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
