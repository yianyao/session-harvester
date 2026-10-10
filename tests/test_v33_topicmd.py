# -*- coding: utf-8 -*-
"""v0.33 测试：主题的**人读那一半**（`topic md`，一页速览）。

V3 的分工：`topic export` 给机器（结构化 JSON，含逐条成员，可能上千行）；
`topic md` 给人（一页四问：是什么 / 跨多久 / 关键转折 / 结论与未决）。

本文件重点钉住两条容易走偏的纪律：

- **不列成员**：人读那页一旦把成员列出来，大主题就是几百行，"一页"没了。
- **不臆造结论**：关键转折/结论/未决只能来自已发布 chain 的正文。取不到要
  **分清"没去读"（未执行）与"确实还没有"（不适用）**——这两者混同会让人
  以为主题没内容（项目 AGENTS.md §五 16 条：不要把"跳过"伪装成"通过"）。
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

from harvester.consolidate import register_noise
from harvester.indexing import SCHEMA, index_session
from harvester.models import Message, SessionRecord
from harvester.topics import add_members, ensure_topics_db, register_topic
from harvester.topicexport import (SECTION_CAP, render_topic_json,
                                   render_topic_md, topic_bundle)

ROOT = Path(__file__).resolve().parents[1]

CHAIN = """---
topic: {name}
topic_id: {tid}
members:
  - src:s1
anchors:
  - stage: 阶段一 · {stage}
    span: 2026-01-01 ~ 2026-02-01
    nodes:
      - sid: src:s1
        turn: 1
        note: {note}
generated_from:
  db_mtime: "2026-10-10 00:00:00"
prompt_version: v-test
---

# 思维链：{name}

## 阶段一 · {stage}（2026-01-01 ~ 2026-02-01）

正文若干。

## 元结论：这条链上可迁移的 {n} 条

{conclusion}

## 待补与限制

{pending}
"""


def _fixture(root: Path, n_chain: int = 1):
    db = root / "h.db"
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)
    for sid, src, title, ts in (("s1", "deepseek-export", "甲会话",
                                 "2026-01-05 10:00:00"),
                                ("s2", "yuanbao-raw", "乙会话",
                                 "2026-02-07 10:00:00"),
                                ("s3", "yuanbao-raw", "丙会话",
                                 "2026-02-08 10:00:00")):
        index_session(con, SessionRecord(
            source=src, session_id=sid, title=title,
            created_at=ts, updated_at=ts,
            messages=[Message(role="user", text="u")]))
    con.commit()
    con.close()
    meta = ensure_topics_db(root / "topics_meta.db")
    tid = register_topic(meta, "主题甲", keywords=["k1", "k2"])
    add_members(meta, tid, ["deepseek-export:s1", "yuanbao-raw:s2"],
                evidence="书名口径")
    chains = root / "chains"
    chains.mkdir(exist_ok=True)
    for i in range(n_chain):
        (chains / f"chain-{i}.md").write_text(
            CHAIN.format(name=f"链{i}", tid=tid, stage=f"技法{i}", note="n",
                         n=3, conclusion="结论正文：先收敛再扩展。",
                         pending="待补正文：未逐回合深读的会话。"),
            encoding="utf-8")
    return db, meta, tid, chains


class TestRenderTopicMd(unittest.TestCase):

    def setUp(self):
        from harvester.topicchain import _HAS_YAML
        if not _HAS_YAML:
            raise RuntimeError(
                "topic md 的 chain 解析需要 PyYAML（venv 解释器，H9）；"
                "不提供降级解析——块结构静默误读比报错更危险")
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db, self.meta, self.tid, self.chains = _fixture(self.root)

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def _md(self, chain_root=None, **kw) -> str:
        from harvester.consolidate import noise_sids
        b = topic_bundle(self.meta, self.db, self.tid, chain_root=chain_root,
                         noise_sids=noise_sids(self.meta),
                         generated_at="T", **kw)
        return render_topic_md(b, chain_root=chain_root)

    def test_page_answers_the_four_questions(self):
        md = self._md(self.chains)
        # 是什么
        self.assertIn("# 主题甲", md)
        self.assertIn("k1、k2", md)
        self.assertIn("deepseek-export 1", md)      # 来源分布
        # 跨多久（成员只加了 s1、s2，末个是 2026-02-07）
        self.assertIn("2026-01-05", md)
        self.assertIn("2026-02-07", md)
        self.assertIn("2026-01", md)
        # 关键转折（来自 frontmatter 锚点）
        self.assertIn("阶段一 · 技法0", md)
        self.assertIn("2026-01-01 ~ 2026-02-01", md)
        # 结论 / 未决（来自 chain 正文的小节）
        self.assertIn("元结论：这条链上可迁移的 3 条", md)
        self.assertIn("先收敛再扩展", md)
        self.assertIn("待补与限制", md)
        self.assertIn("未逐回合深读", md)

    def test_members_are_not_listed(self):
        """人读那页不许退化成成员清单（列了就不是一页）。"""
        md = self._md(self.chains)
        self.assertNotIn("deepseek-export:s1", md)
        self.assertNotIn("yuanbao-raw:s2", md)
        # 机器那半必须仍有成员，否则这个断言只是在验"两边都空"
        b = topic_bundle(self.meta, self.db, self.tid, generated_at="T")
        self.assertIn("deepseek-export:s1",
                      {m["sid"] for m in json.loads(
                          render_topic_json(b))["members"]})

    def test_not_executed_vs_not_generated(self):
        """没传 chain-root = 未执行；传了但没有该主题的链 = 确实还没有。"""
        empty = self.root / "empty"
        empty.mkdir()
        md_no_root = self._md(None)
        self.assertIn("未执行", md_no_root)
        self.assertNotIn("尚未生成", md_no_root)
        md_empty = self._md(empty)
        self.assertIn("尚未生成", md_empty)
        self.assertNotIn("未执行", md_empty)
        # 两边的「结论」都要明说没生成，不许拿成员标题凑
        for md in (md_no_root, md_empty):
            self.assertIn("## 结论", md)
            self.assertIn("未生成", md)
            self.assertIn("不用成员标题凑内容", md)

    def test_conclusion_is_capped(self):
        """一节太长要截断并指向全文——否则「一页」在长链上直接失效。"""
        long_text = "长" * (SECTION_CAP + 500)
        (self.chains / "chain-1.md").write_text(
            CHAIN.format(name="链1", tid=self.tid, stage="技法1", note="n", n=1,
                         conclusion=long_text, pending="p"),
            encoding="utf-8")
        md = self._md(self.chains)
        self.assertIn("…（截断；全文见", md)
        self.assertLess(md.count("长"), SECTION_CAP + 50)

    def test_health_warnings_surface(self):
        """既入主题又登记零散 = 自相矛盾；人读那页要显眼报出来。"""
        register_noise(self.meta, [{"sid": "deepseek-export:s1", "reason": "x"}])
        add_members(self.meta, self.tid, ["src:ghost"], evidence="未入索引库")
        md = self._md(self.chains)
        self.assertIn("被登记为零散的成员 **1** 个", md)
        self.assertIn("不在索引库 **1** 个", md)


class TestTopicMdCli(unittest.TestCase):

    def setUp(self):
        from harvester.topicchain import _HAS_YAML
        if not _HAS_YAML:
            raise RuntimeError("topic md 的 chain 解析需要 PyYAML（H9）")
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db, self.meta, self.tid, self.chains = _fixture(self.root)

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
            [sys.executable, "-X", "utf8", "-m", "harvester", "topic", "md",
             "--meta", str(self.meta), "--db", str(self.db), *extra],
            cwd=str(ROOT), env=env, capture_output=True, text=True,
            encoding="utf-8")

    def test_cli_writes_one_page(self):
        out = self.root / "sub" / "topic.md"
        r = self._run("--id", self.tid, "--chain-root", str(self.chains),
                      "--out", str(out))
        self.assertEqual(r.returncode, 0, r.stderr)
        text = out.read_text(encoding="utf-8")
        self.assertIn("# 主题甲", text)
        self.assertIn("元结论", text)
        self.assertNotIn("deepseek-export:s1", text)

    def test_cli_passes_noise_registry_through(self):
        """CLI 要把 meta 的零散登记传进 bundle —— 忘了传，人读页就不报警。"""
        register_noise(self.meta, [{"sid": "deepseek-export:s1", "reason": "x"}])
        out = self.root / "noise.md"
        r = self._run("--id", self.tid, "--out", str(out))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("被登记为零散的成员 **1** 个",
                      out.read_text(encoding="utf-8"))

    def test_cli_without_id_is_usage_error(self):
        r = self._run()
        self.assertEqual(r.returncode, 2)
        self.assertIn("md 需要 --id", r.stderr)

    def test_cli_unknown_topic_raises(self):
        r = self._run("--id", "tp-ghost")
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
