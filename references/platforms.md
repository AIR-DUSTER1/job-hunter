# 平台适配参考

只在需要调整适配器时读这份。正常采集不需要。

## 城市编码表

每个平台的编码体系完全不同，必须分别维护。

| 城市 | BOSS | 智联 (jl) | 51job (jobArea) | 猎聘 (dq) |
|---|---|---|---|---|
| 青岛 | 101120200 | 703 | 120300 | 250070 |
| 济南 | 101120100 | 702 | 120200 | 250020 |
| 北京 | 101010100 | 530 | 010000 | 010 |
| 上海 | 101020100 | 538 | 020000 | 020 |
| 深圳 | 101280600 | 765 | 040000 | 050090 |
| 杭州 | 101210100 | 653 | 080200 | 070020 |
| 成都 | 101270100 | 801 | 090200 | 280020 |

### 新增城市

1. BOSS：打开目标城市职位页，地址栏 `city=` 后的数字
2. 智联：访问 `https://www.zhaopin.com/<城市拼音>/`，页面里 `/sou/jl<数字>` 的即 jl 码
3. 51job：搜索后看地址栏 `jobArea=` 的值
4. 猎聘：打开 `https://www.liepin.com/city-<城市拼音>/`，点「职位」后的 URL 里 `dq=` 的值

然后加到对应适配器顶部的 `CITY_*` 字典：

```python
# adapters/boss.py
CITY_CODE = {"青岛": "101120200", "新城市": "编码"}
```

## BOSS 直聘

**字段来源**：列表页 `.job-card-wrap`。

### 薪资是字体混淆的

薪资数字被替换成 Unicode 私有区字符，页面上显示成 `K` 或空白。映射关系：

```
U+E031 → 0
U+E032 → 1
...
U+E039 → 8
```

即 `码点 - 0xE031 = 数字`。这个偏移在多次页面加载间稳定（已验证连续三次加载）。
解码在 `adapters/boss.py::decode_obfuscated`。

**不要**改成读 `innerText` 或 `text`——拿到的就是这些私有区字符，不做解码结果是乱码。

### 卡片文本形态

```
AI Agent开发工程师10-15K    ← 岗位名 + 薪资（薪资部分需解码）
3-5年                       ← 经验
本科                        ← 学历
中信恒 青岛·李沧区·李村公园   ← 公司名 + 城市·区·商圈
```

公司名和地区在同一行，用空格分隔，解析时用正则
`^([\u4e00-\u9fa5A-Za-z0-9（）()]{2,26})\s+([\u4e00-\u9fa5]{2,8}·.+)$` 一次拿全。

### 必须登录

未登录时列表页被 `_security_check` 重定向，`card 数量为 0`。
登录成功判据：`xpath://a[@href="https://www.zhipin.com/web/geek/recommend"]//img` 存在。
只判断"页面没有『登录/注册』字样"会误判——安全校验页也没有这个字样。

### 详情页限流

详情页 `job_detail` 连续访问 3 次可能被重定向回首页。
`enrich` 里连续 3 次拿不到内容就停止，不要重试。

## 智联招聘

**这个平台改了实现方式，注意别改回去。**

### 链接和完整数据在 Vue 里，不在 DOM

卡片 DOM 只有岗位名、薪资、技能标签，**没有 jobId、没有详情 URL、没有发布时间**。
完整数据挂在列表组件的 props 上：

```js
document.querySelector(".job-card").parentElement.__vue__.$props.list
```

每项含 100+ 字段，常用的：

| 字段 | 含义 | 示例 |
|---|---|---|
| `name` | 岗位名 | AI智能体开发工程师 |
| `jobId` | 岗位 ID | 40873965603 |
| `positionURL` | **投递链接** | https://www.zhaopin.com/jobdetail/CC... |
| `publishTime` | **上架时间** | 2026-10-31 01:21:17 |
| `jobDescription` | JD 摘要 | 岗位职责1.平台设计与开发... |
| `salary60` | 薪资（人读） | 1.2-2.2万 |
| `salaryReal` | 薪资（数字） | 12001-22000 |
| `companyName` / `companySize` / `industryName` / `financingStage` | 公司信息 | |
| `workingExp` / `education` | 经验学历 | |
| `jobSkillTags` | 技能标签（对象数组，取 `.name`） | |
| `welfareLabel` | 福利 | 列表页通常是空数组 |

实现在 `adapters/zhilian.py::_READ_LIST_JS`。卡片文本解析保留为兜底，但**那条路拿不到链接**。

### 详情页有人机验证

`jobdetail` 页挂了腾讯云 EdgeOne，直接访问返回"正在验证连接安全性，请勾选下方复选框"。
所以**不要逐条打开详情页**。好在列表数据已含 JD 摘要，`enrich` 只做列表回填。

### 城市参数

URL 形式 `https://www.zhaopin.com/sou/jl703/p1?kw=<关键词>`，`jl703` 是青岛。
注意页面会 302 到 `https://www.zhaopin.com/jobs/?jl=703&kw=...`，但列表照常渲染。

## 前程无忧（51job）

**字段最全，且结构化程度最高。**

### 卡片内嵌 JSON

每个 `.joblist-item` 有 `sensorsdata` 属性，是个 JSON 字符串：

```json
{
  "jobId": "173231504",
  "jobTitle": "Java开发（AI智能体）",
  "jobSalary": "1-1.2万",
  "jobArea": "青岛·黄岛区",
  "jobYear": "5年及以上",
  "jobDegree": "大专",
  "jobTime": "2026-08-11 17:53:02"
}
```

`jobTime` 就是**上架时间，精确到秒**。这是四个平台里唯一直接给的。

### 福利在 `.tag` 里

技能标签和福利标签混在一起，靠关键词区分（五险/一金/年假/双休/补贴/奖金/期权/培训等）。

### 详情页

`https://jobs.51job.com/all/{jobId}.html`，含完整 JD、工作地址、公司简介。
地址优先取 `地图完整地址：` 后的文本，比 `工作地址` 段干净（后者混入地图组件噪声）。

### 注意浏览器兼容性检测

51job 会检测 `load_mode`。如果设成 `eager` 会跳"你的浏览器可能存在兼容性问题"页，
导致卡片数为 0。**不要启用 eager 模式**。

## 猎聘

### 卡片文本形态

```
岗位名
【青岛-市南区】
10-18k·15薪
3年以上本科        ← 经验和学历挤在一行，需拆
公司名
IT服务100-499人    ← 行业 + 规模粘连
```

经验/学历用 `_split_exp_edu` 拆：分别正则匹配 `经验不限|应届|\d+-?\d*年` 和 `本科|硕士|博士|大专|学历不限`。

### 公司名常被隐藏

显示为"某知名公司""某青岛大型IT服务公司"是猎聘的保护机制，不是解析失败。
拿不到时兜底成「（猎聘未披露）」。

### 风控最严

容易触发 `safe.liepin.com/v/intercept/verifysms` 短信验证，需要账号主人手动过。

**自动兜底**：检测到拦截时改用 `liepin-cli`（实现在 `adapters/liepin_cli.py`）：

```python
# 探测：Windows 上必须找带扩展名的可执行文件
liepin_cli._resolve_cmd()   # 找 .exe / .cmd / .bat
liepin_cli.available()      # 能否执行
liepin_cli.auth_ok()        # token 是否有效
```

CLI 走独立接口通道，不受浏览器风控影响，但**只有 `search` 和 `apply`，没有详情接口**。
所以兜底能保住列表字段，JD / 地址 / HR 活跃仍为空。

### 详情页

`https://www.liepin.com/job/{id}.shtml`，含 JD、HR 活跃（"21小时前在线"）、公司介绍。
详情页是 SPA，需要 `scroll.to_bottom()` 触发懒加载后再读。

## 通用注意

### 浏览器单例

DrissionPage 在同一 user-data-dir 上重复实例化会互踢连接，报
`PageDisconnectedError: 与页面的连接已断开`。`core/browser.py` 保证进程内只有一个
ChromiumPage，连接失效时重建一次。

各适配器**不要**自己持有 `page` 引用跨多次导航——连接重建后旧引用失效。
每次操作前调 `get_page()` 取当前对象。

### 节流

`safe_get` 内置：每次导航间隔 2-4.5 秒随机，每 8 次导航强制冷却 8 秒。
平台间切换额外冷却 15 秒（`platform_cooldown`）。

### 风控检测

`browser.is_blocked(page)` 统一识别：
- `safe.liepin.com` / `verifysms` → 猎聘短信验证
- `_security_check` → BOSS 安全校验
- `captcha` / `verify` → 验证码

新增平台时把特征加到 `is_blocked` 里，这样 `collect` 能统一提示用户。
