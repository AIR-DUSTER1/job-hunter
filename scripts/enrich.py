# -*- coding: utf-8 -*-
"""对已有清单做详情补全（JD / 地址 / HR活跃 / 公司规模 / 单双休 / 上下班）。

采集和补全分开跑的原因：站点风控看的是短时间内的请求总量。先采列表
（请求少、速度快），隔一段时间再单独补详情，比一次性全跑通过率高得多。

用法：
    python -m scripts.main enrich            # 补最近一次清单，每个平台补 10 条
    python -m scripts.main enrich --limit 20 --platforms boss
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .adapters import boss, job51, liepin, liepin_cli, zhilian
from .core import browser, paths
from .core.models import Job, export

OUT_DIR = paths.out_dir()
LATEST = os.path.join(OUT_DIR, "最新岗位清单.json")

ADAPTERS = {"51job": job51, "liepin": liepin, "zhilian": zhilian, "boss": boss}
# 各适配器对应的 platform 字段值
PLATFORM_NAME = {
    "51job": "前程无忧",
    "liepin": "猎聘",
    "zhilian": "智联招聘",
    "boss": "BOSS直聘",
}


def load_jobs() -> list[Job]:
    if not os.path.exists(LATEST):
        print(f"找不到清单：{LATEST}\n请先运行 python -m scripts.main collect 采集")
        sys.exit(1)
    with open(LATEST, "r", encoding="utf-8") as f:
        rows = json.load(f)
    valid = {f.name for f in Job.__dataclass_fields__.values()}
    return [Job(**{k: v for k, v in r.items() if k in valid}) for r in rows]


def _report_liepin_blocked(reason: str) -> None:
    """猎聘详情被风控时的提示：CLI 只能补列表，详情仍需浏览器。"""
    print(f"\n💡 猎聘详情被风控（{reason}），本条无法补全。", flush=True)
    if liepin_cli.available() and liepin_cli.auth_ok():
        print(
            "   已检测到猎聘 CLI：列表数据可自动改用 CLI 取，"
            "但 JD/地址/HR活跃需浏览器，请在浏览器里过一次验证后重跑本命令。",
            flush=True,
        )
    else:
        print(
            "   建议安装猎聘 CLI，列表数据即可绕过验证码：\n   "
            + liepin_cli.INSTALL_HINT.replace("\n", "\n   "),
            flush=True,
        )


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m scripts.main enrich",
                                    description="给已有清单补全岗位详情")
    ap.add_argument("--limit", type=int, default=10, help="每平台补几条")
    ap.add_argument("--platforms", default="51job,zhilian,liepin,boss")
    args = ap.parse_args()

    jobs = load_jobs()
    order = [p.strip() for p in args.platforms.split(",") if p.strip()]

    # BOSS 最敏感放最后
    order.sort(key=lambda p: 1 if p == "boss" else 0)

    total_done = 0
    for idx, name in enumerate(order):
        mod = ADAPTERS.get(name)
        pname = PLATFORM_NAME.get(name)
        if not mod or not pname:
            continue
        targets = [j for j in jobs if j.platform == pname]
        # 优先补还没抓到 JD 的
        targets.sort(key=lambda j: (bool(j.jd_text), -j.score))
        targets = targets[: args.limit]
        if not targets:
            print(f"[{name}] 清单里没有该平台的岗位，跳过")
            continue

        if idx > 0:
            browser.platform_cooldown(20)

        print(f"[{name}] 补 {len(targets)} 条详情……", flush=True)
        before = sum(1 for j in targets if j.jd_text)
        mod.enrich(targets, limit=len(targets))
        after = sum(1 for j in targets if j.jd_text)
        total_done += after - before
        print(f"[{name}] 本次新增 JD {after - before} 条（{after}/{len(targets)} 有 JD）",
              flush=True)

        # 猎聘被风控时，详情拿不到就提示 CLI 兜底能力
        if name == "liepin" and after == before and targets:
            reason = browser.is_blocked(browser.get_page())
            if reason:
                _report_liepin_blocked(reason)

    # 存回
    with open(LATEST, "w", encoding="utf-8") as f:
        json.dump([j.__dict__ for j in jobs], f, ensure_ascii=False, indent=1)

    import datetime as dt
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M")
    out_path = os.path.join(OUT_DIR, f"岗位清单_补全_{stamp}.xlsx")
    export(jobs, out_path)

    print(f"\n✅ 新增详情 {total_done} 条")
    print(f"   Excel: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
