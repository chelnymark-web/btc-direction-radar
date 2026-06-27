#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CME 缺口模块(阶段三:用户明确要的,需真 CME 数据)。
CME 比特币期货周末休市(周五收盘 ~ 周日晚开盘),重开时跳空留下"缺口";
历史经验:多数缺口最终被回补,未回补缺口是价格的磁吸目标。

数据源(逐级兜底,都带浏览器 UA):
  1) Yahoo Finance BTC=F 日线(query1 失败退 query2)——CME 连续期货,首选
  2) Stooq btc.f 日线 CSV ——退而求其次
  3) Binance 现货日线 ——最后兜底,标注"近似(非 CME)"
算法:遍历相邻交易日,跨越周末/假日(日期差≥2天)处比较前一日 close 与次日 open;
  不等则记缺口 [min,max];用其后所有日线 low~high 判定是否被触及(触及=已回补)。
输出 data/cme_gaps.json:落在 [10000,130000] 区间、未回补的全部缺口,附距现价% 与上/下方。
验证:对照 TradingView CME:BTC1! + CME Gap 指标人工核一下离现价最近几个。
"""
import csv
import io
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

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
BAND = (10000, 130000)   # 只关心这个价格区间内的缺口

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"})


# ---------- 数据源:逐级兜底,统一返回 bars = [(date, o, h, l, c), ...] 升序 ----------

def from_yahoo(symbol="BTC=F"):
    # 注意:range=max 会被 Yahoo 悄悄降采样成 ~周线(只剩几百根),缺口判定全失真;
    # 必须用 period1/period2 显式拉真·日线(实测 2017 至今 ~2100+ 根)。
    p1 = int(datetime(2017, 12, 1, tzinfo=timezone.utc).timestamp())
    p2 = int(time.time())
    for host in ("https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"):
        for attempt in range(3):
            try:
                r = SESSION.get(f"{host}/v8/finance/chart/{symbol}",
                                params={"interval": "1d", "period1": p1, "period2": p2}, timeout=25)
                r.raise_for_status()
                res = r.json()["chart"]["result"][0]
                ts = res["timestamp"]
                q = res["indicators"]["quote"][0]
                bars = []
                for i, t in enumerate(ts):
                    o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
                    if None in (o, h, l, c):
                        continue
                    d = datetime.fromtimestamp(t, tz=timezone.utc).date()
                    bars.append((d, float(o), float(h), float(l), float(c)))
                if len(bars) > 500:
                    return bars, "Yahoo BTC=F(CME 连续期货)"
            except Exception as e:  # noqa: BLE001
                print(f"[warn] Yahoo {host} 第{attempt+1}次失败: {e}", file=sys.stderr)
                time.sleep(1.5 * (attempt + 1))
    return None, None


def from_stooq():
    try:
        r = SESSION.get("https://stooq.com/q/d/l/", params={"s": "btc.f", "i": "d"}, timeout=25)
        r.raise_for_status()
        rows = list(csv.DictReader(io.StringIO(r.text)))
        bars = []
        for row in rows:
            try:
                d = datetime.strptime(row["Date"], "%Y-%m-%d").date()
                bars.append((d, float(row["Open"]), float(row["High"]),
                             float(row["Low"]), float(row["Close"])))
            except (KeyError, ValueError):
                continue
        if len(bars) > 100:
            return bars, "Stooq btc.f(CME 期货)"
    except Exception as e:  # noqa: BLE001
        print(f"[warn] Stooq 失败: {e}", file=sys.stderr)
    return None, None


def from_binance():
    """最后兜底:Binance 现货日线。现货 7×24 无周末缺口,这里用相邻日 close→open 近似,标注非 CME。"""
    base = os.environ.get("BINANCE_SPOT", "https://api.binance.com")
    try:
        r = SESSION.get(f"{base}/api/v3/klines",
                        params={"symbol": "BTCUSDT", "interval": "1d", "limit": 1000}, timeout=25)
        r.raise_for_status()
        bars = []
        for k in r.json():
            d = datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).date()
            bars.append((d, float(k[1]), float(k[2]), float(k[3]), float(k[4])))
        if bars:
            return bars, "Binance 现货(近似·非 CME,仅兜底)"
    except Exception as e:  # noqa: BLE001
        print(f"[warn] Binance 兜底失败: {e}", file=sys.stderr)
    return None, None


def load_bars():
    for src in (from_yahoo, from_stooq, from_binance):
        bars, label = src()
        if bars:
            bars.sort(key=lambda b: b[0])
            return bars, label
    return None, None


# ---------- 缺口检测 ----------

def find_gaps(bars):
    """跨越周末/假日处比较 前日close vs 次日open,记缺口;扫描其后所有日线判定是否被触及。"""
    gaps = []
    for i in range(len(bars) - 1):
        d0, _, _, _, c0 = bars[i]
        d1, o1, _, _, _ = bars[i + 1]
        if (d1 - d0).days < 2:          # 仅跨周末/假日(连续交易日不算)
            continue
        lo, hi = min(c0, o1), max(c0, o1)
        if hi - lo < 1:                 # 几乎无跳空,忽略
            continue
        origin = c0                     # 缺口的"源"= 周五 close;价格重回此处才算完全回补
        up = o1 > c0                    # True=向上跳空(缺口在下方),False=向下跳空
        touched = False                 # 价格其后是否曾探入缺口内部(部分回补)
        full = False                    # 是否回到 origin(完全回补=缺口闭合)
        for j in range(i + 1, len(bars)):
            _, _, hj, lj, _ = bars[j]
            if lj < hi and hj > lo:     # 区间与缺口内部相交=至少部分触及
                touched = True
            # 完全回补:向上跳空需跌回周五 close,向下跳空需涨回周五 close
            if (up and lj <= origin) or ((not up) and hj >= origin):
                full = True
                break
        gaps.append({
            "date": d0.isoformat(), "open_date": d1.isoformat(),
            "low": round(lo, 1), "high": round(hi, 1), "mid": round((lo + hi) / 2, 1),
            "direction": "向上跳空" if up else "向下跳空",
            "touched": touched, "filled": full,
            "partial": touched and not full,   # 探入过但没填满
        })
    return gaps


def main():
    os.makedirs(DATA, exist_ok=True)
    bars, label = load_bars()
    if not bars:
        print("[warn] 所有数据源都失败,未生成 cme_gaps.json", file=sys.stderr)
        sys.exit(1)

    spot = bars[-1][4]
    # 与全套快照一致:优先用 latest.json 的现价
    try:
        with open(os.path.join(DATA, "latest.json"), encoding="utf-8") as f:
            p = json.load(f).get("price")
            if p:
                spot = float(p)
    except Exception:  # noqa: BLE001
        pass

    gaps = find_gaps(bars)
    # 未回补 = 未完全回补(价格未重回周五 close),且落在关心的价格带内
    unfilled = [g for g in gaps
                if not g["filled"] and BAND[0] <= g["mid"] <= BAND[1]]
    for g in unfilled:
        g["dist_pct"] = round((g["mid"] - spot) / spot * 100, 1)
        g["side"] = "上方" if g["mid"] > spot else "下方"
    unfilled.sort(key=lambda g: abs(g["dist_pct"]))

    out = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": label,
        "spot": round(spot, 1),
        "history_from": bars[0][0].isoformat(),
        "history_bars": len(bars),
        "total_gaps": len(gaps),
        "unfilled_count": len(unfilled),
        "gaps": unfilled,                 # 已按距现价排序
    }
    with open(os.path.join(DATA, "cme_gaps.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"[ok] CME 缺口: 源={label} 历史自 {out['history_from']}({len(bars)} 根日线), "
          f"共检出 {len(gaps)} 个缺口, 未回补 {len(unfilled)} 个; 离现价最近 5 个:")
    for g in unfilled[:5]:
        print(f"    {g['low']:>9}–{g['high']:<9} ({g['side']} {g['dist_pct']:+.1f}%) "
              f"{g['direction']} @ {g['date']}")


if __name__ == "__main__":
    main()
