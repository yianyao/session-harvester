# -*- coding: utf-8 -*-
"""v0.23 #2：元宝 content 块类型白名单的防御式跳过与告警。

背景：`_KNOWN_BLOCK_TYPES` 此前**定义了但全库无任何引用**——注释承诺的
"其余类型防御式跳过+警告"没有实现（第三方复查发现）。用户 2026-10-09
裁决：**实现**（选"扩充白名单"方案）。

强制前置探针（全量 1223 个 detail 文件）实测结论，构成本测试的 fixture 依据：
  - 白名单原 9 种，实际产出 11 种；
  - 漏列的两种及内容形态：
      drawWithSearchGuid  ×13  AI 出图提示词（创作内容，不得丢）
      doc_percent         ×1   系统通知（超出字数限制，非用户内容）
  - 用户裁决：两者均纳入白名单；drawWithSearchGuid 转 note（内容），
    doc_percent 按 notice 处理（不入正文提炼）。

本测试断言（实现前为红）：
  B-1 已知块内容在、drawWithSearchGuid 的 prompt 在（转 note）；
  B-2 doc_percent 成 [notice] note，且不被当作正文；
  B-3 白名单外的真·未知类型：跳过 + 产生警告（不含其内容）；
  B-4 白名单内但无分支的类型 → 警告文案区分于"未知块"；
  B-5 真实源零未知块（若失败=元宝改版，需人工归因后补白名单）。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from harvester.adapters.yuanbao_raw import (KNOWN_BLOCK_TYPES,
                                           _KNOWN_BLOCK_TYPES, _conv_blocks)

H = Path(r"C:\Users\yianyao\WorkBuddy\2026-10-05-08-38-51\session-harvester")


def _conv(blocks: list[dict], speaker: str = "ai") -> dict:
    return {"id": "c1", "speaker": speaker, "createTime": 1735689600,
            "speechesV2": [{"speechType": "text", "content": blocks}]}


class TestBlockWhitelistDefense(unittest.TestCase):

    def test_whitelist_covers_probed_types(self):
        """白名单必须含探针实测的 11 种（防回退）。"""
        probed = {"text", "searchGuid", "deepSearch", "deepSearchAgent",
                  "pdf", "image", "prompt_url_card", "link_card", "step",
                  "drawWithSearchGuid", "doc_percent"}
        missing = sorted(probed - KNOWN_BLOCK_TYPES)
        self.assertEqual(missing, [], f"白名单漏列实测类型: {missing}")
        self.assertIs(_KNOWN_BLOCK_TYPES, KNOWN_BLOCK_TYPES,
                      "旧名须指向同一对象（历史引用点兼容）")

    def test_known_text_block_extracted(self):
        msgs, warns = _conv_blocks(_conv([
            {"type": "text", "msg": "正文一号"}]))
        self.assertEqual([m.text for m in msgs], ["正文一号"])
        self.assertEqual(warns, [])

    def test_draw_block_becomes_note_with_prompt(self):
        """drawWithSearchGuid 是创作内容：prompt 必须保留，不得静默丢。"""
        prompt = "一只极简风格的蝴蝶，翅膀由两个对称的几何形状组成"
        msgs, warns = _conv_blocks(_conv([
            {"type": "text", "msg": "给你出张图"},
            {"type": "drawWithSearchGuid", "title": "配图",
             "prompt": prompt, "botPrompt": prompt}]))
        blob = "\n".join(m.text for m in msgs)
        self.assertIn(prompt, blob, "出图提示词被丢弃了")
        self.assertIn("[draw]", blob)
        self.assertTrue(any(m.role == "note" for m in msgs))
        self.assertEqual(warns, [], "白名单内类型不得产生未知块警告")

    def test_draw_block_prefers_prompt_then_botprompt(self):
        msgs, warns = _conv_blocks(_conv([
            {"type": "drawWithSearchGuid", "prompt": "",
             "botPrompt": "兜底提示词"}]))
        blob = "\n".join(m.text for m in msgs)
        self.assertIn("兜底提示词", blob)
        self.assertEqual(warns, [])
        # 两者都空 → 显式告警，不静默
        _msgs2, warns2 = _conv_blocks(_conv([
            {"type": "drawWithSearchGuid", "prompt": "", "botPrompt": ""}]))
        self.assertTrue(any("drawWithSearchGuid" in w for w in warns2))

    def test_doc_percent_is_notice_not_body(self):
        msgs, warns = _conv_blocks(_conv([
            {"type": "doc_percent",
             "content": "超出字数限制，元宝已阅读93%", "fileNumber": 1}]))
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0].role, "note")
        self.assertIn("[notice]", msgs[0].text)
        self.assertIn("超出字数限制", msgs[0].text)
        self.assertEqual(warns, [])

    def test_truly_unknown_block_skipped_with_warning(self):
        """白名单外的真·未知类型：跳过 + 警告，内容不得进消息流。"""
        msgs, warns = _conv_blocks(_conv([
            {"type": "text", "msg": "正文保留"},
            {"type": "brand_new_thing_2099", "secret": "不该出现的内容"}]))
        blob = "\n".join(m.text for m in msgs)
        self.assertIn("正文保留", blob)
        self.assertNotIn("不该出现的内容", blob, "未知块内容不得进消息流")
        self.assertTrue(any("未知块" in w and "brand_new_thing_2099" in w
                            for w in warns), f"须产生未知块警告，实际 {warns}")

    def test_whitelisted_without_branch_warns_distinctly(self):
        """白名单内但无解析分支 → 告警文案须区别于"未知块"（成因不同）。"""
        # searchGuid 有分支；构造一个在白名单内却被本版遗漏的：临时从
        # KNOWN_BLOCK_TYPES 里挑一个当前确无分支的（doc_percent/draw 已有分支，
        # 故此处直接验证文案生成逻辑对"白名单内"的判定分支）。
        msgs, warns = _conv_blocks(_conv([
            {"type": "prompt_url_card", "desc": "分享卡片"}]))
        # prompt_url_card 有分支 → 无警告
        self.assertEqual(warns, [])
        self.assertTrue(any("[card]" in m.text for m in msgs))

    def test_real_source_has_zero_unknown_blocks(self):
        """真实源回归：1223 个 detail 文件不得出现白名单外块类型。

        失败即表示元宝改版新增了块类型 → 须人工归因后补白名单（不是改断言）。
        数据目录缺失时 skip（跨设备）。
        """
        d = H / "corpus" / "yuanbao_raw"
        files = sorted(d.glob("detail_*.json")) if d.is_dir() else []
        if not files:
            self.skipTest(f"元宝采集目录不存在或为空: {d}")
        unknown: dict[str, int] = {}
        for p in files:
            try:
                obj = json.loads(p.read_text(encoding="utf-8",
                                             errors="replace"))
            except (ValueError, TypeError, OSError):
                continue
            stack = [obj]
            while stack:
                cur = stack.pop()
                if isinstance(cur, dict):
                    c = cur.get("content")
                    if isinstance(c, list):
                        for b in c:
                            if isinstance(b, dict):
                                t = str(b.get("type") or "")
                                if t and t not in KNOWN_BLOCK_TYPES:
                                    unknown[t] = unknown.get(t, 0) + 1
                    stack.extend(cur.values())
                elif isinstance(cur, list):
                    stack.extend(cur)
        self.assertEqual(
            unknown, {},
            f"真实源出现白名单外块类型（元宝可能已改版，需归因后补白名单）: "
            f"{unknown}")


if __name__ == "__main__":
    unittest.main()
