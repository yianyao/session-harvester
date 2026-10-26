# -*- coding: utf-8 -*-
"""v0.83 链自动生成（chain-generate）的单测。

分两层：
- **纯函数层**（围栏剥离/客观字段修补/原句库/失败回喂/后端解析）——不碰库不碰网；
- **编排层**（generate 的重试、判定、落盘、`.pending` 留档）——build/render/chat/
  validate/audit 全用桩替换，测的是"过门才写、不过留痕"这套规矩，不是模型水平。

为什么不测真实模型输出：模型不确定，把它的文本钉进断言等于给测试装定时炸弹。
真实生成质量靠 `chain-validate` + `chain-audit` 两道门在现场把关（那是运行时的事）。
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from harvester import chaingen

try:
    import yaml  # noqa: F401
    _HAS_YAML = True
except ImportError:  # pragma: no cover - H9
    _HAS_YAML = False

_NEED_YAML = unittest.skipUnless(_HAS_YAML, "需要 PyYAML（H9：链校验不降级）")

_GOOD_DOC = """---
topic: "测试主题"
topic_id: tp-fix
members:
  - sid-a
anchors:
  - stage: 阶段一
    nodes:
      - sid: sid-a
        turn: 1
        note: 原话词、判断
generated_from:
  db_mtime: "123"
prompt_version: t3-chainbuild-20261024
---

# 测试主题

正文里引一句「原话片段六个字」。
"""


def _fake_pack() -> dict:
    return {
        "schema": "chain-pack/1",
        "prompt_version": "t3-chainbuild-20261024",
        "topic": {"id": "tp-fix", "name": "测试主题", "keywords": ["测试"]},
        "members": ["sid-a"],
        "db_fingerprint": {"db_mtime": "123", "sessions": 9,
                           "steps": 90, "errors": 1},
        "budget": {"max_chars": 72000, "budget_skipped": 0, "truncated": False},
        "buckets": [{"month": "2026-03", "sessions": 1, "span": ["", ""],
                     "candidates": 1,
                     "nodes": [{"sid": "sid-a", "turn": 1, "turn_max": 3,
                                "title": "测试会话",
                                "quote": "原话片段六个字还要更长一点"}]}],
        "counts": {"sessions": 1, "members": 1, "buckets": 1,
                   "anchor_candidates": 1, "anchor_shown": 1},
    }


class StripFenceTest(unittest.TestCase):
    """模型爱包代码围栏/加客套——剥不干净会让 frontmatter 解析直接失败。"""

    def test_plain_doc_untouched(self):
        self.assertEqual(chaingen._strip_fence("---\na: 1\n---\n正文\n"),
                         "---\na: 1\n---\n正文\n")

    def test_markdown_fence_stripped(self):
        raw = "```markdown\n---\na: 1\n---\n正文\n```"
        self.assertTrue(chaingen._strip_fence(raw).startswith("---\n"))

    def test_leading_chatter_dropped(self):
        raw = "好的，下面是链：\n\n---\na: 1\n---\n正文\n"
        self.assertTrue(chaingen._strip_fence(raw).startswith("---\n"))

    def test_empty_safe(self):
        self.assertEqual(chaingen._strip_fence(""), "")


@_NEED_YAML
class PatchFmTest(unittest.TestCase):
    """客观字段可以补（members/库指纹），创作部分一个字都不许动。"""

    def test_fills_missing_members_and_fingerprint(self):
        doc = "---\ntopic: t\ntopic_id: tp-fix\n---\n正文\n"
        out, patched = chaingen._patch_fm(doc, _fake_pack())
        fm = yaml.safe_load(out.split("\n---\n")[0][4:])
        self.assertEqual(fm["members"], ["sid-a"])
        self.assertEqual(fm["generated_from"]["db_mtime"], "123")
        self.assertTrue(any("members" in p for p in patched))
        self.assertEqual(fm["prompt_version"], "t3-chainbuild-20261024")

    def test_wrong_members_aligned(self):
        doc = ("---\ntopic: t\nmembers:\n  - sid-z\n"
               "generated_from:\n  db_mtime: \"1\"\n---\n正文\n")
        out, patched = chaingen._patch_fm(doc, _fake_pack())
        fm = yaml.safe_load(out.split("\n---\n")[0][4:])
        self.assertEqual(fm["members"], ["sid-a"])
        self.assertTrue(patched)

    def test_anchors_untouched(self):
        """锚点是模型的活儿：修补不许重排/删改它。"""
        out, _ = chaingen._patch_fm(_GOOD_DOC, _fake_pack())
        fm = yaml.safe_load(out.split("\n---\n")[0][4:])
        self.assertEqual(fm["anchors"][0]["nodes"][0]["sid"], "sid-a")
        self.assertEqual(fm["anchors"][0]["nodes"][0]["note"], "原话词、判断")

    def test_complete_doc_no_patch(self):
        _, patched = chaingen._patch_fm(_GOOD_DOC, _fake_pack())
        self.assertEqual(patched, [])

    def test_no_frontmatter_returned_as_is(self):
        doc = "# 只有正文\n"
        out, patched = chaingen._patch_fm(doc, _fake_pack())
        self.assertEqual(out, doc)
        self.assertEqual(patched, [])


class QuoteBankTest(unittest.TestCase):
    """可引用原句库：模型只需复制；没有原句时必须明说「别用「」」。"""

    def test_lists_sids_and_quotes(self):
        bank = chaingen._quote_bank(_fake_pack())
        self.assertIn("[Q01]", bank)
        self.assertIn("sid-a", bank)
        self.assertIn("原话片段六个字", bank)

    def test_short_quote_filtered(self):
        pack = _fake_pack()
        pack["buckets"][0]["nodes"][0]["quote"] = "短短"   # < 6 字，不能当引文
        self.assertIn("没有可用原句", chaingen._quote_bank(pack))

    def test_empty_pack_says_no_quotes(self):
        self.assertIn("没有可用原句", chaingen._quote_bank({}))


class DemoteQuotesTest(unittest.TestCase):
    """未逐字命中的「」改判『』：只改标点、不改字（「」=引文，『』=概括）。"""

    def test_demote_hit(self):
        doc = "从「我这台机器坏了」转向「我要长期干活吧」。"
        out, done = chaingen._demote_quotes(doc, ["我这台机器坏了"])
        self.assertEqual(out, "从『我这台机器坏了』转向「我要长期干活吧」。")
        self.assertEqual(done, ["我这台机器坏了"])

    def test_text_not_altered(self):
        doc = "「某个服务坏了」之后重启。"
        out, _ = chaingen._demote_quotes(doc, ["某个服务坏了"])
        self.assertEqual(out.replace("『", "").replace("』", ""),
                         doc.replace("「", "").replace("」", ""))

    def test_no_miss_noop(self):
        doc = "「原话片段六个字」"
        out, done = chaingen._demote_quotes(doc, [])
        self.assertEqual(out, doc)
        self.assertEqual(done, [])

    def test_miss_not_in_doc_ignored(self):
        doc = "「别的句子」"
        out, done = chaingen._demote_quotes(doc, ["不在这份文档里"])
        self.assertEqual(out, doc)
        self.assertEqual(done, [])


class FeedbackTest(unittest.TestCase):
    """失败回喂必须逐条点名，否则模型只会把全文重写一遍（实测更差）。"""

    def test_lists_quote_misses(self):
        v = {"errors": []}
        a = {"quotes": {"misses": ["把评审做成数据"]},
             "anchors": {"suspicious": []}}
        fb = chaingen._feedback(v, a)
        self.assertIn("把评审做成数据", fb)
        self.assertIn("加粗", fb)          # 教它改用 **强调**
        self.assertIn("只修下面这些", fb)   # 明确禁止整体重写

    def test_lists_structure_errors(self):
        fb = chaingen._feedback({"errors": ["阶段「一」无成员证据"]},
                                {"quotes": {"misses": []},
                                 "anchors": {"suspicious": []}})
        self.assertIn("阶段「一」无成员证据", fb)

    def test_empty_case_still_has_instruction(self):
        fb = chaingen._feedback({}, {})
        self.assertIn("校验器没给出具体条目", fb)


class BackendsTest(unittest.TestCase):
    """后端解析：key 只认环境变量；没有 key 的后端要跳过而不是带着空池进 failover。"""

    def test_empty_when_no_keys(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(chaingen.backends(), [])

    def test_env_override(self):
        cfg = ('[{"base_url":"http://x/v1","model":"m1",'
               '"api_key_env":["T_KEY"]}]')
        with mock.patch.dict("os.environ",
                             {"HARVESTER_LLM_BACKENDS": cfg,
                              "T_KEY": "k1,k2"}, clear=True):
            bes = chaingen.backends()
        self.assertEqual(len(bes), 1)
        self.assertEqual(bes[0]["model"], "m1")
        self.assertEqual(bes[0]["keys"], ["k1", "k2"])

    def test_bad_json_raises(self):
        with mock.patch.dict("os.environ",
                             {"HARVESTER_LLM_BACKENDS": "not-json"}, clear=True):
            with self.assertRaises(chaingen.GenError):
                chaingen.backends()

    def test_chat_without_backend_raises(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(chaingen.GenError):
                chaingen.chat("s", "u")


@_NEED_YAML
class GenerateFlowTest(unittest.TestCase):
    """编排层：过门才写草稿位；不过留 `.pending`（不许冒充草稿）。

    也要 PyYAML：generate 内部要先确认 yaml 可用来解析 frontmatter（H9）。
    无 PyYAML 时按不适用 skip，不当 error——"不可用 ≠ 失败"是同一条纪律。
    """

    def _run(self, ok_flags, tmp: Path, write=True):
        """ok_flags：每次尝试的 (结构过?, 引文过?) 序列。"""
        calls = {"n": 0}

        def fake_chat(system, user, **kw):
            calls["n"] += 1
            i = min(calls["n"] - 1, len(ok_flags) - 1)
            self._flags = ok_flags[i]
            return _GOOD_DOC, {"total_tokens": 10}, "stub@local"

        def fake_validate(path, meta, db):
            ok = self._flags[0]
            return {"ok": ok, "errors": [] if ok else ["结构不合格"],
                    "warnings": [], "stats": {"members": 1, "stages": 1,
                                              "nodes": 1}}
        def fake_audit(path, db):
            ok = self._flags[1]
            return {"ok": ok,
                    "quotes": {"total": 1, "exact": 1 if ok else 0,
                               "partial": 0, "misses": [] if ok else ["假引文"]},
                    "anchors": {"suspicious": []}}

        with mock.patch.object(chaingen, "chat", fake_chat), \
             mock.patch("harvester.chainbuild.build", lambda *a, **k: _fake_pack()), \
             mock.patch("harvester.chainbuild.render", lambda d: "PACK"), \
             mock.patch("harvester.chainaudit.audit_chain", fake_audit), \
             mock.patch("harvester.topicchain.validate_chain", fake_validate):
            return chaingen.generate(tmp / "harvester.db", tmp / "topics_meta.db",
                                     "tp-fix", out=tmp / "exports" / "tp-fix.md",
                                     write=write, max_attempts=len(ok_flags))

    def test_pass_writes_draft(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            r = self._run([(True, True)], tmp)
            self.assertTrue(r["ok"])
            self.assertTrue(r["written"])
            self.assertTrue(Path(r["path"]).is_file())
            self.assertIsNone(r["pending"])

    def test_fail_writes_pending_not_draft(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            r = self._run([(True, False)], tmp)
            self.assertFalse(r["ok"])
            self.assertFalse(r["written"])
            self.assertIsNone(r["path"])
            self.assertIsNotNone(r["pending"])
            pend = Path(r["pending"])
            self.assertTrue(pend.is_file())
            self.assertIn(".pending.md", pend.name)
            self.assertFalse((tmp / "exports" / "tp-fix.md").exists(),
                             "没过门绝不许占草稿位")

    def test_retry_second_pass(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            r = self._run([(True, False), (True, True)], tmp)
            self.assertTrue(r["ok"])
            self.assertEqual(len(r["attempts"]), 2)
            self.assertTrue(r["attempts"][0]["ok"] is False)
            self.assertTrue(r["attempts"][1]["ok"] is True)

    def test_no_write_touches_nothing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            r = self._run([(True, True)], tmp, write=False)
            self.assertTrue(r["ok"])
            self.assertFalse(r["written"])
            self.assertFalse((tmp / "exports").exists())

    def test_structure_fail_blocks(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            r = self._run([(False, True)], tmp)
            self.assertFalse(r["ok"])
            self.assertIn("结构不合格", "；".join(r["blockers"]))

    def test_usage_accumulated_over_attempts(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            r = self._run([(True, False), (True, True)], tmp)
            self.assertEqual(r["usage"]["total_tokens"], 20)


if __name__ == "__main__":   # pragma: no cover
    unittest.main()
