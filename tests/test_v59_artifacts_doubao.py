# -*- coding: utf-8 -*-
"""v0.51 S2 测试：artifacts extract 扩源实测**判负登记**（豆包/元宝/千问）。

背景（2026-10-16 全量探针，结论见 harvester/artifacts.py docstring）：
- 豆包 10019 op=2 的 display_content.operation 是 `Write {json}`，但 args
  为服务端截断预览（~4000 字符，尾部拼接"内容较长，暂不支持展示完整内容"，
  全文在沙箱不落盘）；
- 10030 brief 恒 1000 字符截断；op=1 是 Read；
- 元宝/千问 raw 为纯附件引用（fileName+url）。

截断冒充全文即误导 → 三源一律不提取，宁守下轨诚实空态。

本文件的两个职责：
1. **守卫**：未登记/判负源一律 NotImplementedError（含判负依据文案）；
2. **登记过期检测器**：钉住"真库 op2 JSON 截断"这一事实——豆包导出格式
   若变（args 不再截断），这些断言会红，提醒重新登记扩源。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harvester import artifacts as A  # noqa: E402
from harvester.adapters.doubao_raw import DoubaoRawAdapter  # noqa: E402

REAL_SID = "38445626449531138"  # 全库唯一含 10019 块的会话（op 分布 {1:10,2:3}）


class GuardTest(unittest.TestCase):
    """判负源守卫：不许提取。"""

    def test_doubao_raw_rejected(self):
        with self.assertRaises(NotImplementedError) as cm:
            A._extract_records(f"doubao-raw:{REAL_SID}")
        self.assertIn("判负", str(cm.exception))

    def test_yuanbao_and_qianwen_rejected(self):
        for sid in ("yuanbao-raw:x", "qianwen-raw:x"):
            with self.assertRaises(NotImplementedError):
                A._extract_records(sid)

    def test_dsh_still_works(self):
        """dsh 正路不回归：合成行流照常提取（口径回归钉）。"""
        recs = A.extract_dsh_records([{
            "type": "tool/call", "time": 1760000000000,
            "data": {"tool": "Write",
                     "arguments": json.dumps(
                         {"file_path": "a.md", "content": "hi"})}}])
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["file_path"], "a.md")


class RegistrationExpiryTest(unittest.TestCase):
    """登记过期检测：豆包导出格式若变（不再截断）→ 这里红 → 重新登记。"""

    def setUp(self):
        ad = DoubaoRawAdapter()
        d = ad._find_dir()
        if d is None or not (d / f"detail_{REAL_SID}.json").is_file():
            self.skipTest("真库豆包 detail 不在（corpus/doubao_raw）")
        self.j = json.loads(
            (d / f"detail_{REAL_SID}.json").read_text(encoding="utf-8"))

    def _op2_payloads(self) -> list[str]:
        out = []
        for m in self.j.get("messages") or []:
            for cb in (m.get("content_block") or []):
                if not isinstance(cb, dict) or cb.get("block_type") != 10019:
                    continue
                fo = (cb.get("content") or {}).get(
                    "file_operation_block") or {}
                if fo.get("operation_type") != 2:
                    continue
                op = str((fo.get("display_content") or {}).get("operation")
                         or "")
                if op.startswith("Write "):
                    out.append(op[len("Write "):])
        return out

    def test_op2_args_are_truncated_still(self):
        payloads = self._op2_payloads()
        self.assertTrue(payloads, "真库应仍有 op2 Write 块（样本消失→重探）")
        bad = [p for p in payloads
               if not _parses_and_complete(p)]
        self.assertEqual(
            len(bad), len(payloads),
            "有 op2 args 变成完整合法 JSON 了 → 截断前提失效，"
            "请重新实测并登记豆包扩源（artifacts.py docstring）")
        self.assertTrue(all("暂不支持展示完整内容" in p for p in bad),
                        "截断尾注消失 → 导出格式已变，重新实测登记")


def _parses_and_complete(payload: str) -> bool:
    """能否解析为完整 JSON 且不带截断尾注（当前实测：全部否）。"""
    try:
        json.loads(payload)
    except json.JSONDecodeError:
        return False
    return "暂不支持展示完整内容" not in payload


if __name__ == "__main__":
    unittest.main()
