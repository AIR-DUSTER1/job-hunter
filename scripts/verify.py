# -*- coding: utf-8 -*-
"""把浏览器停在指定站点，让用户手动通过验证（短信/滑块/登录）。

用法：
    python -m scripts.main verify liepin
    python -m scripts.main verify boss

脚本打开目标站点，每 5 秒检测一次是否已恢复正常页面，最多等 5 分钟。
验证通过后不用再做任何操作，直接重跑采集即可。

卡片选择器从 `selectors.json` 取，与适配器保持一致——之前这里是第二份硬编码副本，
改版时容易只改一处。
"""

from __future__ import annotations

import sys
import time

from .core import browser, selectors
from .core import canonical_platform

TARGETS = {
    "liepin": "https://www.liepin.com/zhaopin/?key=Agent%E5%BC%80%E5%8F%91&dq=250070",
    "boss": "https://www.zhipin.com/web/geek/jobs?query=Agent%E5%BC%80%E5%8F%91&city=101120200",
    "zhilian": "https://www.zhaopin.com/sou/jl703/p1?kw=Agent%E5%BC%80%E5%8F%91",
    "51job": "https://we.51job.com/pc/search?jobArea=120300&keyword=Agent%E5%BC%80%E5%8F%91&searchType=2",
}


def _card_selector(site: str) -> str:
    try:
        return selectors.primary(canonical_platform(site), "card")
    except selectors.SelectorRegistryError:
        return ""


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        print("用法：python -m scripts.main verify <站点>")
        print("站点：" + " / ".join(TARGETS))
        return 0
    site = canonical_platform(sys.argv[1]) if len(sys.argv) > 1 else "liepin"
    if site not in TARGETS:
        print(f"未知站点 {site}，可选：{', '.join(TARGETS)}")
        return 2

    sel = _card_selector(site)
    if not sel:
        print(f"⚠ 注册表里没有 {site} 的卡片选择器，无法判断是否已恢复")

    page = browser.get_page()
    browser.safe_get(page, TARGETS[site], wait=2)

    print("=" * 56)
    print(f"  已打开 {site}")
    print("  请在浏览器窗口内完成验证 / 登录")
    print("  检测到正常职位列表后脚本会自动结束（最长 5 分钟）")
    print("=" * 56)

    deadline = time.time() + 300
    while time.time() < deadline:
        time.sleep(5)
        try:
            blocked = browser.is_blocked(page)
            cards = browser.safe_eles(page, sel, timeout=4) if sel else []
            if cards and not blocked:
                print(f"\n✅ {site} 已恢复正常，检测到 {len(cards)} 个职位卡片")
                return 0
            print(f"… {'拦截中：' + blocked if blocked else '等待中'}", end="\r")
        except Exception as e:
            print(f"… 等待中（{str(e)[:30]}）", end="\r")

    print(f"\n⚠ {site} 仍在验证中，可稍后重跑本脚本。")
    if not browser.is_blocked(page):
        print("   若页面已正常但仍判不过，可能是卡片结构变了，跑：")
        print(f"   python -m scripts.main diagnose --platforms {site}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
