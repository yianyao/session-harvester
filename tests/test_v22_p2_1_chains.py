# -*- coding: utf-8 -*-
"""v0.22 P2-1 report-chains（失败翼）测试——SOP §SOP-P2-1。

五种检测全部从 steps(sid,seq,tool,phase,status) 算（H14 口径）：
1. 长回合：会话步数 > 全库 p95；
2. 同工具连击：连续 N≥5 个 call 相同工具（call 相为步进单位，
   call/result 交替是正常形态，result 参与会把连击阈值稀释一半）；
3. 序列循环：call 序列相邻去重后 A↔B 交替周期 ≥3；
4. 空转：同一工具连续 error 结果 ≥5 个（首错 + 重试 >3 仍全败；
   重试口径对齐 toolstats：error 后同会话同工具再调用）；
5. 高步会话 Top N。

验收含合成注入（SOP 红线 §5.6 防恒真测试）：构造 40 步同工具合成
会话，报告必须报出。
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester.chainstats import collect, render_report


def make_db(path: Path, sessions: dict[str, list[tuple]]) -> None:
    """建临时索引库。sessions: sid -> [(tool, phase, status), ...]，
    seq 按给出顺序 1 起编；ts 用固定日占位。"""
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE steps (sid TEXT, seq INTEGER, ts TEXT,"
                " tool TEXT, phase TEXT, status TEXT, error TEXT,"
                " detail TEXT)")
    for sid, steps in sessions.items():
        for i, (tool, phase, status) in enumerate(steps, 1):
            con.execute("INSERT INTO steps VALUES (?,?,?,?,?,?,?,?)",
                        (sid, i, "2026-10-08 10:00:00", tool, phase,
                         status, "boom" if status == "error" else None, None))
    con.commit()
    con.close()


def call_ok(tool: str) -> tuple:
    return (tool, "call", None)


def result_ok(tool: str) -> tuple:
    return (tool, "result", "ok")


def result_err(tool: str) -> tuple:
    return (tool, "result", "error")


def normal_session(n: int) -> list[tuple]:
    """4 工具轮换的正常会话（call+result 成对）。

    注意不能用两工具交替——Read/Grep/Read/Grep 本身就是 A↔B 循环
    （相邻去重后周期 ≥3），会被检测器正确报出。
    """
    tools = ["Read", "Grep", "Write", "Edit"]
    steps: list[tuple] = []
    for i in range(n):
        steps += [call_ok(tools[i % 4]), result_ok(tools[i % 4])]
    return steps


class TestP21Chains(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    # ---- 检测 2/1/5：合成 40 步同工具会话（SOP 验收红线） ----

    def test_synth_40_same_tool_flagged(self):
        db = self.dir / "t.db"
        make_db(db, {
            "s:synth": [call_ok("Write") if i % 2 == 0 else result_ok("Write")
                        for i in range(80)],  # 80 行 = 40 次 call
            "s:n1": normal_session(6),
            "s:n2": normal_session(8),
            "s:n3": normal_session(10),
            "s:n4": normal_session(12),
        })
        d = collect(db)
        # 连击：40 个连续 Write call ≥5
        streaks = [(s, t, n) for s, t, _, n in d["streaks"]
                   if s == "s:synth"]
        self.assertTrue(streaks and streaks[0][1] == "Write"
                        and streaks[0][2] == 40,
                        f"40 步同工具必须报连击: {d['streaks']}")
        # 长回合：80 行 > p95（其余会话 12~24 行）
        self.assertIn("s:synth", [s for s, _ in d["long"]])
        # 高步 Top1
        self.assertEqual(d["top"][0][0], "s:synth")
        # 渲染必含三大段（报告级验收）
        rep = render_report(d)
        for token in ("连击", "长回合", "高步"):
            self.assertIn(token, rep)

    # ---- 检测 3：序列循环 ----

    def test_loop_ab_cycle3_flagged_and_cycle2_not(self):
        db = self.dir / "t.db"
        ab3 = []
        for _ in range(3):  # A,B ×3 = 周期 3
            ab3 += [call_ok("A"), result_ok("A"), call_ok("B"), result_ok("B")]
        ab2 = []
        for _ in range(2):  # A,B ×2 = 周期 2，不报
            ab2 += [call_ok("A"), result_ok("A"), call_ok("B"), result_ok("B")]
        make_db(db, {"s:loop3": ab3, "s:loop2": ab2,
                     "s:n1": normal_session(4)})
        d = collect(db)
        self.assertEqual([s for s, *_ in d["loops"]], ["s:loop3"])
        loops = [x for x in d["loops"] if x[0] == "s:loop3"]
        self.assertEqual(loops[0][1], "A↔B")
        self.assertEqual(loops[0][2], 3)

    def test_loop_ignores_same_tool_run(self):
        """连击去重后不成环：40 步同工具只报连击不报循环。"""
        db = self.dir / "t.db"
        make_db(db, {"s:synth": [call_ok("W"), result_ok("W")] * 40,
                     "s:n1": normal_session(4)})
        d = collect(db)
        self.assertEqual([x for x in d["loops"] if x[0] == "s:synth"], [])

    # ---- 检测 4：空转 ----

    def test_spin_5_errors_flagged_4_not(self):
        db = self.dir / "t.db"
        spin5 = []
        for _ in range(5):  # 首错 + 重试 4 次（>3）仍全败
            spin5 += [call_ok("Bash"), result_err("Bash")]
        spin4 = []
        for _ in range(4):  # 重试 3 次，不报
            spin4 += [call_ok("Bash"), result_err("Bash")]
        make_db(db, {"s:spin5": spin5 + normal_session(4),
                     "s:spin4": spin4 + normal_session(4)})
        d = collect(db)
        self.assertEqual([s for s, *_ in d["spins"]], ["s:spin5"])
        self.assertEqual(d["spins"][0][1], "Bash")
        self.assertEqual(d["spins"][0][2], 5)

    def test_spin_broken_by_success(self):
        """中间成功一次即断链：3 错 + 1 成 + 3 错不报。"""
        db = self.dir / "t.db"
        steps = []
        for _ in range(3):
            steps += [call_ok("X"), result_err("X")]
        steps += [call_ok("X"), result_ok("X")]
        for _ in range(3):
            steps += [call_ok("X"), result_err("X")]
        make_db(db, {"s:mixed": steps + normal_session(4)})
        d = collect(db)
        self.assertEqual([x for x in d["spins"] if x[0] == "s:mixed"], [])

    # ---- 正常会话零误报 + since 窗口 ----

    def test_normal_sessions_clean(self):
        db = self.dir / "t.db"
        make_db(db, {f"s:n{i}": normal_session(6) for i in range(6)})
        d = collect(db)
        self.assertEqual(d["streaks"], [])
        self.assertEqual(d["loops"], [])
        self.assertEqual(d["spins"], [])

    def test_since_window_excludes_old(self):
        db = self.dir / "t.db"
        con = sqlite3.connect(str(db))
        con.execute("CREATE TABLE steps (sid TEXT, seq INTEGER, ts TEXT,"
                    " tool TEXT, phase TEXT, status TEXT, error TEXT,"
                    " detail TEXT)")
        for i in range(1, 11):
            con.execute("INSERT INTO steps VALUES (?,?,?,?,?,?,?,?)",
                        ("s:old", i, "2026-09-01 10:00:00", "W",
                         "call" if i % 2 else "result",
                         None if i % 2 else "ok", None, None))
        con.commit()
        con.close()
        d = collect(db, since_days=7)  # cutoff=2026-10-01，旧步全排除
        self.assertEqual(d["steps"], 0)
        self.assertEqual(d["sessions"], 0)

    # ---- 渲染与空态 ----

    def test_render_empty_db(self):
        db = self.dir / "t.db"
        con = sqlite3.connect(str(db))
        con.execute("CREATE TABLE steps (sid TEXT, seq INTEGER, ts TEXT,"
                    " tool TEXT, phase TEXT, status TEXT, error TEXT,"
                    " detail TEXT)")
        con.commit()
        con.close()
        d = collect(db)
        self.assertIn("无 steps 数据", render_report(d))

    def test_cli_report_chains(self):
        from harvester.cli import main
        db = self.dir / "t.db"
        make_db(db, {"s:synth": [call_ok("Write"), result_ok("Write")] * 20,
                     "s:n1": normal_session(6)})
        out = self.dir / "rep.md"
        rc = main(["report-chains", "--db", str(db), "--out", str(out)])
        self.assertEqual(rc, 0)
        text = out.read_text(encoding="utf-8")
        self.assertIn("连击", text)
        self.assertIn("s:synth", text)


if __name__ == "__main__":
    unittest.main()
