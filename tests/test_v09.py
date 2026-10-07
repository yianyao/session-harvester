# -*- coding: utf-8 -*-
"""v0.9: qianwen_raw + doubao_raw adapter 测试（schema 来自真机采集核验）。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from harvester.adapters.doubao_raw import (
    DoubaoRawAdapter,
    _msg_messages,
    _parse_detail as _doubao_parse,
)
from harvester.adapters.qianwen_raw import (
    QianwenRawAdapter,
    _round_messages,
    _parse_detail as _qianwen_parse,
)


# ============ qianwen-raw ============

def _round(pos=1, req=None, resp=None, created=1791257352000):
    return {"user_type": 0, "pos": pos, "created_at": created,
            "create_time": "2026-06-06 10:00:00", "error_code": 0,
            "request_messages": req or [], "response_messages": resp or []}


class TestQianwenRound(unittest.TestCase):
    def test_user_text_round(self):
        r = _round(req=[{"mime_type": "text/plain", "content": "捡漏的近义词"}],
                   resp=[{"mime_type": "multi_load/iframe",
                          "content": "回答正文"}])
        warns = []
        msgs = _round_messages(r, warns)
        self.assertEqual([m.role for m in msgs], ["user", "assistant"])
        self.assertEqual(msgs[0].text, "捡漏的近义词")
        self.assertEqual(msgs[1].text, "回答正文")
        self.assertFalse(warns)

    def test_doc_attachment_and_hidden_skip(self):
        r = _round(req=[
            {"mime_type": "doc/url", "content": "",
             "meta_data": {"resource_infos": [
                 {"file_name": "报告.pdf", "file_format": "pdf",
                  "file_size": 2048}]}},
            {"mime_type": "text/hidden", "content": "元数据噪声"},
            {"mime_type": "text/plain", "content": "总结文档"},
        ], resp=[{"mime_type": "multi_load/iframe", "content": "摘要"}])
        warns = []
        msgs = _round_messages(r, warns)
        self.assertEqual([m.role for m in msgs], ["note", "user", "assistant"])
        self.assertIn("[file] 报告.pdf (2048 bytes)", msgs[0].text)

    def test_think_blocks(self):
        r = _round(resp=[
            {"mime_type": "plan_cot/post", "content": "先拆解问题"},
            {"mime_type": "bar/workflow",
             "meta_data": {"multi_load": [
                 {"type": "bar_thinking",
                  "content": {"title": "检索", "body": "查近义语料"}},
                 {"type": "other_bar", "content": {"title": "噪声"}},
             ]}},
            {"mime_type": "multi_load/iframe", "content": "最终答案"},
        ])
        warns = []
        msgs = _round_messages(r, warns)
        self.assertEqual([m.role for m in msgs], ["note", "assistant"])
        self.assertIn("先拆解问题", msgs[0].text)
        self.assertIn("检索：查近义语料", msgs[0].text)
        self.assertNotIn("噪声", msgs[0].text)

    def test_skipped_response_types(self):
        r = _round(resp=[
            {"mime_type": "signal/post", "content": "x"},
            {"mime_type": "bar/progress", "content": "x"},
            {"mime_type": "bar/iframe", "content": "x"},
            {"mime_type": "paa/iframe", "content": "推荐追问"},
            {"mime_type": "survey/card", "content": "问卷"},
        ])
        warns = []
        msgs = _round_messages(r, warns)
        self.assertEqual(msgs, [])
        self.assertFalse(warns)

    def test_error_round_warns(self):
        r = _round()
        r["error_code"] = "TIMEOUT"
        r["error_msg"] = "超时"
        warns = []
        _round_messages(r, warns)
        self.assertTrue(any("TIMEOUT" in w for w in warns))


class TestQianwenParse(unittest.TestCase):
    def test_rounds_sorted_by_created(self):
        j = {"session_id": "s1",
             "list": [_round(pos=2, created=2000,
                             req=[{"mime_type": "text/plain", "content": "第二轮"}]),
                      _round(pos=1, created=1000,
                             req=[{"mime_type": "text/plain", "content": "第一轮"}])]}
        msgs, warns = _qianwen_parse(j)
        self.assertEqual([m.role for m in msgs], ["user", "user"])
        self.assertEqual(msgs[0].text, "第一轮")
        self.assertFalse(warns)


class TestQianwenAdapter(unittest.TestCase):
    def _make_corpus(self, d: Path):
        (d / "list_0.json").write_text(json.dumps({
            "list": [{"session_id": "s1", "title": "千问测试",
                      "created_at": 1791257352000,
                      "updated_at": 1791257400000}]},
            ensure_ascii=False), encoding="utf-8", newline="\n")
        (d / "detail_s1.json").write_text(json.dumps({
            "session_id": "s1", "list": [
                _round(req=[{"mime_type": "text/plain", "content": "问题"}],
                       resp=[{"mime_type": "multi_load/iframe", "content": "回答"}])]},
            ensure_ascii=False), encoding="utf-8", newline="\n")

    def test_detect_list_load(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "qianwen_raw"
            d.mkdir()
            self._make_corpus(d)
            ad = QianwenRawAdapter(paths=[str(d)])
            self.assertEqual(ad.detect().status, "OK")
            self.assertEqual(ad.detect().session_count, 1)
            items = ad.list_sessions()
            self.assertEqual(items[0]["title"], "千问测试")
            rec = ad.load_session("s1")
            self.assertEqual([m.role for m in rec.messages],
                             ["user", "assistant"])
            self.assertEqual(rec.title, "千问测试")
            self.assertFalse(rec.extra["lossy"])

    def test_detect_missing(self):
        class _Isolated(QianwenRawAdapter):
            candidate_paths = ()

        with tempfile.TemporaryDirectory() as td:
            ad = _Isolated(paths=[str(Path(td) / "nope")])
            self.assertEqual(ad.detect().status, "STUB")
            self.assertEqual(ad.list_sessions(), [])
        self.assertEqual(_Isolated().detect().status, "MISSING")

    def test_load_nonexistent(self):
        with tempfile.TemporaryDirectory() as td:
            ad = QianwenRawAdapter(paths=[td])
            with self.assertRaises(KeyError):
                ad.load_session("nope")


# ============ doubao-raw ============

def _dmsg(ut=1, idx=1, blocks=None, create="1791257353"):
    return {"user_type": ut, "index_in_conv": str(idx),
            "create_time": create, "message_id": f"m{idx}",
            "content_block": blocks or []}


def _block(bt, content, parent_id="", bid="b1"):
    return {"block_type": bt, "block_id": bid, "parent_id": parent_id,
            "content": content}


class TestDoubaoMsg(unittest.TestCase):
    def test_user_text(self):
        m = _dmsg(ut=1, blocks=[_block(10000, {"text_block": {
            "text": "双人间和双床房有什么区别？"}})])
        warns = []
        msgs = _msg_messages(m, warns)
        self.assertEqual([x.role for x in msgs], ["user"])
        self.assertEqual(msgs[0].text, "双人间和双床房有什么区别？")
        self.assertFalse(warns)

    def test_think_title_then_text_order(self):
        m = _dmsg(ut=2, blocks=[
            _block(10040, {"thinking_block": {"finish_title": "设置提问策略"}},
                   bid="t1"),
            _block(10000, {"text_block": {"text": "", "summary": "正在思考"}},
                   parent_id="t1", bid="p1"),      # 占位块，应跳过
            _block(10000, {"text_block": {"text": "正式回答"}},
                   bid="a1"),
            _block(10091, {"elapsed_block": {}}),   # 耗时，跳过
        ])
        warns = []
        msgs = _msg_messages(m, warns)
        self.assertEqual([x.role for x in msgs], ["note", "assistant"])
        self.assertEqual(msgs[0].text, "[think] 设置提问策略")
        self.assertEqual(msgs[1].text, "正式回答")
        self.assertFalse(warns)

    def test_note_blocks_in_order(self):
        m = _dmsg(ut=2, blocks=[
            _block(10025, {"search_query_result_block": {
                "summary": "搜索 3 个关键词",
                "queries": ["双人间", "双床房"]}}, bid="s1"),
            _block(10000, {"text_block": {"text": "区别是…"}}, bid="a1"),
            _block(10082, {"interaction_ask_block": {"questions": [
                {"title": "请问预算多少？"}]}}, bid="q1"),
            _block(10019, {"file_operation_block": {
                "file_name": "prd.md", "path": "/home/x/prd.md"}}, bid="f1"),
            _block(10030, {"artifact_block": {"title": "经营分析"}}, bid="ar1"),
            _block(2074, {"creation_block": {"creations": [
                {"image": {"key": "tos/img/abc.jpeg"}}]}}, bid="c1"),
        ])
        warns = []
        msgs = _msg_messages(m, warns)
        roles = [x.role for x in msgs]
        self.assertEqual(roles, ["note", "assistant", "note", "note",
                                 "note", "note"])
        self.assertIn("[search] 搜索 3 个关键词", msgs[0].text)
        self.assertIn("双人间, 双床房", msgs[0].text)
        self.assertEqual(msgs[1].text, "区别是…")
        self.assertIn("[ask] 请问预算多少？", msgs[2].text)
        self.assertIn("[file-op] prd.md", msgs[3].text)
        self.assertIn("[artifact] 经营分析", msgs[4].text)
        self.assertIn("[image] 1 张生成图 (abc.jpeg)", msgs[5].text)
        self.assertFalse(warns)

    def test_unknown_block_warns(self):
        m = _dmsg(ut=2, blocks=[_block(9999, {"future_block": {}})])
        warns = []
        msgs = _msg_messages(m, warns)
        self.assertEqual(msgs, [])
        self.assertTrue(any("9999" in w for w in warns))


class TestDoubaoParse(unittest.TestCase):
    def test_sorted_by_index(self):
        j = {"messages": [
            _dmsg(ut=2, idx=2, blocks=[_block(10000, {"text_block": {"text": "答"}})]),
            _dmsg(ut=1, idx=1, blocks=[_block(10000, {"text_block": {"text": "问"}})]),
        ]}
        msgs, warns = _doubao_parse(j)
        self.assertEqual([m.role for m in msgs], ["user", "assistant"])
        self.assertEqual(msgs[0].text, "问")
        self.assertFalse(warns)


class TestDoubaoAdapter(unittest.TestCase):
    def _make_corpus(self, d: Path):
        (d / "list_0.json").write_text(json.dumps(
            [{"conversation_id": "s1", "name": "豆包测试",
              "create_time": "1791257352", "update_time": "1791257614"}],
            ensure_ascii=False), encoding="utf-8", newline="\n")
        (d / "detail_s1.json").write_text(json.dumps({
            "conversation_id": "s1", "name": "豆包测试",
            "create_time": "1791257352", "update_time": "1791257614",
            "n_messages": 2, "messages": [
                _dmsg(ut=1, idx=1, blocks=[
                    _block(10000, {"text_block": {"text": "帮我写 PRD"}})]),
                _dmsg(ut=2, idx=2, blocks=[
                    _block(10040, {"thinking_block": {
                        "finish_title": "核实需求"}}, bid="t1"),
                    _block(10000, {"text_block": {"text": "好的，先问几个问题"}},
                           bid="a1")]),
            ]}, ensure_ascii=False), encoding="utf-8", newline="\n")

    def test_detect_list_load(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "doubao_raw"
            d.mkdir()
            self._make_corpus(d)
            ad = DoubaoRawAdapter(paths=[str(d)])
            self.assertEqual(ad.detect().status, "OK")
            self.assertEqual(ad.detect().session_count, 1)
            items = ad.list_sessions()
            self.assertEqual(items[0]["title"], "豆包测试")
            self.assertEqual(items[0]["message_count"], 2)
            rec = ad.load_session("s1")
            self.assertEqual([m.role for m in rec.messages],
                             ["user", "note", "assistant"])
            self.assertEqual(rec.messages[1].text, "[think] 核实需求")
            self.assertIsNotNone(rec.created_at)
            self.assertFalse(rec.extra["lossy"])

    def test_detect_missing(self):
        class _Isolated(DoubaoRawAdapter):
            candidate_paths = ()

        with tempfile.TemporaryDirectory() as td:
            ad = _Isolated(paths=[str(Path(td) / "nope")])
            self.assertEqual(ad.detect().status, "STUB")
            self.assertEqual(ad.list_sessions(), [])
        self.assertEqual(_Isolated().detect().status, "MISSING")

    def test_load_nonexistent(self):
        with tempfile.TemporaryDirectory() as td:
            ad = DoubaoRawAdapter(paths=[td])
            with self.assertRaises(KeyError):
                ad.load_session("nope")


if __name__ == "__main__":
    unittest.main()
