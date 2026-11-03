# job-hunter

一个 ZCode skill：从 **BOSS直聘、智联招聘、前程无忧(51job)、猎聘** 四个平台批量采集
招聘岗位，自动打分排序、识别并排除外包，输出可直接跟踪投递进度的多 sheet Excel。

不需要任何 LLM 接口——打分、外包判定、字段解析全部是本地规则。

## 安装

作为 ZCode skill 使用，放到 skill 目录即可：

```bash
git clone https://github.com/AIR-DUSTER1/job-hunter.git ~/.agents/skills/job-hunter
```

### 环境要求

| 项目 | 版本 | 说明 |
|---|---|---|
| Python | **3.10+** | 函数签名里用了 `X \| None` 联合类型（运行时求值），3.9 及以下会报 TypeError |
| DrissionPage | **4.1.1.4**（实测） | 需 4.x。项目用到 `set_browser_path()` 与 `ele/eles(..., timeout=)` |
| openpyxl | **3.1.5**（实测） | 需 3.0+。项目用到 `DataValidation`、`freeze_panes`、`cell.hyperlink` |
| Chrome / Edge | 任意较新版本 | DrissionPage 会自动拉起，非默认安装位置可用 `CHROME_PATH` 指定 |
| liepin-cli | **0.1.0**（可选） | 装后猎聘字段更全、不触发短信验证 |

上表的固定版本是本项目开发与完整验证时使用的版本。**更高版本通常可用，但未验证**；
遇到 API 变动问题，先按此表固定版本复现。

### 安装依赖

```bash
pip install -r requirements.txt     # 已固定为本项目验证过的版本
```

或手动装：

```bash
pip install "DrissionPage==4.1.1.4" "openpyxl==3.1.5"

# 可选：猎聘 CLI
pip install liepin-cli
liepin-cli setup        # 首次授权（扫码登录）
```

### 检查环境

```bash
python -m scripts.main doctor   # 检查猎聘 CLI 是否就绪
```

CLI 不是必需的。没装时猎聘会退回浏览器采集，只是字段少一些、更容易触发短信验证。

## 用法

```bash
export JOBHUNT_HOME="$PWD/job-data"      # 登录态与产出存放处，必须是绝对路径
cd ~/.agents/skills/job-hunter

# 1. 登录（BOSS 必须登录，否则拿不到任何岗位）
python -m scripts.main login boss

# 2. 采集
python -m scripts.main collect --keywords "Agent开发,前端开发" --city 青岛 \
    --salary 10 20 --years 2 --pages 2

# 3. 隔几分钟后补详情（JD / 地址 / HR活跃）
python -m scripts.main enrich --limit 10

# 4. 遇到风控时手动过验证
python -m scripts.main verify liepin
```

产出的 Excel 在 `$JOBHUNT_HOME/out/`，按岗位方向拆成多个 sheet：
「全部岗位」「<每个方向>」「跨方向」「汇总」。

## 功能

- **四平台采集**：统一成同一套字段，城市编码各自维护
- **打分排序**：岗位匹配 35 / 薪资匹配 25 / 经验匹配 15 / 公司质量 15 / 活跃度 10
- **外包识别**：公司名、JD 关键词、行业字段三层判定，命中后分数打 0.35 折
- **投递管理**：Excel 里「投递状态」下拉（待投递/已投递/已面试/已结束），
  再次采集不会覆盖你填的状态
- **增量合并**：只跑单平台时，其他平台的历史记录原样保留
- **风控应对**：自动节流、风控检测；猎聘被拦时自动切换 `liepin-cli` 取数

## 各平台要点

| 平台 | 登录 | 说明 |
|---|---|---|
| BOSS直聘 | 必须 | 薪资做了字体混淆（Unicode 私有区字符），已解码 |
| 智联招聘 | 建议 | 链接和完整数据在 Vue 组件里，不在 DOM；一次抓全还能拿到上架时间 |
| 前程无忧 | 可选 | 卡片内嵌结构化 JSON，上架时间精确到秒 |
| 猎聘 | 建议 | 风控最严，已配 `liepin-cli` 自动兜底 |

细节（城市编码表、DOM 结构差异、已知限制）见
[references/platforms.md](references/platforms.md)。

## 支持的城市

青岛、济南、北京、上海、深圳、杭州、成都。新增城市的方法见
`references/platforms.md` 的「新增城市」一节。

## 命令行参数

```
collect
  --keywords   逗号分隔的目标岗位关键词，每个会展开成同义词组
  --city       目标城市
  --salary     期望月薪区间（K），两个数字
  --years      工作年限
  --pages      每个关键词翻几页（默认 2）
  --enrich     采集后补几条详情（默认 20；建议设 0，隔几分钟单独跑 enrich）
  --platforms  只跑指定平台，如 51job,zhilian
  --out        自定义输出路径

enrich
  --limit      每个平台补几条（默认 10）
  --platforms  只补指定平台

diagnose
  --platforms  检查哪些平台（默认四平台全检）
  --detail     每平台补几条详情一起查（默认 1，控制风控风险）
  --apply      固化本次在真实页面上验证通过的候选选择器
  --simulate   自测：指定平台，强制假装选择器失效
  --json       只输出 JSON 报告
```

## 站点改版怎么办

四个站点的 DOM 选择器集中在 `scripts/adapters/selectors.json`，适配器、`login`、
`verify` 都从它读。站点改版时采集会报「页面结构变更」，按三步处理：

```bash
python -m scripts.main diagnose --platforms boss          # 1. 定位（区分风控/改版/真没岗位）
python -m scripts.main diagnose --platforms boss --apply  # 2. 固化验证过的候选
python -m scripts.main collect --platforms boss           # 3. 复跑确认
```

`--apply` 只接受在真实页面上验证过的候选（能解析出 ≥60% 岗位名），写前留
`.bak`、变更记进 `learned`，可回滚。诊断报告与 HTML 快照落在
`$JOBHUNT_HOME/out/diagnose/`。

改版当天即使不跑诊断也不会中断采集：`collect` 会自动推断候选、本次临时生效并
重试一次，日志标【临时选择器】，结尾提示去固化。

细节见 `SKILL.md` 的「站点改版：自主适配」和 `references/platforms.md` 的
「选择器注册表」。

## 环境变量

| 变量 | 作用 | 默认 |
|---|---|---|
| `JOBHUNT_HOME` | 数据目录（登录态 + 产出） | 代码所在目录 |
| `JOBHUNT_PORT` | 浏览器调试端口 | 9334 |

同一时间只能有一个进程占用端口，跑之前先确认没有残留进程。

## 注意

- 采集频率已内置节流（2-4.5 秒随机间隔、每 8 次导航冷却 8 秒），请勿调低
- 登录态存在 `$JOBHUNT_HOME/.profile`，已在 `.gitignore` 排除
- 本项目仅用于个人求职，请遵守各招聘网站的服务条款
