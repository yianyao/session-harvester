# -*- coding: utf-8 -*-
"""v0.43 测试：**卡片校验 ↔ 主题注册表打通**（V3 长期缺口）。

背景（HANDOFF-v0.24 §7.1 V3）：卡片与主题此前是两套互不可见的产物——
卡片锚点指着会话、会话在主题注册表里有没有归属，卡片侧一无所知；
`cards validate` 也永远说不出"这张卡挂的会话属于哪个主题"。

本文件钉住的判定（每条都能说出"什么情况下会红"）：

1. **缺省不新增字段**（additive 契约）：不给 `topics_meta` 时结果与 summary
   的键集合与旧版逐字一致——多一个键就红；
2. **给了注册表就报归属**：锚点会话在主题成员里 → 卡片结果带
   `topics`（id+name）与 `declared_topic_id`；漏了这条就红；
3. **sid 双形态**：注册表用 `sessions.sid`（带源前缀）、卡片锚点可能是裸
   `session_id`（`cards new` 脚手架取的就是它）——只按 sid 精确比对会
   **静默漏判**（真实卡 kc-20261006-0003/0004 实测漏判）。别名键没生效就红；
4. **不一致要报错**：声明主题但锚点不是其成员 / 声明的主题不存在 →
   错误级（不是警告）；
5. **零散登记不算漏归主题**：锚点是已登记零散会话 → 独立警告，不当"漏主题"；
6. **fail loud**：`--topics-meta` 指了读不出来的库（不存在/不是 SQLite/
   缺表）→ 抛异常或退出码 2，**绝不静默返回空索引**（那会把没执行的检查
   伪装成通过——空注册表与不可读注册表必须能分开）。
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

from harvester.cards import (render_report, scaffold_card, validate_cards)
from harvester.consolidate import register_noise
from harvester.indexing import SCHEMA
from harvester.topics import add_members, ensure_topics_db, register_topic

ROOT = Path(__file__).resolve().parents[1]

# 两个会话：sid 带源前缀，session_id 是裸 id（真实库的实际形态）。
SID_A = "src:aaa"
ID_A = "aaa"
SID_B = "src:bbb"
ID_B = "bbb"

CARD_TMPL = (
    "---\n"
    "id: {cid}\n"
    "title: 描述性标题 {cid}\n"
    "type: pitfall\n"
    "tags: []\n"
    "anchors: [{{session_id: \"{sid}\", turn: 1}}]\n"
    "{extra}"
    "evidence: |\n"
    "  证据原文 Alpha beta gamma delta\n"
    "confidence: 0.8\n"
    "created: 2026-10-08\n"
    "---\n正文完整，只留标题没改。\n"
)


def _write_card_multi(root: Path, cid: str, sids: list[str],
                      extra: str = "") -> Path:
    """多锚点卡（混合"命中成员 + 未登记"用）。"""
    anchors = ", ".join(f'{{session_id: "{s}", turn: 1}}' for s in sids)
    root.mkdir(parents=True, exist_ok=True)
    p = root / f"{cid}.md"
    p.write_text(CARD_TMPL.format(cid=cid, sid=sids[0], extra=extra).replace(
        f'[{{session_id: "{sids[0]}", turn: 1}}]', f"[{anchors}]"),
        encoding="utf-8", newline="\n")
    return p


def _fixture(tmp: Path) -> tuple[Path, Path, Path]:
    """(db, meta, cards_root)：两个会话 + 主题 tp-a（成员 src:aaa）。"""
    db = tmp / "t.db"
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)
    con.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?)",
                (SID_A, "src", ID_A, "会话甲", "agent", "2026-10-01",
                 "2026-10-01", "f", ""))
    con.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?)",
                (SID_B, "src", ID_B, "会话乙", "agent", "2026-10-02",
                 "2026-10-02", "f", ""))
    for sid in (SID_A, SID_B):
        con.execute("INSERT INTO messages VALUES (?,?,?,?,?)",
                    (sid, "assistant", "2026-10-01 00:00:00",
                     "证据原文 Alpha beta gamma delta",
                     "证据原文 Alpha beta gamma delta"))
    con.commit()
    con.close()
    meta = ensure_topics_db(tmp / "topics_meta.db")
    tid = register_topic(meta, "主题甲", keywords=["甲"])
    add_members(meta, tid, [SID_A], evidence="登记证据")
    return db, meta, tid


def _write_card(root: Path, cid: str, sid: str, extra: str = "") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    p = root / f"{cid}.md"
    p.write_text(CARD_TMPL.format(cid=cid, sid=sid, extra=extra),
                 encoding="utf-8", newline="\n")
    return p


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db, self.meta, self.tid = _fixture(self.root)

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass


class TestAdditiveContract(Base):
    """1：不给 topics_meta → 结果与 summary 键集合**逐字不变**。"""

    def test_no_new_keys_without_topics_meta(self):
        cards_dir = self.root / "cards"
        _write_card(cards_dir, "kc-1", SID_A)
        results, summary = validate_cards(cards_dir, self.db)
        self.assertEqual(sorted(results[0]), ["errors", "path", "warnings"])
        for key in ("declared_topic_id", "topics", "topic_reviewed",
                    "topic_anchor_hits", "topic_alias_keys"):
            self.assertNotIn(key, results[0], f"缺省路径多了字段 {key}")
            self.assertNotIn(key, summary, f"缺省路径多了 summary 键 {key}")

    def test_report_says_topic_check_not_executed(self):
        """"未执行"必须写明——不许让人以为已经核对过主题。"""
        cards_dir = self.root / "cards"
        _write_card(cards_dir, "kc-1", SID_A)
        results, summary = validate_cards(cards_dir, self.db)
        report = render_report(results, summary, cards_dir)
        self.assertIn("主题核对：**未执行**", report)


class TestMembership(Base):
    """2/3：给了注册表 → 报出所属主题；两种 sid 形态都要能解析。"""

    def test_anchor_by_sid_reports_topic(self):
        cards_dir = self.root / "cards"
        _write_card(cards_dir, "kc-1", SID_A)
        results, summary = validate_cards(cards_dir, self.db,
                                          topics_meta=self.meta)
        r = results[0]
        self.assertEqual([t["id"] for t in r["topics"]], [self.tid])
        self.assertEqual(r["topics"][0]["name"], "主题甲")
        self.assertIsNone(r["declared_topic_id"])
        self.assertEqual(summary["topic_reviewed"], True)
        self.assertEqual(summary["topic_anchors_checked"], 1)
        self.assertEqual(summary["topic_anchor_hits"], 1)
        # 未声明主题 → 警告（提示补 topic_id），但**不是错误**
        self.assertEqual(r["errors"], [])
        self.assertTrue(any("未声明主题" in w for w in r["warnings"]), r)

    def test_anchor_by_bare_session_id_resolves(self):
        """注册表存 `sid`、卡片锚点写裸 `session_id` —— 必须解析到同一主题。

        破坏方式（本测试会红）：把 `_load_topic_membership` 的别名展开
        （`_add_sid_aliases`）去掉 → topics 变空、多出"未登记于任何主题"。
        """
        cards_dir = self.root / "cards"
        _write_card(cards_dir, "kc-1", ID_A)  # 裸 id，不是 src:aaa
        results, summary = validate_cards(cards_dir, self.db,
                                          topics_meta=self.meta)
        self.assertEqual([t["id"] for t in results[0]["topics"]], [self.tid])
        self.assertEqual(summary["topic_anchor_hits"], 1)
        self.assertGreaterEqual(summary["topic_alias_keys"], 1)

    def test_scaffold_anchor_form_is_resolved(self):
        """`cards new` 脚手架锚点用的是 session_id 裸形态（真实产物口径）。"""
        out = self.root / "scaffold"
        p = scaffold_card(self.db, SID_A, out, ctype="pitfall")
        results, summary = validate_cards(out, self.db, topics_meta=self.meta)
        self.assertEqual(len(results), 1)
        self.assertEqual([t["id"] for t in results[0]["topics"]], [self.tid],
                         f"脚手架锚点未解析到主题: {results[0]}")
        self.assertEqual(summary["topic_anchor_hits"], 1)
        self.assertTrue(p.is_file())

    def test_unregistered_anchor_warns_no_link(self):
        cards_dir = self.root / "cards"
        _write_card(cards_dir, "kc-1", SID_B)  # 未登记任何主题/零散
        results, summary = validate_cards(cards_dir, self.db,
                                          topics_meta=self.meta)
        self.assertEqual(results[0]["topics"], [])
        self.assertTrue(any("未登记于任何主题" in w
                            for w in results[0]["warnings"]), results[0])
        self.assertEqual(summary["topic_undeclared_cards"], 1)
        self.assertEqual(summary["topic_anchor_hits"], 0)

    def test_mixed_anchors_report_both(self):
        """混合卡（一个锚点命中成员、一个完全没登记）：两条警告都要出。

        否则"部分锚点漏登"会静默消失在"已登记在主题 X"那句里。
        """
        cards_dir = self.root / "cards"
        _write_card_multi(cards_dir, "kc-1", [SID_A, SID_B])
        results, summary = validate_cards(cards_dir, self.db,
                                          topics_meta=self.meta)
        warns = results[0]["warnings"]
        self.assertTrue(any("未声明主题" in w for w in warns), warns)
        self.assertTrue(any("未登记于任何主题" in w for w in warns), warns)
        self.assertEqual(summary["topic_undeclared_cards"], 1)
        self.assertEqual(summary["topic_anchors_checked"], 2)
        self.assertEqual(summary["topic_anchor_hits"], 1)

    def test_noise_anchor_is_not_undeclared(self):
        """已登记零散 ≠ 漏归主题（口径必须分开）。"""
        register_noise(self.meta, [{"sid": SID_B, "reason": "单轮短问答"}])
        cards_dir = self.root / "cards"
        _write_card(cards_dir, "kc-1", SID_B)
        results, summary = validate_cards(cards_dir, self.db,
                                          topics_meta=self.meta)
        self.assertEqual(results[0]["topics"], [])
        self.assertTrue(any("零散" in w for w in results[0]["warnings"]),
                        results[0])
        # 零散卡不得同时报"未登记于任何主题"（那是另一种口径）
        self.assertFalse(any("未登记于任何主题" in w
                             for w in results[0]["warnings"]), results[0])
        self.assertEqual(summary["topic_undeclared_cards"], 0)
        self.assertEqual(summary["topic_anchor_noise"], 1)

    def test_noise_anchor_bare_id_is_not_undeclared(self):
        """零散登记也存 sid，锚点写裸 id 时同样不能误报成"无关联"。"""
        register_noise(self.meta, [{"sid": SID_B, "reason": "单轮短问答"}])
        cards_dir = self.root / "cards"
        _write_card(cards_dir, "kc-1", ID_B)
        _results, summary = validate_cards(cards_dir, self.db,
                                           topics_meta=self.meta)
        self.assertEqual(summary["topic_undeclared_cards"], 0)
        self.assertEqual(summary["topic_anchor_noise"], 1)


class TestConsistency(Base):
    """4：声明了主题 → 必须与成员资格对得上，否则错误级。"""

    def test_declared_topic_matching_member_passes(self):
        cards_dir = self.root / "cards"
        _write_card(cards_dir, "kc-1", SID_A,
                    extra=f"topic_id: {self.tid}\n")
        results, summary = validate_cards(cards_dir, self.db,
                                          topics_meta=self.meta)
        r = results[0]
        self.assertEqual(r["declared_topic_id"], self.tid)
        self.assertEqual(r["errors"], [])
        self.assertEqual(r["warnings"], [])
        self.assertEqual(summary["ok"], 1)
        self.assertEqual(summary["topic_undeclared_cards"], 0)

    def test_declared_topic_foreign_to_anchor_is_error(self):
        """声明主题甲，但锚点是会话乙（乙不在主题甲成员里）→ 错误。

        破坏方式（本测试会红）：把 `_topic_consistency` 的不一致分支
        改成 append 到 warns → errors 为空。
        """
        cards_dir = self.root / "cards"
        _write_card(cards_dir, "kc-1", SID_B,
                    extra=f"topic_id: {self.tid}\n")
        results, summary = validate_cards(cards_dir, self.db,
                                          topics_meta=self.meta)
        errs = results[0]["errors"]
        self.assertTrue(any("与卡片声称的主题不一致" in e for e in errs), errs)
        self.assertEqual(summary["error"], 1)

    def test_declared_unknown_topic_is_error(self):
        cards_dir = self.root / "cards"
        _write_card(cards_dir, "kc-1", SID_A, extra="topic_id: tp-ghost\n")
        results, _summary = validate_cards(cards_dir, self.db,
                                           topics_meta=self.meta)
        errs = results[0]["errors"]
        self.assertTrue(any("tp-ghost" in e for e in errs), errs)

    def test_declared_topic_alias_key(self):
        """`topic` 与 `topic_id` 都认（同一口径，别名不许只认一个）。"""
        cards_dir = self.root / "cards"
        _write_card(cards_dir, "kc-1", SID_A,
                    extra=f"topic: {self.tid}\n")
        results, _summary = validate_cards(cards_dir, self.db,
                                           topics_meta=self.meta)
        self.assertEqual(results[0]["declared_topic_id"], self.tid)
        self.assertEqual(results[0]["errors"], [])

    def test_declared_topic_without_anchors_is_error(self):
        """声明了主题却没有 anchors → 无法核对，错误级（不静默通过）。"""
        cards_dir = self.root / "cards"
        text = CARD_TMPL.format(cid="kc-1", sid=SID_A,
                                extra=f"topic_id: {self.tid}\n")
        text = text.replace(
            'anchors: [{session_id: "src:aaa", turn: 1}]\n', "anchors: []\n")
        cards_dir.mkdir(parents=True, exist_ok=True)
        (cards_dir / "kc-1.md").write_text(text, encoding="utf-8",
                                           newline="\n")
        results, _summary = validate_cards(cards_dir, self.db,
                                           topics_meta=self.meta)
        self.assertTrue(any("没有 anchors 可供核对" in e
                            for e in results[0]["errors"]),
                        results[0]["errors"])


class TestFailLoud(Base):
    """6：读不出注册表 → 报错，不退化成"没有主题"。"""

    def test_missing_registry_raises(self):
        cards_dir = self.root / "cards"
        _write_card(cards_dir, "kc-1", SID_A)
        ghost = self.root / "no-such" / "topics_meta.db"
        with self.assertRaises(FileNotFoundError) as ctx:
            validate_cards(cards_dir, self.db, topics_meta=ghost)
        self.assertIn("主题注册表不存在", str(ctx.exception))

    def test_not_a_topics_db_raises(self):
        """指向别的库（有 sessions 无 topics 表）→ RuntimeError，不是静默空表。"""
        cards_dir = self.root / "cards"
        _write_card(cards_dir, "kc-1", SID_A)
        other = self.root / "not_topics.db"
        con = sqlite3.connect(str(self.db))
        try:
            con.execute("VACUUM INTO ?", (str(other),))  # 复制出另一个库
        finally:
            con.close()
        with self.assertRaises(RuntimeError) as ctx:
            validate_cards(cards_dir, self.db, topics_meta=other)
        self.assertIn("topics", str(ctx.exception))

    def test_same_file_as_db_rejected(self):
        cards_dir = self.root / "cards"
        _write_card(cards_dir, "kc-1", SID_A)
        with self.assertRaises(ValueError):
            validate_cards(cards_dir, self.db, topics_meta=self.db)

    def test_broken_members_json_raises(self):
        con = sqlite3.connect(str(self.meta))
        con.execute("UPDATE topics SET members='{oops' WHERE id=?",
                    (self.tid,))
        con.commit()
        con.close()
        cards_dir = self.root / "cards"
        _write_card(cards_dir, "kc-1", SID_A)
        with self.assertRaises(ValueError) as ctx:
            validate_cards(cards_dir, self.db, topics_meta=self.meta)
        self.assertIn("JSON", str(ctx.exception))


class TestCli(Base):
    """CLI：--topics-meta 是可选门；缺省行为不变；给错则退出码 2。"""

    def _run(self, *extra: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(ROOT)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH")
                           else []))
        return subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "harvester", "cards",
             "validate", "--root", str(self.root / "cards"),
             "--db", str(self.db), *extra],
            cwd=str(ROOT), env=env, capture_output=True, text=True,
            encoding="utf-8")

    def test_cli_without_topics_meta_unchanged(self):
        _write_card(self.root / "cards", "kc-1", SID_A)
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("主题核对：**未执行**", r.stdout)

    def test_cli_with_topics_meta_reports_topic(self):
        _write_card(self.root / "cards", "kc-1", SID_A)
        r = self._run("--topics-meta", str(self.meta))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("主题核对（--topics-meta）", r.stdout)
        self.assertIn(self.tid, r.stdout)

    def test_cli_inconsistency_exits_1(self):
        _write_card(self.root / "cards", "kc-1", SID_B,
                    extra=f"topic_id: {self.tid}\n")
        r = self._run("--topics-meta", str(self.meta))
        self.assertEqual(r.returncode, 1, r.stderr + r.stdout)
        self.assertIn("与卡片声称的主题不一致", r.stdout)

    def test_cli_missing_registry_exits_2_with_message(self):
        _write_card(self.root / "cards", "kc-1", SID_A)
        r = self._run("--topics-meta", str(self.root / "ghost.db"))
        self.assertEqual(r.returncode, 2, r.stderr + r.stdout)
        self.assertIn("主题注册表不存在", r.stderr)


class TestReportJsonShape(Base):
    """给机器（api/view）的那一半：结果字段必须是可直接解析的结构。"""

    def test_topics_field_is_jsonable(self):
        cards_dir = self.root / "cards"
        _write_card(cards_dir, "kc-1", SID_A)
        results, summary = validate_cards(cards_dir, self.db,
                                          topics_meta=self.meta)
        payload = json.dumps({"results": results, "summary": summary},
                             ensure_ascii=False)
        self.assertIn(self.tid, payload)
        self.assertIsInstance(results[0]["topics"], list)
        self.assertEqual(sorted(results[0]["topics"][0]),
                         ["id", "name"])

    def test_summary_counts_reconcile(self):
        """对账：命中 + 零散 + 未关联 == 核对过的锚点数（不许有黑洞）。"""
        register_noise(self.meta, [{"sid": SID_B, "reason": "零散"}])
        cards_dir = self.root / "cards"
        _write_card(cards_dir, "kc-1", SID_A)
        cards_dir2 = self.root / "cards2"
        _write_card(cards_dir2, "kc-2", SID_B)
        results, summary = validate_cards(self.root / "cards", self.db,
                                          topics_meta=self.meta)
        _r2, s2 = validate_cards(cards_dir2, self.db, topics_meta=self.meta)
        total = summary["topic_anchors_checked"] + s2["topic_anchors_checked"]
        covered = (summary["topic_anchor_hits"] + s2["topic_anchor_hits"]
                   + summary["topic_anchor_noise"]
                   + s2["topic_anchor_noise"])
        self.assertEqual(covered, total)
        self.assertEqual(len(results), 1)


if __name__ == "__main__":
    unittest.main()
