# -*- coding: utf-8 -*-
"""v0.22 T5 自动聚类候选推荐器测试——SOP §SOP-T5。

规格：标题 n-gram + 任务签名（首条 user 消息归一 + 工具序列 top-k）
产**候选**（推荐器，绝不改注册表权威）；候选准确率由用户判定并记录。

通用性（用户红线）：不写死任何主题/会话/skill——特征全部从库内数据
计算，已注册成员由 --topics-meta 注册表动态排除；fixture 一律中性。
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester.candidates import (build_candidates, render_candidates,
                                  task_signature)


def make_db(path: Path, sessions: list[dict]) -> None:
    """sessions={sid,title,first_user,tools:[(tool,status)...]}。

    schema 最小化对齐索引库（sessions/messages/steps）。"""
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE IF NOT EXISTS sessions("
        " sid TEXT PRIMARY KEY, source TEXT, sid2 TEXT, title TEXT,"
        " category TEXT, created_at TEXT, updated_at TEXT, file TEXT);"
        "CREATE TABLE IF NOT EXISTS messages("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, sid TEXT, role TEXT,"
        " ts TEXT, text TEXT, raw TEXT);"
        "CREATE TABLE IF NOT EXISTS steps("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, sid TEXT, seq INTEGER,"
        " ts TEXT, tool TEXT, phase TEXT, status TEXT, error TEXT,"
        " detail TEXT);")
    for i, s in enumerate(sessions):
        sid = s["sid"]
        con.execute("INSERT INTO sessions(sid, source, sid2, title,"
                    " category, created_at, updated_at, file)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (sid, "src", sid, s["title"], "", "", "", ""))
        if s.get("first_user"):
            con.execute("INSERT INTO messages(sid, role, ts, text, raw)"
                        " VALUES (?,?,?,?,?)",
                        (sid, "user", f"2026-01-0{i} 10:00:00", "",
                         s["first_user"]))
        for j, (tool, status) in enumerate(s.get("tools", [])):
            con.execute("INSERT INTO steps(sid, seq, ts, tool, phase,"
                        " status, error, detail) VALUES (?,?,?,?,?,?,?,?)",
                        (sid, j, "", tool, "call", "ok" if status != "error"
                         else "error", "", ""))
    con.commit()
    con.close()


class TestTaskSignature(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.db = self.dir / "t.db"
        make_db(self.db, [
            {"sid": "s1", "title": "叙事节奏打磨",
             "first_user": "帮我看看这一章的节奏问题",
             "tools": [("Read", "ok"), ("Read", "ok"), ("Grep", "ok"),
                       ("Edit", "ok")]}])

    def test_signature_from_db(self):
        sig = task_signature(self.db, "s1", top_tools=3)
        # 工具序列 top-k：Read 出现 2 次排最前
        self.assertEqual(sig["top_tools"][0], "Read")
        self.assertIn("Read", sig["top_tools"])
        self.assertIn("Grep", sig["top_tools"])
        # 首条 user raw 归一后非空（去空白）
        self.assertTrue(sig["first_user_norm"])
        self.assertNotIn(" ", sig["first_user_norm"])

    def test_missing_session(self):
        sig = task_signature(self.db, "nope", top_tools=3)
        self.assertEqual(sig["top_tools"], [])
        self.assertEqual(sig["first_user_norm"], "")


class TestBuildCandidates(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.db = self.dir / "t.db"
        self.meta = self.dir / "topics.db"
        make_db(self.db, [
            # 簇 A：三个明显同题（标题/首问/工具谱系接近）
            {"sid": "a1", "title": "叙事节奏分析",
             "first_user": "分析这章的节奏问题",
             "tools": [("Read", "ok"), ("Edit", "ok")]},
            {"sid": "a2", "title": "叙事节奏讨论",
             "first_user": "看看节奏问题在哪里",
             "tools": [("Read", "ok"), ("Edit", "ok")]},
            {"sid": "a3", "title": "叙事节奏复盘",
             "first_user": "节奏问题出在哪一章",
             "tools": [("Read", "ok")]},
            # 簇 B：异题（另一工作面）
            {"sid": "b1", "title": "打包发布流程",
             "first_user": "帮我发布这个网站",
             "tools": [("Bash", "ok"), ("Write", "ok")]},
            {"sid": "b2", "title": "打包发布脚本",
             "first_user": "写个发布脚本",
             "tools": [("Bash", "ok"), ("Write", "ok")]},
            # 孤儿：与谁都不像
            {"sid": "z9", "title": "随手记录",
             "first_user": "今天天气不错",
             "tools": []},
        ])

    def test_two_clusters_found(self):
        r = build_candidates(self.db, min_sim=0.30, min_size=2)
        cids = {c["seed"] for c in r["clusters"]}
        self.assertEqual(len(r["clusters"]), 2)
        sizes = sorted(len(c["members"]) for c in r["clusters"])
        self.assertEqual(sizes, [2, 3])
        # 种子是"相似对最多"的会话，成员按 sim 降序
        for c in r["clusters"]:
            sims = [m["sim"] for m in c["members"]]
            self.assertEqual(sims, sorted(sims, reverse=True))
        _ = cids

    def test_threshold_separates(self):
        # 高阈值下弱相似对不成簇
        r = build_candidates(self.db, min_sim=0.95, min_size=2)
        self.assertEqual(r["clusters"], [])

    def test_registered_members_excluded(self):
        con = sqlite3.connect(self.meta)
        con.execute("CREATE TABLE topics(id TEXT PRIMARY KEY, name TEXT,"
                    " keywords TEXT, members TEXT, created TEXT)")
        con.execute("INSERT INTO topics VALUES ('tp-x','已注册','','"
                    "[{\"sid\": \"a1\"}]','')")
        con.commit()
        con.close()
        r = build_candidates(self.db, min_sim=0.30, min_size=2,
                             topics_meta=self.meta)
        all_sids = {m["sid"] for c in r["clusters"] for m in c["members"]}
        self.assertNotIn("a1", all_sids)

    def test_min_size_filters_orphans(self):
        r = build_candidates(self.db, min_sim=0.30, min_size=2)
        all_sids = {m["sid"] for c in r["clusters"] for m in c["members"]}
        self.assertNotIn("z9", all_sids)

    def test_no_registry_mutation(self):
        """红线：推荐器绝不改注册表——跑完候选后 topics 表必须原样。"""
        con = sqlite3.connect(self.meta)
        con.execute("CREATE TABLE topics(id TEXT PRIMARY KEY, name TEXT,"
                    " keywords TEXT, members TEXT, created TEXT)")
        con.commit()
        con.close()
        build_candidates(self.db, min_sim=0.30, min_size=2,
                         topics_meta=self.meta)
        con = sqlite3.connect(self.meta)
        n = con.execute("SELECT COUNT(*) FROM topics").fetchone()[0]
        con.close()
        self.assertEqual(n, 0)

    def test_suggests_keywords(self):
        r = build_candidates(self.db, min_sim=0.30, min_size=2)
        c = next(c for c in r["clusters"] if c["seed"] == "a1")
        self.assertTrue(c["suggested_keywords"])
        self.assertTrue(all(k for k in c["suggested_keywords"]))

    def test_render_report(self):
        r = build_candidates(self.db, min_sim=0.30, min_size=2)
        text = render_candidates(r)
        self.assertIn("候选", text)
        self.assertIn("topic register", text)  # 引导人工裁决后注册
        self.assertIn("不改注册表", text)


if __name__ == "__main__":
    unittest.main()
