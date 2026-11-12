# job-hunter

四个招聘网站（BOSS直聘 / 智联招聘 / 前程无忧 / 猎聘）的岗位采集与投递管理工具，
做成 ZCode skill 的形态。

采集 → 打分排序 → 排除外包 → 输出可跟踪投递进度的多 sheet Excel。
站点改版时会自动诊断并修复选择器，不用每次手动改代码。

打分、外包判定、字段解析全部是本地规则，**不需要任何 LLM 接口**。

---

## 目录

- [它能做什么](#它能做什么)
- [安装](#安装)
- [快速开始](#快速开始)
- [产出说明](#产出说明)
- [招呼语生成](#招呼语生成)
- [完整命令参考](#完整命令参考)
- [站点改版怎么办](#站点改版怎么办)
- [各平台要点](#各平台要点)
- [支持的城市](#支持的城市)
- [环境变量](#环境变量)
- [数据与隐私](#数据与隐私)
- [项目结构](#项目结构)
- [常见问题](#常见问题)
- [注意事项](#注意事项)

---

## 它能做什么

| 能力 | 说明 |
|---|---|
| **四平台采集** | 同一套字段统一输出，各平台城市编码独立维护 |
| **打分排序** | 岗位匹配 35 / 薪资匹配 25 / 经验匹配 15 / 公司质量 15 / 活跃度 10 |
| **外包识别** | 公司名、JD 关键词、行业字段三层判定，命中后总分打 0.35 折 |
| **投递管理** | Excel 里「投递状态」下拉，再次采集不会覆盖你填的状态 |
| **增量合并** | 只跑单平台时，其他平台的历史记录原样保留 |
| **招呼语** | 按岗位 JD 撰写 ≤200 字的开场白，直接复制去跟 HR 沟通 |
| **风控应对** | 自动节流 + 风控检测；猎聘被拦时自动切换 `liepin-cli` 取数 |
| **改版自愈** | 选择器集中管理，站点改版时自动推断候选并重试，不中断采集 |

---

## 安装

作为 ZCode skill 使用：

```bash
git clone https://github.com/AIR-DUSTER1/job-hunter.git ~/.agents/skills/job-hunter
cd ~/.agents/skills/job-hunter
pip install -r requirements.txt
```

### 环境要求

| 项目 | 版本 | 说明 |
|---|---|---|
| Python | **3.10+** | 用了 `X \| None` 联合类型（运行时求值），3.9 及以下会 TypeError |
| DrissionPage | **4.1.1.4**（实测） | 需 4.x。用到 `set_browser_path()` 与 `ele/eles(..., timeout=)` |
| openpyxl | **3.1.5**（实测） | 需 3.0+。用到 `DataValidation`、`freeze_panes`、`cell.hyperlink` |
| Chrome / Edge | 任意较新版本 | DrissionPage 自动拉起；非默认位置用 `CHROME_PATH` 指定 |
| liepin-cli | **0.1.0**（可选） | 装后猎聘字段更全、不触发短信验证 |

表中版本是开发与完整验证时使用的。**更高版本通常可用但未验证**——遇到 API
变动问题，先按此表固定版本复现。

### 可选：猎聘 CLI

```bash
pip install liepin-cli
liepin-cli setup        # 首次授权（扫码登录）
```

不装也能用，只是猎聘退回浏览器采集，字段少一些、更容易触发短信验证。

检查是否就绪：

```bash
python -m scripts.main doctor
```

---

## 快速开始

```bash
# 数据目录（登录态、产出、画像都放这里），必须是绝对路径
export JOBHUNT_HOME="$PWD/job-data"

cd ~/.agents/skills/job-hunter

# 1. 登录（BOSS 必须登录，否则拿不到任何岗位）
python -m scripts.main login boss

# 2. 采集
python -m scripts.main collect \
    --keywords "Agent开发,前端开发" \
    --city 青岛 --salary 10 20 --years 2 --pages 2

# 3. 隔几分钟补详情（JD / 地址 / HR活跃）
python -m scripts.main enrich --limit 10
```

产出在 `$JOBHUNT_HOME/out/`。

> **为什么采集和补详情要分开跑**：站点风控看的是短时间内的请求总量。采集列表
> 每个关键词只要 1–2 次请求，补详情要逐条打开岗位页。混在一起跑，详情阶段
> 大概率被限流。先拿清单，隔几分钟再补，成功率高得多。

### 如果只想要指定平台

```bash
python -m scripts.main collect --keywords "前端开发" --platforms zhilian,51job
```

平台 key：`boss` / `zhilian` / `51job` / `liepin`。

这四个 key 在 `collect` / `enrich` / `diagnose` / `login` / `verify` 里完全一致，
并且接受常用别名（大小写不敏感）：

| 你可以写 | 归一成 |
|---|---|
| `job51`、`前程无忧` | `51job` |
| `zhaopin`、`智联`、`智联招聘` | `zhilian` |
| `BOSS`、`boss直聘`、`zhipin` | `boss` |
| `leipin`、`猎聘` | `liepin` |

---

## 产出说明

### Excel

`$JOBHUNT_HOME/out/岗位清单_<时间戳>.xlsx`，26 列，按岗位方向拆 sheet：

| Sheet | 内容 |
|---|---|
| 全部岗位 | 跨方向对照，多一列「岗位方向」 |
| `<方向名>` | 每个 `--keywords` 生成一页 |
| 汇总 | 总数、外包数、平均分，按方向和平台的数量分布 |

几点容易问到的：

- **投递链接是超链接**，单元格显示「点击投递」，点开直达岗位页
- **招呼语**是按 JD 写的开场白（≤200 字），见[下一节](#招呼语生成)
- **不含 HR 姓名列**（信息价值低），但保留「HR最近活跃」——它才是判断该不该投的依据
- **不含 JD 全文**：长篇文本会把列撑到几百字宽。原始 JD 存在 JSON 快照里
- 「投递状态」下拉：待投递 / 已投递 / 已面试 / 已结束
- 外包行标淡红，高分非外包（≥75）标淡绿

### JSON 快照

- `最新岗位清单.json` —— 下次运行的增量合并基准
- `岗位清单_<时间戳>.json` —— 同批次的历史备份

---

## 招呼语生成

按岗位 JD 写一段 ≤200 字的开场白，写进 Excel「招呼语」列。

### 为什么这一环需要 agent

打分和外包判定是分类问题，规则足够；招呼语要**读懂 JD 后挑出最贴的项目经历**，
是写作问题，模板拼不出来。所以脚本只准备材料和落库校验，撰写交给调用 skill 的 agent。

### 三步

```bash
# 1. 首次使用：建画像
python -m scripts.main greet --init-profile

# 2. 导出待写清单（含 JD + 画像）
python -m scripts.main greet --export

# 3. agent 读 out/待写招呼语.json，逐条写好，存成
#    [{"key": "...", "greeting": "..."}]
#    再校验写回
python -m scripts.main greet --import greet.json
```

其他参数：

```bash
python -m scripts.main greet --status        # 看还有多少没写
python -m scripts.main greet --top 20        # 只导得分最高的 20 条
python -m scripts.main greet --overwrite     # 连同已写的一起重写
```

超 200 字会自动截断并计数；`key` 对不上的逐条报出来，不静默丢弃。

### 画像放哪

`$JOBHUNT_HOME/profile.md` —— **在你自己的数据目录，不在 skill 里**。升级 skill
不会覆盖，也不会跟着仓库传播。

画像建议包含：基本信息、技术栈、3–6 个真实项目（带量化成果）、按方向的简历
（有多个求职方向时）。格式参考 [references/greeting-guide.md](references/greeting-guide.md)。

没有画像时 `--export` 仍能跑，但招呼语只能写通用版——脚本会提示你先建画像。

### 写作守则

- **只引用画像里有的内容**，不编造技术、项目、公司名
- JD 明确要求的技能，画像里有的就对上去；没有的不要硬凑
- 优先用数字：60%、90%、30%、200+、6 周
- 岗位要求明显超出（如"8 年经验"而你 2 年）：仍可投，坦诚说明并突出实际能力
- 不写薪资期望，留到沟通时谈

### JD 缺失怎么办

招呼语质量直接取决于 JD。`enrich` 只能补一部分（BOSS 风控严时尤其少）：

1. 先跑 `python -m scripts.main enrich --limit 15` 尽量补全
2. 各平台风控不同，**分开跑成功率更高**（`--platforms boss` 单独跑一次）
3. 仍没有 JD 的按岗位名 + 公司 + 薪资写简版，**不要假装读过 JD**

---

## 完整命令参考

```bash
python -m scripts.main <子命令> [参数]
```

### collect —— 采集

| 参数 | 默认 | 说明 |
|---|---|---|
| `--keywords` | `Agent开发,前端开发` | 逗号分隔，每个会展开成同义词组，同时决定 Excel 的方向分页 |
| `--city` | 青岛 | 见[支持的城市](#支持的城市) |
| `--salary` | `10 20` | 期望月薪区间（K），两个数字 |
| `--years` | 2 | 工作年限 |
| `--pages` | 2 | 每个关键词翻几页 |
| `--enrich` | **20** | 采集后立刻补几条详情。**建议设 0**，隔几分钟单独跑 `enrich` |
| `--platforms` | 全部四个 | 只跑指定平台 |
| `--out` | 自动 | 自定义输出路径 |

### enrich —— 补详情

| 参数 | 默认 | 说明 |
|---|---|---|
| `--limit` | 10 | 每平台补几条 |
| `--platforms` | 全部四个 | 只补指定平台 |

补 JD 全文、工作地址、HR 活跃时间、公司规模、上下班时间。

**详情字段不是都能拿到，这是数据本身的限制，不是 bug**：

| 字段 | 情况 |
|---|---|
| 投递链接 | 四平台 100% |
| 上架时间 | 智联 100%（精确到秒）、51job 100%；BOSS 从公司页拿；猎聘需详情页 |
| JD | 智联 100%（列表页自带）；其余需补详情 |
| 上下班时间 | 多数 JD 只写"双休"不写具体时段，留空正常 |
| 岗位福利 | BOSS 公司页最全；51job 靠标签；智联、猎聘散在 JD 里 |

### greet —— 招呼语

| 参数 | 说明 |
|---|---|
| `--init-profile` | 在数据目录生成 `profile.md` 模板 |
| `--force` | 配合 `--init-profile`，覆盖已有画像 |
| `--export` | 导出待写清单（含 JD + 画像） |
| `--import FILE` | 导入写好的招呼语并回写 Excel |
| `--status` | 查看进度 |
| `--top N` | 只导得分最高的 N 条 |
| `--overwrite` | 连同已有招呼语的一起重写 |

### diagnose —— 站点结构诊断

| 参数 | 默认 | 说明 |
|---|---|---|
| `--platforms` | 全部四个 | 检查哪些平台 |
| `--city` | 青岛 | 诊断用的城市 |
| `--keywords` | 开发 | 诊断用的关键词 |
| `--detail` | 1 | 每平台补几条详情一起查（0 = 跳过详情层） |
| `--apply` | 关 | 固化本次在真实页面上验证通过的候选选择器 |
| `--simulate` | — | 自测：指定平台，强制假装选择器失效 |
| `--json` | 关 | 只输出 JSON 报告 |

### login / verify / doctor

```bash
python -m scripts.main login boss       # 打开登录页等扫码（boss/zhilian/job51/liepin）
python -m scripts.main verify liepin    # 打开验证页等手动过验证
python -m scripts.main doctor           # 检查猎聘 CLI 环境
```

---

## 站点改版怎么办

四个站点的 DOM 选择器集中在 `scripts/adapters/selectors.json`，适配器、`login`、
`verify` 都从它读——**改版时改配置，不改代码**。

站点改版时采集会报「页面结构变更」，按三步处理：

```bash
python -m scripts.main diagnose --platforms boss          # 1. 定位
python -m scripts.main diagnose --platforms boss --apply  # 2. 固化验证过的候选
python -m scripts.main collect --platforms boss           # 3. 复跑确认
```

诊断会明确区分四种情况，不用靠猜：

| 结论 | 含义 | 动作 |
|---|---|---|
| `OK` | 结构正常 | 0 条可能是关键词没结果或登录失效 |
| `降级` | 部分字段填充率偏低 | 看报告里的字段统计 |
| `结构变更` | 卡片容器或关键字段失效 | `--apply` 或人工改 |
| `被风控` | 命中验证页 | `verify <平台>`，不是改代码 |

`--apply` 只接受**本次在真实页面上验证过**的候选（能解析出 ≥60% 岗位名），
写前留 `.bak`、变更记进 `learned`，可回滚。诊断报告与 HTML 快照落在
`$JOBHUNT_HOME/out/diagnose/`。

**改版当天即使不跑诊断也不会中断采集**：`collect` 会自动推断候选、本次临时生效
并重试一次，日志标【临时选择器】，结尾提示去固化。这种临时选择器下次运行会失效，
所以看到提示就按上面三步固化掉。

细节见 [SKILL.md](SKILL.md) 的「站点改版：自主适配」和
[references/platforms.md](references/platforms.md) 的「选择器注册表」。

---

## 各平台要点

| 平台 | 登录 | 说明 |
|---|---|---|
| BOSS直聘 | **必须** | 未登录列表页被安全校验拦截。薪资做了字体混淆（Unicode 私有区字符），已解码 |
| 智联招聘 | 建议 | 链接和完整数据在 Vue 组件里，不在 DOM。未登录时薪资显示 `**-**元` |
| 前程无忧 | 可选 | 卡片内嵌结构化 JSON，上架时间精确到秒。未登录也有完整字段 |
| 猎聘 | 建议 | 风控最严，已配 `liepin-cli` 自动兜底 |

细节（城市编码表、DOM 结构差异、已知限制）见
[references/platforms.md](references/platforms.md)。

---

## 支持的城市

青岛、济南、北京、上海、深圳、杭州、成都。

新增城市：改对应适配器顶部的 `CITY_*` 字典，方法见
`references/platforms.md` 的「新增城市」一节。

```python
# adapters/boss.py
CITY_CODE = {"青岛": "101120200", "新城市": "编码"}
```

---

## 环境变量

| 变量 | 作用 | 默认 |
|---|---|---|
| `JOBHUNT_HOME` | 数据目录（登录态 + 产出 + 画像） | 代码所在目录 |
| `JOBHUNT_PORT` | 浏览器调试端口 | 9334 |
| `CHROME_PATH` | 浏览器可执行文件路径（可选） | 自动探测 |

同一时间只能有一个进程占用端口。跑之前先确认没有残留进程：

```bash
taskkill /F /IM chrome.exe          # Windows
```

---

## 数据与隐私

数据目录 `$JOBHUNT_HOME` 里存的东西**都不该进版本库**：

| 内容 | 敏感性 |
|---|---|
| `.profile/` | 浏览器登录态，含 cookie / token |
| `profile.md` | 求职者画像，含姓名、学校、项目经历 |
| `out/*.xlsx` / `*.json` | 采集到的岗位清单 |

`.gitignore` 已排除 `job-data/`、`.profile/`、`out/`、`*profile.md` 等。
如果你用了别的目录名，**自己确认它被忽略了**：

```bash
git check-ignore -v <你的数据目录>
```

本项目仅用于个人求职，请遵守各招聘网站的服务条款。

---

## 项目结构

```
.
├── SKILL.md                    # skill 主文档（agent 执行契约）
├── README.md
├── requirements.txt
├── references/
│   ├── platforms.md            # 各平台 DOM / 城市编码 / 已知限制
│   └── greeting-guide.md       # 画像格式说明
└── scripts/
    ├── main.py                 # 统一命令行入口
    ├── collect.py              # 采集 → 过滤 → 打分 → 导出
    ├── enrich.py               # 补详情
    ├── greet.py                # 招呼语导出 / 回写
    ├── diagnose.py             # 站点结构诊断
    ├── login.py / verify.py    # 登录、过风控
    ├── adapters/
    │   ├── selectors.json      # 四平台选择器单一真源
    │   ├── boss.py / zhilian.py / job51.py / liepin.py
    │   └── liepin_cli.py       # 猎聘 CLI 封装
    └── core/
        ├── browser.py          # 浏览器单例 + 节流 + 风控检测
        ├── dom.py              # 候选链访问 + 结构推断
        ├── health.py           # 字段填充率 + 健康结论
        ├── selectors.py        # 注册表读写
        ├── models.py           # Job 模型 + Excel 导出
        ├── scoring.py          # 打分 / 外包判定
        ├── merge.py            # 增量合并
        └── paths.py            # 路径解析
```

---

## 常见问题

| 现象 | 原因与处理 |
|---|---|
| `与页面的连接已断开` | 同一 profile 被多个进程抢占。杀掉残留 chrome，删 `.profile/Singleton*` 后重跑 |
| `浏览器连接失败，请检查9334端口` | 端口被占用或上次进程残留。换端口：`export JOBHUNT_PORT=9335` |
| 所有平台都返回 0 条 | 大概率没登录或全被风控。先 `login boss`，再单平台 `--platforms zhilian` 定位 |
| 某平台突然 0 条 | 跑 `diagnose --platforms <平台>` 区分是风控还是改版 |
| 采集到但公司名为空 | 猎聘常隐藏雇主名（显示「某知名公司」），已兜底为「（猎聘未披露）」 |
| Excel 里改的状态没了 | 合并逻辑只保留 `最新岗位清单.json` 里的状态，改完要保存再采集 |
| 打招呼语列是空的 | 还没跑 `greet --export` → 撰写 → `--import` |
| 单平台采集后还带着其他平台记录 | 这是有意的（避免丢失其他平台的投递进度）。想要单平台干净结果，先删 `最新岗位清单.json` |

---

## 注意事项

- 采集频率已内置节流（2–4.5 秒随机间隔、每 8 次导航冷却 8 秒），**请勿调低**
- BOSS 对持续访问最敏感，`collect` 会把它排到最后跑，避免连累前面平台
- Excel 里的「投递状态」和「招呼语」都会被增量合并保留，可以放心重复采集
