# -*- coding: utf-8 -*-
"""浏览器会话管理：统一复用 $JOBHUNT_HOME/.profile，避免每个适配器各开一个实例。

注意：DrissionPage 在同一 user-data-dir 上重复实例化会互踢连接，表现为
`PageDisconnectedError: 与页面的连接已断开`。因此本模块保证进程内只有一个
ChromiumPage，并在连接失效时重建一次。
"""

from __future__ import annotations

import atexit
import os

from DrissionPage import ChromiumOptions, ChromiumPage

from . import paths

PROFILE = paths.profile_dir()
PORT = paths.port()

_page: ChromiumPage | None = None


def _new_page() -> ChromiumPage:
    co = ChromiumOptions()
    co.set_user_data_path(PROFILE)
    co.set_local_port(PORT)
    # 非默认安装位置时用 CHROME_PATH 指定
    exe = os.environ.get("CHROME_PATH") or os.environ.get("BROWSER_PATH")
    if exe and os.path.isfile(exe):
        co.set_browser_path(exe)
    co.set_argument("--no-first-run")
    co.set_argument("--no-default-browser-check")
    co.set_argument("--disable-blink-features=AutomationControlled")
    co.set_argument("--window-size=1400,900")
    # 连接层面的硬超时：避免站点长时间不响应时整个流程无限阻塞
    try:
        co.set_timeouts(base=10, page_load=25, script=8)
    except Exception:
        pass
    return ChromiumPage(co)


# 全局加载超时（毫秒），在任何 page 操作前生效
LOAD_TIMEOUT_MS = 25000


def get_page(rebuild: bool = False) -> ChromiumPage:
    """返回全局共享的浏览器页面对象。"""
    global _page
    if _page is not None and not rebuild:
        return _page
    if _page is not None:
        try:
            _page.disconnect()
        except Exception:
            pass
    _page = _new_page()
    try:
        _page.set.timeouts(base=10, page_load=25, script=8)
    except Exception:
        pass
    return _page


def alive(page: ChromiumPage) -> bool:
    """探测 page 是否仍可用。"""
    try:
        _ = page.url
        return True
    except Exception:
        return False


def close_page() -> None:
    global _page
    if _page is not None:
        try:
            _page.quit()
        except Exception:
            pass
        _page = None


atexit.register(close_page)


def body_text(page: ChromiumPage, settle: float = 0.0) -> str:
    if settle:
        page.wait(settle)
    try:
        el = page.ele("tag:body", timeout=3)
        return (el.text if el else "") or ""
    except Exception:
        return ""


def safe_get(page: ChromiumPage, url: str, wait: float = 3.0) -> bool:
    """导航到 url 并等待。连接失效时重建一次浏览器再试。

    每次导航之间插入随机间隔，避免同一站点短时间高频访问触发风控
    （猎聘会跳 safe.liepin.com 短信验证，BOSS 会跳 _security_check）。
    """
    _throttle()
    try:
        page.get(url)
        page.wait(wait)
        return True
    except Exception:
        pass

    # 连接已断：重建（复用同一 profile，登录态保留）后重试一次
    try:
        page = get_page(rebuild=True)
        page.get(url)
        page.wait(wait)
        return True
    except Exception:
        return False


_last_nav = 0.0
_nav_count = 0
_MIN_GAP = 2.0    # 两次页面导航的最小间隔（秒）
_JITTER = 2.5     # 额外随机抖动上限（秒）
_COOL_EVERY = 8   # 每连续导航 N 次强制冷却一次，模拟真人阅读节奏
_COOL_SECONDS = 8.0


def _throttle() -> None:
    global _last_nav, _nav_count
    import random
    import time

    now = time.time()
    gap = _MIN_GAP + random.random() * _JITTER
    wait = _last_nav + gap - now
    if wait > 0:
        time.sleep(wait)

    _nav_count += 1
    # 站点风控主要看短时间内的连续请求；周期性长停能显著降低触发率
    if _nav_count % _COOL_EVERY == 0:
        time.sleep(_COOL_SECONDS)

    _last_nav = time.time()


def platform_cooldown(seconds: float = 20.0) -> None:
    """切换平台前冷却，避免同一会话对多站点连续高频访问被关联判定。"""
    import time
    time.sleep(seconds)


def is_blocked(page: ChromiumPage) -> str:
    """检测是否被站点的风控/验证页拦截，返回拦截页类型。"""
    try:
        u = page.url or ""
    except Exception:
        return ""
    if "safe.liepin.com" in u or "verifysms" in u:
        return "猎聘短信验证"
    if "_security_check" in u:
        return "BOSS 安全校验"
    if "captcha" in u.lower() or "verify" in u.lower():
        return "验证码"
    return ""


def safe_eles(page: ChromiumPage, selector: str, timeout: float = 6.0) -> list:
    """带超时的元素查询。DrissionPage 在连接半失效时可能无限阻塞，这里兜底。"""
    try:
        return page.eles(selector, timeout=timeout) or []
    except Exception:
        return []


def safe_ele(page: ChromiumPage, selector: str, timeout: float = 3.0):
    """带超时的单元素查询，找不到返回 None。"""
    try:
        return page.ele(selector, timeout=timeout)
    except Exception:
        return None
