# -*- coding: utf-8 -*-
"""v0.31 chain-audit 测试：两道内容门（引文逐字 / 锚点语义）。

这两道门此前是 docs/reports 下的一次性脚本，但每写一条 chain 都要跑，
且都抓到过真错：
- 引文门：子代理把 bigram 文本当原文，引文经"还原"后**不是逐字**；
  还有压缩改写（"该夸的夸，该骂的骂"漏掉后半句）。
- 锚点门：note 写着"王德荣与沈望的剧组旧交"，而该 turn 讲的是"锚点的
  心理学依据"——整条**挂错回合**；`chain-validate` 只查结构，查不出这个。
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import yaml  # noqa: F401 —— 本测试**依赖 PyYAML**：缺它就该在导入期显式失败（H9），
# 而不是悄悄跳过（`chain-audit` 的两道门都要读 chain frontmatter）

from harvester.chainaudit import (_anchor_pairs, _ascii_quotes, audit_chain,
                                  render_audit)
from harvester.indexing import SCHEMA, index_session
from harvester.models import Message, SessionRecord
from harvester.topics import ensure_topics_db

FM = """\
---
topic: 主题甲
topic_id: tp-x
members:
  - src:s1
anchors:
  - stage: 阶段一
    span: 2026-01-01 ~ 2026-01-02
    nodes:
      - sid: src:s1
        turn: 1
        note: {note1}
      - sid: src:s1
        turn: 2
        note: {note2}
generated_from:
  db_mtime: "2026-10-10 00:00:00"
prompt_version: v-test
---

# 思维链：主题甲

{body}
"""

BODY_OK = "他写道「丁樾瘫坐在椅子上回气」，这是原文逐字。"
BODY_BAD = "他写道「丁樾瘫坐在椅子上**喘气**」，这是改写过的。"


class TestChainAudit(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "h.db"
        con = sqlite3.connect(str(self.db))
        con.executescript(SCHEMA)
        index_session(con, SessionRecord(
            source="src", session_id="s1", title="甲会话",
            created_at="2026-01-01 10:00:00", updated_at="2026-01-01 10:00:00",
            messages=[Message(role="user", text="丁樾瘫坐在椅子上回气。"),
                      Message(role="assistant", text="收到。"),
                      Message(role="user", text="设计王德荣和沈望的剧组旧交")]))
        con.commit()
        con.close()
        self.meta = ensure_topics_db(root / "topics_meta.db")
        self.chain = root / "chain-x.md"

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def _write(self, body, note1="丁樾回气的动作描写", note2="王德荣与沈望"):
        self.chain.write_text(FM.format(note1=note1, note2=note2, body=body),
                              encoding="utf-8")

    def test_clean_chain_passes_both_gates(self):
        self._write(BODY_OK)
        r = audit_chain(self.chain, self.db)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["quotes"]["exact"], 1)
        self.assertEqual(r["quotes"]["misses"], [])
        self.assertEqual(r["anchors"]["suspicious"], [])

    def test_rewritten_quote_fails_quote_gate(self):
        """引文被改写 → 未命中 → 整体不合格（退出码非 0 的依据）。"""
        self._write(BODY_BAD)
        r = audit_chain(self.chain, self.db)
        self.assertFalse(r["ok"])
        self.assertEqual(len(r["quotes"]["misses"]), 1)
        self.assertIn("喘气", r["quotes"]["misses"][0])

    def test_quote_gate_ignores_markdown_emphasis(self):
        """`**加粗**` 是排版标记不是数据：加粗后仍应逐字命中。"""
        self._write("他写道「丁樾瘫坐在椅子上**回气**」。")
        r = audit_chain(self.chain, self.db)
        self.assertTrue(r["quotes"]["ok"], r["quotes"])

    def test_mismatched_anchor_is_flagged(self):
        """turn 1 的 note 说"锚点的心理学依据"（原文里没有），turn 2 的 note
        与原文对得上 → 只应有 1 条疑似错配。"""
        self._write(BODY_OK, note1="锚点的心理学与哲学依据",
                    note2="王德荣与沈望的剧组旧交")
        r = audit_chain(self.chain, self.db)
        self.assertFalse(r["anchors"]["ok"])
        sus = r["anchors"]["suspicious"]
        self.assertEqual(len(sus), 1)
        self.assertEqual(sus[0]["turn"], 1)
        self.assertEqual(sus[0]["overlap"], [])
        self.assertFalse(r["ok"])          # 有疑似错配 → 不通过

    def test_correct_anchor_has_overlap(self):
        self._write(BODY_OK, note1="丁樾瘫坐回气", note2="王德荣与沈望")
        r = audit_chain(self.chain, self.db)
        self.assertTrue(r["anchors"]["ok"], r["anchors"]["suspicious"])
        first = r["anchors"]["nodes"][0]
        self.assertTrue(first["overlap"])

    def test_gates_can_be_skipped_independently(self):
        self._write(BODY_BAD, note1="完全无关的说明", note2="也无关")
        r = audit_chain(self.chain, self.db, anchors=False)
        self.assertFalse(r["quotes"]["ok"])
        self.assertNotIn("anchors", r)
        r2 = audit_chain(self.chain, self.db, quotes=False)
        self.assertIn("quotes", r2) is False if False else None
        self.assertNotIn("quotes", r2)
        self.assertIn("anchors", r2)

    def test_report_states_limits(self):
        self._write(BODY_BAD, note1="完全无关的说明", note2="也无关")
        txt = render_audit(audit_chain(self.chain, self.db))
        self.assertIn("引文逐字门", txt)
        self.assertIn("锚点语义门", txt)
        self.assertIn("工具不判语义", txt)
        self.assertIn("未命中", txt)


FM2 = """\
---
topic: 主题甲
topic_id: tp-x
members:
  - src:s1
  - src:s2
  - src:s3
  - src:s4
dedup:
  reps: 3
  duplicates: 1
anchors:
  - stage: 阶段一
    span: 2026-01-01 ~ 2026-01-02
    nodes:
      - sid: src:s1
        turn: 1
        note: 丁樾回气的动作描写
      - sid: src:s1
        turn: 2
        note: 王德荣与沈望
      - sid: src:s1
        turn: 3
        note: 再改一版试试
  - stage: 阶段二
    span: 2026-01-03 ~ 2026-01-04
    nodes:
      - sid: src:s3
        turn: 1
        note: 完全不同的另一件事
      - sid: src:s4
        turn: 1
        note: 甲会话的标题依据
generated_from:
  db_mtime: "2026-10-10 00:00:00"
prompt_version: v-test
---

# 思维链：主题甲

他写道「丁樾瘫坐在椅子上回气」（{src:s1, turn 1}），随后（同会话 turn 2，另见 turn 3）补充。

另见（{src:s3, turn 1}）。
"""


class TestCoverageAndReconciliation(unittest.TestCase):
    """v0.45 证据覆盖 + 正文↔frontmatter 锚点对账。

    判据都能说清"什么情况会红"：
    - 没锚点的**重复会话**不算缺口（H40 锚点不迁移），没锚点的**独立代表**才算；
    - 简写 `（同会话 turn N）` 必须归到**同一行最近的前置完整锚点**：旧实现拿
      `turn N` 全库匹配，于是"别的会话引过 turn 1"会把真漏引的节点粉饰成已引用；
    - ASCII 引文成对抽取：`"行不行"`（3 字）后面的下一个引用不能被错配成
      "闭引号→下一个开引号"之间的叙述（真库首版正则即此错，82 条里二十多条假未命中）。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "h.db"
        con = sqlite3.connect(str(self.db))
        con.executescript(SCHEMA)
        sess = {
            "s1": ("甲会话", ["丁樾瘫坐在椅子上回气。", "设计王德荣和沈望的剧组旧交",
                             "再改一版试试"]),
            "s2": ("甲会话（重发）", ["丁樾瘫坐在椅子上回气。",
                                     "设计王德荣和沈望的剧组旧交",
                                     "再改一版试试"]),
            "s3": ("乙会话", ["完全不同的另一件事" * 6]),
            "s4": ("甲会话", ["另一件事"]),
        }
        for sid, (title, turns) in sess.items():
            index_session(con, SessionRecord(
                source="src", session_id=sid, title=title,
                created_at="2026-01-01 10:00:00",
                updated_at="2026-01-01 10:00:00",
                messages=[m for t in turns
                          for m in (Message(role="user", text=t),
                                    Message(role="assistant", text="答"))]))
        con.commit()
        con.close()
        self.meta = ensure_topics_db(root / "topics_meta.db")
        self.chain = root / "chain-c.md"
        self.chain.write_text(FM2, encoding="utf-8")

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def test_uncovered_duplicate_is_not_a_gap(self):
        """s2 与 s1 逐字同稿 → 算重复会话，不算"真缺口"。"""
        c = audit_chain(self.chain, self.db, quotes=False,
                        anchors=False)["coverage"]
        self.assertEqual(c["members"], 4)
        self.assertEqual((c["reps"], c["duplicates"]), (3, 1))
        self.assertEqual(c["anchored_members"], 3)      # s1/s3/s4
        self.assertEqual([r["sid"] for r in c["uncovered"]], ["src:s2"])
        self.assertEqual(c["uncovered_reps"], [])
        self.assertEqual([r["kind"] for r in c["uncovered_dups"]], ["dup"])
        self.assertEqual(c["uncovered_dups"][0]["rep"], "src:s1")

    def test_unanchored_rep_is_the_gap(self):
        """把 s4 的节点删掉 → 它变成"无锚点的独立代表"（真缺口）。"""
        text = FM2.replace("      - sid: src:s4\n        turn: 1\n"
                           "        note: 甲会话的标题依据\n", "")
        self.chain.write_text(text, encoding="utf-8")
        c = audit_chain(self.chain, self.db, quotes=False,
                        anchors=False)["coverage"]
        self.assertEqual([r["sid"] for r in c["uncovered_reps"]], ["src:s4"])

    def test_body_only_anchor_is_reported(self):
        """正文引了 `{src:s2, turn 1}` 却没登记 → 必须报出来（否则 view 不可点）。"""
        self.chain.write_text(
            FM2.replace("另见（{src:s3, turn 1}）。",
                        "另见（{src:s3, turn 1}）与（{src:s2, turn 1}）。"),
            encoding="utf-8")
        c = audit_chain(self.chain, self.db, quotes=False,
                        anchors=False)["coverage"]
        self.assertEqual(c["body_only"], [{"sid": "src:s2", "turn": 1}])

    def test_shorthand_binds_to_nearest_leading_anchor(self):
        """s4 turn 1 全篇没有引用（"turn 1" 只在别的会话的锚点里出现）→ 判未用；
        s1 turn 2/3 由同一行的 `（同会话 turn 2，另见 turn 3）` 引用 → 判简写。"""
        c = audit_chain(self.chain, self.db, quotes=False,
                        anchors=False)["coverage"]
        self.assertEqual(c["fm_unused"], [{"sid": "src:s4", "turn": 1}])
        self.assertEqual([(r["sid"], r["turn"]) for r in c["fm_shorthand"]],
                         [("src:s1", 2), ("src:s1", 3)])
        self.assertEqual(c["shorthand_unresolved"], [])

    def test_shorthand_without_leading_anchor_is_unresolved(self):
        """断言这个口径真的在起作用：**另起一行**写 `（turn 2）` → 无 sid 可归属。"""
        self.chain.write_text(
            FM2.replace("（同会话 turn 2，另见 turn 3）", "。\n\n（turn 2）"),
            encoding="utf-8")
        c = audit_chain(self.chain, self.db, quotes=False,
                        anchors=False)["coverage"]
        self.assertIn(2, c["shorthand_unresolved"])
        self.assertNotIn(2, [r["turn"] for r in c["fm_shorthand"]])

    def test_stage_node_counts_and_empty_stage(self):
        c = audit_chain(self.chain, self.db, quotes=False,
                        anchors=False)["coverage"]
        self.assertEqual([s["nodes"] for s in c["stages"]], [3, 2])
        self.assertEqual(c["empty_stages"], [])
        self.assertEqual(c["nodes"], 5)

    def test_anchor_note_may_match_session_title(self):
        """note 写标题级依据不算错配（真库 5 条告警里 3 条属此类）。"""
        a = audit_chain(self.chain, self.db, quotes=False)["anchors"]
        self.assertEqual(a["suspicious"], [])
        s4 = [r for r in a["nodes"] if r["sid"] == "src:s4"][0]
        self.assertEqual(s4["title"], "甲会话")
        self.assertTrue(s4["overlap"])       # 重叠来自标题，不是 raw

    def test_ascii_quote_gate_is_opt_in_and_says_vacuous(self):
        """只有 ASCII 引文时：默认门**空转**（必须显式报），开了才真核。"""
        head = FM2.split("\n---\n")[0] + "\n---\n"
        self.chain.write_text(
            head + '\n# 思维链：主题甲\n\n'
                   '他写道"丁樾瘫坐在椅子上回气"，又说"这句是自造并非原文"'
                   '（{src:s1, turn 1}）。\n', encoding="utf-8")
        r = audit_chain(self.chain, self.db, anchors=False)["quotes"]
        self.assertEqual(r["total"], 0)          # 「」0 条 → 门空转
        self.assertTrue(r["vacuous"])
        self.assertFalse(r["ascii_checked"])
        self.assertTrue(r["ok"])              # 空转不是失败，但报告要说出来
        r2 = audit_chain(self.chain, self.db, anchors=False,
                         quotes_ascii=True)["quotes"]
        self.assertTrue(r2["ascii_checked"])
        self.assertEqual(r2["ascii_exact"], 1)          # 逐字那条命中
        self.assertEqual(r2["ascii_misses"], ["这句是自造并非原文"])
        self.assertFalse(r2["ok"])                      # 开了就是硬门
        txt = render_audit(audit_chain(self.chain, self.db, anchors=False))
        self.assertIn("本门空转", txt)

    def test_ascii_quotes_pair_without_skipping_short_ones(self):
        """成对抽取：短引用（3 字）不得让后续引用错位成中间叙述。"""
        body = '他说"行不行"，然后说"这段要改"，最后（{src:s1, turn 1}）。'
        self.assertEqual(_ascii_quotes(body), ["行不行", "这段要改"])

    def test_anchor_pairs_separates_full_and_shorthand(self):
        body = "先（{src:a, turn 1}）（同会话 turn 2）。\n\n（turn 5）另起一段。"
        p = _anchor_pairs(body)
        self.assertEqual(p["full"], [("src:a", 1)])
        self.assertIn(("src:a", 2), p["short"])
        self.assertIn((None, 5), p["short"])     # 行内无前置完整锚点 → 无 sid


if __name__ == "__main__":
    unittest.main()
