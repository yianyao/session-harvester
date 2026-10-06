# -*- coding: utf-8 -*-
"""v0.8: yuanbao_raw adapter 测试（schema 来自 2026-10-06 真机采集核验）。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from harvester.adapters.yuanbao_raw import (
    YuanbaoRawAdapter,
    _conv_blocks,
    _parse_detail,
)


def _speech(*blocks):
    return [{"speechType": "text", "content": list(blocks)}]


def _txt(msg):
    return {"type": "text", "msg": msg}


class TestConvBlocks(unittest.TestCase):
    def test_human_text(self):
        conv = {"id": "c1", "speaker": "human", "createTime": 1791185414,
                "speechesV2": _speech(_txt("你好"))}
        msgs, warns = _conv_blocks(conv)
        self.assertEqual([m.role for m in msgs], ["user"])
        self.assertEqual(msgs[0].text, "你好")
        self.assertIsNotNone(msgs[0].timestamp)

    def test_ai_text_plus_search_guid(self):
        conv = {"id": "c2", "speaker": "ai", "createTime": 1791185415,
                "speechesV2": [{"speechType": "search_with_text", "content": [
                    {"type": "searchGuid", "title": "引用 2 篇资料作为参考",
                     "docs": [{"index": 1, "docId": "d1", "title": "文档一"},
                              {"index": 2, "docId": "d2", "title": "文档二"}]},
                    _txt("回答正文"),
                ]}]}
        msgs, warns = _conv_blocks(conv)
        self.assertEqual([m.role for m in msgs], ["note", "assistant"])
        self.assertIn("[search] 引用 2 篇资料作为参考", msgs[0].text)
        self.assertIn("1. 文档一", msgs[0].text)
        self.assertEqual(msgs[1].text, "回答正文")

    def test_deep_search_think(self):
        conv = {"id": "c3", "speaker": "ai", "createTime": 1791185416,
                "speechesV2": [{"speechType": "deep_search", "content": [
                    {"type": "deepSearch", "title": "已深度思考(用时7秒)",
                     "contents": [{"type": "text", "msg": "用户想要的是…先分析…"}]},
                    _txt("最终答案"),
                ]}]}
        msgs, warns = _conv_blocks(conv)
        self.assertEqual([m.role for m in msgs], ["note", "assistant"])
        self.assertIn("[think] 已深度思考(用时7秒)", msgs[0].text)
        self.assertIn("用户想要的是…先分析…", msgs[0].text)
        self.assertFalse(warns)

    def test_deep_search_agent_text_key(self):
        """deepSearchAgent 的 contents[].text 键（非 msg）。"""
        conv = {"id": "c4", "speaker": "ai",
                "speechesV2": [{"speechType": "deep_search_agent", "content": [
                    {"type": "deepSearchAgent", "title": "已处理",
                     "contents": [{"type": "text", "text": "先合并两个条件"}]},
                    _txt("产出"),
                ]}]}
        msgs, warns = _conv_blocks(conv)
        self.assertEqual(len(msgs), 2)
        self.assertIn("先合并两个条件", msgs[0].text)

    def test_human_pdf_attachment(self):
        conv = {"id": "c5", "speaker": "human",
                "speechesV2": _speech(
                    {"type": "pdf", "fileName": "a.pdf", "size": 123,
                     "url": "https://x/y"},
                    _txt("提炼文档要点"))}
        msgs, warns = _conv_blocks(conv)
        self.assertEqual([m.role for m in msgs], ["note", "user"])
        self.assertIn("[file] a.pdf (123 bytes)", msgs[0].text)
        self.assertEqual(msgs[1].text, "提炼文档要点")

    def test_step_and_unknown_block(self):
        conv = {"id": "c6", "speaker": "ai",
                "speechesV2": [{"speechType": "text", "content": [
                    {"type": "step", "msg": "正在优化提示词", "status": "ok"},
                    {"type": "FUTURE_TYPE", "foo": 1},
                    _txt("正文"),
                ]}]}
        msgs, warns = _conv_blocks(conv)
        roles = [m.role for m in msgs]
        self.assertEqual(roles, ["note", "assistant"])
        self.assertIn("[step] 正在优化提示词 (ok)", msgs[0].text)
        self.assertTrue(any("FUTURE_TYPE" in w for w in warns))

    def test_unknown_speaker(self):
        msgs, warns = _conv_blocks({"id": "c7", "speaker": "bot",
                                    "speechesV2": []})
        self.assertEqual(msgs, [])
        self.assertTrue(warns)


class TestParseDetail(unittest.TestCase):
    def test_ordering_and_hide_filter(self):
        j = {"id": "s1", "title": "测试会话", "chatModelId": "deep_seek",
             "convs": [
                 {"id": "a", "conversationId": "s1", "speaker": "ai",
                  "index": 2, "hideConv": 0,
                  "speechesV2": _speech(_txt("答案"))},
                 {"id": "b", "conversationId": "s1", "speaker": "human",
                  "index": 1, "skipConv": 0,
                  "speechesV2": _speech(_txt("问题"))},
                 {"id": "c", "conversationId": "s1", "speaker": "human",
                  "index": 3, "hideConv": 1,
                  "speechesV2": _speech(_txt("被隐藏的轮"))},
             ]}
        msgs, warns, meta = _parse_detail(j)
        self.assertEqual([m.role for m in msgs], ["user", "assistant"])
        self.assertEqual(meta["models"], ["deep_seek"])
        self.assertFalse(warns)

    def test_broken_convs(self):
        msgs, warns, _meta = _parse_detail({"no_convs": 1})
        self.assertEqual(msgs, [])
        self.assertTrue(warns)


class TestAdapter(unittest.TestCase):
    def _make_corpus(self, d: Path):
        (d / "list_0.json").write_text(json.dumps({
            "conversations": [{"id": "s1", "title": "元宝测试",
                               "firstRepliedAt": 1791185414,
                               "lastRepliedAt": 1791249269,
                               "subTitle": "概要文本"}]},
            ensure_ascii=False), encoding="utf-8", newline="\n")
        (d / "detail_s1.json").write_text(json.dumps({
            "id": "s1", "title": "元宝测试", "chatModelId": "deep_seek",
            "modelId": "gpt_175B", "agentName": "元宝",
            "firstRepliedAt": 1791185414, "lastRepliedAt": 1791249269,
            "convs": [
                {"id": "a", "conversationId": "s1", "speaker": "human",
                 "index": 1, "createTime": 1791185414,
                 "speechesV2": _speech(_txt("问题内容"))},
                {"id": "b", "conversationId": "s1", "speaker": "ai",
                 "index": 2, "createTime": 1791185420,
                 "speechesV2": [{"speechType": "deep_search", "content": [
                     {"type": "deepSearch", "title": "已深度思考(用时7秒)",
                      "contents": [{"type": "text", "msg": "思考中"}]},
                     _txt("回答")]}]},
            ]}, ensure_ascii=False), encoding="utf-8", newline="\n")

    def test_detect_list_load(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "yuanbao_raw"
            d.mkdir()
            self._make_corpus(d)
            ad = YuanbaoRawAdapter(paths=[str(d)])
            rep = ad.detect()
            self.assertEqual(rep.status, "OK")
            self.assertEqual(rep.session_count, 1)

            items = ad.list_sessions()
            self.assertEqual(len(items), 1)
            it = items[0]
            self.assertEqual(it["session_id"], "s1")
            self.assertEqual(it["title"], "元宝测试")
            self.assertIsNotNone(it["created_at"])

            rec = ad.load_session("s1")
            roles = [m.role for m in rec.messages]
            self.assertEqual(roles, ["user", "note", "assistant"])
            self.assertEqual(rec.messages[0].text, "问题内容")
            self.assertIn("[think] 已深度思考(用时7秒)", rec.messages[1].text)
            self.assertEqual(rec.extra["models"],
                             ["deep_seek", "gpt_175B"])
            self.assertEqual(rec.extra["agent_name"], "元宝")
            self.assertFalse(rec.extra["lossy"])

    def test_detect_missing(self):
        class _Isolated(YuanbaoRawAdapter):
            candidate_paths = ()          # 屏蔽默认候选，避免命中真实 corpus

        with tempfile.TemporaryDirectory() as td:
            ad = _Isolated(paths=[str(Path(td) / "nope")])
            self.assertEqual(ad.detect().status, "STUB")
            self.assertEqual(ad.list_sessions(), [])
        ad2 = _Isolated()
        self.assertEqual(ad2.detect().status, "MISSING")

    def test_load_nonexistent(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "yuanbao_raw"
            d.mkdir()
            ad = YuanbaoRawAdapter(paths=[str(d)])
            with self.assertRaises(KeyError):
                ad.load_session("nope")


if __name__ == "__main__":
    unittest.main()
