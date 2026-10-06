# -*- coding: utf-8 -*-
"""v0.15：模型归属入库 + vscode-copilot 工具名修复测试。"""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harvester.adapters.vscode_copilot import _ws_extract
from harvester.toolstats import (ToolStats, collect_model_stats,
                                 render_model_table)

_DB_SCHEMA = """
CREATE TABLE sessions (
    sid TEXT PRIMARY KEY, source TEXT NOT NULL, session_id TEXT NOT NULL,
    title TEXT, category TEXT, created_at TEXT, updated_at TEXT, file TEXT,
    model TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS messages USING fts5(
    sid UNINDEXED, role UNINDEXED, ts UNINDEXED, text, raw UNINDEXED,
    tokenize='unicode61');
CREATE TABLE steps (
    sid TEXT NOT NULL, seq INTEGER NOT NULL, ts TEXT, tool TEXT NOT NULL,
    phase TEXT NOT NULL, status TEXT, error TEXT, detail TEXT);
"""


def _make_db(root: Path) -> Path:
    db = root / "harvester.db"
    con = sqlite3.connect(str(db))
    con.executescript(_DB_SCHEMA)
    con.execute("INSERT INTO sessions VALUES "
                "('wb:m1','workbuddy-transcript','m1','会话A',"
                "NULL,NULL,NULL,NULL,'glm-5.2')")
    con.execute("INSERT INTO sessions VALUES "
                "('wb:m2','workbuddy-transcript','m2','会话B',"
                "NULL,NULL,NULL,NULL,'glm-5.3-flash')")
    con.execute("INSERT INTO sessions VALUES "
                "('dsh:x','dsh','x','会话C',"
                "NULL,NULL,NULL,NULL,NULL)")   # 无模型信息的源
    for sid, phase in [("wb:m1", "call"), ("wb:m1", "result"),
                       ("wb:m1", "result"),
                       ("wb:m2", "call"), ("wb:m2", "result")]:
        con.execute("INSERT INTO steps VALUES (?,?,?,?,?,?,?,?)",
                    (sid, 0, "2026-10-06 08:00:00", "Bash", phase,
                     "error" if phase == "result" and sid == "wb:m2"
                     else ("ok" if phase == "result" else None),
                     None, None))
    con.commit()
    con.close()
    return db


class TestModelStats(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db = _make_db(self.root)

    def tearDown(self):
        try:
            self._tmp.cleanup()
        except OSError:
            pass

    def test_collect_model_stats(self):
        stats = collect_model_stats(self.db)
        self.assertEqual(set(stats), {"glm-5.2", "glm-5.3-flash"})
        self.assertEqual(stats["glm-5.2"].calls, 1)
        self.assertEqual(stats["glm-5.2"].success, 2)   # 2 条 result 均 ok
        self.assertEqual(stats["glm-5.3-flash"].error, 1)

    def test_render_model_table(self):
        stats = collect_model_stats(self.db)
        text = render_model_table(stats)
        self.assertIn("| glm-5.2 | 1 | 0 | 0.0% |", text)
        self.assertIn("| glm-5.3-flash | 1 | 1 | 100.0% |", text)
        self.assertIn("providerData", text)
        self.assertEqual(render_model_table({}), "")


class TestCopilotToolName(unittest.TestCase):
    def _run(self, item):
        state = {"requests": [{"message": {"text": "hi"},
                               "response": [item]}]}
        msgs, _ = _ws_extract(state)
        return msgs

    def test_toolid_wins_over_label(self):
        msgs = self._run({
            "kind": "toolInvocationSerialized",
            "toolId": "copilot_readFile",
            "invocationMessage": {"value": "Reading [](file:///d%3A/x.md)"},
        })
        self.assertEqual(len(msgs), 2)   # user 正文 + tool_call note
        self.assertTrue(msgs[-1].text.startswith("[tool_call] copilot_readFile:"))
        self.assertEqual(msgs[-1].raw["tool"], "copilot_readFile")
        self.assertIn("Reading", msgs[-1].raw["detail"])  # 描述句降级为 detail

    def test_fallback_without_toolid(self):
        msgs = self._run({
            "kind": "toolInvocationSerialized",
            "invocationMessage": {"value": "旧格式描述"},
        })
        self.assertEqual(msgs[-1].raw["tool"], "旧格式描述")

    def test_no_tool_no_message(self):
        msgs = self._run({"kind": "toolInvocationSerialized"})
        self.assertEqual(len(msgs), 1)          # 仅 user 正文
        self.assertEqual(msgs[0].role, "user")


if __name__ == "__main__":
    unittest.main()
