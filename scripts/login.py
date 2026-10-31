# -*- coding: utf-8 -*-
"""打开指定招聘站点的登录页，等待用户手动登录。

用法：
    python -m scripts.main login boss
    python -m scripts.main login zhilian
    python -m scripts.main login job51
    python -m scripts.main login liepin

登录态写入 $JOBHUNT_HOME/.profile，所有采集脚本复用。
脚本只负责"把页面打开、等你扫码、确认登录成功"，不代填账号密码。
"""
import os
import sys

from DrissionPage import ChromiumOptions, ChromiumPage

from .core import paths

PROFILE = paths.profile_dir()
PORT = paths.port()

LOGIN_URL = {
    "boss": "https://www.zhipin.com/web/user/?intent=0",
    "zhilian": "https://passport.zhaopin.com/login",
    "job51": "https://login.51job.com/login.php",
    "liepin": "https://www.liepin.com/user/login/",
}

# 登录成功的判据：(检测 URL, 卡片选择器, 不应出现的未登录标记)
VERIFY = {
    "boss": (
        "https://www.zhipin.com/web/geek/jobs?query=Agent%E5%BC%80%E5%8F%91&city=101120200",
        "css:.job-card-wrapper",
        "登录/注册",
    ),
    "zhilian": (
        "https://www.zhaopin.com/sou/jl703/p1?kw=Agent%E5%BC%80%E5%8F%91",
        "css:.job-card",
        "登录/注册",
    ),
    "job51": (
        "https://we.51job.com/pc/search?jobArea=120300&keyword=Agent%E5%BC%80%E5%8F%91&searchType=2",
        "css:.joblist-item",
        None,  # 51job 未登录也能看到列表，用账号名判定
    ),
    "liepin": (
        "https://www.liepin.com/zhaopin/?key=Agent%E5%BC%80%E5%8F%91&dq=250070",
        "css:.jobDetailBox",
        "登录/注册",
    ),
}


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        print("用法：python -m scripts.main login <站点>")
        print("站点：" + " / ".join(LOGIN_URL))
        return 0
    site = sys.argv[1] if len(sys.argv) > 1 else "boss"
    if site not in LOGIN_URL:
        print(f"未知站点 {site}，可选：{', '.join(LOGIN_URL)}")
        return 2

    co = ChromiumOptions()
    co.set_user_data_path(PROFILE)
    co.set_local_port(PORT)
    # 非默认安装位置时用 CHROME_PATH 指定
    exe = os.environ.get("CHROME_PATH") or os.environ.get("BROWSER_PATH")
    if exe and os.path.isfile(exe):
        co.set_browser_path(exe)
    co.set_argument("--no-first-run")
    co.set_argument("--disable-blink-features=AutomationControlled")
    co.set_argument("--window-size=1400,900")

    page = ChromiumPage(co)
    page.get(LOGIN_URL[site])
    page.wait(2)

    print("=" * 56)
    print(f"  已打开 {site} 登录页")
    print("  请在浏览器窗口中完成扫码 / 账号登录")
    print("  登录成功后脚本会自动检测并继续（最长 5 分钟）")
    print("=" * 56)

    url, sel, bad_marker = VERIFY[site]
    import time

    deadline = time.time() + 300
    last_card_count = 0
    while time.time() < deadline:
        time.sleep(4)
        try:
            page.get(url)
            page.wait(3)
            cards = page.eles(sel)
            body = page.ele("tag:body", timeout=2)
            text = (body.text if body else "") or ""
            head = text[:1200]

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

    print(f"\n❌ {site} 登录超时。可重新运行本脚本。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
