# -*- coding: utf-8 -*-
"""P1-4 export-analysis 统一导出器测试。

SOP-P1-4：五类 kind；md/JSON 同源同口径；去重唯一键 = normalize_error；
sessions 走 patterns_dedup（view v2.3 口径下沉）；json 统一 machineWrap 头。
"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from harvester.export_analysis import build_analysis, to_json, KINDS


DDL = """
CREATE TABLE sessions (sid TEXT PRIMARY KEY, source TEXT, session_id TEXT,
    title TEXT, category TEXT, created_at TEXT, updated_at TEXT,
    file TEXT, model TEXT);
CREATE TABLE messages (sid TEXT, role TEXT, ts TEXT, text TEXT, raw TEXT);
CREATE TABLE steps (sid TEXT, seq INT, ts TEXT, tool TEXT, phase TEXT,
    status TEXT, error TEXT, detail TEXT);
"""

ERR_A = ("cannot edit C:/w/a.md: file changed since it was read"
         " — re-read the file, then retry")
ERR_B = ("cannot edit C:/w/b.md: file changed since it was read"
         " — re-read the file, then retry")


def make_db(tmp: str, *, with_raw: bool = True) -> Path:
    db = Path(tmp) / "t.db"
    con = sqlite3.connect(str(db))
    con.executescript(DDL)
    con.execute("INSERT INTO sessions VALUES ('src:s1','t','id-1','会话一',"
                "'agent','2026-10-01','2026-10-01','f','model-x')")
    con.execute("INSERT INTO sessions VALUES ('src:s2','t','id-2','会话二',"
                "'agent','2026-10-02','2026-10-02','f','')")
    # messages：raw=原文，text=bigram 干扰（H3/H38：统计与导出走 raw）
    con.execute("INSERT INTO messages VALUES ('src:s1','user','2026-10-01 00:00:00',"
                "'改 文件 文件 之 前', '请先读取文件再编辑')")
    con.execute("INSERT INTO messages VALUES ('src:s1','assistant',"
                "'2026-10-01 00:01:00','已 处理 完成','好的，已处理完成')")
    for sid, seq, err in (("src:s1", 1, ERR_A), ("src:s1", 2, ERR_B),
                          ("src:s2", 1, ERR_A)):
        con.execute("INSERT INTO steps VALUES (?,?,?,?,?,?,?,?)",
                    (sid, seq, "2026-10-01 00:00:0" + str(seq), "edit",
                     "call", None, None, "args"))
        con.execute("INSERT INTO steps VALUES (?,?,?,?,?,?,?,?)",
                    (sid, seq + 100, "2026-10-01 00:00:0" + str(seq), "edit",
                     "result", "error", err, ""))
    con.commit()
    con.close()
    return db


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = make_db(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()


class TestMachineWrapHead(Base):
    """json 出口统一 machineWrap 头。"""

    def test_head_fields(self):
        obj, md = build_analysis(self.db, "errors")
        for key in ("kind", "generated_at", "db_fingerprint", "dedup",
                    "hint", "data"):
            self.assertIn(key, obj)
        self.assertEqual(obj["kind"], "analysis-errors")
        self.assertEqual(obj["dedup"], "root")
        self.assertTrue(obj["hint"])

    def test_all_kinds_supported(self):
        for kind in KINDS:
            obj, md = build_analysis(self.db, kind)
            self.assertEqual(obj["kind"], f"analysis-{kind}")
            self.assertTrue(md)

    def test_invalid_kind(self):
        with self.assertRaises(ValueError):
            build_analysis(self.db, "nope")

    def test_json_serializable(self):
        obj, _ = build_analysis(self.db, "sessions")
        # 元组等非 JSON 类型不得漏出（samples/occurrences 均已转对象）
        json.loads(to_json(obj))


class TestErrorsKind(Base):
    def test_pattern_dedup_by_normalize_error(self):
        # a.md/b.md 差异被占位符化 → 同一归一键，count=3（跨会话）
        obj, md = build_analysis(self.db, "errors")
        pats = {p["pattern"]: p for p in obj["data"]["patterns"]}
        target = [k for k in pats if "file changed" in k]
        self.assertEqual(len(target), 1, "两差异错误应归同一 pattern 键")
        self.assertEqual(pats[target[0]]["count"], 3)
        self.assertEqual(pats[target[0]]["class"], "tool_interface")
        self.assertIn("错误三分类报告", md)

    def test_samples_have_anchors(self):
        obj, _ = build_analysis(self.db, "errors")
        p0 = obj["data"]["patterns"][0]
        self.assertTrue(all("sid" in s and "seq" in s
                            for s in p0["samples"]))


class TestToolsKind(Base):
    def test_tool_rows_from_toolstats(self):
        obj, md = build_analysis(self.db, "tools")
        rows = {r["tool"]: r for r in obj["data"]["tools"]}
        self.assertIn("edit", rows)
        r = rows["edit"]
        self.assertEqual(r["calls"], 3)
        self.assertEqual(r["error"], 3)
        # roots：同根因一条（normalize_error 归一键）
        roots = [g for g in r["roots"] if "file changed" in g["pattern"]]
        self.assertEqual(len(roots), 1)
        self.assertEqual(roots[0]["count"], 3)
        self.assertIn("工具调用统计", md)


class TestSkillsKind(Base):
    def test_summary_shape(self):
        # fixture 无 skill 调用 → 空汇总但不报错
        obj, md = build_analysis(self.db, "skills")
        self.assertEqual(obj["data"]["n_invocations"], 0)
        self.assertEqual(obj["data"]["skills"], [])
        self.assertTrue(md)


class TestSessionsKind(Base):
    def test_patterns_dedup_cross_session(self):
        obj, _ = build_analysis(self.db, "sessions")
        data = obj["data"]
        self.assertEqual(data["n_sessions"], 2)
        by = {g["pattern"]: g for g in data["patterns_dedup"]}
        target = [k for k in by if "file changed" in k]
        self.assertEqual(len(target), 1)
        self.assertEqual(by[target[0]]["count"], 3)
        self.assertEqual(len(by[target[0]]["occurrences"]), 3)
        self.assertEqual({o["sid"] for o in by[target[0]]["occurrences"]},
                         {"src:s1", "src:s2"})

    def test_md_contains_full_turns_from_raw(self):
        obj, md = build_analysis(self.db, "sessions")
        # 全文走 raw（H3 契约）：原文出现，bigram 干扰不出现
        self.assertIn("请先读取文件再编辑", md)
        self.assertNotIn("改 文件 文件 之 前", md)
        self.assertIn("# 会话一", md)
        self.assertIn("## 回合 #1", md)
        self.assertIn("## 错误步骤清单", md)

    def test_default_targets_are_error_sessions(self):
        # 无 sids → 缺省取全部含错误步骤的会话（fixture 两个都是）
        obj, _ = build_analysis(self.db, "sessions")
        self.assertEqual(obj["data"]["n_sessions"], 2)

    def test_explicit_sids(self):
        obj, _ = build_analysis(self.db, "sessions", sids=["src:s1"])
        self.assertEqual(obj["data"]["n_sessions"], 1)

    def test_missing_sid_raises(self):
        with self.assertRaises(KeyError):
            build_analysis(self.db, "sessions", sids=["src:none"])

    def test_sids_upper_limit(self):
        with self.assertRaises(ValueError):
            build_analysis(self.db, "sessions", sids=["x"] * 201)


class TestTriageKind(Base):
    def test_structured_queue(self):
        obj, md = build_analysis(self.db, "triage")
        data = obj["data"]
        for key in ("new_patterns", "old_patterns", "skills", "hot_sessions"):
            self.assertIn(key, data)
        # samples 已转对象（JSON 可序列化）
        self.assertTrue(all(isinstance(s, dict)
                            for p in data["new_patterns"]
                            for s in p["samples"]))
        self.assertIn("蒸馏队列", md)


if __name__ == "__main__":
    unittest.main()
