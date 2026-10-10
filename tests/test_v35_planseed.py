# -*- coding: utf-8 -*-
"""v0.35 测试：分诊 → plan 草稿的工具化（`planseed`）。

**这个文件存在的理由**（用户 2026-10-10 第 1 点要求，第二轮强调）：v0.33/v0.34
两轮都把这一步写成了 `docs/reports/make-plan-*.py` 一次性脚本，而它**每轮采集/
起草都要重跑**。判断（哪些 sid 改判、哪些登记零散）仍由人/Agent 给（红线：语义
判断不落进代码），但**机械映射 + 完整性校验必须可复用**。

断言都指向具体失败：
- keep 覆盖不全 / 漏主题 → `validate_plan` 报"未归置的主题"；
- 判定类没归置却当通过 → `stats.unhandled` 与 `--require-covered` 退出码 2；
- 同一 sid 既改判又登记零散 → ValueError（自相矛盾）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harvester.consolidate import validate_plan
from harvester.noisetriage import render_brief
from harvester.planseed import (build_seed, dump_plan, load_judgment,
                                render_seed_stats, unhandled_sids)
from harvester.topics import ensure_topics_db, register_topic

ROOT = Path(__file__).resolve().parents[1]


def _row(sid: str, verdict: str, hint: str = "", text: str = "x") -> dict:
    return {"sid": sid, "verdict": verdict,
            "reason": f"命中主题关键词：{hint}" if hint else "理由",
            "created_at": "2026-01-01", "first_user": text, "title": "t"}


def _triage(rows: list[dict], **kw) -> dict:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    d = {"scanned": len(rows), "max_turns": 3, "counts": counts, "rows": rows}
    d.update(kw)
    return d


def _need_yaml() -> None:
    """缺 PyYAML 时**显式报错**（项目约定 H9），别让缺依赖长成断言失败。

    无 PyYAML 门禁跑出来必须是 errors（设计行为）；出现 failures 就说明有人把
    "环境缺依赖"写成了断言——那会把真回归淹掉。
    """
    from harvester.topicchain import _HAS_YAML
    if not _HAS_YAML:
        raise RuntimeError(
            "plan-seed 的 YAML 读写需要 PyYAML（venv 解释器，H9）；"
            "不提供降级解析——plan 读错比报错危险")


TOPICS = [{"id": "tp-A", "name": "甲主题", "keywords": []},
          {"id": "tp-B", "name": "乙主题", "keywords": []},
          {"id": "tp-C", "name": "丙主题", "keywords": []}]


class TestBuildSeed(unittest.TestCase):

    def test_topic_hint_maps_name_to_id_and_keep_covers_rest(self):
        t = _triage([_row("s:1", "topic_hint", hint="甲主题"),
                     _row("s:2", "topic_hint", hint="甲主题")])
        plan, _ = build_seed(t, TOPICS)
        self.assertEqual([a["target"] for a in plan["assign"]], ["tp-A"])
        self.assertEqual(plan["assign"][0]["sids"], ["s:1", "s:2"])
        # 完整性：每个主题要么是 assign 目标，要么在 keep 里
        self.assertEqual({k["id"] for k in plan["keep"]}, {"tp-B", "tp-C"})
        self.assertEqual(validate_plan(plan, {x["id"] for x in TOPICS}), [])

    def test_craft_material_goes_to_craft_topic_either_form(self):
        t = _triage([_row("s:1", "craft_material"), _row("s:2", "craft_material")])
        plan, _ = build_seed(t, TOPICS, craft_topic="tp-C")
        self.assertEqual(plan["assign"][0]["target"], "tp-C")
        self.assertEqual({k["id"] for k in plan["keep"]}, {"tp-A", "tp-B"})

        plan2, _ = build_seed(
            t, TOPICS,
            {"new_topics": [{"key": "M1", "name": "素材库", "keywords": ["描写"]}]},
            craft_topic="new:M1")
        self.assertEqual([n["key"] for n in plan2["new_topics"]], ["M1"])
        self.assertEqual(plan2["assign"][0]["new"], "M1")
        self.assertNotIn("target", plan2["assign"][0])
        self.assertEqual(validate_plan(plan2, {x["id"] for x in TOPICS}), [])

    def test_overrides_beat_the_mechanical_hint(self):
        t = _triage([_row("s:1", "topic_hint", hint="甲主题"),
                     _row("s:2", "topic_hint", hint="甲主题")])
        plan, _ = build_seed(t, TOPICS, {"overrides": {"s:2": "tp-B"}})
        by = {a.get("target"): a["sids"] for a in plan["assign"]}
        self.assertEqual(by["tp-A"], ["s:1"])          # 机械命中留下的
        self.assertEqual(by["tp-B"], ["s:2"])          # 改判的
        self.assertEqual({k["id"] for k in plan["keep"]}, {"tp-C"})

    def test_noise_and_skip_are_explicit_and_disjoint(self):
        t = _triage([_row("s:1", "noise_high"), _row("s:2", "noise_high"),
                     _row("s:3", "noise_high")])
        plan, stats = build_seed(t, TOPICS, {"noise": ["s:1"],
                                            "skip": [{"sid": "s:2", "why": "两可"}]})
        self.assertEqual([n["sid"] for n in plan["noise"]], ["s:1"])
        self.assertEqual(stats["noise_high"]["noise"], 1)
        self.assertEqual(stats["noise_high"]["skip"], 1)
        self.assertEqual(stats["noise_high"]["unhandled"], 1)
        self.assertEqual(unhandled_sids(stats, "noise_high"), ["s:3"])
        # skip 的 sid 不出现在 assign / noise 的任何位置
        flat = [s for a in plan["assign"] for s in a["sids"]]
        flat += [n["sid"] for n in plan["noise"]]
        self.assertNotIn("s:2", flat)

    def test_unhandled_is_reported_not_swallowed(self):
        """substantive / noise_maybe 缺省不动 —— 但必须在统计里看得见。"""
        t = _triage([_row("s:1", "substantive"), _row("s:2", "noise_maybe"),
                     _row("s:3", "noise_high", )])
        plan, stats = build_seed(t, TOPICS)
        self.assertEqual(stats["substantive"]["unhandled"], 1)
        self.assertEqual(stats["noise_maybe"]["unhandled"], 1)
        self.assertEqual(plan["assign"], [])
        self.assertIn("unhandled", render_seed_stats(stats))
        self.assertIn("不是通过", render_seed_stats(stats))

    def test_conflicting_or_ghost_judgment_fails_loud(self):
        t = _triage([_row("s:1", "noise_high")])
        for bad, why in (
                ({"overrides": {"s:1": "tp-A"}, "noise": ["s:1"]}, "既改判又零散"),
                ({"overrides": {"s:nope": "tp-A"}}, "sid 不在分诊结果"),
                ({"overrides": {"s:1": "tp-ghost"}}, "指向不存在的主题"),
                ({"overrides": {"s:1": "new:M9"}}, "未定义的 new key"),
                ({"noise": ["s:1"], "skip": ["s:1"]}, "既零散又 skip"),
        ):
            with self.assertRaises(ValueError, msg=why):
                build_seed(t, TOPICS, bad)

    def test_unknown_hint_name_fails_loud(self):
        """主题被改名/删除后，机械命中会指向不存在的名字 —— 必须报错而不是丢行。"""
        t = _triage([_row("s:1", "topic_hint", hint="已删除的主题")])
        with self.assertRaises(ValueError) as cm:
            build_seed(t, TOPICS)
        self.assertIn("不在注册表", str(cm.exception))

    def test_output_is_deterministic(self):
        _need_yaml()
        t = _triage([_row("s:2", "topic_hint", hint="乙主题"),
                     _row("s:1", "topic_hint", hint="甲主题"),
                     _row("s:3", "craft_material")])
        j = {"craft_topic": "tp-C", "noise": [], "skip": []}
        a = dump_plan(build_seed(t, TOPICS, j)[0])
        b = dump_plan(build_seed(t, TOPICS, j)[0])
        self.assertEqual(a, b)
        self.assertIn("assign:", a)


class TestRenderBrief(unittest.TestCase):

    def test_brief_is_not_truncated(self):
        """人读报告每类 60 条封顶；简报必须全量（这正是它存在的理由）。"""
        rows = [_row(f"s:{i:03d}", "noise_high", text="问词") for i in range(70)]
        rows += [_row("s:craft", "craft_material")]
        t = _triage(rows)
        brief = render_brief(t)
        self.assertEqual(brief.count("\n"), 1 + 72)          # 表头 + 全部行
        self.assertEqual(len([x for x in brief.splitlines()
                              if x.startswith("noise_high")]), 70)
        only = render_brief(t, "craft_material")
        self.assertEqual(len([x for x in only.splitlines()
                              if x.startswith("craft_material")]), 1)
        self.assertNotIn("noise_high\ts:", only)


class TestPlanSeedCli(unittest.TestCase):
    """真跑命令：seed 出来的 plan 必须能被 `--apply` 的 dry-run 接受。"""

    def setUp(self):
        _need_yaml()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.meta = ensure_topics_db(root / "topics_meta.db")
        self.a = register_topic(self.meta, "甲主题", keywords=["甲"])
        self.c = register_topic(self.meta, "丙主题", keywords=["丙"])
        self.triage = root / "triage.json"
        self.triage.write_text(json.dumps(_triage([
            _row("s:1", "topic_hint", hint="甲主题"),
            _row("s:2", "craft_material"),
            _row("s:3", "noise_high"),
        ]), ensure_ascii=False), encoding="utf-8")
        self.judgment = root / "judgment.yaml"
        self.judgment.write_text(
            "version: 1\ncraft_topic: " + self.c + "\nnoise:\n  - s:3\n",
            encoding="utf-8")
        self.out = root / "plan.yaml"

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def _run(self, *extra: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(ROOT)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
        return subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "harvester",
             "topic-consolidate", "--meta", str(self.meta),
             "--db", "harvester.db", *extra],
            cwd=str(ROOT), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace")

    def test_seed_then_apply_dry_run_passes(self):
        r = self._run("--plan-seed", str(self.triage), "--judgment",
                      str(self.judgment), "--seed-out", str(self.out))
        self.assertEqual(r.returncode, 0, r.stderr)
        text = self.out.read_text(encoding="utf-8")
        self.assertIn("覆盖统计", text)
        self.assertIn(f"target: {self.a}", text)
        self.assertIn(f"target: {self.c}", text)
        self.assertIn("sid: s:3", text)
        # ← 关键：seed 的产物直接交给 apply，dry-run 必须过
        r2 = self._run("--apply", str(self.out))
        self.assertEqual(r2.returncode, 0, r2.stderr + r2.stdout)
        self.assertIn("[dry-run] 校验通过", r2.stdout)

    def test_require_covered_blocks_unhandled(self):
        r = self._run("--plan-seed", str(self.triage), "--judgment",
                      str(self.judgment), "--require-covered", "noise_high")
        # 三行覆盖齐全（s:3 登记零散）→ 应当通过
        self.assertEqual(r.returncode, 0, r.stderr)
        # 去掉零散登记 → noise_high 有 1 条未归置 → 退出码 2 且点名 sid
        r2 = self._run("--plan-seed", str(self.triage),
                       "--require-covered", "noise_high")
        self.assertEqual(r2.returncode, 2)
        self.assertIn("s:3", r2.stderr)
        self.assertIn("--require-covered", r2.stderr)

    def test_seed_reports_bad_judgment_as_error(self):
        bad = Path(self.tmp.name) / "bad.yaml"
        bad.write_text("version: 1\nnoise:\n  - s:ghost\n", encoding="utf-8")
        r = self._run("--plan-seed", str(self.triage), "--judgment", str(bad))
        self.assertEqual(r.returncode, 2)
        self.assertIn("s:ghost", r.stderr)

    def test_load_judgment_rejects_unknown_shape(self):
        p = Path(self.tmp.name) / "shape.yaml"
        p.write_text("- just\n- a list\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_judgment(p)


if __name__ == "__main__":
    unittest.main()
