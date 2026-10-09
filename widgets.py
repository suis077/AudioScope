# -*- coding: utf-8 -*-
"""AudioScope 可视化与通用组件（雪白极简风格）"""
import os
import math
import time
import random

import numpy as np

from PySide6.QtCore import (Qt, Signal, QPoint, QRectF, QPointF, QObject,
                            QEvent, QVariantAnimation, QTimer, QEasingCurve)
from PySide6.QtGui import (QColor, QPainter, QPen, QBrush, QFont, QPixmap,
                           QCursor, QPainterPath, QFontMetrics, QLinearGradient,
                           QImage, QRadialGradient)
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
                               QLabel, QSizePolicy, QPushButton, QListWidget,
                               QListWidgetItem, QAbstractItemView, QMenu,
                               QFrame, QSlider)

from core import (BG, PANEL, INK, SOFT_INK, GRAY, LIGHT, LINE, HOVER, RED,
                  ART_BG, BAR_INK, N_BANDS, N_SPOKES, fmt_time, fmt_size)


# ----------------------------------------------------------------------------
# 沉浸模式（Immersive）
# ----------------------------------------------------------------------------
# 背景开启后，播放页整体切换为「沉浸」：背景是一张会流动的着色器画面，
# 前景所有组件一律不画任何底色、不做卡片，文字与图标改成浅色直接压在背景上。
#
# 参考 Johnwikix/original-sound-hq-player 的 View/PlayingDetailPage.xaml：
# 该页面里几乎所有控件都是 Background="Transparent"，只在 hover 时出现一层
# 极淡的白（AppBarButtonBackgroundPointerOver ≈ rgba(255,255,255,0.09)），
# 配合 NowPlayingCanvas 的着色器背景，控件才算"完全嵌进"背景里。
#
# 本模块通过 set_theme() 重绑模块级配色常量，所有控件下一次重绘自动跟随。
IMM = {
    "BG": "#0f1116", "PANEL": "#1a1d24", "INK": "#f4f6fa",
    "SOFT_INK": "#d3d8e2", "GRAY": "#aeb7c6", "LIGHT": "#7c8492",
    "LINE": "#3c4150", "HOVER": "#2b303a", "RED": "#ff5d63",
    "ART_BG": "#2b3039", "BAR_INK": "#e7eaf1",
}
_LIGHT_COLORS = {k: globals()[k] for k in IMM}
_theme_dark = False


def set_theme(dark):
    """全局配色切换（沉浸 / 明亮）。控件在下一次重绘时自动跟随。"""
    global _theme_dark
    dark = bool(dark)
    if dark == _theme_dark:
        return
    _theme_dark = dark
    for k, v in (IMM if dark else _LIGHT_COLORS).items():
        globals()[k] = v


def theme_dark():
    return _theme_dark


def rgba(col, a):
    """'#rrggbb' + 0~255 → QSS 可用的 rgba(...) 串"""
    c = QColor(col)
    return "rgba(%d,%d,%d,%d)" % (c.red(), c.green(), c.blue(), int(a))


def _lift_for_dark(col, target_v):
    """把颜色抬到深色背景上可读的明度（保留色相，只抬不压）。"""
    c = QColor(col)
    h, s, v, a = c.getHsv()
    if h < 0:
        h, s = 0, 0
    v = max(int(v), int(target_v * 255))
    s = int(min(255, s * 0.86))
    return QColor.fromHsv(int(h) % 360, s, v, 255)


def imm_page(w):
    """向上查找处于沉浸模式的播放页（背景已开启的那个）"""
    try:
        x = w.parentWidget()
    except Exception:
        return None
    while x is not None:
        if getattr(x, "_imm_on", False):
            return x
        x = x.parentWidget()
    return None


def on_imm(w):
    """控件是否正画在沉浸背景之上"""
    return imm_page(w) is not None


class GlassPanel(QFrame):
    """卡片容器。

    沉浸模式（背景开启）下**完全不绘制任何东西** —— 这是"组件彻底融进背景"
    的关键；只有在「纯净白」模式下才退回原来的白色圆角卡片。
    """

    def __init__(self, radius=18, alpha=None, margins=(16, 12, 16, 12),
                 spacing=6, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self._radius = radius
        self._body = QVBoxLayout(self)
        self._body.setContentsMargins(*margins)
        self._body.setSpacing(spacing)

    def body(self):
        return self._body

    def set_alpha(self, a):
        """兼容旧接口：沉浸模式已不再给卡片着色"""

    def paintEvent(self, e):
        if on_imm(self):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(LINE), 1))
        p.setBrush(QBrush(QColor(BG)))
        p.drawRoundedRect(r, self._radius, self._radius)


# ----------------------------------------------------------------------------
# 圆形按钮：outline（白底描边主按钮）/ ghost（无框次要按钮）
#           glass（玻璃圆钮，用于流体背景）/ solid（实心深色圆钮，主播放键）
# 图标一律矢量绘制 —— 避免 ▶/⏸ 等 unicode 被系统字体回退渲染成彩色 emoji
# ----------------------------------------------------------------------------
ICON_ALIAS = {"▶": "play", "⏸": "pause", "◀◀": "prev", "▶▶": "next"}


class CircleButton(QWidget):
    clicked = Signal()

    def __init__(self, diameter=44, icon="play", style="ghost", parent=None):
        super().__init__(parent)
        self._d = diameter
        self._icon = ICON_ALIAS.get(icon, icon)
        self._style = style
        self._hover = 0.0
        self._press = 0.0
        self._bounce = 0.0
        self.setFixedSize(diameter + 14, diameter + 14)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

    def _on_anim(self, v):
        self._hover = float(v)
        self.update()

    def set_icon(self, icon, bounce=False):
        icon = ICON_ALIAS.get(icon, icon)
        if icon == self._icon:
            return
        self._icon = icon
        self.update()          # 关键：图标变更后立即重绘（不等 hover 才刷新）
        if bounce:
            a = QVariantAnimation(self)
            a.setStartValue(0.0)
            a.setEndValue(1.0)
            a.setDuration(260)
            a.setEasingCurve(QEasingCurve.OutBack)
            a.valueChanged.connect(self._on_bounce)
            a.finished.connect(a.deleteLater)
            a.start()

    def set_style(self, style):
        if style != self._style:
            self._style = style
            self.update()

    def _on_bounce(self, v):
        self._bounce = float(v)
        self.update()

    def enterEvent(self, e):
        self._anim.stop()
        self._anim.setStartValue(self._hover)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def leaveEvent(self, e):
        self._anim.stop()
        self._anim.setStartValue(self._hover)
        self._anim.setEndValue(0.0)
        self._anim.start()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = 1.0
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = 0.0
            self.clicked.emit()
            self.update()

    def _scale(self):
        bounce = 1.0 + 0.10 * math.sin(math.pi * min(1.0, self._bounce))
        return (1.0 + 0.06 * self._hover) * (1.0 - 0.07 * self._press) * bounce

    def _draw_icon(self, p, cx, cy, col):
        """矢量图标：不受系统字体/emoji 着色影响"""
        s = self._d * 0.19 * self._scale()
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(col)))
        ic = self._icon
        if ic == "play":
            h, w = s * 1.15, s * 0.95
            tri = QPainterPath()
            tri.moveTo(cx - w * 0.45, cy - h)
            tri.lineTo(cx - w * 0.45, cy + h)
            tri.lineTo(cx + w * 0.72, cy)
            tri.closeSubpath()
            p.drawPath(tri)
        elif ic == "pause":
            bw, bh, gap = s * 0.42, s * 2.0, s * 0.62
            p.drawRoundedRect(QRectF(cx - gap / 2 - bw, cy - bh / 2, bw, bh),
                              bw / 2, bw / 2)
            p.drawRoundedRect(QRectF(cx + gap / 2, cy - bh / 2, bw, bh),
                              bw / 2, bw / 2)
        elif ic in ("prev", "next"):
            # ⏮ / ⏭ 经典形状：三角 + 外侧竖条。sign=-1 上一首（向左）、+1 下一首（向右）
            sign = -1.0 if ic == "prev" else 1.0
            half = s * 1.05
            tri = QPainterPath()
            tri.moveTo(cx - sign * 0.34 * s, cy - half)   # 竖直底边（靠内侧）
            tri.lineTo(cx - sign * 0.34 * s, cy + half)
            tri.lineTo(cx + sign * 0.80 * s, cy)          # 尖端（朝外）
            tri.closeSubpath()
            p.drawPath(tri)
            bw = s * 0.24
            p.drawRoundedRect(QRectF(cx + sign * 0.92 * s - bw / 2, cy - half,
                                     bw, half * 2), bw / 2, bw / 2)
        else:
            f = QFont(self.font())
            f.setPixelSize(max(11, int(self._d * 0.28)))
            p.setFont(f)
            p.setPen(QPen(QColor(col), 1))
            p.drawText(QRectF(0, 0, self.width(), self.height()),
                       Qt.AlignCenter, ic)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cx, cy = self.width() / 2, self.height() / 2
        r = self._d / 2 * self._scale()
        st = self._style
        if st == "outline":
            pen_col = QColor(RED) if self._hover > 0.4 else QColor(INK)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor("#ffffff")))
            p.drawEllipse(QPointF(cx, cy), r, r)
            p.setPen(QPen(pen_col, 1.6))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(cx, cy), r - 1, r - 1)
        elif st == "glass":
            # 沉浸圆钮：静止时**完全透明**，只在 hover / 按下时浮起一层极淡的白
            # （对齐参考实现的 AppBarButtonBackgroundPointerOver）
            if self._hover > 0.03:
                al = int(34 + 26 * self._hover)
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(QColor(255, 255, 255, al)))
                p.drawEllipse(QPointF(cx, cy), r, r)
            else:
                p.setPen(Qt.NoPen)
                p.setBrush(Qt.NoBrush)
            pen_col = QColor(RED) if self._hover > 0.4 else QColor(INK)
        elif st == "solid":
            # 主播放键：半透明白圆 + 浅描边（深底上做视觉焦点，而不是一块实心黑）
            if theme_dark():
                p.setPen(QPen(QColor(255, 255, 255, 96 if self._hover < .45 else 176),
                              1.2))
                p.setBrush(QBrush(QColor(255, 255, 255,
                                         int(40 + 34 * self._hover))))
                p.drawEllipse(QPointF(cx, cy), r, r)
                pen_col = QColor(RED) if self._hover > 0.45 else QColor("#ffffff")
            else:
                base = QColor(RED) if self._hover > 0.45 else QColor(INK)
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(QColor(0, 0, 0, 38)))
                p.drawEllipse(QPointF(cx, cy + 2), r, r)
                p.setBrush(QBrush(base))
                p.drawEllipse(QPointF(cx, cy), r, r)
                pen_col = QColor("#ffffff")
        else:  # ghost
            if self._hover > 0.05:
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(QColor(HOVER)))
                p.drawEllipse(QPointF(cx, cy), r, r)
            pen_col = QColor(INK) if self._hover > 0.4 else QColor(GRAY)
            if on_imm(self):
                pen_col = QColor(INK) if self._hover > 0.4 else QColor(SOFT_INK)
        self._draw_icon(p, cx, cy, pen_col)


# ----------------------------------------------------------------------------
# 环形可视化
# ----------------------------------------------------------------------------
class VisualizerWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.spectrum = np.zeros(N_SPOKES, dtype=np.float32)
        self.playing = False
        self.mic_mode = False
        self.cover_src = None
        self._cover_cache = None
        self._cache_key = 0
        self._cover_alpha = 1.0
        self.track_no = "--"
        self.rot = 0.0
        self.on_bg = False               # 直接画在流体背景上 → 换用高对比配色
        self.setMinimumHeight(340)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_on_background(self, v):
        v = bool(v)
        if v != self.on_bg:
            self.on_bg = v
            self.update()

    def set_spectrum(self, spec):
        self.spectrum = spec

    def set_cover(self, image):
        self.cover_src = image
        self._cover_cache = None
        self._cache_key = 0
        self._cover_alpha = 0.0 if image is not None else 1.0
        if image is not None:
            a = QVariantAnimation(self)
            a.setStartValue(0.0)
            a.setEndValue(1.0)
            a.setDuration(260)
            a.setEasingCurve(QEasingCurve.OutCubic)
            a.valueChanged.connect(self._on_cover_fade)
            a.finished.connect(a.deleteLater)
            a.start()

    def _on_cover_fade(self, v):
        self._cover_alpha = float(v)
        self.update()

    def _cover_pixmap(self, side: float):
        if self.cover_src is None or self.cover_src.isNull():
            return None
        key = int(side)
        if self._cache_key != key or self._cover_cache is None:
            pm = QPixmap.fromImage(self.cover_src)
            self._cover_cache = pm.scaled(int(side), int(side),
                                          Qt.KeepAspectRatioByExpanding,
                                          Qt.SmoothTransformation)
            self._cache_key = key
        return self._cover_cache

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2 - 8
        R = min(w, h) * 0.40

        # 直接叠在（深色）流动背景上：一律用浅色描线，保证可见度
        if self.on_bg:
            gray = QColor(255, 255, 255, 58)
            line = QColor(255, 255, 255, 150)
            soft = QColor(255, 255, 255, 128)
            lab_col = QColor(255, 255, 255, 132)
            bar_col = QColor(255, 255, 255, 224)
            cap_col = QColor(255, 255, 255, 150)
        else:
            gray = QColor(GRAY)
            line = QColor(LINE)
            soft = QColor(SOFT_INK)
            lab_col = QColor(GRAY)
            bar_col = QColor(BAR_INK)
            cap_col = QColor(GRAY)
        red = QColor(RED)

        p.setPen(QPen(line, 1))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), R, R)
        for i in range(60):
            ang = math.radians(i * 6 - 90)
            major = (i % 5 == 0)
            r1 = R
            r2 = R - (9 if major else 5)
            p.setPen(QPen(soft if major else gray, 1))
            p.drawLine(QPointF(cx + r1 * math.cos(ang), cy + r1 * math.sin(ang)),
                       QPointF(cx + r2 * math.cos(ang), cy + r2 * math.sin(ang)))
        f8 = QFont(self.font())
        f8.setPointSize(8)
        p.setFont(f8)
        p.setPen(QPen(lab_col, 1))
        p.drawText(QRectF(cx - R - 62, cy - 8, 50, 16), Qt.AlignRight, "N 270°")
        p.drawText(QRectF(cx + R + 12, cy - 8, 50, 16), Qt.AlignLeft, "90°")
        p.drawText(QRectF(cx - 30, cy - R - 28, 60, 14), Qt.AlignCenter, "W 30")
        p.drawText(QRectF(cx - 30, cy + R + 12, 60, 14), Qt.AlignCenter, "#09 V")

        inner = R * 0.62
        amp = R * 0.34
        p.setPen(QPen(QColor(RED) if self.mic_mode else bar_col, 2))
        n = len(self.spectrum)
        for i in range(n):
            v = float(self.spectrum[i])
            if v < 0.015:
                continue
            ang = math.radians(i * 360.0 / n - 90 + self.rot)
            r2 = inner + v * amp
            p.drawLine(QPointF(cx + inner * math.cos(ang), cy + inner * math.sin(ang)),
                       QPointF(cx + r2 * math.cos(ang), cy + r2 * math.sin(ang)))

        a = self._cover_alpha
        side = R * 1.02 * (0.96 + 0.04 * a)
        x0, y0 = cx - side / 2, cy - side / 2
        pm = self._cover_pixmap(side)
        if self.on_bg:
            # 封面软投影，从背景上"抬"起来
            p.setPen(Qt.NoPen)
            for k, al in ((0.060, 26), (0.032, 40), (0.012, 54)):
                off = side * k
                p.setBrush(QBrush(QColor(30, 32, 40, al)))
                p.drawRoundedRect(QRectF(x0 - off * 0.4, y0 + off,
                                         side + off * 0.8, side + off),
                                  14, 14)
        p.setOpacity(max(0.05, a))
        if pm is not None:
            sx = (pm.width() - side) / 2
            sy = (pm.height() - side) / 2
            p.drawPixmap(QRectF(x0, y0, side, side), pm,
                         QRectF(sx, sy, side, side))
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor(ART_BG)))
            p.drawRect(QRectF(x0, y0, side, side))
            p.setPen(QPen(QColor(GRAY), 1))
            fbig = QFont(self.font())
            fbig.setPointSize(30)
            p.setFont(fbig)
            p.drawText(QRectF(x0, y0, side, side), Qt.AlignCenter, "♪")

        bl = R * 0.22
        p.setPen(QPen(QColor(RED), 2))
        for (px, py, dx, dy) in [
            (x0, y0, 1, 1), (x0 + side, y0, -1, 1),
            (x0, y0 + side, 1, -1), (x0 + side, y0 + side, -1, -1),
        ]:
            p.drawLine(QPointF(px, py), QPointF(px + dx * bl, py))
            p.drawLine(QPointF(px, py), QPointF(px, py + dy * bl))
        p.setOpacity(1.0)

        if self.playing:
            ang = math.radians(self.rot * 2 - 90)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(red))
            p.drawEllipse(QPointF(cx + (R + 4) * math.cos(ang),
                                  cy + (R + 4) * math.sin(ang)), 4, 4)
        if self.mic_mode:
            p.setPen(QPen(QColor(RED), 1))
            p.setFont(f8)
            p.drawText(QRectF(cx - 40, y0 - 30, 80, 16), Qt.AlignCenter, "● MIC LIVE")

        p.setPen(QPen(cap_col, 1))
        p.setFont(f8)
        p.drawText(QRectF(cx - 60, h - 24, 120, 16), Qt.AlignCenter,
                   f"TRACK {self.track_no}")

    def tick(self):
        if self.playing:
            self.rot = (self.rot + 0.12) % 360
        self.update()


# ----------------------------------------------------------------------------
# 频谱柱状图
# ----------------------------------------------------------------------------
class SpectrumWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.spectrum = np.zeros(N_BANDS, dtype=np.float32)
        self.peaks = np.zeros(N_BANDS, dtype=np.float32)
        self.last_t = time.time()
        self.on_bg = False               # 直接压在流动背景上 → 刻度改浅色
        self.setMinimumHeight(200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_on_background(self, v):
        v = bool(v)
        if v != self.on_bg:
            self.on_bg = v
            self.update()

    def set_spectrum(self, spec):
        now = time.time()
        dt = min(0.1, now - self.last_t)
        self.last_t = now
        self.peaks = np.maximum(spec, self.peaks - dt * 0.35)
        self.spectrum = spec
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        L, Rm, T, B = 40, 14, 16, 26
        pw, ph = w - L - Rm, h - T - B

        f8 = QFont(self.font()); f8.setPointSize(8)
        p.setFont(f8)
        # 沉浸式：刻度/网格改半透明白，直接融进流动背景，避免深色横线拉出"分区感"
        grid_col = QColor(255, 255, 255, 46) if self.on_bg else QColor(LINE)
        tick_col = QColor(255, 255, 255, 132) if self.on_bg else QColor(GRAY)
        bar_col = QColor(255, 255, 255, 224) if self.on_bg else QColor(BAR_INK)
        for db, frac in ((0, 0.0), (-30, 0.45), (-60, 0.8)):
            y = T + ph * frac
            p.setPen(QPen(grid_col, 1))
            p.drawLine(QPointF(L, y), QPointF(L + pw, y))
            p.setPen(QPen(tick_col, 1))
            p.drawText(QRectF(0, y - 7, L - 8, 14), Qt.AlignRight, f"{db}")

        freq_labels = [(20, "20"), (200, "200"), (1000, "1k"), (2000, "2k"),
                       (5000, "5k"), (20000, "20k")]
        lo10, hi10 = math.log10(20), math.log10(20000)

        def fx(f):
            return L + (math.log10(f) - lo10) / (hi10 - lo10) * pw

        n = len(self.spectrum)
        smooth = self.spectrum.copy()
        if n >= 3:
            smooth[1:-1] = (0.25 * self.spectrum[:-2]
                            + 0.50 * self.spectrum[1:-1]
                            + 0.25 * self.spectrum[2:])
        gap = 2.0
        bw = pw / n - gap
        for i in range(n):
            v = float(smooth[i])
            if v < 0.012:
                continue
            x = L + i * (pw / n) + gap / 2
            bh = v * ph
            if bh > 1:
                p.fillRect(QRectF(x, T + ph - bh, bw, bh), bar_col)
            pv = float(self.peaks[i])
            if pv > 0.01:
                p.fillRect(QRectF(x, T + ph - pv * ph - 2, bw, 2), QColor(RED))

        p.setPen(QPen(grid_col, 1))
        p.drawLine(QPointF(L, T + ph), QPointF(L + pw, T + ph))
        for f, txt in freq_labels:
            x = fx(f)
            p.setPen(QPen(tick_col, 1))
            p.drawLine(QPointF(x, T + ph), QPointF(x, T + ph + 4))
            p.drawText(QRectF(x - 20, T + ph + 6, 40, 14), Qt.AlignCenter, txt)


# ----------------------------------------------------------------------------
# 播放页时间轴
# ----------------------------------------------------------------------------
class TimelineWidget(QWidget):
    seekRequested = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.duration = 0.0
        self.position = 0.0
        self._drag = False
        self.on_bg = False               # 直接压在流动背景上 → 轨道/刻度改浅色
        self.setFixedHeight(70)

    def set_on_background(self, v):
        v = bool(v)
        if v != self.on_bg:
            self.on_bg = v
            self.update()

    def set_state(self, pos, dur):
        self.position, self.duration = pos, dur
        self.update()

    def _pos_at(self, x):
        L, Rm = 14, 14
        w = self.width() - L - Rm
        return float(np.clip((x - L) / max(1, w), 0, 1)) * self.duration

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.duration > 0:
            self._drag = True
            self.seekRequested.emit(self._pos_at(e.position().x()))

    def mouseMoveEvent(self, e):
        if self._drag and self.duration > 0:
            self.seekRequested.emit(self._pos_at(e.position().x()))

    def mouseReleaseEvent(self, e):
        self._drag = False

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        L, Rm = 14, 14
        bar_w = w - L - Rm
        y = h - 32

        # 沉浸式：轨道/刻度改半透明白，直接融进流动背景
        track_col = QColor(255, 255, 255, 64) if self.on_bg else QColor(LINE)
        fill_col = QColor(255, 255, 255, 236) if self.on_bg else QColor(INK)
        tick_col = QColor(255, 255, 255, 150) if self.on_bg else QColor(GRAY)

        p.setPen(QPen(track_col, 3))
        p.drawLine(QPointF(L, y), QPointF(L + bar_w, y))
        if self.duration > 0:
            frac = self.position / self.duration
            mx = L + bar_w * frac
            p.setPen(QPen(fill_col, 3))
            p.drawLine(QPointF(L, y), QPointF(mx, y))
            p.setPen(QPen(QColor(RED), 2))
            p.drawLine(QPointF(mx, y - 12), QPointF(mx, y + 12))
            p.fillRect(QRectF(mx - 1.5, y - 16, 3, 5), QColor(RED))
            f8 = QFont(self.font()); f8.setPointSize(8); f8.setBold(True)
            p.setFont(f8)
            p.setPen(QPen(QColor(RED), 1))
            p.drawText(QRectF(mx - 32, y - 34, 64, 16), Qt.AlignCenter,
                       fmt_time(self.position))
        f9 = QFont(self.font()); f9.setPointSize(8)
        p.setFont(f9)
        p.setPen(QPen(tick_col, 1))
        for i in range(5):
            x = L + bar_w * i / 4
            t = self.duration * i / 4
            p.drawText(QRectF(x - 30, h - 22, 60, 14), Qt.AlignCenter, fmt_time(t))


# ----------------------------------------------------------------------------
# 底部播放条迷你进度（细线 + 红点，可点击拖拽）
# ----------------------------------------------------------------------------
class MiniTimeline(QWidget):
    seekRequested = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.duration = 0.0
        self.position = 0.0
        self._drag = False
        self.setFixedHeight(18)

    def set_state(self, pos, dur):
        self.position, self.duration = pos, dur
        self.update()

    def _pos_at(self, x):
        return float(np.clip(x / max(1, self.width()), 0, 1)) * self.duration

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.duration > 0:
            self._drag = True
            self.seekRequested.emit(self._pos_at(e.position().x()))

    def mouseMoveEvent(self, e):
        if self._drag and self.duration > 0:
            self.seekRequested.emit(self._pos_at(e.position().x()))

    def mouseReleaseEvent(self, e):
        self._drag = False

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        y = h / 2
        p.setPen(QPen(QColor(LINE), 3))
        p.drawLine(QPointF(0, y), QPointF(w, y))
        if self.duration > 0:
            mx = w * self.position / self.duration
            p.setPen(QPen(QColor(INK), 3))
            p.drawLine(QPointF(0, y), QPointF(mx, y))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor(RED)))
            p.drawEllipse(QPointF(mx, y), 4.5, 4.5)


# ----------------------------------------------------------------------------
# 信息栅格
# ----------------------------------------------------------------------------
class InfoGrid(QWidget):
    FIELDS = ["dyn", "loud", "sample_rate", "bit_depth", "bitrate",
              "file_size", "channels", "format"]
    LABELS = {"dyn": "DYNAMIC RANGE", "loud": "LOUDNESS", "sample_rate": "SAMPLE RATE",
              "bit_depth": "BIT DEPTH", "bitrate": "BITRATE", "file_size": "FILE SIZE",
              "channels": "CHANNELS", "format": "FORMAT"}

    def __init__(self, parent=None):
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 8, 0, 4)
        grid.setHorizontalSpacing(30)
        grid.setVerticalSpacing(10)
        self.labels = {}
        self.caps = {}
        positions = [(0, 0), (0, 1), (0, 2), (0, 3),
                     (1, 0), (1, 1), (1, 2), (1, 3)]
        for (r, c), key in zip(positions, self.FIELDS):
            box = QVBoxLayout()
            box.setSpacing(3)
            cap = QLabel(self.LABELS[key])
            val = QLabel("--")
            self.caps[key] = cap
            self.labels[key] = val
            box.addWidget(cap)
            box.addWidget(val)
            wrap = QWidget()
            wrap.setLayout(box)
            grid.addWidget(wrap, r, c)
        self.restyle()

    def restyle(self):
        """跟随当前主题重新着色（沉浸模式下文字转浅色）"""
        for cap in self.caps.values():
            cap.setStyleSheet(
                f"color:{GRAY}; font-size:8pt; letter-spacing:1px;")
        for val in self.labels.values():
            val.setStyleSheet(
                f"color:{INK}; font-size:10.5pt; font-weight:600;")

    def set_values(self, **kw):
        for k, v in kw.items():
            if k in self.labels:
                self.labels[k].setText(str(v))


# ----------------------------------------------------------------------------
# 歌词控件：按时间高亮当前行，平滑滚动
# ----------------------------------------------------------------------------
class LyricsWidget(QWidget):
    """可自定义样式的歌词控件：颜色 / 字号 / 对齐（左·居中·右）"""
    LEFT, CENTER, RIGHT = 0, 1, 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.lines = []          # [(t, text)]
        self.index = -1
        self._disp = -1.0        # 平滑滚动用的「显示行号」（浮点）
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(320)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)
        self.hint = "暂无歌词"
        self.line_h = 46
        self.plain = []          # 纯文本歌词（无时间轴）
        # ---- 可调样式 ----
        self.color = QColor(SOFT_INK)      # 普通行
        self.active_color = QColor(RED)    # 当前行
        self.font_size = 15                # 基准字号（px）
        self.align = self.LEFT
        # 卡片不透明度：255 = 不画卡片（纯白底）；< 255 时画一层圆角「毛玻璃」
        # 卡片，避免在流体背景上出现刺眼的纯白矩形色块
        self.card_alpha = 255
        self.setMinimumHeight(160)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._fade = QVariantAnimation(self)
        self._fade.setDuration(240)
        self._fade.valueChanged.connect(lambda v: self.update())

    # ---------------- 样式 ----------------
    def set_style(self, color=None, active=None, size=None, align=None):
        if color:
            self.color = QColor(color)
        if active:
            self.active_color = QColor(active)
        if size:
            self.font_size = int(max(11, min(40, size)))
        if align is not None:
            self.align = int(align) % 3
        self.update()

    def _align_flag(self):
        return (Qt.AlignLeft, Qt.AlignHCenter, Qt.AlignRight)[self.align]

    def set_card_alpha(self, v):
        """背景为流体/图片时用半透明圆角卡片承载歌词；纯白背景传 255"""
        try:
            v = int(v)
        except Exception:
            v = 255
        v = max(0, min(255, v))
        if v != self.card_alpha:
            self.card_alpha = v
            self.update()

    def set_hint(self, text):
        self.hint = text
        self.update()

    def set_lyrics(self, lines):
        self.lines = list(lines) if lines else []
        self.plain = []
        self.index = -1
        self._disp = -1.0
        self._anim.stop()
        self.update()

    def set_plain_text(self, text):
        """无时间轴的纯文本歌词：静态展示，不做高亮滚动"""
        self.lines = []
        self.plain = [l.strip() for l in (text or "").splitlines() if l.strip()]
        self.index = -1
        self._anim.stop()
        self.update()

    def clear(self):
        self.set_lyrics([])

    def _on_anim(self, v):
        self._disp = float(v)
        self.update()

    def set_time(self, t: float):
        """按播放时间定位当前行；变化时平滑滚动"""
        if not self.lines:
            return
        idx = -1
        for i, (ts, _) in enumerate(self.lines):
            if ts <= t + 0.08:
                idx = i
            else:
                break
        if idx == self.index:
            return
        prev = self.index if self.index >= 0 else idx
        self.index = idx
        self._anim.stop()
        self._anim.setStartValue(float(prev) if prev >= 0 else float(idx))
        self._anim.setEndValue(float(idx))
        self._anim.start()
        self.update()

    def _row_font(self, dist):
        f = QFont(self.font())
        base = self.font_size
        if dist < 0.6:
            f.setPixelSize(int(base * 1.34))
            f.setBold(True)
        elif dist < 1.6:
            f.setPixelSize(base)
            f.setBold(False)
        else:
            f.setPixelSize(int(base * 0.87))
            f.setBold(False)
        return f

    def paintEvent(self, e):
        w, h = self.width(), self.height()
        if w < 4 or h < 4:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        flag = self._align_flag()

        # 明亮模式且要求卡片时画白色圆角卡；沉浸模式**什么都不画**
        if self.card_alpha < 250 and not on_imm(self):
            p.setPen(QPen(QColor(LINE), 1))
            p.setBrush(QBrush(QColor(BG)))
            p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 18, 18)

        if not self.lines and not self.plain:
            p.setPen(QPen(QColor(GRAY), 1))
            f = QFont(self.font())
            f.setPixelSize(14)
            p.setFont(f)
            p.drawText(QRectF(0, h / 2 - 20, w, 40), Qt.AlignCenter, self.hint)
            return

        col, act = self._eff_colors()

        # 文字先画进离屏图，再对这张图做 DestinationIn 上下渐隐 ——
        # 渐隐只作用于我们自己的内容，边缘是"自然淡出"，不会在背景上
        # 糊出一条白色/黑色的色带（这是 v12 里那块白带子的根因）。
        # 高 DPI 屏上必须按「物理像素」建离屏图、再把 devicePixelRatio 设回
        # 去，否则这张 w×h 的图会被系统放大 DPR 倍后才合成到屏幕 —— 本机
        # DPR=1.5，字实际被拉大 1.5 倍，笔画必然发虚（"歌词字体模糊"根因）。
        dpr = self.devicePixelRatioF()
        layer = QPixmap(max(1, int(round(w * dpr))),
                        max(1, int(round(h * dpr))))
        layer.setDevicePixelRatio(dpr)
        layer.fill(Qt.transparent)
        lp = QPainter(layer)
        lp.setRenderHint(QPainter.Antialiasing)
        lp.setRenderHint(QPainter.TextAntialiasing)

        if self.plain:
            # ---- 纯文本歌词（无时间轴）：静态排列 ----
            fs = max(12, int(self.font_size))
            lh = int(fs * 1.9)
            f = QFont(self.font())
            f.setPixelSize(fs)
            lp.setFont(f)
            lp.setPen(QPen(col, 1))
            fm = QFontMetrics(f)
            maxw = w - 36
            for i, t in enumerate(self.plain):
                y = 14 + i * lh
                if y > h - 10:
                    break
                lp.drawText(QRectF(18, y, maxw, lh), flag | Qt.AlignVCenter,
                            fm.elidedText(t, Qt.ElideRight, int(maxw)))
        else:
            cur = self._disp if self._disp >= 0 else float(self.index)
            base = h * 0.40
            lh = int(self.font_size * 2.15)
            top = int(max(0, cur - 7))
            bot = int(min(len(self.lines), cur + 9))
            for i in range(top, bot):
                y = base + (i - cur) * lh
                if y < -lh or y > h + lh:
                    continue
                dist = abs(i - cur)
                alpha = max(0.0, 1.0 - dist / 5.6) ** 1.3
                if dist < 0.6:
                    c, a = QColor(act), 1.0
                elif dist < 1.6:
                    c = QColor(col)
                    a = 0.55 + 0.45 * alpha
                else:
                    c = QColor(col)
                    a = 0.16 + 0.44 * alpha
                a = max(0.0, min(1.0, a))
                if a < 0.02:
                    continue
                f = self._row_font(dist)
                lp.setFont(f)
                c.setAlphaF(a)
                lp.setPen(QPen(c, 1))
                text = self.lines[i][1] or "♪"
                rect = QRectF(18, y - lh / 2, w - 36, lh)
                fm = QFontMetrics(f)
                lp.drawText(rect, flag | Qt.AlignVCenter,
                            fm.elidedText(text, Qt.ElideRight, int(rect.width())))
        lp.end()

        self._fade_layer(layer, w, h)
        p.drawPixmap(0, 0, layer)

    def _eff_colors(self):
        """沉浸（深色背景）模式下把歌词颜色适配到深底上：
        保留用户选的色相与大致饱和度，只把明度抬到可读区间，不改动原设置。
        """
        if not on_imm(self):
            return QColor(self.color), QColor(self.active_color)
        return (_lift_for_dark(self.color, 0.74),
                _lift_for_dark(self.active_color, 0.80))

    @staticmethod
    def _fade_layer(pm, w, h):
        f = max(18.0, min(64.0, h * 0.16))
        p = QPainter(pm)
        p.setCompositionMode(QPainter.CompositionMode_DestinationIn)
        g = QLinearGradient(0, 0, 0, h)
        g.setColorAt(0.0, QColor(0, 0, 0, 0))
        g.setColorAt(f / h, QColor(0, 0, 0, 255))
        g.setColorAt(1.0 - f / h, QColor(0, 0, 0, 255))
        g.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(QRectF(0, 0, w, h), QBrush(g))
        p.end()

    def _edge_fade(self, p, w, h):
        """兼容旧调用点：渐隐已在 _fade_layer 里随文字层完成"""


# ----------------------------------------------------------------------------
# 极简开关（自动歌词 / 自动封面）
# ----------------------------------------------------------------------------
class ToggleSwitch(QWidget):
    toggled = Signal(bool)

    def __init__(self, label="", checked=True, parent=None):
        super().__init__(parent)
        self.label = label
        self.checked = bool(checked)
        self._p = 1.0 if checked else 0.0
        self.setFixedSize(118, 28)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

    def _on_anim(self, v):
        self._p = float(v)
        self.update()

    def set_checked(self, val, emit=False):
        val = bool(val)
        if val == self.checked:
            self._p = 1.0 if val else 0.0
            self.update()
            return
        self.checked = val
        self._anim.stop()
        self._anim.setStartValue(self._p)
        self._anim.setEndValue(1.0 if val else 0.0)
        self._anim.start()
        if emit:
            self.toggled.emit(val)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.set_checked(not self.checked)
            self.toggled.emit(self.checked)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        imm = on_imm(self)
        tw, th = 32, 15
        tx = w - tw - 10
        ty = (h - th) / 2
        on = self._p
        if on > 0.02:
            track = QColor(RED)
            track.setAlphaF((0.35 if imm else 0.12) + (0.65 if imm else 0.88) * on)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(track))
        else:
            # 关闭态：沉浸模式下只留一圈极淡的白描边（与参考实现一致，
            # 静止时组件不应有任何实心底色）
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255, 74) if imm
                          else QColor(224, 49, 49, 46), 1.0))
        p.drawRoundedRect(QRectF(tx + 0.5, ty + 0.5, tw - 1, th - 1),
                          th / 2, th / 2)
        kx = tx + th / 2 + (tw - th) * on
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(RED if on > 0.5 else
                                 (QColor("#dfe3ea") if imm else GRAY))))
        p.drawEllipse(QPointF(kx, ty + th / 2), th / 2 - 1.5, th / 2 - 1.5)
        f = QFont(self.font())
        f.setPixelSize(12)
        p.setFont(f)
        p.setPen(QPen(QColor(INK if on > 0.5 else SOFT_INK), 1))
        p.drawText(QRectF(12, 0, max(10.0, tx - 22.0), h),
                   Qt.AlignRight | Qt.AlignVCenter, self.label)


# ----------------------------------------------------------------------------
# 歌词样式面板：颜色 / 字号 / 对齐
# ----------------------------------------------------------------------------
SWATCHES = ["#17181c", "#e03131", "#f08c00", "#2f9e44", "#1c7ed6",
            "#7048e8", "#c2255c", "#868e96"]


class ColorSwatch(QWidget):
    picked = Signal(str)

    def __init__(self, color, size=20, parent=None):
        super().__init__(parent)
        self.color = color
        self.setFixedSize(size + 6, size + 6)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self._on = False

    def set_on(self, v):
        self._on = bool(v)
        self.update()

    def mousePressEvent(self, e):
        self.picked.emit(self.color)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        d = min(self.width(), self.height())
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(self.color)))
        p.drawEllipse(QRectF(3, 3, d - 6, d - 6))
        if self._on:
            p.setPen(QPen(QColor(INK), 1.6))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QRectF(0.5, 0.5, d - 1, d - 1))


class LyricStylePanel(QFrame):
    """弹出面板：歌词颜色 / 字号 / 左·中·右 对齐"""
    styleChanged = Signal(dict)     # {"color","active","size","align"}

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet(f"""
            QFrame {{ background:#ffffff; border:1px solid {LINE};
                      border-radius:14px; }}
            QLabel {{ color:{SOFT_INK}; font-size:9.5pt; }}
            QPushButton {{ color:{INK}; background:#fff; border:1px solid {LINE};
                           border-radius:12px; padding:5px 10px; font-size:9.5pt; }}
            QPushButton:hover {{ color:{RED}; border:1px solid {RED}; }}
            QPushButton:checked {{ color:#fff; background:{RED}; border:1px solid {RED}; }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        self.color = SOFT_INK
        self.active = RED
        self.size = 15
        self.align = 0

        def row_label(text):
            lab = QLabel(text)
            lab.setStyleSheet(f"color:{GRAY}; font-size:8pt; letter-spacing:1px;")
            return lab

        lay.addWidget(row_label("LYRIC COLOR  ·  歌词颜色"))
        r1 = QHBoxLayout(); r1.setSpacing(6)
        self.sw_normal = []
        for c in SWATCHES:
            s = ColorSwatch(c)
            s.picked.connect(lambda col: self._pick("color", col))
            r1.addWidget(s)
            self.sw_normal.append(s)
        self.btn_custom1 = QPushButton("自定义")
        self.btn_custom1.clicked.connect(lambda: self._custom("color"))
        r1.addWidget(self.btn_custom1)
        lay.addLayout(r1)

        lay.addWidget(row_label("ACTIVE LINE  ·  当前行颜色"))
        r2 = QHBoxLayout(); r2.setSpacing(6)
        self.sw_active = []
        for c in SWATCHES:
            s = ColorSwatch(c)
            s.picked.connect(lambda col: self._pick("active", col))
            r2.addWidget(s)
            self.sw_active.append(s)
        self.btn_custom2 = QPushButton("自定义")
        self.btn_custom2.clicked.connect(lambda: self._custom("active"))
        r2.addWidget(self.btn_custom2)
        lay.addLayout(r2)

        lay.addWidget(row_label("FONT SIZE  ·  字号"))
        r3 = QHBoxLayout(); r3.setSpacing(8)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(12, 34)
        self.slider.setValue(15)
        self.slider.setStyleSheet(slider_style(150))
        self.lbl_size = QLabel("15 px")
        self.lbl_size.setFixedWidth(46)
        self.slider.valueChanged.connect(self._on_size)
        r3.addWidget(self.slider, 1)
        r3.addWidget(self.lbl_size)
        lay.addLayout(r3)

        lay.addWidget(row_label("ALIGN  ·  对齐"))
        r4 = QHBoxLayout(); r4.setSpacing(8)
        self.btn_align = []
        for i, name in enumerate(("左对齐", "居中", "右对齐")):
            b = QPushButton(name)
            b.setCheckable(True)
            b.clicked.connect(lambda c=False, idx=i: self._align(idx))
            r4.addWidget(b)
            self.btn_align.append(b)
        lay.addLayout(r4)

        tip = QLabel("设置会自动保存 · 点面板外任意处关闭")
        tip.setStyleSheet(f"color:{LIGHT}; font-size:8pt;")
        lay.addWidget(tip)

    def set_values(self, color, active, size, align):
        self.color, self.active = color or SOFT_INK, active or RED
        self.size, self.align = int(size or 15), int(align or 0)
        self.slider.setValue(self.size)
        self.lbl_size.setText(f"{self.size} px")
        for s in self.sw_normal:
            s.set_on(s.color.lower() == str(self.color).lower())
        for s in self.sw_active:
            s.set_on(s.color.lower() == str(self.active).lower())
        for i, b in enumerate(self.btn_align):
            b.setChecked(i == self.align)

    def _emit(self):
        self.styleChanged.emit({"color": self.color, "active": self.active,
                                "size": self.size, "align": self.align})

    def _pick(self, which, col):
        if which == "color":
            self.color = col
            for s in self.sw_normal:
                s.set_on(s.color == col)
        else:
            self.active = col
            for s in self.sw_active:
                s.set_on(s.color == col)
        self._emit()

    def _custom(self, which):
        from PySide6.QtWidgets import QColorDialog
        init = QColor(self.color if which == "color" else self.active)
        c = QColorDialog.getColor(init, self, "选择歌词颜色")
        if c.isValid():
            self._pick(which, c.name())

    def _on_size(self, v):
        self.size = int(v)
        self.lbl_size.setText(f"{self.size} px")
        self._emit()

    def _align(self, idx):
        self.align = idx
        for i, b in enumerate(self.btn_align):
            b.setChecked(i == idx)
        self._emit()


# ----------------------------------------------------------------------------
# 播放队列列表（右键：立即播放 / 下一首播放 / 移除）
# ----------------------------------------------------------------------------
class QueueList(QListWidget):
    playRequested = Signal(str)
    insertNextRequested = Signal(str)
    removeRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QListWidget.NoFrame)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setAlternatingRowColors(False)
        self.setSpacing(2)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.itemDoubleClicked.connect(
            lambda it: self.playRequested.emit(it.data(Qt.UserRole)))
        self.restyle()

    def restyle(self):
        """跟随当前主题重新着色：沉浸模式下 hover 只用半透明白，不留实心色块"""
        hov = ("rgba(255,255,255,16)" if theme_dark() else HOVER)
        self.setStyleSheet(f"""
            QListWidget {{ background:transparent; border:none; outline:0;
                           color:{SOFT_INK}; font-size:10.5pt; }}
            QListWidget::item {{ padding:8px 10px; border-radius:8px; }}
            QListWidget::item:hover {{ background:{hov}; color:{INK}; }}
            QListWidget::item:selected {{ background:{hov}; color:{INK}; }}
        """ + scrollbar_style())

    def _menu(self, pos):
        it = self.itemAt(pos)
        if it is None:
            return
        path = it.data(Qt.UserRole)
        if not path:
            return
        m = QMenu(self)
        m.setStyleSheet(f"""
            QMenu {{ background:#ffffff; border:1px solid {LINE}; padding:6px;
                     color:{INK}; font-size:10pt; }}
            QMenu::item {{ padding:7px 22px; border-radius:6px; }}
            QMenu::item:selected {{ background:{HOVER}; color:{RED}; }}
        """)
        a1 = m.addAction("▶  立即播放")
        a2 = m.addAction("⇥  插入到下一首播放")
        m.addSeparator()
        a3 = m.addAction("✕  从列表移除")
        act = m.exec(self.viewport().mapToGlobal(pos))
        if act == a1:
            self.playRequested.emit(path)
        elif act == a2:
            self.insertNextRequested.emit(path)
        elif act == a3:
            self.removeRequested.emit(path)

    def load_queue(self, paths, meta, current=None):
        self.clear()
        for i, p in enumerate(paths):
            m = meta.get(p, {})
            title = m.get("title") or os.path.basename(p)
            artist = m.get("artist") or ""
            it = QListWidgetItem(f"{i + 1:02d}   {title}   ·   {artist}")
            it.setData(Qt.UserRole, p)
            if p == current:
                it.setText(f"▶   {title}   ·   {artist}")
                it.setForeground(QColor(RED))
            else:
                it.setForeground(QColor(INK if p == current else SOFT_INK))
            self.addItem(it)


# ----------------------------------------------------------------------------
# 流体背景着色器
# ----------------------------------------------------------------------------
# 移植自参考软件 Johnwikix/original-sound-hq-player：
#   External/AnimatedWin2dControls/.../Shaders/FluidBackgroundEffect.cs
# 其原始 HLSL 又来自 Storyteller-Studios/Isolation 的 effect.hlsl。
#
# 核心是「噪声驱动旋转 + 双 sin 波扭曲 + 4 色平滑插值」：
#   1) 用一张二维值噪声取 time → 得到一个持续变化的旋转角（可达 ±360°），
#      整个 uv 场随之慢慢打转；
#   2) 再叠加两组 sin 波做横向/纵向扭曲，speed = time * 0.75；
#   3) 用两条 smoothstep 把封面的 4 张主色插值成"流体色块"；
# 因为旋转角与波相位都由 time 连续驱动，所以画面是**一直在流动**的，
# 而不是我们上一版那种几乎静止的色块漂移。
DEFAULT_FLUID = ("#3f6fb5", "#7a4bb8", "#b8455f", "#2f8f86")

# 4 张主色在暗色背景上的目标平均明度（对齐参考实现的 ScalePaletteLuminance）
PALETTE_TARGET_L = 0.44
# 调色板明度硬上限：背景再鲜艳也不能亮到吃掉压在它上面的浅色文字。
# 参考实现同样把配色压到暗背景区间（ScalePaletteLuminance），这里改为逐色钳制，
# 避免"平均达标就整体不压"导致个别亮色冲到 0.7 明度。
PALETTE_L_MAX = 0.52

# 流体着色器每帧的内部分辨率预算（像素数）：据此反推缩放比例，长边变化
# 时开销基本恒定。着色器优化后（去闭包、去 np.float32 装箱）单帧从
# 12.1ms 降到 10.0ms；再把预算从 120000 收到 85000（1410x790 窗口下内部
# 约 382x214），单帧 ≈ 6.8ms —— 流体本身是极平滑的色块，放大 5 倍肉眼看
# 不出差别，但帧率能从 30fps 提到 60fps。
FLUID_PIXELS = 85000


def _hue_dist(a, b):
    d = abs(int(a) - int(b)) % 360
    return min(d, 360 - d)


def shift_hue(c, amount):
    """色相偏移（amount 为 -1~1，表示 -360°~360°）"""
    h, s, v, a = c.getHsv()
    if h < 0:
        h = 0
    return QColor.fromHsv(int((h + amount * 360) % 360) % 360, s, v, a)


def _frac(x):
    """HLSL frac(x) = x - floor(x)（负数同样落在 [0,1)）"""
    return x - np.floor(x)


# F_Hash 的线性系数：哈希输入是 px/py 的线性组合，而 4 个格点角
# (ix,iy) / (ix+1,iy) / (ix,iy+1) / (ix+1,iy+1) 只是「基点 + 常数偏移」，
# 所以先算一次基点再各自加常数，省掉 3/4 的乘加与临时数组 —— 着色器是
# 纯内存带宽瓶颈，少一个 (h,w) 临时数组就省一整趟读写。
_H_C1X, _H_C2X = 2127.1, 81.17
_H_C1Y, _H_C2Y = 1269.5, 283.37
_H_MUL = 43758.5453


def _f_hash(px, py):
    """HLSL F_Hash：float2 → float2"""
    return (_frac(np.sin(px * _H_C1X + py * _H_C2X) * _H_MUL),
            _frac(np.sin(px * _H_C1Y + py * _H_C2Y) * _H_MUL))


def _f_noise(px, py):
    """HLSL F_Noise：二维值噪声，返回 [0,1]"""
    ix, iy = np.floor(px), np.floor(py)
    fx, fy = px - ix, py - iy
    ux = fx * fx * (3.0 - 2.0 * fx)
    uy = fy * fy * (3.0 - 2.0 * fy)

    # 4 个格点角就地展开（原实现用 Python 闭包 corner() 调用 4 次，每帧
    # 要多花约 1.5ms 的解释器开销）。注意：这里必须让每个角独立做完整
    # 的线性组合 —— F_Hash 对输入极端敏感（sin(大数) × 43758.5453 再取
    # frac），float32 下用「基点 + 常数增量」累积会被误差放大成完全
    # 不同的噪声值，实测像素差高达 143。
    ix1, iy1 = ix + 1.0, iy + 1.0
    hx0, hy0 = _f_hash(ix, iy)
    hx1, hy1 = _f_hash(ix1, iy)
    hx2, hy2 = _f_hash(ix, iy1)
    hx3, hy3 = _f_hash(ix1, iy1)

    fx1, fy1 = fx - 1.0, fy - 1.0
    a = (2.0 * hx0 - 1.0) * fx + (2.0 * hy0 - 1.0) * fy
    b = (2.0 * hx1 - 1.0) * fx1 + (2.0 * hy1 - 1.0) * fy
    c = (2.0 * hx2 - 1.0) * fx + (2.0 * hy2 - 1.0) * fy1
    d = (2.0 * hx3 - 1.0) * fx1 + (2.0 * hy3 - 1.0) * fy1

    nx = a + (b - a) * ux
    ny = c + (d - c) * ux
    return 0.5 + 0.5 * (nx + (ny - nx) * uy)


def _smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def fluid_frame(w, h, t, colors):
    """渲染一帧流体画面 → uint8 的 (h, w, 3) 数组。colors 为 4 组 0~1 RGB。"""
    if w < 2 or h < 2:
        return None
    xs = (np.arange(w, dtype=np.float32) + 0.5) / np.float32(w)
    ys = (np.arange(h, dtype=np.float32) + 0.5) / np.float32(h)
    ux = np.broadcast_to(xs, (h, w))
    uy = np.broadcast_to(ys[:, None], (h, w))

    ratio = np.float32(w) / np.float32(h)
    tx = ux - np.float32(0.5)
    ty = (uy - np.float32(0.5)) / ratio

    # ---- 1) 噪声驱动的整体旋转 ----
    degree = _f_noise(np.float32(t) * np.float32(0.1), tx * ty)
    ang = np.deg2rad((degree - 0.5) * 720.0 + 180.0)
    ca, sa = np.cos(ang), np.sin(ang)
    tx, ty = tx * ca - ty * sa, tx * sa + ty * ca

    # ---- 2) 双 sin 波扭曲 ----
    freq, amp = np.float32(5.0), np.float32(25.0)
    speed = np.float32(t) * np.float32(0.75)
    tx = tx + np.sin(ty * freq + speed) / amp
    ty = ty + np.sin(tx * freq * np.float32(1.5) + speed) / (amp * np.float32(0.5))
    ty = ty * ratio

    # ---- 3) 4 色平滑插值 ----
    a2 = np.deg2rad(np.float32(-5.0))
    rotx = tx * np.cos(a2) - ty * np.sin(a2)
    s1 = _smoothstep(np.float32(-0.3), np.float32(0.2), rotx)
    s2 = _smoothstep(np.float32(0.5), np.float32(-0.3), ty)

    cols = [np.asarray(c, np.float32).reshape(3, 1, 1) for c in colors]
    c1, c2, c3, c4 = cols
    layer1 = c1 + (c2 - c1) * s1
    layer2 = c3 + (c4 - c3) * s1
    fin = layer1 + (layer2 - layer1) * s2
    out = np.transpose(fin, (1, 2, 0))
    return np.clip(out * 255.0, 0, 255).astype(np.uint8)


# ---- 调色板：RGB/HSL 互转（只处理 4 个颜色，纯 Python 足够） ----
def _rgb_to_hsl(r, g, b):
    mx, mn = max(r, g, b), min(r, g, b)
    l = (mx + mn) * 0.5
    d = mx - mn
    if d < 1e-5:
        return 0.0, 0.0, l
    s = d / (2.0 - mx - mn) if l > 0.5 else d / (mx + mn)
    if mx == r:
        h = ((g - b) / d + (6.0 if g < b else 0.0)) / 6.0
    elif mx == g:
        h = ((b - r) / d + 2.0) / 6.0
    else:
        h = ((r - g) / d + 4.0) / 6.0
    return h, s, l


def _hue_to_rgb(p, q, t):
    if t < 0.0:
        t += 1.0
    if t > 1.0:
        t -= 1.0
    if t < 1.0 / 6.0:
        return p + (q - p) * 6.0 * t
    if t < 1.0 / 2.0:
        return q
    if t < 2.0 / 3.0:
        return p + (q - p) * (2.0 / 3.0 - t) * 6.0
    return p


def _hsl_to_rgb(h, s, l):
    if s < 1e-5:
        return l, l, l
    q = l * (1.0 + s) if l < 0.5 else l + s - l * s
    p = 2.0 * l - q
    return (_hue_to_rgb(p, q, h + 1.0 / 3.0),
            _hue_to_rgb(p, q, h),
            _hue_to_rgb(p, q, h - 1.0 / 3.0))


class FluidBackground(QWidget):
    """播放页背景层：封面驱动的流体着色器 / 自定义图片 / 关闭

    着色器按 1/3 左右的分辨率用 numpy 逐像素算，再平滑放大成整幅贴上去：
    流体画面本身极其平滑，降低分辨率肉眼几乎无差，帧率却能稳在 24fps 以上。
    单帧重建一旦变慢会自动拉长重建间隔，绝不拖住 UI 线程。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mode = "fluid"            # fluid / image / none
        self.image_path = ""
        self.dim = 0.22                # 整体压暗（越大越沉，保证浅色文字可读）
        self.palette = [QColor(c) for c in DEFAULT_FLUID]
        self.time = 0.0
        self._pix = QPixmap()          # 自定义背景图片
        self._src = QPixmap()          # 封面原图
        self._scene = QPixmap()        # 整幅缓存
        self._scene_key = None
        self._scene_t = -99.0
        self._last = time.monotonic()
        self._frame_dt = 1.0 / 60.0
        self._timer = QTimer(self)
        # 16ms 轮询（≈60fps）：_advance 里再用 _frame_dt 限流，单帧变慢时
        # 会自动降频；轮询间隔必须小于目标帧间隔，否则 33ms 的定时器会把
        # 帧率硬卡在 30fps（这正是之前"流体帧率低"的直接原因）。
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._advance)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    # ---------------- 对外接口 ----------------
    def set_mode(self, mode):
        self.mode = mode or "fluid"
        self._last = time.monotonic()
        self._invalidate()
        self._update_timer()
        self.update()

    def set_dim(self, v):
        """整体压暗程度 0~0.65"""
        try:
            self.dim = max(0.0, min(0.65, float(v)))
        except Exception:
            self.dim = 0.22
        self._invalidate()
        self.update()

    # 兼容旧命名
    set_veil = set_dim

    def set_image(self, path):
        """自定义背景图片；返回是否成功"""
        self.image_path = path or ""
        self._pix = QPixmap()
        ok = False
        if path and os.path.isfile(path):
            pm = QPixmap(path)
            if not pm.isNull():
                self._pix = pm
                ok = True
        self._invalidate()
        self.update()
        return ok

    def set_cover(self, image):
        """接收封面（QImage / QPixmap）→ 提取 4 张流体主色"""
        if image is None:
            return
        try:
            pm = QPixmap.fromImage(image) if isinstance(image, QImage) else image
            if isinstance(image, QPixmap) and image.isNull():
                pm = QPixmap()
            if pm.isNull():
                return
            self._src = pm
            cols = self._extract_palette(pm)
            if cols:
                self.palette = cols
            self._invalidate()
            self.update()
        except Exception:
            pass

    def has_source(self):
        """是否已有封面作为配色来源"""
        return not self._src.isNull()

    def scene_pixmap(self):
        """当前整幅背景（外部取色时用得到；无则 None）"""
        if self.mode == "none":
            return None
        w, h = self.width(), self.height()
        if w < 4 or h < 4:
            return None
        if self._scene.isNull() or self._scene_key != (w, h):
            self._rebuild_scene(w, h)
        return None if self._scene.isNull() else self._scene

    # ---------------- 内部实现 ----------------
    def _invalidate(self):
        self._scene = QPixmap()
        self._scene_key = None
        self._scene_t = -99.0

    def _update_timer(self):
        run = (self.mode == "fluid" and self.isVisible() and self.width() > 4)
        if run and not self._timer.isActive():
            self._last = time.monotonic()
            self._timer.start()
        elif not run and self._timer.isActive():
            self._timer.stop()

    def _advance(self):
        now = time.monotonic()
        dt = now - self._last
        self._last = now
        if dt <= 0.0:
            dt = 0.033
        self.time = (self.time + min(0.12, dt)) % 1800.0
        if self.time - self._scene_t >= self._frame_dt:
            self.update()

    def showEvent(self, e):
        super().showEvent(e)
        self._last = time.monotonic()
        self._update_timer()

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    def resizeEvent(self, e):
        self._invalidate()
        super().resizeEvent(e)

    # ---------------- 调色板 ----------------
    @classmethod
    def _extract_palette(cls, pm, want=4):
        """从封面挑 4 枚色相分散的主色，并压到暗背景可用的明度"""
        try:
            small = pm.scaled(24, 24, Qt.AspectRatioMode.IgnoreAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
            img = small.toImage().convertToFormat(QImage.Format_RGB32)
        except Exception:
            return [QColor(c) for c in DEFAULT_FLUID]
        cand = []
        for y in range(img.height()):
            for x in range(img.width()):
                c = QColor(img.pixel(x, y))
                hh, ss, ll = _rgb_to_hsl(c.redF(), c.greenF(), c.blueF())
                if ll < 0.08 or ll > 0.94:      # 纯黑 / 纯白留白
                    continue
                cand.append((hh, ss, ll))
        picked = []
        for hh, ss, ll in sorted(cand, key=lambda v: -v[1]):
            if ss < 0.14:                       # 太灰的不要
                continue
            if all(_hue_dist(hh * 360, p[0] * 360) >= 40 for p in picked):
                picked.append((hh, ss, ll))
                if len(picked) >= want:
                    break
        if len(picked) < 2:
            return [QColor(c) for c in DEFAULT_FLUID]
        base = list(picked)
        k = 0
        while len(picked) < want:               # 色相旋转补足 4 枚，保证层次
            hh, ss, ll = base[k % len(base)]
            step = 0.11 * (1 + k // len(base)) * (1 if k % 2 else -1)
            picked.append(((hh + step) % 1.0, ss, max(0.35, ll)))
            k += 1
        cols = [_hsl_to_rgb(*p) for p in picked[:want]]
        return [QColor.fromRgbF(*c) for c in cls._scale_luminance(cols)]

    @staticmethod
    def _scale_luminance(cols, target=PALETTE_TARGET_L, lmax=PALETTE_L_MAX):
        """把配色整体移向暗背景目标值，并逐色钳到明度上限。

        与参考实现的区别：不做"均值达标就整体跳过"的短路 —— 那样会让个别
        亮色保持 0.7 明度，压暗后仍是浅色文字读不清的亮底。这里对每一色都
        施加硬上限，保证流体背景在任何封面配色下都足够暗。
        """
        hs = [_rgb_to_hsl(*c) for c in cols]
        avg = sum(v[2] for v in hs) / max(1, len(hs))
        shift = target - avg
        out = []
        for (h, s, l), c in zip(hs, cols):
            nl = l + shift if l >= 0.30 else l     # 已经很暗的不再往下压
            nl = max(0.0, min(lmax, nl))
            out.append(_hsl_to_rgb(h, s, nl))
        return out

    # ---------------- 绘制 ----------------
    def _fluid_cols(self):
        cols = [(c.redF(), c.greenF(), c.blueF()) for c in self.palette[:4]]
        while len(cols) < 4:
            cols.append(cols[-1] if cols else (0.3, 0.3, 0.3))
        return cols

    def _paint_fluid(self, p, w, h):
        # 内部按固定像素预算渲染再平滑放大：流体画面极其平滑，降分辨率肉眼
        # 几乎无差，但 numpy 单帧能压到 ~10ms（实测 480x270 ≈ 11ms）
        scale = min(1.0, math.sqrt(FLUID_PIXELS / max(1.0, w * h)))
        iw = max(80, int(w * scale))
        ih = max(60, int(h * scale))
        arr = fluid_frame(iw, ih, self.time, self._fluid_cols())
        if arr is None:
            return
        img = QImage(arr.tobytes(), iw, ih, iw * 3,
                     QImage.Format_RGB888).copy()
        pm = QPixmap.fromImage(img)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.drawPixmap(QRectF(0, 0, w, h), pm, QRectF(pm.rect()))

    def _paint_scene(self, p, w, h):
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        if self.mode == "image":
            if not self._pix.isNull():
                sc = self._pix.size()
                s = max(w / max(1, sc.width()), h / max(1, sc.height()))
                tw, th = int(sc.width() * s + 0.5), int(sc.height() * s + 0.5)
                p.setRenderHint(QPainter.SmoothPixmapTransform, True)
                p.drawPixmap(int((w - tw) / 2), int((h - th) / 2), tw, th,
                             self._pix)
            else:
                p.fillRect(QRectF(0, 0, w, h), QColor(IMM["BG"]))
        elif self.mode == "fluid":
            self._paint_fluid(p, w, h)
        else:
            return
        # 压暗：让浅色文字在任何封面配色上都站得住
        if self.dim > 0.001:
            p.fillRect(QRectF(0, 0, w, h),
                       QColor(6, 8, 12, int(self.dim * 255)))

    def _rebuild_scene(self, w, h):
        if w < 4 or h < 4:
            return
        pm = QPixmap(w, h)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        t0 = time.monotonic()
        try:
            self._paint_scene(p, w, h)
        finally:
            p.end()
        self._scene = pm
        self._scene_key = (w, h)
        self._scene_t = self.time
        # 自适应降频：这一帧花了多久，下一帧就至少隔 1.6 倍，宁可掉帧不卡
        # UI；下限 1/60 让它有成本余量时能跑满 60fps
        cost = time.monotonic() - t0
        self._frame_dt = min(0.30, max(1.0 / 60.0, cost * 1.6))

    def paintEvent(self, e):
        if self.mode == "none":
            return
        w, h = self.width(), self.height()
        if w < 4 or h < 4:
            return
        if (self._scene.isNull() or self._scene_key != (w, h)
                or (self.mode == "fluid"
                    and self.time - self._scene_t >= self._frame_dt)):
            self._rebuild_scene(w, h)
        if self._scene.isNull():
            return
        p = QPainter(self)
        p.drawPixmap(0, 0, self._scene)


# ----------------------------------------------------------------------------
# 氛围特效层：飘雪 / 下雨（覆盖整个播放页，鼠标穿透）
# ----------------------------------------------------------------------------
class WeatherOverlay(QWidget):
    """氛围特效层：飘雪 / 下雨

    - 雪：三层景深的柔光 bokeh 雪花（预渲染精灵，不再逐帧画渐变）
    - 雨：预渲染斜向渐变雨丝 + 落地涟漪 + 地面水雾
    - 粒子全部走「精灵 + setOpacity」，33fps 开销很低
    """
    RAIN_SLANT = 0.22

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mode = "none"          # none / snow / rain
        self.parts = []
        self.ripples = []
        self.wind = 0.0
        self._sprites = {}
        self._rnd = random.Random()
        self._last_t = time.monotonic()
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._advance)

    # ---------------- 对外接口 ----------------
    def set_mode(self, mode):
        mode = mode or "none"
        if mode == self.mode and self.parts:
            return
        self.mode = mode
        self._last_t = time.monotonic()
        self.ripples = []
        self._reseed()
        if mode != "none" and self.isVisible():
            self._timer.start()
        else:
            self._timer.stop()
        self.update()

    # ---------------- 预渲染精灵 ----------------
    def _snow_sprite(self, r):
        key = ("s", int(round(r * 4)))
        pm = self._sprites.get(key)
        if pm is not None:
            return pm
        d = max(5, int(round(r * 6.4)))
        pm = QPixmap(d, d)
        pm.fill(Qt.transparent)
        pp = QPainter(pm)
        pp.setRenderHint(QPainter.Antialiasing)
        c = d / 2.0
        g = QRadialGradient(c, c, c)
        g.setColorAt(0.00, QColor(255, 255, 255, 255))
        g.setColorAt(0.38, QColor(253, 254, 255, 244))
        g.setColorAt(0.58, QColor(228, 238, 252, 184))
        g.setColorAt(0.76, QColor(180, 200, 232, 108))
        g.setColorAt(0.91, QColor(164, 186, 222, 30))
        g.setColorAt(1.00, QColor(164, 186, 222, 0))
        pp.setPen(Qt.NoPen)
        pp.setBrush(QBrush(g))
        pp.drawEllipse(QRectF(0, 0, d, d))
        pp.end()
        self._sprites[key] = pm
        return pm

    def _rain_sprite(self, length, width):
        lq = int(round(length))
        wq = max(0.5, round(width * 2) / 2.0)
        key = ("r", lq, int(wq * 2))
        pm = self._sprites.get(key)
        if pm is not None:
            return pm
        wl, wd = max(1.0, float(lq)), max(0.8, float(wq))
        dx = wl * self.RAIN_SLANT
        sw = int(dx + wd * 4 + 5)
        sh = int(wl + wd * 4 + 5)
        pm = QPixmap(max(2, sw), max(2, sh))
        pm.fill(Qt.transparent)
        pp = QPainter(pm)
        pp.setRenderHint(QPainter.Antialiasing)
        x0, y0 = wd * 2 + 1.0, wd * 2 + 1.0
        x1, y1 = x0 + dx, y0 + wl
        # 1) 外层：雨滴划过留下的湿痕（柔光）
        gh = QLinearGradient(x0, y0, x1, y1)
        gh.setColorAt(0.00, QColor(196, 220, 248, 0))
        gh.setColorAt(0.45, QColor(198, 222, 250, 76))
        gh.setColorAt(1.00, QColor(196, 220, 248, 0))
        pen = QPen(QBrush(gh), wd * 2.4)
        pen.setCapStyle(Qt.RoundCap)
        pp.setPen(pen)
        pp.drawLine(QPointF(x0, y0), QPointF(x1, y1))
        # 2) 内层：深蓝灰雨丝，浅色背景上清晰可辨
        g = QLinearGradient(x0, y0, x1, y1)
        g.setColorAt(0.00, QColor(116, 148, 190, 0))
        g.setColorAt(0.24, QColor(104, 136, 180, 168))
        g.setColorAt(0.64, QColor(64, 96, 146, 244))
        g.setColorAt(0.90, QColor(96, 128, 174, 152))
        g.setColorAt(1.00, QColor(116, 148, 190, 0))
        pen = QPen(QBrush(g), wd)
        pen.setCapStyle(Qt.RoundCap)
        pp.setPen(pen)
        pp.drawLine(QPointF(x0, y0), QPointF(x1, y1))
        pp.end()
        self._sprites[key] = pm
        return pm

    # ---------------- 粒子 ----------------
    def _reseed(self):
        w, h = max(1, self.width()), max(1, self.height())
        rnd = self._rnd
        if self.mode == "snow":
            parts = []
            layers = ((58, (0.9, 1.8), (10, 24), (0.42, 0.66), 10),
                      (36, (1.9, 3.1), (24, 44), (0.58, 0.86), 18),
                      (16, (3.4, 6.0), (46, 74), (0.72, 1.00), 26))
            for cnt, (r0, r1), (v0, v1), (a0, a1), sway in layers:
                for _ in range(cnt):
                    parts.append({"x": rnd.uniform(0, w),
                                  "y": rnd.uniform(-h, h),
                                  "r": rnd.uniform(r0, r1),
                                  "v": rnd.uniform(v0, v1),
                                  "a": rnd.uniform(a0, a1),
                                  "ph": rnd.uniform(0, 6.2832),
                                  "sw": sway * rnd.uniform(0.7, 1.3)})
            self.parts = parts
        elif self.mode == "rain":
            parts = []
            layers = ((100, (8, 15), (0.9, 1.3), (360, 520), (0.34, 0.56)),
                      (62, (17, 34), (1.4, 2.4), (600, 900), (0.58, 1.00)))
            for cnt, (l0, l1), (w0, w1), (v0, v1), (a0, a1) in layers:
                for _ in range(cnt):
                    parts.append({"x": rnd.uniform(-60, w + 60),
                                  "y": rnd.uniform(-h, h),
                                  "l": rnd.uniform(l0, l1),
                                  "wd": rnd.uniform(w0, w1),
                                  "v": rnd.uniform(v0, v1),
                                  "a": rnd.uniform(a0, a1)})
            self.parts = parts
        else:
            self.parts = []

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.mode != "none":
            self._reseed()

    def showEvent(self, e):
        super().showEvent(e)
        if self.mode != "none":
            if not self.parts:
                self._reseed()
            self._timer.start()

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    def _advance(self):
        now = time.monotonic()
        dt = now - self._last_t
        self._last_t = now
        if dt <= 0.0:
            dt = 0.033
        dt = min(0.08, dt)
        w, h = max(1, self.width()), max(1, self.height())
        self.wind = math.sin(now * 0.19) * 26.0
        snow = self.mode == "snow"
        for pt in self.parts:
            pt["y"] += pt["v"] * dt
            if snow:
                pt["x"] += (math.sin(pt["y"] * 0.014 + pt["ph"]) * pt["sw"]
                            + self.wind) * dt
                if pt["y"] > h + 10:
                    pt["y"] = -12.0
                    pt["x"] = self._rnd.uniform(-20, w + 20)
            else:
                pt["x"] += pt["v"] * self.RAIN_SLANT * dt
                if pt["y"] > h + 8:
                    if pt["a"] > 0.40 and len(self.ripples) < 56:
                        # 落点略作纵向抖动，避免所有涟漪排成整齐一条线
                        self.ripples.append({
                            "x": pt["x"],
                            "y": h - 22.0 + self._rnd.uniform(-11.0, 3.0),
                            "t": 0.0, "a": pt["a"],
                            "sq": self._rnd.uniform(0.72, 1.22)})
                    pt["y"] = -pt["l"] - 4.0
                    pt["x"] = self._rnd.uniform(-60, w + 60)
            if pt["x"] > w + 80:
                pt["x"] = -80.0
            elif pt["x"] < -80:
                pt["x"] = w + 80.0
        if self.ripples:
            alive = []
            for rp in self.ripples:
                rp["t"] += dt
                if rp["t"] < 0.62:
                    alive.append(rp)
            self.ripples = alive
        self.update()

    def paintEvent(self, e):
        if self.mode == "none" or not self.parts:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        w, h = self.width(), self.height()

        if self.mode == "snow":
            for pt in self.parts:
                pm = self._snow_sprite(pt["r"])
                d = pm.width()
                p.setOpacity(pt["a"])
                p.drawPixmap(QRectF(pt["x"] - d / 2.0, pt["y"] - d / 2.0, d, d),
                             pm, QRectF(pm.rect()))
            p.setOpacity(1.0)
            # 地面寒气
            gh = h * 0.16
            g = QLinearGradient(0, h, 0, h - gh)
            g.setColorAt(0.0, QColor(150, 176, 214, 44))
            g.setColorAt(1.0, QColor(150, 176, 214, 0))
            p.fillRect(QRectF(0, h - gh, w, gh), QBrush(g))
        else:
            for pt in self.parts:
                pm = self._rain_sprite(pt["l"], pt["wd"])
                p.setOpacity(pt["a"])
                p.drawPixmap(QPointF(pt["x"], pt["y"]), pm)
            p.setOpacity(1.0)
            # 落地涟漪：双环 + 先快后慢扩散，读起来像水面溅落而非"气泡"
            p.setBrush(Qt.NoBrush)
            for rp in self.ripples:
                t = min(1.0, rp["t"] / 0.62)
                e = 1.0 - (1.0 - t) ** 2
                r = 3.0 + e * 42.0
                a = ((1.0 - t) ** 1.6) * 168 * rp["a"]
                if a < 4:
                    continue
                sq = rp.get("sq", 1.0)
                p.setPen(QPen(QColor(92, 128, 178, int(a)), 1.3))
                p.drawEllipse(QRectF(rp["x"] - r, rp["y"] - r * 0.24 * sq,
                                     r * 2, r * 0.48 * sq))
                p.setPen(QPen(QColor(154, 188, 228, int(a * 0.5)), 1.0))
                p.drawEllipse(QRectF(rp["x"] - r * 0.56,
                                     rp["y"] - r * 0.14 * sq,
                                     r * 1.12, r * 0.28 * sq))
            # 地面水雾
            gh = h * 0.14
            g = QLinearGradient(0, h, 0, h - gh)
            g.setColorAt(0.0, QColor(126, 156, 196, 54))
            g.setColorAt(1.0, QColor(126, 156, 196, 0))
            p.fillRect(QRectF(0, h - gh, w, gh), QBrush(g))
# ----------------------------------------------------------------------------
# 背景设置面板：模式 / 自定义图片 / 纱罩浓度 / 氛围特效
# ----------------------------------------------------------------------------
class BackgroundPanel(QFrame):
    bgChanged = Signal(dict)        # {bg_mode, bg_image, bg_veil, weather}
    imagePickRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet(f"""
            QFrame {{ background:#ffffff; border:1px solid {LINE};
                      border-radius:14px; }}
            QLabel {{ color:{SOFT_INK}; font-size:9.5pt; }}
            QPushButton {{ color:{INK}; background:#fff; border:1px solid {LINE};
                           border-radius:12px; padding:5px 10px; font-size:9.5pt; }}
            QPushButton:hover {{ color:{RED}; border:1px solid {RED}; }}
            QPushButton:checked {{ color:#fff; background:{RED};
                                   border:1px solid {RED}; }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        self.bg_mode = "fluid"
        self.image = ""
        self.veil = 0.22
        self.weather = "none"

        def row_label(text):
            lab = QLabel(text)
            lab.setStyleSheet(f"color:{GRAY}; font-size:8pt; letter-spacing:1px;")
            return lab

        lay.addWidget(row_label("BACKGROUND  ·  播放页背景"))
        r0 = QHBoxLayout(); r0.setSpacing(8)
        self.btn_mode = []
        for key, name in (("fluid", "流体幻彩"), ("image", "自定义图片"),
                          ("none", "纯净白")):
            b = QPushButton(name)
            b.setCheckable(True)
            b.clicked.connect(lambda c=False, k=key: self._mode(k))
            r0.addWidget(b)
            self.btn_mode.append((key, b))
        lay.addLayout(r0)

        self.btn_pick = QPushButton("选择图片…")
        self.btn_pick.clicked.connect(self.imagePickRequested.emit)
        self.lbl_img = QLabel("未选择")
        self.lbl_img.setStyleSheet(f"color:{LIGHT}; font-size:8.5pt;")
        self.lbl_img.setWordWrap(True)
        r_img = QHBoxLayout(); r_img.setSpacing(8)
        r_img.addWidget(self.btn_pick)
        r_img.addWidget(self.lbl_img, 1)
        lay.addLayout(r_img)

        lay.addWidget(row_label("VEIL  ·  背景纱罩浓度"))
        r_v = QHBoxLayout(); r_v.setSpacing(8)
        self.slider_veil = QSlider(Qt.Horizontal)
        self.slider_veil.setRange(0, 75)
        self.slider_veil.setValue(22)
        self.slider_veil.setStyleSheet(slider_style(150))
        self.lbl_veil = QLabel("22 %")
        self.lbl_veil.setFixedWidth(46)
        self.slider_veil.valueChanged.connect(self._on_veil)
        r_v.addWidget(self.slider_veil, 1)
        r_v.addWidget(self.lbl_veil)
        lay.addLayout(r_v)

        lay.addWidget(row_label("AMBIENT  ·  氛围特效"))
        r_w = QHBoxLayout(); r_w.setSpacing(8)
        self.btn_weather = []
        for key, name in (("none", "关闭"), ("snow", "飘雪"), ("rain", "下雨")):
            b = QPushButton(name)
            b.setCheckable(True)
            b.clicked.connect(lambda c=False, k=key: self._weather(k))
            r_w.addWidget(b)
            self.btn_weather.append((key, b))
        lay.addLayout(r_w)

        tip = QLabel("设置会自动保存 · 点面板外任意处关闭")
        tip.setStyleSheet(f"color:{LIGHT}; font-size:8pt;")
        lay.addWidget(tip)

    def set_values(self, bg_mode="fluid", image="", veil=0.22, weather="none"):
        self.bg_mode = bg_mode or "fluid"
        self.image = image or ""
        self.weather = weather or "none"
        try:
            self.veil = float(veil)
        except Exception:
            self.veil = 0.22
        self.slider_veil.setValue(int(round(self.veil * 100)))
        self.lbl_veil.setText(f"{int(round(self.veil * 100))} %")
        name = os.path.basename(self.image) if self.image else "未选择"
        self.lbl_img.setText(name)
        for k, b in self.btn_mode:
            b.setChecked(k == self.bg_mode)
        for k, b in self.btn_weather:
            b.setChecked(k == self.weather)

    def set_image(self, path):
        self.image = path or ""
        self.lbl_img.setText(os.path.basename(self.image) if self.image
                             else "未选择")
        self._emit()

    def _emit(self):
        self.bgChanged.emit({"bg_mode": self.bg_mode, "bg_image": self.image,
                             "bg_veil": self.veil, "weather": self.weather})

    def _mode(self, key):
        self.bg_mode = key
        for k, b in self.btn_mode:
            b.setChecked(k == key)
        self._emit()

    def _weather(self, key):
        self.weather = key
        for k, b in self.btn_weather:
            b.setChecked(k == key)
        self._emit()

    def _on_veil(self, v):
        self.veil = int(v) / 100.0
        self.lbl_veil.setText(f"{int(v)} %")
        self._emit()


# ----------------------------------------------------------------------------
# 无边框窗口自由缩放
# ----------------------------------------------------------------------------
class ResizeFilter(QObject):
    L, R, T, B = 1, 2, 4, 8
    CURSORS = {
        L: Qt.SizeHorCursor, R: Qt.SizeHorCursor,
        T: Qt.SizeVerCursor, B: Qt.SizeVerCursor,
        L | T: Qt.SizeFDiagCursor, R | B: Qt.SizeFDiagCursor,
        T | R: Qt.SizeBDiagCursor, B | L: Qt.SizeBDiagCursor,
    }

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.active = False
        self.edges = 0
        self.start_geo = None
        self.start_pos = None
        self._cursor = None

    def _set_cursor(self, c):
        # 缓存避免每个 MouseMove 都 setCursor（应用级过滤器会放大成整窗重绘）
        if self._cursor != c:
            self._cursor = c
            self.win.setCursor(c)

    def _edge_at(self, gpos):
        if self.win.isMaximized():
            return 0
        g = self.win.frameGeometry()
        m = 7
        e = 0
        if gpos.x() - g.left() <= m:
            e |= self.L
        if g.right() - gpos.x() <= m:
            e |= self.R
        if gpos.y() - g.top() <= m:
            e |= self.T
        if g.bottom() - gpos.y() <= m:
            e |= self.B
        return e

    def eventFilter(self, obj, ev):
        et = ev.type()
        if not self.active and not self.win.updatesEnabled():
            return False            # 标题栏拖动进行中，不参与
        if et == QEvent.MouseMove and not self.active \
                and ev.button() == Qt.NoButton:
            e = self._edge_at(ev.globalPosition().toPoint())
            self._set_cursor(self.CURSORS.get(e, Qt.ArrowCursor))
            return False
        if et == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton:
            e = self._edge_at(ev.globalPosition().toPoint())
            if e:
                self.active = True
                self.edges = e
                self.start_geo = self.win.geometry()
                self.start_pos = ev.globalPosition().toPoint()
                return True
        elif self.active and et == QEvent.MouseMove:
            self._apply(ev.globalPosition().toPoint())
            return True
        elif self.active and et == QEvent.MouseButtonRelease:
            self.active = False
            return True
        return False

    def _apply(self, gpos):
        g = self.start_geo
        x, y, w, h = g.x(), g.y(), g.width(), g.height()
        dx = gpos.x() - self.start_pos.x()
        dy = gpos.y() - self.start_pos.y()
        e = self.edges
        minw, minh = self.win.minimumWidth(), self.win.minimumHeight()
        if e & self.L:
            nx, nw = x + dx, w - dx
            if nw < minw:
                nw, nx = minw, x + w - minw
            x, w = nx, nw
        if e & self.R:
            w = max(minw, w + dx)
        if e & self.T:
            ny, nh = y + dy, h - dy
            if nh < minh:
                nh, ny = minh, y + h - minh
            y, h = ny, nh
        if e & self.B:
            h = max(minh, h + dy)
        self.win.setGeometry(x, y, w, h)


# ----------------------------------------------------------------------------
# 通用极简控件样式：胶囊按钮 / 音量滑杆 / 滚动条
# （滑杆与滚动条显式定义全部子部件，避免 Qt 用系统强调色（蓝）兜底绘制）
# ----------------------------------------------------------------------------
def pill_button(text, parent=None):
    b = QPushButton(text, parent)
    b.setCursor(QCursor(Qt.PointingHandCursor))
    b.setStyleSheet(f"""
        QPushButton {{ color:{INK}; background:#ffffff; border:1px solid {LINE};
                        border-radius:16px; padding:7px 18px; font-size:9.5pt; }}
        QPushButton:hover {{ color:{RED}; border:1px solid {RED}; }}
        QPushButton:pressed {{ background:{HOVER}; }}
    """)
    return b


def slider_style(width=96):
    return f"""
        QSlider {{ background:transparent; }}
        QSlider::groove:horizontal {{ height:2px; background:{LINE}; border-radius:1px; }}
        QSlider::sub-page:horizontal {{ height:2px; background:{INK}; border-radius:1px; }}
        QSlider::add-page:horizontal {{ height:2px; background:{LINE}; border-radius:1px; }}
        QSlider::handle:horizontal {{ width:11px; height:11px; margin:-5px 0;
                                      border-radius:5px; background:{INK}; }}
        QSlider::handle:horizontal:hover {{ background:{RED}; }}
    """


def slider_style_glass(width=96):
    """沉浸（深色背景）上的滑杆：半透明白轨道，静止时也不出现任何实心底"""
    return f"""
        QSlider {{ background:transparent; }}
        QSlider::groove:horizontal {{ height:2px; background:rgba(255,255,255,20%);
                                     border-radius:1px; }}
        QSlider::sub-page:horizontal {{ height:2px; background:rgba(255,255,255,88%);
                                        border-radius:1px; }}
        QSlider::add-page:horizontal {{ height:2px; background:rgba(255,255,255,20%);
                                        border-radius:1px; }}
        QSlider::handle:horizontal {{ width:11px; height:11px; margin:-5px 0;
                                      border-radius:5px; background:#ffffff; }}
        QSlider::handle:horizontal:hover {{ background:{RED}; }}
    """


def scrollbar_style():
    return f"""
        QScrollBar:vertical {{ background:transparent; width:10px; margin:0; }}
        QScrollBar::handle:vertical {{ background:{LIGHT}; border-radius:5px;
                                       min-height:36px; }}
        QScrollBar::handle:vertical:hover {{ background:{GRAY}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background:transparent; }}
        QScrollBar:horizontal {{ background:transparent; height:10px; margin:0; }}
        QScrollBar::handle:horizontal {{ background:{LIGHT}; border-radius:5px;
                                         min-width:36px; }}
        QScrollBar::handle:horizontal:hover {{ background:{GRAY}; }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width:0; }}
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background:transparent; }}
    """


# ----------------------------------------------------------------------------
# 圆角封面图标工具
# ----------------------------------------------------------------------------
# 同一张封面（同一尺寸 / 圆角）在「扫描批次重建」「排序」「过滤」里会被反复
# 请求，每次都做一次缩放 + 抗锯齿圆角裁剪非常昂贵（600 首 ≈ 95ms）。这里按
# QImage.cacheKey() 缓存结果，重复请求直接命中。
_ROUND_CACHE = {}
_ROUND_CACHE_MAX = 600


def rounded_pixmap(img, size, radius=None):
    from PySide6.QtGui import QPainter as QP, QPixmap
    key = None
    try:
        key = (int(img.cacheKey()), int(size), radius)
    except Exception:
        key = None
    if key is not None:
        hit = _ROUND_CACHE.get(key)
        if hit is not None:
            return hit
    pm = QPixmap.fromImage(img) if hasattr(img, "isNull") else QPixmap()
    if pm.isNull():
        pm = QPixmap(size, size)
        pm.fill(QColor(ART_BG))
        p = QP(pm)
        p.setRenderHint(QP.Antialiasing)
        p.setPen(QPen(QColor(GRAY), 1))
        f = QFont()
        f.setPixelSize(int(size * 0.4))
        p.setFont(f)
        p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, "♪")
        p.end()
    pm = pm.scaled(size, size, Qt.KeepAspectRatioByExpanding,
                   Qt.SmoothTransformation)
    sx = max(0, (pm.width() - size) / 2)
    sy = max(0, (pm.height() - size) / 2)
    pm = pm.copy(int(sx), int(sy), size, size)
    radius = radius if radius is not None else max(6, size // 6)
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    p = QP(out)
    p.setRenderHint(QP.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
    p.setClipPath(path)
    p.drawPixmap(0, 0, pm)
    p.end()
    if key is not None:
        if len(_ROUND_CACHE) >= _ROUND_CACHE_MAX:
            _ROUND_CACHE.clear()          # 简单粗暴但足够：清空后重新积累
        _ROUND_CACHE[key] = out
    return out


# ----------------------------------------------------------------------------
# 软件图标
# ----------------------------------------------------------------------------
# 与 _mkeico.py 共用同一份绘制逻辑：程序运行时用代码画 QIcon（不依赖外部
# 文件，onefile 打包后也不会丢），打包 exe 时由 _mkeico.py 导出的 icon.ico
# 提供资源图标。
def app_icon_pixmap(size):
    """按尺寸绘制应用图标：圆角底 + 流体四色渐变 + 白色频谱柱"""
    src = float(size)
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)

    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, src, src), src * 0.225, src * 0.225)
    g = QLinearGradient(0, 0, src, src)
    g.setColorAt(0.00, QColor("#2f5fa8"))
    g.setColorAt(0.48, QColor("#7a4bb8"))
    g.setColorAt(1.00, QColor("#c04a5e"))
    p.fillPath(path, QBrush(g))

    hl = QLinearGradient(0, 0, 0, src * 0.55)
    hl.setColorAt(0.0, QColor(255, 255, 255, 48))
    hl.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.fillPath(path, QBrush(hl))

    p.setClipPath(path)
    p.setPen(Qt.NoPen)
    bars = (0.34, 0.60, 0.88, 0.52, 0.74)
    bw = src * 0.098
    gap = src * 0.052
    total = len(bars) * bw + (len(bars) - 1) * gap
    x0 = (src - total) * 0.5
    base = src * 0.795
    hmax = src * 0.545
    for i, hf in enumerate(bars):
        h = hmax * hf
        rr = bw * 0.5
        p.setBrush(QColor(255, 255, 255, 242 if i == 2 else 216))
        p.drawRoundedRect(QRectF(x0 + i * (bw + gap), base - h, bw, h), rr, rr)
    lh = max(1.0, src * 0.022)
    p.setBrush(QColor(255, 255, 255, 118))
    p.drawRoundedRect(QRectF(x0, base + src * 0.038, total, lh),
                      lh * 0.5, lh * 0.5)
    p.end()
    return pm


def app_icon():
    """多尺寸 QIcon（窗口 / 任务栏 / Alt-Tab 都用得上）"""
    from PySide6.QtGui import QIcon
    ic = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        ic.addPixmap(app_icon_pixmap(s))
    return ic
