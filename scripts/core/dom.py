# -*- coding: utf-8 -*-
"""统一的 DOM 访问与结构推断。

两件事：

1. **候选链访问**：`find/finds` 从 `selectors.json` 的候选链里按序试，命中即返回。
   首项落空、靠后备项命中时记一条 fallback 事件——这是"站点可能改版了"的早期信号，
   诊断报告会把它列出来。

2. **结构推断**：站点真改版后 `infer_card_candidates()` 用纯结构特征猜出卡片容器，
   不依赖任何站点知识。判据是"同签名元素重复出现 + 每个都含链接 + 文本量像一条职位"，
   这类重复列表块在职位列表页几乎只有一处符合。

统计（`stats()`）供健康检查用，`reset_stats()` 每次诊断前清一次。
"""

from __future__ import annotations

import os
import re

from . import selectors
from .browser import safe_ele, safe_eles

# ---------------------------------------------------------------- 命中统计

_stats: dict[str, dict] = {}


def reset_stats() -> None:
    _stats.clear()


def _record(platform: str, key: str, hit: bool, by: str = "") -> None:
    slot = _stats.setdefault(platform, {}).setdefault(
        key, {"hit": 0, "miss": 0, "fallback": 0, "fallback_by": ""}
    )
    if hit:
        slot["hit"] += 1
        if by and by != selectors.primary(platform, key):
            slot["fallback"] += 1
            slot["fallback_by"] = by
    else:
        slot["miss"] += 1


def stats() -> dict:
    return {p: {k: dict(v) for k, v in keys.items()} for p, keys in _stats.items()}


def fallbacks() -> list[dict]:
    """本次运行里靠后备选择器才命中的字段——改版的早期信号。"""
    out = []
    for plat, keys in _stats.items():
        for key, v in keys.items():
            if v.get("fallback"):
                out.append({
                    "platform": plat,
                    "key": key,
                    "primary": selectors.primary(plat, key),
                    "used": v.get("fallback_by", ""),
                    "count": v["fallback"],
                })
    return out


# ---------------------------------------------------------------- 候选链访问

def finds(container, platform: str, key: str, timeout: float = 1.0) -> list:
    """按候选链取多个元素。都为空时返回 []。"""
    try:
        chain = selectors.candidates(platform, key)
    except selectors.SelectorRegistryError:
        return []
    for sel in chain:
        got = safe_eles(container, sel, timeout=timeout)
        if got:
            _record(platform, key, True, sel)
            return got
    _record(platform, key, False)
    return []


def find(container, platform: str, key: str, timeout: float = 1.0):
    """按候选链取单个元素。都为空时返回 None。"""
    try:
        chain = selectors.candidates(platform, key)
    except selectors.SelectorRegistryError:
        return None
    for sel in chain:
        el = safe_ele(container, sel, timeout=timeout)
        if el:
            _record(platform, key, True, sel)
            return el
    _record(platform, key, False)
    return None


def find_one_of(container, chain: list[str], timeout: float = 1.0) -> list:
    """直接给一组选择器（不查注册表），返回 [(selector, element)]。

    推理出的候选还没进注册表时，用小范围验证卡片质量。
    """
    out = []
    for sel in chain:
        el = safe_ele(container, sel, timeout=timeout)
        if el:
            out.append((sel, el))
    return out


# ---------------------------------------------------------------- 结构推断
#
# 在页面里按「同签名重复 + 含链接 + 文本量像职位」给元素分组打分。
# 签名 = tagName + 排序后的 class 名，避免动态 class（keji-xxx-1）把同一类拆散。

_INFER_JS = r"""
return (function(){
  var opts = arguments[0] || {};
  var minRepeat = opts.minRepeat || 6;
  var maxRepeat = opts.maxRepeat || 80;
  var keywords = opts.keywords || [];
  // 排除导航、页脚、筛选下拉、弹窗等永远不是职位卡片的区域。
  // filter/select-box 尤其重要：智联的筛选面板同样是"重复多次的多行文本块"，
  // 在没有详情链接可判断的平台（见 diagnose._LINKLESS_CARDS）上会被误判成卡片。
  var exclude = /nav|footer|header|menu|crumb|sidebar|breadcrumb|pagination|pager|toolbar|tab-|advert|banner|slogan|copyright|login|register|filter|select-box|dropdown|popover|modal|dialog|toast|skeleton|loading|placeholder/i;

  function sig(el){
    var cls = (el.className && typeof el.className === 'string') ? el.className : '';
    // 去掉带数字后缀的动态 class，保留语义部分，避免同一类被拆散
    var parts = cls.split(/\s+/).filter(Boolean)
      .map(function(c){ return c.replace(/[-_]?\d+$/,''); })
      .filter(function(c){ return c && c.length > 2; })
      .sort();
    return el.tagName.toLowerCase() + (parts.length ? '.' + parts.join('.') : '');
  }

  // 生成**能真正选中**该组元素的选择器。
  // 签名里剥掉了数字后缀（job-item-x1 → job-item-x），直接拿它当选择器会命中 0 个。
  // 这里优先用组内元素共有、且不含动态数字的 class；都没有时退回 attribute 前缀匹配。
  function buildSelector(el, tagLower){
    var cls = (el.className && typeof el.className === 'string') ? el.className.split(/\s+/).filter(Boolean) : [];
    var stable = cls.filter(function(c){ return !/[-_]?\d+$/.test(c) && c.length > 2; });
    if (stable.length) {
      // 优先用只含这个稳定 class 的选择器，再看它是否恰好命中整组
      return tagLower + stable.map(function(c){ return '.' + c; }).join('');
    }
    // 全部 class 都带数字（job-item-x1）：用「class 前缀」属性选择器
    var dyn = cls.filter(function(c){ return c.length > 2; })
                 .map(function(c){ return c.replace(/[-_]?\d+$/,''); });
    if (dyn.length) {
      return tagLower + '[class^="' + dyn[0] + '"]';
    }
    return tagLower;
  }

  function depth(el){
    var d = 0, n = el;
    while (n && n.parentElement) { n = n.parentElement; d++; if (d > 40) break; }
    return d;
  }

  var groups = {};
  var nodes = document.body ? document.body.querySelectorAll('*') : [];
  for (var i = 0; i < nodes.length; i++) {
    var el = nodes[i];
    if (!el || el.nodeType !== 1) continue;
    var s = sig(el);
    if (!s) continue;
    if (exclude.test(s)) continue;
    (groups[s] = groups[s] || []).push(el);
  }

  var scored = [];
  for (var k in groups) {
    var arr = groups[k];
    if (arr.length < minRepeat || arr.length > maxRepeat) continue;
    var d = depth(arr[0]);
    // 只排除 body/html 这一层。布局容器的误判靠"重复数 + 含链接 + 文本量"
    // 三个信号就能排掉，再加深度阈值反而会漏掉 body>列表>卡片 这种常见结构。
    if (d < 2) continue;

    var withLink = 0, texty = 0, kwHit = 0, sample = '';
    for (var j = 0; j < arr.length; j++) {
      var e = arr[j];
      if (e.querySelector('a[href]')) withLink++;
      var t = (e.innerText || '').trim();
      if (t.length >= 20 && t.length <= 2000) texty++;
      if (j === 0) sample = t.slice(0, 120);
      if (keywords.length && keywords.some(function(w){ return t.indexOf(w) >= 0; })) kwHit++;
    }
    if (!texty) continue;

    // 打分：重复度 × 含链接比例 × 文本合格比例，关键词命中额外加权
    var score = arr.length * (withLink / arr.length) * (texty / arr.length);
    if (keywords.length) score *= (1 + kwHit / arr.length);

    var sel = buildSelector(arr[0], k.split('.')[0]);

    // 生成的选择器必须真能选中元素，否则固化进注册表等于没修。
    // 命中数明显少于分组数（<50%）说明只覆盖了组内一部分，同样不可用。
    var matches = 0;
    try { matches = document.querySelectorAll(sel).length; } catch (e) { matches = 0; }
    if (!matches || matches < arr.length * 0.5) continue;

    scored.push({selector: sel, count: arr.length, matches: matches,
                 withLink: withLink, texty: texty, kwHit: kwHit,
                 depth: d, score: score, sample: sample});
  }

  scored.sort(function(a,b){ return b.score - a.score; });
  return scored.slice(0, opts.limit || 8);
})(arguments[0])
"""


def _run_js(page, js: str, arg: dict):
    """page.run_js 的容错包装：DrissionPage 把 args 映射成 arguments[n]。"""
    try:
        return page.run_js(js, arg)
    except Exception:
        return None


_CARD_KEYWORDS = ("招聘", "K", "k", "薪", "年", "本科", "大专", "经验", "公司")
_DETAIL_KEYWORDS = ("职位描述", "岗位职责", "职位介绍", "工作职责", "任职要求")


def infer_card_candidates(page, limit: int = 5) -> list[dict]:
    """推断列表页的卡片容器选择器。返回 [{selector, count, score, sample, ...}]。"""
    js = _INFER_JS.replace("(...)", "(arguments[0])")
    data = _run_js(page, js, {
        "minRepeat": 6, "maxRepeat": 80, "limit": limit, "keywords": list(_CARD_KEYWORDS),
    })
    return _clean_inference(data)


def infer_detail_candidates(page, limit: int = 5) -> list[dict]:
    """推断详情页的 JD 文本容器。偏重含 JD 标志词的文本块。"""
    js = _INFER_JS.replace("(...)", "(arguments[0])")
    data = _run_js(page, js, {
        "minRepeat": 1, "maxRepeat": 80, "limit": limit, "keywords": list(_DETAIL_KEYWORDS),
    })
    return _clean_inference(data)


def _clean_inference(data) -> list[dict]:
    if not isinstance(data, list):
        return []
    out = []
    for d in data:
        if not isinstance(d, dict):
            continue
        sel = (d.get("selector") or "").strip()
        if not sel or re.search(r"[^\w.\-#\[\]='\": >*^$~()]", sel):
            continue
        count = int(d.get("count") or 0)
        matches = int(d.get("matches") or 0)
        # JS 侧已经过滤过，这里再兜一次：选择器选不中就丢弃
        if matches and matches < count * 0.5:
            continue
        out.append({
            "selector": "css:" + sel,
            "count": count,
            "matches": matches,
            "with_link": int(d.get("withLink") or 0),
            "kw_hit": int(d.get("kwHit") or 0),
            "depth": int(d.get("depth") or 0),
            "score": float(d.get("score") or 0),
            "sample": (d.get("sample") or "")[:120],
        })
    return out


# ---------------------------------------------------------------- 快照

def snapshot(page, platform: str, out_dir: str) -> dict:
    """落盘 HTML + 全页截图，供人工复盘改版后的结构。"""
    paths_out: dict[str, str] = {}
    import datetime as dt
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")

    try:
        html_path = os.path.join(out_dir, f"{platform}_{stamp}.html")
        html = page.run_js("return document.documentElement.outerHTML;")
        if isinstance(html, str) and html:
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html)
            paths_out["html"] = html_path
    except Exception:
        pass

    try:
        shot_path = os.path.join(out_dir, f"{platform}_{stamp}.png")
        page.get_screenshot(path=out_dir, name=f"{platform}_{stamp}")
        if os.path.isfile(shot_path):
            paths_out["screenshot"] = shot_path
    except Exception:
        pass

    return paths_out
