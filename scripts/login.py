# -*- coding: utf-8 -*-
"""打开指定招聘站点的登录页，等待用户手动登录。

用法：
    python -m scripts.main login boss
    python -m scripts.main login zhilian
    python -m scripts.main login 51job
    python -m scripts.main login liepin

登录态写入 $JOBHUNT_HOME/.profile，所有采集脚本复用。
脚本只负责"把页面打开、等你扫码、确认登录成功"，不代填账号密码。

卡片选择器从 `selectors.json` 取——之前这里硬编码成 `.job-card-wrapper`（多一个
-er），与适配器的 `.job-card-wrap` 不一致，导致 BOSS 登录永远判不成功、只能等满
5 分钟超时。走注册表后这类漂移不会再出现。
"""

from __future__ import annotations

import sys

from .core import browser, selectors
from .core import canonical_platform

LOGIN_URL = {
    "boss": "https://www.zhipin.com/web/user/?intent=0",
    "zhilian": "https://passport.zhaopin.com/login",
    "51job": "https://login.51job.com/login.php",
    "liepin": "https://www.liepin.com/user/login/",
}

# 登录成功后去哪个页面验证、以及不应出现的未登录标记。
# 卡片选择器由注册表提供（见 core/selectors.py）。
VERIFY = {
    "boss": (
        "https://www.zhipin.com/web/geek/jobs?query=Agent%E5%BC%80%E5%8F%91&city=101120200",
        "登录/注册",
    ),
    "zhilian": (
        "https://www.zhaopin.com/sou/jl703/p1?kw=Agent%E5%BC%80%E5%8F%91",
        "登录/注册",
    ),
    "51job": (
        "https://we.51job.com/pc/search?jobArea=120300&keyword=Agent%E5%BC%80%E5%8F%91&searchType=2",
        None,  # 51job 未登录也能看到列表，用账号名判定
    ),
    "liepin": (
        "https://www.liepin.com/zhaopin/?key=Agent%E5%BC%80%E5%8F%91&dq=250070",
        "登录/注册",
    ),
}


def _card_selector(site: str) -> str:
    """从注册表取该站点的卡片选择器。

    站点参数允许写别名（job51 / 51job 都行），注册表内部统一用 51job。
    """
    try:
        return selectors.primary(canonical_platform(site), "card")
    except selectors.SelectorRegistryError:
        return ""


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        print("用法：python -m scripts.main login <站点>")
        print("站点：" + " / ".join(LOGIN_URL))
        return 0
    site = canonical_platform(sys.argv[1]) if len(sys.argv) > 1 else "boss"
    if site not in LOGIN_URL:
        print(f"未知站点 {site}，可选：{', '.join(LOGIN_URL)}")
        return 2

    sel = _card_selector(site)
    if not sel:
        print(f"⚠ 注册表里没有 {site} 的卡片选择器，登录检测会不稳定")

    # 用共享实例：core/browser 保证同一 profile 只有一个 ChromiumPage，
    # 自建第二个实例会互踢连接（PageDisconnectedError）。
    page = browser.get_page()
    browser.safe_get(page, LOGIN_URL[site], wait=2)

    print("=" * 56)
    print(f"  已打开 {site} 登录页")
    print("  请在浏览器窗口中完成扫码 / 账号登录")
    print("  登录成功后脚本会自动检测并继续（最长 5 分钟）")
    print("=" * 56)

    url, bad_marker = VERIFY[site]
    import time

    deadline = time.time() + 300
    last_card_count = 0
    while time.time() < deadline:
        time.sleep(4)
        try:
            browser.safe_get(page, url, wait=3)
            cards = browser.safe_eles(page, sel, timeout=4) if sel else []
            head = browser.body_text(page)[:1200]

            logged_in = False
            if cards:
                if bad_marker:
                    logged_in = bad_marker not in head
                else:
                    # 51job：看右上角是否出现账号相关元素
                    logged_in = "登录/注册" not in head
            if logged_in:
                print(f"\n✅ {site} 登录成功，检测到 {len(cards)} 个岗位卡片")
                return 0
            if len(cards) != last_card_count:
                last_card_count = len(cards)
                print(f"… 当前卡片数 {len(cards)}，仍等待登录")
            else:
                print("… 等待登录", end="\r")
        except Exception as e:
            print(f"… 等待中（{str(e)[:40]}）", end="\r")

    # 超时时区分"选择器失效"与"确实没登录完"，避免用户白等
    print(f"\n❌ {site} 登录超时。")
    if sel and not last_card_count:
        print("   卡片数一直是 0。若你确认已登录，可能是站点结构变了，跑：")
        print(f"   python -m scripts.main diagnose --platforms {site}")
    else:
        print("   可重新运行本脚本。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
