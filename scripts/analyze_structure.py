#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
下跌趋势结构验证与监控(文档 D)。
下跌趋势的教科书定义:高点更低(LH)+ 低点更低(LL)。只要维持,空头结构成立。
本脚本用真实日线 OHLC:
  1) 分形法(fractal)找摆动高低点(swing highs/lows);
  2) 判定 LH/LL 纯度;标出违反点 HH(higher high)/HL(higher low)——用户最想要的"裂缝";
  3) RSI 背离预警(价格创新低但 RSI 不创新低=动能衰竭,结构违反前兆);
  4) 接 levels.json 关键阻力:站上 75k 超强重合带=重大结构破坏,高亮;
输出 data/structure_status.json,可做成指挥台"结构"维度(下跌完好=偏空分)。
诚实要求:如实列出每一次违反点,不为迎合"一直下跌"而隐藏。结构规律滞后于价格,只确认趋势、不预测拐点。
"""
import json
import os
import sys
import urllib.request
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

FRACTAL_N = int(os.environ.get("STRUCT_FRACTAL_N", "5"))   # 摆动点左右各 N 根确认(越大=只留主要转折),可配置
RSI_LEN = 14
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"}


def _get(url, timeout=20):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout))


def klines(limit=500):
    """真 OHLC 日线,逐级兜底。返回 [(date, o, h, l, c), ...] 升序。"""
    # data-api.binance.vision 是 Binance 公开行情镜像,不受地区限制,最稳
    bases = ["https://data-api.binance.vision", "https://api.binance.com", "https://api1.binance.com"]
    for b in bases:
        for _ in range(2):
            try:
                d = _get(f"{b}/api/v3/klines?symbol=BTCUSDT&interval=1d&limit={limit}")
                bars = [(datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).date(),
                         float(k[1]), float(k[2]), float(k[3]), float(k[4])) for k in d]
                if len(bars) > 60:
                    return bars, f"Binance({b.split('//')[1]})"
            except Exception:  # noqa: BLE001
                pass
    # Gate.io 兜底: [t, vol, close, high, low, open, ...]
    try:
        d = _get(f"https://api.gateio.ws/api/v4/spot/candlesticks?currency_pair=BTC_USDT&interval=1d&limit={limit}")
        bars = [(datetime.fromtimestamp(int(k[0]), tz=timezone.utc).date(),
                 float(k[5]), float(k[3]), float(k[4]), float(k[2])) for k in d]
        if len(bars) > 60:
            return bars, "Gate.io"
    except Exception as e:  # noqa: BLE001
        print(f"[warn] Gate.io 兜底失败: {e}", file=sys.stderr)
    return None, None


def rsi(closes, n=RSI_LEN):
    """Wilder RSI,返回与 closes 等长(前 n 个为 None)。"""
    out = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        ch = closes[i] - closes[i - 1]
        gains += max(ch, 0)
        losses += max(-ch, 0)
    ag, al = gains / n, losses / n
    out[n] = 100 - 100 / (1 + (ag / al if al else 999))
    for i in range(n + 1, len(closes)):
        ch = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(ch, 0)) / n
        al = (al * (n - 1) + max(-ch, 0)) / n
        out[i] = 100 - 100 / (1 + (ag / al if al else 999))
    return out


def swings(bars, n=FRACTAL_N):
    """分形法找摆动点,再压成交替的 zigzag 序列。返回 [(idx, 'H'/'L', price, date)]。"""
    raw = []
    for i in range(n, len(bars) - n):
        h, l = bars[i][2], bars[i][3]
        if all(h >= bars[j][2] for j in range(i - n, i + n + 1)):
            raw.append((i, "H", h, bars[i][0]))
        if all(l <= bars[j][3] for j in range(i - n, i + n + 1)):
            raw.append((i, "L", l, bars[i][0]))
    raw.sort(key=lambda x: x[0])
    seq = []
    for p in raw:
        if not seq:
            seq.append(p)
            continue
        if p[1] == seq[-1][1]:                       # 连续同类:保留更极端的
            if (p[1] == "H" and p[2] > seq[-1][2]) or (p[1] == "L" and p[2] < seq[-1][2]):
                seq[-1] = p
        else:
            seq.append(p)
    return seq


def classify(seq, rsi_vals):
    """对相邻同类摆动点判 LH/HH、LL/HL,收集违反点与背离。"""
    highs = [s for s in seq if s[1] == "H"]
    lows = [s for s in seq if s[1] == "L"]
    lh = hh = ll = hl = 0
    violations = []
    for prev, cur in zip(highs, highs[1:]):
        if cur[2] < prev[2]:
            lh += 1
        else:
            hh += 1
            violations.append({"type": "HH", "date": cur[3].isoformat(), "price": round(cur[2]),
                               "note": "高点抬高=下跌结构第一道裂缝"})
    div = None
    for prev, cur in zip(lows, lows[1:]):
        if cur[2] < prev[2]:
            ll += 1
            rp, rc = rsi_vals[prev[0]], rsi_vals[cur[0]]
            if rp is not None and rc is not None and rc > rp + 1:
                div = {"date": cur[3].isoformat(), "price": round(cur[2]),
                       "rsi_prev": round(rp, 1), "rsi_now": round(rc, 1),
                       "note": "价格创新低但 RSI 抬高=底背离、动能衰竭,结构违反前兆"}
        else:
            hl += 1
            violations.append({"type": "HL", "date": cur[3].isoformat(), "price": round(cur[2]),
                               "note": "低点抬高=更强的反转信号"})
    hh_tot, ll_tot = lh + hh, ll + hl
    purity = None
    if hh_tot + ll_tot:
        purity = round((lh + ll) / (hh_tot + ll_tot) * 100)
    return {"lh": lh, "hh": hh, "ll": ll, "hl": hl, "purity_pct": purity,
            "violations": violations, "divergence": div}


def key_resistances():
    """从 levels.json 拿关键阻力(用于'站上=结构破坏'判定)。"""
    try:
        with open(os.path.join(DATA, "levels.json"), encoding="utf-8") as f:
            cfg = json.load(f)
        levels = [lv["price"] for lv in cfg.get("levels", []) if lv.get("price")]
        return sorted(set(levels))
    except Exception:  # noqa: BLE001
        return []


def main():
    os.makedirs(DATA, exist_ok=True)
    bars_all, src = klines()
    if not bars_all:
        print("[warn] 无法获取日线,未生成 structure_status.json", file=sys.stderr)
        sys.exit(1)

    spot = bars_all[-1][4]
    r_all = rsi([b[4] for b in bars_all])
    # 锚定到 ATH 之后的下跌段(文档D:从 2025-10 的 ATH 至今),不把之前的上涨段算进结构统计
    ath_idx = max(range(len(bars_all)), key=lambda i: bars_all[i][2])
    ath = {"price": round(bars_all[ath_idx][2]), "date": bars_all[ath_idx][0].isoformat()}
    start = max(0, ath_idx - FRACTAL_N)          # 留一点余量,让 ATH 本身能被识别为 swing high
    bars = bars_all[start:]
    r = r_all[start:]
    seq = swings(bars)
    cl = classify(seq, r)

    # 最近摆动点序列(展示用,最多 8 个)
    recent = [{"type": s[1], "price": round(s[2]), "date": s[3].isoformat()} for s in seq[-8:]]

    # 关键阻力联动:站上 75k 级重合带 = 重大结构破坏
    MAJOR = 75000
    reclaimed_major = spot >= MAJOR
    last_high = max((s for s in seq if s[1] == "H"), key=lambda x: x[0], default=None)
    last_low = max((s for s in seq if s[1] == "L"), key=lambda x: x[0], default=None)

    dd_from_ath = (spot - ath["price"]) / ath["price"] * 100   # 宏观趋势:距 ATH 回撤

    # 状态判定(诚实:看最近的违反点)
    recent_viol = [v for v in cl["violations"] if v["date"] >= (bars[-45][0].isoformat() if len(bars) > 45 else bars[0][0].isoformat())]
    has_hh = any(v["type"] == "HH" for v in recent_viol)
    has_hl = any(v["type"] == "HL" for v in recent_viol)
    if reclaimed_major:
        status = "重大结构破坏(站上 75k 超强重合带)"
    elif has_hl and has_hh:
        status = "结构转折中(近期同现 HL+HH)"
    elif has_hl:
        status = "出现 HL(反转前兆,低点抬高)"
    elif has_hh:
        status = "出现 HH(结构裂缝,高点抬高)"
    elif cl["purity_pct"] is not None and cl["purity_pct"] >= 60:
        status = "下跌结构完好(LH+LL 为主)"
    else:
        status = "结构不清晰(震荡,LH/LL 不占优)"

    # 结构方向分(供指挥台"结构"维度):下跌完好=偏空(-),违反/破坏=转多/警惕(+)
    if reclaimed_major:
        struct_dir = 0.6
    elif has_hl:
        struct_dir = 0.3
    elif has_hh:
        struct_dir = 0.1
    elif cl["purity_pct"] and cl["purity_pct"] >= 60:
        struct_dir = -0.5
    else:
        struct_dir = 0.0

    out = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": src, "spot": round(spot, 1), "bars": len(bars),
        "from": bars[0][0].isoformat(), "ath": ath, "fractal_n": FRACTAL_N,
        "dd_from_ath_pct": round(dd_from_ath, 1),
        "macro_trend": "宏观下跌" if dd_from_ath <= -15 else ("宏观上行" if dd_from_ath >= 15 else "宏观震荡"),
        "rsi_now": round(r[-1], 1) if r[-1] is not None else None,
        "status": status,
        "struct_dir": struct_dir,                 # -1(下跌完好/偏空)~ +1(破坏/转多)
        "purity_pct": cl["purity_pct"],
        "counts": {"LH": cl["lh"], "LL": cl["ll"], "HH": cl["hh"], "HL": cl["hl"]},
        "last_swing_high": {"price": round(last_high[2]), "date": last_high[3].isoformat()} if last_high else None,
        "last_swing_low": {"price": round(last_low[2]), "date": last_low[3].isoformat()} if last_low else None,
        "recent_swings": recent,
        "violations": cl["violations"][-12:],     # 最近的违反历史
        "divergence": cl["divergence"],
        "reclaimed_major_75k": reclaimed_major,
        "note": "结构规律滞后于价格,只确认趋势不预测拐点;站上 75k(MA200+MSTR成本+0.236斐波)=需重评做空大逻辑。",
    }
    with open(os.path.join(DATA, "structure_status.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    c = cl["counts"] = out["counts"]
    print(f"[ok] 下跌结构: 源={src} 锚定 ATH {ath['price']}({ath['date']}) 至今 {len(bars)} 根, 现价 {spot:.0f}, RSI {out['rsi_now']}")
    print(f"    宏观: {out['macro_trend']} 距ATH {out['dd_from_ath_pct']}% | 摆动结构: {status} 纯度 {cl['purity_pct']}% (LH{c['LH']} LL{c['LL']} / HH{c['HH']} HL{c['HL']})")
    if out["violations"]:
        print(f"    最近违反点:")
        for v in out["violations"][-5:]:
            print(f"      {v['date']} {v['type']} @ {v['price']} — {v['note']}")
    else:
        print("    无违反点(整段 LH+LL,下跌结构完好)")
    if out["divergence"]:
        print(f"    ⚠ 底背离: {out['divergence']['date']} RSI {out['divergence']['rsi_prev']}→{out['divergence']['rsi_now']}")


if __name__ == "__main__":
    main()
