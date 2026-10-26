# -*- coding: utf-8 -*-
"""chain-generate（v0.83）：把链创作包交给本机模型写成 chain 长文。

用户 2026-10-26 裁决：「主题链的生成，应该在主题 tab 提供命令按钮，执行即可。
不能老是在 WorkBuddy 中耗费 token 干这活」。

背景：v0.82 把生成链定成「命令出包 + Agent 写 + 命令校验落盘」。但每条链都要
Agent 读 1.9 万字包、写 5 千字、来回核对引文——16 个主题就是 16 次这样的开销。
用户要的是"执行即可"，所以这一层换成**本机模型代笔**，Agent 退出这条链路。

三条纪律（否则生成器会变成幻觉放大器）：

1. **事实只能来自创作包**：members 逐字等于包里的清单，锚点 sid/turn 只能从
   包里挑，引文只能逐字复制包里的原话；
2. **生成完照样过两道门**（`chain-validate` 结构锚点 + `chain-audit` 引文逐字），
   不过就把失败项**回喂重试**（最多 max_attempts 次）——门是唯一裁判，模型说了不算；
3. **不可复现是明示的**：同库同参数跑两次，链文本会不同（temperature=0 也不保证）。
   所以 frontmatter 记 model / attempts，与 Agent 手写、逐字节可复现的链可区分。

模型接入：OpenAI 兼容 `POST {base_url}/chat/completions`。默认两级——
硅基流动（`KNOWATLAS_LLM_API_KEY_SF1`）→ DeepSeek 官方（`KNOWATLAS_LLM_API_KEY`），
书写顺序即 failover 优先级。换供应商改 `HARVESTER_LLM_BACKENDS`（JSON 数组），
不改代码——可迁移铁律：新机只要有这三样（base_url / model / key 环境变量）就能跑。
key 只从环境变量读，永不入参、入日志、入库；错误信息只带 key 指纹。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

GEN_SCHEMA = "chain-generate/1"
PROMPT_VERSION = "t3-chaingen-20261026"

# 默认两级后端（可被 HARVESTER_LLM_BACKENDS 整体覆盖）
_DEFAULT_BACKENDS: tuple[dict, ...] = (
    {"base_url": "https://api.siliconflow.cn/v1",
     "model": "deepseek-ai/DeepSeek-V4.1-Flash",
     "api_key_env": ["KNOWATLAS_LLM_API_KEY_SF1"]},
    {"base_url": "https://api.deepseek.com/v1",
     "model": "deepseek-chat",
     "api_key_env": ["KNOWATLAS_LLM_API_KEY"]},
)

# 换 key/切后端的状态码（key 失效/限流/余额不足）与「服务端故障」分开处理
_RETRYABLE = frozenset({401, 402, 403, 429})

_SYSTEM = (
    "你是思维链（chain）长文写作者。给你一份「链创作包」，你要按包里的"
    "§3 格式契约和 §5 写作指令，写出一篇讲「想法怎么变的」长文。\n"
    "\n"
    "输出纪律（违反任一条，机器校验会打回，你就得重写）：\n"
    "1. 只输出一个 markdown 文档：从 `---` 开头的 frontmatter 到正文结束。"
    "不要代码围栏、不要解释、不要在正文外说一句话。\n"
    "2. `members` 必须逐字等于包里 §7 的清单（有几个写几个，顺序照抄）。\n"
    "3. 锚点的 `sid` 只能从包里 §2 的候选里挑（那些 sid 都在 §7 清单内），"
    "`turn` 写 1；不许编造 sid、不许编 turn。\n"
    "4. **正文里的「」只能用来做一件事：逐字引用「可引用原句库」里的句子**。"
    "也就是说，每个「」里的内容，必须能从原句库里一字不差地复制过来"
    "（≥6 个汉字，可截取其中一段）。\n"
    "   —— 想强调某个词、某个阶段名、某个概念？**用加粗 `**这样**`**，"
    "不要用「」。\n"
    "   —— 想转述用户的意思？直接写（不加任何引号），不要套「」。\n"
    "   —— **要概括一个人「当时是怎么问的」（原话不是逐字）时，用『』不用「」**。"
    "例：从『我这台机器坏了，告诉我怎么修』转向『我要长期在这台机器上干活』"
    "——这两句是概括，只能用『』。\n"
    "   —— **找不到合适的原句就一处都别用「」**：全篇没有「」也是合格的。"
    "绝不能为了「看起来有证据」而编一句像原文的话塞进「」——那是最严重的错。\n"
    "   反例（这些会被打回）：「把评审做成数据」「帮我看看稿子」「某个服务坏了」"
    "——这类概括话/自问句不是原文，不许加「」，要么改成『』要么直接写。\n"
    "5. 不许出现包里没有的日期、数字、作品名、人名。\n"
    "6. 每个 `stage` 至少 1 个 node；阶段 3–8 个。\n"
    "7. 全文 2500–4500 字，末尾必须有「口子」一节（这条链哪里没做到）。\n"
    "8. 每个锚点的 `note` 按这个句式写：**先原样抄该 sid 原话里的 2–4 个词"
    "（顿号隔开），再冒号，再补你的判断**。例：`商业结果、四千字：把评价"
    "标准从文本挪到数据`。原话里一次都没出现的词（「评价范式」「判断力的"
    "差距」这类自造说法）会判「疑似错配」打回。\n"
)

_USER_TMPL = (
    "下面是链创作包（含判据头、锚点候选原话、格式契约、写作指令、权威成员清单）。\n"
    "请按它写 chain 长文。\n"
    "\n"
    "===== 创作包开始 =====\n"
    "%s\n"
    "===== 创作包结束 =====\n"
    "\n"
    "%s"
    "\n"
    "现在输出链长文（从 `---` 开始）。\n"
)


class GenError(Exception):
    """生成失败（配置缺失 / 网络 / 模型输出不可用）。"""


def _fp(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:8] if key else ""


def backends() -> list[dict]:
    """按优先级返回可用后端；每个后端带已解析的 keys（空池的跳过）。"""
    raw = os.environ.get("HARVESTER_LLM_BACKENDS")
    if raw:
        try:
            cfg = json.loads(raw)
        except ValueError as exc:
            raise GenError(f"HARVESTER_LLM_BACKENDS 不是合法 JSON：{exc}") from exc
        if not isinstance(cfg, list) or not cfg:
            raise GenError("HARVESTER_LLM_BACKENDS 必须是非空 JSON 数组")
        src = cfg
    else:
        src = list(_DEFAULT_BACKENDS)
    out: list[dict] = []
    for be in src:
        if not isinstance(be, dict):
            continue
        names = be.get("api_key_env") or []
        if isinstance(names, str):
            names = [s.strip() for s in names.split(",") if s.strip()]
        keys: list[str] = []
        for name in names:
            for k in str(os.environ.get(name, "")).split(","):
                k = k.strip()
                if k and k not in keys:
                    keys.append(k)
        if not keys:
            continue
        out.append({"base_url": str(be.get("base_url", "")).rstrip("/"),
                    "model": str(be.get("model", "")),
                    "keys": keys})
    return out


def _post(be: dict, key: str, system: str, user: str,
          max_tokens: int, timeout: float) -> tuple[str, dict]:
    """单次调用；返回 (content, usage)。失败抛 GenError（带 key 指纹）。"""
    body = json.dumps({
        "model": be["model"], "temperature": 0, "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }).encode("utf-8")
    req = urllib.request.Request(
        be["base_url"] + "/chat/completions", data=body,
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "application/json"})
    # 本机全局 http_proxy 会被 urllib 读走 → localhost/内网必须显式绕过；
    # 公网直连不需要代理，故默认清空（HARVESTER_LLM_PROXY 可显式指定）。
    proxy = os.environ.get("HARVESTER_LLM_PROXY", "")
    handler = (urllib.request.ProxyHandler({"http": proxy, "https": proxy})
               if proxy else urllib.request.ProxyHandler({}))
    opener = urllib.request.build_opener(handler)
    try:
        with opener.open(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise GenError(f"LLM HTTP {exc.code}：{detail}"
                       f"（key 指纹 {_fp(key)}）") from exc
    except Exception as exc:  # 网络/DNS/超时
        raise GenError(f"LLM 请求失败：{type(exc).__name__}: {exc}") from exc
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise GenError(f"LLM 响应结构异常：{str(payload)[:200]}") from exc
    usage = payload.get("usage") or {}
    return content or "", usage if isinstance(usage, dict) else {}


def chat(system: str, user: str, max_tokens: int = 8192,
         timeout: float = 300.0,
         model: str | None = None) -> tuple[str, dict, str]:
    """两级后端 failover 的单轮对话；返回 (content, usage, 后端标签)。

    `model` 只覆盖**优先级最高那个后端**的模型名（兜底后端保持原样）——
    语义是「这家换个模型试试」，不是「把所有后端都指到不存在的模型上」。
    """
    bes = backends()
    if bes and model:
        bes[0] = dict(bes[0], model=str(model))
    if not bes:
        raise GenError(
            "没有可用的模型后端：请设置 KNOWATLAS_LLM_API_KEY_SF1"
            "（硅基流动）或 KNOWATLAS_LLM_API_KEY（DeepSeek 官方），"
            "或用 HARVESTER_LLM_BACKENDS 指定其他 OpenAI 兼容服务")
    failures: list[str] = []
    for be in bes:
        for key in be["keys"]:
            try:
                content, usage = _post(be, key, system, user, max_tokens, timeout)
                return content, usage, f"{be['model']}@{be['base_url']}"
            except GenError as exc:
                msg = str(exc)
                failures.append(msg)
                code = None
                m = re.search(r"HTTP (\d{3})", msg)
                if m:
                    code = int(m.group(1))
                if code in _RETRYABLE:
                    continue          # key 类问题：先换同后端下一个 key
                if code and code >= 500:
                    break             # 服务端故障：同后端其他 key 无益
                if code is None:
                    break             # 网络不可达：换后端
                raise                 # 其他 4xx：请求/配置问题，换谁都治不好
    raise GenError("所有模型后端均不可用：\n  " + "\n  ".join(failures))


def _strip_fence(text: str) -> str:
    """去掉模型可能自带的代码围栏/前后客套，只留 md 文档。"""
    t = (text or "").strip()
    if not t:
        return ""
    m = re.search(r"```(?:markdown|md)?\s*\n(.*?)\n```", t, re.S)
    if m and "---" in m.group(1):
        t = m.group(1).strip()
    lines = t.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    # 首个 `---` 之前若有客套话，砍掉
    for i, ln in enumerate(lines):
        if ln.strip() == "---":
            lines = lines[i:]
            break
    return "\n".join(lines).strip() + "\n"


def _patch_fm(doc: str, pack: dict) -> tuple[str, list[str]]:
    """最小修补：只对齐**客观事实**字段，创作部分一个字不动。

    允许修补的理由：`members` 是注册表里的事实、`db_mtime` 是库指纹——模型
    抄漏了补上不叫"替它写"，叫把客观事实对齐。`anchors` 与正文一概不碰，
    那才是它的活儿。修补了什么必须回显（第二项），不许静默。
    """
    import yaml  # 链校验本身就要 PyYAML（topicchain H9），生成器不单独降级

    if not doc.startswith("---\n"):
        return doc, []
    idx = doc.find("\n---\n", 3)
    if idx < 0:
        return doc, []
    head, body = doc[4:idx], doc[idx + 5:]
    try:
        fm = yaml.safe_load(head)
    except yaml.YAMLError:
        return doc, []
    if not isinstance(fm, dict):
        return doc, []

    patched: list[str] = []
    want = list(pack.get("members") or [])
    got = fm.get("members")
    if isinstance(got, list) and [str(x) for x in got] != want:
        fm["members"] = want
        patched.append("members 对齐权威清单")
    elif got is None:
        fm["members"] = want
        patched.append("members 缺失已补")

    fp = pack.get("db_fingerprint") or {}
    gf = fm.get("generated_from")
    if not isinstance(gf, dict):
        gf = {}
        patched.append("generated_from 缺失已补")
    if not gf.get("db_mtime") and fp.get("db_mtime"):
        gf["db_mtime"] = str(fp.get("db_mtime"))
        patched.append("db_mtime 已补")
    for k in ("sessions", "steps", "errors"):
        if k not in gf and k in fp:
            gf[k] = fp[k]
    fm["generated_from"] = gf

    if fm.get("prompt_version") is None:
        fm["prompt_version"] = pack.get("prompt_version") or PROMPT_VERSION
        patched.append("prompt_version 已补")
    if not patched:
        return doc, []
    dumped = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False,
                            default_flow_style=False)
    return "---\n" + dumped + "---\n" + body, patched


def _demote_quotes(doc: str, misses: list[str]) -> tuple[str, list[str]]:
    """把**未被原文逐字命中**的「X」改判成『X』（只改标点，一个字不动）。

    为什么这不是"绕开引文门"：门的判据是「`「」` 里必须是逐字原文」。这些地方
    原文确实没有逐字对应，那它本来就不该用「」（引文号）——中文排版里「」与
    『』本来就有分工：**「」= 逐字引文，『』= 概括/概念/转述**。所以这是把
    分错的标点改对，不是把错的文本放行。

    实测依据（2026-10-26，主题 tp-20261008-004）：模型反复写
    「我这台机器坏了，告诉我怎么修」→「我要长期在这台机器上干活」这种
    **概括式引述**来表达"提问方式的位移"——这是链最核心的写法，却天然不是
    逐字原文。两次生成都栽在这里（20/32 未命中），改判后正文一字未改即可过门。
    """
    out = doc
    done: list[str] = []
    for q in misses:
        old = "「" + q + "」"
        if old in out:
            out = out.replace(old, "『" + q + "』")
            done.append(q)
    return out, done


def _feedback(v: dict, a: dict, limit: int = 14) -> str:
    """把门禁失败项写成下一轮的具体修改要求（只改这些，别重写全文）。"""
    lines = ["", "===== 上一版被机器校验打回 =====",
             "**只修下面这些，不要重写全文**（重写会引入新错）："]
    errs = list(v.get("errors") or [])[:limit]
    for e in errs:
        lines.append(f"- 结构门：{e}")
    q = a.get("quotes") or {}
    for m in (q.get("misses") or [])[:8]:
        lines.append(f"- 引文未命中：「{str(m)[:70]}」在原文里找不到逐字对应。"
                     "这几乎都是把「」当强调符号用了——**「」只能装"
                     "「可引用原句库」里的原句**；要强调就改成加粗 `**这样**`，"
                     "要转述就直接写（不加引号）")
    for m in (q.get("misses") or [])[:4]:
        lines.append(f"  · 具体这一条要么换成原句库里的句子，要么去掉「」："
                     f"「{str(m)[:50]}」")
    a_sus = (a.get("anchors") or {}).get("suspicious") or []
    for r in a_sus[:6]:
        lines.append(f"- 锚点疑似错配：sid={r.get('sid')} 的 note"
                     f"「{str(r.get('note'))[:40]}」与原文无共同用词——"
                     f"note 改成原文里出现过的说法（原文片段："
                     f"{str(r.get('raw'))[:60]}）")
    if len(lines) == 3:      # 只有表头三行 → 门没给出可执行的条目
        lines.append("- （校验器没给出具体条目，请重写一遍并更严格地照抄原文）")
    lines.append("重新输出完整文档（仍从 `---` 开始）。")
    return "\n".join(lines)


def _quote_bank(pack: dict, per: int = 120, limit: int = 40) -> str:
    """把包里的首轮原话摘成「可引用原句库」，让模型只需复制、不必自己找。

    为什么要有这一节：首版实测两次都栽在引文门——结构全对，但模型把
    「」当成中文强调符号，写了「把评审做成数据」「帮我看看稿子」这种
    自己的概括话（原文里没有）。给它一份现成清单，比让它从 1.9 万字里
    自己挑准得多。清单顺序 = 包里的桶序，确定性。
    """
    rows: list[tuple[str, str]] = []
    for bk in pack.get("buckets") or []:
        for n in bk.get("nodes") or []:
            q = " ".join((n.get("quote") or "").split())
            if len(q) >= 6:
                rows.append((str(n.get("sid") or ""), q[:per]))
    if not rows:
        return ("===== 可引用原句库 =====\n"
                "（本包没有可用原句——正文一律不要用「」，要强调用加粗）\n")
    out = ["===== 可引用原句库（正文「」里只能从这里逐字复制，共 %d 条）====="
           % len(rows)]
    for i, (sid, q) in enumerate(rows[:limit], 1):
        out.append("[Q%02d] %s  %s" % (i, sid, q))
    out.append("（用法：直接把某条 sid 后面的那段话原样放进「」即可，一字不改；"
               "也可以只截取其中连续的一段，≥6 个汉字。）")
    return "\n".join(out) + "\n"


def generate(db: Path, meta_path: Path, topic_id: str, *,
             out: Path | None = None, max_chars: int | None = None,
             max_attempts: int = 1, model: str | None = None,
             max_tokens: int = 8192, timeout: float = 300.0,
             write: bool = True, demote: bool = True) -> dict:
    """出包 → 模型写 → 过两道门（不过回喂重试）→ 写草稿位。

    `write=False` 只返回文本不落盘（供页面预览/测试）。返回机读结果，
    CLI/页面负责人话渲染。max_attempts 是**钱的上限**：每次都是一次完整调用。
    """
    from .chainbuild import build, render
    from .chainaudit import audit_chain
    from .topicchain import validate_chain

    db, meta_path = Path(db), Path(meta_path)
    attempts_needed = max(1, int(max_attempts))

    try:  # 生成器与链校验同一条 H9 纪律：缺 PyYAML 显式报错，不静默降级
        import yaml  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "chain-generate 需要 PyYAML（与 chain-validate 同一条 H9 纪律）；"
            "不提供降级——frontmatter 静默误读比报错更危险") from exc

    pack = build(db, meta_path, topic_id, max_chars=max_chars)
    pack_md = render(pack)
    user = _USER_TMPL % (pack_md, _quote_bank(pack))
    system = _SYSTEM

    tmpdir = Path(tempfile.mkdtemp(prefix="chaingen-"))
    tmp = tmpdir / "draft.md"
    log: list[dict] = []
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    last_v: dict = {}
    last_a: dict = {}
    doc = ""
    patched: list[str] = []
    demoted: list[str] = []
    backend_label = ""
    prev_fail = ""
    try:
        for n in range(1, attempts_needed + 1):
            text, usage, backend_label = _bump(
                chat(system, user + prev_fail, max_tokens=max_tokens,
                     timeout=timeout, model=model), usage_total)
            raw_len = len(text)
            doc = _strip_fence(text)
            doc, patched = _patch_fm(doc, pack)
            tmp.write_text(doc, encoding="utf-8", newline="\n")
            v = validate_chain(tmp, meta_path, db)
            a = audit_chain(tmp, db)
            # 标点分类修正（只改标点、不改字）：未逐字命中的「」本来就是
            # 概括式引述，该用『』。改完重跑一遍门，让判定基于最终文本。
            n_demoted = 0
            if demote:
                misses = (a.get("quotes") or {}).get("misses") or []
                if misses:
                    doc2, done = _demote_quotes(doc, misses)
                    if done:
                        doc = doc2
                        n_demoted = len(done)
                        demoted.extend(done)
                        tmp.write_text(doc, encoding="utf-8", newline="\n")
                        v = validate_chain(tmp, meta_path, db)
                        a = audit_chain(tmp, db)
            last_v, last_a = v, a
            ok = bool(v.get("ok")) and bool(a.get("ok"))
            log.append({"n": n, "ok": ok, "chars": len(doc), "raw_chars": raw_len,
                        "backend": backend_label,
                        "quotes_demoted": n_demoted,
                        "validate_errors": len(v.get("errors") or []),
                        "quotes_missed": len((a.get("quotes") or {})
                                             .get("misses") or []),
                        "anchors_suspicious": len((a.get("anchors") or {})
                                                  .get("suspicious") or [])})
            if ok:
                break
            prev_fail = _feedback(v, a)
    finally:
        try:
            for f in tmpdir.iterdir():
                f.unlink()
            tmpdir.rmdir()
        except OSError:
            pass

    ok = bool(last_v.get("ok")) and bool(last_a.get("ok"))
    blockers: list[str] = []
    if not ok:
        blockers.extend((last_v.get("errors") or [])[:5])
        qm = len((last_a.get("quotes") or {}).get("misses") or [])
        if qm:
            blockers.append(f"引文逐字门：{qm} 条未命中")
        sus = len((last_a.get("anchors") or {}).get("suspicious") or [])
        if sus:
            blockers.append(f"锚点语义门：{sus} 个疑似错配")

    written = False
    target = Path(out) if out else None
    if ok and write and target is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            bak = target.with_name(
                target.name + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
            bak.write_bytes(target.read_bytes())
        target.write_text(doc, encoding="utf-8", newline="\n")
        written = True

    # 没过门也把最后一版留下来（`.pending.md`，**不是草稿位**）：等了 3 分钟
    # 却什么都看不到，等于让人重跑一遍再等 3 分钟。文件名叫 pending 就是明示
    # "未过门，别拿去发布"——chain-publish 只认草稿位，不会误把这份搬进正本。
    pending: str | None = None
    if (not ok) and write and target is not None and doc.strip():
        pend = target.with_name(target.name + ".pending.md")
        pend.parent.mkdir(parents=True, exist_ok=True)
        pend.write_text(doc, encoding="utf-8", newline="\n")
        pending = str(pend)

    return {
        "schema": GEN_SCHEMA,
        "ok": ok,
        "written": written,
        "path": str(target) if (written and target) else None,
        "pending": pending,
        "topic_id": topic_id,
        "topic": pack["topic"]["name"],
        "model": backend_label,
        "prompt_version": PROMPT_VERSION,
        "pack": {"chars": len(pack_md), "counts": pack["counts"],
                 "budget": pack["budget"]},
        "attempts": log,
        "usage": usage_total,
        "patched": patched,
        "demoted": {"count": len(demoted), "items": demoted[:20]},
        "validate": {"ok": last_v.get("ok"),
                     "stats": last_v.get("stats") or {},
                     "errors": (last_v.get("errors") or [])[:12],
                     "warnings": (last_v.get("warnings") or [])[:6]},
        "audit": {"ok": last_a.get("ok"),
                  "quotes": {k: last_a.get("quotes", {}).get(k)
                             for k in ("total", "exact", "partial", "vacuous")},
                  "misses": (last_a.get("quotes", {}).get("misses") or [])[:8],
                  "suspicious": len((last_a.get("anchors") or {})
                                    .get("suspicious") or [])},
        "blockers": blockers,
        "doc": doc,
    }


def _bump(result: tuple[str, dict, str],
          usage_total: dict) -> tuple[str, dict, str]:
    """把本次 usage 累加进总计（缺失字段按 0 计，不猜）。"""
    text, usage, label = result
    for k in usage_total:
        try:
            usage_total[k] += int(usage.get(k) or 0)
        except (TypeError, ValueError):
            pass
    return text, usage, label
