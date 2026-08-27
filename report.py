"""读取本地采样数据,生成自包含的 HTML 审计报告。

报告不引用任何外部 CSS/JS(可离线打开、可发给别人),配色为暗色主题。
"""
import html
import re
import sys
import webbrowser
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import db

MAX_SAMPLE_SPAN = 180   # 单个样本最长延伸 180s,吸收关机/休眠造成的空洞
TOP_TITLES = 25

BROWSER_SUFFIX = re.compile(
    r"\s+-\s+(Google Chrome|Microsoft Edge|Mozilla Firefox|Chromium|Brave|Opera)$"
)

NAME_MAP = {
    "chrome.exe": "Chrome", "msedge.exe": "Microsoft Edge", "firefox.exe": "Firefox",
    "brave.exe": "Brave", "opera.exe": "Opera",
    "explorer.exe": "文件资源管理器",
    "code.exe": "VS Code", "devenv.exe": "Visual Studio",
    "pycharm64.exe": "PyCharm", "idea64.exe": "IntelliJ IDEA",
    "clion64.exe": "CLion", "goland64.exe": "GoLand", "webstorm64.exe": "WebStorm",
    "wechat.exe": "微信", "weixin.exe": "微信", "qq.exe": "QQ",
    "dingtalk.exe": "钉钉", "feishu.exe": "飞书", "lark.exe": "飞书",
    "wps.exe": "WPS 文字", "et.exe": "WPS 表格", "wpp.exe": "WPS 演示",
    "cmd.exe": "命令提示符", "powershell.exe": "PowerShell", "pwsh.exe": "PowerShell",
    "windowsterminal.exe": "Windows Terminal", "conhost.exe": "控制台",
    "notepad.exe": "记事本", "notepad++.exe": "Notepad++",
    "steam.exe": "Steam", "spotify.exe": "Spotify",
    "cloudmusic.exe": "网易云音乐", "potplayer64.exe": "PotPlayer", "vlc.exe": "VLC",
    "snipaste.exe": "Snipaste", "everything.exe": "Everything",
    "mumunxdevice.exe": "MuMu 模拟器",
}


def app_name(exe: str) -> str:
    if exe in NAME_MAP:
        return NAME_MAP[exe]
    return exe.rsplit(".", 1)[0].replace("_", " ").capitalize() if "." in exe else exe


def clean_title(title: str) -> str:
    return BROWSER_SUFFIX.sub("", title).strip()


def fmt_seconds(sec: float) -> str:
    sec = int(sec)
    if sec < 60:
        return f"{sec} 秒"
    if sec < 3600:
        return f"{sec // 60} 分钟"
    h, m = divmod(sec, 3600)
    return f"{h} 小时 {m // 60:02d} 分"


def aggregate(rows, days: int):
    """把样本还原成时间跨度并按日聚合。

    每个样本的有效期到下一个样本出现为止(封顶 MAX_SAMPLE_SPAN),
    focused=0 的样本计入"离开"。
    """
    def date_of(ts):
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")

    start_date = (datetime.now() - timedelta(days=days - 1)).date()
    all_dates = [(start_date + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]

    per_day = defaultdict(lambda: {"total": 0, "away": 0, "switch": 0, "apps": Counter()})
    heat = defaultdict(float)          # (日期, 小时) -> 专注秒数
    titles = Counter()                 # (应用, 标题) -> 专注秒数
    prev_key = None

    for i, (ts, exe, title, focused) in enumerate(rows):
        nxt = rows[i + 1][0] if i + 1 < len(rows) else ts + HEARTBEAT_FALLBACK
        span = min(nxt - ts, MAX_SAMPLE_SPAN)
        if span <= 0:
            continue
        dstr, day_stat = date_of(ts), per_day[date_of(ts)]
        if focused:
            day_stat["total"] += span
            day_stat["apps"][exe] += span
            heat[(dstr, datetime.fromtimestamp(ts).hour)] += span
            titles[(app_name(exe), clean_title(title) or f"[{app_name(exe)}]")] += span
            key = (exe, title)
            if prev_key is not None and key != prev_key:
                day_stat["switch"] += 1
            prev_key = key
        else:
            day_stat["away"] += span
            prev_key = None

    return all_dates, per_day, heat, titles


HEARTBEAT_FALLBACK = 60


def render_html(all_dates, per_day, heat, titles, days: int) -> str:
    e = lambda s: html.escape(str(s))
    total_focus = sum(d["total"] for d in per_day.values())
    total_away = sum(d["away"] for d in per_day.values())
    total_switch = sum(d["switch"] for d in per_day.values())
    active_days = sum(1 for d in all_dates if d in per_day and per_day[d]["total"] > 0) or 1
    daily_avg = total_focus / active_days

    app_total = Counter()
    for d in per_day.values():
        for exe, sec in d["apps"].items():
            app_total[exe] += sec
    top_apps = app_total.most_common(15)
    max_app = top_apps[0][1] if top_apps else 1

    busy_hour = max(range(24), key=lambda h: sum(sec for (_, hh), sec in heat.items() if hh == h)) \
        if heat else "-"

    fav_app_name = app_name(top_apps[0][0]) if top_apps else "—"

    # 每日概览表
    trend_rows = []
    for d in all_dates:
        s = per_day.get(d)
        if not s:
            trend_rows.append(f"<tr class=muted><td>{d}</td><td colspan=4>无记录</td></tr>")
            continue
        wd = "周" + "一二三四五六日"[datetime.strptime(d, "%Y-%m-%d").weekday()]
        best = max(s["apps"].items(), key=lambda kv: kv[1]) if s["apps"] else ("—", 0)
        trend_rows.append(
            f"<tr><td>{d} {wd}</td><td>{fmt_seconds(s['total'])}</td>"
            f"<td>{fmt_seconds(s['away'])}</td><td>{s['switch']} 次</td>"
            f"<td>{e(app_name(best[0]))}</td></tr>"
        )

    # 热力图:行=日期,列=0~23 时
    max_heat = max(heat.values(), default=1)
    hour_head = "".join(f"<div class='hc'>{h:02d}</div>" for h in range(24))
    heat_rows = []
    for d in all_dates:
        cells = []
        for h in range(24):
            sec = heat.get((d, h), 0)
            alpha = round(min(1.0, sec / max_heat) * 0.92 + (0.08 if sec else 0.02), 3)
            tip = f"{d} {h:02d}:00 – {fmt_seconds(sec)}"
            style = f"--a:{alpha}" if sec else ""
            cells.append(f"<span class='cell' style='{style}' title='{tip}'></span>")
        wd = "一二三四五六日"[datetime.strptime(d, "%Y-%m-%d").weekday()]
        heat_rows.append(f"<div class=hrow><i>{d[5:]} 周{wd}</i>{''.join(cells)}</div>")

    # 应用排行条形图
    bars = []
    for exe, sec in top_apps:
        w = round(sec / max_app * 100, 1)
        pct = round(sec / total_focus * 100, 1) if total_focus else 0
        bars.append(
            f"<div class=bar><label>{e(app_name(exe))}"
            f"<small>{pct}%</small></label>"
            f"<div class=track><b style='width:{w}%'></b></div>"
            f"<em>{fmt_seconds(sec)}</em></div>"
        )

    # 高频窗口标题
    title_items = []
    for (app, t), sec in titles.most_common(TOP_TITLES):
        short = t if len(t) <= 90 else t[:88] + "…"
        title_items.append(
            f"<li><span class=badge>{e(app)}</span>{e(short)}<em>{fmt_seconds(sec)}</em></li>"
        )
    titles_html = (
        "<ol class=titles>" + "".join(title_items) + "</ol>"
        if title_items else "<p class=empty>暂无数据</p>"
    )

    return f"""<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>屏幕时间审计报告</title>
<style>
:root{{--bg:#0f1117;--card:#171a23;--line:#262b38;--tx:#e6e9f2;--mut:#8a93a8;--ac:#6366f1;--ok:#34d399}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--tx);font:15px/1.65 "Segoe UI","Microsoft YaHei",system-ui,sans-serif;padding:36px 20px}}
.wrap{{max-width:1080px;margin:0 auto}}
h1{{font-size:26px;font-weight:700;letter-spacing:.5px}}
.sub{{color:var(--mut);margin:6px 0 28px;font-size:14px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px;margin-bottom:34px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px}}
.card b{{display:block;font-size:22px;margin-top:4px}}
.card span{{color:var(--mut);font-size:13px}}
section{{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:24px;margin-bottom:26px}}
h2{{font-size:17px;margin-bottom:16px;color:var(--tx)}}
table{{width:100%;border-collapse:collapse;font-size:14px}}
th,td{{padding:9px 12px;text-align:left;border-bottom:1px solid var(--line)}}
th{{color:var(--mut);font-weight:600;font-size:13px}}
tr.muted td{{color:var(--mut)}}
.hgrid{{overflow-x:auto}}
.hhead,.hrow{{display:grid;grid-template-columns:96px repeat(24,21px);gap:3px;align-items:center;margin-bottom:3px;min-width:max-content}}
.hhead i{{visibility:hidden}} .hc{{font-size:10px;color:var(--mut);text-align:center}}
.hrow i{{font-style:normal;font-size:12px;color:var(--mut);white-space:nowrap}}
.cell{{width:21px;height:19px;border-radius:4px;background:rgba(99,102,241,var(--a,.05));cursor:default}}
.bar{{display:grid;grid-template-columns:160px 1fr 110px;gap:12px;align-items:center;margin-bottom:11px}}
.bar label{{font-size:14px;text-align:right;color:var(--tx)}}
.bar label small{{color:var(--mut);margin-left:5px;font-size:12px}}
.track{{background:#20242f;height:14px;border-radius:7px;overflow:hidden}}
.track b{{display:block;height:100%;background:linear-gradient(90deg,#6366f1,#8b5cf6);border-radius:7px}}
.bar em{{font-style:normal;color:var(--mut);font-size:13px}}
ol.titles{{list-style:none}} ol.titles li{{padding:8px 0;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:10px;font-size:14px}}
ol.titles em{{margin-left:auto;font-style:normal;color:var(--mut);white-space:nowrap}}
.badge{{background:#222839;color:#aab4cf;border-radius:6px;padding:2px 8px;font-size:12px;white-space:nowrap}}
.empty{{color:var(--mut)}}
footer{{text-align:center;color:var(--mut);font-size:13px;margin-top:30px}}
</style>
</head>
<body><div class="wrap">
<h1>🖥️ 屏幕时间审计报告</h1>
<p class="sub">{all_dates[0]} ~ {all_dates[-1]} · 共 {days} 天 · 数据仅存于本机</p>

<div class="cards">
<div class="card"><span>总专注时长</span><b>{fmt_seconds(total_focus)}</b></div>
<div class="card"><span>日均专注</span><b>{fmt_seconds(daily_avg)}</b></div>
<div class="card"><span>离开/闲置合计</span><b>{fmt_seconds(total_away)}</b></div>
<div class="card"><span>窗口切换总数</span><b>{total_switch:,} 次</b></div>
<div class="card"><span>最常用应用</span><b>{e(fav_app_name)}</b></div>
<div class="card"><span>最活跃时段</span><b>{busy_hour if isinstance(busy_hour,str) else f"{busy_hour}:00 前后"}</b></div>
</div>

<section><h2>📅 每日概览</h2>
<table><tr><th>日期</th><th>专注</th><th>离开</th><th>窗口切换</th><th>当日主力应用</th></tr>
{''.join(trend_rows)}
</table></section>

<section><h2>🔥 专注热力图(颜色越深越专注)</h2>
<div class="hgrid">
<div class="hhead"><i>.</i>{hour_head}</div>
{''.join(heat_rows)}
</div></section>

<section><h2>📊 应用排行</h2>
{''.join(bars) if bars else '<p class=empty>暂无数据</p>'}</section>

<section><h2>🪟 高频窗口标题 TOP{TOP_TITLES}</h2>
{titles_html}</section>

<footer>由 screentime 在本地生成 · 无任何数据上传</footer>
</div></body></html>"""


def generate(days: int = 7, open_browser: bool = True) -> Path:
    rows = db.load_samples(days)
    out_dir = (db.APP_DIR if getattr(sys, "frozen", False)
               else Path(__file__).resolve().parent) / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "report.html"
    if not rows:
        out.write_text(
            "<!doctype html><meta charset=utf-8><body style='font-family:sans-serif;"
            "background:#0f1117;color:#e6e9f2;display:grid;place-items:center;height:100vh'>"
            "<p>还没有数据。先用 <code>screentime start</code>(或 <code>python screentime.py start</code>)开始记录。</p>",
            encoding="utf-8",
        )
        return out

    all_dates, per_day, heat, titles = aggregate(rows, days)
    out.write_text(render_html(all_dates, per_day, heat, titles, days), encoding="utf-8")
    if open_browser:
        try:
            webbrowser.open(out.as_uri())
        except Exception:
            pass
    return out
