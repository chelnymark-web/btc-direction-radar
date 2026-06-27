# 偏见雷达(bias radar)

BTC 衍生品共识拥挤度面板 + 日/周/月自动报告。
数据全部来自 **Binance 与 Deribit 的免费公开行情端点,不需要任何交易所 API key,不碰你的账户**。

巡检逻辑:资金费率(谁拥挤)→ OI 变化(杠杆在进还是被清)→ 大户/全体多空比(聪明钱与散户各站哪边)
→ 基差(狂热或恐慌折价)→ DVOL 与 25Δ skew(恐惧定价到多极端)。
所有指标的正确用法是**读拥挤度、校准认错线**,不是找入场信号。

---

## 一次性配置(约 10 分钟)

1. **建仓库**:在 GitHub 新建一个仓库(私有即可),把本项目所有文件推上去。
   不会用 git 的话:仓库页面 → `Add file → Upload files`,把解压后的文件按原目录结构拖进去即可
   (`.github` 是隐藏文件夹,网页上传时要确认它也传上去了)。

2. **开 Pages**:仓库 `Settings → Pages → Build and deployment`,
   Source 选 `Deploy from a branch`,Branch 选 `main`、目录选 `/docs`,保存。
   几分钟后你会得到一个网址:`https://<你的用户名>.github.io/<仓库名>/`。

3. **开 Actions 权限**:`Settings → Actions → General → Workflow permissions`,
   勾选 `Read and write permissions`(报告要写回仓库)。

4. **可选的两个 Secret**(`Settings → Secrets and variables → Actions → New repository secret`):
   - `ANTHROPIC_API_KEY`:填了之后,每份报告会附一段 Claude 写的中文简评;不填报告照常生成,只是没有简评。
   - `PROXY_URL`:见下方"已知问题"第 1 条,Binance 拦 Actions 服务器 IP 时才需要。

5. **手动跑第一次**:`Actions` 标签页 → 选 `daily-report` → `Run workflow`。
   跑完后,`https://<用户名>.github.io/<仓库名>/` 是报告索引,
   `…/dashboard.html` 是实时面板,`…/reports/` 下是每天积累的报告。

之后就不用管了:每天 07:00(东京时间)出日报,周一出周报,每月 2 日出月报。

---

## 已知问题与对策(诚实交底)

1. **Binance 可能拦 GitHub 的服务器 IP**(美国地区限制,返回 451/403)。
   对策:加一个 `PROXY_URL` secret(格式 `http://user:pass@host:port`,任何非受限地区的 HTTP 代理),
   工作流会自动走代理;或者把工作流换成 self-hosted runner 在你自己电脑上跑。
   Deribit 与新闻 RSS 一般不受影响,所以即使 Binance 被拦,报告仍会带着期权与新闻部分照常生成。

2. **面板的"实时/快照"两种模式**:浏览器直接拉交易所接口若被 CORS 或地区限制拦下,
   面板会自动回退读取 Actions 最近一次提交的 `docs/data/latest.json`,右上角会标明当前是哪种模式。
   也就是说:只要 Actions 在跑,面板永远有数可看,最坏情况下数据是上一次定时任务的快照。

3. **拥挤度判定的演化**:前 30 次运行用代码里的静态启发阈值;
   `data/snapshots.jsonl` 累积满 30 条后,自动切换为相对自身历史的百分位判定(更扎实)。
   阈值在 `scripts/fetch_data.py` 的 `STATIC_RULES` 里,想改就改。

4. **没有覆盖的两块**:清算热图(Coinglass 的 API 收费)与链上数据(Glassnode/CryptoQuant 收费),
   面板底部放了外链。以后想补,可以在 fetch_data.py 里加数据源。

5. **报告时间**:cron 用的是 UTC。22:00 UTC = 次日 07:00 东京时间。想改时间就改三个 workflow 里的 cron。

---

## 文件结构

```
.github/workflows/   daily / weekly / monthly 三个定时任务
scripts/
  fetch_data.py      Binance+Deribit:费率/OI/多空比/基差/DVOL + 算 25Δ skew/期限结构 + 拥挤度标记
  fetch_macro.py     ★宏观维度(Yahoo 纳指/DXY/VIX/油价+黄金)→ 对 BTC 的方向分(2026 最强驱动)
  fetch_cme_gaps.py  ★CME 缺口(Yahoo BTC=F 真日线,逐级兜底 Stooq/Binance)→ 未回补缺口
  fetch_mstr.py      MSTR 杠杆飞轮:mNAV/距成本/股息覆盖(慢变量看 8-K 更新 data/mstr.json)
  fetch_levels.py    关键位+斐波那契+重合带(自动并入 CME 缺口)
  fetch_news.py      daily=24h 新闻 RSS;weekly/monthly=未来事件前瞻日历
  generate_report.py 渲染 HTML 报告 + 重建索引 + 把所有快照复制到 docs/data + 可选 Claude 简评
docs/                GitHub Pages 站点根目录
  command-deck.html  ★流动性指挥台:宏观35%+衍生品30%+位置15%+流动性12%+事件8% 合成方向概率
  direction-compass.html 方向罗盘:10 衍生品 + MSTR + 关键位/CME缺口重合带
  dashboard.html     早期实时面板(浏览器直拉,失败回退快照)
  data/              网页回退用的快照(Actions 每次自动刷新)
  reports/           生成的报告归档
data/                快照与历史(Actions 自动提交)
```

## 新增维度说明(本地 Claude Code 补全并已联网验证)

- **宏观维度(最高权重)**:2026 年 BTC = 高 beta 科技股,与纳指相关 +0.7~0.8,天花板由股市定。
  `fetch_macro.py` 拉 Yahoo `^NDX / DX-Y.NYB / ^VIX / CL=F`(+黄金 `GC=F` 作恐慌极端旁证),
  按"纳指涨=偏多、DXY/VIX/油升=偏空"合成 `macro_bias`,写入 `data/macro.json`,指挥台读快照。
- **CME 缺口**:`fetch_cme_gaps.py` 用 Yahoo `BTC=F` **真日线**(必须用 period1/period2 拉,
  `range=max` 会被降采样成周线导致缺口失真),检出周末跳空、判定是否回补,
  输出未回补缺口到 `data/cme_gaps.json`,自动并入两张网页的重合带并高亮最近几个。
- 浏览器直拉 Yahoo 常被 CORS 拦,所以宏观/CME 走 **Actions 端 Python 取数 → 网页读快照**,
  和现有 `latest.json` 回退机制一致;断网或被拦时网页仍有上一次快照可看。

> daily 模式抓新闻 RSS 需要 `feedparser`(已在 requirements.txt);weekly/monthly 的前瞻日历不需要它。
> 已知:ForexFactory 的 `ff_calendar_nextweek.json` 端点可能 404(它自己改了),代码已优雅跳过,
> 静态 `calendar.json` + 计算的周期事件照常产出。

本项目是个人研究工具,不构成投资建议。
