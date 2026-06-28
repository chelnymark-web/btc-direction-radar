#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
宏观维度(阶段九:当前 BTC 最高关联、却最该补的一块)。
2026 年 BTC = 高 beta 科技股:与纳指相关 +0.7~0.8,反弹天花板由股市定,股市回调时放大下跌。
所以把"纳指 / DXY / VIX / 油价(+黄金作恐慌极端)"合成一个宏观方向分,接进指挥台。

数据源:Yahoo Finance chart 接口(query1 失败退 query2,带浏览器 UA;本地可直拉,
容器里被白名单挡——这正是要在本地/Actions 端取、网页回退快照的原因)。
输出:data/macro.json,供 command-deck.html 的"宏观"维度读取(直拉失败时回退此快照)。

方向约定(对 BTC 而言):
  纳指涨 = 偏多分;DXY 涨 = 偏空分;VIX 涨 = 偏空分;油价急升 = 偏空分。
  黄金不计入主分,仅在"与 BTC 罕见同向下跌"时作恐慌极端信号高亮(BTC 方向由别处给)。
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

SESSION = requests.Session()
# Yahoo 对默认 python-requests UA 偶尔 429/拒绝,伪装成浏览器更稳
SESSION.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"})

YAHOO_HOSTS = ["https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"]


def yahoo_closes(symbol, rng="1mo", interval="1d", tries=3):
    """返回 (closes[], 该序列最后一个非空收盘);query1 失败退 query2,带重试。"""
    last_err = None
    for host in YAHOO_HOSTS:
        url = f"{host}/v8/finance/chart/{symbol}"
        for i in range(tries):
            try:
                r = SESSION.get(url, params={"interval": interval, "range": rng}, timeout=20)
                r.raise_for_status()
                res = r.json()["chart"]["result"][0]
                quote = res["indicators"]["quote"][0]
                closes = [c for c in quote.get("close", []) if c is not None]
                if len(closes) >= 2:
                    return closes
            except Exception as e:  # noqa: BLE001
                last_err = e
    raise last_err or RuntimeError(f"{symbol} 无数据")


def pct(a, b):
    return (a - b) / b * 100 if b else 0.0


# 每个资产:对 BTC 的方向符号 + 日变动的偏高/极端阈值(按各自波动校准)
# btc_sign: 该资产上涨对 BTC 是利多(+1)还是利空(-1)
# 参与方向打分:纳指为主力,标普作大盘佐证;其余股指只展示不打分(避免与纳指高度同步而稀释信号)。
ASSETS = {
    "ndx":  {"symbol": "^NDX",     "name": "纳指100",  "btc_sign": +1, "warn": 0.8, "ext": 2.0, "weight": 0.38},
    "gspc": {"symbol": "^GSPC",    "name": "标普500",  "btc_sign": +1, "warn": 0.7, "ext": 1.8, "weight": 0.12},
    "dxy":  {"symbol": "DX-Y.NYB", "name": "美元指数",  "btc_sign": -1, "warn": 0.3, "ext": 0.7, "weight": 0.25},
    "vix":  {"symbol": "^VIX",     "name": "VIX恐慌",   "btc_sign": -1, "warn": 6.0, "ext": 15.0, "weight": 0.15},
    "oil":  {"symbol": "CL=F",     "name": "WTI原油",   "btc_sign": -1, "warn": 2.0, "ext": 5.0,  "weight": 0.10},
}

# 仅展示、不进打分的全球股指(看盘面全局;与 BTC 相关较弱或偏滞后)
INDICES = [
    {"key": "dji",  "symbol": "^DJI",      "name": "道指"},
    {"key": "hsi",  "symbol": "^HSI",      "name": "恒生"},
    {"key": "sse",  "symbol": "000001.SS", "name": "上证综指"},
    {"key": "n225", "symbol": "^N225",     "name": "日经225"},
    {"key": "dax",  "symbol": "^GDAXI",    "name": "德国DAX"},
    {"key": "sx5e", "symbol": "^STOXX50E", "name": "欧洲50"},
]


def level_of(change_pct, warn, ext):
    a = abs(change_pct)
    if a >= ext:
        return 2
    if a >= warn:
        return 1
    return 0


def note_for(key, last, dod, w5, level, direction):
    arrow = "↑" if dod > 0 else ("↓" if dod < 0 else "→")
    base = f"{arrow}{dod:+.2f}%(5日 {w5:+.1f}%)"
    if key == "ndx":
        tail = "科技股=BTC 最强领先;涨=风险偏好回暖(对 BTC 偏多),跌=天花板压低(放大杀跌)"
    elif key == "gspc":
        tail = "美股大盘风险情绪;与纳指同向,作佐证。涨=risk-on(对 BTC 偏多),跌=risk-off"
    elif key == "dxy":
        line = "上破" if last >= 100 else "下守"
        tail = f"{line} 100 关口;美元强=全球流动性紧(对 BTC 偏空),弱=放水(偏多)"
    elif key == "vix":
        zone = "≥25 risk-off" if last >= 25 else ("20-25 警戒" if last >= 20 else "<20 平静")
        tail = f"现 {last:.1f}({zone});急升=避险抛售(对 BTC 偏空)"
    else:  # oil
        tail = "急升=通胀火星+强化鹰派/滞胀(对 BTC 偏空);急跌=需求忧虑但缓解通胀压力"
    side = {1: "→ 偏多 BTC", -1: "→ 偏空 BTC", 0: "→ 中性"}[direction]
    return f"{base}|{tail} {side if level else '(变动温和,近中性)'}"


def component(key, cfg):
    closes = yahoo_closes(cfg["symbol"])
    last, prev = closes[-1], closes[-2]
    dod = pct(last, prev)
    w5 = pct(last, closes[-6]) if len(closes) >= 6 else pct(last, closes[0])
    level = level_of(dod, cfg["warn"], cfg["ext"])
    # 方向:资产涨跌方向 × 它对 BTC 的符号;变动温和(level=0)记中性
    raw_dir = (1 if dod > 0 else (-1 if dod < 0 else 0)) * cfg["btc_sign"]
    direction = raw_dir if level > 0 else 0
    # DXY 在 100 上方且走强,空头含义加强(把 level 提一档,封顶 2)
    if key == "dxy" and last >= 100 and direction < 0 and level == 1:
        level = 2
    return {
        "name": cfg["name"], "symbol": cfg["symbol"], "value": round(last, 2),
        "dod_pct": round(dod, 2), "w5_pct": round(w5, 2),
        "level": level, "direction": direction, "weight": cfg["weight"],
        "note": note_for(key, last, dod, w5, level, direction),
    }


def gold_signal():
    """黄金:仅作恐慌极端的旁证(去货币化/流动性危机时 BTC 与黄金罕见同向下跌)。"""
    try:
        closes = yahoo_closes("GC=F")
        last, prev = closes[-1], closes[-2]
        dod = pct(last, prev)
        return {"name": "黄金", "symbol": "GC=F", "value": round(last, 2),
                "dod_pct": round(dod, 2), "falling": dod < -0.5,
                "note": f"{'↓' if dod < 0 else '↑'}{dod:+.2f}%;只在与 BTC 同向下跌时读作恐慌最深处(去货币化/流动性危机)"}
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 黄金获取失败(非关键,跳过): {e}", file=sys.stderr)
        return None


def indices_block():
    """仅展示的全球股指:取最新价 + 日变动 + 5日变动,不参与方向打分。"""
    out = []
    for it in INDICES:
        try:
            closes = yahoo_closes(it["symbol"])
            last, prev = closes[-1], closes[-2]
            w5 = pct(last, closes[-6]) if len(closes) >= 6 else pct(last, closes[0])
            out.append({"key": it["key"], "name": it["name"], "symbol": it["symbol"],
                        "value": round(last, 2), "dod_pct": round(pct(last, prev), 2),
                        "w5_pct": round(w5, 1)})
        except Exception as e:  # noqa: BLE001
            print(f"[warn] {it['name']}({it['symbol']}) 跳过: {e}", file=sys.stderr)
    return out


def main():
    os.makedirs(DATA, exist_ok=True)
    comps, score, wsum = {}, 0.0, 0.0
    for key, cfg in ASSETS.items():
        try:
            c = component(key, cfg)
            comps[key] = c
            # 分项方向分 -1~+1 = direction × (level/2),按权重汇总
            score += c["direction"] * (c["level"] / 2.0) * c["weight"]
            wsum += c["weight"]
        except Exception as e:  # noqa: BLE001
            print(f"[warn] {cfg['name']}({cfg['symbol']}) 获取失败,本项跳过: {e}", file=sys.stderr)

    out = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "components": comps,
        "indices": indices_block(),
        "gold": gold_signal(),
        "coverage": f"{len(comps)}/{len(ASSETS)}",
    }
    if wsum > 0:
        # 归一化到有效权重,再映射成 -100(宏观偏空)~ +100(宏观偏多)
        macro_dir = score / wsum                 # -1 ~ +1
        out["macro_dir"] = round(macro_dir, 3)
        out["macro_bias"] = round(macro_dir * 100)
        # 恐慌极端:纳指跌 + 黄金也跌 = 流动性危机味道,单独标记
        g = out.get("gold")
        ndx_down = comps.get("ndx", {}).get("direction", 0) < 0
        out["panic_extreme"] = bool(g and g.get("falling") and ndx_down)
    else:
        out["macro_dir"] = None
        out["macro_bias"] = None
        out["panic_extreme"] = False

    with open(os.path.join(DATA, "macro.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    bias = out.get("macro_bias")
    print(f"[ok] 宏观: 覆盖 {out['coverage']}, macro_bias={bias} "
          f"({'偏多' if (bias or 0) > 0 else '偏空' if (bias or 0) < 0 else '中性'})"
          f"{' · 恐慌极端(纳指+黄金齐跌)' if out.get('panic_extreme') else ''}")
    for k, c in comps.items():
        print(f"    {c['name']:8} {c['value']:>10} {c['dod_pct']:+.2f}% "
              f"[{'多' if c['direction']>0 else '空' if c['direction']<0 else '·'} L{c['level']}]")
    idx = out.get("indices", [])
    if idx:
        print(f"  参考股指 {len(idx)}/{len(INDICES)}: "
              + " | ".join(f"{c['name']} {c['dod_pct']:+.2f}%" for c in idx))


if __name__ == "__main__":
    main()
