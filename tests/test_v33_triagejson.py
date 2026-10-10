# -*- coding: utf-8 -*-
"""v0.33 测试：分诊的**全量**机器出口（`--triage-json`）。

来源是一个真实的续作风险：`topic-consolidate --triage-out` 是人读报告，
每类最多印 60 条（`…另有 N 条`）。而 v0.32 交接把下一件事定为"Agent 读包
填 plan"——**照人读报告填 plan，尾部条目会静默漏掉**，且不会报错。

所以本文件的核心断言只有一条：同一个库里，**若某类超过 60 条，报告必须截断
而 JSON 必须全量**。它什么时候会红：`dump_triage` 被改成"解析报告文本"、
或被加上任何 `[:60]` 之类的截断。
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harvester.noisetriage import dump_triage, render_triage, triage
from harvester.indexing import SCHEMA, index_session
from harvester.models import Message, SessionRecord
from harvester.topics import add_members, ensure_topics_db, register_topic

ROOT = Path(__file__).resolve().parents[1]
N_NOISE = 65          # > render_triage 的 60 条上限，正是"截断"能显形的地方


def _fixture(dirpath: Path, n_noise: int = N_NOISE) -> tuple[Path, Path]:
    """建库：n_noise 条单轮查词（noise_high）+ 1 条创作素材 + 1 条已入主题。"""
    db = dirpath / "h.db"
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)
    for i in range(n_noise):
        index_session(con, SessionRecord(
            source="yuanbao-raw", session_id=f"n{i:03d}",
            title=f"差额{i}英文", created_at="2026-01-01 10:00:00",
            updated_at="2026-01-01 10:00:00",
            messages=[Message(role="user", text=f"“差额{i}”的英文表达"),
                      Message(role="assistant", text="答")]))
    index_session(con, SessionRecord(
        source="yuanbao-raw", session_id="c1", title="皱眉描写",
        created_at="2026-01-02 10:00:00", updated_at="2026-01-02 10:00:00",
        messages=[Message(role="user", text="描写皱眉动作的方法")]))
    index_session(con, SessionRecord(
        source="yuanbao-raw", session_id="m1", title="已入主题",
        created_at="2026-01-03 10:00:00", updated_at="2026-01-03 10:00:00",
        messages=[Message(role="user", text="“悄摸摸”的含义解析")]))
    con.commit()
    con.close()
    meta = ensure_topics_db(dirpath / "topics_meta.db")
    t = register_topic(meta, "某主题", keywords=["悄摸摸"])
    add_members(meta, t, ["yuanbao-raw:m1"], evidence="e")
    return db, meta


class TestDumpTriage(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db, self.meta = _fixture(self.dir)

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def test_report_truncates_but_json_does_not(self):
        """本文件存在的理由：报告截断 60，JSON 必须全量 65。"""
        t = triage(self.db, self.meta)
        self.assertEqual(t["counts"]["noise_high"], N_NOISE)
        txt = render_triage(t)
        self.assertIn(f"另有 {N_NOISE - 60} 条", txt)      # 报告确实截断了
        self.assertEqual(txt.count("`yuanbao-raw:n"), 60)   # 只印了 60 条
        rows = dump_triage(t)["rows"]
        # 全量 = 所有判定（65 条 noise_high + 1 条 craft_material）
        self.assertEqual(len(rows), N_NOISE + 1)
        self.assertEqual(len([r for r in rows if r["verdict"] == "noise_high"]),
                         N_NOISE)
        self.assertEqual(len({r["sid"] for r in rows}), len(rows))

    def test_dump_is_json_serializable_and_keeps_authoring_fields(self):
        """填 plan 只需 sid + verdict，但复核需要 first_user；三者都要在。"""
        d = dump_triage(triage(self.db, self.meta))
        s = json.dumps(d, ensure_ascii=False)
        back = json.loads(s)
        self.assertEqual(back["scanned"], d["scanned"])
        self.assertEqual(back["max_turns"], 3)
        self.assertEqual(len(back["rows"]), d["scanned"])
        r = [x for x in back["rows"] if x["sid"] == "yuanbao-raw:c1"][0]
        self.assertEqual(r["verdict"], "craft_material")
        self.assertEqual(r["first_user"], "描写皱眉动作的方法")
        self.assertIn("title", r)

    def test_members_are_excluded_from_both_exits(self):
        d = dump_triage(triage(self.db, self.meta))
        self.assertNotIn("yuanbao-raw:m1", {r["sid"] for r in d["rows"]})

    def test_first_user_is_capped(self):
        """原文只作复核用，不把几十 KB 正文塞进 JSON。"""
        con = sqlite3.connect(str(self.db))
        index_session(con, SessionRecord(
            source="yuanbao-raw", session_id="long", title="长",
            created_at="2026-01-04 10:00:00", updated_at="2026-01-04 10:00:00",
            messages=[Message(role="user", text="查一下" + "水" * 500)]))
        con.commit()
        con.close()
        d = dump_triage(triage(self.db, self.meta))
        r = [x for x in d["rows"] if x["sid"] == "yuanbao-raw:long"][0]
        self.assertEqual(len(r["first_user"]), 200)


class TestTriageJsonCli(unittest.TestCase):
    """真跑文档里的命令（README 的命令块据此防腐烂）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db, self.meta = _fixture(self.dir)

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def _run(self, *extra: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(ROOT)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
        return subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "harvester",
             "topic-consolidate", "--meta", str(self.meta),
             "--db", str(self.db), *extra],
            cwd=str(ROOT), env=env, capture_output=True, text=True,
            encoding="utf-8")

    def test_cli_writes_full_json(self):
        out = self.dir / "sub" / "triage.json"
        r = self._run("--triage-json", str(out))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(out.is_file(), r.stderr)
        d = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(len(d["rows"]), d["scanned"])
        self.assertEqual(d["counts"]["noise_high"], N_NOISE)
        self.assertIn("全量不截断", r.stderr)
    def test_cli_report_and_json_side_by_side(self):
        """两个出口并存时：报告 60 条、JSON 65 条 —— 差异被测出来。"""
        rep, js = self.dir / "triage.md", self.dir / "triage.json"
        r = self._run("--triage-out", str(rep), "--triage-json", str(js))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("另有 5 条", rep.read_text(encoding="utf-8"))
        self.assertEqual(
            len(json.loads(js.read_text(encoding="utf-8"))["rows"]), N_NOISE + 1)

    def test_cli_without_out_still_prints_report(self):
        """只给 --triage（不给输出路径）时行为不变：报告打到 stdout。"""
        r = self._run("--triage")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("# 零散分诊报告", r.stdout)
        self.assertIn("高置信零散", r.stdout)

    def test_cli_without_triage_flag_is_not_triage(self):
        """不带 --triage 时仍走「出梳理包」——别把缺省行为改掉。"""
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("主题梳理包", r.stdout)


if __name__ == "__main__":
    unittest.main()
