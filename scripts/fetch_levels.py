#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
关键位 + 斐波那契 + 重合(confluence)计算。
读 data/levels.json(手动维护的支撑/阻力/均线/缺口/MSTR + 斐波那契摆动区间),
自动算斐波那契回撤/扩展位,把所有位放在一起找"彼此靠近"的重合带,
按距现价排序、标注在上方还是下方、距现价百分比,输出 data/levels_status.json。

核心理念:斐波那契/单条支撑阻力单独看是噪音;多个不同来源的位重合处才是真磁吸/阻力。
"""
import json
import os
import sys
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
BINANCE = os.environ.get("BINANCE_FAPI", "https://fapi.binance.com")

FIB_RETRACE = [0.236, 0.382, 0.5, 0.618, 0.786]
FIB_EXTEND = [1.272, 1.618]


def get_btc_price(fallback):
    try:
        with open(os.path.join(DATA, "latest.json"), encoding="utf-8") as f:
            p = json.load(f).get("price")
            if p:
                return float(p)
    except Exception:  # noqa: BLE001
        pass
    try:
        r = requests.get(f"{BINANCE}/fapi/v1/premiumIndex", params={"symbol": "BTCUSDT"},
                         headers={"User-Agent": "bias-radar/1.0"}, timeout=20)
        r.raise_for_status()
        return float(r.json()["indexPrice"])
    except Exception as e:  # noqa: BLE001
        print(f"[warn] BTC 价获取失败,用配置 spot_ref: {e}", file=sys.stderr)
        return fallback


def fib_levels(high, low):
    """回撤位(区间内)+ 扩展位(区间外,上下都给)。"""
    r = high - low
    out = []
    for f in FIB_RETRACE:
        out.append({"price": round(high - r * f), "type": "斐波那契",
                    "label": f"fib {f:.3f} 回撤", "src": "fib"})
    for f in FIB_EXTEND:
        out.append({"price": round(low + r * f), "type": "斐波那契",
                    "label": f"fib {f:.3f} 扩展(上)", "src": "fib"})
        out.append({"price": round(high - r * f), "type": "斐波那契",
                    "label": f"fib {f:.3f} 扩展(下)", "src": "fib"})
    return [x for x in out if x["price"] > 0]


def cluster(levels, spot, pct):
    """把彼此在 pct% 内的位聚成重合带。"""
    levels = sorted(levels, key=lambda x: x["price"])
    bands, cur = [], []
    for lv in levels:
        if not cur:
            cur = [lv]
            continue
        if abs(lv["price"] - cur[-1]["price"]) / cur[-1]["price"] * 100 <= pct:
            cur.append(lv)
        else:
            bands.append(cur)
            cur = [lv]
    if cur:
        bands.append(cur)

    out = []
    for b in bands:
        prices = [x["price"] for x in b]
        srcs = sorted(set(x["src"] for x in b))
        mid = sum(prices) / len(prices)
        out.append({
            "lo": min(prices), "hi": max(prices), "mid": round(mid),
            "members": [f"{x['label']}" for x in b],
            "sources": srcs,
            "n_sources": len(srcs),
            "confluence": len(srcs) >= 2,   # 两个不同来源以上才算真重合
            "side": "上方" if mid > spot else "下方",
            "dist_pct": round((mid - spot) / spot * 100, 1),
        })
    out.sort(key=lambda x: abs(x["dist_pct"]))
    return out


def main():
    try:
        with open(os.path.join(DATA, "levels.json"), encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 读不到 levels.json,跳过: {e}", file=sys.stderr)
        return

    spot = get_btc_price(cfg.get("spot_ref"))
    all_levels = list(cfg.get("levels", []))
    sw = cfg.get("fib_swing", {})
    if sw.get("high") and sw.get("low"):
        all_levels += fib_levels(sw["high"], sw["low"])
    # 未回补 CME 缺口的中点作为一个位
    for g in cfg.get("cme_gaps", []):
        if not g.get("filled"):
            all_levels.append({"price": round((g["low"] + g["high"]) / 2),
                               "type": "缺口", "label": g.get("label", "CME 缺口"), "src": "CME"})

    bands = cluster(all_levels, spot, cfg.get("confluence_pct", 1.5))
    out = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "spot": spot,
        "bands": bands,
        "confluence_bands": [b for b in bands if b["confluence"]],
    }
    with open(os.path.join(DATA, "levels_status.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    conf = out["confluence_bands"]
    print(f"[ok] 关键位: 现价 {spot:.0f}, 共 {len(bands)} 带, 其中 {len(conf)} 个重合带:")
    for b in conf[:6]:
        print(f"    {b['mid']:>7} ({b['side']} {b['dist_pct']:+.1f}%) "
              f"[{'+'.join(b['sources'])}] {' / '.join(b['members'])}")


if __name__ == "__main__":
    main()
