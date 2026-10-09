# -*- coding: utf-8 -*-
"""③117 候选簇注册脚本（用户 2026-10-09 认可后执行，一次性）。

输入：docs/reports/topic-candidates-2026-10-09.md（T5 推荐器产物）。
解析每个"候选簇 N｜K 会话｜种子：<title>"节，register_topic（name=种子
标题，keywords=建议关键词）+ add_members（evidence 注明来源与认可日期）。
幂等：register 同名返回旧 id（同名字簇成员自然合并，计数留痕）；
add_members 幂等跳过重复 sid。只写 topics_meta.db（独立 meta 库红线），
不碰 harvester.db。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from harvester.topics import add_members, register_topic  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "docs" / "reports" / "topic-candidates-2026-10-09.md"
META = ROOT / "topics_meta.db"
EVIDENCE = ("T5 候选簇 {n}（topic-candidates-2026-10-09.md；"
            "用户 2026-10-09 认可注册）")

SEC = re.compile(r"^## 候选簇 (\d+)｜(\d+) 会话｜种子：(.+)$")
KW = re.compile(r"^- 建议关键词：(.+)$")
MEM = re.compile(r"^- `([^`]+)` sim=")


def main() -> int:
    text = REPORT.read_text(encoding="utf-8")
    clusters = []  # (n, size, title, keywords, sids)
    cur = None
    for line in text.splitlines():
        m = SEC.match(line)
        if m:
            cur = {"n": int(m.group(1)), "size": int(m.group(2)),
                   "title": m.group(3).strip(), "keywords": [], "sids": []}
            clusters.append(cur)
            continue
        if cur is None:
            continue
        m = KW.match(line)
        if m:
            cur["keywords"] = [k.strip() for k in m.group(1).split("、")
                               if k.strip()]
            continue
        m = MEM.match(line)
        if m:
            cur["sids"].append(m.group(1))
    print(f"解析簇数: {len(clusters)}")
    if len(clusters) != 117:
        ans = input(f"预期 117 簇，实际 {len(clusters)}，继续? [y/N] ")
        if ans.strip().lower() != "y":
            print("中止，未写库。")
            return 1
    merged = {}
    for c in clusters:
        assert len(c["sids"]) == c["size"], f"簇 {c['n']} 成员数不符"
        tid = register_topic(META, c["title"], keywords=c["keywords"])
        if tid in merged:
            merged[tid].append(c["n"])
        else:
            merged[tid] = [c["n"]]
        add_members(META, tid, c["sids"], evidence=EVIDENCE.format(n=c["n"]))
        print(f"簇 {c['n']:>3} → {tid}  {c['title']}  ({c['size']} 成员)")
    dup = {t: ns for t, ns in merged.items() if len(ns) > 1}
    print(f"\n注册完成：{len(clusters)} 簇 → {len(merged)} 个主题"
          f"（同名合并 {len(dup)} 组: {dup if dup else '无'}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
