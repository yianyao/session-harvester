# -*- coding: utf-8 -*-
"""网页端/客户端 Chat 的登录态探测与准备模块（weblogin 命令实现）。

三级流程（对应用户需求）：
  1. check       —— 探测浏览器登录态：读取各 Chromium 配置档案的 Cookies 库，
                    按产品域名统计 cookie。无任何 cookie → 确定未登录；
                    有 cookie → 大概率已登录（无法离线验证有效性，如实标注）。
  2. init-config —— 生成账号密码配置模板 webchat.accounts.json（明文存储，
                    仅存本机，工具不做任何上传；文件头附安全提示）。
  3. prepare     —— 若安装了 playwright，则打开持久化浏览器档案并跳转登录页，
                    由用户在窗口内手动完成登录（工具不代填表单——各产品登录页
                    结构无公开承诺，不硬猜选择器）；完成后工具侧档案即持有
                    登录态，供后续提取器复用。未装 playwright 时给出安装提示，
                    并建议"先在系统浏览器手动登录，再重跑 check"。

铁律一致声明：本模块只做登录态基础设施，不实现具体产品的会话提取
（各产品内部接口无公开文档，不硬猜）。
"""

from __future__ import annotations

import glob
import json
import os
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .discovery import APPDATA_LOCAL, APPDATA_ROAMING

TOOL_PROFILE_DIR = Path(__file__).resolve().parent.parent / "weblogin_profile"


@dataclass
class Product:
    id: str
    name: str
    domains: list[str]                 # cookie host_key 匹配用
    login_url: str
    #: 除系统浏览器外，桌面客户端自带的 Chromium 档案里的 Cookies 库
    app_cookie_dbs: list[Path] = field(default_factory=list)


PRODUCTS: list[Product] = [
    Product(
        id="deepseek-desktop", name="DeepSeek",
        domains=["deepseek.com"],
        login_url="https://chat.deepseek.com/sign_in",
        app_cookie_dbs=list(APPDATA_ROAMING.glob("@deepseek-ai/*/Partitions/*/Network/Cookies")),
    ),
    Product(
        id="yuanbao", name="腾讯元宝",
        domains=["yuanbao.tencent.com"],
        login_url="https://yuanbao.tencent.com/",
        app_cookie_dbs=list(APPDATA_LOCAL.glob("com.tencent.yuanbao/EBWebView/Default/Network/Cookies")),
    ),
    Product(
        id="doubao", name="豆包",
        domains=["doubao.com"],
        login_url="https://www.doubao.com/chat/",
    ),
    Product(
        id="qianwen", name="通义千问",
        domains=["tongyi.aliyun.com", "tongyi.com"],
        login_url="https://www.tongyi.com/",
        app_cookie_dbs=list(APPDATA_LOCAL.glob("Qianwen/User Data/*/Network/Cookies")),
    ),
]

#: 系统浏览器的 Cookies 库位置（User Data/<profile>/Network/Cookies）
BROWSER_GLOBS: list[str] = [
    str(APPDATA_LOCAL / "Microsoft/Edge/User Data/*/Network/Cookies"),
    str(APPDATA_LOCAL / "Google/Chrome/User Data/*/Network/Cookies"),
    str(APPDATA_LOCAL / "BraveSoftware/Brave-Browser/User Data/*/Network/Cookies"),
    str(TOOL_PROFILE_DIR / "Default/Network/Cookies"),
]

#: 各产品会话指示性 cookie 名（公开可观察的会话 cookie；只用于提升置信度描述，
#: 不作为硬判据——判据是"该域名下是否存在任何 cookie"）
INDICATIVE_COOKIES: dict[str, list[str]] = {
    "deepseek-desktop": ["sess_shim", "ds_session_id", "token"],
    "yuanbao": ["login-uid", "tb-sid"],
    "doubao": ["sessionid"],
    "qianwen": ["tongyi_sso_ticket", "login_tongyi_ticket"],
}


@dataclass
class ProfileCookieReport:
    profile: str
    cookie_count: int
    indicative_found: list[str]
    error: str = ""


def _read_file_share_all(path: Path) -> bytes:
    """以共享模式读取被占用的文件（Windows）。

    Chromium 以独占锁打开 Cookies 库，普通 open/copy 会报 WinError 32；
    改用 CreateFileW + FILE_SHARE_READ|WRITE|DELETE 读取原始字节。
    非 Windows 平台退化为普通读取。
    """
    if os.name != "nt":
        return path.read_bytes()
    import ctypes
    import ctypes.wintypes as wt
    GENERIC_READ = 0x80000000
    FILE_SHARE_ALL = 1 | 2 | 4  # READ | WRITE | DELETE
    OPEN_EXISTING = 3
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    h = k32.CreateFileW(str(path), GENERIC_READ, FILE_SHARE_ALL, None,
                        OPEN_EXISTING, 0, None)
    if h in (-1, 0xFFFFFFFFFFFFFFFF):
        err = ctypes.get_last_error()
        tag = "sharing violation (WinError 32)" if err == 32 else str(err)
        raise OSError(f"CreateFileW failed: {tag}")
    try:
        size = k32.GetFileSize(h, None)
        if size == 0xFFFFFFFF:  # >4GB 不可能出现在 cookie 库，防御
            raise OSError("文件过大")
        buf = ctypes.create_string_buffer(size)
        read = wt.DWORD(0)
        if not k32.ReadFile(h, buf, size, ctypes.byref(read), None):
            raise OSError(f"ReadFile 失败: {ctypes.get_last_error()}")
        return buf.raw[:read.value]
    finally:
        k32.CloseHandle(h)


def _host_matches(host: str, domains: list[str]) -> bool:
    """cookie 域名精确匹配：host 等于域名，或是其子域（.domain 结尾）。

    不用子串匹配——否则 mydoubao.com 之类会被误判为 doubao.com 已登录。
    """
    h = host or ""
    return any(h == d or h.endswith("." + d) for d in domains)


def _read_cookie_db(db: Path, domains: list[str]) -> ProfileCookieReport:
    """读取 Cookies 库中匹配域名的 cookie 数。

    浏览器运行时库被独占锁占用，先以共享模式读原始字节落到临时文件再开 SQLite
    （连同 -wal 文件，保证读到最新数据）。
    """
    tmp: Path | None = None
    try:
        fd, tmpname = tempfile.mkstemp(suffix=".cookies")
        os.close(fd)  # mkstemp 返回的 fd 不关闭会泄漏句柄（每读一个库漏一个）
        tmp = Path(tmpname)
        tmp.write_bytes(_read_file_share_all(db))
        wal = db.with_name(db.name + "-wal")
        if wal.is_file():
            try:
                tmp.with_name(tmp.name + "-wal").write_bytes(_read_file_share_all(wal))
            except OSError:
                pass  # wal 读不到则退化为主库快照
        con = sqlite3.connect(f"file:{tmp.as_posix()}?mode=ro", uri=True)
        try:
            rows = con.execute("SELECT host_key, name FROM cookies").fetchall()
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        return ProfileCookieReport(str(db), 0, [], f"无法读取: {e}")
    finally:
        if tmp:
            for p in (tmp, tmp.with_name(tmp.name + "-wal")):
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass  # 句柄尚未完全释放时容忍清理失败（临时目录文件，无害）
    count = 0
    indicative: list[str] = []
    for host, name in rows:
        if _host_matches(host, domains):
            count += 1
            indicative.append(name)
    return ProfileCookieReport(str(db), count, indicative)


#: Windows ERROR_SHARING_VIOLATION：文件被运行中的浏览器独占锁定
_LOCK_MARKER = ("err 32", "error 32", "sharing violation", "正在使用")


def _is_lock_error(msg: str) -> bool:
    low = (msg or "").lower()
    return any(m in low for m in _LOCK_MARKER)


def check_product(product: Product) -> dict:
    # 桌面客户端自带档案 + 系统浏览器档案 + 工具自有档案
    dbs: list[Path] = list(product.app_cookie_dbs)
    for g in BROWSER_GLOBS:
        dbs.extend(Path(p) for p in glob.glob(g))
    reports = [_read_cookie_db(d, product.domains) for d in dbs]
    ok_reports = [r for r in reports if not r.error]
    lock_reports = [r for r in reports if r.error and _is_lock_error(r.error)]
    total = sum(r.cookie_count for r in ok_reports)
    if ok_reports and total > 0:
        status = "LIKELY_LOGGED_IN"
        detail = f"共 {total} 枚该域名 cookie"
        ind = [n for r in ok_reports for n in r.indicative_found]
        if any(n in set(ind) for n in INDICATIVE_COOKIES.get(product.id, [])):
            detail += "，且命中会话指示 cookie"
    elif ok_reports:
        status = "NOT_LOGGED_IN"
        detail = "所有可读档案中均无该域名 cookie"
    elif lock_reports:
        status = "LOCKED"
        detail = ("cookie 库被运行中的浏览器独占锁定，无法离线判定；"
                  "关闭对应浏览器/客户端后重跑 check，或用 weblogin prepare 走工具自有档案")
    else:
        status = "UNKNOWN"
        detail = "; ".join(r.error for r in reports) or "未找到任何浏览器档案"
    return {
        "product": product.name, "product_id": product.id, "status": status,
        "detail": detail,
        "profiles": [{"profile": r.profile, "cookies": r.cookie_count,
                      "indicative": r.indicative_found[:5]} for r in reports],
    }


def check_all() -> list[dict]:
    return [check_product(p) for p in PRODUCTS]


CONFIG_TEMPLATE = {
    "_security_notice": [
        "此文件包含明文账号密码，仅保存在本机，请勿提交到任何仓库或网盘。",
        "工具不会将此文件内容发送到任何第三方；仅在浏览器自动化登录时使用。",
        "建议为此文件设置系统级访问权限（仅当前用户可读）。",
        "【当前状态：预留接口】prepare 采用人工登录，不读取本文件；",
        "它仅为将来实现自动登录时预留（届时建议改用系统 keyring，不再落明文）。",
    ],
    "_consumers": [],
    "products": {
        p.id: {"username": "", "password": "", "enabled": False,
               "note": f"{p.name} 登录凭据；enabled=true 才会被使用"}
        for p in PRODUCTS
    },
}


def init_config(out_path: Path) -> Path:
    """生成账号密码配置模板。

    注意：当前 prepare 流程为人工登录，本文件暂无消费者（预留接口）。
    生成后应确保其被 .gitignore 覆盖，避免误提交。
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(CONFIG_TEMPLATE, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    return out_path


def prepare(product_id: str) -> int:
    """三级流程第 3 步：打开持久化浏览器档案由用户手动完成登录。"""
    product = next((p for p in PRODUCTS if p.id == product_id), None)
    if product is None:
        print(f"未知产品: {product_id}（可选: {', '.join(p.id for p in PRODUCTS)}）")
        return 2
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("提示（第 3 级）：未安装 playwright，无法打开自动化浏览器。")
        print("  方案 A：pip install playwright && playwright install chromium，然后重跑本命令")
        print("  方案 B：在你的系统浏览器（Edge/Chrome）中手动登录该产品，然后重跑:")
        print(f"          python -m harvester weblogin check   （工具会读取系统浏览器档案）")
        return 1
    TOOL_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(TOOL_PROFILE_DIR), headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(product.login_url)
        print(f"已在自动化浏览器中打开 {product.name} 登录页。")
        print("请在窗口中手动完成登录（工具不代填表单），完成后回到此窗口按回车...")
        try:
            input()
        except EOFError:
            pass
        ctx.close()
    rep = check_product(product)
    tool_prof = [p for p in rep["profiles"] if "weblogin_profile" in p["profile"]]
    if tool_prof and any(p["cookies"] > 0 for p in tool_prof):
        print(f"[OK] {product.name} 登录态已写入工具档案（{tool_prof[0]['cookies']} 枚 cookie）。")
        return 0
    print("[!] 未能检测到工具档案中的登录 cookie；请确认登录是否成功后重试。")
    return 1
