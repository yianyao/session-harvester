# -*- coding: utf-8 -*-
"""v0.23 #15：T4 join 的 margin 方向语义——阶段内 vs 阶段后必须分开。

缺陷背景（2026-10-09 全量复查）：`--margin-days N` **只向后放宽**（接住
"阶段收尾后的工具化延续"）。但渲染时它与阶段**之内**的调用同为
kind=`temporal`/`member_span`，读者会把"阶段结束后 80 天"的调用误读成
"阶段之内的贡献"。真实库实测：阶段七 span 至 2026-07-10，而 --margin-days 90
命中的是 2026-09/10 的调用（chain 附录自称"分析参数非事实"，
但报告里没有区分）。

本测试钉死判据 `day > span_end`：
  阶段内         → kind=temporal        ，after_stage=False
  span 终点当日 → kind=temporal        ，after_stage=False（含尾日）
  margin 区内    → kind=temporal_after  ，after_stage=True
  margin 区外    → 不进该 stage（unassigned）
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester.skilljoin import build_cross, render_cross


def make_db(path: Path, sessions, steps, user_msgs) -> None:
    con = sqlite3.connect(str(path))
    con.executescript("""
    CREATE TABLE sessions (sid TEXT PRIMARY KEY, source TEXT, session_id TEXT,
                           title TEXT, category TEXT, created_at TEXT,
                           updated_at TEXT, file TEXT);
    CREATE TABLE messages (sid TEXT, role TEXT, ts TEXT, text TEXT, raw TEXT);
    CREATE TABLE steps (sid TEXT, seq INTEGER, ts TEXT, tool TEXT,
                        phase TEXT, status TEXT, error TEXT, detail TEXT);
    """)
    con.executemany("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?)", sessions)
    con.executemany("INSERT INTO messages VALUES (?,?,?,?,?)", user_msgs)
    con.executemany("INSERT INTO steps VALUES (?,?,?,?,?,?,?,?)", steps)
    con.commit()
    con.close()


def skill_call(sid: str, seq: int, ts: str, skill: str,
               status: str = "ok") -> list[tuple]:
    d = json.dumps({"skill": skill, "args": "任务"}, ensure_ascii=False)
    return [(sid, seq, ts, "Skill", "call", None, None, d),
            (sid, seq + 1, ts, "Skill", "result", status, None, None)]


#: 阶段二 span = 2026-03-01 ~ 2026-03-05（尾日 03-05）
CHAIN = """---
topic: 测试主题
topic_id: tp-test
members:
  - src:m1
generated_from: 测试
prompt_version: v1
anchors:
  - stage: 阶段一·起步
    span: 2026-01-01 ~ 2026-01-10
    nodes:
      - sid: src:m1
        turn: 1
        note: 起点
  - stage: 阶段二·理论化
    span: 2026-03-01 ~ 2026-03-05
    nodes:
      - sid: src:m1
        turn: 2
        note: 理论学习
---

正文。
"""

MARGIN = 10          # → 阶段二放宽到 2026-03-15


class TestAfterStageLabeling(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.chain = self.dir / "chain.md"
        self.chain.write_text(CHAIN, encoding="utf-8", newline="\n")

    def tearDown(self):
        self.tmp.cleanup()

    def _db(self, steps) -> Path:
        db = self.dir / "t.db"
        if db.exists():
            db.unlink()
        make_db(db, [
            ("src:m1", "src", "m1", "成员会话", "", "", "", ""),
            ("wb:in", "workbuddy-transcript", "in", "阶段内", "",
             "", "", ""),
            ("wb:edge", "workbuddy-transcript", "edge", "尾日", "",
             "", "", ""),
            ("wb:after", "workbuddy-transcript", "after", "阶段后", "",
             "", "", ""),
            ("wb:far", "workbuddy-transcript", "far", "远处", "",
             "", "", ""),
        ], steps, [])
        return db

    def _build(self, margin=MARGIN):
        steps = (skill_call("wb:in", 1, "2026-03-03 10:00:00", "demo-skill")
                 + skill_call("wb:edge", 1, "2026-03-05 09:00:00",
                              "demo-skill")
                 + skill_call("wb:after", 1, "2026-03-12 10:00:00",
                              "demo-skill")
                 + skill_call("wb:far", 1, "2026-06-01 10:00:00",
                              "demo-skill"))
        return build_cross(self.chain, self._db(steps), margin_days=margin)

    def test_kind_distinguishes_in_stage_from_after_stage(self):
        d = self._build()
        rows = d["stages"][1]["rows"]          # 阶段二
        by = {r["sid"]: r for r in rows}
        # 阶段内（03-03）与尾日（03-05）——都不带 _after
        self.assertEqual(by["wb:in"]["kind"], "temporal")
        self.assertFalse(by["wb:in"]["after_stage"])
        self.assertEqual(by["wb:edge"]["kind"], "temporal")
        self.assertFalse(by["wb:edge"]["after_stage"],
                         "span 尾日当日属阶段内，不得标 _after")
        # margin 区内（03-12 > 03-05）——必须带 _after
        self.assertEqual(by["wb:after"]["kind"], "temporal_after")
        self.assertTrue(by["wb:after"]["after_stage"])
        # margin 区外（06-01）
        self.assertNotIn("wb:far", by)

    def test_after_stage_counter_and_report_warning(self):
        d = self._build()
        self.assertEqual(d["after_stage"], 1)
        rep = render_cross(d)
        self.assertIn("_after", rep)
        self.assertIn("阶段结束之后", rep)
        # 逐 stage 标题也标注
        self.assertIn("⚠ 1 次在阶段结束之后", rep)

    def test_margin_zero_means_no_after_stage(self):
        """margin=0 时不应出现任何 _after（回归保护）。"""
        d = self._build(margin=0)
        allrows = [r for s in d["stages"] for r in s["rows"]]
        self.assertTrue(all(not r["after_stage"] for r in allrows))
        self.assertEqual(d["after_stage"], 0)
        self.assertNotIn("阶段结束之后", render_cross(d))

    def test_member_branch_also_labels_after(self):
        """**节点精确匹配**的调用同样要按日期标 _after（成员也会跨数月）。

        这是原始缺陷的另一半：节点匹配只保证"属本主题的这个 turn"，
        不保证日期在 stage 之内。修复前该行 kind='member' 且无任何
        阶段后标记。fixture：turn 解析得 2，恰好命中阶段二的节点 turn 2。
        """
        steps = skill_call("src:m1", 5, "2026-03-12 10:00:00", "demo-skill")
        db = self._db(steps)
        con = sqlite3.connect(str(db))
        for ts in ("2026-03-01 09:00:00", "2026-03-04 09:00:00"):
            con.execute("INSERT INTO messages VALUES (?,?,?,?,?)",
                        ("src:m1", "user", ts, "轮", "x"))
        con.commit()
        con.close()
        d = build_cross(self.chain, db, margin_days=MARGIN)
        rows = [r for s in d["stages"] for r in s["rows"]
                if r["sid"] == "src:m1" and r["seq"] == 5]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["turn"], 2)
        self.assertEqual(rows[0]["kind"], "member_after",
                         "节点匹配路径也必须带 _after（日期在 span 之后）")
        self.assertTrue(rows[0]["after_stage"])

    def test_member_span_branch_also_labels_after(self):
        """成员会话走 **span 兜底** 分支时同样要标 _after（三条路径口径一致）。

        fixture：把一个无节点的 sid 加进 chain 的 members，使其进入成员
        分支、又匹配不到任何节点 → 落到 span 兜底（member_span）。
        """
        chain = self.dir / "chain_m2.md"
        chain.write_text(
            CHAIN.replace("members:\n  - src:m1",
                          "members:\n  - src:m1\n  - src:m2"),
            encoding="utf-8", newline="\n")
        steps = skill_call("src:m2", 5, "2026-03-12 10:00:00", "demo-skill")
        db = self._db(steps)
        con = sqlite3.connect(str(db))
        con.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?)",
                    ("src:m2", "src", "m2", "无节点成员", "", "", "", ""))
        for ts in ("2026-03-01 09:00:00", "2026-03-04 09:00:00"):
            con.execute("INSERT INTO messages VALUES (?,?,?,?,?)",
                        ("src:m2", "user", ts, "轮", "x"))
        con.commit()
        con.close()
        d = build_cross(chain, db, margin_days=MARGIN)
        rows = [r for s in d["stages"] for r in s["rows"]
                if r["sid"] == "src:m2" and r["seq"] == 5]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "member_span_after")
        self.assertTrue(rows[0]["after_stage"])

    def test_non_member_in_margin_is_temporal_after(self):
        """非成员落在 margin 区 → temporal_after（与阶段内的 temporal 分开）。"""
        steps = skill_call("wb:after", 1, "2026-03-12 10:00:00", "demo-skill")
        d = build_cross(self.chain, self._db(steps), margin_days=MARGIN)
        rows = d["stages"][1]["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "temporal_after")
        self.assertTrue(rows[0]["after_stage"])


if __name__ == "__main__":
    unittest.main()
