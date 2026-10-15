# -*- coding: utf-8 -*-
"""v0.74 卡片复核台账 + 类型差异化 + evidence/summary 拆角色。

钉住四件事（用户裁决 2026-10-15："机器只核对它核得了的，把语义裁决还给
人，且人裁过的不许再烦人"）：
1. insight 卡不做 evidence 逐字核对（综述性质）；turn:null 警告仍要出；
2. evidence 与 summary 至少一项非空（summary 在、evidence 缺 → 不再报
   "evidence 为空"；两者都空 = 空壳卡错误）；
3. record_review 落账后 validate_cards 不再显示已裁决警告（additive 计数；
   review_meta 不传 = 旧行为，summary 不多出任何键）；
4. 一言而决（整卡落账）与改判 UPSERT；--warn 单条由数字归一键保证同类
   合并（第 1 行/第 2 行未命中=同一裁决）。
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester.cards import (_warn_key, list_reviews, load_review_map,
                             record_review, validate_cards)
from harvester.indexing import SCHEMA


def _mk_db(path: Path, sid: str = "dsh:abc", raw: str = "深蓝的夜空下"):
    con = sqlite3.connect(str(path))
    con.executescript(SCHEMA)
    con.execute("INSERT INTO sessions (sid, source, session_id, title) "
                "VALUES (?,?,?,?)", (sid, "dsh", sid.split(":", 1)[1], "标题"))
    con.execute("INSERT INTO messages (sid, role, ts, text, raw) "
                "VALUES (?,?,?,?,?)", (sid, "user", "2026-10-06 10:00:00",
                                       raw, raw))
    con.commit()
    con.close()


def _card(root: Path, name: str, ctype: str, anchors: str,
          evidence: str | None = None, summary: str | None = None) -> Path:
    fm = ["---", f"id: {name[:-3]}", "title: 描述性标题", f"type: {ctype}",
          "tags: []", f"anchors: [{anchors}]"]
    if evidence is not None:
        fm.append("evidence: |")
        fm.extend("  " + ln for ln in evidence.splitlines())
    if summary is not None:
        fm.append(f"summary: {summary}")
    fm += ["confidence: 0.8", "created: 2026-10-15", "---", "正文"]
    p = root / name
    p.write_text("\n".join(fm) + "\n", encoding="utf-8", newline="\n")
    return p


class TestTypeDifferentiation(unittest.TestCase):
    """① 类型差异化：insight 不逐字核对；turn:null 全类型保留。"""

    def test_insight_no_verbatim_warn(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "h.db"
            _mk_db(db)
            cards = root / "cards"
            cards.mkdir()
            _card(cards, "kc-i.md", "insight",
                  '{session_id: "dsh:abc", turn: 1}',
                  evidence="这句综述性的话原文里根本没有")
            results, _s = validate_cards(cards, db)
            self.assertEqual(results[0]["warnings"], [],
                             f"insight 不该有逐字警告：{results[0]['warnings']}")

    def test_pitfall_keeps_verbatim_warn(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "h.db"
            _mk_db(db)
            cards = root / "cards"
            cards.mkdir()
            _card(cards, "kc-p.md", "pitfall",
                  '{session_id: "dsh:abc", turn: 1}',
                  evidence="第一句不在原文\n第二句也不在原文")
            results, _s = validate_cards(cards, db)
            warns = results[0]["warnings"]
            self.assertEqual(
                sum(1 for w in warns if "未在锚点会话原文中找到" in w), 2)

    def test_turn_null_still_warns_for_insight(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "h.db"
            _mk_db(db)
            cards = root / "cards"
            cards.mkdir()
            _card(cards, "kc-i.md", "insight",
                  '{session_id: "dsh:abc", turn: null}',
                  evidence="综述一行不少于六个字")
            results, _s = validate_cards(cards, db)
            warns = results[0]["warnings"]
            self.assertTrue(any("turn 为 null" in w for w in warns))
            self.assertFalse(any("未在锚点会话原文中找到" in w for w in warns))


class TestEvidenceSummarySplit(unittest.TestCase):
    """② evidence/summary 拆角色：至少一项；summary 永不逐字核对。"""

    def test_summary_only_no_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "h.db"
            _mk_db(db)
            cards = root / "cards"
            cards.mkdir()
            _card(cards, "kc-s.md", "pitfall",
                  '{session_id: "dsh:abc", turn: 1}',
                  summary="人的蒸馏综述，不是引文")
            results, _s = validate_cards(cards, db)
            self.assertEqual(results[0]["errors"], [])

    def test_both_empty_is_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "h.db"
            _mk_db(db)
            cards = root / "cards"
            cards.mkdir()
            _card(cards, "kc-e.md", "pitfall",
                  '{session_id: "dsh:abc", turn: 1}')
            results, _s = validate_cards(cards, db)
            self.assertTrue(any("evidence/summary 均为空" in e
                                for e in results[0]["errors"]))


class TestReviewLedger(unittest.TestCase):
    """③④ 复核台账：落账即隐去；不传 review_meta = 旧行为零感知。"""

    def _fixture(self, tmp: str):
        root = Path(tmp)
        db = root / "h.db"
        _mk_db(db)
        cards = root / "cards"
        cards.mkdir()
        _card(cards, "kc-p.md", "pitfall",
              '{session_id: "dsh:abc", turn: 1}',
              evidence="第一句不在原文\n第二句也不在原文")
        return root, db, cards

    def test_warn_key_merges_line_numbers(self):
        a = "evidence 第 1 行未在锚点会话原文中找到"
        b = "evidence 第 2 行未在锚点会话原文中找到"
        self.assertEqual(_warn_key(a), _warn_key(b))

    def test_recorded_warnings_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, db, cards = self._fixture(tmp)
            meta = root / "cards_meta.db"
            results, _ = validate_cards(cards, db)
            warns = results[0]["warnings"]
            self.assertEqual(len(warns), 2)
            record_review(meta, "kc-p.md",
                          [_warn_key(w) for w in warns],
                          verdict="accepted", note="综述性质，人工接受")
            # 落账后：默认隐藏
            results, summary = validate_cards(cards, db, review_meta=meta)
            self.assertEqual(results[0]["warnings"], [])
            self.assertEqual(results[0]["reviewed_accepted"], 2)
            self.assertTrue(summary["review_filter"])
            self.assertEqual(summary["reviewed_accepted_total"], 2)
            # --show-review：连同显示
            results, _ = validate_cards(cards, db, review_meta=meta,
                                        show_reviewed=True)
            self.assertEqual(len(results[0]["warnings"]), 2)
            # 不传 review_meta：旧行为，无任何新键
            results, summary = validate_cards(cards, db)
            self.assertEqual(len(results[0]["warnings"]), 2)
            self.assertNotIn("review_filter", summary)
            self.assertNotIn("reviewed_accepted", results[0])

    def test_upsert_changes_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, db, cards = self._fixture(tmp)
            meta = root / "cards_meta.db"
            _results, _ = validate_cards(cards, db)
            record_review(meta, "kc-p.md", ["k"], verdict="accepted")
            record_review(meta, "kc-p.md", ["k"], verdict="fixed",
                          note="已改回逐字")
            rows = list_reviews(meta)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["verdict"], "fixed")
            self.assertEqual(rows[0]["note"], "已改回逐字")

    def test_load_review_map_missing_db(self):
        self.assertEqual(load_review_map(None), {})
        self.assertEqual(load_review_map(Path("无此库.db")), {})


if __name__ == "__main__":
    unittest.main()
