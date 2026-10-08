# -*- coding: utf-8 -*-
"""v0.22 P0-5 adapter model 抽取测试（H13：dsh/autoclaw/vscode_copilot
均无 model 抽取 → 库中 model 覆盖仅 24%，160/261 错误归"(空)"）。

三个 adapter 的 model 提取均为模块级纯函数（fixture 直接喂解析后的
行/条目/state，不依赖 zstd/sqlite 环境）：
- dsh._model_from_lines：request/header 的 header.config.model 计次
  （真实日志实测路径），subagent/descriptor.agentModel 兜底；众数胜出；
- autoclaw._model_from_entries：entry payload 的 data.model.model
  （真实库实测四类 entry 统一路径）；
- vscode_copilot._model_from_state：requests[].result.metadata.resolvedModel
  优先（真实模型），兜底 requests[].modelId；众数胜出。
"""
from __future__ import annotations

import unittest

from harvester.adapters.autoclaw import _model_from_entries
from harvester.adapters.dsh import _model_from_lines
from harvester.adapters.vscode_copilot import _model_from_state


class TestDshModel(unittest.TestCase):

    def test_majority_from_request_header(self):
        lines = [
            {"type": "request/header",
             "data": {"header": {"config": {"model": "deepseek-flash"}}}},
            {"type": "request/header",
             "data": {"header": {"config": {"model": "deepseek-flash"}}}},
            {"type": "request/header",
             "data": {"header": {"config": {"model": "deepseek-reasoner"}}}},
        ]
        model, models = _model_from_lines(lines)
        self.assertEqual(model, "deepseek-flash")  # 众数
        self.assertEqual(models, {"deepseek-flash": 2,
                                  "deepseek-reasoner": 1})

    def test_subagent_descriptor_fallback(self):
        lines = [
            {"type": "subagent/descriptor",
             "data": {"agentModel": "deepseek-flash", "label": "x"}},
        ]
        model, _models = _model_from_lines(lines)
        self.assertEqual(model, "deepseek-flash")

    def test_no_model_rows(self):
        model, models = _model_from_lines(
            [{"type": "user/message", "data": {"content": []}}])
        self.assertIsNone(model)
        self.assertEqual(models, {})


class TestAutoclawModel(unittest.TestCase):

    def test_from_entry_payload(self):
        entries = [
            (0, "r1", "request", '{"data": {"model": {"model": '
             '"zai_glm-5.3-flash"}, "text": "hi"}}', "2026-09-24T06:55:09Z"),
            (1, "r1", "reasoning", '{"data": {"model": {"model": '
             '"zai_glm-5.3-flash"}, "text": "think"}}', "2026-09-24T06:55:10Z"),
            (2, "r1", "assistant", '{"data": {"model": {"model": '
             '"zai_glm-5.3-flash"}, "content": []}}', "2026-09-24T06:55:11Z"),
        ]
        self.assertEqual(_model_from_entries(entries), "zai_glm-5.3-flash")

    def test_bad_payload_ignored(self):
        entries = [
            (0, "r1", "assistant", "not-json", None),
            (1, "r1", "assistant", '{"data": {}}', None),
        ]
        self.assertIsNone(_model_from_entries(entries))


class TestVscodeModel(unittest.TestCase):

    def test_resolved_model_preferred(self):
        state = {"requests": [
            {"modelId": "copilot/auto",
             "result": {"metadata": {"resolvedModel": "mai-code-1.1-flash"}}},
            {"modelId": "copilot/auto",
             "result": {"metadata": {"resolvedModel": "mai-code-1.1-flash"}}},
            {"modelId": "gpt-5"},  # 无 resolvedModel → 兜底 modelId
        ]}
        model, models = _model_from_state(state)
        self.assertEqual(model, "mai-code-1.1-flash")
        self.assertEqual(models, {"mai-code-1.1-flash": 2, "gpt-5": 1})

    def test_empty_state(self):
        self.assertEqual(_model_from_state(None), (None, {}))
        self.assertEqual(_model_from_state({}), (None, {}))


if __name__ == "__main__":
    unittest.main()
