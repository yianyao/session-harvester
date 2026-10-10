# -*- coding: utf-8 -*-
"""v0.41 测试：机械命中必须**确定**，且深会话的命中要看得见。

两个缺陷都由 v0.40 子代理复核 65 条深会话时实测报出：

- **B（严重）**：`_topic_keyword_index` 的排序键只有 `-len(kw)`：**同长度关键词之间
  的顺序继承 `set` 的迭代顺序**，而字符串哈希按进程随机化（PYTHONHASHSEED）→
  **同一库、同一输入、两次独立进程可能命中不同主题**（实测 65 条并列里 2 条真的
  翻了：072↔010、009↔007）。这违反项目"同库重跑可复现"的红线：`--triage-brief` /
  `--triage-json` / `--plan-seed` 会喂出不同的机械归位。
- **A**：`render_brief` 只对 `topic_hint` 填「命中主题」列，`deep_topic_hint` 那列
  **全空**（65 条一行都没值），复核时只能绕道去读 JSON。

**为什么用 8 个哈希种子跑子进程**：单元级断言测不出这个缺陷——同长度关键词的相对
顺序取决于哈希种子，单进程里"恰好"可能是对的。8 个种子下若顺序仍依赖 set，全部一致
的概率约 1/128，故这条断言**可靠地会红**（而不是偶尔红）。
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harvester.indexing import SCHEMA, index_session
from harvester.models import Message, SessionRecord
from harvester.noisetriage import render_brief, triage
from harvester.topics import ensure_topics_db, register_topic

REPO = Path(__file__).resolve().parents[1]

#: 两个**同长度**关键词，且都会命中同一条会话 → 制造并列
KW_A, KW_B = "甲乙丙", "丁戊己"
TOPIC_A, TOPIC_B = "主题甲", "主题乙"


def _fixture(root: Path):
    db = root / "h.db"
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)

    def add(sid: str, texts: list[str], title: str) -> None:
        index_session(con, SessionRecord(
            source="yuanbao-raw", session_id=sid, title=title,
            created_at="2026-01-01 10:00:00", updated_at="2026-01-01 10:00:00",
            messages=[Message(role="user", text=t) for t in texts]))

    add("shallow", [f"{KW_A}{KW_B} 是什么"], "并列命中")     # 两个关键词都命中
    add("deep", [f"{KW_A}{KW_B}"] + [f"第{i}问" for i in range(2, 7)], "深并列")
    con.commit()
    con.close()
    meta = ensure_topics_db(root / "topics_meta.db")
    register_topic(meta, TOPIC_A, keywords=[KW_A])
    register_topic(meta, TOPIC_B, keywords=[KW_B])
    return db, meta


class TestHintsAreDeterministic(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db, self.meta = _fixture(self.root)

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def test_brief_is_byte_identical_across_hash_seeds(self):
        """8 个 PYTHONHASHSEED 下，同一库同一输入的简报必须**逐字节一致**。"""
        outs = []
        for seed in range(8):
            env = dict(os.environ)
            env["PYTHONHASHSEED"] = str(seed)
            env["PYTHONPATH"] = os.pathsep.join(
                [str(REPO / "scripts" / "sandbox"), str(REPO)]
                + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
            r = subprocess.run(
                [sys.executable, "-X", "utf8", "-m", "harvester",
                 "topic-consolidate", "--meta", str(self.meta),
                 "--db", str(self.db), "--triage", "--triage-brief", "*"],
                cwd=str(REPO), env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace")
            self.assertEqual(r.returncode, 0, r.stderr)
            outs.append(r.stdout)
        uniq = sorted(set(outs))
        self.assertEqual(
            len(uniq), 1,
            "同一库同一输入在不同哈希种子下给出了不同结果（机械命中不确定）：\n"
            + "\n---\n".join(u.splitlines()[-1] for u in uniq))

    def test_deep_rows_show_the_hint(self):
        """深会话的「命中主题」列必须有值（修前全空）。"""
        t = triage(self.db, self.meta, max_turns=3, include_deep=True)
        by = {r["sid"]: r for r in t["rows"]}
        self.assertEqual(by["yuanbao-raw:deep"]["verdict"], "deep_topic_hint")
        brief = render_brief(t, "deep_topic_hint")
        line = [x for x in brief.splitlines()
                if x.startswith("deep_topic_hint")][0]
        cols = line.split("\t")
        self.assertIn(cols[3], (TOPIC_A, TOPIC_B), f"命中列为空或异常: {line!r}")


if __name__ == "__main__":
    unittest.main()
