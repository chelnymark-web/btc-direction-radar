# 从这里开始

这是一个**已经有可用雏形**的项目,不是空白起点。你的电脑上启动 Claude Code,把这整个文件夹作为工作目录,然后对它说:

> 「读 BUILD-PLAN.md 和现有代码,从阶段一开始,每个阶段联网验证后再进下一阶段。先告诉我你打算怎么改进现有的 direction-compass.html。」

## 这个文件夹里已经有什么(都是真实可跑的代码,不是占位)

```
BUILD-PLAN.md              ← 完整五阶段方案,Claude Code 的主任务书
docs-cme-gap-task.md       ← CME 缺口模块的细化规格(阶段三用)
README.md                  ← 配置与部署说明(GitHub Pages + Actions 五步)
requirements.txt           ← Python 依赖(requests, feedparser)

scripts/
  fetch_data.py            ← 抓 Binance+Deribit 全套指标、算 25Δ skew、拥挤度标记
  fetch_news.py            ← 日报抓新闻 / 周月报抓前瞻日历(双模式)
  generate_report.py       ← 渲染日/周/月报告 HTML、重建索引、可选 Claude 简评

docs/                      ← GitHub Pages 站点根目录
  command-deck.html        ← ★ 流动性指挥台:综合方向概率(衍生品+USDT+价格位置+事件+宏观)
  direction-compass.html   ← 方向罗盘:10衍生品+MSTR+关键位重合带
  dashboard.html           ← 早期面板版本(可与罗盘合并或取舍)
  index.html               ← 报告索引(Actions 跑后自动重建)

data/
  calendar.json            ← 已核实的 2026 FOMC/CPI 日程(前瞻日历用)

.github/workflows/         ← daily / weekly / monthly 三个定时任务
```

## 现状与待办(为什么还需要 Claude Code)

这些代码在**能联网的环境**里就能跑,但有两件事我(在受限环境里)没法替你完成,正是交给本地 Claude Code 的原因:

1. **真跑验证**:我所在的环境出口被白名单挡住(`host_not_allowed`),拉不到 Binance/Deribit/Yahoo 实时数据,所以以上脚本只做过离线冒烟测试,没在真数据上跑通。你本地没有这道墙,Claude Code 能真连、真验证、真调试。
2. **补齐缺失模块**:CME 缺口(需 Yahoo BTC=F 真数据,见 docs-cme-gap-task.md / BUILD-PLAN 阶段三)、清算热图与链上数据(需 Coinglass/Glassnode key 或外链)——这些都要在本地联网才能接入或验证。

## 建议顺序(BUILD-PLAN 的浓缩版)

1. **阶段一**:跑通并改进 `docs/direction-compass.html`(它已能用),抽出可配置阈值、加历史百分位判定。先让网页在你浏览器里真显示实时数据。
2. **阶段二**:跑通 `scripts/` 三件套 + 配 GitHub Actions/Pages,让日/周/月报告自动生成归档。
3. **阶段三**:按 docs-cme-gap-task.md 加 CME 缺口模块(真 Yahoo 数据)。
4. **阶段四/五**:前瞻日历完善、可选的消息冲击模拟器。

每步都让 Claude Code 联网实测、打印关键产出给你核对,再进下一步。

个人研究工具,不构成投资建议。
