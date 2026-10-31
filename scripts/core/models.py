# -*- coding: utf-8 -*-
"""岗位数据模型与 Excel 导出。

字段覆盖用户需求：
- 平台、岗位、公司、薪资、地区、经验、学历
- 打分、外包判定
- 上架时间、HR 最近活跃、公司规模、公司简介
- 单双休、上下班时间、岗位福利
- 投递状态、投递地址、岗位链接
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field, asdict
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

# ---------------------------------------------------------------- 数据模型

# 投递状态枚举（Excel 下拉选项）
STATUS_OPTIONS = ["待投递", "已投递", "已面试", "已结束"]


@dataclass
class Job:
    """一条岗位记录。"""

    platform: str = ""           # 平台：BOSS直聘 / 智联招聘 / 前程无忧 / 猎聘
    title: str = ""              # 岗位名称
    company: str = ""            # 公司名称
    salary: str = ""             # 原始薪资文本
    salary_num: float = 0.0      # 月薪中位数（K），用于打分
    city: str = ""               # 城市 + 区
    experience: str = ""         # 经验要求
    education: str = ""          # 学历要求

    score: float = 0.0           # 综合得分 0-100
    score_detail: str = ""       # 得分拆解
    is_outsourcing: str = ""     # 是 / 否 / 待确认
    outsourcing_reason: str = "" # 判定理由

    published: str = ""          # 上架时间
    hr_active: str = ""          # HR 最近活跃
    hr_name: str = ""            # HR 姓名/职位
    company_size: str = ""       # 公司规模
    company_intro: str = ""      # 公司简介（截断）
    company_industry: str = ""   # 行业
    company_financing: str = ""  # 融资阶段

    work_rest: str = ""          # 单双休
    work_hours: str = ""         # 上下班时间
    welfare: str = ""            # 岗位福利
    address: str = ""            # 工作地址（投递地址）

    status: str = "待投递"       # 投递状态
    apply_url: str = ""          # 投递/详情链接
    jd_text: str = ""            # 职位描述全文
    fetched_at: str = ""         # 采集时间
    remark: str = ""             # 备注
    direction: str = ""          # 岗位方向（用于 Excel 分 sheet）
    company_url: str = ""        # 公司主页，补规模/行业/融资用

    def to_row(self) -> list[Any]:
        return [
            self.platform, self.title, self.company, self.salary, self.city,
            self.experience, self.education, round(self.score, 1),
            self.is_outsourcing, self.outsourcing_reason,
            self.published, self.hr_active, self.hr_name,
            self.company_size, self.company_industry, self.company_financing,
            self.work_rest, self.work_hours, self.welfare,
            self.address, self.status, self.apply_url, self.remark,
            self.score_detail, self.company_intro, self.jd_text, self.fetched_at,
        ]


HEADERS = [
    ("平台", 12), ("岗位名称", 26), ("公司名称", 26), ("薪资", 14), ("地区", 16),
    ("经验要求", 12), ("学历要求", 10), ("综合得分", 10),
    ("是否外包", 10), ("外包判定理由", 26),
    ("上架时间", 18), ("HR最近活跃", 20), ("HR", 14),
    ("公司规模", 14), ("行业", 18), ("融资阶段", 12),
    ("单双休", 12), ("上下班时间", 18), ("岗位福利", 34),
    ("工作地址", 34), ("投递状态", 10), ("投递链接", 46), ("备注", 20),
    ("得分拆解", 40), ("公司简介", 60), ("职位描述", 80), ("采集时间", 18),
]

# 短表头视图（先给用户看的核心列）
CORE_COLUMNS = [
    "平台", "岗位名称", "公司名称", "薪资", "地区", "综合得分",
    "是否外包", "HR最近活跃", "公司规模", "单双休", "上下班时间", "投递状态",
]


# ---------------------------------------------------------------- Excel 导出

_HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
_HEADER_FONT = Font(color="FFFFFF", bold=True, size=11)
_OUTSOURCE_FILL = PatternFill("solid", fgColor="FCE4E4")
_GOOD_FILL = PatternFill("solid", fgColor="E2F0D9")
_THIN = Side(style="thin", color="BFBFBF")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)


def _write_sheet(ws, jobs: list[Job], *, apply_validation: bool, show_direction: bool) -> None:
    """把一批岗位写进指定 sheet，统一样式。"""
    headers = HEADERS
    if show_direction:
        headers = HEADERS[:1] + [("岗位方向", 14)] + HEADERS[1:]

    for idx, (name, width) in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=idx, value=name)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.column_dimensions[get_column_letter(idx)].width = width

    for r, job in enumerate(jobs, start=2):
        row = job.to_row()
        if show_direction:
            row = row[:1] + [job.direction] + row[1:]
        for c, value in enumerate(row, start=1):
            cell = ws.cell(row=r, column=c, value=value)
            cell.border = _BORDER
            cell.alignment = Alignment(vertical="top", wrap_text=c >= 17)

        # 外包行整行淡红，高分非外包行淡绿
        if job.is_outsourcing == "是":
            fill = _OUTSOURCE_FILL
        elif job.score >= 75:
            fill = _GOOD_FILL
        else:
            fill = None
        if fill:
            for c in range(1, len(headers) + 1):
                ws.cell(row=r, column=c).fill = fill

    ws.freeze_panes = "D2" if show_direction else "C2"
    if jobs:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{len(jobs) + 1}"

    # 投递状态下拉
    if apply_validation and jobs:
        status_idx = [name for name, _ in headers].index("投递状态") + 1
        status_col = get_column_letter(status_idx)
        dv = DataValidation(
            type="list",
            formula1='"' + ",".join(STATUS_OPTIONS) + '"',
            allow_blank=False,
        )
        dv.error = "请从下拉列表选择"
        dv.prompt = "选择当前投递进度"
        ws.add_data_validation(dv)
        dv.add(f"{status_col}2:{status_col}{len(jobs) + 1}")


def _sheet_title(name: str, used: set[str]) -> str:
    """Excel sheet 名不能超过 31 字符，也不能含 []:*?/\\ 。"""
    t = re.sub(r"[\[\]:*?/\\]", "_", (name or "其他").strip()) or "其他"
    t = t[:31]
    base, n = t, 2
    while t in used:
        suffix = f"_{n}"
        t = base[: 31 - len(suffix)] + suffix
        n += 1
    used.add(t)
    return t


def export(
    jobs: list[Job],
    path: str,
    *,
    apply_validation: bool = True,
    split_by_direction: bool = True,
) -> str:
    """把岗位列表写成 Excel。

    split_by_direction=True 时按岗位方向拆成多个 sheet（每个方向一页），
    外加一个「全部岗位」总表；False 时保持单页。
    """
    wb = Workbook()

    if not split_by_direction or not any(j.direction for j in jobs):
        ws = wb.active
        ws.title = "岗位清单"
        _write_sheet(ws, jobs, apply_validation=apply_validation, show_direction=False)
        _write_summary(wb, jobs)
        wb.save(path)
        return path

    # 按方向分组，保持每个方向内部按分数降序
    groups: dict[str, list[Job]] = {}
    for job in jobs:
        groups.setdefault(job.direction or "其他", []).append(job)

    # 方向顺序：按该方向岗位数降序，让主要方向排在前面
    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))

    used: set[str] = {"全部岗位"}
    first = True
    for direction, items in ordered:
        items.sort(key=lambda j: -j.score)
        if first:
            ws = wb.active
            ws.title = _sheet_title(direction, used)
            first = False
        else:
            ws = wb.create_sheet(_sheet_title(direction, used))
        _write_sheet(ws, items, apply_validation=apply_validation, show_direction=False)

    # 总表放最前面，方便跨方向对比
    total_ws = wb.create_sheet("全部岗位", 0)
    _write_sheet(total_ws, jobs, apply_validation=apply_validation, show_direction=True)

    _write_summary(wb, jobs, groups)
    wb.save(path)
    return path


def _write_summary(wb, jobs: list[Job], groups: dict[str, list[Job]] | None = None) -> None:
    """统计摘要 sheet。"""
    ws2 = wb.create_sheet("汇总")
    ws2.column_dimensions["A"].width = 22
    ws2.column_dimensions["B"].width = 16
    ws2["A1"] = "指标"
    ws2["B1"] = "数值"
    for c in (ws2["A1"], ws2["B1"]):
        c.fill = _HEADER_FILL
        c.font = _HEADER_FONT

    total = len(jobs)
    outsourced = sum(1 for j in jobs if j.is_outsourcing == "是")
    avg = round(sum(j.score for j in jobs) / total, 1) if total else 0
    by_platform: dict[str, int] = {}
    for j in jobs:
        by_platform[j.platform] = by_platform.get(j.platform, 0) + 1

    rows = [
        ("岗位总数", total),
        ("外包岗位数", outsourced),
        ("平均得分", avg),
        ("最高得分", max((j.score for j in jobs), default=0)),
        ("采集时间", dt.datetime.now().strftime("%Y-%m-%d %H:%M")),
    ]
    for i, (k, v) in enumerate(rows, start=2):
        ws2.cell(row=i, column=1, value=k)
        ws2.cell(row=i, column=2, value=v)

    r = len(rows) + 3
    if groups:
        ws2.cell(row=r, column=1, value="按岗位方向").font = Font(bold=True)
        for direction, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            r += 1
            ws2.cell(row=r, column=1, value=direction)
            ws2.cell(row=r, column=2, value=len(items))
        r += 2

    ws2.cell(row=r, column=1, value="按平台").font = Font(bold=True)
    for k, v in by_platform.items():
        r += 1
        ws2.cell(row=r, column=1, value=k)
        ws2.cell(row=r, column=2, value=v)
