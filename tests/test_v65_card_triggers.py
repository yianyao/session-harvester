# -*- coding: utf-8 -*-
"""v0.78 测试：卡片触发层与反面清单（P6-卡片触发钩子，用户 2026-10-19 全批）。

依据：`docs/PROPOSAL-card-trigger-hook.md`（借鉴 DSH-agent-procedures
调研结论：卡的生产端已机械化，缺的是消费端触发——「记忆是被动的，
触发必须是主动的」）。钉的验收（每条红=什么坏了）：

§1 triggers 可选字段 + 「别再来」段：
1. `cards new` 脚手架含 triggers 占位与「别再来」空表 → 有人删模板段 → 红；
2. 占位未补 validate 红（占位符错误级，复用 <待补 机制）→ 静默放过 → 红；
3. **旧卡零感知**：无 triggers 字段的既有卡 validate 结果与改动前一致
   → 有人把 triggers 加进必填清单 → 红；
4. triggers 给了但非列表 → 警告不报错（照 anchors 非列表先例）；
5. `draft` 蒸馏包模板含 triggers 指引与「别再来」骨架 → 有人漏改 → 红。

§2 cards index-md（只读、只出清单、不裁决）：
6. 三段结构：触发行清单 + 疑似易混对（归一子串口径）+ 钩子模板 → 红；
7. **只读**：跑前后卡目录字节指纹不变 → 有人顺手写文件 → 红；
8. 目录空/不存在诚实空态 + 指引 → 编内容 → 红；
9. 无 triggers 的卡在清单里标「未登记触发词」不缺席 → 红。

夹具全 tempfile；真库/真卡目录一字节不动。
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester.cards import build_index_md, scaffold_card, validate_card


def _mk_min_db(path: Path) -> None:
    con = sqlite3.connect(str(path))
    con.executescript(
        "CREATE TABLE sessions (sid TEXT PRIMARY KEY, source TEXT NOT NULL,"
        " session_id TEXT NOT NULL, title TEXT, category TEXT,"
        " created_at TEXT, updated_at TEXT, file TEXT, model TEXT);"
        "CREATE TABLE steps (sid TEXT, seq INTEGER, ts TEXT, tool TEXT,"
        " phase TEXT, status TEXT, error TEXT, detail TEXT);"
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " sid TEXT, role TEXT, ts TEXT, text TEXT, raw TEXT);")
    con.execute("INSERT INTO sessions VALUES "
                "('s1','dsh','s1','甲','t',NULL,NULL,NULL,NULL)")
    con.commit()
    con.close()


def _mk_card(root: Path, name: str, fm: dict, body: str = "## 结论\nx\n") -> Path:
    lines = ["---"]
    for k, v in fm.items():
        if isinstance(v, list):
            lines.append(f"{k}: [" + ", ".join(
                ('"%s"' % i) if isinstance(i, str) else str(i)
                for i in v) + "]")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    p = root / name
    p.write_text("\n".join(lines) + "\n\n" + body, encoding="utf-8")
    return p


def _good_fm(**over) -> dict:
    fm = dict(id="kc-20261019-0001", title="测试卡", type="pitfall",
              tags=["t"], anchors=[{"session_id": "s1", "turn": 1}],
              evidence="逐字证据", confidence=0.8,
              created="2026-10-19")
    fm.update(over)
    return fm


class TestScaffoldTriggers(unittest.TestCase):
    """§1：脚手架模板带触发层。"""

    def test_scaffold_has_triggers_and_banned_table(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            db = d / "harvester.db"
            _mk_min_db(db)
            p = scaffold_card(db, "s1", d / "cards", ctype="pitfall")
            md = p.read_text(encoding="utf-8")
        self.assertIn("triggers:", md, "脚手架缺 triggers 字段")
        self.assertIn("<待补", md, "triggers 占位必须显式可抓")
        self.assertIn("## 别再来", md, "脚手架缺「别再来」反面清单段")
        self.assertIn("为什么不通", md, "反面清单列头必须钉「为什么不通」")

    def test_placeholder_triggers_fails_validation(self):
        with tempfile.TemporaryDirectory() as td:
            p = _mk_card(Path(td), "kc-x.md",
                         _good_fm(triggers=["<待补：现象词>"]))
            errs, _w, _fm = validate_card(p)
            self.assertEqual(len(errs), 1,
                             "triggers 占位未补必须是错误级")

    def test_filled_triggers_pass_and_nonlist_warns(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            ok = _mk_card(d, "a.md", _good_fm(
                triggers=["file changed", "old_string 不一致"]))
            errs, _w, _fm = validate_card(ok)
            self.assertEqual(errs, [])
            bad = _mk_card(d, "b.md", _good_fm(id="kc-20261019-0002",
                                               triggers="file changed"))
            errs, warns, _fm = validate_card(bad)
            self.assertEqual(errs, [], "非列表只是警告不是错误")
            self.assertTrue(any("triggers" in w for w in warns),
                            "非列表形态须出警告（照 anchors 先例）")

    def test_legacy_card_without_triggers_untouched(self):
        """旧卡零感知：无 triggers 的最小合规卡必须全绿（不进必填）。"""
        with tempfile.TemporaryDirectory() as td:
            p = _mk_card(Path(td), "old.md", _good_fm())
            errs, warns, _fm = validate_card(p)
            self.assertEqual(errs, [])
            self.assertFalse(any("triggers" in w for w in warns),
                             "没填 triggers 不得警告（可选字段）")


class TestIndexMd(unittest.TestCase):
    """§2：cards index-md 只出清单不裁决。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "cards"
        self.root.mkdir()

    def test_three_sections_and_confusion_pairs(self):
        _mk_card(self.root, "kc-a.md", _good_fm(
            id="kc-a", title="读后写失败", triggers=["file changed",
                                                     "old_string 不一致"]))
        _mk_card(self.root, "kc-b.md", _good_fm(
            id="kc-b", title="同族坑", triggers=["FILE  CHANGED"]))
        _mk_card(self.root, "kc-c.md", _good_fm(
            id="kc-c", title="无触发卡"))
        md = build_index_md(self.root)
        self.assertIn("触发行", md)
        self.assertIn("钩子模板", md)
        self.assertIn("kc-a.md", md)
        # 归一子串口径（大小写+空白折叠）：a/b 两卡疑似易混必须被抓出
        self.assertIn("疑似易混", md)
        self.assertIn("kc-b.md", md)
        # 无 triggers 的卡不缺席，但明说未登记
        self.assertIn("未登记触发词", md)
        # 只列不裁决：不得出现机器定稿语气
        self.assertNotIn("已定稿", md)

    def test_readonly_fingerprint(self):
        p = _mk_card(self.root, "kc-a.md", _good_fm(
            triggers=["file changed"]))
        fp0 = p.read_bytes()
        build_index_md(self.root)
        self.assertEqual(fp0, p.read_bytes(),
                         "index-md 是只读清单，任何卡文件变化都是写事故")

    def test_honest_empty_and_missing_dir(self):
        md = build_index_md(self.root)
        self.assertIn("没有卡", md)
        self.assertIn("cards new", md, "空态必须给下一步命令")
        md2 = build_index_md(self.root.parent / "nope")
        self.assertIn("不存在", md2)


if __name__ == "__main__":
    unittest.main()
