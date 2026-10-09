# -*- coding: utf-8 -*-
"""P1-2 跨建议/跨卡根因去重测试。

SOP-P1-2 口径：
- 去重键 = errstats.normalize_error（唯一权威，禁第二把键）；
- build_suggestion_entries：同根因（pattern 集有交集）的模板条目合并——
  计数按并集相加（每 pattern 计一次，不重复计）、samples 合并去重、
  title 取 calls 最高者（H15 验收：Edit 前置根因全局只出 1 条主建议）；
- triage A 节同理：同根因 pattern 分组渲染；
- cards new 按 normalize_error 比对既有卡，命中警告"疑似已有卡"；
- 证据引用不再截断 100 字符（clean_error_sample 清理工具回显 + 单行，
  全文走锚点/view details）。
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester.agent_suggest import (build_suggestion_entries,
                                     build_suggestions, root_key_assign)
from harvester.cards import scaffold_card
from harvester.errstats import classify_error, clean_error_sample, \
    normalize_error
from harvester.toolstats import render_report, ToolStats


def E(sid: str, seq: int, err: str, resolved: bool = True,
      tool: str = "Edit") -> dict:
    """构造 collect_errors_from_db 形态的单条错误。"""
    return {"sid": sid, "seq": seq, "ts": "2026-10-01 00:00:00",
            "tool": tool, "error": err, "source": "t",
            "class": classify_error(err), "pattern": normalize_error(err),
            "bucket": "中途", "resolved": resolved}


FNF = "File not found: a.md Use an empty old_string to create a new file."
NLR = "cannot write b.md: file no longer exists — re-read the file, then retry"
OSN = "old_string was not found in c.md"


class TestMergeOverlapping(unittest.TestCase):
    """模板条目重叠合并（H15 复现用例）。"""

    def test_shared_pattern_merges_to_one_entry(self):
        # File not found 同 pattern 命中模板 6（Edit 前置）+ 模板 9（旧路径）
        # → 现状出 2 条且各计 x3；合并后全局只出 1 条主建议（H15 验收）
        errors = [E("s1", i + 1, FNF) for i in range(3)]
        built = build_suggestion_entries(errors)
        titles = [e["title"] for e in built["entries"]]
        self.assertEqual(len(built["entries"]), 1,
                         f"同根因应合并为 1 条，实得 {titles}")
        e = built["entries"][0]
        self.assertEqual(e["total"], 3, "并集计数：每 pattern 只计一次")
        self.assertEqual(e["unresolved_count"], 0)
        self.assertEqual(len(e["samples"]), 3)

    def test_union_count_and_title_takes_highest(self):
        # 模板 6 侧（old_string）x2 + 模板 9 侧（no longer exists）x10
        # + 共享 FNF x3 → 合并 1 条 total=15，title 取 calls 最高者（模板 9）
        errors = ([E("s1", i + 1, OSN) for i in range(2)]
                  + [E("s2", i + 1, NLR) for i in range(10)]
                  + [E("s3", i + 1, FNF) for i in range(3)])
        built = build_suggestion_entries(errors)
        self.assertEqual(len(built["entries"]), 1)
        e = built["entries"][0]
        self.assertEqual(e["total"], 15)
        self.assertEqual(e["title"], "引用记忆里的旧路径前先确认目标仍在。")
        # samples 为样例（每 pattern 至多 3 条，pattern_stats 口径）：
        # OSN 2 + NLR 3 + FNF 3 = 8，合并去重后仍 8
        self.assertEqual(len(e["samples"]), 8)

    def test_samples_deduped_across_templates(self):
        # 同一 (sid,seq,raw) 经两个模板收编后只保留一份
        errors = [E("s1", 1, FNF)]
        built = build_suggestion_entries(errors)
        if built["entries"]:
            raws = [(s[0], s[1]) for s in built["entries"][0]["samples"]]
            self.assertEqual(len(raws), len(set(raws)))

    def test_status_follows_merged_title(self):
        errors = ([E("s1", i + 1, OSN) for i in range(2)]
                  + [E("s2", i + 1, FNF) for i in range(3)])
        statuses = {"Edit/Write 前必须先 Read 目标文件最新内容。": "adopted"}
        built = build_suggestion_entries(errors, statuses=statuses)
        for e in built["entries"]:
            self.assertEqual(e["status"],
                             statuses.get(e["title"], "pending"))

    def test_min_count_applies_after_merge(self):
        # 各自 2 次低于 min_count=3，但同根因合并后 4 次达标
        errors = ([E("s1", i + 1, OSN) for i in range(2)]
                  + [E("s2", i + 1, FNF) for i in range(2)])
        built = build_suggestion_entries(errors, min_count=3)
        self.assertEqual(len(built["entries"]), 1)
        self.assertEqual(built["entries"][0]["total"], 4)

    def test_unrelated_patterns_not_merged(self):
        # 无交集的两模板（沙箱 / browser）不合并
        errors = ([E("s1", i + 1, "sandbox-center cmd blocked", tool="Bash")
                   for i in range(3)]
                  + [E("s2", i + 1, "browser_instance_unknown", tool="BWeb")
                     for i in range(3)])
        built = build_suggestion_entries(errors)
        self.assertEqual(len(built["entries"]), 2)


class TestRootKeyAssign(unittest.TestCase):
    """triage A 节分组的连通分量分配。"""

    def test_transitive_grouping(self):
        # A:{6} B:{6,9} C:{9} → 三者同组（连通分量），组 id=min(6,9)=6
        got = root_key_assign([("pa", [6]), ("pb", [6, 9]), ("pc", [9])])
        self.assertEqual(got, {"pa": 6, "pb": 6, "pc": 6})

    def test_unhit_pattern_is_own_group(self):
        # 未命中任何模板 → 组 id=None；渲染层对 None 各自独立成块
        # （不合并），见 TestTriageRenderGroups.test_ungrouped_stay_alone
        got = root_key_assign([("px", []), ("py", [])])
        self.assertIsNone(got["px"])
        self.assertIsNone(got["py"])

    def test_disjoint_components(self):
        got = root_key_assign([("pa", [0]), ("pb", [1])])
        self.assertEqual(got["pa"], 0)
        self.assertEqual(got["pb"], 1)


class TestCleanErrorSample(unittest.TestCase):
    """证据引用清理：去工具回显、单行、可选截断。"""

    def test_strips_tool_echo(self):
        raw = ("Error: String to replace not found in file.\n"
               "String: 七步骨架\nOld: aaa\nNew: bbb")
        got = clean_error_sample(raw)
        self.assertNotIn("七步骨架", got)
        self.assertIn("String to replace not found", got)

    def test_single_line(self):
        got = clean_error_sample("line one\nline two\nline three")
        self.assertNotIn("\n", got)

    def test_no_truncation_by_default(self):
        raw = "x" * 150
        self.assertEqual(len(clean_error_sample(raw)), 150)

    def test_max_len(self):
        self.assertEqual(len(clean_error_sample("x" * 150, max_len=120)),
                         120)

    def test_empty(self):
        self.assertEqual(clean_error_sample(""), "")


class TestSuggestRenderNoTruncation(unittest.TestCase):
    """SOP 第 5 条：raw[:100] 放开——md 渲染不截断证据本体。"""

    def test_long_error_body_kept(self):
        err = "tool_permission_revoked but the reason is " + "y" * 80
        errors = [E(f"s{i}", i + 1, err, tool="Read") for i in range(4)]
        md = build_suggestions(errors, min_count=3)
        # 证据引用行应含完整错误本体（>100 字符不截断）
        self.assertIn("y" * 80, md)

    def test_render_single_line_evidence(self):
        err = "tool_permission_revoked\nsecond line detail"
        errors = [E(f"s{i}", i + 1, err, tool="Read") for i in range(4)]
        md = build_suggestions(errors, min_count=3)
        for ln in md.splitlines():
            self.assertNotIn("second line detail\n", ln + "\n" + "x")
        # 折叠后同行出现
        self.assertTrue(any("second line detail" in ln and
                            "tool_permission_revoked" in ln
                            for ln in md.splitlines()))


class TestToolstatsReportUnchanged(unittest.TestCase):
    """toolstats.render_report 改用共享 helper 后行为不变。"""

    def test_report_still_truncates_sample(self):
        st = ToolStats()
        st.calls, st.success = 3, 0
        st.error, st.given_up = 3, 3
        st.errors = {"Error: String to replace not found in file.\n"
                     "String: 污染内容" * 5: 3}
        md = render_report({"edit": st})
        self.assertIn("String to replace not found", md)
        self.assertNotIn("污染内容污染内容", md)


class TestCardsNewDupeCheck(unittest.TestCase):
    """cards new 按 normalize_error 比对既有卡，命中警告疑似已有卡。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "t.db"
        con = sqlite3.connect(str(self.db))
        con.executescript("""
            CREATE TABLE sessions (sid TEXT PRIMARY KEY, source TEXT,
                session_id TEXT, title TEXT, category TEXT,
                created_at TEXT, updated_at TEXT, file TEXT, model TEXT);
            CREATE TABLE steps (sid TEXT, seq INT, ts TEXT, tool TEXT,
                phase TEXT, status TEXT, error TEXT, detail TEXT);
            INSERT INTO sessions VALUES ('src:s1', 't', 'sid-1', '标题',
                'agent', '2026-10-01', '2026-10-01', 'f', '');
            INSERT INTO steps VALUES ('src:s1', 1, '2026-10-01 00:00:00',
                'Edit', 'result', 'error',
                'File not found: a.md Use an empty old_string to create a new file.', '');
        """)
        con.commit()
        con.close()
        # 既有卡：evidence 含同根因错误原文（真实路径形态）
        (self.root / "kc-20260101-0001-existing.md").write_text(
            "---\nid: kc-20260101-0001-existing\ntitle: 旧卡\ntype: pitfall\n"
            "tags: []\nanchors: []\nevidence: |\n"
            "  File not found: a.md Use an empty old_string to create a new file.\n"
            "confidence: 0.8\ncreated: 2026-01-01\n---\n正文\n",
            encoding="utf-8", newline="\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_dupe_warning_emitted(self):
        w: list[str] = []
        scaffold_card(self.db, "src:s1", self.root, dupe_warnings=w)
        self.assertTrue(w, "pattern 命中既有卡应产生警告")
        self.assertIn("kc-20260101-0001-existing", w[0])

    def test_no_warning_without_match(self):
        # 空卡池 → 无警告
        (self.root / "kc-20260101-0001-existing.md").unlink()
        w: list[str] = []
        scaffold_card(self.db, "src:s1", self.root, dupe_warnings=w)
        self.assertEqual(w, [])

    def test_backward_compatible_default(self):
        # 不传 dupe_warnings 时签名兼容、正常返回路径
        p = scaffold_card(self.db, "src:s1", self.root)
        self.assertTrue(p.exists())


class TestTriageRenderGroups(unittest.TestCase):
    """triage A 节：同根因 pattern 分组渲染。"""

    def _r(self, patterns: list[dict]) -> dict:
        base = {"cutoff": None, "db": "t.db", "n_sessions": 1,
                "error_count_window": sum(p["count"] for p in patterns),
                "old_patterns": [], "skills": [], "hot_sessions": [],
                "cards_scanned": None}
        base["new_patterns"] = patterns
        return base

    def test_grouped_render(self):
        from harvester.triage import render_triage
        pats = [
            {"pattern": "String to replace not found in file.",
             "class": "tool_interface", "tools": ["Edit"], "first_seen":
             "2026-10-01", "count": 38,
             "samples": [("s1", 1, "raw")], "known_card": False,
             "templates": [6]},
            {"pattern": "cannot modify x: file has not been read",
             "class": "tool_interface", "tools": ["Edit"], "first_seen":
             "2026-10-02", "count": 30,
             "samples": [("s2", 2, "raw")], "known_card": False,
             "templates": [6]},
            {"pattern": "host_bridge_declared_error", "class": "env",
             "tools": ["B"], "first_seen": "2026-10-03", "count": 5,
             "samples": [("s3", 3, "raw")], "known_card": False,
             "templates": []},
        ]
        md = render_triage(self._r(pats))
        self.assertIn("同根因", md)
        self.assertIn("Edit/Write 前必须先 Read", md)
        # 各 pattern 明细行保留
        self.assertIn("x38", md)
        self.assertIn("x30", md)
        self.assertIn("host_bridge_declared_error", md)

    def test_ungrouped_patterns_render_alone(self):
        from harvester.triage import render_triage
        pats = [{"pattern": "host_bridge_declared_error", "class": "env",
                 "tools": ["B"], "first_seen": "2026-10-03", "count": 5,
                 "samples": [("s3", 3, "raw")], "known_card": False,
                 "templates": []}]
        md = render_triage(self._r(pats))
        self.assertNotIn("同根因", md)
        self.assertIn("host_bridge_declared_error", md)

    def test_multiple_unhit_patterns_stay_alone(self):
        # 多个未命中模板的 pattern 各自独立成块（None 组不合并渲染）
        from harvester.triage import render_triage
        mk = lambda pat, n: {"pattern": pat, "class": "env", "tools": ["B"],
                             "first_seen": "2026-10-03", "count": n,
                             "samples": [("s", 1, "raw")],
                             "known_card": False, "templates": []}
        md = render_triage(self._r([mk("alpha_err", 5), mk("beta_err", 4)]))
        self.assertNotIn("同根因", md)
        self.assertIn("alpha_err", md)
        self.assertIn("beta_err", md)


if __name__ == "__main__":
    unittest.main()
