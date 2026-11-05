# -*- coding: utf-8 -*-
"""招呼语生成：导出待写清单 → agent 逐条撰写 → 校验写回。

为什么拆成三步而不是脚本自动生成：招呼语要精准引用求职者的真实经历（哪个项目、
哪项技术、解决了什么问题），这需要读懂 JD 再做匹配判断，是模板拼不出来的。
本项目其余部分坚持"不依赖 LLM 接口"，所以这里把判断交给调用它的 agent，
脚本只负责准备材料和落库校验。

用法：

    # 1. 导出待写清单（含 JD、岗位要求、画像摘要）
    python -m scripts.main greet --export

    # 2. agent 读清单，逐条写好招呼语，存成 JSON：
    #    [{"key": "...", "greeting": "..."}, ...]

    # 3. 校验并写回 Excel 与快照
    python -m scripts.main greet --import greet.json

    # 只看进度
    python -m scripts.main greet --status
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .core import paths
from .core.models import GREETING_MAX_LEN, Job, export
from .core.merge import _key

OUT_DIR = paths.out_dir()
LATEST = os.path.join(OUT_DIR, "最新岗位清单.json")


def profile_path() -> str:
    """求职者画像的位置：数据目录下，follow JOBHUNT_HOME，不进 skill。"""
    return os.path.join(paths.home(), "profile.md")


_PROFILE_TEMPLATE = """# 求职者画像

> 写招呼语时只引用这里真实存在的内容，不要编造技术和项目。
> 有多个求职方向时，把各自的简历列出来，按岗位 JD 挑最贴的那份写。

## 基本信息

- 工作年限：
- 城市：
- 学历：
- 到岗时间：

## 按方向的简历（可选）

| 方向 | 简历文件 | 何时用 |
|---|---|---|
|  |  |  |

## 核心技术栈

- 主力：
- 后端：
- 其他：

## 项目弹药

写招呼语时从这里挑最贴 JD 的，越具体越好（带数字更有说服力）：

- **项目名**（起止时间）— 一句话定位
  - 你负责的部分：
  - 量化成果：
  - 关键技术：

## 写作要点

- 开头直接点岗位名，不要「您好，我是…」
- 中间 1–2 句对应 JD 的具体要求
- 优先用数字
- 不超过 200 字
- 不写薪资期望
- 不编造画像里没有的技术和项目
"""


def do_init_profile(force: bool = False) -> str:
    """在数据目录生成画像模板。已存在时不覆盖，除非 force。"""
    path = profile_path()
    if os.path.exists(path) and not force:
        print(f"画像已存在：{path}")
        print("要重新生成模板加 --force（会覆盖现有内容）")
        return path
    with open(path, "w", encoding="utf-8") as f:
        f.write(_PROFILE_TEMPLATE)
    print(f"已生成画像模板：{path}")
    print("请按自己的情况填写，或直接把简历内容摘进去。")
    print("填好后 greet --export 会把它附在物料里。")
    return path


def _read_profile() -> str:
    """读取画像正文。文件不存在返回空串——导出仍可用，只是没有个人经历可引用。"""
    path = profile_path()
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def load_jobs() -> list[Job]:
    if not os.path.exists(LATEST):
        print(f"找不到清单：{LATEST}\n请先运行 python -m scripts.main collect 采集")
        sys.exit(1)
    with open(LATEST, "r", encoding="utf-8") as f:
        rows = json.load(f)
    valid = {f.name for f in Job.__dataclass_fields__.values()}
    return [Job(**{k: v for k, v in r.items() if k in valid}) for r in rows]


def save_jobs(jobs: list[Job]) -> None:
    with open(LATEST, "w", encoding="utf-8") as f:
        json.dump([j.__dict__ for j in jobs], f, ensure_ascii=False, indent=1)


def _slim(job: Job) -> dict:
    """给 agent 看的精简物料：只留写招呼语真正需要的字段。

    JD 截到 1200 字——写招呼语不需要全文，省 token。
    """
    return {
        "key": _key(job.__dict__),
        "platform": job.platform,
        "title": job.title,
        "company": job.company,
        "salary": job.salary,
        "city": job.city,
        "experience": job.experience,
        "education": job.education,
        "score": round(job.score, 1),
        "industry": job.company_industry,
        "company_size": job.company_size,
        "financing": job.company_financing,
        "welfare": job.welfare,
        "work_rest": job.work_rest,
        "jd": (job.jd_text or "")[:1200],
        "existing_greeting": job.greeting,
    }


def do_export(*, top: int = 0, overwrite: bool = False) -> str:
    """导出待写招呼语的岗位清单。"""
    jobs = load_jobs()
    targets = jobs
    if not overwrite:
        targets = [j for j in targets if not (j.greeting or "").strip()]
    if top > 0:
        targets = sorted(targets, key=lambda j: -j.score)[:top]

    profile = _read_profile()
    payload = {
        "_note": (
            f"为每条岗位写一段不超过 {GREETING_MAX_LEN} 字的招呼语。"
            "要求：引用画像里与该 JD 真实匹配的经历；口吻自然、不用敬语堆砌；"
            "不要编造画像里没有的技术或项目；写好后存成 "
            '[{"key": "...", "greeting": "..."}] 再执行 --import。'
        ),
        "max_len": GREETING_MAX_LEN,
        "profile": profile,
        "profile_found": bool(profile),
        "count": len(targets),
        "items": [_slim(j) for j in targets],
    }

    path = os.path.join(OUT_DIR, "待写招呼语.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    print(f"已导出 {len(targets)} 条待写岗位 → {path}")
    if not profile:
        print(f"⚠ 没有找到画像文件：{profile_path()}")
        print("  招呼语将无法引用你的具体经历。先运行：")
        print("  python -m scripts.main greet --init-profile")
    if len(targets) < len(jobs):
        print(f"（跳过 {len(jobs) - len(targets)} 条已有招呼语的，加 --overwrite 重写全部）")
    return path


def do_status() -> None:
    jobs = load_jobs()
    total = len(jobs)
    done = sum(1 for j in jobs if (j.greeting or "").strip())
    print(f"招呼语进度：{done}/{total}")
    if done < total:
        rest = sorted((j for j in jobs if not (j.greeting or "").strip()),
                      key=lambda j: -j.score)
        print(f"待写 {len(rest)} 条，前 5：")
        for j in rest[:5]:
            print(f"  {j.score:>5.1f}  {j.platform:<6} {j.title[:24]:<26} {j.company[:18]}")


def do_import(path: str) -> int:
    """校验并写回 agent 生成的招呼语。返回写入条数。"""
    if not os.path.exists(path):
        print(f"找不到文件：{path}")
        return 0
    with open(path, "r", encoding="utf-8") as f:
        items = json.load(f)

    # 允许传数组，也允许传 {"items": [...]}
    if isinstance(items, dict):
        items = items.get("items") or []

    jobs = load_jobs()
    by_key = {_key(j.__dict__): j for j in jobs}

    written = 0
    skipped: list[str] = []
    truncated = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        key = (it.get("key") or "").strip()
        text = (it.get("greeting") or "").strip()
        if not key or not text:
            skipped.append(f"缺 key 或 greeting：{str(it)[:60]}")
            continue
        job = by_key.get(key)
        if job is None:
            skipped.append(f"清单里找不到该岗位：{key[:60]}")
            continue
        if len(text) > GREETING_MAX_LEN:
            text = text[:GREETING_MAX_LEN]
            truncated += 1
        job.greeting = text
        written += 1

    save_jobs(jobs)

    # 重新导出 Excel，让招呼语直接出现在表里
    import datetime as dt
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M")
    out_path = os.path.join(OUT_DIR, f"岗位清单_招呼语_{stamp}.xlsx")
    export(jobs, out_path)

    print(f"✅ 写入招呼语 {written} 条")
    if truncated:
        print(f"   {truncated} 条超过 {GREETING_MAX_LEN} 字，已截断")
    if skipped:
        print(f"   跳过 {len(skipped)} 条：")
        for s in skipped[:5]:
            print(f"     - {s}")
    print(f"   Excel: {out_path}")
    return written


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="python -m scripts.main greet",
        description="招呼语：导出待写清单 / 校验写回",
    )
    ap.add_argument("--export", action="store_true", help="导出待写清单")
    ap.add_argument("--import", dest="import_path", default="", metavar="FILE",
                    help="导入 agent 写好的招呼语 JSON")
    ap.add_argument("--status", action="store_true", help="查看进度")
    ap.add_argument("--init-profile", action="store_true",
                    help="在数据目录生成 profile.md 模板")
    ap.add_argument("--force", action="store_true", help="配合 --init-profile 覆盖已有画像")
    ap.add_argument("--top", type=int, default=0, help="只导出得分最高的 N 条")
    ap.add_argument("--overwrite", action="store_true", help="连同已有招呼语的一起重写")
    args = ap.parse_args()

    if args.init_profile:
        do_init_profile(force=args.force)
        return 0
    if args.import_path:
        return 0 if do_import(args.import_path) else 1
    if args.status:
        do_status()
        return 0
    if args.export:
        do_export(top=args.top, overwrite=args.overwrite)
        return 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
