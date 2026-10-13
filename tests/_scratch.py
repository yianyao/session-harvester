# -*- coding: utf-8 -*-
"""tests 共享临时根：**每进程一个**独立目录（tests/_tmp 下），env 可显式指定。

为什么存在（P4-6 挂账修复，2026-10-13）：历史实现是全部测试模块直写
`tests/_tmp` 的**固定名**文件，两大环境坑都被踩过：

1. **嵌套活跑共享冲突**：顶层全量 discover 里 `test_v48_test_baseline`
   真跑「嵌套套件」与「无 PyYAML 门禁」子进程，父子两进程写同一批固定名
   文件；外层泄漏的 Windows 句柄锁住残file，嵌套进程覆写即 WinError 32
   （实测 nested errors=25，单跑全绿）。
2. **沙箱 bulk-delete guard**：WorkBuddy 沙箱 vendor shim 把 `unlink`
   钩到回收站并带 bulk-delete 守卫——对**残file堆积的大目录**（tests/_tmp
   历史残file数千项）逐个 unlink 会被守卫拦截报错（实测 gate 直跑
   errors=81/86 抖动，其中 59 条是守卫拦截）。

修复 = 每个进程第一次调 `tmp_root()` 时在 tests/_tmp 下领一个
`proc-<pid>-<rand>` 专属目录并**全进程记忆**：跨模块共享语义不变
（同进程所有模块同根），跨进程彻底隔离；进程内的 unlink 只落在
自己刚建的干净小目录里，两个坑同时消失。

残file政策：**一概不删**——历史残file原地不动（清扫被用户明确拒绝过），
proc-* 目录也只增不清；用户哪天想清，删 tests/_tmp 下任意子目录即可。

显式指定：设 `HARVESTER_TEST_TMP` 后所有进程用同一路径
（调试/复现用；正常跑不需要）。
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
DEFAULT_ROOT = TESTS_DIR / "_tmp"

#: 显式指定临时根的环境变量名（调试/复现用）
ENV_VAR = "HARVESTER_TEST_TMP"

_MEMO: Path | None = None


def tmp_root() -> Path:
    """返回**本进程**的测试临时根（默认根首次调用确定，之后全进程同一根）。

    设了 HARVESTER_TEST_TMP 则**恒**用该路径（优先于记忆，调试/复现
    语义确定）；否则用 tests/_tmp/proc-<pid>-<rand>。目录**不在此
    创建**——用例自行 mkdir（历史语义，exist_ok 幂等）。
    """
    global _MEMO
    override = os.environ.get(ENV_VAR)
    if override:
        return Path(override)
    if _MEMO is None:
        _MEMO = DEFAULT_ROOT / (
            "proc-%d-%s" % (os.getpid(), uuid.uuid4().hex[:8]))
    return _MEMO
