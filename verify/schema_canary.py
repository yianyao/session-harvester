# -*- coding: utf-8 -*-
"""采集前置 schema 探针（canary）。

背景：元宝/千问/豆包均无官方导出，采集器手写、依赖登录态端点与响应
schema。平台改版（端点参数变化 / 字段更名 / 消息组织方式变化）会让采集器
静默产出残缺数据。本脚本在全量采集前对新鲜样本做指纹校验，
PASS 再放行全量，DRIFT 则停下来 diff——把"采完才发现坏了"变成"开跑前就拦截"。

用法（先小样本，后全量）：
    # 1) 用对应采集器先采一个会话（或 list 阶段本身），
    # 2) 把新鲜样本喂给本脚本：
    python schema_canary.py yuanbao  path/to/sample_detail.json
    python schema_canary.py qianwen  path/to/sample_detail.json
    python schema_canary.py doubao   path/to/sample_detail.json

输出 PASS / DRIFT + 差异清单；DRIFT 时退出码 1（可接 CI / 批处理门禁）。
指纹定义与 harvester/adapters/{yuanbao,qianwen,doubao}_raw.py 的解析
逻辑同步维护：adapter 新增对某字段的消费时，把该字段加进 REQUIRED。
"""
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------- 指纹定义

YUANBAO = {
    "detail_top_required": ["id", "convs"],
    "conv_required": ["id", "speaker", "speechesV2"],
    "speaker_enum": ["human", "ai"],
    "speech_required": ["speechType", "content"],
    "block_type_known": [
        "text", "searchGuid", "deepSearch", "deepSearchAgent", "pdf",
        "image", "link_card", "prompt_url_card", "step",
    ],
    "sample_notes": "speaker 必须是 human/ai（v0.8 真机修正，非 user）",
}

QIANWEN = {
    "detail_top_required": ["session_id"],   # 轮列表在 list（旧样本另有 rounds）
    "detail_top_oneof": ["list", "rounds"],  # 二者至少其一
    "round_required": ["user_type", "pos", "created_at",
                       "request_messages", "response_messages"],
    "req_mime_known": ["text/plain", "doc/url", "image/url", "text/hidden"],
    "resp_mime_known": [
        "multi_load/iframe", "plan_cot/post", "bar/workflow",
        "signal/post", "bar/progress", "bar/iframe", "paa/iframe",
        "survey/card",
    ],
    "sample_notes": "created_at 为 epoch 毫秒；error_code 成功值是 int 0（falsy）",
}

DOUBAO = {
    "detail_top_required": ["conversation_id", "messages"],
    "msg_required": ["user_type", "index_in_conv", "create_time",
                     "content_block"],
    "user_type_enum": [1, 2],                # 1=用户 / 2=bot
    "block_type_known": [
        10000, 10040, 10091, 10082, 10019, 10030, 10025, 2074,
    ],
    "sample_notes": "index_in_conv 是字符串数字；thinking_block 仅标题不落正文",
}

FINGERPRINTS = {"yuanbao": YUANBAO, "qianwen": QIANWEN, "doubao": DOUBAO}


# ---------------------------------------------------------------- 校验逻辑

def _diff(fp: dict, j: dict, platform: str) -> list[str]:
    d: list[str] = []
    # 顶层必需键
    for k in fp.get("detail_top_required", []):
        if k not in j:
            d.append(f"顶层缺少键 {k!r}")
    if "detail_top_oneof" in fp and not any(k in j for k in fp["detail_top_oneof"]):
        d.append(f"顶层轮列表键缺失（需其一: {fp['detail_top_oneof']}）")
    if d:
        return d  # 顶层都缺就不用往下看

    items = j.get("convs") if platform == "yuanbao" else \
        j.get("list") or j.get("rounds") or j.get("messages") or []
    if not items:
        d.append("样本内无任何消息/轮（可能端点返回空体）")
        return d

    if platform == "yuanbao":
        conv = items[0]
        for k in fp["conv_required"]:
            if k not in conv:
                d.append(f"conv 缺少键 {k!r}")
        if conv.get("speaker") not in fp["speaker_enum"]:
            d.append(f"speaker 出现新值 {conv.get('speaker')!r} "
                     f"（已知: {fp['speaker_enum']}）")
        blocks = [b for sp in conv.get("speechesV2") or []
                  for b in (sp.get("content") or [])]
        if blocks:
            unknown = {b.get("type") for b in blocks
                       if b.get("type") not in fp["block_type_known"]}
            if unknown:
                d.append(f"content 块出现未知 type: {sorted(unknown)} "
                         f"（新增块通常无害，确认 adapter 是否需消费）")
    elif platform == "qianwen":
        r0 = items[0]
        for k in fp["round_required"]:
            if k not in r0:
                d.append(f"轮缺少键 {k!r}")
        req_mts = {b.get("mime_type") for b in r0.get("request_messages") or []}
        resp_mts = {b.get("mime_type") for b in r0.get("response_messages") or []}
        for m in req_mts - set(fp["req_mime_known"]):
            d.append(f"request 出现新 mime_type {m!r}")
        for m in resp_mts - set(fp["resp_mime_known"]):
            d.append(f"response 出现新 mime_type {m!r} "
                     f"（新增通常无害，确认 adapter 是否需消费）")
    else:  # doubao
        m0 = items[0]
        for k in fp["msg_required"]:
            if k not in m0:
                d.append(f"消息缺少键 {k!r}")
        if m0.get("user_type") not in fp["user_type_enum"]:
            d.append(f"user_type 出现新值 {m0.get('user_type')!r}")
        bts = {b.get("block_type") for b in m0.get("content_block") or []}
        unknown = {t for t in bts if t not in fp["block_type_known"]}
        if unknown:
            d.append(f"content_block 出现未知 block_type: {sorted(unknown)} "
                     f"（新增通常无害，确认 adapter 是否需消费）")
    return d


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in FINGERPRINTS:
        print(__doc__)
        return 2
    platform, sample = sys.argv[1], Path(sys.argv[2])
    if not sample.is_file():
        print(f"样本不存在: {sample}")
        return 2
    j = json.loads(sample.read_text(encoding="utf-8"))
    d = _diff(FINGERPRINTS[platform], j, platform)
    if d:
        print(f"DRIFT [{platform}] schema 与指纹不符，先 diff 再放行全量：")
        for x in d:
            print(f"  - {x}")
        print(f"  指纹备注: {FINGERPRINTS[platform]['sample_notes']}")
        return 1
    print(f"PASS [{platform}] 指纹一致，可放行全量采集。"
          f"（{FINGERPRINTS[platform]['sample_notes']}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
