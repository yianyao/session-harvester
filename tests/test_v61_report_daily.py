# -*- coding: utf-8 -*-
"""v0.81 测试：`report-daily` 五段聚合（P5-⑤，用户 2026-10-16 批准）。

依据：`docs/PROPOSAL-faster-data-access.md` §2-5 / §4-5。
钉的验收（每条红=什么坏了）：

1. 五段齐全 + 四队列数字与**直调同源函数**一致 → 有人换了口径/参数 → 红；
2. 建议台账缺失 → decide/verify 按空账计且 md 注明（诚实空态）→
   若有人静默 0 值不注明 → 红；
3. 无 steps 数据 → 工具段诚实留空不编数 → 红；
4. **只读**：跑前后索引库/建议台账字节指纹不变 → 有人顺手写库 → 红；
5. --out 落盘（自动建父目录）、无 --out 走 stdout、库不存在 fail loud → 红。

夹具全 tempfile；真库一字节不动。
"""
from __future__ import annotations

import contextlib
import io
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from harvester.cli import cmd_report_daily
from harvester.apiserve import api_report_daily, api_reports_agents
from harvester.harvesteval import VERDICT_INCONCLUSIVE, run_eval
from harvester.triage import collect_triage

SCHEMA = """
CREATE TABLE sessions (sid TEXT PRIMARY KEY, source TEXT NOT NULL,
                       session_id TEXT NOT NULL, title TEXT, category TEXT,
                       created_at TEXT, updated_at TEXT, file TEXT, model TEXT);
CREATE TABLE steps (sid TEXT, seq INTEGER, ts TEXT, tool TEXT,
                    phase TEXT, status TEXT, error TEXT, detail TEXT);
CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, sid TEXT,
                       role TEXT, ts TEXT, text TEXT, raw TEXT);
"""


def _mk_db(path: Path) -> None:
    con = sqlite3.connect(str(path))
    con.executescript(SCHEMA)
    con.execute("INSERT INTO sessions VALUES "
                "('s1','dsh','s1','甲','t',NULL,NULL,NULL,NULL)")
    con.execute("INSERT INTO messages(sid, role, ts, text, raw) "
                "VALUES ('s1','user','2026-10-16 10:00:00','正文','')")
    d = (datetime.now() - timedelta(days=0)).strftime("%Y-%m-%d")
    # 同一 pattern（old_string 不一致）×3 → api_reports_agents 出 1 条
    # unresolved entry（min_count=3 缺省）→ 待修补队列=1
    rows = [("s1", i, f"{d} 10:00:0{i}", "edit", "result", "error",
             "Error: String to replace not found in file. old_string 不一致",
             None) for i in range(3)]
    con.executemany("INSERT INTO steps VALUES (?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


def _mk_meta(path: Path) -> None:
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE suggestion_status (key TEXT PRIMARY KEY, "
                "status TEXT NOT NULL, adopted_at TEXT, note TEXT, "
                "updated_at TEXT NOT NULL)")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    con.executemany(
        "INSERT INTO suggestion_status VALUES (?,?,?,?,?)",
        [("甲", "pending", None, None, now),
         ("乙", "pending", None, None, now),
         ("丙", "adopted", "2026-10-15", None, now)])
    con.commit()
    con.close()


def _args(**kw):
    import argparse
    base = dict(db="harvester.db", meta="suggestions_meta.db",
                cards_root=None, since=1.0, top=10, out=None)
    base.update(kw)
    return argparse.Namespace(**base)


class TestReportDaily(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "harvester.db"
        self.meta = self.root / "suggestions_meta.db"
        _mk_db(self.db)
        _mk_meta(self.meta)

    def _run_md(self, **kw) -> str:
        out = kw.pop("out", None)
        db = kw.pop("db", str(self.db))
        meta = kw.pop("meta", str(self.meta))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cmd_report_daily(_args(db=db, meta=meta,
                                        out=str(out) if out else None, **kw))
        self.assertEqual(rc, 0)
        return buf.getvalue()

    def test_five_sections_and_queues_match_sources(self):
        md = self._run_md()
        for sec in ("## 一、四队列", "## 二、工具调用摘要",
                    "## 三、错误分类摘要", "## 四、工具链失败模式摘要",
                    "## 五、账目"):
            self.assertIn(sec, md)
        self.assertIn("| 待裁决 | 2 |", md)
        self.assertIn("只读聚合", md)
        # 同源对账：四队列数字必须等于直调同源函数（口径漂移即红）
        con = sqlite3.connect("file:%s?mode=ro" % self.db.as_posix(),
                              uri=True)
        con.row_factory = sqlite3.Row  # 同 view open_ro；api_reports_agents
        # 消费方按列名取值（errstats 行 236 按 r["sid"] 取）
        try:
            agents = api_reports_agents(con, self.db, {}, meta_db=self.meta)
            tri = collect_triage(self.db, cards_root=None, min_count=2,
                                 top_n=10, con=con)
        finally:
            con.close()
        ev = run_eval(self.db, self.meta)
        self.assertIn("| 待修补 | %d |" % sum(
            1 for e in agents["entries"]
            if (e.get("unresolved_count") or 0) >= 1), md)
        self.assertIn("| 待蒸馏 | %d |" % len(
            tri.get("new_patterns") or []), md)
        self.assertIn("| 待验证 | %d |" % sum(
            1 for e in ev.get("entries") or []
            if e.get("verdict") == VERDICT_INCONCLUSIVE), md)
        # repair 至少吃到夹具那条 unresolved（否则夹具失效，测试自证）
        self.assertGreaterEqual(len([e for e in agents["entries"]
                                     if (e.get("unresolved_count") or 0) >= 1]),
                                1)

    def test_missing_meta_is_honest_empty(self):
        md = self._run_md(meta=str(self.root / "nope.db"))
        self.assertFalse((self.root / "nope.db").exists(),
                         "缺台账不得顺手建库")
        self.assertIn("| 待裁决 | 0 |", md)
        self.assertIn("不存在", md)

    def test_readonly_fingerprint(self):
        fp0 = (self.db.read_bytes(), self.meta.read_bytes())
        self._run_md()
        self.assertEqual(fp0, (self.db.read_bytes(), self.meta.read_bytes()),
                         "report-daily 是只读聚合，任何库字节变化都是写库事故")

    def test_out_writes_and_makes_parents(self):
        out = self.root / "docs" / "reports" / "daily.md"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cmd_report_daily(_args(db=str(self.db), meta=str(self.meta),
                                        out=str(out)))
        self.assertEqual(rc, 0)
        self.assertIn("报告已写入", buf.getvalue())
        text = out.read_text(encoding="utf-8")
        for sec in ("## 一、四队列", "## 五、账目"):
            self.assertIn(sec, text, "--out 落盘必须是完整五段")

    def test_missing_db_fails_loud(self):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            rc = cmd_report_daily(_args(db=str(self.root / "no.db")))
        self.assertEqual(rc, 2)
        self.assertIn("不存在", buf.getvalue())

    def test_no_steps_honest_empty(self):
        db2 = self.root / "empty.db"
        con = sqlite3.connect(str(db2))
        con.executescript(SCHEMA)
        con.commit()
        con.close()
        md = self._run_md(db=str(db2))
        self.assertIn("无 steps 数据", md)
        self.assertIn("诚实留空", md)


class TestReportDailyEndpoint(unittest.TestCase):
    """/api/report-daily（view「昨日报告」卡取数口，P5-⑤）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name) / "reports"
        self.d.mkdir()

    def test_latest_and_whitelist(self):
        (self.d / "daily-2026-10-15.md").write_text("旧", encoding="utf-8")
        (self.d / "daily-2026-10-16.md").write_text("新", encoding="utf-8")
        # 白名单外：同名变体/任意文件一律不入候选（防目录里混入别的东西）
        (self.d / "daily-evil.md").write_text("x", encoding="utf-8")
        (self.d / "secret.md").write_text("x", encoding="utf-8")
        (self.d / "daily-2026-10-17.md.bak").write_text("x", encoding="utf-8")
        r = api_report_daily(self.d, {})
        self.assertTrue(r["found"])
        self.assertEqual(r["date"], "2026-10-16")
        self.assertEqual(r["md_head"], "新")
        r2 = api_report_daily(self.d, {"date": "2026-10-15"})
        self.assertEqual(r2["date"], "2026-10-15")

    def test_traversal_rejected(self):
        for bad in ("../../etc", "2026-10-16%00", "x/y", "2026-1-6"):
            with self.assertRaises(ValueError, msg=bad):
                api_report_daily(self.d, {"date": bad})

    def test_honest_empty_and_missing_dir(self):
        r = api_report_daily(self.d, {})
        self.assertFalse(r["found"])
        self.assertIn("hint", r)
        r2 = api_report_daily(Path(self.tmp.name) / "nope", {})
        self.assertFalse(r2["found"])


if __name__ == "__main__":
    unittest.main()
