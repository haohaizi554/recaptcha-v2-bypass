"""
无边框窗口 Win32 原生边框控制器
================================
为 FramelessWindowHint + WA_TranslucentBackground 窗口提供原生 Windows 窗框行为:
  - WM_NCCALCSIZE: 移除默认边框, 整个窗口区域为客户区
  - WM_NCHITTEST: 标题栏拖拽 (HTCAPTION) + 按钮区域穿透 (HTCLIENT)
  - WM_GETMINMAXINFO: 最大化时正确处理任务栏和自动隐藏任务栏
  - Qt startSystemResize: 边框缩放 (兼容 WA_TranslucentBackground 分层窗口)
  - DPI 感知的缩放边距

设计决策:
  不通过 SetWindowLongPtrW 修改窗口样式 (WS_POPUP → WS_CAPTION|WS_THICKFRAME),
  因为 WA_TranslucentBackground 创建的分层窗口 (WS_EX_LAYERED) 与样式修改冲突
  会导致原生崩溃。取而代之:
  - WM_NCCALCSIZE 返回 0 移除默认边框 (WS_POPUP 本身已无边框)
  - WM_NCHITTEST 返回 HTCAPTION 触发系统级拖拽 (支持 Aero Snap)
  - Qt startSystemResize() 处理边框缩放 (系统级, 同样平滑)

参考: window_chrome_controller.py (专业级双模式实现)
"""

from __future__ import annotations

import ctypes
import sys
import weakref
from collections.abc import Callable
from ctypes import wintypes

from PyQt6.QtCore import QAbstractNativeEventFilter, QEvent, QPoint, Qt, QTimer
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QApplication, QWidget


# ============================================================
# Win32 结构体定义
# ============================================================
class _MINMAXINFO(ctypes.Structure):
    _fields_ = [
        ("ptReserved", wintypes.POINT),
        ("ptMaxSize", wintypes.POINT),
        ("ptMaxPosition", wintypes.POINT),
        ("ptMinTrackSize", wintypes.POINT),
        ("ptMaxTrackSize", wintypes.POINT),
    ]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class _APPBARDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uCallbackMessage", wintypes.UINT),
        ("uEdge", wintypes.UINT),
        ("rc", wintypes.RECT),
        ("lParam", wintypes.LPARAM),
    ]


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hWnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


# ============================================================
# 原生事件过滤器
# ============================================================
class _ChromeNativeEventFilter(QAbstractNativeEventFilter):
    """将应用级 Windows 窗框事件转发给对应的 chrome controller。"""

    def __init__(self, controller: FramelessChromeController) -> None:
        super().__init__()
        self._controller_ref = weakref.ref(controller)

    def nativeEventFilter(self, event_type, message):  # noqa: N802
        controller = self._controller_ref()
        if controller is None:
            return False, 0
        hit_test = controller.handle_native_event(event_type, message)
        if hit_test is None:
            return False, 0
        return True, hit_test


# ============================================================
# 无边框窗口边框控制器
# ============================================================
class FramelessChromeController:
    """
    统一 Qt 顶层窗口的无边框 chrome、Win32 命中测试与系统缩放行为。

    核心优势 (相比纯 Qt mouseEvent 缩放):
      - Windows Aero Snap 支持 (HTCAPTION 触发系统拖拽, 拖到屏幕边缘自动半屏)
      - 原生平滑拖拽 (系统级 WM_NCLBUTTONDOWN, 无 Qt 事件延迟)
      - 最大化时不遮挡任务栏 (WM_GETMINMAXINFO 处理)
      - DPI 感知的缩放边距
      - 兼容 WA_TranslucentBackground (不修改窗口样式)

    缩放方案:
      使用 Qt startSystemResize() 而非 Win32 WM_NCHITTEST 返回缩放码,
      因为 WS_POPUP (FramelessWindowHint) 无 WS_THICKFRAME 样式位,
      Windows 不会处理 WM_NCHITTEST 返回的缩放码。
      startSystemResize() 是系统级 API, 同样平滑且支持 DPI 感知。
    """

    # Win32 常量
    FRAMELESS_RESIZE_BORDER_PX = 8
    AUTO_HIDE_TASKBAR_RESERVE_PX = 2
    MONITOR_DEFAULTTONEAREST = 2
    ABM_GETSTATE = 0x00000004
    ABM_GETTASKBARPOS = 0x00000005
    ABM_GETAUTOHIDEBAREX = 0x0000000B
    ABS_AUTOHIDE = 0x00000001
    ABE_LEFT = 0
    ABE_TOP = 1
    ABE_RIGHT = 2
    ABE_BOTTOM = 3
    SW_MAXIMIZE = 3
    SW_RESTORE = 9
    WM_MOVE = 0x0003
    WM_SIZE = 0x0005
    WM_GETMINMAXINFO = 0x0024
    WM_WINDOWPOSCHANGED = 0x0047
    WM_NCCALCSIZE = 0x0083
    WM_NCHITTEST = 0x0084
    WM_NCLBUTTONDBLCLK = 0x00A3
    HTCLIENT = 1
    HTCAPTION = 2
    HTLEFT = 10
    HTRIGHT = 11
    HTTOP = 12
    HTTOPLEFT = 13
    HTTOPRIGHT = 14
    HTBOTTOM = 15
    HTBOTTOMLEFT = 16
    HTBOTTOMRIGHT = 17
    SM_CXSIZEFRAME = 32
    SM_CYSIZEFRAME = 33
    SM_CXPADDEDBORDER = 92

    def __init__(
        self,
        host: QWidget,
        *,
        title_bar_getter: Callable[[], QWidget | None],
        is_effectively_maximized: Callable[[], bool] | None = None,
        toggle_maximized: Callable[[], None] | None = None,
        resizable: bool = True,
        minimizable: bool = True,
        maximizable: bool = True,
    ) -> None:
        self.host = host
        self._title_bar_getter = title_bar_getter
        self._is_effectively_maximized_callback = is_effectively_maximized
        self._toggle_maximized_callback = toggle_maximized
        self.resizable = bool(resizable)
        self.minimizable = bool(minimizable)
        self.maximizable = bool(maximizable)
        self._windows_hwnd: int | None = None
        self._native_filter: _ChromeNativeEventFilter | None = None
        self._filter_installed = False
        self._event_filter_installed = False
        self._window_ready = False  # showEvent 完成后才允许完整消息处理
        self._resize_cursor_active = False

    # ========================================================
    # 安装 / 卸载
    # ========================================================
    def install(self) -> None:
        """安装原生事件过滤器 + Qt 事件过滤器"""
        if not sys.platform.startswith("win"):
            # 非 Windows: 仅安装 Qt 事件过滤器用于缩放
            self._install_event_filter()
            return
        if self._filter_installed:
            return
        app = QApplication.instance()
        if app is None:
            return
        try:
            self._windows_hwnd = int(self.host.winId())
        except (RuntimeError, TypeError, ValueError):
            return
        self._native_filter = _ChromeNativeEventFilter(self)
        app.installNativeEventFilter(self._native_filter)
        self._filter_installed = True
        self._install_event_filter()

    def uninstall(self) -> None:
        """卸载所有过滤器"""
        self._remove_event_filter()
        if not self._filter_installed:
            return
        app = QApplication.instance()
        if app is not None and self._native_filter is not None:
            app.removeNativeEventFilter(self._native_filter)
        self._native_filter = None
        self._filter_installed = False

    def on_show_event(self) -> None:
        """在 showEvent 中调用: 标记窗口就绪 + 同步状态"""
        self._window_ready = True
        self.sync_title_bar_state()

    # ========================================================
    # Qt 事件过滤器 (边框缩放 + 光标管理)
    # ========================================================
    def _install_event_filter(self) -> None:
        if self._event_filter_installed:
            return
        app = QApplication.instance()
        if app is None:
            return
        app.installEventFilter(self.host)
        self._event_filter_installed = True

    def _remove_event_filter(self) -> None:
        if not self._event_filter_installed:
            return
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self.host)
        self._event_filter_installed = False
        self._set_resize_cursor(None)

    def event_filter(self, watched, event) -> bool:
        """Qt 事件过滤器: 监听鼠标移动更新光标, 监听鼠标按下启动缩放"""
        event_type = event.type()
        if not self._event_belongs_to_window(watched):
            return False
        if event_type in {QEvent.Type.MouseMove, QEvent.Type.HoverMove, QEvent.Type.Enter}:
            self._update_resize_cursor(self._event_global_pos(event))
        elif event_type in {QEvent.Type.Leave, QEvent.Type.WindowDeactivate}:
            self._set_resize_cursor(None)
        elif (
            event_type == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
            and self._start_system_resize(self._event_global_pos(event))
        ):
            event.accept()
            return True
        return False

    def mouse_press_event(self, event) -> bool:
        """处理鼠标按下: 如果在缩放边框区域则启动系统缩放"""
        if event.button() == Qt.MouseButton.LeftButton and self._start_system_resize(self._event_global_pos(event)):
            event.accept()
            return True
        return False

    # ========================================================
    # 缩放边框检测与系统缩放
    # ========================================================
    def _resize_edges_for_pos(self, global_pos: QPoint) -> Qt.Edge | None:
        """判断全局坐标是否在缩放边框区域, 返回对应的 Qt.Edge 组合"""
        if not self.resizable or self.host.isFullScreen() or self._is_maximized():
            return None
        frame = self.host.frameGeometry()
        border_x, border_y = self._resize_margins()
        hit_frame = frame.adjusted(-border_x, -border_y, border_x, border_y)
        if not hit_frame.contains(global_pos):
            return None
        x, y = global_pos.x(), global_pos.y()
        left = frame.left() - border_x <= x < frame.left() + border_x
        right = frame.right() - border_x < x <= frame.right() + border_x
        top = frame.top() - border_y <= y < frame.top() + border_y
        bottom = frame.bottom() - border_y < y <= frame.bottom() + border_y
        edge = None
        for enabled, qt_edge in (
            (left, Qt.Edge.LeftEdge),
            (right, Qt.Edge.RightEdge),
            (top, Qt.Edge.TopEdge),
            (bottom, Qt.Edge.BottomEdge),
        ):
            if enabled:
                edge = qt_edge if edge is None else edge | qt_edge
        return edge

    def _start_system_resize(self, global_pos: QPoint) -> bool:
        """启动系统级窗口缩放"""
        edge = self._resize_edges_for_pos(global_pos)
        if edge is None:
            return False
        handle = self.host.windowHandle()
        if handle is None:
            return False
        start_resize = getattr(handle, "startSystemResize", None)
        if not callable(start_resize):
            return False
        try:
            started = bool(start_resize(edge))
            if started:
                self._set_resize_cursor(None)
            return started
        except Exception:
            return False

    def _update_resize_cursor(self, global_pos: QPoint) -> None:
        """根据鼠标位置更新缩放光标"""
        edge = self._resize_edges_for_pos(global_pos)
        cursor = self._cursor_for_edge(edge)
        self._set_resize_cursor(cursor)

    @classmethod
    def _cursor_for_edge(cls, edge: Qt.Edge | None) -> Qt.CursorShape | None:
        if edge is None:
            return None
        left = bool(edge & Qt.Edge.LeftEdge)
        right = bool(edge & Qt.Edge.RightEdge)
        top = bool(edge & Qt.Edge.TopEdge)
        bottom = bool(edge & Qt.Edge.BottomEdge)
        if (top and left) or (bottom and right):
            return Qt.CursorShape.SizeFDiagCursor
        if (top and right) or (bottom and left):
            return Qt.CursorShape.SizeBDiagCursor
        if left or right:
            return Qt.CursorShape.SizeHorCursor
        if top or bottom:
            return Qt.CursorShape.SizeVerCursor
        return None

    def _set_resize_cursor(self, cursor: Qt.CursorShape | None) -> None:
        app = QApplication.instance()
        if app is None:
            return
        if cursor is None:
            if self._resize_cursor_active:
                app.restoreOverrideCursor()
                self._resize_cursor_active = False
            return
        qt_cursor = QCursor(cursor)
        if self._resize_cursor_active:
            app.changeOverrideCursor(qt_cursor)
        else:
            app.setOverrideCursor(qt_cursor)
            self._resize_cursor_active = True

    # ========================================================
    # 原生事件处理 (Win32 消息)
    # ========================================================
    def handle_native_event(self, _event_type, message) -> int | None:
        if not sys.platform.startswith("win"):
            return None
        try:
            msg = _MSG.from_address(int(message))
        except (AttributeError, TypeError, ValueError):
            return None
        if not self._msg_belongs_to_window(msg):
            return None
        msg_id = int(msg.message)

        # 窗口未就绪时仅处理 WM_NCCALCSIZE (移除边框), 其余消息交给默认处理
        # 避免 show() 期间 Qt 组件树未完全初始化时调用 mapTo 等方法导致原生崩溃
        if not self._window_ready:
            if msg_id == self.WM_NCCALCSIZE and bool(msg.wParam):
                return 0
            return None

        if msg_id == self.WM_NCCALCSIZE and bool(msg.wParam):
            # 返回 0 让 Windows 保留全部客户区域 (移除默认边框)
            return 0

        if msg_id == self.WM_GETMINMAXINFO:
            self._handle_min_max_info(msg)
            return 0

        if msg_id in {self.WM_MOVE, self.WM_SIZE, self.WM_WINDOWPOSCHANGED}:
            self.sync_title_bar_state()
            QTimer.singleShot(0, self.sync_title_bar_state)
            return None

        if msg_id == self.WM_NCHITTEST:
            return self._hit_test(msg)

        if msg_id == self.WM_NCLBUTTONDBLCLK and int(msg.wParam) == self.HTCAPTION:
            self.toggle_maximized()
            return 0

        return None

    # ========================================================
    # 命中测试
    # ========================================================
    def _hit_test(self, msg) -> int:
        """WM_NCHITTEST 命中测试: 标题栏拖拽区域返回 HTCAPTION, 其余返回 HTCLIENT

        注意: 不返回缩放码 (HTLEFT/HTTOP 等), 因为 WS_POPUP 无 WS_THICKFRAME,
        Windows 不会处理缩放码。缩放由 Qt startSystemResize() 处理。
        """
        pos = self._client_pos_from_lparam(msg)
        x, y = int(pos.x()), int(pos.y())

        title_bar = self._title_bar()
        if title_bar is not None and title_bar.isVisible():
            # 1. 检查标题栏按钮 — 返回 HTCLIENT 让 Qt 处理点击
            for attr in ("btn_minimize", "btn_maximize", "btn_close"):
                btn = getattr(title_bar, attr, None)
                if self._point_in_widget(btn, x, y):
                    return self.HTCLIENT
            # 2. 标题栏非按钮区域 — 返回 HTCAPTION 触发系统拖拽 (支持 Aero Snap)
            if self._point_in_widget(title_bar, x, y):
                return self.HTCAPTION

        return self.HTCLIENT

    # ========================================================
    # 最大化/最小化信息处理
    # ========================================================
    def _handle_min_max_info(self, msg) -> None:
        """WM_GETMINMAXINFO: 最大化时正确处理任务栏区域"""
        try:
            user32 = ctypes.windll.user32
            monitor = user32.MonitorFromWindow(msg.hWnd, self.MONITOR_DEFAULTTONEAREST)
            if not monitor:
                return
            info = _MONITORINFO()
            info.cbSize = ctypes.sizeof(_MONITORINFO)
            if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                return
            min_max = ctypes.cast(msg.lParam, ctypes.POINTER(_MINMAXINFO)).contents
            mon = info.rcMonitor
            work = info.rcWork
            # 检测自动隐藏任务栏
            taskbar_edge = self._auto_hide_taskbar_edge(mon)
            if taskbar_edge is not None:
                work_left, work_top, work_right, work_bottom = self._adjust_for_auto_hide(mon, work, taskbar_edge)
            else:
                work_left, work_top, work_right, work_bottom = (work.left, work.top, work.right, work.bottom)
            min_max.ptMaxPosition.x = work_left - mon.left
            min_max.ptMaxPosition.y = work_top - mon.top
            min_max.ptMaxSize.x = max(1, work_right - work_left)
            min_max.ptMaxSize.y = max(1, work_bottom - work_top)
            min_max.ptMaxTrackSize.x = min_max.ptMaxSize.x
            min_max.ptMaxTrackSize.y = min_max.ptMaxSize.y
            # 最小尺寸 (DPI 换算)
            min_size = self.host.minimumSize()
            dpr = self._qt_dpr()
            if min_size.width() > 0:
                min_max.ptMinTrackSize.x = min(
                    min_max.ptMaxSize.x,
                    max(min_max.ptMinTrackSize.x, round(min_size.width() * dpr)),
                )
            if min_size.height() > 0:
                min_max.ptMinTrackSize.y = min(
                    min_max.ptMaxSize.y,
                    max(min_max.ptMinTrackSize.y, round(min_size.height() * dpr)),
                )
        except Exception:
            pass

    # ========================================================
    # 状态同步
    # ========================================================
    def sync_title_bar_state(self) -> None:
        title_bar = self._title_bar()
        if title_bar is None:
            return
        sync = getattr(title_bar, "sync_window_state", None)
        if callable(sync):
            sync()

    def toggle_maximized(self) -> None:
        if not self.maximizable:
            return
        if self._toggle_maximized_callback is not None:
            self._toggle_maximized_callback()
            return
        should_max = not self._is_maximized()
        if sys.platform.startswith("win") and self._windows_hwnd is not None:
            try:
                ctypes.windll.user32.ShowWindow(
                    wintypes.HWND(self._windows_hwnd),
                    self.SW_MAXIMIZE if should_max else self.SW_RESTORE,
                )
                self.sync_title_bar_state()
                QTimer.singleShot(80, self.sync_title_bar_state)
                return
            except Exception:
                pass
        if should_max:
            self.host.showMaximized()
        else:
            self.host.showNormal()
        self.sync_title_bar_state()

    # ========================================================
    # 辅助方法
    # ========================================================
    def _resize_margins(self) -> tuple[int, int]:
        """DPI 感知的缩放边距 (物理像素)"""
        fallback = self.FRAMELESS_RESIZE_BORDER_PX
        if not sys.platform.startswith("win"):
            return fallback, fallback
        try:
            hwnd = int(self._windows_hwnd if self._windows_hwnd else self.host.winId())
            h_frame = max(
                fallback,
                self._system_metric(self.SM_CXSIZEFRAME, hwnd) + self._system_metric(self.SM_CXPADDEDBORDER, hwnd),
            )
            v_frame = max(
                fallback,
                self._system_metric(self.SM_CYSIZEFRAME, hwnd) + self._system_metric(self.SM_CXPADDEDBORDER, hwnd),
            )
            return h_frame, v_frame
        except Exception:
            return fallback, fallback

    def _title_bar(self):
        try:
            return self._title_bar_getter()
        except RuntimeError:
            return None

    def _is_maximized(self) -> bool:
        if self._is_effectively_maximized_callback is not None:
            return bool(self._is_effectively_maximized_callback())
        if sys.platform.startswith("win") and self._windows_hwnd is not None:
            try:
                return bool(ctypes.windll.user32.IsZoomed(self._windows_hwnd))
            except Exception:
                pass
        return bool(self.host.windowState() & Qt.WindowState.WindowMaximized) or self.host.isMaximized()

    def _msg_belongs_to_window(self, msg) -> bool:
        try:
            hwnd = int(msg.hWnd)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return False
        return self._windows_hwnd is not None and hwnd == int(self._windows_hwnd)

    def _client_pos_from_lparam(self, msg) -> QPoint:
        point = wintypes.POINT(
            self._signed_word(int(msg.lParam)),
            self._signed_word(int(msg.lParam) >> 16),
        )
        try:
            ctypes.windll.user32.ScreenToClient(msg.hWnd, ctypes.byref(point))
        except Exception:
            pass
        return QPoint(int(point.x), int(point.y))

    def _qt_dpr(self) -> float:
        try:
            handle = self.host.windowHandle()
            if handle is not None:
                dpr = float(handle.devicePixelRatio())
                if dpr > 0:
                    return dpr
        except Exception:
            pass
        try:
            dpr = float(self.host.devicePixelRatioF())
            return dpr if dpr > 0 else 1.0
        except Exception:
            return 1.0

    def _point_in_widget(self, widget: QWidget | None, x: int, y: int) -> bool:
        """判断物理像素坐标 (x, y) 是否在 widget 内 (widget 坐标为逻辑像素)"""
        if widget is None or not widget.isVisible():
            return False
        dpr = self._qt_dpr()
        pos = widget.mapTo(self.host, QPoint(0, 0))
        left = round(pos.x() * dpr)
        top = round(pos.y() * dpr)
        right = round((pos.x() + widget.width()) * dpr)
        bottom = round((pos.y() + widget.height()) * dpr)
        return left <= x < right and top <= y < bottom

    def _system_metric(self, metric: int, hwnd) -> int:
        try:
            return int(ctypes.windll.user32.GetSystemMetricsForDpi(metric, self._window_dpi(hwnd)))
        except Exception:
            try:
                return int(ctypes.windll.user32.GetSystemMetrics(metric))
            except Exception:
                return 0

    def _window_dpi(self, hwnd) -> int:
        try:
            dpi = int(ctypes.windll.user32.GetDpiForWindow(hwnd))
            return dpi if dpi > 0 else 96
        except Exception:
            return 96

    def _auto_hide_taskbar_edge(self, monitor_rect) -> int | None:
        try:
            shell32 = ctypes.windll.shell32
            for edge in (self.ABE_BOTTOM, self.ABE_TOP, self.ABE_LEFT, self.ABE_RIGHT):
                data = _APPBARDATA()
                data.cbSize = ctypes.sizeof(_APPBARDATA)
                data.uEdge = edge
                data.rc.left = monitor_rect.left
                data.rc.top = monitor_rect.top
                data.rc.right = monitor_rect.right
                data.rc.bottom = monitor_rect.bottom
                if shell32.SHAppBarMessage(self.ABM_GETAUTOHIDEBAREX, ctypes.byref(data)):
                    return edge
        except Exception:
            pass
        return None

    def _adjust_for_auto_hide(self, mon, work, edge) -> tuple[int, int, int, int]:
        left, top, right, bottom = work.left, work.top, work.right, work.bottom
        reserve = self.AUTO_HIDE_TASKBAR_RESERVE_PX
        if edge == self.ABE_LEFT and left <= mon.left:
            left += reserve
        elif edge == self.ABE_TOP and top <= mon.top:
            top += reserve
        elif edge == self.ABE_RIGHT and right >= mon.right:
            right -= reserve
        elif edge == self.ABE_BOTTOM and bottom >= mon.bottom:
            bottom -= reserve
        return left, top, max(left + 1, right), max(top + 1, bottom)

    def _event_belongs_to_window(self, watched: object) -> bool:
        widget = watched if isinstance(watched, QWidget) else None
        return widget is not None and widget.window() is self.host

    @staticmethod
    def _event_global_pos(event) -> QPoint:
        global_position = getattr(event, "globalPosition", None)
        if callable(global_position):
            return global_position().toPoint()
        global_pos = getattr(event, "globalPos", None)
        if callable(global_pos):
            return global_pos()
        return QCursor.pos()

    @staticmethod
    def _signed_word(value: int) -> int:
        value &= 0xFFFF
        return value - 0x10000 if value & 0x8000 else value
