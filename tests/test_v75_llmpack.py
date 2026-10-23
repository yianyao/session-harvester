# -*- coding: utf-8 -*-
"""第④b步：`llm-pack` 的门（结构 + 自洽 + 只读 + 命令可解析）。

口径（对应用户原则「数据在、LLM 可能不足 → 给导出接口」）：
  - 包必须自带 schema 与判定槽，槽与 items **一一对应**（对不上号 = 回填会丢）；
  - 事实字段必须可回溯（锚点/工具名），不是只有计数；
  - 只读：跑一次不能改库（比 mtime + fingerprint）；
  - `readback.command` 必须能过**真解析器**（AGENTS §三 3：发出去的命令要能跑）。
"""
from __future__ import annotations

import shlex
import unittest
from pathlib import Path

from harvester import llmpack as L

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "harvester.db"


class TestPackShape(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not DB.is_file():
            raise unittest.SkipTest("缺真库 harvester.db")
        cls.before = (DB.stat().st_mtime_ns, DB.stat().st_size)
        cls.pack = L.build_pack(DB, faces=("errors", "tools"), limit=5)

    def test_schema_and_faces(self):
        self.assertEqual(self.pack["schema"], "llm-pack/1")
        self.assertEqual(self.pack["faces"], ["errors", "tools"])
        self.assertIn("db_fingerprint", self.pack)
        self.assertTrue(self.pack["hint"])

    def test_slot_covers_every_item(self):
        items = [it["item_id"] for it in self.pack["items"]]
        entries = [e["item_id"] for e in self.pack["slot"]["entries"]]
        self.assertEqual(items, entries, "判定槽必须与 items 同序同号")
        self.assertEqual(self.pack["slot"]["columns"], list(L.SLOT_COLUMNS))

    def test_items_carry_traceable_facts(self):
        self.assertTrue(self.pack["items"], "真库应当有可打包的项")
        for it in self.pack["items"]:
            self.assertIn(it["face"], ("errors", "tools"))
            self.assertTrue(it["subject"], "每条都要有可回溯的 subject")
            self.assertIsInstance(it["facts"], dict)
            self.assertTrue(it["facts"], "facts 不允许为空（只给 id 等于让人猜）")
            self.assertIn("attrib", it)
            if it["attrib"]:
                self.assertTrue(it["attrib"]["candidate"])

    def test_errors_items_sorted_by_count(self):
        counts = [it["facts"]["count"] for it in self.pack["items"]
                  if it["face"] == "errors"]
        self.assertEqual(counts, sorted(counts, reverse=True))

    def test_tools_items_have_tool_rules(self):
        tools = [it for it in self.pack["items"] if it["face"] == "tools"]
        self.assertTrue(tools, "tools 面应当有真实工具")
        hit = [it for it in tools if it["attrib"]]
        self.assertTrue(hit, "tools 面应当至少有一条命中 AR-T 规则")
        for it in hit:
            self.assertTrue(it["attrib"]["rule_id"].startswith("AR-T"))

    def test_pack_validates_clean(self):
        self.assertEqual(L.validate_pack(self.pack), [])

    def test_read_only(self):
        self.assertEqual((DB.stat().st_mtime_ns, DB.stat().st_size), self.before,
                         "llm-pack 读库不许留下写痕迹")

    def test_readback_command_parses(self):
        """回填命令必须是**真能跑**的：把模板里的占位符换成第一条的真实值，
        再过一遍真解析器（AGENTS §三 3：发出去的命令要能跑）。"""
        it = next(i for i in self.pack["items"] if i["attrib"])
        tmpl = self.pack["readback"]["command"]
        filled = (tmpl.replace("<subject>", it["subject"])
                      .replace("<rule_id>", it["attrib"]["rule_id"])
                      .replace("<verdict>", "accepted")
                      .replace("<note>", "样本"))
        self.assertTrue(self.pack["readback"]["one_per_entry"])
        self.assertEqual(self.pack["readback"]["verdicts"], list(L.ATTRIB_VERDICTS))
        cmd = shlex.split(filled)
        self.assertEqual(cmd[:3], ["python", "-m", "harvester"])
        from harvester.cli import build_parser
        ns = build_parser().parse_args(cmd[3:])
        self.assertEqual(ns.cmd, "attrib-review")
        self.assertEqual(ns.pattern, it["subject"])
        self.assertEqual(ns.verdict, "accepted")


class TestPackRejects(unittest.TestCase):
    def test_unknown_face_fails_loud(self):
        with self.assertRaises(ValueError):
            L.build_pack(DB, faces=("chains",))

    def test_non_positive_limit_fails_loud(self):
        with self.assertRaises(ValueError):
            L.build_pack(DB, faces=("errors",), limit=0)


class TestValidateCatches(unittest.TestCase):
    """`--validate` 的每一条判据都要能红（判据不红等于没判据）。"""

    def base(self):
        return {
            "schema": L.SCHEMA, "items": [
                {"item_id": "E-001", "face": "errors", "subject": "P",
                 "facts": {"count": 3}, "attrib": None}],
            "slot": {"columns": list(L.SLOT_COLUMNS), "entries": [
                {"item_id": "E-001", "subject": "P", "verdict": "",
                 "rule_id": "", "class": "", "note": ""}]},
        }

    def test_good_pack_passes(self):
        self.assertEqual(L.validate_pack(self.base()), [])

    def test_wrong_schema(self):
        p = self.base(); p["schema"] = "llm-pack/0"
        self.assertTrue(any("schema" in x for x in L.validate_pack(p)))

    def test_slot_entry_count_mismatch(self):
        p = self.base(); p["slot"]["entries"] = []
        self.assertTrue(any("≠ items" in x for x in L.validate_pack(p)))

    def test_dangling_item_id(self):
        p = self.base(); p["slot"]["entries"][0]["item_id"] = "E-999"
        self.assertTrue(any("不在 items" in x for x in L.validate_pack(p)))

    def test_subject_mismatch(self):
        p = self.base(); p["slot"]["entries"][0]["subject"] = "别的"
        self.assertTrue(any("对不上号" in x for x in L.validate_pack(p)))

    def test_bad_verdict(self):
        p = self.base(); p["slot"]["entries"][0]["verdict"] = "随便写"
        self.assertTrue(any("verdict" in x for x in L.validate_pack(p)))

    def test_duplicate_item_ids(self):
        p = self.base()
        p["items"].append(dict(p["items"][0]))
        p["slot"]["entries"].append(dict(p["slot"]["entries"][0]))
        self.assertTrue(any("重复" in x for x in L.validate_pack(p)))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()