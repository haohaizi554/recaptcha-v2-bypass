"""
持久化层 — QSettings 配置 + SQLite 历史记录
=============================================
所有磁盘 I/O 均在此模块封装, GUI 主线程只做信号收发.

QSettings  (轻量键值, ~0ms):
  - 窗口几何/状态
  - 上次选择的方案
  - API provider 偏好
  - 日志级别

SQLite     (结构化数据, <1ms 单条):
  - 运行历史 (方案, 成功/失败, 耗时, 时间戳)
  - 统计汇总 (成功率, 平均耗时, 按方案分组)

线程安全:
  - QSettings 天生线程安全 (Qt 内部加锁)
  - SQLite 使用 check_same_thread=False + 应用层 QMutex 保护写操作
  - 所有写操作通过 submit() 投递到工作线程, 主线程零阻塞
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import datetime
from typing import Any, Optional

from PyQt6.QtCore import QSettings, QMutex, QObject, pyqtSignal

logger = logging.getLogger("Persistence")

# 项目根目录
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# SQLite 数据库路径
_DB_PATH = os.path.join(_PROJECT_ROOT, "data", "recaptcha_history.db")


class PersistenceManager(QObject):
    """
    统一持久化管理器.

    信号:
      history_updated — 新记录写入完成, 通知 UI 刷新统计面板
    """

    history_updated = pyqtSignal()

    # QSettings 键名常量, 避免拼写错误
    KEY_WINDOW_GEOMETRY = "window/geometry"
    KEY_WINDOW_STATE = "window/state"
    KEY_SELECTED_METHOD = "ui/selectedMethod"
    KEY_API_PROVIDER = "ui/apiProvider"
    KEY_LOG_LEVEL = "ui/logLevel"
    KEY_LAST_RUN = "stats/lastRunTimestamp"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = QSettings("ApplyKitty", "reCAPTCHABypass", self)
        self._db_mutex = QMutex()
        self._db: Optional[sqlite3.Connection] = None
        self._init_db()

    # ========================================================
    # SQLite 初始化
    # ========================================================
    def _init_db(self):
        """初始化 SQLite 数据库 (构造时调用一次, <10ms)"""
        os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
        try:
            self._db = sqlite3.connect(
                _DB_PATH,
                check_same_thread=False,  # 允许跨线程访问 (由 _db_mutex 保护)
                isolation_level=None,     # 自动提交模式
            )
            self._db.execute("PRAGMA journal_mode=WAL")  # WAL 模式: 读写不互斥
            self._db.execute("PRAGMA synchronous=NORMAL")  # 平衡安全与性能
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS run_history (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    method      TEXT    NOT NULL,
                    success     INTEGER NOT NULL,  -- 0=失败, 1=成功
                    duration_s  REAL,              -- 耗时 (秒)
                    timestamp   TEXT    NOT NULL,  -- ISO 格式时间
                    detail      TEXT               -- 附加信息 (错误摘要等)
                )
            """)
            self._db.execute("""
                CREATE INDEX IF NOT EXISTS idx_history_ts
                ON run_history(timestamp DESC)
            """)
            self._db.execute("""
                CREATE INDEX IF NOT EXISTS idx_history_method
                ON run_history(method)
            """)
            logger.info(f"SQLite 初始化完成: {_DB_PATH}")
        except Exception as e:
            logger.error(f"SQLite 初始化失败: {e}", exc_info=True)
            self._db = None

    # ========================================================
    # QSettings: 键值读写 (主线程安全, ~0ms)
    # ========================================================
    def get(self, key: str, default: Any = None) -> Any:
        """读取 QSettings 值"""
        return self._settings.value(key, default)

    def set(self, key: str, value: Any):
        """写入 QSettings 值"""
        self._settings.setValue(key, value)

    def remove(self, key: str):
        """删除 QSettings 键"""
        self._settings.remove(key)

    # ========================================================
    # 窗口状态快捷方法
    # ========================================================
    def save_window_state(self, window):
        """保存窗口几何与状态"""
        self._settings.setValue(self.KEY_WINDOW_GEOMETRY, window.saveGeometry())
        self._settings.setValue(self.KEY_WINDOW_STATE, window.saveState())

    def restore_window_state(self, window) -> bool:
        """恢复窗口几何与状态, 返回是否成功恢复"""
        geometry = self._settings.value(self.KEY_WINDOW_GEOMETRY)
        if geometry is not None:
            try:
                window.restoreGeometry(geometry)
            except Exception:
                # 几何数据可能与当前窗口标志不兼容 (如从有边框切换到无边框), 安全跳过
                self._settings.remove(self.KEY_WINDOW_GEOMETRY)
        state = self._settings.value(self.KEY_WINDOW_STATE)
        if state is not None:
            try:
                window.restoreState(state)
            except Exception:
                self._settings.remove(self.KEY_WINDOW_STATE)
        return geometry is not None

    # ========================================================
    # SQLite: 运行历史 (写操作由调用方投递到工作线程)
    # ========================================================
    def add_record(
        self,
        method: str,
        success: bool,
        duration: float,
        detail: str = "",
    ):
        """
        写入一条运行记录.

        线程安全: 由 _db_mutex 保护, 可在工作线程调用.
        写入后发射 history_updated 信号 (如果从工作线程发射,
        Qt 会自动排队到主线程).
        """
        if self._db is None:
            return

        ts = datetime.now().isoformat(timespec="seconds")
        self._db_mutex.lock()
        try:
            self._db.execute(
                "INSERT INTO run_history (method, success, duration_s, timestamp, detail) "
                "VALUES (?, ?, ?, ?, ?)",
                (method, 1 if success else 0, round(duration, 2), ts, detail),
            )
        except Exception as e:
            logger.error(f"写入历史记录失败: {e}", exc_info=True)
        finally:
            self._db_mutex.unlock()

        self.set(self.KEY_LAST_RUN, ts)
        self.history_updated.emit()

    def get_recent_records(self, limit: int = 20) -> list[dict]:
        """
        获取最近的运行记录 (主线程调用, <1ms).

        Returns:
          [{"method": str, "success": bool, "duration": float, "timestamp": str, "detail": str}, ...]
        """
        if self._db is None:
            return []

        self._db_mutex.lock()
        try:
            cursor = self._db.execute(
                "SELECT method, success, duration_s, timestamp, detail "
                "FROM run_history ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            rows = cursor.fetchall()
        except Exception as e:
            logger.error(f"查询历史记录失败: {e}", exc_info=True)
            return []
        finally:
            self._db_mutex.unlock()

        return [
            {
                "method": row[0],
                "success": bool(row[1]),
                "duration": row[2] or 0.0,
                "timestamp": row[3],
                "detail": row[4] or "",
            }
            for row in rows
        ]

    def get_stats(self) -> dict:
        """
        获取统计汇总 (主线程调用, <2ms).

        Returns:
          {
            "total": int,
            "success": int,
            "fail": int,
            "success_rate": float,       # 0.0 ~ 1.0
            "avg_duration": float,       # 秒
            "by_method": {               # 按方案分组
              "audio": {"total": int, "success": int, "avg_duration": float},
              ...
            }
          }
        """
        if self._db is None:
            return self._empty_stats()

        self._db_mutex.lock()
        try:
            # 总体统计
            row = self._db.execute(
                "SELECT COUNT(*), SUM(success), AVG(duration_s) FROM run_history"
            ).fetchone()
            total = row[0] or 0
            success = row[1] or 0
            avg_dur = row[2] or 0.0

            # 按方案分组
            by_method: dict[str, dict] = {}
            for m_row in self._db.execute(
                "SELECT method, COUNT(*), SUM(success), AVG(duration_s) "
                "FROM run_history GROUP BY method"
            ).fetchall():
                m_name = m_row[0]
                by_method[m_name] = {
                    "total": m_row[1] or 0,
                    "success": m_row[2] or 0,
                    "avg_duration": round(m_row[3] or 0.0, 2),
                }
        except Exception as e:
            logger.error(f"查询统计失败: {e}", exc_info=True)
            return self._empty_stats()
        finally:
            self._db_mutex.unlock()

        return {
            "total": total,
            "success": success,
            "fail": total - success,
            "success_rate": (success / total) if total > 0 else 0.0,
            "avg_duration": round(avg_dur, 2),
            "by_method": by_method,
        }

    def clear_history(self):
        """清空所有历史记录"""
        if self._db is None:
            return
        self._db_mutex.lock()
        try:
            self._db.execute("DELETE FROM run_history")
            self._db.execute("VACUUM")  # 回收空间
        except Exception as e:
            logger.error(f"清空历史失败: {e}", exc_info=True)
        finally:
            self._db_mutex.unlock()
        self.history_updated.emit()

    # ========================================================
    # 清理
    # ========================================================
    def close(self):
        """关闭数据库连接 (应用退出时调用)"""
        self._db_mutex.lock()
        try:
            if self._db:
                self._db.close()
                self._db = None
                logger.info("SQLite 连接已关闭")
        except Exception as e:
            logger.error(f"关闭数据库失败: {e}", exc_info=True)
        finally:
            self._db_mutex.unlock()

    @staticmethod
    def _empty_stats() -> dict:
        return {
            "total": 0,
            "success": 0,
            "fail": 0,
            "success_rate": 0.0,
            "avg_duration": 0.0,
            "by_method": {},
        }
