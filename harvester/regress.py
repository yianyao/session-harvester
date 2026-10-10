# -*- coding: utf-8 -*-
"""端到端回归语料（v0.43）——从空库跑完整条链路，逐项核对关键数字。

**为什么需要它**：`scan`→`triage`→`--plan-seed`→`topic-consolidate --apply`
→`topic export` 每一步都有自己的单测，但**没有一条"从空库开始把整条链路跑一遍、
把中间数字与产物都核对一遍"的端到端回归**。项目里有大量"改了 A 结果 B 悄悄变了"
的历史（H83 双出口漏条、H87 跨进程不确定），单测各自全绿也挡不住这类问题
——它们出在**步骤之间**。

**铁律：绝不碰真库。**
`run_regress(base)` 只在 `tempfile.TemporaryDirectory()` 里造库：
`harvester/indexing.SCHEMA` + `index_session` 建临时索引库（与
`tests/test_v30_noisetriage.py` 同一套夹具构造方式），meta 库直接写入**固定主题
id**（不用 `register_topic`：它的 id 带当天日期，会让"同一语料不同日期跑出不同
结果"）。跑完即删，真 `harvester.db` / `topics_meta.db` 一字节不动。

**能力进工具本体**（项目铁律）：这是 `harvester/regress.py` + CLI `regress`
+ 测试 + README 一节，不是 `docs/reports/` 下的一次性脚本——每次改链路
（分诊判据、plan-seed 映射、apply 校验、导出结构）都该重跑它。

## 跑哪几步、每步断言什么

| 步 | 断言（失败即回归） |
|---|---|
| `corpus` | 临时库建成：主题 3 个、会话 12 条、各会话 user 回合数与语料自述一致、预置成员就位 |
| `triage` | 浅分诊各判定类计数逐类对账；深分诊只多出 `deep_*` 两类且浅层结果逐 sid 不变（v0.39/v0.42 口径）；并列关键词会话的判定与归属被钉住 |
| `plan_seed` | 覆盖统计 `_total`、assign 逐主题成员、noise、显式 skip 不进 unhandled、keep 覆盖剩余主题、人读统计里 unhandled 可见 |
| `apply` | plan 过 `validate_plan`；dry-run 通过且不写库；真写库后成员数/主题数/零散登记与 plan 逐项一致 |
| `export` | `topic export` 的 `members_count` 与 meta 一致、`health` 为 0、JSON 可解析；`topic md` 非空且含主题名；产物落盘非空 |
| `determinism` | **H87**：`_topic_keyword_index` 的次序在**不同 PYTHONHASHSEED 的独立进程**里相同，且两次独立进程的分诊结果逐 sid 一致 |

## 退出码（照 `harvester-view/tests/check_real_payload.py` 的既有约定）

- `0` 全部通过（`status=pass`）
- `1` 有断言失败（`status=fail`）
- `3` **未执行**（`status=not_run`）：前置缺失（缺 PyYAML 等），**不是通过**；
  调用方据此 skip，而不是当成绿灯。
- `2` 用法错误（argparse 兜底）

报告里状态互斥且字面不同：`通过` / `失败` / `未执行`；另有 `不适用`
（`not_applicable`，仅"该步在当前配置下本就不跑"），与"未执行"分开。

## 明确没有覆盖（别以为买到了）

- **"长关键词优先"那条规则**：夹具里六个关键词都是 2 字，长度键实际没参与排序
  ——只测了"次序是输入的纯函数"，没测长度优先级。
- **`chain-audit` / `chain-validate`**：链路只到 `topic export`/`topic md`；
  写 chain 文那两道门仍只有各自的单测（`tests/test_v31_chainaudit.py`）。
- **性能**：不测分诊耗时（回归语料只有 12 条，快得没有意义；真库的 529 秒那类
  问题靠 `tests/test_v30_noisetriage.py` 的口径说明与人工实测，本工具不声称覆盖）。
- **真库状态**：全部在临时库上跑，真库的"删过主题 / 跳号 / 空洞"那类状态
  （见 `$DSH_HOME/AGENTS.md` §七 21）**不在**覆盖范围内。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from .indexing import SCHEMA, index_session
from .models import Message, SessionRecord

#: 步骤状态（**"未执行"与"通过"必须在报告与退出码里分得开**）
S_PASS = "pass"
S_FAIL = "fail"
S_NOT_RUN = "not_run"
S_NA = "not_applicable"

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_NOT_RUN = 0, 1, 2, 3

_STATUS_CN = {S_PASS: "通过", S_FAIL: "失败", S_NOT_RUN: "未执行",
              S_NA: "不适用"}

REPORT_VERSION = "harvester.regress/1"
#: 语料/期望的版本号。**改语料或改期望都要 +1**（报告的机读字段）。
CORPUS_VERSION = 1


# ── 语料与夹具 ─────────────────────────────────────────────────────────
class SessionSpec:
    """一条语料会话：sid / 标题 / user 回合原文（时间序）。"""

    __slots__ = ("sid", "title", "turns")

    def __init__(self, sid: str, title: str, turns: list[str]):
        self.sid = sid
        self.title = title
        self.turns = list(turns)


#: 语料会话的 source（索引库里的 sid = `source:session_id`）
CORPUS_SOURCE = "regress-raw"


def sid_of(session_id: str) -> str:
    """语料里的短 id → 索引库真实的 sid（`source:session_id`）。

    分诊/plan/judgment 全都用**完整 sid**——回归语料若用短 id，就测不到
    "跨源同名会话"这类真实形状（`index_session` 一律拼前缀）。
    """
    return f"{CORPUS_SOURCE}:{session_id}"


#: 语料会话的 session_id → 本步想要它钉住的东西（**报告里逐条印出来**：
#: 否则读报告的人无法判断"某类计数变了"是不是因为语料被改过）
CORPUS_NOTES = {
    "s01": "纯净零散：单轮 + 纯查询（「是多少」）+ 无整合诉求 → noise_high",
    "s02": "创作素材型：看着像查词，实为写作找料 → craft_material",
    "s03": "标题/首条命中主题关键词「创作」→ topic_hint（tp-B 乙创作法）",
    "s04": "多轮物质会话：含「分析/方案」→ substantive（judgment 显式 skip）",
    "s05": "多轮物质会话：含「梳理」→ substantive",
    "s06": "首条命中「仙侠」→ topic_hint（tp-A 甲小说线）",
    "s07": "命中「仙侠」→ topic_hint；**同时已是 tp-A 成员**（钉 member_owner 校验），"
           "judgment 用 overrides 改判进 tp-B",
    "s08": "查询型多轮（无整合诉求）→ noise_maybe；judgment 用 overrides 改判进 tp-A",
    "s09": "单轮查询但命中 craft → craft_material（craft 必须排在零散前）",
    "s10": "深会话 8 回合，命中「情节」→ deep_topic_hint",
    "s11": "深会话 5 回合，无主题命中 → deep_unassigned",
    "s12": "**H87 并列关键词**：首条同时含「江面」（tp-A）与「云帆」（tp-B），"
           "两个关键词等长 → 归属取决于机械次序 → 钉住跨进程确定性",
}


#: 可选的语料变体（`--corpus`）。**只有"故意漂移"这一种**：它多一条会话，
#: 而期望值是照内置语料写死的 → 必然红。存在的理由是"让这道门**可被证明会红**"：
#: CLI 是独立进程，测试没法 patch 它的 `EXPECT`，就用这个开关在真进程里制造
#: 一次真失败，验证"断言失败 → 退出码 1"这条路是通的。
CORPUS_VARIANTS = {
    "default": lambda: None,
    "drift": lambda: default_corpus() + [
        SessionSpec("x97", "漂移用会话", ["这是为了让期望值对不上"])],
}


def corpus_by_name(name: str) -> list[SessionSpec] | None:
    """`--corpus` 的名字 → 语料（未知名字由调用方报错，不静默回退默认）。"""
    if name == "default":
        return None
    return CORPUS_VARIANTS[name]()


def default_corpus() -> list[SessionSpec]:
    """内置固定语料：12 条会话，覆盖分诊每一档 + 深会话两类 + H87 并列命中。

    **改语料必须同步改 `EXPECT`**——计数对不上时整步失败。这是设计行为：
    语料与期望是一体的，不存在"半对"。
    """
    return [
        SessionSpec("s01", "汇率换算", ["100美元折合人民币是多少"]),
        SessionSpec("s02", "皱眉描写", ["描写皱眉动作的方法"]),
        SessionSpec("s03", "创作流程梳理", ["帮我创作一份第三章的叙事节拍表"]),
        SessionSpec("s04", "关税分析", ["分析一下这轮关税对出口的影响",
                                        "再给出一个应对方案"]),
        SessionSpec("s05", "工作流梳理", ["把这周的工作流梳理成一个规范",
                                          "顺便列出待办"]),
        SessionSpec("s06", "仙侠设定回顾", ["仙侠世界的境界划分该怎么设定"]),
        SessionSpec("s07", "仙侠设定集", ["仙侠世界的势力表该怎么排"]),
        SessionSpec("s08", "方差怎么算", ["查一下方差公式",
                                          "那标准差怎么算"]),
        SessionSpec("s09", "感谢动作", ["表示感谢时的简单而轻微的动作有哪些"]),
        SessionSpec("s10", "长篇小说节奏线", ["第8章的情节线要不要提前铺垫",
                                             "读者会不会觉得节奏太慢",
                                             "那第9章的冲突怎么加码",
                                             "支线要不要并进主线",
                                             "结尾的伏笔怎么收",
                                             "人物的动机还够不够",
                                             "有没有更好的结构",
                                             "定稿前还要改什么"]),
        SessionSpec("s11", "选情长谈", ["这次民调的样本怎么选",
                                        "街访数据要不要加权",
                                        "投票率怎么估",
                                        "摇摆州怎么判断",
                                        "最后怎么对外说明"]),
        SessionSpec("s12", "江面与云帆", ["江面和云帆这两个意象哪个更合适"]),
    ]


#: meta 夹具：[主题 id, 名称, 关键词, 预置成员]
#: 名称带 ASCII 前缀（`A甲小说线` / `B乙创作法` / `C丙心理`），**故意的**：
#: `_topic_keyword_index` 的次级排序键是**主题名**，而 Python 的字符串比较按
#: Unicode 码位——「乙」比「甲」小，不带前缀的话"名称序"在码位意义上与
#: 直觉相反，断言自己都会绕晕。带 ASCII 前缀后，"A < B < C" 既是码位序也是
#: 直觉序，谁把次级键改掉都藏不住。
#: tp-A 的预置成员 s07 用来钉 `member_owner` 校验：它机械命中 tp-A，
#: judgment 又把它改判到 tp-B，两条路都真实走一遍。
TOPIC_FIXTURE = [
    ("tp-A", "A甲小说线", ["仙侠", "江面", "情节"], ["s07"]),
    ("tp-B", "B乙创作法", ["创作", "云帆"], []),
    ("tp-C", "C丙心理", ["心理"], []),
]

EXPECT = {
    "sessions": 12,
    "topics": 3,
    # 索引库里的 sid 一律带 source 前缀（`index_session` 的口径）
    "sids": sorted(sid_of(s.sid) for s in default_corpus()),
    "turns": {sid_of(s.sid): len(s.turns) for s in default_corpus()},
    "deep_turns": {sid_of(s.sid): len(s.turns) for s in default_corpus()
                   if len(s.turns) > 3},
    # 浅分诊（--triage-max-turns 3，不含深会话）各判定类计数
    "shallow_counts": {"noise_high": 1, "craft_material": 2,
                       "topic_hint": 4, "noise_maybe": 1, "substantive": 2},
    "deep_counts": {"deep_topic_hint": 1, "deep_unassigned": 1},
    "deep_sids": [sid_of("s10"), sid_of("s11")],
    # 并列关键词命中：「江面」(tp-A) 与「云帆」(tp-B) 等长，且**同长序里
    # tp-A 的名称在前** → 「江面」先命中，但 s12 的判定由**分诊**按同一索引给出，
    # 关键不是谁赢，而是"每次跑都赢同一个"。若有人把 `_topic_keyword_index`
    # 的排序键去掉/改掉，triage 与 determinism 两步同时红。
    "tie_verdict": {"sid": sid_of("s12"), "target": "tp-A", "name": "A甲小说线"},
    "tie_keywords": [["江面", "A甲小说线"], ["云帆", "B乙创作法"]],
    # `_topic_keyword_index` 的**全序**（按 (-len(kw), 主题名, kw)）：
    # 本夹具关键词都是 2 字 → **长度键不参与**，实际次序 = 主题名 → 关键词码位。
    # 钉住整条序列（不只钉并列那一对）：有人加/删关键词或改排序键，这里当场红。
    # ⚠ 未覆盖："长关键词优先"这条规则（夹具里没有不同长度的关键词）——
    #    见模块 docstring「明确没有覆盖」。
    "keyword_order": [["仙侠", "A甲小说线"], ["情节", "A甲小说线"],
                      ["江面", "A甲小说线"], ["云帆", "B乙创作法"],
                      ["创作", "B乙创作法"], ["心理", "C丙心理"]],
    # 浅池 = user 回合 ≤ 3 的全部会话（s01–s09 + s12）
    "shallow_only": sorted([sid_of("s" + f"{i:02d}") for i in range(1, 10)]
                           + [sid_of("s12")]),
    # plan-seed（judgment 见 judgment_text）
    # tp-A：s06「仙侠」/ s10「情节」机械命中 + s12「江面」并列命中赢了 + s11 改判
    # tp-B：s03/s07 机械命中「创作」+ s02/s09 整类 craft 归入
    # s05/substantive 与 s08/noise_maybe **故意不归置**：机械映射覆盖不到它们，
    # 必须留在 stats 的 unhandled 里（真库那半池子的缩影，"未处理 ≠ 通过"）。
    "plan_assign": {
        "tp-A": sorted([sid_of("s06"), sid_of("s10"), sid_of("s11"),
                        sid_of("s12")]),
        "tp-B": sorted([sid_of("s02"), sid_of("s03"), sid_of("s07"),
                        sid_of("s09")])},
    "plan_noise": [sid_of("s01")],
    "plan_skip": [sid_of("s04")],
    "plan_unhandled": {"noise_maybe": 1, "substantive": 1},
    "plan_stats_totals": {"rows": 12, "assign": 8, "noise": 1, "skip": 1},
    # apply 之后（tp-A 原有 1 个成员 s07，被 overrides 改判进 tp-B）
    "members_after": {"tp-A": 5, "tp-B": 4, "tp-C": 0},
    "noise_registered": [sid_of("s01")],
    # H87：独立进程跑同一 meta，次序必须相同
    "hash_seeds": ["1", "7", "12345"],
    "triage_seeds": ["1", "424242"],
}


def judgment_text() -> str:
    """固定 judgment（YAML 文本）——**语义判断在这里显式写死**，
    工具只做机械映射（红线：判断不落进代码）。

    三条判断各钉一件事：
    - `s07` 用 overrides 从机械命中的 tp-A 改判到 tp-B → 证明改判真的盖过机械命中
      （且它是 tp-A 的**已有成员**，顺带走一遍 `member_owner` 校验）；
    - `craft_topic: tp-B` 把 craft 整类归位（s02/s09）；`s11` 从 deep_unassigned
      改判进 tp-A → 逐条 overrides 与整类归位两条路都真跑过；
    - `s01` 登记零散；`s04` 显式 skip——skip 与"忘了填"必须分得开。

    **不给 s09 既 skip 又 craft_topic**：那是自相矛盾的 judgment（工具按
    skip 优先处理），把它当语料会把"自相矛盾"当成"正常行为"钉进回归。
    """
    return (
        "version: 1\n"
        "craft_topic: tp-B\n"
        "overrides:\n"
        f"  {sid_of('s07')}: tp-B\n"
        f"  {sid_of('s11')}: tp-A\n"
        "noise:\n"
        f"  - {sid_of('s01')}\n"
        "skip:\n"
        f"  - {{sid: {sid_of('s04')}, why: 本轮人工看过，暂不动}}\n"
    )


def build_fixture(base: Path | str, corpus: list[SessionSpec] | None = None
                  ) -> dict:
    """在 `base` 下造临时索引库 + meta 库，返回路径与语料。

    **只用 tests/ 里已有的夹具构造方式**（`indexing.SCHEMA` + `index_session`），
    不另起一套造库代码：临时库与测试库结构一致，回归才有意义。
    """
    base = Path(base)
    db, meta = base / "harvester.db", base / "topics_meta.db"
    corpus = list(corpus if corpus is not None else default_corpus())
    con = sqlite3.connect(str(db))
    try:
        con.executescript(SCHEMA)
        for s in corpus:
            index_session(con, SessionRecord(
                source="regress-raw", session_id=s.sid, title=s.title,
                created_at="2026-01-01 10:00:00",
                updated_at="2026-01-01 10:00:00",
                messages=[Message(role="user", text=t) for t in s.turns]
                + [Message(role="assistant", text="答")]))
        con.commit()
    finally:
        con.close()

    mcon = sqlite3.connect(str(meta))
    try:
        mcon.executescript(
            "CREATE TABLE IF NOT EXISTS topics (id TEXT PRIMARY KEY, "
            "name TEXT NOT NULL UNIQUE, keywords TEXT NOT NULL DEFAULT '[]', "
            "members TEXT NOT NULL DEFAULT '[]', created TEXT NOT NULL);")
        for tid, name, kws, members in TOPIC_FIXTURE:
            mcon.execute(
                "INSERT INTO topics (id, name, keywords, members, created) "
                "VALUES (?,?,?,?,?)",
                (tid, name, json.dumps(kws, ensure_ascii=False),
                 json.dumps([{"sid": s, "evidence": "fixture"} for s in members],
                            ensure_ascii=False),
                 "2026-01-01 10:00"))
        mcon.commit()
    finally:
        mcon.close()
    return {"db": db, "meta": meta, "corpus": corpus}


def _ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _corpus_shape(db: Path, meta: Path) -> dict:
    con = _ro(db)
    try:
        sids = [r[0] for r in con.execute("SELECT sid FROM sessions ORDER BY sid")]
        turns = {r["sid"]: r["n"] for r in con.execute(
            "SELECT sid, COUNT(*) n FROM messages WHERE role='user' GROUP BY sid")}
    finally:
        con.close()
    m = _ro(meta)
    try:
        topics = m.execute("SELECT COUNT(*) FROM topics").fetchone()[0]
        members = [x["sid"] for x in json.loads(
            m.execute("SELECT members FROM topics WHERE id='tp-A'").fetchone()[0])]
    finally:
        m.close()
    return {"sids": sids, "turns": turns, "topics": topics, "members": members}


# ── 步骤/断言容器 ──────────────────────────────────────────────────────
def _jsonable(v):
    """把断言值收敛成**可 JSON 序列化、且确定有序**的形状。

    set/frozenset → 排序后的 list：机读出口（`--json`）要能直接落盘，
    `{"a","b"}` 与 `["a","b"]` 在"相等"这件事上等价，但只有后者能序列化
    （首版没做，`--json` 当场 TypeError）。
    """
    if isinstance(v, (set, frozenset)):
        return sorted((_jsonable(x) for x in v), key=lambda x: json.dumps(
            x, ensure_ascii=False, sort_keys=True, default=str))
    if isinstance(v, tuple):
        return [_jsonable(x) for x in v]
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in sorted(v.items(), key=str)}
    return v


class Step:
    """一步的断言集合。`expected` / `actual` 都写成人能核对的形状。"""

    def __init__(self, name: str, what: str):
        self.name = name
        self.what = what
        self.checks: list[dict] = []
        self.status = S_PASS
        self.why = ""

    def check(self, name: str, expected, actual) -> bool:
        expected, actual = _jsonable(expected), _jsonable(actual)
        ok = expected == actual
        self.checks.append({"name": name, "expected": expected,
                            "actual": actual, "ok": ok})
        if not ok:
            # **断言失败必须当场把整步标红**。首版只在 `_finish` 里按步的
            # 显式 mark 汇总，于是"某条断言不相等但没人 mark"会让整份报告
            # 在 `failed_assertions=1` 的同时结论写"通过"（自测当场抓到）。
            self.mark(S_FAIL)
        return ok

    @property
    def failed(self) -> list[dict]:
        return [c for c in self.checks if not c["ok"]]

    def mark(self, status: str, why: str = "") -> None:
        """整步标成非 pass。**失败不会被后续的"未执行"覆盖**。"""
        if status == S_FAIL or self.status == S_PASS:
            self.status = status
        if why:
            self.why = why

    def to_dict(self) -> dict:
        return {"step": self.name, "what": self.what, "status": self.status,
                "why": self.why, "checks": self.checks,
                "failed": [c["name"] for c in self.failed]}


def _by_sid(rows: list[dict]) -> dict:
    return {r["sid"]: r for r in rows}


def _safe(step: Step, fn, *a, **kw):
    """跑被测代码；异常 → **本步失败并带类型与消息**（绝不静默吞掉）。"""
    try:
        return fn(*a, **kw)
    except Exception as exc:                         # noqa: BLE001 —— fail loud
        step.mark(S_FAIL, f"抛异常: {type(exc).__name__}: {exc}")
        return None


# ── 主流程 ─────────────────────────────────────────────────────────────
def run_regress(base: Path | str, corpus: list[SessionSpec] | None = None,
                keep_temp: bool = False) -> dict:
    """在临时库上跑完整条链路，返回机读结果 dict。

    `base`：临时目录的存放父目录（配 `keep_temp=True` 时人工翻查用）。
    `corpus`：自定义语料（缺省内置 12 条）；**改语料就会让计数断言失败**。
    `keep_temp`：保留临时目录（报告里给出路径），缺省跑完即删。
    """
    base = Path(base)
    tmp = None
    if keep_temp:
        base.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="regress-", dir=str(base)))
    else:
        tmp = tempfile.TemporaryDirectory(prefix="regress-")
        work = Path(tmp.name)
    steps: list[Step] = []
    try:
        pre = _step_precondition()
        steps.append(pre)
        if pre.status == S_PASS:
            st1 = _step_corpus(work, corpus)
            steps.append(st1)
            if st1.status == S_PASS:
                steps.append(_step_triage(work))
                seed = _step_plan_seed(work)
                steps.append(seed["step"])
                steps.append(_step_apply(work, seed))
                steps.append(_step_export(work))
                steps.append(_step_determinism(work))
            else:
                steps += _blocked_chain([st1])
        else:
            # 前置缺失（缺 PyYAML）：链路后半段**整段未执行**——
            # 不许把"跑都没跑"报成通过（AGENTS.md §五 16 条）
            steps += _blocked_chain([pre])
    finally:
        if tmp is not None:
            tmp.cleanup()
    return _finish(steps, work, keep_temp, corpus)


#: 链路里"会被前置带塌"的步骤（名字 + 做什么），供未执行时逐条登记
_CHAIN_STEPS = (("triage", "分诊（含 --triage-deep）各判定类计数"),
                ("plan_seed", "plan-seed 覆盖计数与逐 sid 归属"),
                ("apply", "apply：dry-run 与真写库逐项对账"),
                ("export", "topic export / topic md 产物核对"),
                ("determinism", "H87 并列关键词跨进程确定性"))


def _blocked_chain(prev: list[Step]) -> list[Step]:
    return [_blocked(name, what, prev) for name, what in _CHAIN_STEPS]


def _blocked(name: str, what: str, prev: list[Step]) -> Step:
    st = Step(name, what)
    st.mark(S_NOT_RUN, "前置未成功（" + "、".join(
        f"{s.name}:{_STATUS_CN[s.status]}" for s in prev
        if s.status != S_PASS) + "）——本步**未执行**，不是通过")
    return st


def _step_precondition() -> Step:
    st = Step("precondition", "链路前置：PyYAML（plan / apply 的必需依赖）")
    # 探测口径与 `tests/test_v35_planseed.py::_need_yaml` 一致：读
    # `topicchain._HAS_YAML`。缺依赖时**不记断言失败**——它是"未执行"
    # 而不是"工具坏了"：记成 failed check 会让结论变成 `fail`/退出码 1，
    # 调用方就分不出"环境缺依赖"与"真回归"（AGENTS.md §五 16 条）。
    from .topicchain import _HAS_YAML
    if not _HAS_YAML:
        st.mark(S_NOT_RUN, "缺 PyYAML（import yaml 失败）→ 链路后半段"
                           "**未执行**，不是通过")
        return st
    st.check("PyYAML 可用", True, True)
    return st


def _step_corpus(work: Path, corpus: list[SessionSpec] | None) -> Step:
    st = Step("corpus", "临时库建成：12 条会话 + 3 个固定 id 主题（真库不碰）")
    fx = _safe(st, build_fixture, work, corpus)
    if not isinstance(fx, dict):
        return st
    shape = _safe(st, _corpus_shape, fx["db"], fx["meta"])
    if not isinstance(shape, dict):
        return st
    st.check("会话数", EXPECT["sessions"], len(shape["sids"]))
    st.check("session id 集合", EXPECT["sids"], shape["sids"])
    st.check("各会话 user 回合数", EXPECT["turns"], shape["turns"])
    st.check("主题数", EXPECT["topics"], shape["topics"])
    st.check("tp-A 预置成员", ["s07"], shape["members"])
    st.check("索引库落在临时目录里（不是真 harvester.db）", True,
             work.resolve() in fx["db"].resolve().parents)
    return st


def _step_triage(work: Path) -> Step:
    from .noisetriage import triage
    st = Step("triage", "分诊（含 --triage-deep）：判定类计数 + 深/浅口径")
    db, meta = work / "harvester.db", work / "topics_meta.db"
    shallow = _safe(st, triage, db, meta, max_turns=3)
    deep = _safe(st, triage, db, meta, max_turns=3, include_deep=True)
    if not isinstance(shallow, dict) or not isinstance(deep, dict):
        return st
    st.check("浅分诊判定计数", EXPECT["shallow_counts"], dict(shallow["counts"]))
    st.check("浅分诊池子总数", len(EXPECT["shallow_only"]), shallow["scanned"])
    st.check("深分诊只多出 deep_* 两类", DEEP_VERDICTS,
             sorted(k for k in deep["counts"] if k.startswith("deep_")))
    st.check("深会话两类计数", EXPECT["deep_counts"],
             {k: v for k, v in deep["counts"].items() if k.startswith("deep_")})
    deep_sids = sorted(r["sid"] for r in deep["rows"]
                       if r["verdict"].startswith("deep_"))
    st.check("深会话 sid", EXPECT["deep_sids"], deep_sids)
    # 口径（v0.39/v0.42）：深会话**只增两类**，浅层逐 sid 判定一字不改；
    # 池子总数 = 浅 + 深（既不漏，也不双计）
    light = {r["sid"] for r in shallow["rows"]}
    st.check("浅层逐 sid 判定在含深模式下不变",
             _by_sid(shallow["rows"]),
             {k: v for k, v in _by_sid(deep["rows"]).items() if k in light})
    st.check("池子总数 = 浅 + 深",
             shallow["scanned"] + len(deep_sids), deep["scanned"])
    # 并列关键词会话（H87 的根因场景）在分诊里的判定与归属必须钉住
    row = _by_sid(deep["rows"]).get(EXPECT["tie_verdict"]["sid"])
    st.check("并列关键词会话的判定", "topic_hint",
             row["verdict"] if row else None)
    st.check("并列关键词会话的归属",
             EXPECT["tie_verdict"]["name"],
             (row["reason"].split("命中主题关键词：", 1)[1].strip()
              if row else None))
    # 语料自述：每种判定都得有代表（否则"覆盖"是假的）
    st.check("语料覆盖全部 7 档判定", 7, len(deep["counts"]))
    st.check("深会话条数与语料自述一致（不是靠猜 max_turns）",
             EXPECT["deep_turns"],
             {r["sid"]: r["turns"] for r in deep["rows"]
              if r["verdict"].startswith("deep_")})
    return st


#: 深会话两类的名字（口径变了 = 这里红）
DEEP_VERDICTS = ["deep_topic_hint", "deep_unassigned"]


def _step_plan_seed(work: Path) -> dict:
    from .noisetriage import triage
    from .planseed import build_seed, load_judgment, render_seed_stats
    from .topics import list_topics
    st = Step("plan_seed", "plan-seed（固定 judgment）：覆盖计数 + 逐 sid 归属")
    db, meta = work / "harvester.db", work / "topics_meta.db"
    t = _safe(st, triage, db, meta, max_turns=3, include_deep=True)
    jf = work / "judgment.yaml"
    jf.write_text(judgment_text(), encoding="utf-8", newline="\n")
    j = _safe(st, load_judgment, jf)
    if not isinstance(t, dict) or not isinstance(j, dict):
        return {"step": st, "plan": None}
    got = _safe(st, build_seed, t, list_topics(meta), j)
    if not isinstance(got, tuple):
        return {"step": st, "plan": None}
    plan, stats = got
    st.check("覆盖统计 _total", EXPECT["plan_stats_totals"],
             stats.get("_total"))
    assign = {a.get("target") or f"new:{a.get('new')}": sorted(a["sids"])
              for a in plan["assign"]}
    st.check("assign 逐主题成员", EXPECT["plan_assign"], assign)
    st.check("noise 登记", EXPECT["plan_noise"],
             [n["sid"] for n in plan["noise"]])
    st.check("未归置（unhandled）逐类计数", EXPECT["plan_unhandled"],
             {v: s["unhandled"] for v, s in stats.items()
              if not v.startswith("_") and s["unhandled"]})
    # 显式 skip 的 sid **不属于**"未归置"：skip 是已判断，unhandled 是没判断
    unhandled_all = sorted(s for v, s in stats.items() if not v.startswith("_")
                           for s in (s.get("unhandled_sids") or []))
    st.check("显式 skip 的 sid 不进 unhandled_sids", [],
             sorted(set(unhandled_all) & set(EXPECT["plan_skip"])))
    st.check("unhandled_sids 的条数 = 各类 unhandled 之和",
             sum(s["unhandled"] for v, s in stats.items()
                 if not v.startswith("_")), len(unhandled_all))
    # 完整性：keep 必须覆盖除 assign 目标之外的全部主题
    used = {a["target"] for a in plan["assign"] if a.get("target")}
    st.check("keep 覆盖剩余主题",
             sorted({x["id"] for x in list_topics(meta)} - used),
             sorted(k["id"] for k in plan["keep"]))
    text = render_seed_stats(stats)
    st.check("人读统计里 unhandled 可见", True, "unhandled" in text)
    st.check("人读统计写明『不是通过』", True, "不是通过" in text)
    (work / "plan.yaml").write_text(
        render_seed_stats(stats) + "\n", encoding="utf-8", newline="\n")
    return {"step": st, "plan": plan, "stats": stats}


def _step_apply(work: Path, seed: dict) -> Step:
    from .consolidate import apply_plan, list_noise, validate_plan
    from .topics import list_topics, show_topic
    st = Step("apply", "apply：dry-run 通过 → 真写库 → 成员/主题/零散逐项对账")
    meta = work / "topics_meta.db"
    plan = seed.get("plan")
    if not isinstance(plan, dict):
        return _blocked("apply", st.what, [seed["step"]])
    have = {t["id"] for t in list_topics(meta)}
    st.check("plan 过 validate_plan（完整性 + 冲突）", [],
             _safe(st, validate_plan, plan, have))
    dry = _safe(st, apply_plan, meta, plan, dry_run=True)
    if not isinstance(dry, dict):
        return st
    st.check("dry-run 通过", True, bool(dry["ok"]))
    st.check("dry-run 不写库", False, bool(dry["applied"]))
    before = {t["id"]: t["members"] for t in list_topics(meta)}
    res = _safe(st, apply_plan, meta, plan, dry_run=False)
    if not isinstance(res, dict):
        return st
    st.check("真写库成功", True, bool(res["ok"]))
    st.check("applied 标记为真", True, bool(res["applied"]))
    after = {t["id"]: t["members"] for t in list_topics(meta)}
    st.check("主题数不增不减", len(before), len(after))
    st.check("apply 后各主题成员数", EXPECT["members_after"], after)
    want = sorted(s for a in plan["assign"] for s in a["sids"])
    got = sorted(m["sid"] for tid in after
                 for m in show_topic(meta, tid)["members"])
    st.check("成员表 = plan 期望的成员（含预置 s07，无重复无遗漏）",
             sorted(want + [s for s in shape_members() if s not in want]), got)
    st.check("零散登记", EXPECT["noise_registered"],
             [r["sid"] for r in list_noise(meta)])
    st.check("零散与成员不相交（自相矛盾检查）", set(),
             {r["sid"] for r in list_noise(meta)} & set(got))
    st.check("plan 里 assign 的 sid 全部真的进了成员表", [],
             sorted(set(want) - set(got)))
    return st


def shape_members() -> list[str]:
    """夹具里预置的成员（tp-A 的 s07）。"""
    return [s for _tid, _n, _k, ms in TOPIC_FIXTURE for s in ms]


def _step_export(work: Path) -> Step:
    from .consolidate import noise_sids
    from .topicexport import render_topic_json, render_topic_md, topic_bundle
    st = Step("export", "topic export / topic md：产物存在非空 + 计数与 meta 一致")
    db, meta = work / "harvester.db", work / "topics_meta.db"
    for tid in ("tp-A", "tp-B"):
        b = _safe(st, topic_bundle, meta, db, tid, chain_root=None,
                  noise_sids=noise_sids(meta))
        if not isinstance(b, dict):
            return st
        st.check(f"{tid} 导出成员数与 meta 一致", EXPECT["members_after"][tid],
                 b["topic"]["members_count"])
        st.check(f"{tid} health：成员里无零散登记", 0,
                 (b.get("health") or {}).get("noise_members", 0))
        text = _safe(st, render_topic_json, b)
        if not isinstance(text, str):
            return st
        parsed = None
        try:
            parsed = json.loads(text)
        except ValueError as exc:
            st.check(f"{tid} JSON 可解析", True, f"解析失败: {exc}")
        if parsed is not None:
            st.check(f"{tid} 机读出口是 schema 化的 JSON", True,
                     parsed.get("schema") == "harvester.topic/1")
            st.check(f"{tid} JSON 里的成员条数与 meta 一致",
                     EXPECT["members_after"][tid], len(parsed.get("members")))
        st.check(f"{tid} JSON 非空", True, len(text) > 200)
        (work / f"topic-{tid}.json").write_text(text, encoding="utf-8",
                                                newline="\n")
        md = _safe(st, render_topic_md, b, chain_root=None)
        if not isinstance(md, str):
            return st
        st.check(f"{tid} 人读 md 非空且含主题名", True,
                 len(md) > 100 and b["topic"]["name"] in md)
        (work / f"topic-{tid}.md").write_text(md, encoding="utf-8",
                                              newline="\n")
        st.check(f"{tid} 两个产物都已落盘且非空", True,
                 (work / f"topic-{tid}.json").stat().st_size > 0
                 and (work / f"topic-{tid}.md").stat().st_size > 0)
    return st


#: 独立进程探针：把 `_topic_keyword_index` 的输出原样打成 JSON。
#: **必须另起进程**——H87 的根因正是"同一库同一输入、两次独立进程可能不同"，
#: 同进程内比较看不到字符串哈希随机化。
_PROBE = (
    "import json,sys\n"
    "from pathlib import Path\n"
    "from harvester.noisetriage import _topic_keyword_index\n"
    "print(json.dumps(_topic_keyword_index(Path(sys.argv[1])),\n"
    "                 ensure_ascii=False))\n"
)

_TRIAGE_PROBE = (
    "import json,sys\n"
    "from pathlib import Path\n"
    "from harvester.noisetriage import triage\n"
    "t = triage(Path(sys.argv[2]), Path(sys.argv[1]), max_turns=3)\n"
    "print(json.dumps([[r['sid'], r['verdict'], r['reason']]\n"
    "                  for r in t['rows']], ensure_ascii=False))\n"
)


def _probe_env(seed: str) -> dict:
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = seed
    pp = str(Path(__file__).resolve().parents[1])
    if env.get("PYTHONPATH"):
        pp = os.pathsep.join([pp, env["PYTHONPATH"]])
    env["PYTHONPATH"] = pp
    return env


def _run_probe(work: Path, name: str, src: str, args: list[str],
               seed: str) -> tuple[str, str]:
    p = work / name
    p.write_text(src, encoding="utf-8", newline="\n")
    r = subprocess.run([sys.executable, "-X", "utf8", str(p)] + args,
                       cwd=str(work), env=_probe_env(seed), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return r.stdout.strip(), (r.stderr or "").strip()[-300:]


def _step_determinism(work: Path) -> Step:
    st = Step("determinism", "H87：并列关键词的机械次序与判定跨进程一致")
    meta, db = work / "topics_meta.db", work / "harvester.db"
    outs = {}
    for seed in EXPECT["hash_seeds"]:
        out, err = _run_probe(work, "_probe_keyword_order.py", _PROBE,
                              [str(meta)], seed)
        outs[seed] = out
        if not out:
            st.check(f"探针(PYTHONHASHSEED={seed})有输出", True,
                     f"无输出；stderr：{err or '（空）'}")
            return st
    seen = {}
    base = outs[EXPECT["hash_seeds"][0]]
    for seed in EXPECT["hash_seeds"]:
        seen[seed] = hashlib.sha256(outs[seed].encode()).hexdigest()[:12]
    st.check("不同 PYTHONHASHSEED 的关键词次序相同",
             [seen[EXPECT["hash_seeds"][0]]] * len(seen),
             [seen[s] for s in EXPECT["hash_seeds"]])
    st.check("关键词全序 = (-长度, 主题名, 关键词) 的纯函数",
             EXPECT["keyword_order"], json.loads(base))
    # 端到端那一半：**必须用分诊前（apply 之前）的 meta**——apply 之后
    # 大部分会话已成成员，会被分诊按"不属于任何主题"的口径排除掉，
    # 探针就只剩几行，"跨进程一致"也就成了空断言（首次实现即踩到这个）。
    pre = work / "determinism"
    pre.mkdir(exist_ok=True)
    fxp = _safe(st, build_fixture, pre)
    if not isinstance(fxp, dict):
        return st
    touts = {}
    for seed in EXPECT["triage_seeds"]:
        out, err = _run_probe(work, "_probe_triage_order.py", _TRIAGE_PROBE,
                              [str(fxp["meta"]), str(fxp["db"])], seed)
        if not out:
            st.check(f"分诊探针(PYTHONHASHSEED={seed})有输出", True,
                     f"无输出；stderr：{err or '（空）'}")
            return st
        touts[seed] = out
    st.check("两次独立进程的分诊结果逐 sid 相同",
             touts[EXPECT["triage_seeds"][0]],
             touts[EXPECT["triage_seeds"][1]])
    rows = json.loads(touts[EXPECT["triage_seeds"][0]])
    st.check("分诊探针看到整个浅池（不是被 apply 掏空的残余）",
             len(EXPECT["shallow_only"]), len(rows))
    st.check("独立进程里并列会话仍归同一主题",
             EXPECT["tie_verdict"]["name"],
             _hint_of(rows, EXPECT["tie_verdict"]["sid"]))
    return st


def _hint_of(rows: list, sid: str) -> str | None:
    for r in rows or []:
        if r[0] == sid:
            reason = r[2] or ""
            marker = "命中主题关键词："
            return reason.split(marker, 1)[1].strip() if marker in reason else None
    return None


def _finish(steps: list[Step], work: Path, keep_temp: bool,
            corpus: list[SessionSpec] | None) -> dict:
    counts = {s: sum(1 for x in steps if x.status == s)
              for s in (S_PASS, S_FAIL, S_NOT_RUN, S_NA)}
    if counts[S_FAIL]:
        status, code = S_FAIL, EXIT_FAIL
    elif counts[S_NOT_RUN]:
        status, code = S_NOT_RUN, EXIT_NOT_RUN
    else:
        status, code = S_PASS, EXIT_OK
    bad = [s for s in steps if s.status != S_PASS]
    return {
        "version": REPORT_VERSION,
        "corpus_version": CORPUS_VERSION,
        "status": status,
        "status_cn": _STATUS_CN[status],
        "exit_code": code,
        "why": "; ".join(f"{s.name}: {s.why or s.failed}"
                         for s in bad) if bad else "",
        "counts": counts,
        "assertions": sum(len(s.checks) for s in steps),
        "failed_assertions": sum(len(s.failed) for s in steps),
        "temp_dir": str(work) if keep_temp else None,
        "corpus": [{"sid": s.sid, "title": s.title, "turns": len(s.turns),
                    "note": CORPUS_NOTES.get(s.sid, "")}
                   for s in (corpus if corpus is not None else default_corpus())],
        "steps": [s.to_dict() for s in steps],
    }


# ── 出口 ───────────────────────────────────────────────────────────────
def render_report(r: dict) -> str:
    """人读报告（机读走返回值 / `dump_json`）。

    每步先给判定，再逐条列 expected vs actual——"期望值/实测值"是这份报告
    存在的理由：只写"通过"的报告挡不住"改了 A 结果 B 悄悄变了"。
    """
    L = ["# 端到端回归（regress）", "",
         f"- 结论：**{r['status_cn']}**（退出码 {r['exit_code']}）｜"
         f"断言 {r['assertions']} 条，失败 {r['failed_assertions']} 条｜"
         f"步骤：通过 {r['counts'][S_PASS]} / 失败 {r['counts'][S_FAIL]} / "
         f"未执行 {r['counts'][S_NOT_RUN]} / 不适用 {r['counts'][S_NA]}",
         f"- 语料版本 {r['corpus_version']}｜会话 {len(r['corpus'])} 条"
         "（每条钉住什么见下表）",
         "- 跑法：**临时库**（`indexing.SCHEMA` + `index_session`），"
         "真 `harvester.db` / `topics_meta.db` 一字节不动"
         + (f"｜临时目录：`{r['temp_dir']}`（--keep-temp）"
            if r.get("temp_dir") else "｜临时目录已删除")]
    L += ["", "## 语料（每条钉住什么）", "",
          "| sid | 回合 | 标题 | 钉住的东西 |", "|---|---|---|---|"]
    for c in r["corpus"]:
        L.append(f"| `{c['sid']}` | {c['turns']} | {c['title']} | {c['note']} |")
    for s in r["steps"]:
        L += ["", f"## {s['step']}　{_STATUS_CN[s['status']]}", "",
              f"- 做什么：{s['what']}"]
        if s["why"]:
            L.append(f"- 说明：{s['why']}")
        if not s["checks"]:
            L.append("- （本步没有断言：**未执行 ≠ 通过**）")
            continue
        L += ["", "| 断言 | 期望 | 实测 | 判定 |", "|---|---|---|---|"]
        for c in s["checks"]:
            L.append(f"| {c['name']} | `{_fmt(c['expected'])}` | "
                     f"`{_fmt(c['actual'])}` | {'通过' if c['ok'] else '**失败**'} |")
    L += ["", "---", "",
          "退出码：`0` 全通过 / `1` 有断言失败 / `3` **未执行**（前置缺失，"
          "不是通过）/ `2` 用法错误。", ""]
    return "\n".join(L)


def _fmt(v) -> str:
    """断言值 → 一行可读文本（表格里不许换行、不许撑爆）。

    非 JSON 可序列化的值（set/tuple 等）用 `default=str` 兜底——报告渲染
    **绝不能**因为"某个实测值形状怪"就自己抛异常（那样失败信息全丢）。
    """
    if isinstance(v, str):
        return v.replace("|", "\\|")[:400]
    if isinstance(v, (set, frozenset, tuple)):
        v = sorted(v, key=str) if not isinstance(v, tuple) else list(v)
    s = json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)
    return s.replace("|", "\\|")[:400]


def dump_json(r: dict) -> str:
    """机读出口（下游是程序 → 不做人读那套截断/表格）。

    `_jsonable` 已把 set/tuple 收敛掉；这里**不打 `default=` 兜底**：
    序列化不出来就该当场炸（那说明断言值形状没被收敛，是工具缺陷）。
    """
    return json.dumps(r, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
