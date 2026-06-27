# 任务:给 btc-bias-radar 新增「CME 缺口」模块

你是在我现有的 `btc-bias-radar` 项目里工作(BTC 衍生品拥挤度面板 + 日/周/月自动报告,GitHub Actions 驱动,GitHub Pages 托管)。
现在新增一个功能:抓 CME 比特币期货的真实历史数据,算出**所有时段的未回补缺口(CME gap)**,接进日报和实时面板。

请先读 `scripts/fetch_data.py`、`scripts/generate_report.py`、`docs/dashboard.html`、`.github/workflows/daily.yml`,理解现有风格(中文注释、单项失败不拖垮整体的 `safe()` 模式、CSS 变量配色)后再动手,保持一致。

## 背景:CME 缺口是什么

CME 比特币期货(连续合约)周五收盘、周日重开,周末加密现货市场照常波动,于是周一开盘价常和周五收盘价之间留一段没有成交的价格空档,这就是「CME 缺口」。很多交易者相信价格倾向于回补这些缺口,所以未回补缺口是常用的目标位/支撑阻力参照。

## 数据源(真 CME 数据)

首选 Yahoo Finance 的 `BTC=F`(CME 比特币期货连续合约),日线 OHLC,有 2017 年底上市至今的完整历史:

```
https://query1.finance.yahoo.com/v8/finance/chart/BTC=F?period1=1500000000&period2=<now_unix>&interval=1d
```

- 需要带浏览器 User-Agent 头,否则 403。
- 解析:`result[0]["timestamp"]`(秒级 UTC 数组)与 `result[0]["indicators"]["quote"][0]` 里的 `open/high/low/close`。注意可能有 null,跳过。
- query1 失败就退到 `query2.finance.yahoo.com`;两个都失败,退到备用源 Stooq:`https://stooq.com/q/d/l/?s=btc.f&i=d`(CSV)。
- 兜底(只在 CME 真源全失败时用,并在输出里明确标注「近似」):用 Binance 现货 `api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d` 取周五/周一日线近似。

## 缺口计算算法

1. 把日线按日期排序。识别每个「周末跳空」:找到星期五(weekday=4)那根的 `close`(记 C),与紧接着下一根交易日(通常周一,CME 周末停盘)那根的 `open`(记 O)。
2. 若 `C != O`,记一个缺口:`low=min(C,O)`, `high=max(C,O)`, `date=周五日期`, `direction = "上方" if O>C else "下方"`(相对当时)。
3. **回补判定**:遍历该缺口形成之后的所有日线,若任意一根的 `[low, high]`(用该日的 `low`~`high` 区间)与缺口区间 `[gap_low, gap_high]` 有交集,则该缺口已回补(filled=True),并记录回补日期。严格判定用「价格是否触及缺口区间内任一点」;部分缺口会被部分回补,先按「触及即视为回补」实现,留一个 `partial` 字段记录是否完全穿越。
4. 产出全部缺口后,筛选:`filled == False` 且缺口区间与 [10000, 130000] 有交集的,作为「当前未回补缺口」。
5. 给每个未回补缺口附:距当前价格的百分比(`(gap_mid - spot)/spot`)、在现价上方还是下方。
6. 边界情况:数据里若某周五缺失(假期),用该周最后一个有数据的交易日代替;跨年、夏令时不用特殊处理(都用 UTC 日期)。

## 接入现有项目

- 新建 `scripts/fetch_cme_gaps.py`:抓数据 + 算缺口,输出 `data/cme_gaps.json`,结构例如:
  ```json
  {"spot_ref": 68000, "source": "yahoo BTC=F", "generated": "...",
   "open_gaps": [{"low": 80100, "high": 81250, "date": "2026-01-09", "side": "上方", "dist_pct": 18.5, "partial": false}, ...]}
  ```
  按距现价由近到远排序。
- 在 `daily.yml`(以及 weekly/monthly 三个 workflow)的 pipeline 里,`fetch_data.py` 之后加一行 `python scripts/fetch_cme_gaps.py`。
- 改 `generate_report.py`:在报告里加一个「未回补 CME 缺口」表格区块(列:缺口区间、形成日期、上/下方、距现价%)。只在 `data/cme_gaps.json` 存在时渲染。
- 改 `docs/dashboard.html`:面板底部加一块,浏览器直接拉 Yahoo `BTC=F`(若被 CORS 拦,回退读 `./data/cme_gaps.json`,和现有 latest.json 的回退逻辑一致),把未回补缺口画成横线叠在一个简单的价格刻度上,最近的几个高亮。

## 验证(重要)

写完后**真的联网跑一遍** `python scripts/fetch_cme_gaps.py`,确认:
- 能成功从 Yahoo 取到 2017 至今的数据(打印拿到多少根日线);
- 打印出未回补缺口总数,以及离现价最近的 5 个缺口区间;
- 人工核对最近一个缺口是否合理(对照 TradingView `CME:BTC1!` 周末跳空)。
若 Yahoo 403,依次试 query2、Stooq,并在日志里说明最终用了哪个源。

## 风格约束

中文注释;单个数据源失败要 `try/except` 优雅降级、不能让整个 pipeline 崩;不要引入重依赖(用 `requests` + 标准库即可,别上 pandas);所有产出文件编码 UTF-8。
完成后更新 README,在「文件结构」和「已知问题」里补上 CME 缺口模块的说明,并注明这是个人研究工具、不构成投资建议。
