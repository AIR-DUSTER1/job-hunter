# -*- coding: utf-8 -*-
"""增量合并：把新采集结果并进已有清单，保留用户手工维护的投递状态。

规则：
- 同一平台 + 同一投递链接（无链接时用 岗位名+公司名）视为同一岗位
- 已存在的记录：保留 status / remark 等人工字段，其余用最新采集值刷新
- 新出现的记录：追加，状态默认「待投递」
"""

from __future__ import annotations

import json
import os

from .models import Job


def _key(rec: dict) -> str:
    url = (rec.get("apply_url") or "").strip()
    if url:
        return url
    return f"{rec.get('platform','')}|{rec.get('title','')}|{rec.get('company','')}"


def load_existing(path: str) -> dict[str, Job]:
    """读取既有 JSON 清单，返回 key -> Job。"""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            rows = json.load(f)
    except Exception:
        return {}

    out: dict[str, Job] = {}
    valid = {f.name for f in Job.__dataclass_fields__.values()}
    for rec in rows:
        try:
            job = Job(**{k: v for k, v in rec.items() if k in valid})
            out[_key(rec)] = job
        except Exception:
            continue
    return out


def merge(
    new_jobs: list[Job],
    existing: dict[str, Job],
    active_platforms: set[str] | None = None,
) -> tuple[list[Job], int, int, int]:
    """合并，返回 (结果列表, 新增数, 更新数, 历史保留数)。

    active_platforms 表示本次实际采集的平台。旧记录里属于这些平台的、
    且这次没再出现的岗位说明已经下架，可以丢弃；而属于**其他平台**的记录
    （本次没跑）必须原样保留，否则只跑单平台就会把别的平台数据冲掉。

    人工维护字段（status / remark / greeting）以已有记录为准。
    """
    result: list[Job] = []
    added = updated = carried = 0

    for job in new_jobs:
        k = _key(job.__dict__)
        old = existing.get(k)
        if old is None:
            result.append(job)
            added += 1
            continue
        # 保留人工字段（招呼语也是人工/agent 写的结果，不能被采集覆盖）
        job.status = old.status or job.status
        job.remark = old.remark or job.remark
        job.greeting = old.greeting or job.greeting
        result.append(job)
        updated += 1

    new_keys = {_key(j.__dict__) for j in new_jobs}
    for k, old in existing.items():
        if k in new_keys:
            continue
        # 本次没采这个平台 → 原样保留全部记录
        if active_platforms is not None and old.platform not in active_platforms:
            result.append(old)
            carried += 1
            continue
        # 采过该平台但这条没再出现：只保留已有进展的记录（可能下架了）
        if (old.status or "待投递") != "待投递" or old.remark:
            result.append(old)
            carried += 1

    return result, added, updated, carried
