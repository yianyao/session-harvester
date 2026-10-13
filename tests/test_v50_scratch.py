# -*- coding: utf-8 -*-
"""测试临时根隔离门（P4-6 挂账修复，2026-10-13）：每进程一个独立临时根。

为什么值得钉（flake 机制，两大坑）：顶层全量 discover 里
`test_v48_test_baseline` 真跑「嵌套套件」与「无 PyYAML 门禁」子进程——

1. 父子两进程写同一批 tests/_tmp **固定名**文件，外层泄漏句柄锁住残file，
   嵌套进程覆写即 WinError 32（实测 nested errors=25，单跑全绿）；
2. 沙箱 vendor shim 的 unlink 钩子带 bulk-delete 守卫，对残file堆积的
   大目录逐个 unlink 会被拦（实测 gate errors=81/86 抖动，59 条是守卫）。

修复 = 所有测试模块经 `_scratch.tmp_root()` 领**每进程独立**目录
（tests/_tmp/proc-<pid>-<rand>，全进程记忆），跨进程零共享、进程内
只 unlink 自己刚建的文件。残file**原地不动**——清扫被用户明确拒绝过。

判据（何时会红）：
- 谁再把固定名 `_tmp` 路径字面量写进 tests/（绕过 helper 直连）→ 红；
- `tmp_root()` 同进程两次调用不同根 / 不在 tests/_tmp 下 / 不认
  `HARVESTER_TEST_TMP` 显式指定 → 红。
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

import _scratch  # noqa: E402

ENV = "HARVESTER_TEST_TMP"


class TestTmpRoot(unittest.TestCase):

    def test_default_is_process_unique_under_tests_tmp(self):
        """默认根：同进程两次调用同根（跨模块共享语义不变），
        落在 tests/_tmp 下、名字带 proc- 前缀（跨进程隔离）。"""
        old = os.environ.pop(ENV, None)
        try:
            a, b = _scratch.tmp_root(), _scratch.tmp_root()
            self.assertEqual(a, b, "同进程两次调用拿到不同根 → 跨模块共享被破坏")
            self.assertEqual(a.parent, TESTS / "_tmp")
            self.assertRegex(a.name, r"^proc-\d+-")
        finally:
            if old is not None:
                os.environ[ENV] = old

    def test_env_override_redirects(self):
        """设了 HARVESTER_TEST_TMP 就用它的路径（调试/复现入口）。"""
        with tempfile.TemporaryDirectory() as td:
            os.environ[ENV] = td
            try:
                self.assertEqual(_scratch.tmp_root(), Path(td))
            finally:
                os.environ.pop(ENV, None)


class TestNoBareLiterals(unittest.TestCase):
    """谁绕过 helper 直写固定名 tests/_tmp → 红（修完 6 模块后必须 0 命中）。"""

    def test_no_tmp_literal_in_tests(self):
        hits = []
        me = Path(__file__).name
        for p in sorted(TESTS.glob("test_*.py")):
            if p.name == me:
                continue    # 本门源码含检测正则与示例文本，排除自身
            if re.search(r'parent / ["\']_tmp["\']',
                         p.read_text(encoding="utf-8")):
                hits.append(p.name)
        self.assertEqual(
            hits, [],
            "以下模块仍直写固定名 tests/_tmp（应改走 _scratch.tmp_root()，"
            "否则嵌套活跑父子共享固定名 → Windows 句柄锁 flake，"
            "且沙箱 bulk-delete guard 会拦残file unlink）：" + ", ".join(hits))


if __name__ == "__main__":
    unittest.main()
