"""可评审分级（P6-7）：每个数据面自己标 R0–R3，且**只按内容算、不按面写死**。

判据逐字来自设计 §3.1（`docs/inputs-2026-10-22/设计-证据归因与可评审数据面-2026-10-22.md`）：

| 等级 | 含义 | 判据 |
|---|---|---|
| R0 | 不可评审 | 只有原文，没有结构化计数 |
| R1 | 可计数 | 有结构化计数 + 原文锚点，**但没有根因候选** |
| R2 | 可归因 | 有根因候选（why/fix 齐全）+ 证据锚点 |
| R3 | 可裁决 | R2 + 对拍集（人裁过的台账）+ 回填通道（`attrib-review`） |

**唯一硬约束：R1 及以下不得标「可评审」**——R1 的数据给再强的模型也评不出根因
（设计 §3.1 原话）。这不是措辞洁癖：把 R1 包装成"可评审"等于把
"留数据待更好地 LLM"这件事**做成了假动作**——留下去的东西根本评不动。

本模块**不 import 任何 harvester 子模块**（除了 `backfill_available()` 里那处
惰性 import，因为 `cli` 反过来 import 各统计模块，模块级 import 会成环）。
于是统计层（`errstats`/`chainstats`/…）可以放心 import 它来给自己标级。
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: 从低到高，顺序即"可评审性"的序（`rank()` 用于"取最弱一环"）。
LEVELS: tuple[str, ...] = ("R0", "R1", "R2", "R3")

LABELS: dict[str, str] = {
    "R0": "不可评审",
    "R1": "可计数",
    "R2": "可归因",
    "R3": "可裁决",
}

CRITERIA: dict[str, str] = {
    "R0": "只有原文，没有结构化计数",
    "R1": "有结构化计数与原文锚点，但没有根因候选",
    "R2": "有根因候选（why/fix 齐全）+ 证据锚点",
    "R3": "R2 + 对拍集（台账）+ 回填通道（attrib-review）",
}


def rank(level: str) -> int:
    """R0→0 … R3→3。未知等级 raise（拼错等级不许静默降级）。"""
    try:
        return LEVELS.index(level)
    except ValueError:
        raise ValueError(f"未知的可评审等级：{level!r}（应为 {LEVELS}）") from None


def weakest(levels) -> str:
    """多个分节合成一个面时**取最弱一环**：整体不可能比它最弱的分节更可评审。

    传字符串会被拒绝：`weakest("R1")` 会按字符迭代成 `"R"`，症状是
    `rank("R")` 抛错或（更坏）被某处兜成 R0——正是 `AGENTS.md` §三 14 那类
    "形态推错、静默得空值"的坑，索性在入口挡住。
    """
    if isinstance(levels, str):
        raise TypeError("weakest() 收等级序列（如 ['R1','R2']），不收单个字符串")
    got = list(levels)
    if not got:
        return "R0"
    return min(got, key=rank)


def backfill_available() -> bool:
    """回填通道是否存在（= R3 的后一半）。

    **只查真解析器里有没有 `attrib-review` 子命令**，不试写台账：本模块全只读，
    "能不能写"按"命令在不在"判定（与 `AGENTS.md` §三 3 同口径）。
    惰性 import：`cli` 会 import 各统计模块，模块级 import 会成环。
    """
    try:
        from .cli import build_parser
        for act in build_parser()._actions:      # noqa: SLF001
            choices = getattr(act, "choices", None)
            if choices and "attrib-review" in choices:
                return True
    except Exception:  # pragma: no cover - 解析器异常时按"没有通道"处理
        return False
    return False


def level_of_counts(entries: int, candidates: int, backfill: bool) -> str:
    """按**内容**定级：条目数 / 归因候选数 / 回填通道，三者决定等级。"""
    if not entries:
        return "R0"
    if not candidates:
        return "R1"
    return "R3" if backfill else "R2"


def level_of(rows: list[dict], backfill: bool) -> str:
    """条目形态的便捷版：`rows[i]["attrib"]` 非空即"这条有归因候选"。"""
    return level_of_counts(len(rows), sum(1 for r in rows if r.get("attrib")),
                           backfill)


def level_reason(rows: list[dict], level: str, backfill: bool) -> str:
    """人读理由（证据包与各报告面共用一套措辞，免得两处口径漂移）。"""
    if level == "R0":
        return "窗口内没有可结构化的条目（只有原文，不可评审）"
    if level == "R1":
        return ("有条目计数与原文锚点，但**没有根因候选**——"
                "这种数据给再强的模型也评不出根因（设计 §3.1）")
    if level == "R2":
        return "有条目 + 原文锚点 + 归因候选（why/fix/置信）"
    return ("有条目 + 原文锚点 + 归因候选 + **回填通道**"
            "（`attrib-review` 可把裁决落进 attribations 台账）"
            if backfill else "R2（回填通道不可用）")


@dataclass(frozen=True)
class Level:
    """一个数据面的等级判定结果（可 JSON 化，页面与 CLI 同源）。"""

    level: str
    reason: str
    coverage: str = ""
    note: str = ""
    evidence: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.level not in LEVELS:
            raise ValueError(f"未知的可评审等级：{self.level!r}")

    @property
    def label(self) -> str:
        return LABELS[self.level]

    @property
    def can_review(self) -> bool:
        """**R2 起才评得动**（R1 只能数、不能评）。"""
        return rank(self.level) >= rank("R2")

    @property
    def can_verdict(self) -> bool:
        """**只有 R3 能落裁决**（R2 有候选但没有台账回填通道）。"""
        return self.level == "R3"

    def as_dict(self) -> dict:
        return {
            "level": self.level,
            "label": self.label,
            "criteria": CRITERIA[self.level],
            "reason": self.reason,
            "can_review": self.can_review,
            "can_verdict": self.can_verdict,
            "coverage": self.coverage,
            "note": self.note,
            **({"evidence": self.evidence} if self.evidence else {}),
        }

    def render(self) -> str:
        """一行 Markdown（各报告面表头用它，措辞统一）。"""
        tail = f"；覆盖 {self.coverage}" if self.coverage else ""
        if self.can_review:
            ability = ("**可评审、可裁决**" if self.can_verdict
                       else "**可评审但不可裁决**（有候选，缺台账回填通道）")
        else:
            # R0/R1：必须显式说"不可评审"。注意措辞里**不出现裸的「可评审」**
            # （连"「可评审」"这种引用都不写）——否则"扫裸词"的门会分不清
            # "在声称可评审"和"在解释规则"，那种门只能被绕过或被迫放宽。
            ability = "**不可评审**（判据见设计 §3.1：R1 只能数、评不出根因）"
        extra = f"（{self.note}）" if self.note else ""
        return (f"- **本面等级：{self.level}（{self.label}）**——"
                f"{self.reason}{tail}；{ability}{extra}")


def assess(*, entries: int, candidates: int = 0, backfill: bool | None = None,
           coverage: str = "", note: str = "",
           evidence: dict | None = None) -> Level:
    """给一个数据面定级。`entries` = 结构化条目数（0 即 R0）。"""
    if backfill is None:
        backfill = backfill_available()
    lv = level_of_counts(entries, candidates, backfill)
    reason = level_reason([], lv, backfill)
    if lv == "R0":
        reason = CRITERIA["R0"]
    return Level(level=lv, reason=reason, coverage=coverage, note=note,
                 evidence=dict(evidence or {}))