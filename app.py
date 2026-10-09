# -*- coding: utf-8 -*-
"""
AudioScope 声波观测台 —— 本地音乐播放器 + 音频可视化
雪白极简风格 · 左侧导航（首页/所有歌曲/专辑/艺术家/最近播放）+ 底部播放条
"""
import sys
import os
import time
import random as _random

import numpy as np

from PySide6.QtCore import (Qt, QTimer, QThread, Signal, QPoint, QObject,
                            QPropertyAnimation, QEasingCurve, QEvent, QRectF)
from PySide6.QtGui import (QColor, QFont, QPixmap, QCursor, QIcon, QPalette,
                           QPainter, QPainterPath, QPen, QBrush, QImage)
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                               QLabel, QPushButton, QSlider, QFileDialog,
                               QMessageBox, QStackedWidget, QGraphicsOpacityEffect,
                               QSizePolicy)

from core import (BG, PANEL, INK, SOFT_INK, GRAY, LIGHT, LINE, HOVER, RED,
                  ART_BG, SR, fmt_time, fmt_total, AudioEngine, MicEngine,
                  LoadThread, Store, LibraryThread, parse_lrc, find_local_lrc,
                  split_filename, LyricsThread, CoverThread)
from widgets import (CircleButton, MiniTimeline, ResizeFilter, rounded_pixmap,
                     slider_style, scrollbar_style, app_icon)
from pages import HomePage, SongsPage, GridPage, DetailPage, PlayerPage

APP_TITLE = "AudioScope  ·  声波观测台"

PAGE_HOME, PAGE_SONGS, PAGE_ALBUMS, PAGE_ARTISTS, PAGE_RECENT, PAGE_PLAYER, PAGE_DETAIL = range(7)


# ----------------------------------------------------------------------------
# 标题栏
# ----------------------------------------------------------------------------
class TitleBar(QWidget):
    folderClicked = Signal()
    micToggled = Signal()

    def __init__(self, window):
        super().__init__(window)
        self.win = window
        self._drag = None      # 已开始拖动时的光标偏移
        self._press = None     # 按下位置（用于判断是否越过拖动阈值）
        self.setFixedHeight(44)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 0, 8, 0)
        lay.setSpacing(8)

        dot = QLabel("●")
        dot.setStyleSheet(f"color:{RED}; font-size:9px;")
        title = QLabel(APP_TITLE)
        title.setStyleSheet(f"color:{INK}; font-size:13px; font-weight:600; letter-spacing:1px;")

        self.btn_folder = self._tool_btn("打开文件夹")
        self.btn_mic = self._tool_btn("麦克风监控")
        self.btn_mic.setCheckable(True)

        self.btn_min = self._win_btn("—")
        self.btn_max = self._win_btn("□")
        self.btn_close = self._win_btn("✕", hover="#e03131")

        lay.addWidget(dot)
        lay.addWidget(title)
        lay.addSpacing(18)
        lay.addWidget(self.btn_folder)
        lay.addWidget(self.btn_mic)
        lay.addStretch(1)
        lay.addWidget(self.btn_min)
        lay.addWidget(self.btn_max)
        lay.addWidget(self.btn_close)

    def _tool_btn(self, text):
        b = QPushButton(text)
        b.setCursor(QCursor(Qt.PointingHandCursor))
        b.setStyleSheet(f"""
            QPushButton {{ color:{SOFT_INK}; background:transparent; border:1px solid transparent;
                          padding:5px 10px; font-size:12px; border-radius:4px; }}
            QPushButton:hover {{ color:{RED}; border:1px solid {RED}; }}
            QPushButton:checked {{ color:#fff; background:{RED}; border:1px solid {RED}; }}
        """)
        return b

    def _win_btn(self, sym, hover="#d9dadd"):
        b = QPushButton(sym)
        b.setFixedSize(34, 28)
        b.setCursor(QCursor(Qt.PointingHandCursor))
        b.setStyleSheet(f"""
            QPushButton {{ color:{SOFT_INK}; background:transparent; border:none;
                           font-size:12px; border-radius:4px; }}
            QPushButton:hover {{ background:{hover}; color:white; }}
        """)
        return b

    # ---- 拖动窗口：最大化时先一次性还原再跟手，避免每帧反复「还原→重排」导致闪屏 ----
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = e.globalPosition().toPoint()
            self._drag = None

    def _begin_drag(self, gpos):
        w = self.win
        if w.isMaximized():
            fg = w.frameGeometry()
            rel = (gpos.x() - fg.left()) / max(1, fg.width())
            ng = w.normalGeometry()
            off = int(max(1, ng.width()) * min(max(rel, 0.0), 1.0))
            w.begin_gesture()          # 期间冻结重绘，杜绝中间帧撕裂
            w.showNormal()             # 只做这一次的还原（而非每个 move 都触发）
            w.move(gpos.x() - off, max(0, gpos.y() - 18))
            w.end_gesture()
            self._drag = QPoint(off, 18)
        else:
            self._drag = gpos - w.frameGeometry().topLeft()
        self._press = None
        # 交给系统接管拖动：由 OS 合成，比 Qt 自绘 move 平滑得多
        try:
            wh = w.windowHandle()
            if wh is not None and wh.startSystemMove():
                self._drag = None
        except Exception:
            pass

    def mouseMoveEvent(self, e):
        if self._press is not None:
            g = e.globalPosition().toPoint()
            if (g - self._press).manhattanLength() >= 4:
                self._begin_drag(g)
        elif self._drag is not None and e.buttons() & Qt.LeftButton:
            self.win.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None
        self._press = None

    def mouseDoubleClickEvent(self, e):
        if self.win.isMaximized():
            self.win.showNormal()
        else:
            self.win.showMaximized()


# ----------------------------------------------------------------------------
# 侧边导航
# ----------------------------------------------------------------------------
# 可视化播放不再占侧栏入口 —— 由底部播放条的封面一键进入
NAV_ITEMS = ["首页", "所有歌曲", "专辑", "艺术家", "最近播放"]


class SideBar(QWidget):
    pageSelected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(190)
        self.setStyleSheet(f"background:{BG}; border-right:1px solid {LINE};")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 18, 14, 16)
        lay.setSpacing(4)
        self.buttons = []
        for i, name in enumerate(NAV_ITEMS):
            b = QPushButton(name)
            b.setCheckable(True)
            b.setCursor(QCursor(Qt.PointingHandCursor))
            b.setFixedHeight(38)
            b.setStyleSheet(f"""
                QPushButton {{ color:{SOFT_INK}; background:transparent; border:none;
                               border-left:3px solid transparent; border-radius:0 8px 8px 0;
                               padding:0 14px; font-size:11pt; text-align:left; }}
                QPushButton:hover {{ color:{INK}; background:{HOVER}; }}
                QPushButton:checked {{ color:{INK}; background:{HOVER};
                                       border-left:3px solid {RED}; font-weight:600; }}
            """)
            b.clicked.connect(lambda checked=False, idx=i: self._select(idx))
            lay.addWidget(b)
            self.buttons.append(b)
        lay.addStretch(1)
        tip = QLabel("极简 · 纯净 · 声音的形态")
        tip.setStyleSheet(f"color:{LIGHT}; font-size:8pt; letter-spacing:1px;")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        self.buttons[0].setChecked(True)

    def _select(self, idx):
        for i, b in enumerate(self.buttons):
            b.setChecked(i == idx)
        self.pageSelected.emit(idx)

    def set_current(self, idx):
        for i, b in enumerate(self.buttons):
            b.setChecked(i == idx)

    def clear_current(self):
        """进入不属于导航的页面（可视化播放）时取消高亮"""
        for b in self.buttons:
            b.setChecked(False)


# ----------------------------------------------------------------------------
# 底部全局播放条
# ----------------------------------------------------------------------------
class CoverButton(QLabel):
    """底部条左侧封面：点击进入可视化播放页（hover 显示 ▶ 提示）"""
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(46, 46)
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self._hover = False
        self._pm = None

    def set_cover(self, pm):
        self._pm = pm
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, w, h), 9, 9)
        p.setClipPath(path)
        if self._pm is not None and not self._pm.isNull():
            p.drawPixmap(QRectF(0, 0, w, h), self._pm, QRectF(self._pm.rect()))
        else:
            p.fillRect(QRectF(0, 0, w, h), QColor(ART_BG))
            p.setPen(QPen(QColor(GRAY), 1))
            f = QFont(self.font())
            f.setPixelSize(int(w * 0.42))
            p.setFont(f)
            p.drawText(QRectF(0, 0, w, h), Qt.AlignCenter, "♪")
        if self._hover:
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor(0, 0, 0, 96)))
            p.drawRect(QRectF(0, 0, w, h))
            s = w * 0.17
            cx, cy = w / 2 + s * 0.3, h / 2
            tri = QPainterPath()
            tri.moveTo(cx - s * 0.55, cy - s)
            tri.lineTo(cx - s * 0.55, cy + s)
            tri.lineTo(cx + s * 0.75, cy)
            tri.closeSubpath()
            p.setBrush(QBrush(QColor("#ffffff")))
            p.drawPath(tri)
        p.setPen(QPen(QColor(LINE), 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)


class BottomBar(QWidget):
    playClicked = Signal()
    prevClicked = Signal()
    nextClicked = Signal()
    seekRequested = Signal(float)
    coverClicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(66)
        self.setStyleSheet(f"background:{BG}; border-top:1px solid {LINE};")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 8, 20, 8)
        lay.setSpacing(14)

        self.cover = CoverButton()
        self.cover.clicked.connect(self.coverClicked.emit)
        lay.addWidget(self.cover)

        info = QVBoxLayout()
        info.setSpacing(2)
        self.title = QLabel("AudioScope")
        self.title.setStyleSheet(f"color:{INK}; font-size:10pt; font-weight:600;")
        self.artist = QLabel("准备就绪")
        self.artist.setStyleSheet(f"color:{GRAY}; font-size:8.5pt;")
        info.addWidget(self.title)
        info.addWidget(self.artist)
        wrap = QWidget()
        wrap.setFixedWidth(170)
        wrap.setLayout(info)
        lay.addWidget(wrap)

        lay.addStretch(1)
        self.btn_prev = CircleButton(36, "prev", style="ghost")
        self.btn_play = CircleButton(46, "play", style="outline")
        self.btn_next = CircleButton(36, "next", style="ghost")
        self.btn_prev.clicked.connect(self.prevClicked.emit)
        self.btn_play.clicked.connect(self.playClicked.emit)
        self.btn_next.clicked.connect(self.nextClicked.emit)
        lay.addWidget(self.btn_prev)
        lay.addWidget(self.btn_play)
        lay.addWidget(self.btn_next)

        self.t_cur = QLabel("0:00")
        self.t_cur.setStyleSheet(f"color:{GRAY}; font-size:8.5pt;")
        self.t_cur.setFixedWidth(36)
        self.t_cur.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.progress = MiniTimeline()
        self.progress.setFixedWidth(280)
        self.progress.seekRequested.connect(self.seekRequested.emit)
        self.t_tot = QLabel("0:00")
        self.t_tot.setStyleSheet(f"color:{GRAY}; font-size:8.5pt;")
        self.t_tot.setFixedWidth(36)
        lay.addWidget(self.t_cur)
        lay.addWidget(self.progress)
        lay.addWidget(self.t_tot)
        lay.addStretch(1)

        vol_cap = QLabel("VOL")
        vol_cap.setStyleSheet(f"color:{GRAY}; font-size:8pt;")
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setValue(85)
        self.vol.setFixedWidth(96)
        self.vol.setStyleSheet(slider_style(96))
        lay.addWidget(vol_cap)
        lay.addWidget(self.vol)

    def set_track_info(self, title, artist, cover_img):
        self.title.setText(title)
        self.artist.setText(artist)
        self.cover.set_cover(rounded_pixmap(cover_img, 46, 9)
                             if cover_img is not None else None)

    def set_state(self, pos, dur):
        self.progress.set_state(pos, dur)
        self.t_cur.setText(fmt_time(pos))
        self.t_tot.setText(fmt_time(dur))

    def sync_play_icon(self, playing):
        self.btn_play.set_icon("pause" if playing else "play")


# ============================================================================
# 主窗口
# ============================================================================
class MainWindow(QWidget):
    TICK_BUSY = 16      # 有音频/麦克风时：~60fps，频谱与歌词跟手
    TICK_IDLE = 100     # 空闲时：~10fps，只够推进计时与位置同步

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.resize(1180, 780)
        self.setMinimumSize(1020, 680)
        # 只给窗口自身刷白：无选择器写法会把 background 级联给所有后代控件，
        # 导致播放页毛玻璃卡片里的 QLabel 各自糊上一块纯白（"组件发白"的真凶）
        self.setObjectName("appRoot")
        self.setStyleSheet(f"QWidget#appRoot {{ background:{BG}; }}")
        self.setAcceptDrops(True)

        self.engine = AudioEngine(self)
        self.mic = MicEngine()
        self.store = Store()
        self.library = []            # list[dict]
        self.lib_map = {}            # path → dict
        self._lib_seen = set()       # 已收下的 path（扫描批次去重）
        self.queue = []              # 播放队列
        self.qpos = -1
        self.load_thread = None
        self.lib_thread = None
        self._load_seq = 0
        self._fade_refs = []
        self.player_mode = False
        self._last_pos = -1.0
        self._last_save = time.monotonic()
        self._lyric_key = ""
        self._cover_key = ""
        self._lyric_thread = None
        self._cover_thread = None
        self._exit_state = None
        self._toast_refs = []

        self.engine.ended.connect(self._on_ended)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 标题栏
        self.titlebar = TitleBar(self)
        self.titlebar.btn_folder.clicked.connect(self.open_folder)
        self.titlebar.btn_mic.clicked.connect(self.toggle_mic)
        self.titlebar.btn_min.clicked.connect(self.showMinimized)
        self.titlebar.btn_max.clicked.connect(self._toggle_max)
        self.titlebar.btn_close.clicked.connect(self.close)
        root.addWidget(self.titlebar)

        # 主体：侧栏 + 页面
        body = QWidget()
        body_lay = QHBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(0)

        self.sidebar = SideBar()
        self.sidebar.pageSelected.connect(self._on_nav)
        body_lay.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        self.home_page = HomePage()
        self.songs_page = SongsPage("本地音乐")
        self.albums_page = GridPage("专辑", "ALBUMS  ·  按专辑归类")
        self.artists_page = GridPage("艺术家", "ARTISTS  ·  按艺术家归类")
        self.recent_page = SongsPage("最近播放", show_tools=False)
        self.player_page = PlayerPage()
        self.detail_page = DetailPage()

        for pg in (self.home_page, self.songs_page, self.albums_page,
                   self.artists_page, self.recent_page, self.player_page,
                   self.detail_page):
            self.stack.addWidget(pg)
        self.stack.setCurrentIndex(PAGE_HOME)
        self._detail_from = PAGE_ALBUMS
        body_lay.addWidget(self.stack, 1)
        root.addWidget(body, 1)

        # 底部播放条
        self.bottom = BottomBar()
        self.bottom.playClicked.connect(self.engine.toggle)
        self.bottom.prevClicked.connect(self.prev_track)
        self.bottom.nextClicked.connect(self.next_track)
        self.bottom.seekRequested.connect(self.seek_and_sync)
        self.bottom.vol.valueChanged.connect(self._on_volume)
        self.bottom.coverClicked.connect(self.open_player_page)
        root.addWidget(self.bottom)

        # 页面信号
        self.home_page.playRequested.connect(self._home_play)
        self.home_page.openFolderRequested.connect(self.open_folder)
        self.songs_page.playRequested.connect(self._page_play)
        self.recent_page.playRequested.connect(self._page_play)
        # 双击 = 播放并直接进入可视化播放页
        self.songs_page.playAndShowRequested.connect(self._page_play_and_show)
        self.recent_page.playAndShowRequested.connect(self._page_play_and_show)
        self.detail_page.playAndShowRequested.connect(self._page_play_and_show)
        self.albums_page.itemActivated.connect(self._album_open)
        self.artists_page.itemActivated.connect(self._artist_open)
        self.detail_page.playRequested.connect(self._page_play)
        self.detail_page.backRequested.connect(self._detail_back)
        self.player_page.prevClicked.connect(self.prev_track)
        self.player_page.nextClicked.connect(self.next_track)
        self.player_page.playClicked.connect(self.engine.toggle)
        self.player_page.timeline.seekRequested.connect(self.seek_and_sync)
        self.player_page.vol.valueChanged.connect(self._on_volume_page)
        self.player_page.exitRequested.connect(self.exit_player_page)
        self.player_page.settingChanged.connect(self._on_setting)
        self.player_page.queuePlayRequested.connect(self._queue_play)
        self.player_page.queueNextRequested.connect(self.insert_next)
        self.player_page.queueRemoveRequested.connect(self.remove_from_queue)
        self.player_page.lyricRefreshRequested.connect(self._fetch_lyrics_net)
        # 右键菜单：插入到下一首 / 加入队列 / 进入播放页
        for pg_ in (self.songs_page, self.recent_page, self.detail_page):
            pg_.queueNextRequested.connect(self.insert_next)
            pg_.queueAddRequested.connect(self.queue_add)
            pg_.showPlayerRequested.connect(self._show_path_player)
        self.player_page.lyricStyleChanged.connect(self._on_lyric_style)
        self.player_page.bgChanged.connect(self._on_bg_changed)
        self.player_page.set_settings(self.store.get("auto_lyrics", True),
                                      self.store.get("auto_cover", True),
                                      self.store.get("auto_fullscreen", False))
        # 恢复上次保存的歌词样式
        self.player_page.apply_lyric_style(
            self.store.get("lyric_color", SOFT_INK),
            self.store.get("lyric_active", RED),
            self.store.get("lyric_size", 15),
            self.store.get("lyric_align", 0))
        # 恢复播放页背景 / 氛围特效设置
        self.player_page.apply_background({
            "bg_mode": self.store.get("bg_mode", "fluid"),
            "bg_image": self.store.get("bg_image", ""),
            "bg_veil": self.store.get("bg_veil", 0.22),
            "weather": self.store.get("weather", "none")})
        # 让背景/特效设置立刻落盘（便于排查，也让首次运行就有默认值）
        self.store.save()

        # 刷新定时器
        self.idle_frames = 0
        self._last_tick = time.monotonic()
        # 库视图重建的合并调度器（见 _schedule_refresh）
        self._last_refresh = 0.0
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._refresh_views)
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

        self._sync_all_play_btns()

    # ---------------- 页面切换 / 最大化 / 手势 ----------------
    def _fade_page(self, w):
        """新页面淡入。

        透明度特效会把整页渲染到离屏缓冲再合成，实测单帧 3.9ms -> 7.3ms。
        播放页本身每帧都在重绘流体背景，再叠一层离屏合成最吃亏，而且它自带
        「进入播放」的视觉节奏，直接跳过更顺滑。
        """
        if w is self.player_page:
            w.setGraphicsEffect(None)
            return
        eff = QGraphicsOpacityEffect(w)
        eff.setOpacity(0.0)
        w.setGraphicsEffect(eff)
        a = QPropertyAnimation(eff, b"opacity", w)
        a.setStartValue(0.0)
        a.setEndValue(1.0)
        a.setDuration(150)
        a.setEasingCurve(QEasingCurve.OutCubic)
        a.finished.connect(lambda: w.setGraphicsEffect(None))
        a.finished.connect(a.deleteLater)
        a.start()
        self._fade_refs.append(a)
        self._fade_refs[:] = self._fade_refs[-20:]

    def _on_nav(self, idx):
        if self.player_mode:
            self._restore_window()     # 从全屏播放页还原窗口装饰
        cur = self.stack.currentIndex()
        if idx == cur:
            self._update_chrome()
            return
        if idx == PAGE_RECENT:
            self._fill_recent_page()
        self.stack.setCurrentIndex(idx)
        self._fade_page(self.stack.currentWidget())
        self._update_chrome()

    def _update_chrome(self):
        """可视化播放页自带完整控件 → 隐藏底部播放条，避免重复"""
        idx = self.stack.currentIndex()
        self.player_mode = (idx == PAGE_PLAYER)
        self.bottom.setVisible(not self.player_mode)

    # ---------------- 全屏播放页：进入 / 退出 ----------------
    def open_player_page(self):
        """从任意页面一键进入沉浸播放页（隐藏侧栏 / 底栏）。

        是否再整屏由「播放页全屏」开关决定（store.settings["auto_fullscreen"]，
        默认关）：
        - 关（默认）：保持当前窗口大小，只把侧栏/底栏收起来；
        - 开：整屏显示，同时把自绘标题栏一并隐藏。
        注意窗口是 FramelessWindowHint，标题栏是唯一能拖动/最小化/关闭的
        把手 —— 所以窗口模式下必须留着它，否则窗口就"动不了"了。
        """
        if self.player_mode:
            return
        prev = self.stack.currentIndex()
        self._exit_state = {
            "idx": prev if prev != PAGE_PLAYER else PAGE_HOME,
            "maximized": self.isMaximized(),
            "geom": self.normalGeometry(),
        }
        self.stack.setCurrentIndex(PAGE_PLAYER)
        self.sidebar.clear_current()
        # 先隐藏装饰再切窗口状态，避免中间出现一帧半布局
        self.sidebar.setVisible(False)
        self.bottom.setVisible(False)
        self._fs_on = bool(self.store.get("auto_fullscreen", False))
        if self._fs_on:
            self.titlebar.setVisible(False)
            self.showFullScreen()
        else:
            self.titlebar.setVisible(True)
            if self.isFullScreen():          # 上次退出时残留的全屏状态
                self.showNormal()
                self.setGeometry(self._exit_state["geom"])
        self.player_mode = True
        self._fade_page(self.player_page)
        self._refresh_queue()
        # 还没播放过任何歌时，用「上次播放」的封面先把背景铺上，
        # 避免一进来看到默认的灰白底色（用户反馈的「怪状态」之一）
        if not self.player_page.bg.has_source():
            e = self.lib_map.get(self.store.last or "")
            if e is not None and e.get("cover") is not None:
                self.player_page.set_bg_cover(e["cover"])

    def _restore_window(self):
        """还原标题栏 / 侧栏 / 底栏；只有真整屏过才需要还原窗口尺寸与状态"""
        st = self._exit_state or {"idx": PAGE_HOME, "maximized": False,
                                  "geom": None}
        self.titlebar.setVisible(True)
        self.sidebar.setVisible(True)
        self.bottom.setVisible(True)
        if getattr(self, "_fs_on", False):
            if self.isFullScreen():
                self.showNormal()
            if st.get("geom") is not None:
                self.setGeometry(st["geom"])
            if st.get("maximized"):
                self.showMaximized()

    def exit_player_page(self):
        """退出沉浸播放页 → 还原窗口装饰与上一页"""
        if not self.player_mode:
            return
        st = self._exit_state or {"idx": PAGE_HOME, "maximized": False,
                                  "geom": None}
        self._restore_window()
        self.player_mode = False
        idx = st.get("idx", PAGE_HOME)
        if idx == PAGE_RECENT:
            self._fill_recent_page()
        self.stack.setCurrentIndex(idx)
        if idx in (PAGE_HOME, PAGE_SONGS, PAGE_ALBUMS, PAGE_ARTISTS, PAGE_RECENT):
            self.sidebar.set_current(idx)
        else:
            self.sidebar.clear_current()
        self._update_chrome()
        self._fade_page(self.stack.currentWidget())

    def _show_path_player(self, path):
        """右键「进入可视化播放页」：若不是当前曲则先播放，再进入"""
        if path != self.engine.path:
            self.play_path(path)
        self.open_player_page()

    # ---------------- 队列：插入下一首 / 追加 / 移除 / 播放 ----------------
    def _refresh_queue(self):
        cur = self.queue[self.qpos] if 0 <= self.qpos < len(self.queue) else None
        self.player_page.update_queue(self.queue, self.lib_map, cur)

    def _queue_title(self, path):
        e = self.lib_map.get(path)
        if e:
            return e["title"]
        return os.path.splitext(os.path.basename(path))[0]

    def insert_next(self, path):
        """插入到当前曲目之后播放（不改变正在播放的歌）"""
        if not self.queue:
            self.play_path(path, [path])
            self._refresh_queue()
            self._toast(f"开始播放：{self._queue_title(path)}")
            return
        if path in self.queue:
            idx = self.queue.index(path)
            self.queue.pop(idx)
            if idx < self.qpos:
                self.qpos -= 1
        pos = max(0, min(self.qpos + 1, len(self.queue)))
        self.queue.insert(pos, path)
        self._refresh_queue()
        self._toast(f"已插入到下一首：{self._queue_title(path)}")

    def queue_add(self, path):
        """添加到播放队列末尾"""
        if path in self.queue:
            self._toast("已在播放队列中")
            return
        self.queue.append(path)
        self._refresh_queue()
        self._toast(f"已加入队列：{self._queue_title(path)}")

    def remove_from_queue(self, path):
        if path not in self.queue:
            return
        idx = self.queue.index(path)
        if idx == self.qpos:
            self.queue.pop(idx)
            if self.qpos >= len(self.queue):
                self.qpos = max(0, len(self.queue) - 1)
            if self.queue:
                self.load_path(self.queue[self.qpos])
        else:
            self.queue.pop(idx)
            if idx < self.qpos:
                self.qpos -= 1
        self._refresh_queue()

    def _queue_play(self, path):
        if path in self.queue:
            self.qpos = self.queue.index(path)
            self.load_path(path)
            self._refresh_queue()
        else:
            self.play_path(path)
            self._refresh_queue()

    def _on_setting(self, key, value):
        self.store.set_setting(key, value)
        if key == "auto_lyrics" and value and self.engine.path:
            self._fetch_lyrics_net()
        if key == "auto_cover" and value and self.engine.path:
            self._fetch_cover_net()
        if key == "auto_fullscreen" and self.player_mode:
            self._apply_player_fullscreen(value)

    def _apply_player_fullscreen(self, on):
        """在播放页里切换「全屏」开关时立即生效（退出播放页不变窗口状态）"""
        self._fs_on = bool(on)
        if self._fs_on:
            self.titlebar.setVisible(False)
            self.showFullScreen()
        else:
            if self.isFullScreen():
                self.showNormal()
                st = self._exit_state or {}
                if st.get("geom") is not None:
                    self.setGeometry(st["geom"])
            self.titlebar.setVisible(True)

    # ---------------- 轻提示（右上角浮层） ----------------
    def _toast(self, text):
        lab = QLabel(text, self)
        lab.setStyleSheet(f"""
            QLabel {{ background:{INK}; color:#fff; border-radius:14px;
                      padding:8px 16px; font-size:10pt; }}
        """)
        lab.adjustSize()
        lab.move(self.width() - lab.width() - 24, 60)
        lab.raise_()
        lab.show()
        eff = QGraphicsOpacityEffect(lab)
        eff.setOpacity(0.0)
        lab.setGraphicsEffect(eff)
        a = QPropertyAnimation(eff, b"opacity", lab)
        a.setStartValue(0.0)
        a.setEndValue(1.0)
        a.setDuration(160)
        a.start()
        self._toast_refs.append((lab, a))
        QTimer.singleShot(1900, lambda: self._fade_out_toast(lab))

    def _fade_out_toast(self, lab):
        eff = lab.graphicsEffect()
        if eff is None:
            lab.deleteLater()
            return
        a = QPropertyAnimation(eff, b"opacity", lab)
        a.setStartValue(1.0)
        a.setEndValue(0.0)
        a.setDuration(260)
        a.finished.connect(lab.deleteLater)
        a.start()
        self._toast_refs.append(a)
        self._toast_refs[:] = self._toast_refs[-20:]

    def begin_gesture(self):
        """拖动 / 缩放期间冻结界面更新（结束后由 end_gesture 恢复）"""
        self.setUpdatesEnabled(False)

    def end_gesture(self):
        self.setUpdatesEnabled(True)

    def _toggle_max(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _on_volume(self, v):
        self.engine.volume = v / 100.0
        self.player_page.vol.blockSignals(True)
        self.player_page.vol.setValue(v)
        self.player_page.vol.blockSignals(False)

    def _page_play_and_show(self, context, start):
        self._page_play(context, start)
        self.open_player_page()

    def seek_and_sync(self, sec):
        """跳转进度：暂停状态下也要立刻看到游标移动（原来要等到开始播放才更新）"""
        self.engine.seek(sec)
        self._sync_positions(force=True)

    def _sync_positions(self, force=False):
        pos, dur = self.engine.position(), self.engine.duration
        if dur <= 0:
            return
        if force or self.engine.playing or abs(pos - self._last_pos) > 0.02:
            self._last_pos = pos
            if not self.player_mode:
                self.bottom.set_state(pos, dur)
            self.player_page.timeline.set_state(pos, dur)

    def _on_volume_page(self, v):
        self.engine.volume = v / 100.0
        self.bottom.vol.blockSignals(True)
        self.bottom.vol.setValue(v)
        self.bottom.vol.blockSignals(False)

    # ---------------- 音乐库 ----------------
    def open_folder(self):
        d = QFileDialog.getExistingDirectory(self, "选择音乐文件夹加入曲库")
        if not d:
            return
        if d not in self.store.folders:
            self.store.folders.append(d)
            self.store.save()
        self.rescan()

    def rescan(self):
        if not self.store.folders:
            return
        if self.lib_thread is not None and self.lib_thread.isRunning():
            return
        self.lib_thread = LibraryThread(self.store.folders, self,
                                        self.store.cache_dir)
        self.lib_thread.batch.connect(self._on_lib_batch)
        self.lib_thread.scanDone.connect(self._on_lib_done)
        self._lib_seen = set()
        self.library = []
        self.lib_map = {}
        self.lib_thread.start()

    def _on_lib_batch(self, entries):
        new = [e for e in entries if e["path"] not in self._lib_seen]
        for e in new:
            self._lib_seen.add(e["path"])
        self.library.extend(new)
        self.lib_map.update({e["path"]: e for e in new})
        self._schedule_refresh()

    def _on_lib_done(self):
        self._schedule_refresh(immediate=True)

    # 扫描期间「凑够 40 首就整库重建一次」是启动卡顿的主因：重建一次 600 首
    # 要 100ms+，一个 600 首的库会产生 15 个批次 → 白堵 1.5 秒以上。这里改成
    # 合并调度：两次重建之间至少隔 REFRESH_MIN_GAP，且批间安静 250ms 才动手，
    # 扫描结束再补一次完整重建。既保留增量可见，又不让 UI 反复被拖住。
    REFRESH_MIN_GAP = 0.7
    REFRESH_QUIET = 0.25

    def _schedule_refresh(self, immediate=False):
        if immediate:
            self._refresh_timer.stop()
            self._refresh_views()
            return
        if time.monotonic() - self._last_refresh >= self.REFRESH_MIN_GAP:
            self._refresh_timer.stop()
            self._refresh_views()
            return
        self._refresh_timer.start(int(self.REFRESH_QUIET * 1000))

    def _refresh_views(self):
        ws = self.stack.currentWidget() if hasattr(self, "stack") else None
        # 冻结整个窗口重绘：重建期间会创建/销毁成百上千个子控件
        self.setUpdatesEnabled(False)
        try:
            self.songs_page.set_entries(self.library)
            self.albums_page.set_groups(self._group("album", "未知专辑"))
            self.artists_page.set_groups(self._group("artist", "未知艺术家"))
            self.home_page.update_home(self.library, self.store, self.lib_map)
        finally:
            self.setUpdatesEnabled(True)
        self._last_refresh = time.monotonic()
        if ws is not None and ws is not self.stack.currentWidget():
            self.stack.setCurrentWidget(ws)

    def _group(self, key, fallback):
        groups = {}
        for e in self.library:
            k = e.get(key) or fallback
            groups.setdefault(k, []).append(e)
        return groups

    def add_files_to_library(self, files):
        new = []
        for p in files:
            if p not in self.lib_map:
                e = LibraryThread.read_entry(p, self.store.cache_dir)
                self.library.append(e)
                self.lib_map[p] = e
                new.append(e)
        if new:
            self._refresh_views()
        return [e["path"] for e in new]

    # ---------------- 播放控制 ----------------
    def _home_play(self, context, start):
        if start == "__random__":
            if not self.library:
                return
            e = _random.choice(self.library)
            self.play_path(e["path"], [x["path"] for x in self.library])
        else:
            self.play_path(start, context or [start])

    def _page_play(self, context, start):
        self.play_path(start, context)

    def _album_open(self, key):
        """点击专辑 → 打开曲目列表，让用户自选歌曲（不再直接播放第一首）"""
        entries = self._group("album", "未知专辑").get(key, [])
        if not entries:
            return
        cover = next((e["cover"] for e in entries if e.get("cover") is not None), None)
        self.detail_page.set_detail(key, entries, cap="ALBUM  ·  专辑", cover=cover)
        self._detail_from = PAGE_ALBUMS
        self._show_detail()

    def _artist_open(self, key):
        entries = self._group("artist", "未知艺术家").get(key, [])
        if not entries:
            return
        cover = next((e["cover"] for e in entries if e.get("cover") is not None), None)
        self.detail_page.set_detail(key, entries, cap="ARTIST  ·  艺术家", cover=cover)
        self._detail_from = PAGE_ARTISTS
        self._show_detail()

    def _show_detail(self):
        self.stack.setCurrentIndex(PAGE_DETAIL)
        self._fade_page(self.detail_page)
        self._update_chrome()

    def _detail_back(self):
        self.stack.setCurrentIndex(self._detail_from)
        self._fade_page(self.stack.currentWidget())
        self._update_chrome()
        self.sidebar.set_current(self._detail_from)

    def play_path(self, path, context=None):
        if context:
            self.queue = list(context)
        elif path not in self.queue:
            self.queue.append(path)
        self.qpos = self.queue.index(path) if path in self.queue else len(self.queue) - 1
        self.load_path(path)

    def load_path(self, path):
        self._load_seq += 1
        seq = self._load_seq
        self.player_page.title_lbl.setText(os.path.basename(path))
        self.player_page.artist_lbl.setText("正在加载…")
        self.player_page.src_label.setText("")
        if self.load_thread is not None and self.load_thread.isRunning():
            self.load_thread.wait(1500)
        self.load_thread = LoadThread(path, seq, self)
        self.load_thread.loaded.connect(self._on_loaded)
        self.load_thread.failed.connect(self._on_load_failed)
        self.load_thread.start()

    def next_track(self):
        if self.queue:
            self.qpos = (self.qpos + 1) % len(self.queue)
            self.load_path(self.queue[self.qpos])

    def prev_track(self):
        if self.queue:
            self.qpos = (self.qpos - 1) % len(self.queue)
            self.load_path(self.queue[self.qpos])

    def _on_ended(self):
        QTimer.singleShot(300, self.next_track)

    def _on_load_failed(self, msg):
        seq, _, err = msg.partition("|")
        if seq.isdigit() and int(seq) != self._load_seq:
            return
        self.player_page.artist_lbl.setText(f"加载失败：{err}")

    def _on_loaded(self, data):
        seq = data.get("seq", -1)
        if seq != self._load_seq:
            return
        try:
            self.engine.stop_stream()
            self.engine.set_track(data["samples"], data["duration"], data["path"])
            self.engine.play()
        except Exception as e:
            self.player_page.artist_lbl.setText(f"播放失败：{e}")
            return
        meta = data["meta"]
        pg = self.player_page
        pg.title_lbl.setText(meta["title"])
        pg.artist_lbl.setText(meta["artist"])
        pg.fade_titles()
        album = meta["album"]
        pg.src_label.setText(f"■ From : {album}" if album
                             else f"■ FILE : {os.path.basename(data['path'])}")
        srate = meta["sample_rate"] if meta["sample_rate"] != "--" else "48 kHz"
        pg.quality_lbl.setText(
            f"{meta['format']} / {meta['bit_depth']} / {srate}\n{meta['bitrate']}")
        pg.viz.set_cover(meta["cover"])
        pg.set_bg_cover(meta["cover"])
        pg.info.set_values(
            dyn=f"{data['dyn']:.1f} dB", loud=f"{data['loudness']:.1f} dB",
            sample_rate=srate, bit_depth=meta["bit_depth"], bitrate=meta["bitrate"],
            file_size=meta["file_size"], channels=meta["channels"],
            format=meta["format"])
        bpm = f"{data['bpm']:.0f}" if data["bpm"] else "--"
        pg.bpm_lbl.setText(f"BPM  {bpm}          GENRES  --          "
                           f"DURATION  {fmt_time(data['duration'])}")
        pg.viz.track_no = (f"{self.qpos + 1:02d} / {len(self.queue):02d}"
                           if self.queue else "--")
        pg.top_title.setText(f"{meta['title']}  ·  {meta['artist']}")

        # ---- 歌词：本地同名 lrc → 缓存 → 联网（受开关控制） ----
        self._cur_meta = meta
        self._cur_path = data["path"]
        self._load_lyrics(data["path"], meta)
        # ---- 封面：内嵌封面缺失时按开关联网补全 ----
        if meta["cover"] is None:
            self._load_cover(data["path"], meta)
        self._refresh_queue()

        self.bottom.set_track_info(meta["title"], meta["artist"], meta["cover"])
        # 各列表高亮当前曲目（▶ 标记）
        for t in (self.songs_page.table, self.recent_page.table,
                  self.detail_page.table):
            t.set_current(data["path"])
        # 最近播放 + 持久化
        self.store.push_recent(data["path"])
        self._recent_dirty = True

    # ---------------- 歌词获取：本地 → 缓存 → 联网（多源） ----------------
    @staticmethod
    def _split_meta(meta):
        """标签不全时从「歌手 - 标题」这类文件名里补出歌手，提升匹配率"""
        title = (meta.get("title") or "").strip()
        artist = (meta.get("artist") or "").strip()
        if not artist or artist in ("未知艺术家", "Unknown", "unknown"):
            t2, a2 = split_filename(title)
            if a2:
                title, artist = t2, a2
        return title, artist

    def _load_lyrics(self, path, meta):
        pg = self.player_page
        title, artist = self._split_meta(meta)
        key = Store.cache_key(artist, title, meta["album"])
        self._lyric_key = key
        local = find_local_lrc(path)
        if local:
            lines = parse_lrc(local)
            if lines:
                pg.set_lyrics(lines)
                pg.lyric_cap.setText("LYRICS  ·  本地歌词")
                return
            if local.strip():                      # 有文件但无时间轴 → 纯文本
                pg.set_plain_lyrics(local)
                pg.lyric_cap.setText("LYRICS  ·  本地文本歌词")
                return
        cached = self.store.read_cached_lyric(key)
        if cached:
            lines = parse_lrc(cached)
            if lines:
                pg.set_lyrics(lines)
                pg.lyric_cap.setText("LYRICS  ·  已缓存歌词")
                return
        if self.store.get("auto_lyrics", True):
            pg.set_lyrics([])
            pg.set_lyric_hint("正在匹配歌词…")
            pg.lyric_cap.setText("LYRICS  ·  匹配中")
            self._fetch_lyrics_net()
        else:
            pg.set_lyrics([])
            pg.set_lyric_hint("未开启「自动歌词」—— 可在右上角开关打开")
            pg.lyric_cap.setText("LYRICS  ·  歌词")

    def _fetch_lyrics_net(self):
        meta = getattr(self, "_cur_meta", None)
        if meta is None:
            return
        title, artist = self._split_meta(meta)
        key = Store.cache_key(artist, title, meta["album"])
        self._lyric_key = key
        dur = self.engine.duration or 0.0
        t = LyricsThread(key, title, artist, meta["album"], dur, self)
        t.fetched.connect(self._on_lyrics)
        t.status.connect(lambda s, k=key: self._lyric_status(k, s))
        self._lyric_thread = t
        t.start()

    def _lyric_status(self, key, text):
        if key == self._lyric_key:
            self.player_page.lyric_cap.setText(f"LYRICS  ·  {text}")

    def _on_lyrics(self, key, text, source):
        if key != self._lyric_key:
            return
        pg = self.player_page
        lines = parse_lrc(text) if text else None
        if lines:
            self.store.write_cached_lyric(key, text)
            pg.set_lyrics(lines)
            pg.lyric_cap.setText(f"LYRICS  ·  {source or '网络'} · 逐行")
        elif text and text.strip():
            # 拿到歌词但没有时间轴：静态展示，至少能看
            self.store.write_cached_lyric(key, text)
            pg.set_plain_lyrics(text)
            pg.lyric_cap.setText(f"LYRICS  ·  {source or '网络'} · 纯文本")
        else:
            pg.set_lyrics([])
            pg.set_lyric_hint("未匹配到歌词（可放同名 .lrc 或点「重新获取」）")
            pg.lyric_cap.setText("LYRICS  ·  歌词")

    def _on_lyric_style(self, d):
        self.store.settings["lyric_color"] = d.get("color", SOFT_INK)
        self.store.settings["lyric_active"] = d.get("active", RED)
        self.store.settings["lyric_size"] = int(d.get("size", 15))
        self.store.settings["lyric_align"] = int(d.get("align", 0))
        self.store.save()

    def _on_bg_changed(self, d):
        for Key in ("bg_mode", "bg_image", "bg_veil", "weather"):
            if Key in d:
                self.store.settings[Key] = d[Key]
        self.store.save()

    # ---------------- 封面联网补全 ----------------
    def _load_cover(self, path, meta):
        title, artist = self._split_meta(meta)
        key = Store.cache_key(artist, title, meta["album"])
        self._cover_key = key
        cached = self.store.read_cached_cover(key)
        if cached:
            img = QImage(cached)
            if not img.isNull():
                self._apply_net_cover(path, img)
                return
        if not self.store.get("auto_cover", True):
            return
        t = CoverThread(key, title, artist, meta["album"],
                        self.engine.duration or 0.0, self)
        t.fetched.connect(lambda k, b: self._on_cover(k, b, path))
        self._cover_thread = t
        t.start()

    def _on_cover(self, key, data, path):
        if key != self._cover_key or not data:
            return
        img = QImage.fromData(data)
        if img.isNull():
            return
        self.store.write_cached_cover(key, data)
        self._apply_net_cover(path, img)

    def _apply_net_cover(self, path, img):
        self.player_page.viz.set_cover(img)
        self.player_page.set_bg_cover(img)
        meta = getattr(self, "_cur_meta", None)
        if meta is not None and path == getattr(self, "_cur_path", None):
            self.bottom.set_track_info(meta["title"], meta["artist"], img)
            meta["cover"] = img
        e = self.lib_map.get(path)
        if e is not None:
            try:
                e["cover"] = img.scaled(160, 160,
                                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                        Qt.TransformationMode.SmoothTransformation)
            except Exception:
                pass
        # 关键：封面匹配成功后立刻刷新各页面（此前只有重扫曲库才会显示）
        self._refresh_covers(path, img)

    def _refresh_covers(self, path, img):
        """把迟到/联网补全的封面推给所有已渲染的页面"""
        for page in (self.songs_page, self.recent_page):
            try:
                page.refresh_cover(path, img)
            except Exception:
                pass
        for page in (self.albums_page, self.artists_page):
            try:
                page.refresh_covers()
            except Exception:
                pass
        try:
            self.home_page.refresh_covers()
        except Exception:
            pass
        try:
            self.detail_page.refresh_cover(path, img)
        except Exception:
            pass

    def _fill_recent_page(self):
        entries = [self.lib_map[p] for p in self.store.recent if p in self.lib_map]
        self.recent_page.set_entries(entries)
        self.recent_page.table.set_current(
            self.engine.path if self.engine.path else None)

    # ---------------- 麦克风监控 ----------------
    def toggle_mic(self):
        if not self.mic.active:
            try:
                self.mic.start()
            except Exception as e:
                QMessageBox.warning(self, "麦克风错误", f"无法打开麦克风：{e}")
                self.titlebar.btn_mic.setChecked(False)
                return
            self.titlebar.btn_mic.setChecked(True)
            self.player_page.viz.mic_mode = True
        else:
            self.mic.stop()
            self.titlebar.btn_mic.setChecked(False)
            self.player_page.viz.mic_mode = False

    # ---------------- 刷新 ----------------
    def _tick(self):
        now = time.monotonic()
        dt = now - self._last_tick
        self._last_tick = now
        # 累计「用这个软件听歌的时长」（拖动窗口时不中断计时）
        if self.engine.playing and dt < 1.0:
            self.store.listened += dt
            if now - self._last_save > 20.0:
                self._last_save = now
                self.store.save()
                self.home_page.stat_labels["time"].setText(
                    fmt_total(self.store.listened)
                    if self.store.listened >= 60 else f"{int(self.store.listened)} 秒")
        # 拖动窗口 / 缩放期间冻结重绘 —— 避免每帧全窗口刷新造成闪屏
        if not self.updatesEnabled():
            return
        pg = self.player_page
        if self.mic.active:
            an = self.mic.analyzer
        else:
            an = self.engine.analyzer
            self._sync_positions()
        spec = an.advance(dt)
        busy = (self.engine.playing or self.mic.active
                or float(np.max(spec)) > 0.01)
        # 空闲降频：没有音频时没必要 62.5fps 空转重绘（可视化/频谱/歌词都在
        # 无事发生）。降到 10fps，把 CPU 让给用户真正在做的操作。
        want = self.TICK_BUSY if busy else self.TICK_IDLE
        if self.timer.interval() != want:
            self.timer.setInterval(want)
        pg.viz.set_spectrum(an.resampled(128))
        pg.spec.set_spectrum(spec)

        pg.viz.playing = self.engine.playing
        if busy or self.idle_frames % 6 == 0:
            pg.viz.tick()
        self.idle_frames = (self.idle_frames + 1) % 600
        self._sync_all_play_btns()
        # 歌词跟着播放进度走（暂停时保持当前行）
        if self.stack.currentIndex() == PAGE_PLAYER:
            pg.sync_lyrics(self.engine.position())

    def _sync_all_play_btns(self):
        self.player_page.btn_play.set_icon("pause" if self.engine.playing else "play")
        self.bottom.sync_play_icon(self.engine.playing)

    # ---------------- 拖拽 / 快捷键 ----------------
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        exts = {".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac", ".opus"}
        files = [u.toLocalFile() for u in e.mimeData().urls()
                 if os.path.splitext(u.toLocalFile())[1].lower() in exts]
        if files:
            paths = self.add_files_to_library(files)
            if paths:
                self.play_path(paths[0], paths)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            if self.player_mode:
                self.exit_player_page()
                return
        if e.key() == Qt.Key_Space:
            self.engine.toggle()
        elif e.key() == Qt.Key_Right:
            self.seek_and_sync(self.engine.position() + 5)
        elif e.key() == Qt.Key_Left:
            self.seek_and_sync(self.engine.position() - 5)
        else:
            super().keyPressEvent(e)

    def showEvent(self, e):
        super().showEvent(e)
        if not getattr(self, "_faded_in", False):
            self._faded_in = True
            eff = QGraphicsOpacityEffect(self)
            eff.setOpacity(0.0)
            self.setGraphicsEffect(eff)
            a = QPropertyAnimation(eff, b"opacity", self)
            a.setStartValue(0.0)
            a.setEndValue(1.0)
            a.setDuration(220)
            a.setEasingCurve(QEasingCurve.OutCubic)
            a.finished.connect(lambda: self.setGraphicsEffect(None))
            a.finished.connect(a.deleteLater)
            # 关键：窗口淡入期间整窗挂着透明度特效，任何子控件失效都会让整窗
            # 走一遍离屏合成。此时若开始扫描并重建视图，两者叠加就是"刚点开
            # 软件有点卡"的主因。等淡入结束再启动扫描，用户完全无感。
            a.finished.connect(self._kick_scan)
            a.start()
            self._fade_win_ref = a
            self._scan_kicked = False
            QTimer.singleShot(900, self._kick_scan)   # 兜底：动画异常也要扫
        elif not getattr(self, "_scan_kicked", True):
            self._kick_scan()

    def _kick_scan(self):
        """淡入结束（或兜底超时）后启动库扫描；重复调用无副作用"""
        if getattr(self, "_scan_kicked", False):
            return
        self._scan_kicked = True
        if not self.store.folders:
            music = os.path.join(os.path.expanduser("~"), "Music")
            if not (os.path.isdir(music) and os.listdir(music)):
                return
            self.store.folders.append(music)
            self.store.save()
        self.rescan()

    def closeEvent(self, e):
        self.engine.stop_stream()
        self.mic.stop()
        self.store.save()
        super().closeEvent(e)


def neutral_palette(app):
    """统一调色板 + Fusion 风格：杜绝 Windows 强调色（蓝）渗进原生控件绘制"""
    try:
        app.setStyle("Fusion")
    except Exception:
        pass
    pal = QPalette()
    mapping = {
        QPalette.Window: BG, QPalette.WindowText: INK,
        QPalette.Base: "#ffffff", QPalette.AlternateBase: "#fbfbfc",
        QPalette.Text: INK, QPalette.Button: "#ffffff", QPalette.ButtonText: INK,
        QPalette.Highlight: INK, QPalette.HighlightedText: "#ffffff",
        QPalette.Link: INK, QPalette.LinkVisited: SOFT_INK,
        QPalette.ToolTipBase: "#ffffff", QPalette.ToolTipText: INK,
        QPalette.BrightText: "#ffffff", QPalette.PlaceholderText: GRAY,
    }
    for role, color in mapping.items():
        pal.setColor(role, QColor(color))
    app.setPalette(pal)


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei UI", 10))
    # 窗口 / 任务栏 / Alt-Tab 图标：代码现画，不依赖外部文件，
    # onefile 解包后也不会丢（exe 文件本身的图标由 spec 的 icon= 提供）
    app.setWindowIcon(app_icon())
    neutral_palette(app)
    app.setStyleSheet(f"QMessageBox {{ background:{BG}; }}"
                      + scrollbar_style())
    win = MainWindow()
    win.show()
    resize_filter = ResizeFilter(win)
    win._resize_filter = resize_filter
    app.installEventFilter(resize_filter)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
