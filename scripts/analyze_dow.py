#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
星期几 / 日历规律验证(文档 B)。
背景:用户观察"BTC 近期疑似周一高点、周三低点"。但这必须用真实数据验证——大概率含确认偏误。
本脚本用真实日线 OHLC:
  1) 对每个自然周(ISO 周),标出当周最高价、最低价分别落在星期几;
  2) 统计每个星期几"成为当周高点/低点"的占比。随机基准 ≈ 1/7 ≈ 14.3%,显著高于才算信号;
  3) 分市场阶段(下跌段/震荡段/上涨段)分别统计,看规律是否只在特定阶段成立;
  4) 计算各星期几平均日回报 / 隔夜回报(收→次开),验证"周一弱周五强"隔夜效应;
输出 data/dow_stats.json + 报告表。
诚实要求:若占比在 14% 上下浮动,如实说"无显著规律,大概率确认偏误",不美化。规律很弱且会被套利侵蚀。
"""
import json
import os
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

for var in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
    if os.environ.get(var) == "":
        del os.environ[var]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
WD_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
BASELINE = 100.0 / 7                      # 14.3%
SIGNIF = 22.0                             # 高于此才当"疑似信号"(≈1.5×基准)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"}


def _get(url, timeout=20):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout))


def klines(limit=400):
    """真 OHLC 日线,逐级兜底。返回 [(date, o, h, l, c), ...] 升序(≈13 个月)。"""
    for b in ("https://data-api.binance.vision", "https://api.binance.com", "https://api1.binance.com"):
        for _ in range(2):
            try:
                d = _get(f"{b}/api/v3/klines?symbol=BTCUSDT&interval=1d&limit={limit}")
                bars = [(datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).date(),
                         float(k[1]), float(k[2]), float(k[3]), float(k[4])) for k in d]
                if len(bars) > 120:
                    return bars, f"Binance({b.split('//')[1]})"
            except Exception:  # noqa: BLE001
                pass
    try:
        d = _get(f"https://api.gateio.ws/api/v4/spot/candlesticks?currency_pair=BTC_USDT&interval=1d&limit={limit}")
        bars = [(datetime.fromtimestamp(int(k[0]), tz=timezone.utc).date(),
                 float(k[5]), float(k[3]), float(k[4]), float(k[2])) for k in d]
        if len(bars) > 120:
            return bars, "Gate.io"
    except Exception as e:  # noqa: BLE001
        print(f"[warn] Gate.io 兜底失败: {e}", file=sys.stderr)
    return None, None


def phase_of(bars, i, lookback=20, thr=8.0):
    """用 20 交易日涨跌幅给该 bar 打市场阶段标签。"""
    if i < lookback:
        return "震荡段"
    chg = (bars[i][4] - bars[i - lookback][4]) / bars[i - lookback][4] * 100
    return "下跌段" if chg <= -thr else ("上涨段" if chg >= thr else "震荡段")


def week_high_low_days(week_bars):
    """返回 (最高价所在weekday, 最低价所在weekday)。"""
    hi = max(week_bars, key=lambda b: b[2])
    lo = min(week_bars, key=lambda b: b[3])
    return hi[0].weekday(), lo[0].weekday()


def share_table(weeks):
    """weeks: [(high_wd, low_wd)] -> 每个星期几高点/低点占比。"""
    hi = defaultdict(int)
    lo = defaultdict(int)
    for h, l in weeks:
        hi[h] += 1
        lo[l] += 1
    n = len(weeks)
    return ({WD_CN[d]: round(hi[d] / n * 100, 1) for d in range(7)},
            {WD_CN[d]: round(lo[d] / n * 100, 1) for d in range(7)}, n)


def main():
    os.makedirs(DATA, exist_ok=True)
    bars, src = klines()
    if not bars:
        print("[warn] 无法获取日线,未生成 dow_stats.json", file=sys.stderr)
        sys.exit(1)

    # 按 ISO 周分组
    weeks_map = defaultdict(list)
    phase_map = {}
    for i, b in enumerate(bars):
        iso = b[0].isocalendar()
        key = (iso[0], iso[1])
        weeks_map[key].append(b)
        phase_map[key] = phase_of(bars, i)          # 用该周最后一根的阶段

    all_weeks, by_phase = [], defaultdict(list)
    for key, wb in weeks_map.items():
        if len(wb) < 4:                              # 不完整的周(首尾)跳过,免失真
            continue
        hl = week_high_low_days(wb)
        all_weeks.append(hl)
        by_phase[phase_map[key]].append(hl)

    hi_share, lo_share, n = share_table(all_weeks)

    # 各星期几平均日回报 / 隔夜回报(收→次开)/ 日内(开→收)
    ret = {d: {"daily": [], "overnight": [], "intraday": []} for d in range(7)}
    for i in range(1, len(bars)):
        wd = bars[i][0].weekday()
        pc = bars[i - 1][4]
        ret[wd]["daily"].append((bars[i][4] - pc) / pc * 100)
        ret[wd]["overnight"].append((bars[i][1] - pc) / pc * 100)
        ret[wd]["intraday"].append((bars[i][4] - bars[i][1]) / bars[i][1] * 100)
    returns = {WD_CN[d]: {k: round(sum(v) / len(v), 3) if v else None
                          for k, v in ret[d].items()} for d in range(7)}

    # 用户观察检验:周一高点、周三低点
    mon_high = hi_share["周一"]
    wed_low = lo_share["周三"]
    top_hi = max(hi_share.items(), key=lambda x: x[1])
    top_lo = max(lo_share.items(), key=lambda x: x[1])
    signif = top_hi[1] >= SIGNIF or top_lo[1] >= SIGNIF

    if not signif:
        verdict = (f"无显著规律:高点最集中的是 {top_hi[0]}({top_hi[1]}%)、低点最集中的是 {top_lo[0]}({top_lo[1]}%),"
                   f"都在基准 14.3% 附近浮动——「周一高、周三低」大概率是确认偏误。")
    else:
        verdict = (f"疑似弱信号:高点偏向 {top_hi[0]}({top_hi[1]}%)、低点偏向 {top_lo[0]}({top_lo[1]}%)"
                   f"(基准 14.3%)。但样本仅 {n} 周、规律很弱且会被套利侵蚀,只能作边际概率修正,不能单独作交易依据。")

    obs = (f"用户观察「周一高({mon_high}%)+周三低({wed_low}%)」:"
           + ("与数据不符,基本是印象。" if (mon_high < SIGNIF and wed_low < SIGNIF)
              else "数据有一定呼应,但仍弱,勿重仓依赖。"))

    phase_out = {}
    for ph, wk in by_phase.items():
        if len(wk) >= 5:
            h, l, wn = share_table(wk)
            phase_out[ph] = {"weeks": wn, "high_day_share": h, "low_day_share": l}

    # 跨阶段稳健性:全样本最强"高点日"在各阶段是否一致——若在某阶段跌破基准甚至反转,即趋势假象
    robust = None
    tday = top_hi[0]
    cross = {ph: phase_out[ph]["high_day_share"].get(tday) for ph in phase_out}
    if cross:
        as_high = [ph for ph, s in cross.items() if s is not None and s >= SIGNIF]
        as_weak = [ph for ph, s in cross.items() if s is not None and s < BASELINE]
        # 该日在上涨段是否反而成了低点
        flips_low = ("上涨段" in phase_out and phase_out["上涨段"]["low_day_share"].get(tday, 0) >= SIGNIF)
        if as_weak or flips_low:
            robust = (f"{tday}高点不稳健:在 {'/'.join(as_high) or '部分阶段'} 明显,但在 "
                      f"{'/'.join(as_weak) or '其它阶段'} 跌回基准"
                      + ("、且上涨段里它反而是低点" if flips_low else "")
                      + " → 大概率是趋势假象(下跌段周开高走低),非稳健日历规律,换阶段会消失/反转。")
        else:
            robust = f"{tday}高点在各阶段较一致(仍需更多样本与套利侵蚀检验)。"

    out = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": src, "weeks": n, "from": bars[0][0].isoformat(), "to": bars[-1][0].isoformat(),
        "baseline_pct": round(BASELINE, 1),
        "high_day_share": hi_share, "low_day_share": lo_share,
        "by_phase": phase_out,
        "returns": returns,
        "observation_check": obs,
        "verdict": verdict,
        "robustness": robust,
        "caveat": "星期几规律很弱且会被套利侵蚀,即使显著也只能作边际概率修正,不能作单独交易依据。",
    }
    with open(os.path.join(DATA, "dow_stats.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"[ok] 星期几规律: 源={src} {out['from']}~{out['to']} 共 {n} 周 (基准 14.3%)")
    print("    当周高点占比: " + " ".join(f"{k}{v}%" for k, v in hi_share.items()))
    print("    当周低点占比: " + " ".join(f"{k}{v}%" for k, v in lo_share.items()))
    print(f"    {verdict}")
    print(f"    {obs}")
    if robust:
        print(f"    稳健性: {robust}")


if __name__ == "__main__":
    main()
