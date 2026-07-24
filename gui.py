"""
reCAPTCHA v2 自动化绕过工具 - PyQt6 GUI 面板
=============================================

在保留原有五种方案、线程、日志、配置和 runtime 接口的前提下，
提供与批准稿一致的深色紫蓝卡片式桌面界面。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtCore import (
    QEvent,
    QPoint,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QThread,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontDatabase,
    QIcon,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
    QPixmapCache,
    QPolygonF,
    QRadialGradient,
    QResizeEvent,
    QTextCharFormat,
    QTextCursor,
)
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import config

# 优化模块: 模型预加载 / 优先级队列 / 持久化 / 窗口边框
from core.model_loader import ModelLoader
from core.persistence import PersistenceManager
from core.task_queue import Priority, PriorityTaskQueue
from core.window_chrome import FramelessChromeController
from solutions import SOLUTIONS as _SOLUTIONS

# ============================================================
# 全局日志配置
# ============================================================
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("GUI")


# ============================================================
# 方案定义 (从 solutions.py 导入, CLI/GUI 共享)
# ============================================================
# 统一方案定义在 solutions.py 中, 避免与 main.py 重复
# 字段: key, name, short_desc, detail, cost, status, status_color, icon, cli_icon, deps
SOLUTIONS = _SOLUTIONS


# ============================================================
# 矢量图标绘制
# ============================================================
_ICON_COLORS = {
    "shield": ("#8b5cf6", "#4f46e5"),
    "orb": ("#a855f7", "#5b21b6"),
    "music": ("#c4b5fd", "#7c3aed"),
    "key": ("#fde047", "#f59e0b"),
    "image": ("#60a5fa", "#2563eb"),
    "cookie": ("#f5c27a", "#b66a32"),
    "puzzle": ("#86efac", "#22c55e"),
    "cube": ("#a78bfa", "#4f46e5"),
    "terminal": ("#cbd5e1", "#64748b"),
    "trash": ("#cbd5e1", "#64748b"),
    "eye": ("#94a3b8", "#64748b"),
    "eye_off": ("#94a3b8", "#64748b"),
    "rocket": ("#ffffff", "#ddd6fe"),
    "check": ("#ffffff", "#c4b5fd"),
    "recaptcha": ("#6366f1", "#8b5cf6"),
    "minimize": ("#cbd5e1", "#cbd5e1"),
    "maximize": ("#cbd5e1", "#cbd5e1"),
    "restore": ("#cbd5e1", "#cbd5e1"),
    "close": ("#f8fafc", "#f8fafc"),
}


def _gradient(primary: QColor, secondary: QColor) -> QLinearGradient:
    gradient = QLinearGradient(4.0, 3.0, 28.0, 29.0)
    gradient.setColorAt(0.0, primary)
    gradient.setColorAt(1.0, secondary)
    return gradient


def _draw_vector_icon(
    painter: QPainter,
    kind: str,
    target: QRectF,
    primary: QColor | None = None,
    secondary: QColor | None = None,
    opacity: float = 1.0,
) -> None:
    """在给定矩形中绘制无外部资源的 32×32 逻辑矢量图标。"""
    default_primary, default_secondary = _ICON_COLORS.get(kind, ("#cbd5e1", "#64748b"))
    primary = primary or QColor(default_primary)
    secondary = secondary or QColor(default_secondary)

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setOpacity(max(0.0, min(opacity, 1.0)))
    painter.translate(target.left(), target.top())
    painter.scale(target.width() / 32.0, target.height() / 32.0)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(_gradient(primary, secondary)))

    if kind == "shield":
        path = QPainterPath()
        path.moveTo(16, 2.5)
        path.lineTo(28, 7.0)
        path.lineTo(26.2, 19.2)
        path.cubicTo(24.9, 25.0, 20.4, 28.6, 16.0, 30.5)
        path.cubicTo(11.6, 28.6, 7.1, 25.0, 5.8, 19.2)
        path.lineTo(4.0, 7.0)
        path.closeSubpath()
        painter.drawPath(path)
        pen = QPen(QColor("#f8fafc"), 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        inner = QPainterPath()
        inner.moveTo(10.2, 13.0)
        inner.lineTo(13.5, 16.3)
        inner.lineTo(16.0, 12.5)
        inner.lineTo(18.5, 16.3)
        inner.lineTo(21.8, 13.0)
        inner.lineTo(20.6, 20.8)
        inner.lineTo(11.4, 20.8)
        inner.closeSubpath()
        painter.drawPath(inner)

    elif kind == "orb":
        glow = QRadialGradient(QPointF(16.0, 16.0), 15.0)
        glow.setColorAt(0.0, QColor(216, 180, 254, 255))
        glow.setColorAt(0.38, primary)
        glow.setColorAt(1.0, QColor(primary.red(), primary.green(), primary.blue(), 0))
        painter.setBrush(QBrush(glow))
        painter.drawEllipse(QRectF(1.0, 1.0, 30.0, 30.0))
        painter.setBrush(QColor("#f8fafc"))
        painter.drawEllipse(QRectF(12.0, 12.0, 8.0, 8.0))

    elif kind == "music":
        pen = QPen(QBrush(_gradient(primary, secondary)), 3.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(QBrush(_gradient(primary, secondary)))
        painter.drawLine(QPointF(12.0, 8.0), QPointF(12.0, 22.0))
        painter.drawLine(QPointF(12.0, 8.0), QPointF(25.0, 5.5))
        painter.drawLine(QPointF(25.0, 5.5), QPointF(25.0, 18.8))
        painter.drawEllipse(QRectF(4.0, 19.0, 10.5, 8.5))
        painter.drawEllipse(QRectF(17.0, 16.0, 10.5, 8.5))

    elif kind == "key":
        painter.setBrush(QBrush(_gradient(primary, secondary)))
        painter.drawEllipse(QRectF(3.0, 3.0, 16.0, 16.0))
        painter.setBrush(QColor("#111827"))
        painter.drawEllipse(QRectF(8.0, 8.0, 6.0, 6.0))
        pen = QPen(QBrush(_gradient(primary, secondary)), 5.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(QPointF(16.0, 16.0), QPointF(28.0, 28.0))
        painter.drawLine(QPointF(22.5, 22.5), QPointF(26.0, 19.0))
        painter.drawLine(QPointF(25.0, 25.0), QPointF(28.5, 21.5))

    elif kind == "image":
        pen = QPen(QColor(primary), 1.4)
        painter.setPen(pen)
        painter.setBrush(QBrush(_gradient(primary, secondary)))
        painter.drawRoundedRect(QRectF(3.0, 4.0, 26.0, 24.0), 4.0, 4.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(235, 245, 255, 220))
        painter.drawEllipse(QRectF(20.0, 8.0, 5.0, 5.0))
        mountain = QPainterPath()
        mountain.moveTo(5.5, 25.5)
        mountain.lineTo(12.0, 16.0)
        mountain.lineTo(16.0, 20.5)
        mountain.lineTo(20.0, 15.0)
        mountain.lineTo(27.5, 25.5)
        mountain.closeSubpath()
        painter.setBrush(QColor(219, 234, 254, 235))
        painter.drawPath(mountain)

    elif kind == "cookie":
        painter.setBrush(QBrush(_gradient(primary, secondary)))
        painter.drawEllipse(QRectF(3.0, 3.0, 26.0, 26.0))
        painter.setBrush(QColor("#6f4026"))
        for x, y, r in ((9, 10, 2.2), (18, 8, 1.9), (14, 17, 2.0), (22, 21, 2.1), (8, 23, 1.7)):
            painter.drawEllipse(QRectF(x - r, y - r, r * 2, r * 2))
        painter.setBrush(QColor("#111827"))
        painter.drawEllipse(QRectF(23.0, 2.0, 7.0, 7.0))
        painter.drawEllipse(QRectF(26.0, 7.0, 6.0, 6.0))

    elif kind == "puzzle":
        path = QPainterPath()
        path.moveTo(5, 5)
        path.lineTo(12, 5)
        path.cubicTo(11.4, 2.4, 13.2, 0.8, 16, 0.8)
        path.cubicTo(18.8, 0.8, 20.6, 2.4, 20, 5)
        path.lineTo(27, 5)
        path.lineTo(27, 12)
        path.cubicTo(29.6, 11.4, 31.2, 13.2, 31.2, 16)
        path.cubicTo(31.2, 18.8, 29.6, 20.6, 27, 20)
        path.lineTo(27, 27)
        path.lineTo(20, 27)
        path.cubicTo(20.6, 24.4, 18.8, 22.8, 16, 22.8)
        path.cubicTo(13.2, 22.8, 11.4, 24.4, 12, 27)
        path.lineTo(5, 27)
        path.lineTo(5, 20)
        path.cubicTo(2.4, 20.6, 0.8, 18.8, 0.8, 16)
        path.cubicTo(0.8, 13.2, 2.4, 11.4, 5, 12)
        path.closeSubpath()
        painter.drawPath(path)

    elif kind == "cube":
        pen = QPen(QBrush(_gradient(primary, secondary)), 1.8)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(QColor(primary.red(), primary.green(), primary.blue(), 50))
        top = QPolygonF([QPointF(16, 3), QPointF(28, 10), QPointF(16, 17), QPointF(4, 10)])
        left = QPolygonF([QPointF(4, 10), QPointF(16, 17), QPointF(16, 30), QPointF(4, 23)])
        right = QPolygonF([QPointF(16, 17), QPointF(28, 10), QPointF(28, 23), QPointF(16, 30)])
        painter.drawPolygon(top)
        painter.drawPolygon(left)
        painter.drawPolygon(right)

    elif kind == "terminal":
        painter.setBrush(QBrush(_gradient(primary, secondary)))
        painter.drawRoundedRect(QRectF(2.0, 4.0, 28.0, 24.0), 5.0, 5.0)
        pen = QPen(QColor("#f8fafc"), 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(8, 11), QPointF(13, 16))
        painter.drawLine(QPointF(13, 16), QPointF(8, 21))
        painter.drawLine(QPointF(17, 21), QPointF(24, 21))

    elif kind == "trash":
        pen = QPen(primary, 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(8, 10, 16, 18), 2.2, 2.2)
        painter.drawLine(QPointF(6, 8), QPointF(26, 8))
        painter.drawLine(QPointF(12, 5), QPointF(20, 5))
        painter.drawLine(QPointF(13, 14), QPointF(13, 23))
        painter.drawLine(QPointF(19, 14), QPointF(19, 23))

    elif kind in {"eye", "eye_off"}:
        pen = QPen(primary, 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        eye = QPainterPath()
        eye.moveTo(3, 16)
        eye.cubicTo(8, 8, 24, 8, 29, 16)
        eye.cubicTo(24, 24, 8, 24, 3, 16)
        painter.drawPath(eye)
        painter.setBrush(primary)
        painter.drawEllipse(QRectF(12, 12, 8, 8))
        if kind == "eye_off":
            pen = QPen(QColor("#94a3b8"), 2.5)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(QPointF(5, 5), QPointF(27, 27))

    elif kind == "rocket":
        body = QPainterPath()
        body.moveTo(18.0, 4.0)
        body.cubicTo(23.0, 4.5, 27.5, 8.8, 28.0, 14.0)
        body.cubicTo(24.5, 18.6, 20.5, 22.2, 15.0, 24.0)
        body.lineTo(8.0, 17.0)
        body.cubicTo(9.8, 11.5, 13.4, 7.5, 18.0, 4.0)
        body.closeSubpath()
        painter.setBrush(QBrush(_gradient(primary, secondary)))
        painter.drawPath(body)
        painter.setBrush(QColor("#7c3aed"))
        painter.drawEllipse(QRectF(18.0, 9.0, 5.0, 5.0))
        painter.setBrush(QColor("#c4b5fd"))
        left_fin = QPolygonF([QPointF(9, 16), QPointF(4, 18), QPointF(8, 22), QPointF(13, 20)])
        right_fin = QPolygonF([QPointF(16, 23), QPointF(14, 29), QPointF(20, 25), QPointF(21, 20)])
        painter.drawPolygon(left_fin)
        painter.drawPolygon(right_fin)
        flame = QPainterPath()
        flame.moveTo(8, 22)
        flame.cubicTo(4, 23, 3, 26, 3, 29)
        flame.cubicTo(6, 29, 9, 28, 10, 24)
        flame.closeSubpath()
        painter.setBrush(QColor("#f0abfc"))
        painter.drawPath(flame)

    elif kind == "check":
        painter.setBrush(QBrush(_gradient(primary, secondary)))
        painter.drawEllipse(QRectF(2, 2, 28, 28))
        pen = QPen(QColor("#ffffff"), 3.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(QPointF(9, 16), QPointF(14, 21))
        painter.drawLine(QPointF(14, 21), QPointF(23, 11))

    elif kind == "recaptcha":
        pen = QPen(QBrush(_gradient(primary, secondary)), 4.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        arc1 = QPainterPath()
        arc1.arcMoveTo(QRectF(5, 5, 22, 22), 35)
        arc1.arcTo(QRectF(5, 5, 22, 22), 35, 165)
        painter.drawPath(arc1)
        arc2 = QPainterPath()
        arc2.arcMoveTo(QRectF(5, 5, 22, 22), 215)
        arc2.arcTo(QRectF(5, 5, 22, 22), 215, 165)
        painter.drawPath(arc2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(primary)
        painter.drawPolygon(QPolygonF([QPointF(7, 6), QPointF(14, 5), QPointF(10, 12)]))
        painter.setBrush(secondary)
        painter.drawPolygon(QPolygonF([QPointF(25, 26), QPointF(18, 27), QPointF(22, 20)]))

    elif kind == "minimize":
        pen = QPen(primary, 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(8, 18), QPointF(24, 18))

    elif kind == "maximize":
        pen = QPen(primary, 1.6)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(8, 8, 16, 16))

    elif kind == "restore":
        pen = QPen(primary, 1.6)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(10, 7, 14, 14))
        painter.drawRect(QRectF(7, 10, 14, 14))

    elif kind == "close":
        pen = QPen(primary, 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(9, 9), QPointF(23, 23))
        painter.drawLine(QPointF(23, 9), QPointF(9, 23))

    painter.restore()


def _icon_pixmap(kind: str, size: int, color: str | None = None) -> QPixmap:
    """带缓存的图标渲染, 避免重复绘制 (QPixmapCache 全局缓存)."""
    cache_key = f"icon:{kind}:{size}:{color or 'default'}"
    cached = QPixmapCache.find(cache_key)
    if cached is not None:
        return cached

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    primary = QColor(color) if color else None
    _draw_vector_icon(painter, kind, QRectF(0, 0, size, size), primary=primary)
    painter.end()

    QPixmapCache.insert(cache_key, pixmap)
    return pixmap


# ============================================================
# 样式表
# ============================================================
QSS = """
QMainWindow, QWidget#central {
    background: transparent;
}

QFrame#appShell {
    background: transparent;
    border: none;
}

QWidget#content {
    background: transparent;
    color: #e7ecf7;
    font-family: "Microsoft YaHei UI", "Segoe UI";
}

QWidget#titleBar {
    background: transparent;
    border: none;
}

QLabel#windowTitle {
    color: #dbe4f3;
    font-size: 12px;
    font-weight: 500;
}

QLabel#headerTitle {
    color: #f5f7ff;
    font-size: 27px;
    font-weight: 700;
    background: transparent;
}

QLabel#headerSubtitle {
    color: #8390a8;
    font-size: 12px;
    background: transparent;
}

QFrame#methodListPanel,
QFrame#configPanel,
QFrame#logPanel {
    background-color: rgba(10, 20, 39, 224);
    border: 1px solid rgba(112, 132, 170, 52);
    border-radius: 14px;
}

QLabel#sectionTitle {
    color: #e6ebf7;
    font-size: 14px;
    font-weight: 650;
    background: transparent;
}

QFrame#methodCard {
    background-color: rgba(15, 27, 49, 236);
    border: 1px solid rgba(111, 130, 166, 50);
    border-radius: 13px;
}

QFrame#methodCard[hovered="true"] {
    background-color: rgba(20, 34, 61, 244);
    border-color: rgba(130, 151, 192, 86);
}

QFrame#methodCard[selected="true"] {
    background-color: rgba(31, 27, 71, 246);
    border: 1px solid #8b5cf6;
}

QFrame#methodCard:disabled {
    background-color: rgba(13, 23, 42, 190);
    border-color: rgba(111, 130, 166, 28);
}

QFrame#iconWell {
    border: 1px solid rgba(148, 163, 184, 42);
    border-radius: 11px;
    background-color: rgba(30, 41, 74, 205);
}

QLabel#cardTitle {
    color: #f1f5ff;
    font-size: 15px;
    font-weight: 650;
    background: transparent;
}

QLabel#cardDesc {
    color: #9aa8c2;
    font-size: 11px;
    background: transparent;
}

QLabel#cardDetail {
    color: #718099;
    font-size: 10px;
    background: transparent;
}

QLabel#badge {
    font-size: 9px;
    font-weight: 650;
    padding: 3px 8px;
    border-radius: 5px;
}

QLabel#configTitle,
QLabel#logTitle {
    color: #eef2ff;
    font-size: 15px;
    font-weight: 650;
    background: transparent;
}

QLabel#configDetail {
    color: #96a3bc;
    font-size: 11px;
    line-height: 1.35;
    background: transparent;
}

QLabel#configLabel {
    color: #aab6ca;
    font-size: 11px;
    background: transparent;
}

QWidget#dynamicConfig {
    background: transparent;
}

QLineEdit {
    min-height: 38px;
    background-color: rgba(5, 10, 21, 232);
    border: 1px solid rgba(116, 131, 165, 98);
    border-radius: 8px;
    padding: 0px 12px;
    color: #e7ecf7;
    font-size: 11px;
    selection-background-color: #7c3aed;
}

QLineEdit:hover {
    border-color: rgba(139, 92, 246, 155);
}

QLineEdit:focus,
QLineEdit#apiKeyInput:focus {
    border: 1px solid #8b5cf6;
    background-color: rgba(10, 15, 31, 242);
}

QLineEdit#apiKeyInput {
    min-height: 42px;
    border: 1px solid #7c3aed;
    border-radius: 9px;
    padding-left: 14px;
    padding-right: 42px;
}

QRadioButton {
    color: #dde4f2;
    font-size: 11px;
    spacing: 8px;
    padding: 4px 0px;
    background: transparent;
}

QRadioButton::indicator {
    width: 17px;
    height: 17px;
    border-radius: 9px;
    border: 2px solid #53617b;
    background-color: #07101f;
}

QRadioButton::indicator:hover {
    border-color: #8b5cf6;
}

QRadioButton::indicator:checked {
    border: 2px solid #8b5cf6;
    background: qradialgradient(
        cx: 0.5, cy: 0.5, radius: 0.5,
        fx: 0.5, fy: 0.5,
        stop: 0.00 #ffffff,
        stop: 0.28 #ffffff,
        stop: 0.32 #8b5cf6,
        stop: 1.00 #6d28d9
    );
}

QPushButton#secondaryBtn {
    min-height: 32px;
    background-color: rgba(30, 41, 62, 220);
    color: #cbd5e1;
    border: 1px solid rgba(123, 139, 170, 62);
    border-radius: 7px;
    padding: 0px 13px;
    font-size: 10px;
}

QPushButton#secondaryBtn:hover {
    background-color: rgba(43, 56, 81, 235);
    border-color: rgba(139, 92, 246, 130);
    color: #f8fafc;
}

QPushButton#startBtn {
    background: transparent;
    border: none;
    color: #ffffff;
    font-size: 16px;
    font-weight: 700;
}

QPushButton#stopBtn {
    background-color: #b91c1c;
    color: #ffffff;
    border: 1px solid rgba(254, 202, 202, 90);
    border-radius: 12px;
    font-size: 15px;
    font-weight: 700;
}

QPushButton#stopBtn:hover {
    background-color: #dc2626;
}

QPushButton#stopBtn:pressed {
    background-color: #991b1b;
}

QPushButton#stopBtn:disabled,
QPushButton#startBtn:disabled {
    color: #64748b;
}

QPlainTextEdit#logOutput {
    background-color: rgba(3, 8, 18, 240);
    border: 1px solid rgba(99, 118, 153, 55);
    border-radius: 10px;
    color: #aab5c8;
    padding: 10px;
    font-size: 10px;
    selection-background-color: #4c1d95;
}

QLabel#statusLabel {
    color: #8492aa;
    font-size: 11px;
    background: transparent;
}

QLabel#statusRunning {
    color: #fbbf24;
    font-size: 11px;
    font-weight: 600;
    background: transparent;
}

QLabel#statusSuccess {
    color: #34d399;
    font-size: 11px;
    font-weight: 600;
    background: transparent;
}

QLabel#statusFailed {
    color: #f87171;
    font-size: 11px;
    font-weight: 600;
    background: transparent;
}

QProgressBar {
    background-color: rgba(52, 65, 89, 140);
    border: none;
    border-radius: 3px;
    min-height: 6px;
    max-height: 6px;
    text-align: center;
}

QProgressBar::chunk {
    background-color: #8b5cf6;
    border-radius: 3px;
}

QScrollArea {
    border: none;
    background: transparent;
}

QComboBox {
    min-height: 28px;
    background-color: rgba(15, 27, 49, 236);
    border: 1px solid rgba(111, 130, 166, 50);
    border-radius: 6px;
    padding: 0px 8px;
    color: #cbd5e1;
    font-size: 10px;
}

QComboBox:hover {
    border-color: rgba(139, 92, 246, 155);
}

QComboBox::drop-down {
    border: none;
    width: 18px;
}

QComboBox::down-arrow {
    image: none;
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    border-top: 4px solid #8492aa;
    width: 0;
    height: 0;
}

QComboBox QAbstractItemView {
    background-color: rgba(10, 20, 39, 250);
    border: 1px solid rgba(111, 130, 166, 80);
    border-radius: 6px;
    padding: 4px;
    color: #cbd5e1;
    selection-background-color: rgba(139, 92, 246, 80);
    outline: none;
}

QScrollBar:vertical {
    background: rgba(15, 23, 42, 120);
    width: 8px;
    border: none;
    border-radius: 4px;
}

QScrollBar::handle:vertical {
    background: rgba(100, 116, 139, 145);
    border-radius: 4px;
    min-height: 30px;
}

QScrollBar::handle:vertical:hover {
    background: rgba(139, 92, 246, 175);
}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {
    height: 0px;
}
"""


# ============================================================
# 表现层组件
# ============================================================
class VectorIcon(QWidget):
    """可缩放、无外部资源的矢量图标。"""

    def __init__(self, kind: str, size: int = 24, parent=None):
        super().__init__(parent)
        self.kind = kind
        self._icon_size = size
        self._primary: QColor | None = None
        self._secondary: QColor | None = None
        self._opacity = 1.0
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_colors(self, primary: str | QColor, secondary: str | QColor | None = None):
        self._primary = QColor(primary)
        self._secondary = QColor(secondary) if secondary is not None else None
        self.update()

    def set_icon_opacity(self, opacity: float):
        self._opacity = opacity
        self.update()

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        _draw_vector_icon(
            painter,
            self.kind,
            rect,
            primary=self._primary,
            secondary=self._secondary,
            opacity=self._opacity,
        )
        painter.end()


class IconButton(QWidget):
    """用于标题栏和输入框尾部的自绘图标按钮。

    使用 QWidget 而非 QAbstractButton, 避免 QAbstractButton 内部状态机
    在半透明分层窗口 (WA_TranslucentBackground) 中触发原生回调崩溃
    (STATUS_FATAL_USER_CALLBACK_EXCEPTION / 0xC000041D)。
    手动实现 clicked 信号和按下/悬停状态。
    """

    clicked = pyqtSignal()

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.setFixedSize(34, 34)
        self._hovered = False
        self._pressed = False

    def sizeHint(self) -> QSize:
        return QSize(34, 34)

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._pressed = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self._pressed:
            self._pressed = False
            self.update()
            if self.rect().contains(event.position().toPoint()):
                self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect())

        if self._hovered or self._pressed:
            if self.kind == "close":
                background = QColor(220, 38, 38, 225 if self._pressed else 190)
            else:
                background = QColor(148, 163, 184, 28 if self._pressed else 18)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(background)
            painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 6, 6)

        icon_side = 17.0 if self.kind in {"eye", "eye_off"} else 15.0
        icon_rect = QRectF(
            (rect.width() - icon_side) / 2.0,
            (rect.height() - icon_side) / 2.0,
            icon_side,
            icon_side,
        )
        opacity = 1.0 if self.isEnabled() else 0.35
        _draw_vector_icon(painter, self.kind, icon_rect, opacity=opacity)
        painter.end()


class AppShell(QFrame):
    """绘制圆角窗口壳体、深色渐变和顶部环境光。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("appShell")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        window = self.window()
        maximized = bool(window and window.isMaximized())
        radius = 0.0 if maximized else 14.0

        shell_path = QPainterPath()
        shell_path.addRoundedRect(rect, radius, radius)
        painter.setClipPath(shell_path)

        background = QLinearGradient(0, 0, 0, rect.height())
        background.setColorAt(0.0, QColor("#07101f"))
        background.setColorAt(0.42, QColor("#081222"))
        background.setColorAt(1.0, QColor("#060d19"))
        painter.fillPath(shell_path, QBrush(background))

        purple_glow = QRadialGradient(QPointF(rect.width() * 0.82, 112.0), rect.width() * 0.35)
        purple_glow.setColorAt(0.0, QColor(126, 34, 206, 78))
        purple_glow.setColorAt(0.43, QColor(79, 70, 229, 34))
        purple_glow.setColorAt(1.0, QColor(30, 41, 59, 0))
        painter.fillRect(rect, QBrush(purple_glow))

        blue_glow = QRadialGradient(QPointF(rect.width() * 0.62, 42.0), rect.width() * 0.32)
        blue_glow.setColorAt(0.0, QColor(37, 99, 235, 40))
        blue_glow.setColorAt(1.0, QColor(15, 23, 42, 0))
        painter.fillRect(rect, QBrush(blue_glow))

        painter.setPen(QPen(QColor(122, 140, 177, 42), 1.0))
        painter.drawLine(QPointF(0, 38.0), QPointF(rect.width(), 38.0))

        # 少量固定星点让顶部环境光更接近批准稿，同时保持确定性。
        painter.setPen(Qt.PenStyle.NoPen)
        for x, y, alpha, diameter in (
            (0.61, 72, 110, 1.4),
            (0.73, 108, 70, 1.1),
            (0.87, 78, 140, 1.8),
            (0.92, 136, 75, 1.0),
        ):
            painter.setBrush(QColor(196, 181, 253, alpha))
            painter.drawEllipse(QRectF(rect.width() * x, float(y), diameter, diameter))

        painter.setClipping(False)
        painter.setPen(QPen(QColor(111, 129, 164, 65), 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, radius, radius)
        painter.end()


class TitleBarButton(QWidget):
    """标题栏窗口控制按钮: 原生 Windows 11 风格全矩形渲染。

    使用 QWidget 而非 QAbstractButton, 避免 QAbstractButton 内部状态机
    在半透明分层窗口 (WA_TranslucentBackground) 中触发原生回调崩溃
    (STATUS_FATAL_USER_CALLBACK_EXCEPTION / 0xC000041D)。
    手动实现 clicked 信号和按下/悬停状态。
    """

    clicked = pyqtSignal()

    BUTTON_WIDTH = 42
    BUTTON_HEIGHT = 34

    def __init__(self, kind: str, tooltip: str, parent=None):
        super().__init__(parent)
        self.kind = kind  # "minimize" | "maximize" | "restore" | "close"
        self.setToolTip(tooltip)
        self.setFixedSize(self.BUTTON_WIDTH, self.BUTTON_HEIGHT)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setMouseTracking(True)
        self._hovered = False
        self._pressed = False

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._pressed = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self._pressed:
            self._pressed = False
            self.update()
            if self.rect().contains(event.position().toPoint()):
                self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        rect = QRectF(self.rect())

        # 全矩形背景 (Windows 11 原生风格)
        bg = None
        if self._hovered or self._pressed:
            if self.kind == "close":
                bg = QColor(232, 17, 35, 255) if self._pressed else QColor(196, 43, 28, 220)
            else:
                bg = QColor(148, 163, 184, 30) if self._pressed else QColor(148, 163, 184, 18)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bg)
            painter.drawRect(rect)

        # 绘制图标符号 (纯线条, 不依赖字体)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        is_close_hover = self.kind == "close" and self._hovered
        color = QColor("#ffffff") if is_close_hover else QColor("#dbe4f3")
        pen = QPen(color, 1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        # int 转换必须: rect.center() 返回 QPointF (float), 传给 drawRect(int,int,int,int)
        # 时混合 float/int 参数会导致 PyQt6 6.11.0 原生层 0xC0000409 崩溃
        cx, cy = int(rect.center().x()), int(rect.center().y())

        if self.kind == "minimize":
            painter.drawLine(cx - 5, cy + 4, cx + 5, cy + 4)
        elif self.kind in ("maximize", "restore"):
            if self.kind == "restore":
                # 还原图标: 两个重叠方框
                painter.drawRect(cx - 3, cy - 6, 9, 9)
                fill = bg if bg is not None else QColor("#081222")
                painter.fillRect(cx - 6, cy - 3, 9, 9, fill)
                painter.drawRect(cx - 6, cy - 3, 9, 9)
            else:
                # 最大化图标: 单个方框
                painter.drawRect(cx - 5, cy - 5, 10, 10)
        elif self.kind == "close":
            painter.drawLine(cx - 5, cy - 5, cx + 5, cy + 5)
            painter.drawLine(cx + 5, cy - 5, cx - 5, cy + 5)
        painter.end()


class TitleBar(QWidget):
    """无边框窗口标题栏，保留拖动与窗口控制能力。

    优化: 使用 Win32 原生命中测试 (WM_NCHITTEST) 替代 Qt mouseEvent,
    支持 Windows Aero Snap 和原生平滑缩放。
    按钮属性名 (btn_minimize/btn_maximize/btn_close) 与 FramelessChromeController 对齐。
    """

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self._window = window
        self.setObjectName("titleBar")
        self.setFixedHeight(38)
        self.setMouseTracking(True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 0, 0)
        layout.setSpacing(8)

        app_icon = VectorIcon("shield", 21, self)
        layout.addWidget(app_icon, 0, Qt.AlignmentFlag.AlignVCenter)

        title = QLabel("reCAPTCHA v2 自动化绕过工具", self)
        title.setObjectName("windowTitle")
        layout.addWidget(title, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addStretch(1)

        # 按钮属性名与 FramelessChromeController._hit_test 对齐
        self.btn_minimize = TitleBarButton("minimize", "最小化", self)
        self.btn_maximize = TitleBarButton("maximize", "最大化", self)
        self.btn_close = TitleBarButton("close", "关闭", self)
        for button in (self.btn_minimize, self.btn_maximize, self.btn_close):
            layout.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)

        self.btn_minimize.clicked.connect(self._window.showMinimized)
        self.btn_maximize.clicked.connect(self._toggle_maximized)
        self.btn_close.clicked.connect(self._window.close)

    def _toggle_maximized(self):
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()
        self.sync_window_state()

    def sync_window_state(self):
        is_max = self._window.isMaximized()
        self.btn_maximize.kind = "restore" if is_max else "maximize"
        self.btn_maximize.setToolTip("还原" if is_max else "最大化")
        self.btn_maximize.update()

    def _button_at(self, pos: QPoint) -> str | None:
        """返回指定位置的按钮类型 (用于排除按钮区域的双击/拖拽)"""
        for btn, kind in (
            (self.btn_minimize, "minimize"),
            (self.btn_maximize, "maximize"),
            (self.btn_close, "close"),
        ):
            if btn.isVisible() and btn.geometry().contains(pos):
                return kind
        return None

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self._button_at(event.position().toPoint()) is None:
            self._toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self._button_at(event.position().toPoint()) is None:
            # 使用系统级拖拽 (startSystemMove), 比 manual move 更流畅
            # 且支持 Windows Aero Snap (拖到屏幕边缘自动半屏)
            handle = self._window.windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)


class GlowButton(QPushButton):
    """批准稿中的紫色渐变主操作按钮。"""

    def __init__(self, text: str, icon_kind: str = "rocket", parent=None):
        super().__init__(text, parent)
        self.icon_kind = icon_kind
        self.setObjectName("startBtn")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedSize(190, 62)
        # QGraphicsDropShadowEffect 在 WA_TranslucentBackground 分层窗口中
        # 触发原生访问违规 (0xC000041D), 改用 paintEvent 内直接绘制辉光阴影

    def sizeHint(self) -> QSize:
        return QSize(190, 62)

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(2.0, 2.0, -2.0, -2.0)

        if not self.isEnabled():
            gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
            gradient.setColorAt(0.0, QColor("#334155"))
            gradient.setColorAt(1.0, QColor("#1e293b"))
            border = QColor(100, 116, 139, 70)
            text_color = QColor("#64748b")
            glow_alpha = 0
        else:
            gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
            if self.isDown():
                gradient.setColorAt(0.0, QColor("#6d28d9"))
                gradient.setColorAt(1.0, QColor("#a21caf"))
                glow_alpha = 60
            elif self.underMouse():
                gradient.setColorAt(0.0, QColor("#8b5cf6"))
                gradient.setColorAt(0.55, QColor("#7c3aed"))
                gradient.setColorAt(1.0, QColor("#c026d3"))
                glow_alpha = 110
            else:
                gradient.setColorAt(0.0, QColor("#7c3aed"))
                gradient.setColorAt(0.55, QColor("#6d28d9"))
                gradient.setColorAt(1.0, QColor("#a21caf"))
                glow_alpha = 80
            border = QColor(221, 214, 254, 150)
            text_color = QColor("#ffffff")

        # 辉光阴影 (替代 QGraphicsDropShadowEffect): 多层半透明圆角矩形
        if glow_alpha > 0:
            for _i, (expand, alpha_mul) in enumerate([(6, 0.15), (4, 0.25), (2, 0.40)]):
                glow_rect = rect.adjusted(-expand, -expand + 1, expand, expand + 1)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(124, 58, 237, int(glow_alpha * alpha_mul)))
                painter.drawRoundedRect(glow_rect, 12.0 + expand, 12.0 + expand)

        painter.setPen(QPen(border, 1.2))
        painter.setBrush(QBrush(gradient))
        painter.drawRoundedRect(rect, 12.0, 12.0)

        highlight = QLinearGradient(0, rect.top(), 0, rect.top() + rect.height() * 0.5)
        highlight.setColorAt(0.0, QColor(255, 255, 255, 48))
        highlight.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(highlight))
        painter.drawRoundedRect(rect.adjusted(1, 1, -1, -rect.height() * 0.48), 11, 11)

        icon_rect = QRectF(36.0, 19.0, 24.0, 24.0)
        _draw_vector_icon(
            painter,
            self.icon_kind,
            icon_rect,
            primary=QColor("#ffffff"),
            secondary=QColor("#ddd6fe"),
            opacity=1.0 if self.isEnabled() else 0.45,
        )

        font = QFont(self.font())
        font.setPointSizeF(14.0)
        font.setWeight(QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(text_color)
        painter.drawText(
            QRectF(64.0, 0.0, rect.width() - 67.0, self.height()),
            Qt.AlignmentFlag.AlignCenter,
            self.text(),
        )
        painter.end()


class PasswordLineEdit(QLineEdit):
    """带本地显隐按钮的密码输入框，不读取或持久化内容。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEchoMode(QLineEdit.EchoMode.Password)
        self._reveal_button = IconButton("eye", self)
        self._reveal_button.setFixedSize(32, 32)
        self._reveal_button.setToolTip("显示密钥")
        self._reveal_button.clicked.connect(self._toggle_visibility)
        self.setTextMargins(0, 0, 34, 0)

    def _toggle_visibility(self):
        show_plain_text = self.echoMode() == QLineEdit.EchoMode.Password
        self.setEchoMode(QLineEdit.EchoMode.Normal if show_plain_text else QLineEdit.EchoMode.Password)
        self._reveal_button.kind = "eye_off" if show_plain_text else "eye"
        self._reveal_button.setToolTip("隐藏密钥" if show_plain_text else "显示密钥")
        self._reveal_button.update()

    def resizeEvent(self, event: QResizeEvent):
        super().resizeEvent(event)
        button_size = self._reveal_button.size()
        self._reveal_button.move(
            self.width() - button_size.width() - 5,
            max(0, (self.height() - button_size.height()) // 2),
        )
        self._reveal_button.raise_()


class StatusIndicator(QWidget):
    """底部状态文字旁的轻量状态指示器。"""

    _COLORS = {
        "idle": QColor("#64748b"),
        "selected": QColor("#34d399"),
        "running": QColor("#fbbf24"),
        "success": QColor("#34d399"),
        "failed": QColor("#f87171"),
        "stopping": QColor("#fbbf24"),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = "idle"
        self.setFixedSize(18, 18)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_state(self, state: str):
        self._state = state
        self.update()

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        color = self._COLORS.get(self._state, self._COLORS["idle"])
        painter.setBrush(QColor(color.red(), color.green(), color.blue(), 24))
        painter.setPen(QPen(color, 1.6))
        painter.drawEllipse(QRectF(2.0, 2.0, 14.0, 14.0))

        pen = QPen(color, 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        if self._state in {"selected", "success"}:
            painter.drawLine(QPointF(5.5, 9.0), QPointF(8.0, 11.5))
            painter.drawLine(QPointF(8.0, 11.5), QPointF(12.8, 6.6))
        elif self._state in {"running", "stopping"}:
            painter.drawLine(QPointF(9.0, 5.2), QPointF(9.0, 9.2))
            painter.drawLine(QPointF(9.0, 9.2), QPointF(12.0, 10.8))
        elif self._state == "failed":
            painter.drawLine(QPointF(6.1, 6.1), QPointF(11.9, 11.9))
            painter.drawLine(QPointF(11.9, 6.1), QPointF(6.1, 11.9))
        else:
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QRectF(7.0, 7.0, 4.0, 4.0))
        painter.end()


# ============================================================
# 日志信号桥接: logging -> Qt Signal
# ============================================================
class QtLogHandler(logging.Handler):
    """将 Python logging 输出桥接到 Qt 信号"""

    def __init__(self, signal):
        super().__init__()
        self.signal = signal
        self.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(message)s",
                datefmt="%H:%M:%S",
            )
        )

    def emit(self, record):
        try:
            msg = self.format(record)
            self.signal.emit(msg, record.levelno)
        except Exception:
            pass


# ============================================================
# 方案卡片
# ============================================================
class MethodCard(QFrame):
    """可点击的方案选择卡片"""

    clicked = pyqtSignal(str)  # 发射方案 key

    def __init__(self, solution: dict, parent=None):
        super().__init__(parent)
        self.solution = solution
        self._selected = False
        self._hovered = False
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setObjectName("methodCard")
        self.setProperty("selected", False)
        self.setProperty("hovered", False)
        self.setFixedHeight(96)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)

        # QGraphicsDropShadowEffect 在 WA_TranslucentBackground 分层窗口中
        # 触发原生访问违规 (0xC000041D), 改用 paintEvent 内绘制辉光边框
        self._glow_enabled = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 14, 12)
        layout.setSpacing(13)

        icon_well = QFrame(self)
        icon_well.setObjectName("iconWell")
        icon_well.setFixedSize(58, 58)
        icon_layout = QVBoxLayout(icon_well)
        icon_layout.setContentsMargins(9, 9, 9, 9)
        icon_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon = VectorIcon(solution["icon"], 40, icon_well)
        icon_layout.addWidget(self._icon)
        layout.addWidget(icon_well, 0, Qt.AlignmentFlag.AlignVCenter)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 1, 0, 1)
        text_layout.setSpacing(6)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(7)

        title = QLabel(solution["name"], self)
        title.setObjectName("cardTitle")
        title_row.addWidget(title)

        cost_badge = QLabel(solution["cost"], self)
        cost_badge.setObjectName("badge")
        if solution["cost"] == "免费":
            cost_badge.setStyleSheet(
                "QLabel { color: #34d399; background-color: rgba(16, 185, 129, 30); "
                "border: 1px solid rgba(16, 185, 129, 45); }"
            )
        else:
            cost_badge.setStyleSheet(
                "QLabel { color: #fb7185; background-color: rgba(244, 63, 94, 30); "
                "border: 1px solid rgba(244, 63, 94, 45); }"
            )
        title_row.addWidget(cost_badge)

        status_badge = QLabel(solution["status"], self)
        status_badge.setObjectName("badge")
        status_color = QColor(solution["status_color"])
        status_badge.setStyleSheet(
            "QLabel {"
            f" color: {status_color.name()};"
            f" background-color: rgba({status_color.red()}, {status_color.green()}, {status_color.blue()}, 30);"
            f" border: 1px solid rgba({status_color.red()}, {status_color.green()}, {status_color.blue()}, 45);"
            " }"
        )
        title_row.addWidget(status_badge)
        title_row.addStretch(1)
        text_layout.addLayout(title_row)

        desc = QLabel(solution["short_desc"], self)
        desc.setObjectName("cardDesc")
        desc.setTextFormat(Qt.TextFormat.PlainText)
        text_layout.addWidget(desc)

        layout.addLayout(text_layout, 1)

        self._check_icon = VectorIcon("check", 24, self)
        self._check_icon.setVisible(False)
        layout.addWidget(self._check_icon, 0, Qt.AlignmentFlag.AlignVCenter)

    def mousePressEvent(self, event):
        if self.isEnabled() and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.solution["key"])
        super().mousePressEvent(event)

    def enterEvent(self, event):
        self._hovered = True
        self.setProperty("hovered", True)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.setProperty("hovered", False)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()
        super().leaveEvent(event)

    def set_selected(self, selected: bool):
        self._selected = selected
        self.setProperty("selected", selected)
        self._check_icon.setVisible(selected)
        self._glow_enabled = selected
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def paintEvent(self, event: QPaintEvent):
        """选中时绘制辉光边框 (替代 QGraphicsDropShadowEffect)"""
        super().paintEvent(event)
        if self._glow_enabled or self._hovered:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            # 选中: 紫色辉光; 悬停: 更淡的紫色边框
            if self._glow_enabled:
                for expand, alpha in [(3, 25), (1.5, 50)]:
                    glow_rect = rect.adjusted(-expand, -expand, expand, expand)
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(124, 58, 237, alpha))
                    painter.drawRoundedRect(glow_rect, 10 + expand, 10 + expand)
            elif self._hovered:
                painter.setPen(QPen(QColor(124, 58, 237, 40), 1.0))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(rect, 10, 10)
            painter.end()


# ============================================================
# 异步工作线程
# ============================================================
class BypassWorker(QThread):
    """在独立线程中运行异步 reCAPTCHA 绕过流程"""

    log_signal = pyqtSignal(str, int)  # (消息, 日志级别)
    status_signal = pyqtSignal(str)  # 状态: running/success/failed
    finished_signal = pyqtSignal(bool)  # 成功/失败

    def __init__(self, runtime):
        super().__init__()
        self.runtime = runtime
        self._loop = None
        self._stop_flag = False

    def run(self):
        """线程入口: 创建事件循环并运行"""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        # 安装日志桥接
        root_logger = logging.getLogger()
        handler = QtLogHandler(self.log_signal)
        root_logger.addHandler(handler)

        self.status_signal.emit("running")

        try:
            result = self._loop.run_until_complete(self._run_with_keepalive())
            self.finished_signal.emit(result)
            if result:
                self.status_signal.emit("success")
            else:
                self.status_signal.emit("failed")
        except Exception as e:
            logger.error(f"工作线程异常: {e}", exc_info=True)
            self.finished_signal.emit(False)
            self.status_signal.emit("failed")
        finally:
            root_logger.removeHandler(handler)
            if self._loop and not self._loop.is_closed():
                self._loop.close()

    async def _run_with_keepalive(self):
        """运行 runtime, 但不执行 finally 中的无限等待"""
        runtime = self.runtime
        runtime._keep_browser_open = False  # 由 worker 管理生命周期

        try:
            await runtime.init_browser()
            await runtime.navigate_to_target()

            sitekey = await runtime.extract_sitekey()
            page_url = runtime.page.url
            logger.info(f"页面 URL: {page_url}")
            logger.info(f"Sitekey: {sitekey}")

            token = await runtime.solve_recaptcha(sitekey, page_url)
            await runtime.inject_token_and_submit(token)
            success = await runtime.verify_result()

            if success:
                logger.info("=" * 50)
                logger.info(f"  [{runtime.method_name}] reCAPTCHA 绕过成功!")
                logger.info("=" * 50)
            else:
                logger.error("=" * 50)
                logger.error(f"  [{runtime.method_name}] reCAPTCHA 绕过失败")
                logger.error("=" * 50)

            return success

        except Exception as e:
            logger.error(f"执行异常: {e}", exc_info=True)
            return False

    def stop(self):
        """请求停止 (关闭浏览器)"""
        self._stop_flag = True
        if self._loop and self._loop.is_running():
            # 在事件循环中调度浏览器关闭
            if self.runtime and self.runtime.browser:
                asyncio.run_coroutine_threadsafe(self.runtime.close(), self._loop)


class InstallWorker(QThread):
    """在子线程中执行 pip install, 避免阻塞 UI"""

    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)  # (成功, 消息)

    def __init__(self, deps: list):
        super().__init__()
        self.deps = deps

    def run(self):
        import subprocess

        pkg_str = " ".join(self.deps)
        self.log_signal.emit(f">>> 正在安装: pip install {pkg_str}")
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install"] + self.deps,
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode == 0:
                self.log_signal.emit(">>> 安装成功!")
                self.finished_signal.emit(True, "安装成功")
            else:
                self.log_signal.emit(f">>> 安装失败:\n{result.stderr}")
                self.finished_signal.emit(False, result.stderr)
        except Exception as e:
            self.log_signal.emit(f">>> 安装异常: {e}")
            self.finished_signal.emit(False, str(e))


class DepCheckWorker(QThread):
    """
    在子线程中检测 Python 包是否已安装.
    torch (~7s) 和 transformers (~6s) 的 import 非常耗时,
    在主线程执行会冻结 UI 约 14 秒.
    """

    finished_signal = pyqtSignal(dict)  # {package_name: bool}

    # 包名 → import 名 的映射
    _CHECK_MAP = {
        "Pillow": "PIL",
        "torch": "torch",
        "transformers": "transformers",
        "ultralytics": "ultralytics",
        "faster_whisper": "faster_whisper",
        "pydub": "pydub",
        "SpeechRecognition": "speech_recognition",
    }

    def __init__(self, packages: list = None):
        super().__init__()
        # 默认检测图像方案的全部依赖 (含 YOLOv8)
        self._packages = packages or ["Pillow", "torch", "transformers", "ultralytics"]

    def run(self):
        results = {}
        for pkg_name in self._packages:
            import_name = self._CHECK_MAP.get(pkg_name, pkg_name)
            try:
                __import__(import_name)
                results[pkg_name] = True
            except ImportError:
                results[pkg_name] = False
        self.finished_signal.emit(results)


# ============================================================
# 主窗口
# ============================================================
class MainWindow(QMainWindow):
    """主窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("reCAPTCHA v2 自动化绕过工具")
        self.setWindowFlags(
            Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowSystemMenuHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(1000, 720)
        self.resize(1160, 870)

        # QPixmapCache 配置: 100MB 缓存上限
        QPixmapCache.setCacheLimit(102400)

        self._selected_key: str | None = None
        self._worker: BypassWorker | None = None
        self._runtime = None
        self._run_start_time: float = 0.0  # 运行计时

        # 持久化管理器 (QSettings + SQLite)
        self._persistence = PersistenceManager(self)

        # 四级优先级任务队列 + 背压
        self._task_queue = PriorityTaskQueue(parent=self)
        self._task_queue.backpressure.connect(self._on_backpressure)
        self._task_queue.recovered.connect(self._on_backpressure_recovered)
        self._log_degraded = False  # 背压降级标志

        # 模型预加载器 (后台线程加载 torch/transformers/ultralytics)
        self._model_loader: ModelLoader | None = None
        self._models_ready = False

        # 日志缓冲: 避免高频信号直接操作 QPlainTextEdit 导致 UI 卡死
        self._log_buffer: list[tuple[str, int]] = []
        self._log_timer = QTimer(self)
        self._log_timer.timeout.connect(self._flush_logs)
        self._log_max_lines = 2000  # 日志最大行数, 超出自动裁剪

        self._build_ui()

        # 原生无边框窗口控制器 (Win32 WM_NCCALCSIZE/WM_NCHITTEST/WM_GETMINMAXINFO)
        # 替代旧 Qt mouseEvent 缩放: 支持 Aero Snap、原生平滑缩放、DPI 感知边距
        self._window_chrome = FramelessChromeController(
            self,
            title_bar_getter=lambda: self._title_bar,
            is_effectively_maximized=lambda: self.isMaximized(),
        )
        # install() 在 showEvent 中调用, 避免窗口创建阶段安装过滤器导致原生崩溃

        self._apply_theme()
        self._log_timer.start(100)  # UI 构建完成后才启动定时器, 避免竞态

        # 恢复窗口状态
        self._persistence.restore_window_state(self)

        # 恢复上次选择的方案
        last_method = self._persistence.get(PersistenceManager.KEY_SELECTED_METHOD, "api")
        self._on_method_selected(last_method)

        # 延迟到窗口显示后启动模型预加载, 避免后台线程 import torch
        # 与窗口原生创建竞争导致 access violation 崩溃
        self._pending_model_loader = True

    def _start_model_loader(self):
        """在后台线程预加载 ML 模型"""
        self._model_loader = ModelLoader(self)
        self._model_loader.progress.connect(lambda msg: self._on_log(f"[ModelLoader] {msg}", logging.INFO))
        self._model_loader.ready.connect(self._on_models_ready)
        self._model_loader.error.connect(lambda err: self._on_log(f"[ModelLoader] 预加载失败: {err}", logging.WARNING))
        self._model_loader.start()

    def _on_models_ready(self, cache: dict):
        """模型预加载完成回调 (主线程, 由信号触发)"""
        self._models_ready = True
        self._on_log(
            f"[ModelLoader] 预加载完成: {', '.join(cache.keys())}",
            logging.INFO,
        )
        # 如果当前已选择 image 方案, 刷新配置面板
        if self._selected_key == "image":
            self._clear_dynamic_config()
            self._build_dynamic_config("image")

    def _on_backpressure(self):
        """背压触发: 启用日志降级"""
        self._log_degraded = True
        self._on_log("[系统] 背压触发, 日志降级到 10%", logging.WARNING)

    def _on_backpressure_recovered(self):
        """背压恢复: 关闭日志降级"""
        self._log_degraded = False
        self._on_log("[系统] 背压恢复, 日志恢复正常", logging.INFO)

    # ========================================================
    # UI 构建
    # ========================================================
    def _build_ui(self):
        central = QWidget(self)
        central.setObjectName("central")
        central.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCentralWidget(central)

        self._outer_layout = QVBoxLayout(central)
        self._outer_layout.setContentsMargins(9, 9, 9, 9)
        self._outer_layout.setSpacing(0)

        self._shell = AppShell(central)
        # 阴影改为 MainWindow.paintEvent 预渲染, 避免 QGraphicsDropShadowEffect 级联重绘
        self._shell_shadow_pixmap: QPixmap | None = None
        self._outer_layout.addWidget(self._shell)

        shell_layout = QVBoxLayout(self._shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)

        self._title_bar = TitleBar(self, self._shell)
        shell_layout.addWidget(self._title_bar)

        content = QWidget(self._shell)
        content.setObjectName("content")
        shell_layout.addWidget(content, 1)

        root = QVBoxLayout(content)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        root.addLayout(self._build_header())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(16)

        method_list = self._build_method_list()
        body.addWidget(method_list, 0)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(14)
        right.addWidget(self._build_config_panel(), 0)
        right.addWidget(self._build_log_panel(), 1)
        body.addLayout(right, 1)

        root.addLayout(body, 1)
        root.addLayout(self._build_footer())

    def _build_header(self) -> QVBoxLayout:
        layout = QVBoxLayout()
        layout.setContentsMargins(9, 1, 8, 0)
        layout.setSpacing(0)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        orb = VectorIcon("orb", 25)
        row.addWidget(orb, 0, Qt.AlignmentFlag.AlignTop)

        brand = QVBoxLayout()
        brand.setContentsMargins(0, 0, 0, 0)
        brand.setSpacing(5)

        title = QLabel("reCAPTCHA v2 自动化绕过工具")
        title.setObjectName("headerTitle")
        brand.addWidget(title)

        subtitle = QLabel("6 种绕过方案 · Playwright + Stealth")
        subtitle.setObjectName("headerSubtitle")
        brand.addWidget(subtitle)
        row.addLayout(brand)

        row.addStretch(1)
        decoration = VectorIcon("recaptcha", 72)
        decoration.set_icon_opacity(0.42)
        row.addWidget(decoration, 0, Qt.AlignmentFlag.AlignTop)

        layout.addLayout(row)
        return layout

    def _build_method_list(self) -> QWidget:
        container = QFrame()
        container.setObjectName("methodListPanel")
        container.setFixedWidth(390)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(14, 15, 14, 15)
        layout.setSpacing(10)

        label = QLabel("选择绕过方案")
        label.setObjectName("sectionTitle")
        layout.addWidget(label)
        layout.addSpacing(4)

        scroll = QScrollArea(container)
        scroll.setObjectName("methodScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        cards_host = QWidget(scroll)
        cards_host.setObjectName("methodCardsHost")
        cards_layout = QVBoxLayout(cards_host)
        cards_layout.setContentsMargins(0, 0, 4, 0)
        cards_layout.setSpacing(10)

        self._cards: dict[str, MethodCard] = {}
        for sol in SOLUTIONS:
            card = MethodCard(sol, cards_host)
            card.clicked.connect(self._on_method_selected)
            self._cards[sol["key"]] = card
            cards_layout.addWidget(card)

        cards_layout.addStretch(1)
        scroll.setWidget(cards_host)
        self._method_scroll = scroll
        layout.addWidget(scroll, 1)
        return container

    def _build_config_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("configPanel")
        panel.setMinimumHeight(196)
        panel.setMaximumHeight(238)

        self._config_layout = QVBoxLayout(panel)
        self._config_layout.setContentsMargins(18, 15, 18, 15)
        self._config_layout.setSpacing(7)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(9)
        title_row.addWidget(VectorIcon("cube", 24), 0, Qt.AlignmentFlag.AlignVCenter)

        self._config_title = QLabel("方案配置")
        self._config_title.setObjectName("configTitle")
        title_row.addWidget(self._config_title, 1)
        self._config_layout.addLayout(title_row)

        self._config_detail = QLabel("请从左侧选择一个绕过方案")
        self._config_detail.setObjectName("configDetail")
        self._config_detail.setWordWrap(True)
        self._config_detail.setTextFormat(Qt.TextFormat.PlainText)
        self._config_layout.addWidget(self._config_detail)

        self._dynamic_config = QWidget(panel)
        self._dynamic_config.setObjectName("dynamicConfig")
        self._dynamic_layout = QHBoxLayout(self._dynamic_config)
        self._dynamic_layout.setContentsMargins(0, 2, 0, 0)
        self._dynamic_layout.setSpacing(18)
        self._config_layout.addWidget(self._dynamic_config, 1)

        return panel

    def _build_log_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("logPanel")

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 13, 16, 15)
        layout.setSpacing(9)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        header.addWidget(VectorIcon("terminal", 22), 0, Qt.AlignmentFlag.AlignVCenter)

        title = QLabel("运行日志")
        title.setObjectName("logTitle")
        header.addWidget(title)
        header.addStretch(1)

        # 日志级别过滤下拉框
        self._log_level_filter = QComboBox()
        self._log_level_filter.addItem("全部", 0)
        self._log_level_filter.addItem("INFO+", logging.INFO)
        self._log_level_filter.addItem("WARNING+", logging.WARNING)
        self._log_level_filter.addItem("ERROR+", logging.ERROR)
        self._log_level_filter.setFixedWidth(95)
        self._log_level_filter.setCursor(Qt.CursorShape.PointingHandCursor)
        header.addWidget(self._log_level_filter)

        self._clear_log_btn = QPushButton("清空")
        self._clear_log_btn.setObjectName("secondaryBtn")
        self._clear_log_btn.setIcon(QIcon(_icon_pixmap("trash", 15, "#cbd5e1")))
        self._clear_log_btn.setIconSize(QSize(15, 15))
        self._clear_log_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        header.addWidget(self._clear_log_btn)
        layout.addLayout(header)

        self._log_output = QPlainTextEdit()
        self._log_output.setObjectName("logOutput")
        self._log_output.setReadOnly(True)
        self._log_output.setMaximumBlockCount(self._log_max_lines)  # 自动裁剪, 无需手动删行
        self._log_output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        fixed_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        fixed_font.setPointSize(9)
        self._log_output.setFont(fixed_font)
        layout.addWidget(self._log_output, 1)

        self._clear_log_btn.clicked.connect(self._log_output.clear)
        return panel

    def _build_footer(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setContentsMargins(9, 2, 0, 0)
        layout.setSpacing(10)

        self._status_indicator = StatusIndicator()
        layout.addWidget(self._status_indicator, 0, Qt.AlignmentFlag.AlignVCenter)

        self._status_label = QLabel("就绪")
        self._status_label.setObjectName("statusLabel")
        layout.addWidget(self._status_label, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addStretch(1)

        self._progress = QProgressBar()
        self._progress.setTextVisible(False)
        self._progress.setFixedWidth(120)
        self._progress.setVisible(False)
        layout.addWidget(self._progress, 0, Qt.AlignmentFlag.AlignVCenter)

        action_slot = QFrame()
        action_slot.setFixedSize(190, 62)
        action_layout = QGridLayout(action_slot)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(0)

        self._start_btn = GlowButton("启动绕过", "rocket", action_slot)
        self._start_btn.setObjectName("startBtn")
        self._start_btn.setEnabled(False)
        self._start_btn.clicked.connect(self._on_start)
        action_layout.addWidget(self._start_btn, 0, 0)

        self._stop_btn = QPushButton("停止", action_slot)
        self._stop_btn.setObjectName("stopBtn")
        self._stop_btn.setFixedSize(190, 62)
        self._stop_btn.setIcon(QIcon(_icon_pixmap("close", 17, "#ffffff")))
        self._stop_btn.setIconSize(QSize(17, 17))
        self._stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._stop_btn.setVisible(False)
        self._stop_btn.clicked.connect(self._on_stop)
        action_layout.addWidget(self._stop_btn, 0, 0)

        layout.addWidget(action_slot, 0, Qt.AlignmentFlag.AlignRight)
        return layout

    # ========================================================
    # 主题
    # ========================================================
    def _apply_theme(self):
        self.setStyleSheet(QSS)

    # ========================================================
    # 方案选择
    # ========================================================
    def _on_method_selected(self, key: str):
        self._selected_key = key
        self._persistence.set(PersistenceManager.KEY_SELECTED_METHOD, key)

        for k, card in self._cards.items():
            card.set_selected(k == key)

        sol = next(s for s in SOLUTIONS if s["key"] == key)
        self._config_title.setText(f"方案配置 — {sol['name']}")
        self._config_detail.setText(sol["detail"])
        self._status_label.setText(f"已选择: {sol['name']}")
        self._status_label.setObjectName("statusLabel")
        self._status_label.style().unpolish(self._status_label)
        self._status_label.style().polish(self._status_label)
        self._status_indicator.set_state("selected")

        # 清空动态配置
        self._clear_dynamic_config()
        self._build_dynamic_config(key)

        self._start_btn.setEnabled(True)

    def _clear_dynamic_config(self):
        def delete_item(item):
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.deleteLater()
            elif child_layout is not None:
                while child_layout.count():
                    delete_item(child_layout.takeAt(0))
                child_layout.deleteLater()

        while self._dynamic_layout.count():
            delete_item(self._dynamic_layout.takeAt(0))

        # QButtonGroup 的父控件已被上面的 delete_item 销毁,
        # 此时 _api_group 的 C++ 对象可能已失效, 不能再调用 deleteLater()
        if hasattr(self, "_api_group"):
            try:
                self._api_group.deleteLater()
            except RuntimeError:
                pass
            del self._api_group

    def _build_dynamic_config(self, key: str):
        if key == "audio":
            self._add_config_label("无需额外配置, 直接启动即可 (faster-whisper 模型自动加载)")

        elif key == "api":
            self._api_group = QButtonGroup(self._dynamic_config)
            self._rb_2captcha = QRadioButton("2captcha (~$2.99/1000次)")
            self._rb_capsolver = QRadioButton("CapSolver (~$0.80/1000次)")

            # 恢复上次选择的 provider
            saved_provider = self._persistence.get(PersistenceManager.KEY_API_PROVIDER, "2captcha")
            if saved_provider == "capsolver":
                self._rb_capsolver.setChecked(True)
            else:
                self._rb_2captcha.setChecked(True)

            self._api_group.addButton(self._rb_2captcha)
            self._api_group.addButton(self._rb_capsolver)

            # provider 变化时保存偏好
            self._rb_2captcha.toggled.connect(
                lambda checked: checked and self._persistence.set(PersistenceManager.KEY_API_PROVIDER, "2captcha")
            )
            self._rb_capsolver.toggled.connect(
                lambda checked: checked and self._persistence.set(PersistenceManager.KEY_API_PROVIDER, "capsolver")
            )

            provider_col = QVBoxLayout()
            provider_col.setContentsMargins(0, 0, 0, 0)
            provider_col.setSpacing(3)
            provider_col.addWidget(self._rb_2captcha)
            provider_col.addWidget(self._rb_capsolver)
            provider_col.addStretch(1)
            self._dynamic_layout.addLayout(provider_col, 1)

            key_col = QVBoxLayout()
            key_col.setContentsMargins(0, 0, 0, 0)
            key_col.setSpacing(6)
            api_label = QLabel("API Key（已在 config.py 配置则留空）")
            api_label.setObjectName("configLabel")
            key_col.addWidget(api_label)

            self._api_key_input = PasswordLineEdit()
            self._api_key_input.setObjectName("apiKeyInput")
            self._api_key_input.setPlaceholderText("输入 API Key")
            self._api_key_input.setMinimumWidth(310)
            key_col.addWidget(self._api_key_input)
            key_col.addStretch(1)
            self._dynamic_layout.addLayout(key_col, 2)

        elif key == "image":
            # 如果模型已预加载完成, 直接显示就绪状态
            if self._models_ready:
                cls_path = getattr(config, "YOLO_CLS_MODEL_PATH", "")
                seg_model = getattr(config, "YOLO_SEG_MODEL_NAME", "yolov8n-seg.pt")
                cls_status = "✓" if os.path.exists(cls_path) else "✗"
                self._add_config_label(
                    f"三引擎就绪 (模型已预加载):\n"
                    f"  YOLOv8-cls: recaptcha_cls_best.pt {cls_status} (3x3 分类)\n"
                    f"  YOLOv8-seg: {seg_model} (4x4 分割)\n"
                    f"  CLIP: {config.IMAGE_CLASSIFIER_MODEL} (回退)"
                )
            else:
                # 先显示加载占位 (torch import ~7s, transformers ~6s, 不能阻塞主线程)
                self._image_loading_label = QLabel("正在检测依赖...")
                self._image_loading_label.setObjectName("cardDesc")
                self._image_loading_label.setWordWrap(True)
                self._dynamic_layout.addWidget(self._image_loading_label, 1)

                # 启动子线程检测依赖 (窗口未显示时延迟启动, 避免与原生窗口创建竞争)
                self._dep_check_worker = DepCheckWorker()
                self._dep_check_worker.finished_signal.connect(self._on_dep_check_finished)
                if getattr(self, "_window_ready", False):
                    self._dep_check_worker.start()
                else:
                    self._pending_dep_check = self._dep_check_worker

        elif key == "cookie":
            self._cookie_input = QLineEdit()
            self._cookie_input.setPlaceholderText("输入 recaptcha-accessibility-cookie 值")
            self._cookie_input.setMinimumWidth(350)
            self._dynamic_layout.addWidget(self._cookie_input, 2)

            hint = QLabel("获取: google.com/recaptcha/admin/accessibility")
            hint.setObjectName("cardDetail")
            hint.setWordWrap(True)
            self._dynamic_layout.addWidget(hint, 1)

        elif key == "extension":
            ext_path = config.NOPECHA_EXTENSION_PATH
            exists = os.path.isdir(ext_path) and len(os.listdir(ext_path)) > 1

            if exists:
                self._add_config_label(f"扩展路径: {ext_path}\n状态: 已就绪")
            else:
                self._add_config_label(f"扩展路径: {ext_path}\n状态: 未找到扩展文件\n请下载 NopeCHA 扩展并解压到该目录")

            browse_btn = QPushButton("选择扩展目录")
            browse_btn.setObjectName("secondaryBtn")
            browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            browse_btn.clicked.connect(self._browse_extension)
            self._dynamic_layout.addWidget(browse_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        elif key == "native":
            # 检查 patchright 和 pyautogui 是否可用
            deps_ok = True
            missing = []
            for pkg, import_name in [("patchright", "patchright"), ("pyautogui", "pyautogui")]:
                if not self._check_module(import_name):
                    deps_ok = False
                    missing.append(pkg)

            if deps_ok:
                profile_mode = "临时profile+复制cookies" if not config.NATIVE_USE_REAL_PROFILE else "真实Chrome profile"
                self._add_config_label(
                    f"零 CDP 痕迹方案:\n"
                    f"  浏览器: patchright launch_persistent_context\n"
                    f"  Profile: {profile_mode}\n"
                    f"  点击: PyAutoGUI OS级控制 (isTrusted=true)\n"
                    f"  Fallback: 图像挑战 YOLO 三引擎"
                )
            else:
                self._add_config_label(f"缺少依赖: {', '.join(missing)}\n请运行: pip install {' '.join(missing)}")
                install_btn = QPushButton("安装依赖")
                install_btn.setObjectName("secondaryBtn")
                install_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                install_btn.clicked.connect(lambda: self._install_deps(missing))
                self._dynamic_layout.addWidget(install_btn, 0, Qt.AlignmentFlag.AlignVCenter)

    def _add_config_label(self, text: str):
        label = QLabel(text)
        label.setObjectName("cardDesc")
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        self._dynamic_layout.addWidget(label, 1)

    def _on_dep_check_finished(self, results: dict):
        """依赖检测完成回调 (主线程, 由 DepCheckWorker 信号触发)"""
        # 清空加载占位
        self._clear_dynamic_config()

        deps = []
        for pkg_name, installed in results.items():
            if not installed:
                deps.append(pkg_name)

        if deps:
            self._add_config_label(f"缺少依赖: {', '.join(deps)}\n请运行: pip install {' '.join(deps)}")
            install_btn = QPushButton("安装依赖")
            install_btn.setObjectName("secondaryBtn")
            install_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            install_btn.clicked.connect(lambda: self._install_deps(deps))
            self._dynamic_layout.addWidget(install_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        else:
            cls_path = getattr(config, "YOLO_CLS_MODEL_PATH", "")
            seg_model = getattr(config, "YOLO_SEG_MODEL_NAME", "yolov8n-seg.pt")
            cls_status = "✓" if os.path.exists(cls_path) else "✗"

            if self._models_ready:
                self._add_config_label(
                    f"三引擎就绪 (模型已预加载):\n"
                    f"  YOLOv8-cls: recaptcha_cls_best.pt {cls_status} (3x3 分类)\n"
                    f"  YOLOv8-seg: {seg_model} (4x4 分割)\n"
                    f"  CLIP: {config.IMAGE_CLASSIFIER_MODEL} (回退)"
                )
            else:
                self._add_config_label(
                    f"依赖已安装, 模型后台预加载中...\n"
                    f"  YOLOv8-cls: recaptcha_cls_best.pt {cls_status} (3x3 分类)\n"
                    f"  YOLOv8-seg: {seg_model} (4x4 分割)\n"
                    f"  CLIP: {config.IMAGE_CLASSIFIER_MODEL} (回退)\n"
                    f"预加载完成后可立即使用, 无需等待"
                )

    def _check_module(self, name: str) -> bool:
        try:
            __import__(name)
            return True
        except ImportError:
            return False

    def _install_deps(self, deps: list):
        """通过子线程执行 pip install, 不阻塞 UI"""
        self._install_worker = InstallWorker(deps)
        self._install_worker.log_signal.connect(lambda msg: self._on_log(msg, logging.INFO))
        self._install_worker.finished_signal.connect(self._on_install_finished)
        self._install_worker.start()

    def _on_install_finished(self, success: bool, msg: str):
        """安装完成回调 (主线程)"""
        if success:
            # 刷新配置面板
            if self._selected_key:
                self._clear_dynamic_config()
                self._build_dynamic_config(self._selected_key)

    def _browse_extension(self):
        from PyQt6.QtWidgets import QFileDialog

        path = QFileDialog.getExistingDirectory(
            self,
            "选择 NopeCHA 扩展目录",
            os.path.dirname(config.NOPECHA_EXTENSION_PATH),
        )
        if path:
            config.NOPECHA_EXTENSION_PATH = path
            self._clear_dynamic_config()
            self._build_dynamic_config("extension")

    # ========================================================
    # 启动/停止
    # ========================================================
    def _on_start(self):
        """启动按钮回调: 创建 runtime → 启动 BypassWorker 线程 → 切换 UI 到运行态"""
        if not self._selected_key:
            return

        key = self._selected_key
        try:
            runtime = self._create_runtime(key)
        except ValueError as e:
            QMessageBox.warning(self, "配置错误", str(e))
            return
        except Exception as e:
            QMessageBox.critical(self, "初始化失败", str(e))
            return

        if runtime is None:
            return

        self._runtime = runtime
        self._worker = BypassWorker(runtime)

        # 连接信号
        self._worker.log_signal.connect(self._on_log)
        self._worker.status_signal.connect(self._on_status)
        self._worker.finished_signal.connect(self._on_finished)

        # UI 状态切换
        self._set_running_ui(True)
        self._log_output.clear()

        # 记录开始时间 (用于计算耗时)
        self._run_start_time = time.perf_counter()

        # 启动线程
        self._worker.start()

    def _on_stop(self):
        """停止按钮回调: 通知 worker 关闭浏览器, 切换 UI 到停止态"""
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self._status_label.setText("正在停止...")
            self._status_label.setObjectName("statusLabel")
            self._status_label.style().unpolish(self._status_label)
            self._status_label.style().polish(self._status_label)
            self._status_indicator.set_state("stopping")

    def _create_runtime(self, key: str):
        """根据选择的方案创建对应的 runtime 实例"""
        if key == "audio":
            from runtimes.runtime_audio import AudioRuntime

            return AudioRuntime()

        elif key == "api":
            from runtimes.runtime_api import APIRuntime

            provider = "2captcha" if self._rb_2captcha.isChecked() else "capsolver"
            api_key = self._api_key_input.text().strip()

            if api_key:
                if provider == "2captcha":
                    config.TWOCAPTCHA_API_KEY = api_key
                else:
                    config.CAPSOLVER_API_KEY = api_key
            else:
                if provider == "2captcha":
                    if not config.TWOCAPTCHA_API_KEY or "YOUR_" in config.TWOCAPTCHA_API_KEY:
                        raise ValueError("2captcha API Key 未配置, 请在输入框或 config.py 中设置")
                else:
                    if not config.CAPSOLVER_API_KEY or "YOUR_" in config.CAPSOLVER_API_KEY:
                        raise ValueError("CapSolver API Key 未配置, 请在输入框或 config.py 中设置")

            return APIRuntime(provider=provider)

        elif key == "image":
            from runtimes.runtime_image import ImageRuntime

            if not self._check_module("PIL"):
                raise ValueError("Pillow 未安装, 请先安装依赖 (点击'安装依赖'按钮)")
            return ImageRuntime()

        elif key == "cookie":
            from runtimes.runtime_cookie import CookieRuntime

            cookie_val = self._cookie_input.text().strip()
            if not cookie_val:
                if config.RECAPTCHA_ACCESSIBILITY_COOKIE and "YOUR_" not in config.RECAPTCHA_ACCESSIBILITY_COOKIE:
                    cookie_val = config.RECAPTCHA_ACCESSIBILITY_COOKIE
                else:
                    raise ValueError("请输入无障碍 cookie 值")
            return CookieRuntime(cookie_value=cookie_val)

        elif key == "extension":
            from runtimes.runtime_extension import ExtensionRuntime

            ext_path = config.NOPECHA_EXTENSION_PATH
            if not os.path.isdir(ext_path) or len(os.listdir(ext_path)) <= 1:
                raise ValueError(f"NopeCHA 扩展目录无效: {ext_path}\n请选择正确的扩展目录")
            return ExtensionRuntime(extension_path=ext_path)

        elif key == "native":
            from runtimes.runtime_native import NativeRuntime

            if not self._check_module("patchright"):
                raise ValueError("patchright 未安装, 请先安装依赖")
            if not self._check_module("pyautogui"):
                raise ValueError("pyautogui 未安装, 请先安装依赖")
            return NativeRuntime()

        return None

    def _set_running_ui(self, running: bool):
        """切换 UI 运行/空闲态: 显示/隐藏启动停止按钮, 禁用方案卡片"""
        self._start_btn.setVisible(not running)
        self._stop_btn.setVisible(running)
        self._progress.setVisible(running)
        if running:
            self._progress.setRange(0, 0)  # 不确定进度
        # 禁用卡片选择
        for card in self._cards.values():
            card.setEnabled(not running)

    # ========================================================
    # 信号处理
    # ========================================================
    def _on_log(self, msg: str, level: int):
        """日志信号回调: 仅写入缓冲区, 不直接操作 UI"""
        # 背压降级: 降级状态下丢弃 DEBUG 和部分 INFO
        if self._log_degraded and level < logging.WARNING:
            if level == logging.INFO and hash(msg) % 10 != 0:  # 只保留 10% INFO
                return
        self._log_buffer.append((msg, level))

    def _flush_logs(self):
        """定时批量刷新日志到 QPlainTextEdit, 带智能滚动和级别过滤"""
        if not self._log_buffer:
            return

        buffer = self._log_buffer
        self._log_buffer = []

        # 级别过滤
        min_level = self._log_level_filter.currentData() or 0
        if min_level > 0:
            buffer = [(msg, lvl) for msg, lvl in buffer if lvl >= min_level]
            if not buffer:
                return

        color_map = {
            logging.DEBUG: "#64748b",
            logging.INFO: "#aab5c8",
            logging.WARNING: "#fbbf24",
            logging.ERROR: "#f87171",
            logging.CRITICAL: "#ef4444",
        }

        # 智能滚动: 仅当用户已在底部时自动滚动
        scrollbar = self._log_output.verticalScrollBar()
        auto_scroll = scrollbar.value() >= scrollbar.maximum() - 4

        cursor = self._log_output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.beginEditBlock()

        current_color = None
        for msg, level in buffer:
            color = color_map.get(level, "#aab5c8")
            if color != current_color:
                fmt = QTextCharFormat()
                fmt.setForeground(QColor(color))
                cursor.setCharFormat(fmt)
                current_color = color
            cursor.insertText(msg + "\n")

        cursor.endEditBlock()

        if auto_scroll:
            self._log_output.setTextCursor(cursor)
            scrollbar.setValue(scrollbar.maximum())

    def _on_status(self, status: str):
        """状态信号回调: 更新状态标签文本和颜色, 同步状态指示灯"""
        status_map = {
            "running": ("运行中...", "statusRunning"),
            "success": ("绕过成功!", "statusSuccess"),
            "failed": ("绕过失败", "statusFailed"),
        }
        text, obj_name = status_map.get(status, ("就绪", "statusLabel"))
        self._status_label.setText(text)
        self._status_label.setObjectName(obj_name)
        self._status_label.style().unpolish(self._status_label)
        self._status_label.style().polish(self._status_label)
        self._status_indicator.set_state(status if status in status_map else "idle")

    def _on_finished(self, success: bool):
        """完成信号回调: 计算耗时, 写入 SQLite 历史, 更新 UI 状态"""
        self._set_running_ui(False)
        self._progress.setVisible(False)

        # 计算耗时并记录到 SQLite
        duration = time.perf_counter() - self._run_start_time if self._run_start_time else 0.0
        method_name = next(
            (s["name"] for s in SOLUTIONS if s["key"] == self._selected_key),
            self._selected_key or "unknown",
        )
        detail = "成功" if success else "失败"
        self._task_queue.submit(
            self._persistence.add_record,
            method_name,
            success,
            duration,
            detail,
            priority=Priority.NORMAL,
        )

        if success:
            self._status_label.setText("绕过成功! 浏览器保持打开")
            self._status_indicator.set_state("success")
        else:
            self._status_label.setText("绕过失败, 查看日志了解详情")
            self._status_indicator.set_state("failed")

    # ========================================================
    # 原生窗口事件 (FramelessChromeController)
    # ========================================================
    def showEvent(self, event):
        """窗口显示时安装原生边框控制器 + 启动延迟的后台任务"""
        super().showEvent(event)
        if not getattr(self, "_chrome_installed", False):
            self._chrome_installed = True
            # 延迟到下一事件循环迭代安装, 避免在 show() 期间调用 winId()
            # 和 installNativeEventFilter 导致原生窗口 access violation
            QTimer.singleShot(0, self._install_chrome)
        self._window_ready = True

        # 启动延迟的后台任务 (避免与窗口原生创建竞争导致 access violation)
        if getattr(self, "_pending_model_loader", False):
            self._pending_model_loader = False
            QTimer.singleShot(100, self._start_model_loader)
        pending_dep = getattr(self, "_pending_dep_check", None)
        if pending_dep is not None:
            self._pending_dep_check = None
            QTimer.singleShot(200, pending_dep.start)

    def _install_chrome(self):
        """延迟安装原生边框控制器 (在事件循环中调用)"""
        self._window_chrome.install()
        self._window_chrome.on_show_event()

    # nativeEvent 不覆写: PyQt6 6.11.0 中在 FramelessWindowHint + WA_TranslucentBackground
    # 窗口上覆写 nativeEvent 会导致 show() 时 access violation (0xC000041D).
    # 原生消息 (WM_NCCALCSIZE/WM_NCHITTEST/WM_GETMINMAXINFO) 由 FramelessChromeController
    # 的 QAbstractNativeEventFilter 处理, 无需在此覆写.

    def eventFilter(self, watched, event):
        """Qt 事件过滤器: 边框缩放光标 + startSystemResize"""
        if hasattr(self, "_window_chrome") and self._window_chrome.event_filter(watched, event):
            return True
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event):
        """鼠标按下: 检测边框缩放区域"""
        if hasattr(self, "_window_chrome") and self._window_chrome.mouse_press_event(event):
            return
        super().mousePressEvent(event)

    # ========================================================
    # 无边框窗口阴影 (预渲染, 替代 QGraphicsDropShadowEffect)
    # ========================================================
    def paintEvent(self, event: QPaintEvent):
        """绘制窗口阴影: 仅在非最大化时绘制, 使用缓存避免重复渲染."""
        if self.isMaximized():
            super().paintEvent(event)
            return

        if self._shell_shadow_pixmap is None:
            self._shell_shadow_pixmap = self._render_shadow_pixmap()

        painter = QPainter(self)
        if self._shell_shadow_pixmap and not self._shell_shadow_pixmap.isNull():
            painter.drawPixmap(0, 0, self._shell_shadow_pixmap)
        painter.end()
        super().paintEvent(event)

    def _render_shadow_pixmap(self) -> QPixmap:
        """预渲染阴影 pixmap (仅在尺寸变化时调用一次)."""
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return QPixmap()

        pixmap = QPixmap(w, h)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        margin = 9
        shell_rect = QRectF(margin, margin, w - 2 * margin, h - 2 * margin)

        # 多层圆角矩形模拟 blur 阴影 (比 QGraphicsDropShadowEffect 快 10x+)
        for _i, (offset, blur, alpha) in enumerate(
            [
                (8, 34, 18),
                (6, 28, 28),
                (4, 22, 42),
                (2, 16, 58),
                (0, 10, 75),
            ]
        ):
            rect = shell_rect.adjusted(
                -blur + offset,
                -blur + offset,
                blur + offset,
                blur + offset,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, alpha))
            painter.drawRoundedRect(rect, 14 + blur, 14 + blur)

        painter.end()
        return pixmap

    def resizeEvent(self, event: QResizeEvent):
        """尺寸变化时清空阴影缓存, 触发重新渲染."""
        self._shell_shadow_pixmap = None
        super().resizeEvent(event)

    # ========================================================
    # 无边框窗口状态与缩放
    # ========================================================
    def changeEvent(self, event: QEvent):
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "_outer_layout"):
            maximized = self.isMaximized()
            self._outer_layout.setContentsMargins(
                0 if maximized else 9,
                0 if maximized else 9,
                0 if maximized else 9,
                0 if maximized else 9,
            )
            self._shell_shadow_pixmap = None  # 状态改变时重新生成阴影
            self._title_bar.sync_window_state()
            self._shell.update()
            self.update()  # 触发阴影重绘
        super().changeEvent(event)

    # ========================================================
    # 关闭事件
    # ========================================================
    def closeEvent(self, event):
        # 卸载原生边框控制器
        self._window_chrome.uninstall()

        # 保存窗口状态
        self._persistence.save_window_state(self)

        # 停止模型预加载线程
        if self._model_loader and self._model_loader.isRunning():
            self._model_loader.stop()
            self._model_loader.wait(3000)

        # 停止工作线程
        if self._worker and self._worker.isRunning():
            reply = QMessageBox.question(
                self,
                "确认退出",
                "绕过任务正在运行, 确定要退出吗?\n浏览器将被关闭.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                event.ignore()
                return
            self._worker.stop()
            self._worker.wait(5000)

        # 等待任务队列完成
        self._task_queue.wait_done(3000)

        # 关闭数据库
        self._persistence.close()

        event.accept()


# ============================================================
# 入口 (独立运行 gui.py 时使用; 推荐通过 main.py 统一入口)
# ============================================================
def main():
    app = QApplication(sys.argv)
    app.setApplicationName("reCAPTCHA Bypass Tool")
    app.setApplicationVersion("2.0.0")

    font = QFont("Microsoft YaHei UI", 9)
    app.setFont(font)
    app.setWindowIcon(QIcon(_icon_pixmap("shield", 64)))

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
