# -*- coding: utf-8 -*-
"""sitecustomize：临时目录可写的进程级补丁（**环境适配，不是项目代码**）。

**为什么它会在版本库里**（v0.38）：它此前放在 `docs/reports/`（gitignore 目录），
结果是——**全量套件的前置条件在新 clone 上不存在**，而文档却按"它就是有"来教人
跑测试（新会话只会看到一堆 `PermissionError`）。环境适配件也该被版本化，故移到这里。

背景（2026-10-10 实测，见交接台账 H53）：本会话 DSH 沙箱把
`os.mkdir(path, 0o700)` 建出的目录 provision 成连本进程都写不进去的形态
（`PermissionError: Errno 13`，连 `icacls` 读 ACL 都 Access is denied）；
0o755/0o777/默认 正常。`tempfile.mkdtemp()` 用 0o700、`mkstemp()` 用 0o600，
故**任何基于 tempfile 的测试在本沙箱都会失败**（与项目代码无关）。

本文件把传给 `os.mkdir` / `os.open` 的 mode 补上组/其他位；解释器启动时自动导入
（需把本目录放进 PYTHONPATH）：

    $env:PYTHONPATH = "<repo>\\scripts\\sandbox"
    & $venv -X utf8 -m unittest discover -s tests

**只在沙箱会话需要**（普通机器上直接跑套件即可）；对工作区外的项目（如
`harvester-view`）同样有效，因为它不要求写入目标项目目录。
"""
import os

_mkdir, _open = os.mkdir, os.open


def _mkdir_wide(path, mode=0o777, *a, **k):
    return _mkdir(path, mode | 0o077, *a, **k)


def _open_wide(path, flags, mode=0o777, *a, **k):
    return _open(path, flags, mode | 0o077, *a, **k)


os.mkdir = _mkdir_wide
os.open = _open_wide
