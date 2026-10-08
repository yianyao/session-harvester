# -*- coding: utf-8 -*-
"""v0.22 T4 skill 进化 join 测试——SOP §SOP-T4。

交叉表两种 join（数据驱动，不写死任何 skill 名/主题名）：
1. 成员内 join：调用 sid ∈ chain members，turn 用用户消息 ts 二分
   解析（步骤 ts 落在 turn k 的 user 消息 ts 之后、turn k+1 之前 → 归
   turn k）；优先按 chain 节点 (sid, turn) 精确归组，其次按 stage 时间窗。
2. 时间窗 join：非成员会话的调用，ts 落在 stage span（含尾日）+
   margin 内 → 归该 stage。

验收背景（T4 实测，仅示例）：「叙事节奏」55 成员全为导出型源、成员内
skill 调用为 0——时间窗 join 是该数据形态的主通道。本模块对任意
chain × 任意 skill 通用（用户红线：工具必须面向任何主题的会话，
禁止绑定特定主题/会话/skill）；fixture 用中性名 demo-skill。
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from harvester.skilljoin import build_cross, parse_span, render_cross


def make_db(path: Path, sessions: list[tuple], steps: list[tuple],
            user_msgs: list[tuple]) -> None:
    """sessions=(sid,source,sid2,title,cat,created,updated,file)
    steps=(sid,seq,ts,tool,phase,status,error,detail)
    user_msgs=(sid, role, ts, text, raw)——只插 user 消息供 turn 解析。"""
    con = sqlite3.connect(str(path))
    con.executescript("""
    CREATE TABLE sessions (sid TEXT PRIMARY KEY, source TEXT, session_id TEXT,
                           title TEXT, category TEXT, created_at TEXT,
                           updated_at TEXT, file TEXT);
    CREATE TABLE messages (sid TEXT, role TEXT, ts TEXT, text TEXT,
                           raw TEXT);
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
    d = json.dumps({"skill": skill, "args": "测试任务"}, ensure_ascii=False)
    return [
        (sid, seq, ts, "Skill", "call", None, None, d),
        (sid, seq + 1, ts, "Skill", "result", status,
         "boom" if status == "error" else None, None),
    ]


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


class TestT4SkillJoin(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.chain = self.dir / "chain.md"
        self.chain.write_text(CHAIN, encoding="utf-8", newline="\n")

    def tearDown(self):
        self.tmp.cleanup()

    def _db(self, steps, user_msgs) -> Path:
        self._n = getattr(self, "_n", 0) + 1
        db = self.dir / f"t{self._n}.db"
        make_db(db, [
            ("src:m1", "src", "m1", "成员会话", "", "", "", ""),
            ("wb:1", "workbuddy-transcript", "1", "理论书PDF转换", "",
             "", "", ""),
            ("wb:2", "workbuddy-transcript", "2", "窗口外任务", "",
             "", "", ""),
        ], steps, user_msgs)
        return db

    def test_parse_span(self):
        self.assertEqual(parse_span("2026-01-01 ~ 2026-01-10"),
                         (date(2026, 1, 1), date(2026, 1, 10)))
        self.assertEqual(parse_span("无日期"), (None, None))

    def test_member_join_resolves_turn(self):
        steps = skill_call("src:m1", 5, "2026-01-02 10:00:00",
                           "demo-skill")
        msgs = [("src:m1", "user", "2026-01-01 09:00:00", "第一轮", "x"),
                ("src:m1", "user", "2026-01-03 09:00:00", "第二轮", "x")]
        d = build_cross(self.chain, self._db(steps, msgs))
        rows = [r for r in d["stages"][0]["rows"] if r["kind"] == "member"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["turn"], 1)  # ts 在 turn1 与 turn2 之间
        self.assertEqual(rows[0]["anchor"], "src:m1#5")

    def test_temporal_join_and_margin(self):
        # 窗口内（2026-03-03 ∈ 阶段二 span）与窗口外（2026-03-12）
        steps = (skill_call("wb:1", 1, "2026-03-03 10:00:00",
                            "demo-skill")
                 + skill_call("wb:2", 1, "2026-03-12 10:00:00",
                              "demo-skill"))
        d = build_cross(self.chain, self._db(steps, []))
        rows2 = d["stages"][1]["rows"]
        self.assertEqual([r["sid"] for r in rows2], ["wb:1"])
        self.assertEqual(rows2[0]["kind"], "temporal")
        self.assertEqual(rows2[0]["title"], "理论书PDF转换")
        # margin 放宽后窗口外也归入
        d2 = build_cross(self.chain, self._db(steps, []), margin_days=10)
        self.assertEqual([r["sid"] for r in d2["stages"][1]["rows"]],
                         ["wb:1", "wb:2"])

    def test_skill_filter(self):
        steps = (skill_call("wb:1", 1, "2026-03-03 10:00:00",
                            "demo-skill")
                 + skill_call("wb:1", 3, "2026-03-03 11:00:00",
                              "other-skill"))
        d = build_cross(self.chain, self._db(steps, []),
                        skill="demo-skill")
        all_rows = [r for s in d["stages"] for r in s["rows"]]
        self.assertEqual([r["skill"] for r in all_rows],
                         ["demo-skill"])

    def test_unassigned_bucket(self):
        steps = skill_call("wb:9", 1, "2026-06-01 10:00:00", "x-skill")
        d = build_cross(self.chain, self._db(steps, []))
        self.assertEqual(len(d["unassigned"]), 1)
        self.assertEqual(d["unassigned"][0]["sid"], "wb:9")

    def test_render_contains_anchors(self):
        steps = (skill_call("src:m1", 5, "2026-01-02 10:00:00",
                            "demo-skill")
                 + skill_call("wb:1", 1, "2026-03-03 10:00:00",
                              "demo-skill"))
        d = build_cross(self.chain, self._db(steps, []),
                        skill="demo-skill")
        rep = render_cross(d)
        self.assertIn("src:m1#5", rep)   # 成员内锚点回链
        self.assertIn("wb:1#1", rep)     # 时间窗锚点回链
        self.assertIn("阶段二·理论化", rep)

    def test_cli_report_skill_join(self):
        from harvester.cli import main
        steps = skill_call("wb:1", 1, "2026-03-03 10:00:00",
                           "demo-skill")
        db = self._db(steps, [])
        out = self.dir / "rep.md"
        rc = main(["report-skill-join", "--chain", str(self.chain),
                   "--db", str(db), "--skill", "demo-skill",
                   "--out", str(out)])
        self.assertEqual(rc, 0)
        text = out.read_text(encoding="utf-8")
        self.assertIn("demo-skill", text)
        self.assertIn("阶段二·理论化", text)


if __name__ == "__main__":
    unittest.main()
