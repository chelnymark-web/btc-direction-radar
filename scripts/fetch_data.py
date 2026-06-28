#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抓取巡检清单数据并计算拥挤度标记。
数据源(全部为免费公开端点,不需要 API key):
  - Binance 合约: 资金费率 / 未平仓合约 OI / 大户持仓多空比 / 全体账户多空比 / 主动买卖比 / 季度基差
  - Deribit: 指数价 / DVOL / 期权链 mark IV(本地用 Black-Scholes 算 delta,得到 25-delta skew 与期限结构)
输出:
  - data/latest.json      最新快照(面板与报告共用)
  - data/snapshots.jsonl  历史累积(用于百分位判定"极端")
"""
import json
import math
import os
import sys
import time
from datetime import datetime, timezone

import requests

# Windows 控制台默认 GBK,中文 [ok]/[warn] 会乱码;统一切 UTF-8(Py3.7+,失败则忽略)
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

# GitHub Actions 里 secrets 未设置时会注入空字符串,清掉以免 requests 报错
for var in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
    if os.environ.get(var) == "":
        del os.environ[var]

BINANCE = os.environ.get("BINANCE_FAPI", "https://fapi.binance.com")
DERIBIT = "https://www.deribit.com/api/v2"
GATE = "https://api.gateio.ws/api/v4"   # 备用源:Binance 封美国 IP(GitHub Actions)时切到这
SYMBOL = "BTCUSDT"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "bias-radar/1.0 (personal research)"})


def get(url, params=None, tries=3):
    """带重试:瞬时超时/网络抖动(曾见 Binance fundingRate 超时)不应丢字段。"""
    last = None
    for i in range(tries):
        try:
            r = SESSION.get(url, params=params, timeout=25)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
            if i < tries - 1:
                time.sleep(1.5 * (i + 1))  # 线性退避 1.5s / 3s
    raise last


def safe(fn, label):
    """单项失败不拖垮整个快照,打警告继续。"""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        print(f"[warn] {label} 获取失败: {e}", file=sys.stderr)
        return None


# ---------- Black-Scholes 辅助(用 mark IV 反推 delta) ----------

def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_delta(spot, strike, t_years, iv, is_call):
    if t_years <= 0 or iv <= 0:
        return None
    d1 = (math.log(spot / strike) + 0.5 * iv * iv * t_years) / (iv * math.sqrt(t_years))
    return norm_cdf(d1) if is_call else norm_cdf(d1) - 1.0


MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
          "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}


def parse_instrument(name):
    """BTC-26JUN26-100000-P -> (expiry_ts, strike, is_call)"""
    try:
        _, dstr, strike, cp = name.split("-")
        day = int(dstr[:-5])
        mon = MONTHS[dstr[-5:-2]]
        year = 2000 + int(dstr[-2:])
        expiry = datetime(year, mon, day, 8, 0, tzinfo=timezone.utc)  # Deribit 到期 08:00 UTC
        return expiry.timestamp(), float(strike), cp == "C"
    except Exception:  # noqa: BLE001
        return None


# ---------- Binance ----------

def fetch_binance():
    out = {}
    prem = safe(lambda: get(f"{BINANCE}/fapi/v1/premiumIndex", {"symbol": SYMBOL}), "premiumIndex")
    if prem:
        mark, idx = float(prem["markPrice"]), float(prem["indexPrice"])
        out["price"] = idx
        out["funding_rate"] = float(prem["lastFundingRate"])  # 每 8 小时
        out["perp_basis_pct"] = (mark - idx) / idx * 100

    hist = safe(lambda: get(f"{BINANCE}/fapi/v1/fundingRate", {"symbol": SYMBOL, "limit": 90}), "fundingRate 历史")
    if hist:
        rates = [float(x["fundingRate"]) for x in hist]
        out["funding_mean_30d"] = sum(rates) / len(rates)

    oi = safe(lambda: get(f"{BINANCE}/futures/data/openInterestHist",
                          {"symbol": SYMBOL, "period": "1h", "limit": 48}), "openInterestHist")
    if oi and len(oi) >= 25:
        latest, prev24 = float(oi[-1]["sumOpenInterest"]), float(oi[-25]["sumOpenInterest"])
        out["oi_btc"] = latest
        out["oi_change_24h_pct"] = (latest - prev24) / prev24 * 100

    top = safe(lambda: get(f"{BINANCE}/futures/data/topLongShortPositionRatio",
                           {"symbol": SYMBOL, "period": "1h", "limit": 2}), "大户多空比")
    if top:
        out["top_ls_ratio"] = float(top[-1]["longShortRatio"])

    glob = safe(lambda: get(f"{BINANCE}/futures/data/globalLongShortAccountRatio",
                            {"symbol": SYMBOL, "period": "1h", "limit": 2}), "全体多空比")
    if glob:
        out["global_ls_ratio"] = float(glob[-1]["longShortRatio"])

    taker = safe(lambda: get(f"{BINANCE}/futures/data/takerlongshortRatio",
                             {"symbol": SYMBOL, "period": "1h", "limit": 2}), "主动买卖比")
    if taker:
        out["taker_ratio"] = float(taker[-1]["buySellRatio"])

    basis = safe(lambda: get(f"{BINANCE}/futures/data/basis",
                             {"pair": "BTCUSDT", "contractType": "CURRENT_QUARTER",
                              "period": "1h", "limit": 2}), "季度基差")
    if basis:
        out["quarter_basis_pct"] = float(basis[-1]["basisRate"]) * 100
    return out


# ---------- Gate.io 备用源 ----------
# Binance 的 fapi 会拦美国 IP(GitHub Actions 常见 451/403),导致云端快照缺价格/费率/多空比。
# Gate.io 公开合约接口一般不封美国,且 contract_stats 一次给齐 OI/大户多空/全体多空/主动买卖。
# 字段名与 Binance 对齐,下游(拥挤度标记、网页、报告)无需改动。

def fetch_gate():
    out = {}
    c = safe(lambda: get(f"{GATE}/futures/usdt/contracts/BTC_USDT"), "Gate 合约")
    if c:
        mark, idx = float(c["mark_price"]), float(c["index_price"])
        out["price"] = idx
        out["funding_rate"] = float(c["funding_rate"])          # 每 8h,与 Binance 同口径
        out["perp_basis_pct"] = (mark - idx) / idx * 100

    fh = safe(lambda: get(f"{GATE}/futures/usdt/funding_rate",
                          {"contract": "BTC_USDT", "limit": 90}), "Gate 资金费率历史")
    if fh:
        rates = [float(x["r"]) for x in fh]
        out["funding_mean_30d"] = sum(rates) / len(rates)

    st = safe(lambda: get(f"{GATE}/futures/usdt/contract_stats",
                          {"contract": "BTC_USDT", "interval": "1h", "limit": 25}), "Gate 合约统计")
    if st:
        latest = st[-1]
        if len(st) >= 25:
            oi_now = float(latest.get("open_interest") or 0)
            oi_prev = float(st[-25].get("open_interest") or 0)
            out["oi_btc"] = oi_now
            if oi_prev:
                out["oi_change_24h_pct"] = (oi_now - oi_prev) / oi_prev * 100
        if latest.get("top_lsr_account") is not None:
            out["top_ls_ratio"] = float(latest["top_lsr_account"])    # 大户(头部账户)多空比
        if latest.get("lsr_account") is not None:
            out["global_ls_ratio"] = float(latest["lsr_account"])     # 全体账户多空比
        if latest.get("lsr_taker") is not None:
            out["taker_ratio"] = float(latest["lsr_taker"])           # 主动买卖比
    return out


# ---------- Deribit ----------

def fetch_deribit():
    out = {}
    idx = safe(lambda: get(f"{DERIBIT}/public/get_index_price", {"index_name": "btc_usd"}), "Deribit 指数")
    spot = idx["result"]["index_price"] if idx else None
    out["deribit_index"] = spot

    now_ms = int(time.time() * 1000)
    dvol = safe(lambda: get(f"{DERIBIT}/public/get_volatility_index_data",
                            {"currency": "BTC", "resolution": "3600",
                             "start_timestamp": now_ms - 48 * 3600 * 1000,
                             "end_timestamp": now_ms}), "DVOL")
    if dvol and dvol["result"].get("data"):
        out["dvol"] = dvol["result"]["data"][-1][4]  # [ts, o, h, l, c]

    book = safe(lambda: get(f"{DERIBIT}/public/get_book_summary_by_currency",
                            {"currency": "BTC", "kind": "option"}), "期权链")
    if not (book and spot):
        return out

    now_s = time.time()
    by_expiry = {}
    for item in book.get("result", []):
        parsed = parse_instrument(item.get("instrument_name", ""))
        iv = item.get("mark_iv")
        if not parsed or iv is None or iv <= 0:
            continue
        exp_ts, strike, is_call = parsed
        days = (exp_ts - now_s) / 86400
        if days < 5:  # 太近的到期噪声大,跳过
            continue
        by_expiry.setdefault(exp_ts, []).append((strike, is_call, float(iv)))

    expiries = sorted(by_expiry)
    if not expiries:
        return out

    def expiry_metrics(exp_ts):
        t_years = (exp_ts - now_s) / 86400 / 365.0
        rows = by_expiry[exp_ts]
        atm = min(rows, key=lambda r: abs(r[0] - spot))[2]
        best_put, best_call = None, None
        for strike, is_call, iv in rows:
            d = bs_delta(spot, strike, t_years, iv / 100.0, is_call)
            if d is None:
                continue
            if not is_call and strike < spot:
                gap = abs(d + 0.25)
                if best_put is None or gap < best_put[0]:
                    best_put = (gap, iv)
            if is_call and strike > spot:
                gap = abs(d - 0.25)
                if best_call is None or gap < best_call[0]:
                    best_call = (gap, iv)
        skew = (best_put[1] - best_call[1]) if (best_put and best_call) else None
        return atm, skew, (exp_ts - now_s) / 86400

    near = expiries[0]
    atm_near, skew_near, days_near = expiry_metrics(near)
    out["skew_25d"] = skew_near            # 正 = put 更贵 = 怕跌
    out["atm_iv_near"] = atm_near
    out["near_expiry_days"] = round(days_near, 1)

    later = [e for e in expiries if (e - near) / 86400 >= 14]
    if later:
        atm_next, _, days_next = expiry_metrics(later[0])
        out["atm_iv_next"] = atm_next
        out["term_slope"] = atm_next - atm_near   # 负 = 倒挂 = 近月更贵 = 眼前紧张
        out["next_expiry_days"] = round(days_next, 1)
    return out


# ---------- 拥挤度标记 ----------
# v1 静态启发阈值;snapshots.jsonl 累积满 30 条后自动切换为历史百分位判定。
# side: "long" = 多头侧拥挤/贪婪方向, "short" = 空头侧拥挤/恐惧方向
STATIC_RULES = {
    "funding_rate":     {"warn_hi": 0.0003, "ext_hi": 0.0008, "warn_lo": -0.0001, "ext_lo": -0.0004,
                         "hi_side": "long", "lo_side": "short"},
    "top_ls_ratio":     {"warn_hi": 2.0, "ext_hi": 3.0, "warn_lo": 0.8, "ext_lo": 0.6,
                         "hi_side": "long", "lo_side": "short"},
    "global_ls_ratio":  {"warn_hi": 2.5, "ext_hi": 4.0, "warn_lo": 0.9, "ext_lo": 0.7,
                         "hi_side": "long", "lo_side": "short"},
    "perp_basis_pct":   {"warn_hi": 0.05, "ext_hi": 0.15, "warn_lo": -0.03, "ext_lo": -0.10,
                         "hi_side": "long", "lo_side": "short"},
    "quarter_basis_pct": {"warn_hi": 2.5, "ext_hi": 5.0, "warn_lo": 0.0, "ext_lo": -1.0,
                          "hi_side": "long", "lo_side": "short"},
    "skew_25d":         {"warn_hi": 8.0, "ext_hi": 15.0, "warn_lo": -3.0, "ext_lo": -8.0,
                         "hi_side": "short", "lo_side": "long"},   # put 贵 = 恐惧
    "dvol":             {"warn_hi": 75.0, "ext_hi": 95.0, "warn_lo": 35.0, "ext_lo": 28.0,
                         "hi_side": None, "lo_side": None},        # 波动水位,不计方向
}


def load_history():
    path = os.path.join(DATA_DIR, "snapshots.jsonl")
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


def percentile_rank(values, x):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    below = sum(1 for v in vals if v <= x)
    return below / len(vals) * 100


def make_flags(snap, history):
    flags = {}
    hist_ok = len(history) >= 30
    for key, rule in STATIC_RULES.items():
        val = snap.get(key)
        if val is None:
            continue
        level, side, note = 0, None, "中性"
        if hist_ok:
            pct = percentile_rank([h.get(key) for h in history], val)
            if pct is not None:
                if pct >= 90 or pct <= 10:
                    level = 2
                elif pct >= 75 or pct <= 25:
                    level = 1
                side = rule["hi_side"] if pct >= 50 else rule["lo_side"]
                note = f"历史百分位 {pct:.0f}%"
        else:
            if val >= rule["ext_hi"]:
                level, side = 2, rule["hi_side"]
            elif val >= rule["warn_hi"]:
                level, side = 1, rule["hi_side"]
            elif val <= rule["ext_lo"]:
                level, side = 2, rule["lo_side"]
            elif val <= rule["warn_lo"]:
                level, side = 1, rule["lo_side"]
            note = "静态阈值(历史样本<30)"
        if level == 0:
            side = None
        flags[key] = {"level": level, "side": side, "note": note}
    return flags


def bias_score(flags):
    """聚合成 -100(空头侧拥挤饱和) ~ +100(多头侧拥挤饱和)。"""
    score, weight = 0.0, 0.0
    for f in flags.values():
        if f["side"] == "long":
            score += f["level"]
        elif f["side"] == "short":
            score -= f["level"]
        weight += 2
    return round(score / weight * 100) if weight else 0


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    snap = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    snap.update(fetch_binance())
    if snap.get("price") is None:
        # Binance 拿不到(多半是 GitHub 美国服务器被 451 拦)→ 切 Gate.io 备用源,补齐价格/费率/多空比
        print("[warn] Binance 无数据,切换备用源 Gate.io", file=sys.stderr)
        gate = fetch_gate()
        snap.update(gate)
        snap["deriv_source"] = "gate.io(备用)" if gate.get("price") is not None else "无(binance/gate 都失败)"
    else:
        snap["deriv_source"] = "binance"
    snap.update(fetch_deribit())

    history = load_history()
    snap["flags"] = make_flags(snap, history)
    snap["bias_score"] = bias_score(snap["flags"])

    with open(os.path.join(DATA_DIR, "latest.json"), "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    with open(os.path.join(DATA_DIR, "snapshots.jsonl"), "a", encoding="utf-8") as f:
        slim = {k: v for k, v in snap.items() if k != "flags"}
        f.write(json.dumps(slim, ensure_ascii=False) + "\n")

    got = [k for k in ("price", "funding_rate", "oi_btc", "skew_25d", "dvol") if snap.get(k) is not None]
    print(f"[ok] 快照完成,拿到 {len(got)} 个核心字段: {got}; bias={snap['bias_score']}")


if __name__ == "__main__":
    main()
