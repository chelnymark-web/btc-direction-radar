#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
双模式:
  daily   -> 过去 24 小时新闻(RSS 复盘)
  weekly  -> 未来 7 天关键日历(备战)
  monthly -> 未来 30 天关键日历(备战)

前瞻日历三个来源合并:
  1) data/calendar.json  手动维护的已核实日程(FOMC / CPI 等,日期来自官方发布)
  2) 代码计算的周期性事件:Deribit 月度期权到期(每月最后一个周五 08:00 UTC,
     3/6/9/12 月同时为季度合约到期)、非农(常规为每月第一个周五,以官方为准)
  3) ForexFactory 免费周历 JSON(高影响 USD 事件,抓不到就跳过,不影响主体)
"""
import calendar as pycal
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

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

FEEDS = [
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("The Block", "https://www.theblock.co/rss.xml"),
    ("美联储新闻稿", "https://www.federalreserve.gov/feeds/press_all.xml"),
]

FF_FEEDS = [
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
]


# ---------- daily: 过去 24 小时新闻 ----------

def fetch_news_24h():
    try:
        import feedparser  # 惰性导入:日历模式完全不需要它
    except ImportError:
        print("[warn] feedparser 未安装,日报新闻部分跳过", file=sys.stderr)
        return []
    cutoff = time.time() - 86400
    items = []
    for source, url in FEEDS:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries[:60]:
                ts = None
                for attr in ("published_parsed", "updated_parsed"):
                    if getattr(e, attr, None):
                        ts = time.mktime(getattr(e, attr))
                        break
                if ts is None or ts < cutoff:
                    continue
                items.append({
                    "source": source,
                    "title": e.get("title", "").strip(),
                    "link": e.get("link", ""),
                    "published": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="minutes"),
                })
        except Exception as err:  # noqa: BLE001
            print(f"[warn] {source} RSS 失败: {err}", file=sys.stderr)
    items.sort(key=lambda x: x["published"], reverse=True)
    return items[:60]


# ---------- weekly / monthly: 未来事件日历 ----------

def nth_weekday(year, month, weekday, n):
    """该月第 n 个星期 weekday(周一=0)的日期。"""
    days = [d for d in pycal.Calendar().itermonthdates(year, month)
            if d.month == month and d.weekday() == weekday]
    return days[n - 1] if len(days) >= n else None


def last_weekday(year, month, weekday):
    days = [d for d in pycal.Calendar().itermonthdates(year, month)
            if d.month == month and d.weekday() == weekday]
    return days[-1] if days else None


def computed_events(start, end):
    """在 [start, end] 窗口内生成周期性事件。"""
    events = []
    y, m = start.year, start.month
    for _ in range(3):  # 覆盖最多跨 3 个自然月的窗口
        last_fri = last_weekday(y, m, 4)
        if last_fri and start <= last_fri <= end:
            name = "Deribit 月度期权到期(08:00 UTC)"
            if m in (3, 6, 9, 12):
                name = "Deribit 季度+月度期权/合约到期(08:00 UTC)·大日子"
            events.append({"date": last_fri.isoformat(), "name": name, "source": "规则"})
        first_fri = nth_weekday(y, m, 4, 1)
        if first_fri and start <= first_fri <= end:
            events.append({"date": first_fri.isoformat(),
                           "name": "美国非农就业报告(常规日,以 BLS 官方为准)",
                           "source": "规则"})
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return events


def static_events(start, end):
    path = os.path.join(ROOT, "data", "calendar.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as err:  # noqa: BLE001
        print(f"[warn] calendar.json 解析失败: {err}", file=sys.stderr)
        return []
    out = []
    for ev in data.get("events", []):
        try:
            d = date.fromisoformat(ev["date"])
        except (KeyError, ValueError):
            continue
        if start <= d <= end:
            out.append({"date": ev["date"], "name": ev.get("name", "?"),
                        "source": ev.get("tag", "日历")})
    return out


def forexfactory_events(start, end):
    """高影响 USD 事件作补充;接口失效就安静跳过。"""
    out = []
    for url in FF_FEEDS:
        try:
            r = requests.get(url, timeout=20,
                             headers={"User-Agent": "bias-radar/1.0"})
            r.raise_for_status()
            for ev in r.json():
                if ev.get("country") != "USD" or ev.get("impact") != "High":
                    continue
                d = (ev.get("date") or "")[:10]
                try:
                    dd = date.fromisoformat(d)
                except ValueError:
                    continue
                if start <= dd <= end:
                    out.append({"date": d, "name": f"{ev.get('title','?')}(USD·高影响)",
                                "source": "ForexFactory"})
        except Exception as err:  # noqa: BLE001
            print(f"[warn] ForexFactory 失败(跳过): {err}", file=sys.stderr)
    return out


def upcoming(days):
    start = datetime.now(timezone.utc).date()
    end = start + timedelta(days=days)
    events = static_events(start, end) + computed_events(start, end)
    if days <= 10:  # 周报才用 FF 补充,月报窗口超出它的覆盖范围
        events += forexfactory_events(start, end)
    # 去重:同日同名只留一条
    seen, uniq = set(), []
    for ev in sorted(events, key=lambda x: x["date"]):
        key = (ev["date"], ev["name"])
        if key not in seen:
            seen.add(key)
            uniq.append(ev)
    return uniq


def main():
    kind = os.environ.get("REPORT_KIND", "daily")
    if kind == "daily":
        out = {"kind": kind, "mode": "news", "items": fetch_news_24h()}
    else:
        days = 7 if kind == "weekly" else 30
        out = {"kind": kind, "mode": "calendar", "window_days": days,
               "items": upcoming(days)}
    out["generated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(os.path.join(ROOT, "data", "news.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[ok] {kind}({out['mode']}): {len(out['items'])} 条")


if __name__ == "__main__":
    main()
