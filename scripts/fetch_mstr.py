#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MSTR(Strategy)监控模块。
设计前提(诚实):MSTR 的 mNAV / 持仓 / 卖币 / 股息覆盖没有干净的免费实时 API,
所以采用「实时 + 慢变量」混合:
  - 实时:BTC 价(从 latest.json 或 Binance)
  - 慢变量:持仓/成本/债务/股息/股数,从 data/mstr.json 手动维护(看到 8-K 就更新)
据此自动算 mNAV、距成本线、股息覆盖,并按阈值给状态标记。
输出 data/mstr_status.json,供报告与面板使用。
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


def get_btc_price():
    """优先用本轮 latest.json 的价(同一快照一致),取不到再打 Binance。"""
    try:
        with open(os.path.join(DATA, "latest.json"), encoding="utf-8") as f:
            p = json.load(f).get("price")
            if p:
                return float(p)
    except Exception:  # noqa: BLE001
        pass
    try:
        r = requests.get(f"{BINANCE}/fapi/v1/premiumIndex",
                         params={"symbol": "BTCUSDT"},
                         headers={"User-Agent": "bias-radar/1.0"}, timeout=20)
        r.raise_for_status()
        return float(r.json()["indexPrice"])
    except Exception as e:  # noqa: BLE001
        print(f"[warn] BTC 价获取失败: {e}", file=sys.stderr)
        return None


def main():
    try:
        with open(os.path.join(DATA, "mstr.json"), encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 读不到 mstr.json,跳过 MSTR 模块: {e}", file=sys.stderr)
        return

    btc = get_btc_price()
    th = cfg["thresholds"]
    out = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of_config": cfg.get("as_of"),
        "btc_price": btc,
        "avg_cost": cfg["avg_cost_usd"],
        "holdings": cfg["btc_holdings"],
        "flags": {},
    }

    # 1. 距成本线
    if btc:
        dist = (btc - cfg["avg_cost_usd"]) / cfg["avg_cost_usd"] * 100
        out["dist_to_cost_pct"] = round(dist, 1)
        out["underwater"] = btc < cfg["avg_cost_usd"]
        out["flags"]["cost_basis"] = {
            "level": 2 if btc < cfg["avg_cost_usd"] else (1 if dist < 8 else 0),
            "note": f"现价距 MSTR 成本线 {cfg['avg_cost_usd']:,} 为 {dist:+.1f}%"
                    + ("(仓位浮亏)" if btc < cfg["avg_cost_usd"] else ""),
        }

    # 2. mNAV:有真实市值用真实,否则用 premium_hint 提示(并标注是估计)
    btc_value_musd = (btc * cfg["btc_holdings"] / 1e6) if btc else None
    mcap = cfg.get("last_mstr_marketcap_musd")
    if mcap and btc_value_musd:
        mnav = mcap / btc_value_musd
        mnav_src = "真实市值"
    else:
        mnav = cfg.get("premium_hint")
        mnav_src = "手填 premium_hint(非实时,仅提示)"
    if mnav:
        out["mnav"] = round(mnav, 3)
        out["mnav_source"] = mnav_src
        lvl = 2 if mnav <= th["mnav_extreme"] else (1 if mnav <= th["mnav_warn"] else 0)
        out["flags"]["mnav"] = {
            "level": lvl,
            "note": f"mNAV {mnav:.2f}({mnav_src});跌破 {th['mnav_extreme']:.2f}=飞轮反转、被迫卖币概率跳升",
        }

    # 3. 股息覆盖月数(纯慢变量)
    if cfg.get("usd_reserve_musd") and cfg.get("annual_dividends_musd"):
        months = cfg["usd_reserve_musd"] / (cfg["annual_dividends_musd"] / 12)
        out["usd_div_coverage_months"] = round(months, 1)
        out["flags"]["div_coverage"] = {
            "level": 2 if months < 6 else (1 if months < th["dividend_months_warn"] else 0),
            "note": f"现金仅够付 {months:.1f} 个月优先股息;低于 {th['dividend_months_warn']} 个月=融资压力、逼近被迫卖币",
        }

    # 4. 是否已卖币(行为信号,慢变量)
    if th.get("selling_btc"):
        out["flags"]["selling"] = {
            "level": 1,
            "note": "已在卖币付息(2026-06-01 起)。规模显著扩大=Saylor 铁律实质破裂的行为信号——读 8-K,别听 X 表态。",
        }

    # 聚合一个 MSTR 压力分(0 平静 ~ 100 临界)
    levels = [f["level"] for f in out["flags"].values()]
    out["stress_score"] = round(sum(levels) / (len(levels) * 2) * 100) if levels else 0

    with open(os.path.join(DATA, "mstr_status.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[ok] MSTR: mNAV={out.get('mnav')} 距成本={out.get('dist_to_cost_pct')}% "
          f"覆盖={out.get('usd_div_coverage_months')}月 压力分={out['stress_score']}")


if __name__ == "__main__":
    main()
