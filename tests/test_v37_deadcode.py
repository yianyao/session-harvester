# -*- coding: utf-8 -*-
"""v0.37 测试：死代码扫描工具化（`harvester/deadcode.py` + CLI `deadcode-scan`）。

来源：它此前是 `docs/reports/` 下的一次性脚本——**每轮收尾的固定动作**（全局记忆
§四 12），按"能力进工具本体"的要求收回（用户 2026-10-10 第 1 点）。

**工具刚跑第一次就抓到旧脚本漏掉的 3 条**（旧版 import 检查用了
`src.replace("import x", "")` 这种字符串 hack）：
- `cli.py` 的 `title_chain`、`noisetriage.py` 的 `re` → **真死**，已删；
- `dsh.py` 的 `from compression import zstd as _z  # noqa: F401` → 是**可用性探测**
  （try 里 import 只为判断能否解压），行内的 `noqa` 就是既有约定 → 工具改为**认它**。

断言都是双向的（死要报、活的不能报），任一侧写错都会红。最后一个类把"harvester/
必须 0 条"钉进套件，使这道例行检查不必靠人记得跑。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harvester.deadcode import found_total, render_deadcode, scan

REPO = Path(__file__).resolve().parents[1]

DIRTY = '''\
import os
import json
import sys  # noqa: F401

UNUSED_CONST = 1
USED_CONST = 2
KEPT_CONST = 3  # noqa


def used_fn():
    return json.dumps({"c": USED_CONST})


def dead_fn():
    return 1


async def dead_async_fn():
    return 2


class C:
    def method_x(self):
        return 1
'''

USERS = '''\
"""用到 used_fn / USED_CONST / KEPT_CONST / method_x 的地方。"""

USERS = [used_fn, USED_CONST, KEPT_CONST, "method_x"]
'''

#: 去掉豁免的同一份夹具（注意只删标记本身，留下缩进空格——否则
#: `import sys  # noqa: F401` 会被削成 `import sys: F401` 而变成语法错误）
DIRTY_NO_WAIVER = DIRTY.replace("# noqa: F401", "").replace("# noqa", "")

CLEAN = '''\
import json

USED_CONST = 2


def used_fn():
    return json.dumps({"c": USED_CONST})
'''


def _make_fixture(root: Path, pkg_src: str, users_src: str = USERS) -> None:
    (root / "pkg").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(parents=True, exist_ok=True)
    (root / "pkg" / "mod.py").write_text(pkg_src, encoding="utf-8")
    (root / "tests" / "test_use.py").write_text(users_src, encoding="utf-8")


def _mods(r: dict) -> set[str]:
    """从发现串里取**精确**的模块名。

    必须精确比对：`"USED_CONST"` 是 `"UNUSED_CONST"` 的子串——子串断言在这里
    会假通过（本文件第一版就是这么写的，被测试自己抓出来）。
    """
    out = set()
    for e in r["unused_imports"]:
        if " import " not in e:
            continue                       # 语法错误那条没有 import
        out.add(e.split(" import ", 1)[1].split(" as ")[0].strip())
    return out


def _fns(r: dict) -> set[str]:
    return {e.rsplit("def ", 1)[1].rstrip("()") for e in r["uncalled_functions"]}


def _consts(r: dict) -> set[str]:
    return {e.rsplit(" ", 1)[-1] for e in r["unused_constants"]}


class TestScan(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def test_reports_dead_and_spares_live(self):
        _make_fixture(self.root, DIRTY)
        r = scan(roots=("pkg", "tests"), base=self.root, report_root="pkg")
        # 同名但被用到的 / 被 noqa 豁免的，都不许报
        self.assertEqual(_mods(r), {"os"}, r["unused_imports"])
        self.assertEqual(_fns(r), {"dead_fn", "dead_async_fn"},
                         r["uncalled_functions"])       # async 也要查，类方法豁免
        self.assertEqual(_consts(r), {"UNUSED_CONST"}, r["unused_constants"])
        self.assertGreaterEqual(r["files"], 2)

    def test_noqa_waives_any_kind_finding(self):
        """豁免机制要真的生效：去掉 `# noqa` 后同一条必须被报出来。"""
        _make_fixture(self.root, DIRTY)
        kept = scan(roots=("pkg",), base=self.root, report_root="pkg")
        self.assertEqual(_mods(kept), {"os"}, kept["unused_imports"])
        self.assertEqual(_consts(kept), {"UNUSED_CONST"}, kept["unused_constants"])

        _make_fixture(self.root, DIRTY_NO_WAIVER)
        now = scan(roots=("pkg",), base=self.root, report_root="pkg")
        self.assertEqual(_mods(now), {"os", "sys"}, now["unused_imports"])
        self.assertEqual(_consts(now), {"UNUSED_CONST", "KEPT_CONST"},
                         now["unused_constants"])

    def test_clean_fixture_is_empty_and_renders(self):
        _make_fixture(self.root, CLEAN)
        r = scan(roots=("pkg", "tests"), base=self.root, report_root="pkg")
        self.assertEqual(found_total(r), 0, r)
        self.assertIn("未发现死代码", render_deadcode(r))

    def test_missing_root_is_not_an_error(self):
        """兄弟仓库可能不在（只 checkout 了后端）——缺根目录不算错误，

        但**必须在报告里显式说出"没找到"**（`missing`），不能假装覆盖了。
        """
        _make_fixture(self.root, CLEAN)
        r = scan(roots=("pkg", "tests", "harvester-view"), base=self.root,
                 report_root="pkg")
        self.assertEqual(found_total(r), 0, r)
        self.assertIn("harvester-view", r["missing"])
        self.assertIn("未找到", render_deadcode(r))

    def test_sibling_repo_root_is_actually_found(self):
        """`harvester-view` 是**兄弟仓库**：上一版只在 `base/<名>` 下找 → 从后端
        仓库里跑时静默落空，报告却仍写着"覆盖 harvester-view"。这条钉住兄弟查找。
        """
        backend = self.root / "backend"
        _make_fixture(backend, CLEAN)                  # 造出 pkg/ 与 tests/
        sibling = self.root / "harvester-view"
        sibling.mkdir()
        (sibling / "v.py").write_text("def helper_in_view():\n    return 1\n",
                                      encoding="utf-8")
        r = scan(roots=("pkg", "tests", "harvester-view"), base=backend,
                 report_root="pkg")
        self.assertEqual(r["missing"], [], r)
        self.assertIn("harvester-view", r["found"])
        self.assertEqual(found_total(r), 0, r)
        self.assertIn("harvester-view", render_deadcode(r))

    def test_report_root_can_be_the_sibling(self):
        """也能对兄弟仓库报发现（view 侧审计用）。"""
        backend = self.root / "backend"
        _make_fixture(backend, CLEAN)
        sibling = self.root / "harvester-view"
        sibling.mkdir()
        (sibling / "v.py").write_text("import os\n\n\ndef dead_in_view():\n"
                                      "    return 1\n", encoding="utf-8")
        r = scan(roots=("pkg", "tests", "harvester-view"), base=backend,
                 report_root="harvester-view")
        self.assertEqual(_mods(r), {"os"}, r["unused_imports"])
        self.assertEqual(_fns(r), {"dead_in_view"}, r["uncalled_functions"])


class TestRealRepoIsClean(unittest.TestCase):
    """把"每轮收尾的死代码扫描"钉进套件：**两个根都必须 0 条**。

    v0.39 之前只查 `harvester/`（工具默认的 `report_root`），于是 `tests/` 那一角
    **从没被查过**——补查一次就捞出 9 条未用 import。所以这里两个根都断言，
    否则同样会长回来。

    这条红的时候怎么做：真死 → 删；是给别人用的公开 API、或依赖标记（如
    `import yaml` 表示"缺 PyYAML 就该导入期失败"，H9）→ 在该行加 `# noqa` 并说明理由。
    """

    def test_harvester_and_tests_have_no_dead_code(self):
        for root in ("harvester", "tests"):
            r = scan(base=REPO, report_root=root)
            # v0.45：若扫描期间有文件被改动（并发编辑/子代理保存），本轮结论**不可信**
            # ——此时既不判红（那是假红，只冤枉代码），也不静默通过（那是把不确定当通过），
            # 而是**可区分的 skip**（unittest 摘要里以 skipped 出现）。机制与实测见
            # `harvester/deadcode.py::unstable_files`。
            if r.get("unstable"):
                self.skipTest(
                    f"{root}/ 有 {len(r['unstable'])} 个文件在扫描期间被改动 → 结论不可信，"
                    f"请等写入结束后重跑本门：{r['unstable'][:3]}")
            self.assertEqual(
                found_total(r), 0,
                f"{root}/ 出现死代码（删掉，或在该行加 `# noqa` 说明保留理由）：\n"
                + render_deadcode(r))


class TestDeadcodeCli(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def _run(self, *extra: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(REPO)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
        return subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "harvester", "deadcode-scan",
             "--root", "pkg", "--root", "tests", "--report-root", "pkg", *extra],
            cwd=str(self.root), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace")

    def test_fail_on_found_and_out(self):
        _make_fixture(self.root, DIRTY)
        r = self._run("--fail-on-found", "--out", "report.md")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        text = (self.root / "report.md").read_text(encoding="utf-8")
        self.assertIn("def dead_fn()", text)

    def test_clean_exits_zero_without_flag_and_with_flag(self):
        _make_fixture(self.root, CLEAN)
        for extra in ((), ("--fail-on-found",)):
            r = self._run(*extra)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("未发现死代码", r.stdout)


if __name__ == "__main__":
    unittest.main()
