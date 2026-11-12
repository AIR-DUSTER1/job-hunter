# -*- coding: utf-8 -*-
"""核心层：浏览器、DOM、选择器注册表、健康检查、数据模型、打分与合并。

这里只放与具体平台无关的逻辑。平台相关的都在 `scripts/adapters/`。
"""

from __future__ import annotations

# 平台 key 的规范名。命令行的 --platforms 允许用户写别名，统一归一到这里，
# 避免 collect 用 "51job"、diagnose 用 "job51" 这类不一致。
PLATFORM_KEYS = ("51job", "liepin", "zhilian", "boss")

_ALIASES = {
    "51job": "51job",
    "job51": "51job",
    "51": "51job",
    "前程无忧": "51job",
    "liepin": "liepin",
    "leipin": "liepin",
    "猎聘": "liepin",
    "zhilian": "zhilian",
    "zhaopin": "zhilian",
    "智联": "zhilian",
    "智联招聘": "zhilian",
    "boss": "boss",
    "boss直聘": "boss",
    "zhipin": "boss",
}


def canonical_platform(name: str) -> str:
    """把用户输入的平台名归一成规范 key。不认识的原样返回（交给调用方报错）。"""
    key = (name or "").strip().lower()
    return _ALIASES.get(key, key)


def split_platforms(raw: str) -> list[str]:
    """解析 --platforms 参数，逗号分隔，逐个归一，去重保序。"""
    out: list[str] = []
    for part in (raw or "").split(","):
        key = canonical_platform(part)
        if key and key not in out:
            out.append(key)
    return out
