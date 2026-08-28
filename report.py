"""读取本地采样数据,生成自包含的 HTML 审计报告。

分析管线:样本 -> 会话(相邻同窗口心跳合并) -> 按天/小时精确切分 -> 聚合渲染。
视觉:Apple HIG 风格 —— 毛玻璃卡片、大圆角、双主题(自动/浅/深)、
入场错峰渐显、条形生长与圆环描边动效;零外部资源,可离线分享。
"""
import html
import json
import re
import sys
import webbrowser
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import db

VERSION = "1.1.0"

MAX_SAMPLE_SPAN = 180   # 单个样本最长延伸 180s,吸收关机/休眠造成的空洞
HEARTBEAT_FALLBACK = 60
TOP_TITLES = 25
FOCUS_TARGET_HOURS = 6      # 专注分:日均专注时长的满分线
DEPTH_TARGET_MINUTES = 45   # 专注分:日均最长单段的满分线

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
    "excel.exe": "Excel", "winword.exe": "Word", "outlook.exe": "Outlook",
}

DEFAULT_CATEGORIES = {
    "工作": ["code.exe", "wps.exe", "et.exe", "wpp.exe", "excel.exe", "winword.exe",
             "outlook.exe", "devenv.exe", "pycharm64.exe", "idea64.exe", "clion64.exe",
             "goland64.exe", "webstorm64.exe", "windowsterminal.exe", "cmd.exe",
             "powershell.exe", "pwsh.exe", "conhost.exe", "notepad++.exe"],
    "浏览": ["chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe"],
    "沟通": ["wechat.exe", "weixin.exe", "qq.exe", "dingtalk.exe", "feishu.exe",
             "lark.exe", "telegram.exe", "slack.exe", "foxmail.exe"],
    "创作": ["photoshop.exe", "premiere.exe", "afterfx.exe", "blender.exe",
             "audacity.exe", "obs64.exe", "figma.exe", "notepad.exe"],
    "娱乐": ["steam.exe", "cloudmusic.exe", "spotify.exe", "potplayer64.exe",
             "vlc.exe", "mumunxdevice.exe", "dnplayer.exe"],
}
CAT_COLORS = {
    "工作": "#6366f1", "浏览": "#38bdf8", "沟通": "#34d399",
    "创作": "#f59e0b", "娱乐": "#f43f5e", "其他": "#8e96a8",
}
CAT_ORDER = ["工作", "浏览", "沟通", "创作", "娱乐", "其他"]


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


def load_categories():
    """返回 (exe->分类 映射, 有序分类名列表)。用户可用 data/categories.json 覆盖。"""
    path = db.DATA_DIR / "categories.json"
    if not path.exists():
        path.write_text(json.dumps(DEFAULT_CATEGORIES, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = DEFAULT_CATEGORIES
    mapping = {}
    cats = list(raw.keys()) + [c for c in ("其他",) if c not in raw]
    for cat, exes in raw.items():
        for exe in exes:
            mapping[exe.lower()] = cat
    return mapping, cats


def category_of(mapping, exe: str) -> str:
    return mapping.get(exe, "其他")


# ---------------------------------------------------------------- 会话重构

def reconstruct_sessions(rows, max_span: int = MAX_SAMPLE_SPAN):
    """样本 -> 会话 [(start, end, exe, title, focused)]。

    相邻且内容完全一致的心跳样本合并为一段;跨度封顶 max_span,
    关机/休眠留下的空洞不会被计成任何时长。
    """
    sessions = []
    for i, (ts, exe, title, focused) in enumerate(rows):
        nxt = rows[i + 1][0] if i + 1 < len(rows) else ts + HEARTBEAT_FALLBACK
        end = ts + min(nxt - ts, max_span)
        if end <= ts:
            continue
        if (sessions and sessions[-1][4] == focused and sessions[-1][2] == exe
                and sessions[-1][3] == title and sessions[-1][1] == ts):
            sessions[-1][1] = end
        else:
            sessions.append([ts, end, exe, title, focused])
    return sessions


def _clip_sessions(sessions, start_ts, end_ts=None):
    for s, e, exe, title, focused in sessions:
        s2, e2 = max(s, start_ts), (min(e, end_ts) if end_ts else e)
        if e2 > s2:
            yield [s2, e2, exe, title, focused]


# ---------------------------------------------------------------- 聚合

def aggregate(sessions, cat_map):
    """把会话按 天/小时 边界精确切分后聚合。跨界时长各归各的桶。"""
    def midnight(ts):
        d = datetime.fromtimestamp(ts)
        return (d + timedelta(days=1)).replace(hour=0, minute=0,
                                               second=0, microsecond=0).timestamp()

    def hour_mark(ts):
        d = datetime.fromtimestamp(ts)
        return (d + timedelta(hours=1)).replace(minute=0, second=0,
                                                microsecond=0).timestamp()

    agg = {
        "per_day": defaultdict(lambda: {"total": 0, "away": 0, "switch": 0,
                                        "longest": 0, "apps": Counter(), "cats": Counter()}),
        "heat": defaultdict(float),
        "titles": Counter(),
        "apps": Counter(),
        "cats": Counter(),
    }
    prev_key = None
    for s, e, exe, title, focused in sessions:
        while s < e:
            nb = min(e, midnight(s), hour_mark(s))
            span = nb - s
            d = datetime.fromtimestamp(s)
            dstr = d.strftime("%Y-%m-%d")
            stat = agg["per_day"][dstr]
            if focused:
                stat["total"] += span
                stat["apps"][exe] += span
                cat = category_of(cat_map, exe)
                stat["cats"][cat] += span
                agg["apps"][exe] += span
                agg["cats"][cat] += span
                agg["heat"][(dstr, d.hour)] += span
                agg["titles"][(app_name(exe), clean_title(title) or f"[{app_name(exe)}]")] += span
                if span > stat["longest"]:
                    stat["longest"] = span
                if prev_key is not None and (exe, title) != prev_key:
                    stat["switch"] += 1
                prev_key = (exe, title)
            else:
                stat["away"] += span
                prev_key = None
            s = nb
    return agg


def focus_score(daily_focus: float, daily_longest: float) -> tuple[int, int, int]:
    """专注分 = 时长达成 60% + 单段深度 40%,两项分别相对目标线取饱和值。"""
    time_part = round(60 * min(1.0, daily_focus / (FOCUS_TARGET_HOURS * 3600)))
    depth_part = round(40 * min(1.0, daily_longest / (DEPTH_TARGET_MINUTES * 60)))
    return min(99, time_part + depth_part) or (1 if daily_focus > 0 else 0), time_part, depth_part


def today_totals():
    """今日 (总专注秒, [(应用, 秒), ...]) —— 供 CLI 与浮窗共用。"""
    midnight = datetime.combine(datetime.today(), datetime.min.time()).timestamp()
    rows = [r for r in db.load_samples(2) if r[0] >= midnight - MAX_SAMPLE_SPAN]
    sessions = [s for s in reconstruct_sessions(rows) if s[1] > midnight]
    apps, total = Counter(), 0
    for s, e, exe, _t, focused in sessions:
        if not focused:
            continue
        span = e - max(s, midnight)
        apps[exe] += span
        total += span
    return total, apps


# ---------------------------------------------------------------- 报表渲染

STYLE = """
:root{
  --bg:#f5f5f7;--bg2:#ececf1;--card:rgba(255,255,255,.72);--glass:rgba(245,245,247,.65);
  --line:rgba(0,0,0,.08);--line2:rgba(0,0,0,.06);--tx:#1d1d1f;--mut:#6e6e73;
  --ac:#5e5ce6;--ac2:#04bdff;--track:#e8e8ed;--cellbase:rgba(94,92,230,.06);
  --shadow:0 10px 34px rgba(30,30,60,.10);--shadowh:0 18px 48px rgba(30,30,60,.16);
  --good:#34c759;--bad:#ff3b30}
@media (prefers-color-scheme: dark){
 :root:not([data-theme=light]){
  --bg:#0a0c12;--bg2:#10131c;--card:rgba(26,30,44,.62);--glass:rgba(16,19,28,.62);
  --line:rgba(255,255,255,.08);--line2:rgba(255,255,255,.05);--tx:#f2f2f7;--mut:#98989f;
  --ac:#7d7aff;--ac2:#64d2ff;--track:#232738;--cellbase:rgba(125,122,255,.06);
  --shadow:0 10px 34px rgba(0,0,0,.42);--shadowh:0 18px 48px rgba(0,0,0,.55);
  --good:#30d158;--bad:#ff453a}}
:root[data-theme=dark]{
  --bg:#0a0c12;--bg2:#10131c;--card:rgba(26,30,44,.62);--glass:rgba(16,19,28,.62);
  --line:rgba(255,255,255,.08);--line2:rgba(255,255,255,.05);--tx:#f2f2f7;--mut:#98989f;
  --ac:#7d7aff;--ac2:#64d2ff;--track:#232738;--cellbase:rgba(125,122,255,.06);
  --shadow:0 10px 34px rgba(0,0,0,.42);--shadowh:0 18px 48px rgba(0,0,0,.55);
  --good:#30d158;--bad:#ff453a}
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{
  background:
   radial-gradient(1100px 520px at 85% -8%, color-mix(in srgb, var(--ac) 16%, transparent), transparent 70%),
   radial-gradient(900px 500px at -10% 12%, color-mix(in srgb, var(--ac2) 12%, transparent), transparent 70%),
   linear-gradient(180deg, var(--bg) 0%, var(--bg2) 100%);
  background-attachment:fixed;
  color:var(--tx);
  font:15px/1.6 -apple-system,BlinkMacSystemFont,"SF Pro Display","SF Pro Text",
       "Segoe UI Variable Display","Segoe UI",system-ui,"Microsoft YaHei UI",sans-serif;
  padding:0 20px 48px;letter-spacing:.01em;
  -webkit-font-smoothing:antialiased}
::selection{background:color-mix(in srgb,var(--ac) 30%,transparent)}
.wrap{max-width:1120px;margin:0 auto}
.topbar{position:sticky;top:0;z-index:50;margin:0 -20px;padding:12px 24px;
 backdrop-filter:blur(22px) saturate(1.8);-webkit-backdrop-filter:blur(22px) saturate(1.8);
 background:var(--glass);border-bottom:1px solid var(--line2)}
.topbar .wrap{display:flex;align-items:center;gap:14px}
.brand{font-size:15px;font-weight:700;display:flex;align-items:center;gap:8px}
.brand .dot{width:10px;height:10px;border-radius:50%;
 background:linear-gradient(135deg,var(--ac),var(--ac2));box-shadow:0 0 12px var(--ac)}
.seg{margin-left:auto;display:flex;background:var(--card);border:1px solid var(--line);
 border-radius:999px;padding:3px;backdrop-filter:blur(10px)}
.seg button{border:0;background:transparent;color:var(--mut);font:inherit;font-size:12.5px;
 padding:5px 14px;border-radius:999px;cursor:pointer;transition:all .3s cubic-bezier(.25,.1,.25,1)}
.seg button.on{background:var(--ac);color:#fff;box-shadow:0 2px 10px color-mix(in srgb,var(--ac) 45%,transparent)}
h1{font-size:34px;font-weight:800;letter-spacing:-.022em;margin:34px 0 6px}
.sub{color:var(--mut);font-size:14.5px;margin-bottom:30px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:14px;margin-bottom:30px}
.card{background:var(--card);border:1px solid var(--line);border-radius:22px;padding:18px 20px;
 backdrop-filter:blur(24px) saturate(1.6);-webkit-backdrop-filter:blur(24px) saturate(1.6);
 box-shadow:var(--shadow);transition:transform .4s cubic-bezier(.22,1,.36,1),box-shadow .4s}
.card:hover{transform:translateY(-3px);box-shadow:var(--shadowh)}
.card>span{color:var(--mut);font-size:12.5px;font-weight:600;letter-spacing:.02em}
.card b{display:block;font-size:23px;font-weight:700;letter-spacing:-.02em;margin-top:6px;
 font-variant-numeric:tabular-nums}
.delta{font-size:12px;font-weight:700;margin-left:7px}
.sub2{display:block;margin-top:2px;font-size:12px;color:var(--mut)}
.ringwrap{display:flex;align-items:center;gap:14px;margin-top:6px}
.ringwrap b{font-size:30px}
.ring{width:74px;height:74px;transform:rotate(-90deg)}
.ring .tr{fill:none;stroke:var(--track);stroke-width:11}
.ring .prog{fill:none;stroke:url(#ringgrad);stroke-width:11;stroke-linecap:round;
 stroke-dasharray:327;stroke-dashoffset:327;
 transition:stroke-dashoffset 1.4s cubic-bezier(.22,1,.36,1) .25s}
.in .ring .prog{stroke-dashoffset:var(--off)}
section{background:var(--card);border:1px solid var(--line);border-radius:24px;
 padding:26px 28px;margin-bottom:24px;
 backdrop-filter:blur(24px) saturate(1.6);-webkit-backdrop-filter:blur(24px) saturate(1.6);
 box-shadow:var(--shadow)}
h2{font-size:17px;font-weight:700;letter-spacing:-.01em;margin-bottom:18px}
table{width:100%;border-collapse:collapse;font-size:14px}
th{color:var(--mut);font-weight:600;font-size:12.5px;text-align:left;
 padding:8px 12px;letter-spacing:.03em}
td{padding:10px 12px;border-top:1px solid var(--line2);font-variant-numeric:tabular-nums}
tbody tr{transition:background .25s}
tbody tr:hover{background:color-mix(in srgb,var(--ac) 6%,transparent)}
tr.muted td{color:var(--mut)}
.ov{display:flex;gap:38px;flex-wrap:wrap;align-items:center}
.donutwrap{display:flex;gap:22px;align-items:center}
.legend{display:flex;flex-direction:column;gap:9px}
.lg{display:flex;align-items:center;gap:9px;font-size:13.5px}
.lg i{width:11px;height:11px;border-radius:4px}
.lg small{color:var(--mut);margin-left:2px}
.daycats{flex:1;min-width:380px}
.daycats h3{font-size:13px;color:var(--mut);font-weight:600;margin-bottom:12px}
.stackrow{display:grid;grid-template-columns:104px 1fr 96px;gap:12px;
 align-items:center;margin-bottom:9px}
.stackrow i{font-style:normal;font-size:13px;color:var(--mut);text-align:right;white-space:nowrap}
.stackrow em{font-style:normal;color:var(--mut);font-size:13px;text-align:right;
 font-variant-numeric:tabular-nums}
.stack{display:flex;height:14px;border-radius:8px;overflow:hidden;background:var(--track)}
.stack b{display:block;height:100%;width:var(--w);
 animation:grow 1.1s cubic-bezier(.22,1,.36,1) both;animation-delay:var(--d)}
.stackrow .track{height:12px}
.hgrid{overflow-x:auto;padding:2px}
.hhead,.hrow{display:grid;grid-template-columns:96px repeat(24,21px);gap:3px;
 align-items:center;margin-bottom:3px;min-width:max-content}
.hhead i{visibility:hidden}
.hc{font-size:10px;color:var(--mut);text-align:center}
.hrow i{font-style:normal;font-size:12px;color:var(--mut);white-space:nowrap}
.cell{width:21px;height:19px;border-radius:5px;cursor:default;opacity:0;transform:scale(.6);
 background:rgba(99,102,241,.35);
 background:color-mix(in srgb,var(--ac) calc(var(--a,.05)*100%),transparent);
 animation:pop .5s cubic-bezier(.34,1.56,.64,1) both;animation-delay:var(--d);
 transition:filter .2s,transform .2s}
.cell:hover{filter:brightness(1.35);transform:scale(1.18)}
.bar{display:grid;grid-template-columns:160px 1fr 110px;gap:12px;
 align-items:center;margin-bottom:12px}
.bar label{font-size:14px;text-align:right;font-weight:500}
.bar label small{color:var(--mut);margin-left:6px;font-size:12px}
.track{background:var(--track);height:14px;border-radius:8px;overflow:hidden}
.track b{display:block;height:100%;width:var(--w);border-radius:8px;
 background:linear-gradient(90deg,var(--c1),var(--c2));
 animation:grow 1.1s cubic-bezier(.22,1,.36,1) both;animation-delay:var(--d)}
.bar em{font-style:normal;color:var(--mut);font-size:13px;font-variant-numeric:tabular-nums}
ol.titles{list-style:none}
ol.titles li{padding:9px 6px;border-radius:10px;display:flex;align-items:center;gap:10px;
 font-size:14px;transition:background .25s,transform .25s}
ol.titles li:hover{background:color-mix(in srgb,var(--ac) 7%,transparent);transform:translateX(4px)}
ol.titles em{margin-left:auto;font-style:normal;color:var(--mut);white-space:nowrap;
 font-variant-numeric:tabular-nums}
.badge{background:color-mix(in srgb,var(--ac) 14%,transparent);color:var(--ac);
 border-radius:7px;padding:2px 9px;font-size:12px;font-weight:600;white-space:nowrap}
.empty{color:var(--mut)}
footer{text-align:center;color:var(--mut);font-size:13px;margin-top:34px}
.reveal{opacity:0;transform:translateY(18px);
 transition:opacity .7s cubic-bezier(.25,.1,.25,1),transform .7s cubic-bezier(.25,.1,.25,1);
 transition-delay:var(--d,0ms)}
.reveal.in{opacity:1;transform:none}
@keyframes grow{from{width:0}}
@keyframes pop{from{opacity:0;transform:scale(.6)}to{opacity:1;transform:scale(1)}}
@media (prefers-reduced-motion: reduce){
 *,*::before,*::after{animation:none!important;transition:none!important}
 .reveal{opacity:1;transform:none}
 .cell{opacity:1;transform:none}
 .ring .prog{stroke-dashoffset:var(--off)}
}
"""

SCRIPT = """
(function(){
 var root=document.documentElement;
 var q=new URLSearchParams(location.search).get('theme');
 if(q==='light'||q==='dark')root.dataset.theme=q;
 var saved=null;try{saved=localStorage.getItem('st-theme')}catch(e){}
 if(!root.dataset.theme&&(saved==='light'||saved==='dark'))root.dataset.theme=saved;
 var seg=document.querySelectorAll('.seg button');
 for(var i=0;i<seg.length;i++){
  seg[i].addEventListener('click',function(){
   var m=this.dataset.mode;
   if(m==='auto')delete root.dataset.theme;else root.dataset.theme=m;
   try{m==='auto'?localStorage.removeItem('st-theme'):localStorage.setItem('st-theme',m)}catch(e){}
   Array.prototype.forEach.call(seg,function(b){b.classList.toggle('on',b===this)},this);
  });
 }
 var mode=root.dataset.theme||'auto';
 Array.prototype.forEach.call(seg,function(b){b.classList.toggle('on',b.dataset.mode===mode)});
 function fmt(s){s=Math.round(s);
  if(s<60)return s+' \\u79d2';
  if(s<3600)return Math.floor(s/60)+' \\u5206\\u949f';
  var h=Math.floor(s/3600),m=Math.floor(s%3600/60);
  return h+' \\u5c0f\\u65f6 '+String(m).padStart(2,'0')+' \\u5206';}
 function ease(t){return 1-Math.pow(1-t,3)}
 function countUp(el){
  var isNum=el.dataset.num!==undefined;
  var target=parseFloat(isNum?el.dataset.num:el.dataset.sec),t0=null,dur=1100;
  function step(ts){if(!t0)t0=ts;var p=Math.min(1,(ts-t0)/dur),v=Math.round(target*ease(p));
   el.textContent=isNum?v.toLocaleString('en-US'):fmt(v);
   if(p<1)requestAnimationFrame(step);}
  requestAnimationFrame(step);
 }
 var io=new IntersectionObserver(function(es){
  es.forEach(function(en){
   if(!en.isIntersecting)return;
   en.target.classList.add('in');
   Array.prototype.forEach.call(
    en.target.querySelectorAll('[data-sec],[data-num]'),countUp);
   var sc=en.target.querySelector('.scorenum');
   if(sc&&!sc.dataset.done){sc.dataset.done=1;
    var v=parseInt(sc.dataset.score,10),t0=null;
    (function step(ts){if(!t0)t0=ts;var p=Math.min(1,(ts-t0)/1200);
     sc.textContent=Math.round(v*ease(p));
     if(p<1)requestAnimationFrame(step);})(performance.now());}
   io.unobserve(en.target);
  });
 },{threshold:.15});
 Array.prototype.forEach.call(document.querySelectorAll('.reveal'),function(el){io.observe(el)});
})();
"""


def delta_badge(cur: float, prev: float, good_when_up: bool) -> str:
    if not prev:
        return ""
    pct = (cur - prev) / prev * 100
    if abs(pct) < 0.5:
        return "<span class='delta' style='color:var(--mut)'>· 持平</span>"
    up = pct > 0
    good = up == good_when_up
    return (f"<span class='delta' style='color:var(--{ 'good' if good else 'bad' })'>"
            f"{'▲' if up else '▼'} {abs(pct):.0f}%</span>")


def donut_svg(cats: Counter, total: float, cat_colors) -> str:
    if total <= 0:
        return "<svg width='150' height='150'></svg>"
    r, c = 56, 2 * 3.14159265 * 56
    acc, parts = 0.0, []
    ordered = [cat for cat in CAT_ORDER if cats.get(cat)] + \
              [cat for cat in cats if cat not in CAT_ORDER]
    for cat in ordered:
        frac = cats[cat] / total
        seg = (f"<circle cx='70' cy='70' r='{r}' fill='none' "
               f"stroke='{cat_colors.get(cat, '#94a3b8')}' stroke-width='22' "
               f"stroke-dasharray='{frac * c:.2f} {c:.2f}' "
               f"stroke-dashoffset='{-acc:.2f}'/>")
        parts.append(seg)
        acc += frac * c
    h = int(total // 3600)
    center = (f"<text x='70' y='66' text-anchor='middle' fill='var(--tx)' "
              f"font-size='22' font-weight='700'>{h}h</text>"
              f"<text x='70' y='86' text-anchor='middle' fill='var(--mut)' "
              f"font-size='11'>总专注</text>")
    return (f"<svg width='150' height='150' viewBox='0 0 140 140'>"
            f"<g transform='rotate(-90 70 70)'>{''.join(parts)}</g>{center}</svg>")


def render_html(all_dates, cur, prev_tot, days: int) -> str:
    e = lambda s: html.escape(str(s))
    per_day, heat, titles = cur["per_day"], cur["heat"], cur["titles"]
    cat_map, cats_order = load_categories()
    cat_colors = dict(CAT_COLORS)
    for c in cats_order:
        cat_colors.setdefault(c, "#94a3b8")

    total_focus = sum(d["total"] for d in per_day.values())
    total_away = sum(d["away"] for d in per_day.values())
    total_switch = sum(d["switch"] for d in per_day.values())
    active_days = sum(1 for d in all_dates if per_day.get(d, {}).get("total", 0) > 0) or 1
    daily_avg = total_focus / active_days
    daily_longest = sum(per_day[d]["longest"] for d in all_dates) / active_days
    score, s_time, s_depth = focus_score(daily_avg, daily_longest)

    top_apps = cur["apps"].most_common(15)
    max_app = top_apps[0][1] if top_apps else 1
    fav_app = app_name(top_apps[0][0]) if top_apps else "—"
    busy_hour = max(range(24), key=lambda h: sum(v for (_, hh), v in heat.items() if hh == h)) \
        if heat else "-"

    trend_rows = []
    stack_rows = []
    for d in all_dates:
        s = per_day.get(d)
        if not s or (not s["total"] and not s["away"]):
            trend_rows.append(f"<tr class=muted><td>{d}</td><td colspan=4>无记录</td></tr>")
            continue
        wd = "周" + "一二三四五六日"[datetime.strptime(d, "%Y-%m-%d").weekday()]
        best = max(s["apps"].items(), key=lambda kv: kv[1]) if s["apps"] else ("—", 0)
        trend_rows.append(
            f"<tr><td>{d} {wd}</td><td>{fmt_seconds(s['total'])}</td>"
            f"<td>{fmt_seconds(s['away'])}</td><td>{s['switch']} 次</td>"
            f"<td>{e(app_name(best[0]))}</td></tr>")
        if s["total"] > 0:
            segs, di = [], 0
            for cat in cats_order:
                sec = s["cats"].get(cat, 0)
                if sec <= 0:
                    continue
                w = sec / s["total"] * 100
                segs.append(f"<b style='--w:{w:.2f}%;--d:{di * 70}ms;"
                            f"background:{cat_colors[cat]}' "
                            f"title='{e(cat)} {fmt_seconds(sec)}'></b>")
                di += 1
            stack_rows.append(
                f"<div class=stackrow><i>{d[5:]} {wd}</i>"
                f"<div class=stack>{''.join(segs)}</div>"
                f"<em>{fmt_seconds(s['total'])}</em></div>")

    legend = "".join(
        f"<span class='lg'><i style='background:{cat_colors[cat]}'></i>"
        f"{e(cat)} <small>{cur['cats'].get(cat, 0) / total_focus * 100:.0f}%</small></span>"
        for cat in cats_order if cur["cats"].get(cat, 0) > 0) \
        if total_focus else "<span class=empty>暂无数据</span>"

    hour_head = "".join(f"<div class='hc'>{h:02d}</div>" for h in range(24))
    max_heat = max(heat.values(), default=1)
    heat_rows = []
    for d in all_dates:
        cells = []
        for h in range(24):
            sec = heat.get((d, h), 0)
            alpha = round(min(1.0, sec / max_heat) * 0.92 + (0.08 if sec else 0.02), 3)
            tip = f"{d} {h:02d}:00 – {fmt_seconds(sec)}"
            style = (f"--a:{alpha};--d:{h * 14 + 200}ms"
                     if sec else f"--d:{h * 14 + 200}ms")
            cells.append(f"<span class='cell' style='{style}' title='{tip}'></span>")
        wd = "一二三四五六日"[datetime.strptime(d, "%Y-%m-%d").weekday()]
        heat_rows.append(f"<div class=hrow><i>{d[5:]} 周{wd}</i>{''.join(cells)}</div>")

    bars = []
    for idx, (exe, sec) in enumerate(top_apps):
        w = round(sec / max_app * 100, 1)
        pct = round(sec / total_focus * 100, 1) if total_focus else 0
        cat = category_of(cat_map, exe)
        c1 = cat_colors.get(cat, "#6366f1")
        bars.append(
            f"<div class=bar><label>{e(app_name(exe))}<small>{pct}%</small></label>"
            f"<div class=track><b style='--w:{w}%;--d:{idx * 60}ms;--c1:{c1};--c2:#8b5cf6'>"
            f"</b></div><em>{fmt_seconds(sec)}</em></div>")

    title_items = []
    for (app, t), sec in titles.most_common(TOP_TITLES):
        short = t if len(t) <= 90 else t[:88] + "…"
        title_items.append(
            f"<li><span class=badge>{e(app)}</span>{e(short)}<em>{fmt_seconds(sec)}</em></li>")
    titles_html = ("<ol class=titles>" + "".join(title_items) + "</ol>"
                   if title_items else "<p class=empty>暂无数据</p>")

    cats_html = "".join(
        f"<div class='bar'><label>{e(cat)}<small>"
        f"{cur['cats'].get(cat, 0) / total_focus * 100:.0f}%</small></label>"
        f"<div class=track><b style='--w:{cur['cats'].get(cat, 0) / total_focus * 100:.1f}%;"
        f"--d:{i * 60}ms;--c1:{cat_colors[cat]};--c2:{cat_colors[cat]}'></b></div>"
        f"<em>{fmt_seconds(cur['cats'].get(cat, 0))}</em></div>"
        for i, cat in enumerate(cats_order) if cur["cats"].get(cat, 0) > 0) \
        if total_focus else "<p class=empty>暂无数据</p>"

    ring_off = round(327 * (1 - score / 100), 1)
    cards = f"""
<div class="cards">
 <div class="card reveal" style="--d:0ms"><span>总专注时长</span>
  <b><span data-sec="{total_focus:.0f}">{fmt_seconds(total_focus)}</span>{delta_badge(total_focus, prev_tot["total"], True)}</b></div>
 <div class="card reveal" style="--d:40ms"><span>日均专注</span>
  <b>{fmt_seconds(daily_avg)}</b></div>
 <div class="card reveal" style="--d:80ms"><span>🎯 专注分</span>
  <div class="ringwrap">
   <svg class="ring" viewBox="0 0 120 120">
    <defs><linearGradient id="ringgrad" x1="0" y1="0" x2="1" y2="1">
     <stop offset="0" stop-color="#5e5ce6"/><stop offset="1" stop-color="#64d2ff"/>
    </linearGradient></defs>
    <circle class="tr" cx="60" cy="60" r="52"/>
    <circle class="prog" cx="60" cy="60" r="52" style="--off:{ring_off}px"/>
   </svg>
   <div><b class="scorenum" data-score="{score}">0</b>
   <span class="sub2">/ 100 · 时长 {s_time} 深度 {s_depth}</span></div>
  </div></div>
 <div class="card reveal" style="--d:120ms"><span>最长单次专注(日均)</span>
  <b>{fmt_seconds(daily_longest)}</b></div>
 <div class="card reveal" style="--d:160ms"><span>离开/闲置合计</span>
  <b><span data-sec="{total_away:.0f}">{fmt_seconds(total_away)}</span>{delta_badge(total_away, prev_tot["away"], False)}</b></div>
 <div class="card reveal" style="--d:200ms"><span>窗口切换总数</span>
  <b><span data-num="{total_switch:.0f}">{total_switch:,}</span> 次{delta_badge(total_switch, prev_tot["switch"], False)}</b></div>
 <div class="card reveal" style="--d:240ms"><span>最常用应用</span><b>{e(fav_app)}</b></div>
 <div class="card reveal" style="--d:280ms"><span>最活跃时段</span>
  <b>{busy_hour if isinstance(busy_hour, str) else f"{busy_hour}:00 前后"}</b></div>
</div>"""

    return ("""<!doctype html>
<html lang="zh"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>屏幕时间审计报告</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='9' fill='%23171a23'/%3E%3Crect x='7' y='7' width='8' height='8' rx='2.5' fill='%236366f1'/%3E%3Crect x='17' y='7' width='8' height='8' rx='2.5' fill='%236366f1' opacity='.55'/%3E%3Crect x='7' y='17' width='8' height='8' rx='2.5' fill='%236366f1' opacity='.75'/%3E%3Crect x='17' y='17' width='8' height='8' rx='2.5' fill='%238b5cf6'/%3E%3C/svg%3E">
<style>""" + STYLE + """</style>
</head>
<body>
<header class="topbar"><div class="wrap">
 <span class="brand"><span class="dot"></span>ScreenTime</span>
 <div class="seg">
  <button data-mode="auto">自动</button><button data-mode="light">浅色</button>
  <button data-mode="dark">深色</button>
 </div>
</div></header>
<div class="wrap">
<h1>🖥️ 屏幕时间审计报告</h1>
<p class="sub">""" + f"{all_dates[0]} ~ {all_dates[-1]} · 共 {days} 天 · 对比前 {days} 天 · 数据仅存于本机" + """</p>
""" + cards + f"""
<section class="reveal"><h2>🧩 分类总览</h2>
<div class="ov">
<div class="donutwrap">{donut_svg(cur['cats'], total_focus, cat_colors)}<div class="legend">{legend}</div></div>
<div class="daycats"><h3>每日构成</h3>{''.join(stack_rows) or '<p class=empty>暂无数据</p>'}</div>
</div>
<div style="margin-top:22px">{cats_html}</div></section>

<section class="reveal"><h2>📅 每日概览</h2>
<table><thead><tr><th>日期</th><th>专注</th><th>离开</th><th>窗口切换</th><th>当日主力应用</th></tr></thead>
<tbody>
{''.join(trend_rows)}
</tbody></table></section>

<section class="reveal"><h2>🔥 专注热力图(颜色越深越专注)</h2>
<div class="hgrid">
<div class="hhead"><i>.</i>{hour_head}</div>
{''.join(heat_rows)}
</div></section>

<section class="reveal"><h2>📊 应用排行</h2>
{''.join(bars) if bars else '<p class=empty>暂无数据</p>'}</section>

<section class="reveal"><h2>🪟 高频窗口标题 TOP{TOP_TITLES}</h2>
{titles_html}</section>

<footer>由 screentime v{VERSION} 在本地生成 · 无任何数据上传<br>
<small>“离开”包含纯阅读等无键鼠输入场景 · 动效遵循系统“减弱动态效果”设置</small></footer>
</div>
<script>""" + SCRIPT + """</script>
</body></html>""")


def generate(days: int = 7, open_browser: bool = True) -> Path:
    rows = db.load_samples(days * 2)
    out_dir = (db.APP_DIR if getattr(sys, "frozen", False)
               else Path(__file__).resolve().parent) / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "report.html"
    if not rows:
        out.write_text(
            "<!doctype html><meta charset=utf-8><meta name=viewport "
            "content='width=device-width,initial-scale=1'>"
            "<body style='font-family:-apple-system,system-ui,sans-serif;"
            "background:#0a0c12;color:#f2f2f7;display:grid;place-items:center;height:100vh'>"
            "<p>还没有数据。先用 <code>screentime start</code>(或 <code>python screentime.py start</code>)开始记录。</p>",
            encoding="utf-8",
        )
        return out

    today0 = datetime.combine(datetime.today(), datetime.min.time())
    period_start = (today0 - timedelta(days=days - 1)).timestamp()
    prev_start = period_start - days * 86400
    sessions = reconstruct_sessions(rows)

    cat_map, _ = load_categories()
    cur = aggregate(list(_clip_sessions(sessions, period_start)), cat_map)
    prev = aggregate(list(_clip_sessions(sessions, prev_start, period_start)), cat_map)
    prev_tot = {
        "total": sum(d["total"] for d in prev["per_day"].values()),
        "away": sum(d["away"] for d in prev["per_day"].values()),
        "switch": sum(d["switch"] for d in prev["per_day"].values()),
    }

    all_dates = [(datetime.fromtimestamp(period_start) + timedelta(days=i)).strftime("%Y-%m-%d")
                 for i in range(days)]
    out.write_text(render_html(all_dates, cur, prev_tot, days), encoding="utf-8")
    if open_browser:
        try:
            webbrowser.open(out.as_uri())
        except Exception:
            pass
    return out


def export_csv(days: int, out_path: Path) -> Path:
    """导出重建后的会话为 CSV(UTF-8 BOM,Excel 直接打开不乱码)。"""
    import csv
    sessions = reconstruct_sessions(db.load_samples(days))
    with open(out_path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["start", "end", "seconds", "app", "title", "focused"])
        for s, e, exe, title, focused in sessions:
            w.writerow([datetime.fromtimestamp(s).strftime("%Y-%m-%d %H:%M:%S"),
                        datetime.fromtimestamp(e).strftime("%Y-%m-%d %H:%M:%S"),
                        int(e - s), exe, title, focused])
    return out_path
