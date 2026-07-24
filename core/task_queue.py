"""
四级优先级任务队列 — 背压保护 + 降级机制
=======================================
防止高频任务 (日志、推理) 淹没 GUI 主线程.

优先级:
  CRITICAL (100) — 用户交互 (停止、窗口操作)
  HIGH (50)      — 实时状态 (日志显示、进度)
  NORMAL (0)     — 后台任务 (runtime 创建、模型加载)
  LOW (-50)      — 维护任务 (日志归档、缓存清理)

背压策略:
  - pending 任务超过 max_pending 时拒绝 LOW/NORMAL
  - backpressure 信号触发日志降级 (100% → 10%)
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from enum import IntEnum

from PyQt6.QtCore import QMutex, QObject, QThreadPool, pyqtSignal

logger = logging.getLogger("TaskQueue")


class Priority(IntEnum):
    """任务优先级"""

    CRITICAL = 100
    HIGH = 50
    NORMAL = 0
    LOW = -50


class TaskResult(QObject):
    """任务执行结果信号载体"""

    finished = pyqtSignal(object)  # result
    error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._has_result = False


class PriorityTaskQueue(QObject):
    """
    四级优先级队列 + 背压保护.

    信号:
      backpressure — pending 超限, 通知上游降速
      recovered    — pending 恢复正常, 可全速提交
    """

    backpressure = pyqtSignal()
    recovered = pyqtSignal()

    def __init__(self, max_pending: int = 200, parent=None):
        super().__init__(parent)
        self._pool = QThreadPool.globalInstance()
        self._max_pending = max_pending
        self._pending = 0
        self._mutex = QMutex()
        self._in_backpressure = False

        # 根据 CPU 核心数设置线程池大小
        import os

        cpu = os.cpu_count() or 4
        self._pool.setMaxThreadCount(max(2, cpu - 2))
        logger.info(f"线程池: {self._pool.maxThreadCount()} 线程 (CPU={cpu})")

    def submit(
        self,
        func: Callable,
        *args,
        priority: Priority = Priority.NORMAL,
        **kwargs,
    ) -> bool:
        """
        提交任务到线程池.

        Returns:
          True — 已接受
          False — 被背压拒绝 (priority < HIGH 且 pending 超限)
        """
        self._mutex.lock()
        try:
            if self._pending >= self._max_pending:
                if not self._in_backpressure:
                    self._in_backpressure = True
                    self.backpressure.emit()
                    logger.warning(f"背压触发: pending={self._pending} >= {self._max_pending}")

                if priority < Priority.HIGH:
                    return False  # 拒绝低优先任务

            self._pending += 1
        finally:
            self._mutex.unlock()

        result = TaskResult()

        def _execute():
            try:
                ret = func(*args, **kwargs)
                result.finished.emit(ret)
            except Exception as e:
                result.error.emit(str(e))
            finally:
                self._on_done()

        # QThreadPool.start 不支持 Python callable, 用 QRunnable 包装
        from PyQt6.QtCore import QRunnable

        class _Runnable(QRunnable):
            def __init__(self):
                super().__init__()
                self.setAutoDelete(True)

            def run(self):
                _execute()

        self._pool.start(_Runnable(), int(priority))
        return True

    def _on_done(self):
        """任务完成回调"""
        self._mutex.lock()
        try:
            self._pending -= 1

            if self._in_backpressure and self._pending < self._max_pending // 2:
                self._in_backpressure = False
                self.recovered.emit()
                logger.info(f"背压恢复: pending={self._pending}")
        finally:
            self._mutex.unlock()

    @property
    def pending(self) -> int:
        self._mutex.lock()
        try:
            return self._pending
        finally:
            self._mutex.unlock()

    @property
    def in_backpressure(self) -> bool:
        return self._in_backpressure

    def wait_done(self, timeout_ms: int = 5000) -> bool:
        """等待所有任务完成 (优雅退出)"""
        return self._pool.waitForDone(timeout_ms)
