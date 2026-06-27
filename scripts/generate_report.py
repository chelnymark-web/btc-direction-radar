#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
读取 data/latest.json + data/news.json,生成 docs/reports/{kind}-{date}.html,
重建 docs/index.html,并把 latest.json 复制到 docs/data/ 供面板回退使用。
若设置了 ANTHROPIC_API_KEY,会请求 Claude 生成一段中文简评(可选,不设也能跑)。
"""
import json
import os
import re
import shutil
import sys
from datetime import date, datetime, timezone, timedelta

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
DOCS = os.path.join(ROOT, "docs")
JST = timezone(timedelta(hours=9))

KIND_CN = {"daily": "日报", "weekly": "周报", "monthly": "月报"}

METRIC_META = [
    ("price",            "BTC 价格",        "{:,.0f} USD", "锚定一切的那个数"),
    ("funding_rate",     "资金费率 / 8h",   "{:+.4%}",     "正=多头付钱(多头拥挤),负=空头付钱(空头拥挤);看极端不看常态"),
    ("oi_change_24h_pct","OI 24h 变化",     "{:+.2f}%",    "价格跌+OI升=新空进场;价格跌+OI骤降=多头被强平/去杠杆"),
    ("top_ls_ratio",     "大户持仓多空比",  "{:.2f}",      "更接近聪明钱;与全体账户比背离时跟这个"),
    ("global_ls_ratio",  "全体账户多空比",  "{:.2f}",      "散户情绪,极端值当反向素材"),
    ("perp_basis_pct",   "永续基差",        "{:+.3f}%",    "升水=多头狂热;贴水=恐慌折价"),
    ("quarter_basis_pct","季度基差",        "{:+.2f}%",    "深贴水历来是投降区域的味道之一"),
    ("dvol",             "DVOL",            "{:.1f}",      "BTC 的 VIX:整体恐惧水位"),
    ("skew_25d",         "25Δ skew",        "{:+.1f} vol", "正=put更贵=怕跌;翘到极端=该侧恐惧饱和,警惕反转"),
    ("term_slope",       "期限结构斜率",    "{:+.1f} vol", "负=近月倒挂=对眼前事件极度紧张"),
]

LEVEL_CN = {0: "中性", 1: "偏高", 2: "极端"}
SIDE_CN = {"long": "多头侧", "short": "空头侧", None: ""}

CSS = """
:root{--bg:#0E1116;--panel:#161B23;--line:#242B36;--ink:#E8E3D8;--dim:#8B93A1;
--cold:#5B8DB8;--hot:#D9842B;--alert:#C73E2E;--mono:'IBM Plex Mono',ui-monospace,'SF Mono',Menlo,monospace}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.75 -apple-system,'Hiragino Sans','Noto Sans CJK SC',sans-serif;padding:32px 20px}
.wrap{max-width:880px;margin:0 auto}a{color:var(--cold)}
h1{font-size:21px;letter-spacing:.04em;margin:0 0 4px}
.sub{color:var(--dim);font-size:13px;margin-bottom:28px}
h2{font-size:15px;letter-spacing:.12em;color:var(--dim);font-weight:600;
border-bottom:1px solid var(--line);padding-bottom:8px;margin:36px 0 14px}
table{width:100%;border-collapse:collapse;font-size:14px}
td,th{padding:9px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--dim);font-weight:500;font-size:12px;letter-spacing:.06em}
.num{font-family:var(--mono);font-size:14px;white-space:nowrap}
.chip{display:inline-block;padding:1px 9px;border-radius:99px;font-size:12px;border:1px solid var(--line);color:var(--dim)}
.chip.l1{border-color:var(--hot);color:var(--hot)}.chip.l2{background:var(--alert);border-color:var(--alert);color:#fff}
.chip.s-short.l1{border-color:var(--cold);color:var(--cold)}.chip.s-short.l2{background:var(--cold);border-color:var(--cold);color:#0E1116}
.use{color:var(--dim);font-size:12.5px}
.gauge{height:10px;border-radius:99px;background:linear-gradient(90deg,var(--cold),#3a4250 50%,var(--hot));position:relative;margin:10px 0 6px}
.needle{position:absolute;top:-5px;width:2px;height:20px;background:var(--ink)}
.glabel{display:flex;justify-content:space-between;color:var(--dim);font-size:12px}
.summary{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:18px 20px;white-space:pre-wrap}
.news li{margin-bottom:7px}.news .src{color:var(--dim);font-size:12px;font-family:var(--mono)}
.foot{color:var(--dim);font-size:12px;margin-top:40px;border-top:1px solid var(--line);padding-top:14px}
"""


def fmt(key, value, pattern):
    try:
        if key == "funding_rate":
            return f"{value:+.4%}"
        return pattern.format(value)
    except (TypeError, ValueError):
        return "—"


def claude_summary(snap, news):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    lines = [f"{m[1]}: {fmt(m[0], snap.get(m[0]), m[2])} [{LEVEL_CN[snap['flags'][m[0]]['level']]}"
             f" {SIDE_CN[snap['flags'][m[0]]['side']]}]"
             for m in METRIC_META if m[0] in snap.get("flags", {}) and snap.get(m[0]) is not None]
    if news.get("mode") == "calendar":
        headlines = [f"- {i['date']} {i['name']}" for i in news.get("items", [])[:25]]
        news_label = "未来事件日历"
    else:
        headlines = [f"- {i['title']} ({i['source']})" for i in news.get("items", [])[:25]]
        news_label = "过去 24 小时新闻标题"
    prompt = (
        "你是一名宏观与加密衍生品分析师。基于下面的指标读数与" + news_label + ",用中文写一份简报,分三段:"
        "一、数据读数与拥挤度判断(哪一侧拥挤、到什么程度、哪些指标在打架);"
        "二、" + ("前瞻:把未来事件串成备战要点,标出最可能点火的日子" if news.get("mode") == "calendar"
                 else "本期要闻脉络(把标题串成一两条主线,不逐条复述)") + ";"
        "三、巡检提示(接近极端需要警惕什么,尤其是反向风险)。"
        "克制、具体、不喊单,结尾注明不构成投资建议。\n\n"
        f"指标读数:\n" + "\n".join(lines) + "\n\n" + news_label + ":\n" + "\n".join(headlines)
    )
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6"),
                  "max_tokens": 1200,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=90,
        )
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json().get("content", []))
    except Exception as e:  # noqa: BLE001
        print(f"[warn] Claude 摘要失败(跳过): {e}")
        return None


def render_macro_section(macro):
    if not macro or not macro.get("components"):
        return ""
    mb = macro.get("macro_bias")
    bias_cn = "偏多" if (mb or 0) > 0 else ("偏空" if (mb or 0) < 0 else "中性")
    rows = []
    for k in ("ndx", "dxy", "vix", "oil"):
        c = macro["components"].get(k)
        if not c:
            continue
        dir_cn = "偏多" if c["direction"] > 0 else ("偏空" if c["direction"] < 0 else "中性")
        side = "s-short" if c["direction"] < 0 else ""   # 对BTC偏空=冷色蓝,偏多=暖色
        chip = f'<span class="chip l{c["level"]} {side}">{dir_cn}</span>'
        rows.append(f"<tr><td>{c['name']} <span class='use'>{c['symbol']}</span></td>"
                    f"<td class='num'>{c['value']:,} ({c['dod_pct']:+.2f}%)</td>"
                    f"<td>{chip}</td><td class='use'>{c['note']}</td></tr>")
    gold = macro.get("gold")
    if gold:
        gc = '<span class="chip l2">恐慌极端</span>' if macro.get("panic_extreme") else '<span class="chip">旁证</span>'
        rows.append(f"<tr><td>{gold['name']} <span class='use'>{gold['symbol']}</span></td>"
                    f"<td class='num'>{gold['value']:,} ({gold['dod_pct']:+.2f}%)</td>"
                    f"<td>{gc}</td><td class='use'>{gold['note']}</td></tr>")
    return (f"<h2>宏观维度 · 纳指 / DXY / VIX / 油价</h2>"
            f"<p class='use'>综合 macro_bias = <b>{mb}</b>({bias_cn})。2026 年 BTC = 高 beta 科技股,"
            f"与纳指相关 +0.7~0.8,天花板由股市定、回调时放大下跌——这是当前最强方向驱动。</p>"
            f"<table><tr><th>资产</th><th>现值(日变)</th><th>对 BTC</th><th>含义</th></tr>"
            f"{''.join(rows)}</table>")


def render_gaps_section(gaps):
    if not gaps or not gaps.get("gaps"):
        return ""
    rows = []
    for g in gaps["gaps"][:8]:
        rows.append(f"<tr><td class='num'>{g['low']:,.0f}–{g['high']:,.0f}</td>"
                    f"<td>{g['side']} {g['dist_pct']:+.1f}%</td>"
                    f"<td class='use'>{g['direction']}</td><td class='num'>{g['date']}</td></tr>")
    return (f"<h2>未回补 CME 缺口 · 已知磁吸</h2>"
            f"<p class='use'>来源 {gaps.get('source', '—')}(现价 {gaps.get('spot', '—'):,});"
            f"共 {gaps.get('unfilled_count', 0)} 个未回补,列出离现价最近 8 个。"
            f"多数缺口终被回补,未回补处是价格的磁吸目标。</p>"
            f"<table><tr><th>缺口区间</th><th>位置</th><th>方向</th><th>形成日</th></tr>"
            f"{''.join(rows)}</table>")


def render_report(kind, snap, news, summary, macro=None, gaps=None):
    now = datetime.now(JST)
    flags = snap.get("flags", {})
    bias = snap.get("bias_score", 0)
    rows = []
    for key, name, pattern, usage in METRIC_META:
        if snap.get(key) is None:
            continue
        f = flags.get(key, {"level": 0, "side": None, "note": ""})
        chip = (f'<span class="chip l{f["level"]} s-{f["side"] or "none"}">'
                f'{LEVEL_CN[f["level"]]}{(" · " + SIDE_CN[f["side"]]) if f["side"] else ""}</span>')
        rows.append(f"<tr><td>{name}</td><td class='num'>{fmt(key, snap[key], pattern)}</td>"
                    f"<td>{chip}</td><td class='use'>{usage}</td></tr>")

    if news.get("mode") == "calendar":
        wd_cn = "一二三四五六日"
        ev_rows = []
        for ev in news.get("items", []):
            try:
                wd = "周" + wd_cn[date.fromisoformat(ev["date"]).weekday()]
            except (ValueError, KeyError):
                wd = ""
            ev_rows.append(f"<tr><td class='num'>{ev['date']}</td><td>{wd}</td>"
                           f"<td>{ev['name']}</td><td class='use'>{ev['source']}</td></tr>")
        sec_title = f"未来 {news.get('window_days', '?')} 天关键日历"
        news_html = ("<table><tr><th>日期</th><th></th><th>事件</th><th>来源</th></tr>"
                     + "".join(ev_rows) + "</table>") if ev_rows else \
            "<p class='use'>窗口内没有已登记的事件,检查 data/calendar.json 是否需要补充。</p>"
    else:
        sec_title = "过去 24 小时要闻"
        news_li = "".join(
            f"<li><a href='{i['link']}'>{i['title']}</a> <span class='src'>{i['source']} · {i['published'][5:16]}</span></li>"
            for i in news.get("items", [])[:40]) or "<li class='use'>窗口内未抓到条目</li>"
        news_html = f"<ul class='news'>{news_li}</ul>"

    summary_html = f"<h2>简评</h2><div class='summary'>{summary}</div>" if summary else ""
    macro_html = render_macro_section(macro)
    gaps_html = render_gaps_section(gaps)
    needle = max(2, min(98, (bias + 100) / 2))

    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{KIND_CN[kind]} · {now:%Y-%m-%d}</title><style>{CSS}</style></head><body><div class="wrap">
<h1>偏见雷达 · {KIND_CN[kind]}</h1>
<div class="sub">{now:%Y-%m-%d %H:%M} JST · 数据时间 {snap.get('ts','—')} · <a href="../dashboard.html">实时面板</a> · <a href="../index.html">全部报告</a></div>
<h2>共识拥挤度</h2>
<div class="gauge"><div class="needle" style="left:{needle}%"></div></div>
<div class="glabel"><span>空头侧拥挤 −100</span><span class="num">读数 {bias:+d}</span><span>+100 多头侧拥挤</span></div>
<p class="use">聚合各指标的方向与极端等级而成。两端都不是顺势信号——是"该侧已饱和、警惕反转"的警报。</p>
<h2>巡检清单</h2>
<table><tr><th>指标</th><th>读数</th><th>状态</th><th>用法</th></tr>{''.join(rows)}</table>
{macro_html}
{gaps_html}
{summary_html}
<h2>{sec_title}</h2>{news_html}
<div class="foot">数据:Binance / Deribit 公开端点;清算与链上请看 Coinglass 与 CryptoQuant。
本页为个人研究工具,不构成投资建议。指标状态判定:{list(flags.values())[0]['note'] if flags else '—'}。</div>
</div></body></html>"""


def rebuild_index():
    rep_dir = os.path.join(DOCS, "reports")
    files = sorted((f for f in os.listdir(rep_dir) if f.endswith(".html")), reverse=True)
    groups = {"daily": [], "weekly": [], "monthly": []}
    for f in files:
        m = re.match(r"(daily|weekly|monthly)-(.+)\.html", f)
        if m:
            groups[m.group(1)].append((m.group(2), f))
    sections = "".join(
        f"<h2>{KIND_CN[k]}</h2><ul>" +
        "".join(f"<li><a href='reports/{fn}'>{date}</a></li>" for date, fn in v[:62]) + "</ul>"
        for k, v in groups.items() if v)
    html = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>偏见雷达</title><style>{CSS}</style></head><body><div class="wrap">
<h1>偏见雷达</h1><div class="sub">BTC 衍生品共识拥挤度 · 自动报告归档</div>
<p><a href="dashboard.html">→ 打开实时面板</a></p>{sections or '<p class="use">报告将在第一次 Actions 运行后出现。</p>'}
<div class="foot">由 GitHub Actions 自动生成。</div></div></body></html>"""
    with open(os.path.join(DOCS, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)


def main():
    kind = os.environ.get("REPORT_KIND", "daily")
    with open(os.path.join(ROOT, "data", "latest.json"), encoding="utf-8") as f:
        snap = json.load(f)
    news_path = os.path.join(ROOT, "data", "news.json")
    news = {}
    if os.path.exists(news_path):
        with open(news_path, encoding="utf-8") as f:
            news = json.load(f)

    def load_opt(fn):
        p = os.path.join(ROOT, "data", fn)
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:  # noqa: BLE001
                print(f"[warn] {fn} 读取失败(报告里跳过该段): {e}")
        return None

    macro = load_opt("macro.json")
    gaps = load_opt("cme_gaps.json")

    summary = claude_summary(snap, news)
    html = render_report(kind, snap, news, summary, macro, gaps)
    date = datetime.now(JST).strftime("%Y-%m-%d")
    out = os.path.join(DOCS, "reports", f"{kind}-{date}.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)

    os.makedirs(os.path.join(DOCS, "data"), exist_ok=True)
    shutil.copy(os.path.join(ROOT, "data", "latest.json"),
                os.path.join(DOCS, "data", "latest.json"))
    for fn in ("mstr.json", "mstr_status.json", "levels.json", "levels_status.json",
               "macro.json", "cme_gaps.json", "calendar.json", "news.json"):
        src = os.path.join(ROOT, "data", fn)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(DOCS, "data", fn))
    rebuild_index()
    print(f"[ok] 报告已生成: {out}")


if __name__ == "__main__":
    main()
