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
from harvester.suppress import filter_dup_clusters, find_candidates


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


class TestFilterByMinJaccard(unittest.TestCase):
    """v0.53（用户 2026-10-14 明示）：`suppress candidates --min-jaccard`。

    用户原话："命令参数里有没一个按 J 值大小来做批量处理的？"——裁决
    工作流是"看清单 → 点口径"，阈值过滤就该长在清单命令上。钉住的不变式：
    ① 只过滤**有 J 值**的候选；② 链式候选（无独立 J，jaccard=None）不参与
    过滤、始终保留（它经簇内其他成员 ≥0.90 间接相连，不是"低 J"）；
    ③ 候选被清空的簇整簇隐藏；④ 原结果 dict 不被改（过滤返回新 dict）；
    ⑤ CLI 给非法阈值（不在 0~1）要报错退出码 2，不许静默当没给。
    """

    def _synthetic(self):
        return {
            "scanned": 10,
            "zero_user": [{"sid": "t:z", "title": "零轮次"}],
            "dup_clusters": [
                {"rep": "t:a", "rep_title": "甲",
                 "candidates": [
                     {"sid": "t:b", "title": "乙", "jaccard": 0.98},
                     {"sid": "t:c", "title": "丙", "jaccard": 0.91},
                     {"sid": "t:d", "title": "丁", "jaccard": None},
                 ]},
                {"rep": "t:e", "rep_title": "戊",
                 "candidates": [
                     {"sid": "t:f", "title": "己", "jaccard": 0.905},
                 ]},
            ],
        }

    def test_filters_low_j_keeps_chain_and_zero_user(self):
        r = self._synthetic()
        out = filter_dup_clusters(r, 0.92)
        self.assertIsNot(r, out)                     # 原 dict 不动
        self.assertEqual(len(out["dup_clusters"]), 1)  # 戊簇清空 → 整簇隐藏
        cands = out["dup_clusters"][0]["candidates"]
        self.assertEqual([x["sid"] for x in cands], ["t:b", "t:d"])
        self.assertEqual(out["dropped_low_j"], 2)    # 丙 0.91 + 己 0.905
        self.assertEqual(out["zero_user"], r["zero_user"])  # 零轮次不受影响
        self.assertEqual(len(r["dup_clusters"][0]["candidates"]), 3)  # 原 dict 未被改

    def test_nothing_dropped_when_threshold_low(self):
        r = self._synthetic()
        out = filter_dup_clusters(r, 0.90)
        self.assertEqual(out["dropped_low_j"], 0)
        self.assertEqual(len(out["dup_clusters"]), 2)

    def test_cli_min_jaccard_passes_and_notes(self):
        con, path = TestFindCandidates._fixture(unittest.TestCase())
        con.close()                                  # 夹具候选 J≈1.0，阈值 0.95 仍保留
        meta = Path(tempfile.mkdtemp()) / "topics_meta.db"
        out, err = io.StringIO(), io.StringIO()
        ns = argparse.Namespace(action="candidates", db=str(path),
                                meta=str(meta), min_jaccard=0.95)
        from harvester.cli import cmd_suppress
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cmd_suppress(ns)
        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertIn("0.95", text)                  # 过滤说明可见
        self.assertIn("t:b", text)                   # J≈1.0 候选仍在
        self.assertIn("链式", text)                  # 白话说明：链式始终保留

    def test_cli_min_jaccard_invalid_range_fails_loud(self):
        meta = Path(tempfile.mkdtemp()) / "topics_meta.db"
        out, err = io.StringIO(), io.StringIO()
        ns = argparse.Namespace(action="candidates",
                                db="Z:/definitely/not/here.db",
                                meta=str(meta), min_jaccard=1.5)
        from harvester.cli import cmd_suppress
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cmd_suppress(ns)
        self.assertEqual(rc, 2)
        self.assertIn("0~1", err.getvalue())         # 错误走 stderr，含合法范围
        self.assertFalse(meta.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
