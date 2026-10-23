# -*- coding: utf-8 -*-
"""第④步：归因规则表加 `face`（errors/tools/chains/skills）+ 首批扩面规则。

门的职责（对应 v71 的两条坏味：**死规则**与**劫持**）：
  1. 24 条历史规则全部落在 errors 面（缺省 face 不许漂）；
  2. 每个新面至少一条规则，且**至少有一条能在真实数据上命中**（死规则会红）；
  3. 面之间不串味：tools 规则不匹配错误串（`^edit$` 不该抢 FS_* 的活）；
  4. 未知 face / 非法 face 加载即失败（静默不生效最坏）；
  5. `attribute_face` 的返回仍是候选（`candidate=True`）。
"""
from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

from harvester import attrib as A
from harvester import toolstats

ROOT = Path(__file__).resolve().parent.parent
UP = ROOT


@unittest.skipUnless(A._HAS_YAML, "缺 PyYAML（scripts/gate_no_yaml.py 会单独判）")
class RuleFaces(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = A.load_rules()
        cls.by_face = {}
        for r in cls.rules:
            cls.by_face.setdefault(r["face"], []).append(r)

    def test_legacy_rules_stay_on_errors_face(self):
        """AR-001..024 是历史规则：缺省 face 必须是 errors，不许被顺手改成别的。"""
        legacy = [r for r in self.rules if r["face"] == "errors"]
        self.assertGreaterEqual(len(legacy), 24)
        self.assertTrue(all(r["id"].startswith("AR-0") for r in legacy))

    def test_every_face_has_a_rule(self):
        for face in A.FACES:
            self.assertIn(face, self.by_face, "面 %s 一条规则都没有" % face)

    def test_tools_rules_match_real_tool_names(self):
        """tools 面的 match 打在工具名上：先用真库的工具清单核对，
        一条都命不中就是死规则（AGENTS §三 21：死规则是扩容的典型坏味）。"""
        db = UP / "harvester.db"
        if not db.is_file():
            self.skipTest("缺真库 harvester.db")
        stats, _ = toolstats.collect_stats_from_db(db)
        names = list(stats.keys())
        compiled = A._compile(self.rules, face="tools")
        hit_ids = set()
        for name in names:
            hit = A.attribute(name, compiled)
            if hit:
                hit_ids.add(hit["rule_id"])
        self.assertTrue(hit_ids, "tools 面规则一条都没命中真库工具名（全是死规则）")
        # 空转检查：编译出的每条规则都要在某个真实工具名上命中
        dead = [r["id"] for r, _ in compiled if r["id"] not in hit_ids]
        self.assertEqual(dead, [], "tools 面有死规则：%s" % dead)

    def test_tools_rules_do_not_hijack_error_text(self):
        """`^edit$` 这类规则必须带定界；否则它会去抢错误串里的 edit。"""
        compiled_tools = A._compile(self.rules, face="tools")
        for text in ("FS_NOT_OBSERVED: file has not been read yet",
                     "String to replace not found in file.",
                     "WEB_PROVIDER_ERROR upstream 502"):
            self.assertIsNone(A.attribute(text, compiled_tools),
                              "tools 规则劫持了错误串：%r" % text)

    def test_errors_face_still_ignores_face_rules(self):
        """反向也要成立：errors 面编译结果里不含 tools/chains/skills 规则。"""
        err_ids = {r["id"] for r, _ in A._compile(self.rules)}
        self.assertFalse([i for i in err_ids if "-T" in i or "-C" in i or "-S" in i])

    def test_chains_and_skills_faces_hit_real_strings(self):
        """chains/skills 的锚点是**真实文案**（cards.validate_cards / 分诊状态词）。
        这里直接用真实串打一遍，命不中说明规则与文案脱节。"""
        chains = A._compile(self.rules, face="chains")
        self.assertIsNotNone(
            A.attribute("frontmatter 缺失或不可解析（§8 规范要求 YAML 头）", chains))
        self.assertIsNotNone(A.attribute("锚点 session_id=x 不在索引库", chains))
        skills = A._compile(self.rules, face="skills")
        self.assertIsNotNone(A.attribute("待鉴定", skills))
        self.assertIsNotNone(A.attribute("low-sample", skills))

    def test_unknown_face_fails_loud(self):
        tmp = ROOT / "tests" / "_tmp_face.yaml"
        tmp.parent.mkdir(exist_ok=True)
        try:
            tmp.write_text(
                "version: 1\nrules:\n  - id: X-1\n    face: cards\n"
                "    class: env\n    match: ['x']\n    why: y\n    fix: z\n",
                encoding="utf-8")
            with self.assertRaises(A.RuleError) as cm:
                A.load_rules(tmp)
            self.assertIn("face", str(cm.exception))
        finally:
            tmp.unlink(missing_ok=True)

    def test_attribute_face_is_candidate(self):
        hit = A.attribute_face("Edit", self.rules, face="tools")
        self.assertIsNotNone(hit)
        self.assertTrue(hit["candidate"])
        self.assertEqual(hit["rule_id"], "AR-T01")
        self.assertIsNone(A.attribute_face("NoSuchTool", self.rules, face="tools"))

    def test_face_defaults_to_errors(self):
        hit = A.attribute_face("FS_NOT_OBSERVED", self.rules)  # 缺省面
        self.assertIsNotNone(hit)
        self.assertEqual(hit["class"], "tool_interface")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()