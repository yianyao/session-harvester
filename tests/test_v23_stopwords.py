# -*- coding: utf-8 -*-
"""v0.23 #1：停用词表（通用中文虚词）与默认启用。

用户 2026-10-09 裁决：「接受停用词表」。**红线**：通用性——表内不得写死
任何具体作品的人名/地名/主题词（工具须面向任意主题×任意 skill，
PLAN-v0.22 §0.2）。人名由使用者私有表叠加。

背景：doc_freq 口径修正后（H48），真实库前排由虚词占据
（一个/自己/怎么/没有/出来…），关键词表因此缺少决策价值。

本测试断言：
  S-1 随包默认表存在且被加载（非空）；
  S-2 默认启用：虚词从结果中消失；
  S-3 `--no-stopwords` 关闭后虚词回来（且与旧口径一致）；
  S-4 用户私有表可叠加（默认表仍生效）；
  S-5 **红线护栏**：默认表不含长度 >= 3 的 CJK 词条；
  S-6 默认表不含任何具体作品人名（以真实库高频人名做黑名单断言）。
"""

from __future__ import annotations

import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester.cli import _kw_stopword_paths
from harvester.kwstats import (DEFAULT_STOPWORDS, build_stats,
                               load_stopwords, load_stopwords_multi)

CJK = re.compile(r"[\u4e00-\u9fff]")

#: 真实库 doc_freq 前排的人名/专名——**绝不能**出现在随包默认表里。
#: 这些必须由使用者私有表提供（红线：不写死主题/作品）。
FORBIDDEN_PROPER_NOUNS = {"丁樾", "南星", "江之南", "郭南星", "归墟"}

#: 允许出现在随包表里的 **>=3 字汉语虚词** 白名单。
#: 三字以上基本都是专名，故默认禁止；此处穷举放行的只有真正的虚词，
#: 新增放行必须同时在 tests/test_v23_stopwords.py 与本表登记（评审可见）。
ALLOWED_LONG_FUNCTION_WORDS = {"为什么"}


def make_db(path: Path, rows: list[tuple]) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE IF NOT EXISTS sessions("
        " sid TEXT PRIMARY KEY, source TEXT, sid2 TEXT, title TEXT,"
        " category TEXT, created_at TEXT, updated_at TEXT, file TEXT);"
        "CREATE TABLE IF NOT EXISTS messages("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, sid TEXT, role TEXT,"
        " ts TEXT, text TEXT, raw TEXT);")
    for sid, role, ts, text, raw in rows:
        con.execute("INSERT OR IGNORE INTO sessions(sid) VALUES (?)", (sid,))
        con.execute("INSERT INTO messages(sid, role, ts, text, raw)"
                    " VALUES (?,?,?,?,?)", (sid, role, ts, text, raw))
    con.commit()
    con.close()


class TestStopwordFile(unittest.TestCase):

    def test_default_file_exists_and_loads(self):
        self.assertTrue(DEFAULT_STOPWORDS.is_file(),
                        f"随包默认表缺失: {DEFAULT_STOPWORDS}")
        words = load_stopwords(DEFAULT_STOPWORDS)
        self.assertGreater(len(words), 50, "默认表过小，疑未加载完整")
        # 抽样断言若干通用虚词在表内
        for w in ("一个", "自己", "怎么", "没有", "出来", "不是", "什么"):
            self.assertIn(w, words, f"通用虚词 {w!r} 不在默认表内")

    def test_redline_no_long_cjk_terms(self):
        """红线护栏：默认表不得含长度 >= 3 的 CJK 词条。

        三字以上几乎不可能是汉语虚词；此断言防止后人往随包表里
        添加作品人名/主题词（那会破坏工具的通用性）。
        仅 `ALLOWED_LONG_FUNCTION_WORDS` 中穷举登记的真·虚词可放行。
        私有表不受此限（用户自负责），故只校验随包表。
        """
        long_cjk = [w for w in load_stopwords(DEFAULT_STOPWORDS)
                    if len(CJK.findall(w)) >= 3 and len(w) == len(CJK.findall(w))]
        bad = [w for w in long_cjk if w not in ALLOWED_LONG_FUNCTION_WORDS]
        self.assertEqual(
            bad, [],
            f"随包停用词表含 >=3 字 CJK 词条（疑人名/主题词）: {bad}；"
            f"确为虚词请登记进 ALLOWED_LONG_FUNCTION_WORDS")
        # 放行名单本身要小且可解释（防"白名单无限膨胀"绕过护栏）
        self.assertLessEqual(len(ALLOWED_LONG_FUNCTION_WORDS), 5,
                             "放行名单过大，护栏已失效——应改用私有停用词表")

    def test_redline_no_proper_nouns_from_real_corpus(self):
        """真实库高频人名不得进随包表（严格红线）。"""
        words = load_stopwords(DEFAULT_STOPWORDS)
        hit = sorted(FORBIDDEN_PROPER_NOUNS & words)
        self.assertEqual(hit, [],
                         f"随包表含具体作品人名（违反通用性红线）: {hit}")

    def test_multi_table_union(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "mine.txt"
            p.write_text("# 私有\n私有人名\n", encoding="utf-8", newline="\n")
            both = load_stopwords_multi([DEFAULT_STOPWORDS, p])
            self.assertIn("私有人名", both)
            self.assertIn("一个", both, "默认表须与私有表叠加生效")


class TestDefaultEnabled(unittest.TestCase):

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.db = self.dir / "t.db"
        self.meta = self.dir / "kw.db"
        make_db(self.db, [
            ("s1", "user", "t", "-", "一个自己怎么没有出来但不是"),
            ("s2", "user", "t", "-", "叙事节奏"),
        ])

    def _grams(self, **kw):
        s = build_stats(self.db, self.meta, ns=[2], role="user", **kw)
        return {r["gram"] for r in s["rows"]}

    def test_default_table_removes_function_words(self):
        got = self._grams(stopwords_paths=[DEFAULT_STOPWORDS])
        for w in ("一个", "自己", "怎么", "没有", "出来", "不是"):
            self.assertNotIn(w, got, f"默认表未过滤 {w!r}")
        # 非虚词必须保留
        self.assertIn("叙事", got)
        self.assertIn("节奏", got)

    def test_no_stopwords_keeps_function_words(self):
        got = self._grams()          # 不传 stopwords_paths
        self.assertIn("一个", got)
        self.assertIn("自己", got)

    def test_private_table_stacks_on_default(self):
        p = self.dir / "mine.txt"
        p.write_text("节奏\n", encoding="utf-8", newline="\n")
        got = self._grams(stopwords_paths=[DEFAULT_STOPWORDS, p])
        self.assertNotIn("节奏", got, "私有表未生效")
        self.assertNotIn("一个", got, "默认表未叠加")
        self.assertIn("叙事", got)


class TestCliWiring(unittest.TestCase):

    class _A:
        def __init__(self, stopwords=None, no_stopwords=False):
            self.stopwords = stopwords
            self.no_stopwords = no_stopwords

    def test_default_enabled(self):
        paths = _kw_stopword_paths(self._A())
        self.assertEqual(paths, [DEFAULT_STOPWORDS])

    def test_no_stopwords_disables_even_default(self):
        self.assertEqual(_kw_stopword_paths(self._A(no_stopwords=True)), [])

    def test_user_tables_stack_after_default(self):
        paths = _kw_stopword_paths(self._A(stopwords=["a.txt", "b.txt"]))
        self.assertEqual(paths[0], DEFAULT_STOPWORDS)
        self.assertEqual([p.name for p in paths[1:]], ["a.txt", "b.txt"])

    def test_unknown_path_does_not_crash(self):
        """不存在的私有表：静默跳过（不崩、默认表仍生效）。"""
        paths = _kw_stopword_paths(self._A(stopwords=["no_such_file.txt"]))
        words = load_stopwords_multi(paths)
        self.assertIn("一个", words)


if __name__ == "__main__":
    unittest.main()
