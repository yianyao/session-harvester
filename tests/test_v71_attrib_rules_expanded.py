# -*- coding: utf-8 -*-
"""P6-1 增量门：规则表扩容（7 → 24 条）后**语义没走样**。

为什么单独一个文件：规则是**数据**，扩容最容易出的两种错都不会让程序崩——
① 新规则把老规则的串劫持走（"归错类"）；② 规则看着在、其实一条也没命中（死规则）。
两者都只能靠"拿真实错误串逐个对答案"发现，靠四要素/正则合法性检查抓不到。

本文件只用**真实语料里见过的字面**（来源 `attrib --json` 实测，
清单留档 docs/reports/unattributed-p69.txt），不跑真库、不依赖网络。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO))

from harvester.attrib import (  # noqa: E402
    DEFAULT_RULES, _compile, attribute, load_rules)

# (真实错误串, 期望规则) —— 每条都来自实测语料；顺序即优先级，改序会红。
CORPUS: list[tuple[str, str]] = [
    # AR-001..007：原有七条（回归用，防扩容把它们挤掉）
    ("Error: FS_NOT_OBSERVED", "AR-001"),
    ("Error: cannot edit: file has not been read yet", "AR-001"),
    ("Error: Edit error on /tmp/a.md: String to replace not found", "AR-002"),
    ("Error: file changed since it was read", "AR-003"),
    ("filesystem_not_found", "AR-004"),
    ("Error: File not found: C:\\x.md", "AR-004"),
    ("WEB_PROVIDER_ERROR", "AR-005"),
    ("Error: subagent depth 2 exceeds maxDepth 1", "AR-006"),
    ("tool_permission_revoked", "AR-007"),
    # AR-008..024：本轮新增（按次数从高到低）
    ("Error: sandbox-center cmd decisionRecord missing actual resource subject",
     "AR-008"),
    ("Error: SetNamedSecurityInfoW failed (Win32 5): grantWrite(D:\\Data\\x)",
     "AR-008"),
    ("FS_SANDBOX_DENIED", "AR-008"),
    ("Error: ReplaceFileW EIO (Win32 1175): D:\\Data\\x\\tmp.txt", "AR-008"),
    ("Error updating task: EPERM: operation not permitted, rename 'a' -> 'b'",
     "AR-009"),
    ("Error: Write error: EPERM: operation not permitted, mkdir 'D:\\'", "AR-009"),
    ("Error: Permission to use Glob has been denied.", "AR-010"),
    ("Error: Permission to use Bash with command export PATH=\"/usr/bin:$PATH\"",
     "AR-010"),
    ("FS_AMBIGUOUS_EDIT", "AR-011"),
    ("Error: Found 2 matches of the string to replace, but replace_all is "
     "false. To replace all occurrences, set replace_all to true.", "AR-011"),
    ("Error: result (105,811 characters) exceeds maximum allowed tokens. "
     "Output has been saved to C:\\x\\y.txt", "AR-012"),
    ("Error: Error during web fetch for https://a/x: fetch failed", "AR-013"),
    ("Error: Error during web fetch for https://a/x: URL redirected to "
     "different host: https://b/y", "AR-013"),
    ("tool_input_invalid", "AR-014"),
    ("Error: Invalid arguments for Edit: missing required parameter(s): "
     "new_string. Retry the tool call with all required parameters.", "AR-014"),
    ("run_checklist_multiple_in_progress: 3", "AR-014"),
    ("tool_result_recall_invalid_artifact", "AR-014"),
    ("tool_result_recall_invalid_limit", "AR-014"),
    ("browser_timeout_invalid", "AR-014"),
    ("browser_wait_invalid", "AR-014"),
    ("SEARCH_INVALID_PATTERN", "AR-014"),
    ('Error: Can not find skill: "MarkItDown". Also checked for slash command '
     '"/MarkItDown" but not found.', "AR-015"),
    ("skill_asset_invalid", "AR-015"),
    ("skill_asset_not_executable", "AR-015"),
    ("UNKNOWN_TOOL", "AR-015"),
    ('Error: No installed connector named "lighthouse-ops".', "AR-015"),
    ("Error: unknown job 8696fd00-d460-4255-9e90-348267908150", "AR-015"),
    ("TOOL_TIMEOUT", "AR-016"),
    ("Error: glob error: Error during glob search operation: Search timeout "
     "after 30000ms", "AR-016"),
    ("Error: grep error: Error during grep search operation: Search timeout "
     "after 30000ms", "AR-016"),
    ("browser_instance_unknown", "AR-017"),
    ("browser_cancelled", "AR-017"),
    ("artifact_secret_detected", "AR-018"),
    ("SEARCH_FAILED", "AR-019"),
    ("memory_tool_cursor_run_mismatch", "AR-020"),
    ("host_bridge_declared_error", "AR-021"),
    ("Error: socket hang up (requestId: 0dd5f302056c41bba164a8ed4e6ccc8b)",
     "AR-021"),
    ("plan_changes_requested: 补充：从第三点延伸出来的……", "AR-022"),
    ("FS_NOT_TEXT", "AR-023"),
    ("filesystem_offset_out_of_range", "AR-023"),
    ("browser_element_obscured", "AR-024"),
]

# 刻意**不**立规则的两条（看不到具体根因就不编）——钉住"别顺手补规则"
DELIBERATELY_OPEN = ["tool_executor_failed", "ABORTED"]


class TestCorpusMapping(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = load_rules()
        cls.compiled = _compile(cls.rules)

    def test_every_real_string_lands_on_its_rule(self):
        for text, want in CORPUS:
            hit = attribute(text, self.compiled)
            self.assertIsNotNone(hit, f"未命中任何规则: {text[:60]}")
            self.assertEqual(hit["rule_id"], want,
                             f"{text[:60]} → {hit['rule_id']}，期望 {want}")

    def test_hits_stay_candidates_and_carry_why_fix(self):
        for text, _ in CORPUS:
            hit = attribute(text, self.compiled)
            self.assertTrue(hit["candidate"], f"{text[:40]} 未标 candidate")
            self.assertTrue(hit["why"] and hit["fix"], text[:40])
            self.assertIn(hit["confidence"], ("high", "medium", "low"))

    def test_no_rule_is_dead_on_the_corpus(self):
        """**errors 面**的每条规则都必须在这批真实串里至少命中一次。

        死规则最坏：它让"规则表 24 条"看起来覆盖很广，实际没接任何真实形态。
        （第④步起规则表分面：tools/chains/skills 的锚点不是错误串，各自在
        `tests/test_v74_rule_faces.py` 里用真库工具名/真实文案核对，别在这里
        拿错误语料判它们的死活。）
        """
        used = {attribute(t, self.compiled)["rule_id"] for t, _ in CORPUS}
        all_ids = {r["id"] for r in self.rules if r["face"] == "errors"}
        self.assertEqual(all_ids - used, set(), "这些规则在语料里一次也没命中")

    def test_deliberately_open_ones_stay_open(self):
        """这两条**不许**被顺手补规则：报错本身没给根因，编一个就是假归因。"""
        for text in DELIBERATELY_OPEN:
            self.assertIsNone(attribute(text, self.compiled),
                              f"{text} 被某条规则归因了——先确认根因真的已知")


class TestOrderingTraps(unittest.TestCase):
    """扩容最容易踩的两个劫持陷阱（第一版就是这么错的：匹配词太宽）。"""

    @classmethod
    def setUpClass(cls):
        cls.compiled = _compile(load_rules())

    def test_empty_old_string_hint_is_missing_file_not_edit_miss(self):
        hit = attribute('File not found: /a.md. Use an empty old_string to '
                        'create a new file.', self.compiled)
        self.assertEqual(hit["rule_id"], "AR-004",
                         "`old_string` 又被当成编辑未命中了（AR-002 的坑）")

    def test_browser_timeout_invalid_is_not_a_timeout(self):
        """`browser_timeout_invalid` 是**参数非法**；裸 `timeout` 会把它劫走。"""
        self.assertEqual(attribute("browser_timeout_invalid",
                                   self.compiled)["rule_id"], "AR-014")
        self.assertEqual(attribute("TOOL_TIMEOUT", self.compiled)["rule_id"],
                         "AR-016")

    def test_ambiguous_edit_is_not_plain_edit_miss(self):
        """多处命中（AR-011）的处置与"没找到"（AR-002）不同，不能合并。"""
        self.assertEqual(attribute("FS_AMBIGUOUS_EDIT", self.compiled)["rule_id"],
                         "AR-011")


class TestTableShape(unittest.TestCase):
    def test_ids_are_contiguous(self):
        """**errors 面**连续编号（顺序即优先级）；另三面用 `AR-T/C/S` 前缀单独编。

        第④步扩面后一张表里有四个面：混在一起编号会让人误以为顺序即全局优先级，
        而面之间根本不互相匹配（`_compile(face=...)` 先过滤）。
        """
        ids = [r["id"] for r in load_rules() if r["face"] == "errors"]
        self.assertEqual(ids, ["AR-%03d" % i for i in range(1, len(ids) + 1)],
                         "errors 面规则 id 必须连续编号")

    def test_other_faces_use_their_own_prefix(self):
        prefix = {"tools": "AR-T", "chains": "AR-C", "skills": "AR-S"}
        for face, pre in prefix.items():
            ids = [r["id"] for r in load_rules() if r["face"] == face]
            self.assertTrue(ids, f"{face} 面一条规则都没有")
            for rid in ids:
                self.assertTrue(rid.startswith(pre),
                                f"{face} 面的 {rid} 没用 {pre} 前缀")

    def test_verified_date_shape(self):
        for r in load_rules():
            self.assertRegex(r.get("verified") or "", r"^\d{4}-\d{2}-\d{2}$",
                             f"{r['id']} 的 verified 不合格式（人核过才写）")

    def test_file_is_packaged(self):
        """规则表随包分发：路径必须在 harvester/data/ 下（docs/ 会被 gitignore）。"""
        self.assertEqual(DEFAULT_RULES.parent.name, "data")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()