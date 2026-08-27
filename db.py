"""SQLite 存储层。

样本采用 append-only 写入(ts 为主键),采集进程被强杀也不会破坏已有历史,
事后还可以用不同的口径重新分析原始数据。
"""
import sqlite3
import sys
import time
from pathlib import Path


def _app_dir() -> Path:
    # PyInstaller 冻结后模块位于临时解包目录,数据必须跟着 exe 本体走
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = _app_dir()
DATA_DIR = APP_DIR / "data"
DB_PATH = DATA_DIR / "screen_time.db"


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS samples (
            ts      INTEGER PRIMARY KEY,
            exe     TEXT    NOT NULL,
            title   TEXT    NOT NULL,
            focused INTEGER NOT NULL DEFAULT 1
        )"""
    )
    return conn


def insert_sample(conn: sqlite3.Connection, ts: int, exe: str, title: str, focused: int) -> None:
    conn.execute("INSERT OR REPLACE INTO samples VALUES (?, ?, ?, ?)", (ts, exe, title, focused))
    conn.commit()


def latest_sample(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT ts, exe, title, focused FROM samples ORDER BY ts DESC LIMIT 1"
    ).fetchone()


def load_samples(days: int):
    """返回最近 days 天的样本,按时间升序:[(ts, exe, title, focused), ...]"""
    since = int(time.time()) - days * 86400
    conn = connect()
    try:
        return conn.execute(
            "SELECT ts, exe, title, focused FROM samples WHERE ts >= ? ORDER BY ts",
            (since,),
        ).fetchall()
    finally:
        conn.close()
