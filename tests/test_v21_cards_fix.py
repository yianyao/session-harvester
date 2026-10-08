# -*- coding: utf-8 -*-
"""v0.21 P0-1 cards 校验器完整性测试（SOP-P0-1）：

- H8：降级 frontmatter 解析器补块标量（|/|-|/>）与块序列；
- H9：强制 _HAS_YAML=False 是唯一覆盖降级分支的方式；
- H11：cards new 脚手架占位卡必须被 validate 拒绝（错误级）；
- H10：turn:null 警告必须在提前 return 之前、保证可达；
- 未核对 ≠ 通过：evidence_unchecked 显式化 + 结论改写；
- H22 教训：过滤器类断言必须演示"计数真的会变"。
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harvester import cards
from harvester.cards import (_parse_frontmatter, render_report, scaffold_card,
                             validate_card, validate_cards)
from harvester.apiserve import open_ro
from harvester.indexing import SCHEMA, index_session
from harvester.models import Message, SessionRecord


def _fixture_db(tmp: Path) -> Path:
    """单会话 aaa，标题与一条 assistant 原文供 evidence 核对。"""
    db = tmp / "v21_test.db"
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)
    rec = SessionRecord(
        source="src", session_id="aaa", title="原创会话标题",
        created_at="2026-10-08 10:00:00", updated_at="2026-10-08 10:00:00",
        messages=[Message(role="user", text="写一段"),
                  Message(role="assistant",
                          text="证据原文 Alpha beta gamma delta")])
    index_session(con, rec)
    con.commit()
    con.close()
    return db


_BLOCK_SCALAR_FM = (
    "---\n"
    "id: kc-x\n"
    "title: t\n"
    "type: pitfall\n"
    "tags: []\n"
    'anchors: [{session_id: "aaa", turn: 1}]\n'
    "evidence: |\n"
    "  证据原文 Alpha beta gamma delta\n"
    "confidence: 0.8\n"
    "created: 2026-10-08\n"
    "---\n正文\n"
)

_BLOCK_SEQ_FM = (
    "---\n"
    "id: kc-y\n"
    "title: t\n"
    "type: insight\n"
    "tags: []\n"
    "anchors:\n"
    '  - session_id: "aaa"\n'
    '  - {session_id: "aaa", turn: 1}\n'
    "evidence: 引用第一行 Alpha\n"
    "confidence: 0.9\n"
    "created: 2026-10-08\n"
    "---\n正文\n"
)


class TestDegradedFrontmatter(unittest.TestCase):
    """H8/H9：mock _HAS_YAML=False 强制走降级分支（venv 有 pyyaml 时
    该分支不可达，mock 是唯一覆盖方式）。"""

    def test_block_scalar_degraded(self):
        with mock.patch.object(cards, "_HAS_YAML", False):
            fm, _body = _parse_frontmatter(_BLOCK_SCALAR_FM)
        self.assertIsNotNone(fm)
        self.assertIn("证据原文", str(fm.get("evidence")))
        # H8 症状反断言：不得残留 '|' 字面量
        self.assertNotEqual(str(fm.get("evidence")).strip(), "|")

    def test_block_scalar_folded_degraded(self):
        text = _BLOCK_SCALAR_FM.replace("evidence: |\n", "evidence: >-\n")
        with mock.patch.object(cards, "_HAS_YAML", False):
            fm, _body = _parse_frontmatter(text)
        self.assertIsNotNone(fm)
        self.assertIn("证据原文", str(fm.get("evidence")))

    def test_block_seq_degraded(self):
        with mock.patch.object(cards, "_HAS_YAML", False):
            fm, _body = _parse_frontmatter(_BLOCK_SEQ_FM)
        self.assertIsInstance(fm.get("anchors"), list)
        self.assertEqual(len(fm["anchors"]), 2)
        self.assertEqual(fm["anchors"][0].get("session_id"), "aaa")
        self.assertEqual(fm["anchors"][1].get("turn"), 1)

    def test_flow_seq_null_semantics_degraded(self):
        """降级分支 flow seq 内裸 null 必须解析为 Python None（与 PyYAML
        对齐）——否则 turn:null 警告在无 PyYAML 环境不可达（真实卡
        kc-20261006-0004 双环境 diff 实测）。"""
        text = ('---\nid: kc-n\ntitle: t\ntype: pitfall\ntags: []\n'
                'anchors: [{session_id: "aaa", turn: null}]\n'
                "evidence: 证据原文 Alpha beta gamma delta\n"
                "confidence: 0.8\ncreated: 2026-10-08\n---\n正文\n")
        with mock.patch.object(cards, "_HAS_YAML", False):
            fm, _body = _parse_frontmatter(text)
        self.assertIsNone(fm["anchors"][0]["turn"])

    def test_degraded_endtoend_evidence_check(self):
        """降级分支下整套 validate 仍可用：块标量 evidence 能被核对
        （checked>=1 且无 miss）。当前红：evidence=='|'，核对必然 miss。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = _fixture_db(root)
            cards_dir = root / "cards"
            cards_dir.mkdir()
            (cards_dir / "c.md").write_text(_BLOCK_SCALAR_FM,
                                            encoding="utf-8", newline="\n")
            con = open_ro(db)
            try:
                with mock.patch.object(cards, "_HAS_YAML", False):
                    results, summary = validate_cards(cards_dir, db, con=con)
            finally:
                con.close()
            self.assertEqual(summary["evidence_checked"], 1)
            self.assertEqual(summary["evidence_misses"], 0)
            self.assertEqual(results[0]["errors"], [])
            self.assertEqual(results[0]["warnings"], [])


class TestYamlFrontmatter(unittest.TestCase):
    """pyyaml 在场路径（防回归：改降级分支不得破坏正常分支）。"""

    def test_block_scalar_yaml(self):
        fm, _body = _parse_frontmatter(_BLOCK_SCALAR_FM)
        self.assertIn("证据原文", str(fm.get("evidence")))

    def test_block_seq_yaml(self):
        fm, _body = _parse_frontmatter(_BLOCK_SEQ_FM)
        self.assertIsInstance(fm.get("anchors"), list)
        self.assertEqual(len(fm["anchors"]), 2)


class TestPlaceholderRejected(unittest.TestCase):
    """H11：cards new 脚手架产物必须被 validate 出错误级。"""

    def test_scaffold_card_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = _fixture_db(root)
            out = root / "cards"
            p = scaffold_card(db, "aaa", out, ctype="pitfall")
            errors, _warns, _fm = validate_card(p)
            self.assertTrue(any("[占位符]" in e for e in errors),
                            f"脚手架卡未被拒：{errors}")
            results, summary = validate_cards(out, db)
            self.assertGreater(summary["placeholder_cards"], 0)
            report = render_report(results, summary, out)
            self.assertIn("禁止并入主库", report)

    def test_title_equals_session_title(self):
        """title 与锚点会话原标题相同 = 未补全脚手架痕迹 → 错误级。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = _fixture_db(root)
            cards_dir = root / "cards"
            cards_dir.mkdir()
            (cards_dir / "c.md").write_text(
                "---\n"
                "id: kc-t\n"
                "title: 原创会话标题\n"
                "type: pitfall\n"
                "tags: []\n"
                'anchors: [{session_id: "aaa", turn: 1}]\n'
                "evidence: |\n"
                "  证据原文 Alpha beta gamma delta\n"
                "confidence: 0.8\n"
                "created: 2026-10-08\n"
                "---\n正文完整，只留标题没改。\n",
                encoding="utf-8", newline="\n")
            results, _summary = validate_cards(cards_dir, db)
            errs = results[0]["errors"]
            self.assertTrue(any("原标题" in e for e in errs),
                            f"原标题痕迹未被检出：{errs}")

    def test_body_placeholder_rejected(self):
        """正文 <待补 占位 → 错误级。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = _fixture_db(root)
            cards_dir = root / "cards"
            cards_dir.mkdir()
            (cards_dir / "c.md").write_text(
                "---\n"
                "id: kc-b\n"
                "title: 描述性标题\n"
                "type: pitfall\n"
                "tags: []\n"
                'anchors: [{session_id: "aaa", turn: 1}]\n'
                "evidence: |\n"
                "  证据原文 Alpha beta gamma delta\n"
                "confidence: 0.8\n"
                "created: 2026-10-08\n"
                "---\n"
                "## 现象\n\n<待补：这条记录说明了什么问题>\n",
                encoding="utf-8", newline="\n")
            errors, _warns, _fm = validate_card(cards_dir / "c.md")
            self.assertTrue(any("[占位符]" in e for e in errors))


class TestTurnNullReachable(unittest.TestCase):
    """H10：turn:null 警告在 evidence 为空（提前 return 路径）时仍可达。
    注意走 validate_cards 路径（证据核对在那里触发）。"""

    def test_warn_without_evidence(self):
        text = ("---\n"
                "id: kc-z\n"
                "title: 描述性标题\n"
                "type: pitfall\n"
                "tags: []\n"
                'anchors: [{session_id: "aaa", turn: null}]\n'
                "evidence: \n"
                "confidence: 0.5\n"
                "created: 2026-10-08\n"
                "---\n正文\n")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = _fixture_db(root)
            cards_dir = root / "cards"
            cards_dir.mkdir()
            (cards_dir / "c.md").write_text(text, encoding="utf-8",
                                            newline="\n")
            con = open_ro(db)
            try:
                results, _summary = validate_cards(cards_dir, db, con=con)
            finally:
                con.close()
            warns = results[0]["warnings"]
            self.assertTrue(any("turn 为 null" in w for w in warns),
                            f"turn:null 警告不可达：{warns}")


class TestEvidenceUnchecked(unittest.TestCase):
    """未核对 ≠ 通过：evidence_unchecked 显式化 + 结论改写。"""

    def test_unchecked_flagged_without_db(self):
        """无 db（原文不可得）→ checked==0、evidence_unchecked>0、
        结论不得视为通过。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cards_dir = root / "cards"
            cards_dir.mkdir()
            (cards_dir / "c.md").write_text(
                "---\n"
                "id: kc-u\n"
                "title: 描述性标题\n"
                "type: pitfall\n"
                "tags: []\n"
                'anchors: [{session_id: "aaa", turn: 1}]\n'
                "evidence: |\n"
                "  证据原文 Alpha beta gamma delta\n"
                "confidence: 0.8\n"
                "created: 2026-10-08\n"
                "---\n正文完整。\n",
                encoding="utf-8", newline="\n")
            results, summary = validate_cards(cards_dir, None)
            self.assertEqual(summary["evidence_checked"], 0)
            self.assertEqual(summary["evidence_unchecked"], 1)
            report = render_report(results, summary, cards_dir)
            self.assertIn("不得视为通过", report)

    def test_checked_count_changes_when_evidence_broken(self):
        """H22 教训：破坏 evidence 行数后 checked 计数必须真的变化。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = _fixture_db(root)
            con = open_ro(db)
            cards_dir = root / "cards"
            cards_dir.mkdir()
            full = ("---\n"
                    "id: kc-c\n"
                    "title: 描述性标题\n"
                    "type: pitfall\n"
                    "tags: []\n"
                    'anchors: [{session_id: "aaa", turn: 1}]\n'
                    "evidence: |\n"
                    "  证据原文 Alpha beta gamma delta\n"
                    "  证据原文 Alpha beta gamma delta\n"
                    "confidence: 0.8\n"
                    "created: 2026-10-08\n"
                    "---\n正文完整。\n")
            (cards_dir / "c.md").write_text(full, encoding="utf-8",
                                            newline="\n")
            try:
                _r1, s1 = validate_cards(cards_dir, db, con=con)
                self.assertEqual(s1["evidence_checked"], 2)
                broken = full.replace(
                    "  证据原文 Alpha beta gamma delta\n"
                    "  证据原文 Alpha beta gamma delta\n",
                    "  证据原文 Alpha beta gamma delta\n")
                (cards_dir / "c.md").write_text(broken, encoding="utf-8",
                                                newline="\n")
                _r2, s2 = validate_cards(cards_dir, db, con=con)
                self.assertEqual(s2["evidence_checked"], 1)
            finally:
                con.close()


if __name__ == "__main__":
    unittest.main()
