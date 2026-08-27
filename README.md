# 屏幕时间被动审计器 (screentime)

一个纯本地的 Windows 屏幕时间统计工具:后台每隔几秒记录当前焦点窗口的进程名和标题,
汇总成分应用耗时、每日趋势、**专注热力图**、窗口切换分析等一份自包含 HTML 报告。

- **零依赖**:只用 Python 标准库 + ctypes 调 Windows API,`pip install` 都不需要
- **零上传**:数据全部存在本机 SQLite 里,没有任何网络请求
- **无感运行**:内存占用极小,空闲超过 2 分钟自动标记为"离开"

## 快速上手

> 不想装 Python?直接到 [Releases](https://github.com/uahz/screen-auditor/releases)
> 下载 `screentime.exe`,双击无法用的情况下在命令行运行 `screentime.exe start` 即可,
> 用法与下面的命令完全一致。

```bat
:: 1. 后台开始记录
python screentime.py start

:: 2. 用一会儿电脑……然后看看今天用了哪些应用
python screentime.py today

:: 3. 生成完整报告(会自动在浏览器打开)
python screentime.py report --days 7

:: 其他命令
python screentime.py status    :: 是否在运行 + 最后一条记录
python screentime.py stop      :: 停止记录
python screentime.py run       :: 前台调试模式,Ctrl+C 退出
```

## 数据与文件

| 路径 | 说明 |
| --- | --- |
| `data/screen_time.db` | SQLite 原始样本表(append-only) |
| `data/tracker.pid` / `tracker.log` | 后台进程标识与日志 |
| `reports/report.html` | 最近一次生成的报告 |

## 实现要点

- **采样策略**:每 3 秒读一次前台窗口,只有内容变化或每满 60 秒心跳才落库;
  锁屏 / UAC 安全桌面时读不到窗口,自动跳过该轮。
- **离开检测**:Win32 `GetLastInputInfo`,120 秒无键鼠输入即记为未专注。
- **报表口径**:每个样本的有效期延续到下一条样本(封顶 180s),
  所以关机、休眠留下的时间空洞不会被计成专注时长。

## 可以继续做的方向

- 开机自启(计划任务:`schtasks /create ... /sc onlogon`)
- 敏感窗口标题排除规则(如包含"密码""账单"的标题不入库或脱敏)
- 报告里增加域名级浏览器细分(UI Automation 读地址栏)
- 每周日晚定时自动生成报告并弹出总结
