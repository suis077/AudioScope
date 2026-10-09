# -*- coding: utf-8 -*-
"""AudioScope 页面层：首页 / 所有歌曲 / 专辑 / 艺术家 / 最近播放 / 可视化播放"""
import os

from PySide6.QtCore import (Qt, Signal, QRectF, QSize, QVariantAnimation,
                            QEasingCurve, QPoint, QTimer)
from PySide6.QtGui import (QColor, QFont, QPixmap, QPainter, QIcon,
                           QPainterPath, QPen, QBrush)
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QTableWidget, QTableWidgetItem, QHeaderView,
                               QLineEdit, QPushButton, QScrollArea, QGridLayout,
                               QListWidget, QListWidgetItem, QSizePolicy,
                               QGraphicsOpacityEffect, QAbstractItemView,
                               QSlider, QMenu, QFileDialog)

from core import (BG, PANEL, INK, SOFT_INK, GRAY, LIGHT, LINE, HOVER, RED,
                  ART_BG, fmt_time, fmt_total, LoadThread)
from widgets import (CircleButton, VisualizerWidget, SpectrumWidget,
                     TimelineWidget, InfoGrid, LyricsWidget, ToggleSwitch,
                     QueueList, LyricStylePanel, FluidBackground,
                     WeatherOverlay, BackgroundPanel, GlassPanel,
                     rounded_pixmap, pill_button, slider_style,
                     slider_style_glass, scrollbar_style)
import widgets as _W


def _cap_label(text):
    lab = QLabel(text)
    lab.setStyleSheet(f"color:{GRAY}; font-size:8pt; letter-spacing:2px;")
    return lab


def _title_label(text, size=34):
    lab = QLabel(text)
    lab.setStyleSheet(f"color:{INK}; font-size:{size}pt; font-weight:700;")
    return lab


def greet():
    import datetime
    h = datetime.datetime.now().hour
    g = "早上好" if h < 11 else ("下午好" if h < 18 else "晚上好")
    wd = "一二三四五六日"[datetime.datetime.now().weekday()]
    d = datetime.datetime.now()
    return g, f"{d.month}月{d.day}日 · 周{wd} · 本地音乐库"


# ============================================================================
# 所有歌曲 / 最近播放 共用的曲目表格
# ============================================================================
class SongsTable(QTableWidget):
    rowActivated = Signal(list)        # 单击：[path, visible_paths]
    rowDoubleActivated = Signal(list)  # 双击：[path, visible_paths]（播放并进入播放页）
    queueNextRequested = Signal(str)   # 右键：插入到下一首播放
    queueAddRequested = Signal(str)    # 右键：添加到队列末尾
    showPlayerRequested = Signal(str)  # 右键：进入可视化播放页

    def __init__(self, parent=None):
        super().__init__(0, 7, parent)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)
        self.setHorizontalHeaderLabels(["#", "", "标题", "歌手", "专辑", "格式", "时长"])
        self.verticalHeader().setVisible(False)
        self.setShowGrid(False)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setAlternatingRowColors(False)
        self.setFocusPolicy(Qt.NoFocus)
        self.setWordWrap(False)
        hh = self.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Fixed)
        hh.setSectionResizeMode(0, QHeaderView.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.Fixed)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        hh.setSectionResizeMode(4, QHeaderView.Stretch)
        hh.setSectionResizeMode(5, QHeaderView.Fixed)
        hh.setSectionResizeMode(6, QHeaderView.Fixed)
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.setFixedHeight(34)
        self.setColumnWidth(0, 44)
        self.setColumnWidth(1, 52)
        self.setColumnWidth(5, 92)
        self.setColumnWidth(6, 72)
        self.verticalHeader().setDefaultSectionSize(46)
        # 自己接管排序：默认保持库内顺序，点击表头才排序（内置 setSortingEnabled
        # 会打乱行序，且排序后行与数据错位）
        self.setSortingEnabled(False)
        self.horizontalHeader().setSectionsClickable(True)
        self.horizontalHeader().sectionClicked.connect(self._on_header)
        self._sort_col = -1
        self._sort_desc = False
        self._filter_kw = ""
        self._entry_map = {}
        self._order_index = {}
        self.setStyleSheet(f"""
            QTableWidget {{ background:transparent; border:none; color:{SOFT_INK};
                            font-size:10pt; }}
            QTableWidget::item {{ border:none; padding:0px; }}
            QTableWidget::item:selected {{ background:{HOVER}; color:{INK}; }}
            QHeaderView::section {{ background:transparent; border:none;
                                    border-bottom:1px solid {LINE};
                                    color:{GRAY}; font-size:8pt;
                                    padding:6px 4px; letter-spacing:1px; }}
            QTableCornerButton::section {{ background:transparent; border:none; }}
        """ + scrollbar_style())
        self.cellClicked.connect(self._on_cell)
        self.cellDoubleClicked.connect(self._on_double)
        self._paths = []       # 行 → path（加载时的顺序）
        self._icon_cache = {}  # (封面 cacheKey, size) → QIcon
        self._all_visible = True   # apply_filter 快速通道用
        self._visible = []     # 过滤后可见 path（按显示顺序）
        self._flash_row = None
        self._flash_anim = None
        self._current_path = None

    # ---------- 行数据 ----------
    def _row_path(self, row):
        """路径存在 item 的 UserRole 里 —— 排序后行序变化也不会串歌"""
        it = self.item(row, 0)
        return it.data(Qt.UserRole) if it is not None else None

    def update_cover(self, path, image):
        """联网/内嵌封面迟到时，就地补上该行封面（原先只有重扫才会刷新）"""
        if image is None or not path:
            return 0
        try:
            icon = QIcon(rounded_pixmap(image, 36))
        except Exception:
            return 0
        hit = 0
        for r in range(self.rowCount()):
            if self._row_path(r) != path:
                continue
            it = self.item(r, 1)
            if it is None:
                it = QTableWidgetItem("")
                self.setItem(r, 1, it)
            it.setIcon(icon)
            hit += 1
        return hit

    def _on_cell(self, row, col):
        path = self._row_path(row)
        if path:
            self.flash_row(row)
            self.rowActivated.emit([path, self.ordered_visible()])

    def _on_double(self, row, col):
        path = self._row_path(row)
        if path:
            self.flash_row(row)
            self.rowDoubleActivated.emit([path, self.ordered_visible()])

    # ---------- 右键菜单：播放 / 插入下一首 / 加入队列 ----------
    def _context_menu(self, pos):
        row = self.rowAt(pos.y())
        if row < 0:
            return
        path = self._row_path(row)
        if not path:
            return
        title = self.item(row, 2).text() if self.item(row, 2) else ""
        m = QMenu(self)
        m.setStyleSheet(f"""
            QMenu {{ background:#ffffff; border:1px solid {LINE}; padding:6px;
                     color:{INK}; font-size:10pt; }}
            QMenu::item {{ padding:7px 22px; border-radius:6px; }}
            QMenu::item:selected {{ background:{HOVER}; color:{RED}; }}
        """)
        head = m.addAction(f"♪   {title[:26]}")
        head.setEnabled(False)
        m.addSeparator()
        a1 = m.addAction("▶  立即播放")
        a2 = m.addAction("⇥  插入到下一首播放")
        a3 = m.addAction("＋  添加到播放队列末尾")
        m.addSeparator()
        a4 = m.addAction("⛶  进入可视化播放页")
        act = m.exec(self.viewport().mapToGlobal(pos))
        if act == a1:
            self.flash_row(row)
            self.rowActivated.emit([path, self.ordered_visible()])
        elif act == a2:
            self.flash_row(row)
            self.queueNextRequested.emit(path)
        elif act == a3:
            self.queueAddRequested.emit(path)
        elif act == a4:
            self.showPlayerRequested.emit(path)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            r = self.currentRow()
            path = self._row_path(r)
            if path:
                self.flash_row(r)
                self.rowActivated.emit([path, self.ordered_visible()])
        else:
            super().keyPressEvent(e)

    # ---------- 点击动效：整行红色高亮淡出 ----------
    def flash_row(self, row):
        if row is None or row < 0:
            return
        self._flash_row = row
        if self._flash_anim is None:
            self._flash_anim = QVariantAnimation(self)
            self._flash_anim.setDuration(430)
            self._flash_anim.setEasingCurve(QEasingCurve.OutCubic)
            self._flash_anim.valueChanged.connect(self._on_flash)
            self._flash_anim.finished.connect(self._clear_flash)
        self._flash_anim.stop()
        self._flash_anim.setStartValue(0.0)
        self._flash_anim.setEndValue(1.0)
        self._flash_anim.start()

    def _on_flash(self, v):
        row = self._flash_row
        if row is None or row >= self.rowCount():
            return
        a = 0.26 * (1.0 - float(v))
        c = QColor(RED)
        c.setAlphaF(max(0.0, a))
        for col in range(self.columnCount()):
            it = self.item(row, col)
            if it is not None:
                it.setBackground(QBrush(c))

    def _clear_flash(self):
        row = self._flash_row
        self._flash_row = None
        if row is None or row >= self.rowCount():
            return
        for col in range(self.columnCount()):
            it = self.item(row, col)
            if it is not None:
                it.setBackground(QBrush())

    # ---------- 当前播放曲目标记 ----------
    def set_current(self, path):
        self._current_path = path
        for r in range(self.rowCount()):
            it = self.item(r, 0)
            if it is None:
                continue
            hit = (path is not None and self._row_path(r) == path)
            if hit:
                it.setText("▶")
                it.setForeground(QColor(RED))
            else:
                it.setText(str(self._order_index.get(self._row_path(r), r) + 1))
                it.setForeground(QColor(GRAY))

    def load_entries(self, entries):
        """entries: list of dict（库条目），按给定顺序填充"""
        self._entries = list(entries)
        self._entry_map = {e["path"]: e for e in entries}
        self._order_index = {e["path"]: i for i, e in enumerate(entries)}
        self._sort_col = -1
        self._sort_desc = False
        self.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)
        self._fill([e["path"] for e in entries])

    def _icon_for(self, cover, size=36):
        """封面 → QIcon，按 (cacheKey,size) 复用，避免同图反复封装"""
        if cover is None:
            return None
        key = None
        try:
            key = (int(cover.cacheKey()), size)
        except Exception:
            key = None
        if key is not None:
            hit = self._icon_cache.get(key)
            if hit is not None:
                return hit
        try:
            ic = QIcon(rounded_pixmap(cover, size))
        except Exception:
            ic = None
        if key is not None and ic is not None:
            if len(self._icon_cache) >= 600:
                self._icon_cache.clear()
            self._icon_cache[key] = ic
        return ic

    def _fill(self, paths):
        # 整表重填期间冻结视图更新：否则每次 setItem 都会触发一次 viewport
        # 重绘，几百行下来就是几十毫秒的纯浪费
        vp = self.viewport()
        self.setUpdatesEnabled(False)
        vp.setUpdatesEnabled(False)
        try:
            self.setRowCount(len(paths))
            _gray, _ink, _soft = QColor(GRAY), QColor(INK), QColor(SOFT_INK)
            for r, path in enumerate(paths):
                e = self._entry_map.get(path, {})
                c0 = QTableWidgetItem(str(self._order_index.get(path, r) + 1))
                c0.setData(Qt.UserRole, path)
                c0.setForeground(_gray)
                c0.setTextAlignment(Qt.AlignCenter)
                self.setItem(r, 0, c0)
                ic = self._icon_for(e.get("cover"))
                self.setItem(r, 1, QTableWidgetItem(ic, "") if ic else
                             QTableWidgetItem(""))
                t = QTableWidgetItem(e.get("title", ""))
                t.setForeground(_ink)
                self.setItem(r, 2, t)
                a = QTableWidgetItem(e.get("artist", ""))
                a.setForeground(_soft)
                self.setItem(r, 3, a)
                al = QTableWidgetItem(e.get("album") or "—")
                al.setForeground(_soft)
                self.setItem(r, 4, al)
                q = QTableWidgetItem(e.get("format", ""))
                q.setForeground(_gray)
                self.setItem(r, 5, q)
                d = QTableWidgetItem(fmt_time(e.get("duration", 0.0)))
                d.setForeground(_soft)
                d.setData(Qt.UserRole, e.get("duration", 0.0))
                self.setItem(r, 6, d)
        finally:
            vp.setUpdatesEnabled(True)
            self.setUpdatesEnabled(True)
        if self._current_path:
            self.set_current(self._current_path)
        self.apply_filter(self._filter_kw, force=True)

    # ---------------- 排序 / 过滤 ----------------
    SORT_TEXT = {2: "title", 3: "artist", 4: "album", 5: "format"}

    def _on_header(self, col):
        if self._sort_col == col:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_col = col
            self._sort_desc = False
        self.horizontalHeader().setSortIndicator(
            col, Qt.DescendingOrder if self._sort_desc else Qt.AscendingOrder)
        self._resort()

    def _sort_key(self, path):
        e = self._entry_map.get(path, {})
        if self._sort_col == 0:
            return self._order_index.get(path, 0)
        if self._sort_col == 6:
            return float(e.get("duration", 0.0) or 0.0)
        key = self.SORT_TEXT.get(self._sort_col)
        return (e.get(key) or "").lower() if key else ""

    def _resort(self):
        paths = [self._row_path(r) for r in range(self.rowCount())]
        paths.sort(key=self._sort_key, reverse=self._sort_desc)
        self._fill(paths)

    def apply_filter(self, keyword="", force=False):
        self._filter_kw = keyword or ""
        kw = self._filter_kw.strip().lower()
        n = self.rowCount()
        # 快速通道：无关键词且上一轮本来就全可见 → 不必逐行 setRowHidden
        # （每次 setRowHidden 都会让 viewport 失效，600 行的空过滤是纯浪费）
        if not force and not kw and self._all_visible and n == len(self._visible):
            return
        self._visible = []
        if not kw:
            self.setUpdatesEnabled(False)
            try:
                for r in range(n):
                    self.setRowHidden(r, False)
                    p = self._row_path(r)
                    if p:
                        self._visible.append(p)
            finally:
                self.setUpdatesEnabled(True)
            self._all_visible = True
            return
        for r in range(n):
            texts = []
            for c in (2, 3, 4):
                it = self.item(r, c)
                if it:
                    texts.append(it.text().lower())
            ok = any(kw in t for t in texts)
            self.setRowHidden(r, not ok)
            if ok:
                p = self._row_path(r)
                if p:
                    self._visible.append(p)
        self._all_visible = len(self._visible) == n

    def ordered_visible(self):
        """按当前视觉顺序（含排序）返回可见 path 列表"""
        order = []
        for r in range(self.rowCount()):
            if not self.isRowHidden(r):
                p = self._row_path(r)
                if p:
                    order.append(p)
        return order


class SongsPage(QWidget):
    """所有歌曲 / 最近播放页（排序 + 搜索 + 播放全部/随机）"""
    playRequested = Signal(list, str)        # context_paths, start_path
    playAndShowRequested = Signal(list, str) # 双击：播放并进入可视化播放页
    queueNextRequested = Signal(str)         # 右键：插入到下一首播放
    queueAddRequested = Signal(str)          # 右键：加入队列末尾
    showPlayerRequested = Signal(str)        # 右键：进入播放页

    def __init__(self, title="本地音乐", show_tools=True, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(34, 26, 34, 20)
        root.setSpacing(12)

        head = QHBoxLayout()
        head.addWidget(_title_label(title))
        head.addStretch(1)
        if show_tools:
            btn_all = QPushButton("▶  播放全部")
            btn_rand = QPushButton("⤨  随机播放")
            for b in (btn_all, btn_rand):
                b.setCursor(b.cursor())
                b.setStyleSheet(f"""
                    QPushButton {{ color:{INK}; background:#fff; border:1px solid {LINE};
                                    border-radius:14px; padding:6px 16px; font-size:9.5pt; }}
                    QPushButton:hover {{ border:1px solid {RED}; color:{RED}; }}
                """)
            btn_all.clicked.connect(lambda: self._play(self.table.ordered_visible(), first=True))
            btn_rand.clicked.connect(lambda: self._play(self.table.ordered_visible(), random=True))
            head.addWidget(btn_all)
            head.addWidget(btn_rand)
            self.search = QLineEdit()
            self.search.setPlaceholderText("搜索歌曲、歌手、专辑…")
            self.search.setFixedSize(240, 32)
            self.search.setStyleSheet(f"""
                QLineEdit {{ background:#fff; border:1px solid {LINE}; border-radius:16px;
                             padding:0 14px; font-size:9.5pt; color:{INK}; }}
                QLineEdit:focus {{ border:1px solid {INK}; }}
            """)
            self.search.textChanged.connect(self._on_search)
            head.addWidget(self.search)
        root.addLayout(head)

        self.table = SongsTable()
        if show_tools:
            self.search.textChanged.connect(self.table.apply_filter)
        # 关键：连接行点击 —— 单击播放，双击播放并进入可视化播放页
        self.table.rowActivated.connect(self._on_song)
        self.table.rowDoubleActivated.connect(self._on_song_full)
        self.table.queueNextRequested.connect(self.queueNextRequested.emit)
        self.table.queueAddRequested.connect(self.queueAddRequested.emit)
        self.table.showPlayerRequested.connect(self.showPlayerRequested.emit)
        root.addWidget(self.table, 1)
        self.empty_hint = QLabel("库里还没有歌曲 —— 点击左上角「打开文件夹」扫描你的音乐")
        self.empty_hint.setStyleSheet(f"color:{GRAY}; font-size:10pt;")
        self.empty_hint.setAlignment(Qt.AlignCenter)
        root.addWidget(self.empty_hint)

    def _on_search(self, text):
        self.table.apply_filter(text)

    def _on_song(self, payload):
        self.playRequested.emit(payload[1], payload[0])

    def _on_song_full(self, payload):
        self.playAndShowRequested.emit(payload[1], payload[0])

    def _play(self, paths, first=False, random=False):
        if not paths:
            return
        if random:
            import random as _r
            start = _r.choice(paths)
        elif first:
            start = paths[0]
        else:
            start = paths[0]
        self.playRequested.emit(list(paths), start)

    def set_entries(self, entries):
        self.table.load_entries(entries)
        kw = self.search.text() if hasattr(self, "search") else ""
        # load_entries 已按当前关键字过滤过一次，关键字没变就不必再来一遍
        if kw != getattr(self.table, "_filter_kw", None):
            self.table.apply_filter(kw)
        self.empty_hint.setVisible(len(entries) == 0)

    def refresh_cover(self, path, image):
        """封面迟到时就地补该行（不重建表格，保留滚动/排序状态）"""
        try:
            return self.table.update_cover(path, image)
        except Exception:
            return 0


# ============================================================================
# 专辑 / 艺术家 归类网格
# ============================================================================
class GridPage(QWidget):
    """封面卡片网格（专辑 / 艺术家共用）；itemClicked → (key, entry_count)"""
    itemActivated = Signal(str)

    def __init__(self, title, cap, parent=None):
        super().__init__(parent)
        self.cap_text = cap
        root = QVBoxLayout(self)
        root.setContentsMargins(34, 26, 34, 20)
        root.setSpacing(14)
        head = QVBoxLayout()
        head.setSpacing(2)
        head.addWidget(_title_label(title))
        head.addWidget(_cap_label(cap))
        root.addLayout(head)

        self.grid = QListWidget()
        self.grid.setViewMode(QListWidget.IconMode)
        self.grid.setResizeMode(QListWidget.Adjust)
        self.grid.setMovement(QListWidget.Static)
        self.grid.setSpacing(18)
        self.grid.setIconSize(QSize(132, 132))
        self.grid.setGridSize(QSize(150, 190))
        self.grid.setWordWrap(True)
        self.grid.setFrameShape(QListWidget.NoFrame)
        self.grid.setStyleSheet(f"""
            QListWidget {{ background:transparent; border:none; outline:0; }}
            QListWidget::item {{ color:{INK}; font-size:9.5pt; }}
            QListWidget::item:selected {{ background:transparent; color:{RED}; }}
            QListWidget::item:hover {{ color:{RED}; }}
        """)
        self.grid.itemClicked.connect(
            lambda it: self.itemActivated.emit(it.data(Qt.UserRole)))
        self.grid.setStyleSheet(self.grid.styleSheet() + scrollbar_style())
        root.addWidget(self.grid, 1)
        self.empty_hint = QLabel("暂无内容")
        self.empty_hint.setStyleSheet(f"color:{GRAY};")
        self.empty_hint.setAlignment(Qt.AlignCenter)
        root.addWidget(self.empty_hint)

    def _cover_icon(self, entries):
        for e in entries:
            if e.get("cover") is not None:
                return QIcon(rounded_pixmap(e["cover"], 132))
        pm = QPixmap(132, 132)
        pm.fill(QColor(ART_BG))
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(GRAY), 1))
        f = QFont()
        f.setPixelSize(44)
        p.setFont(f)
        p.drawText(QRectF(0, 0, 132, 132), Qt.AlignCenter, "♪")
        p.end()
        pm2 = QPixmap(132, 132)
        pm2.fill(Qt.transparent)
        p2 = QPainter(pm2)
        p2.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, 132, 132), 18, 18)
        p2.setClipPath(path)
        p2.drawPixmap(0, 0, pm)
        p2.end()
        return QIcon(pm2)

    def set_groups(self, groups):
        """groups: OrderedDict key → list of entries（保持首次出现顺序）"""
        self._groups = dict(groups or {})
        g = self.grid
        vp = g.viewport()
        g.setUpdatesEnabled(False)
        vp.setUpdatesEnabled(False)
        try:
            g.clear()
            for key, entries in self._groups.items():
                it = QListWidgetItem(self._cover_icon(entries),
                                     f"{key}\n{len(entries)} 首")
                it.setData(Qt.UserRole, key)
                it.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
                it.setSizeHint(QSize(150, 190))
                g.addItem(it)
        finally:
            vp.setUpdatesEnabled(True)
            g.setUpdatesEnabled(True)
        self.empty_hint.setVisible(len(self._groups) == 0)

    def refresh_covers(self):
        """封面迟到时只重画图标，不动选中/滚动位置"""
        groups = getattr(self, "_groups", None) or {}
        if not groups:
            return 0
        hit = 0
        for i in range(self.grid.count()):
            it = self.grid.item(i)
            entries = groups.get(it.data(Qt.UserRole)) or []
            if not entries:
                continue
            pix = self._cover_icon(entries)
            if not pix.pixmap(132, 132).isNull():
                it.setIcon(pix)
                hit += 1
        return hit


# ============================================================================
# 专辑 / 艺术家 详情（曲目列表，可自由选曲）
# ============================================================================
class DetailPage(QWidget):
    playRequested = Signal(list, str)        # context_paths, start_path
    playAndShowRequested = Signal(list, str) # 双击：播放并进入可视化播放页
    backRequested = Signal()
    queueNextRequested = Signal(str)
    queueAddRequested = Signal(str)
    showPlayerRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries = []
        root = QVBoxLayout(self)
        root.setContentsMargins(34, 22, 34, 20)
        root.setSpacing(14)

        self.btn_back = QPushButton("←  返回")
        self.btn_back.setCursor(Qt.PointingHandCursor)
        self.btn_back.setStyleSheet(f"""
            QPushButton {{ color:{SOFT_INK}; background:transparent; border:none;
                            padding:4px 0; font-size:10pt; text-align:left; }}
            QPushButton:hover {{ color:{RED}; }}
        """)
        self.btn_back.clicked.connect(self.backRequested.emit)
        back_row = QHBoxLayout()
        back_row.addWidget(self.btn_back)
        back_row.addStretch(1)
        root.addLayout(back_row)

        head = QHBoxLayout()
        head.setSpacing(26)
        self.cover = QLabel()
        self.cover.setFixedSize(178, 178)
        self.cover.setAlignment(Qt.AlignCenter)
        self.cover.setStyleSheet(f"""
            background:{ART_BG}; border-radius:16px; color:{GRAY}; font-size:34pt;
        """)
        head.addWidget(self.cover)

        info = QVBoxLayout()
        info.setSpacing(6)
        self.cap = _cap_label("ALBUM")
        self.name = _title_label("—", size=30)
        self.name.setWordWrap(True)
        self.sub = QLabel("")
        self.sub.setStyleSheet(f"color:{SOFT_INK}; font-size:11pt;")
        self.meta = QLabel("")
        self.meta.setStyleSheet(f"color:{GRAY}; font-size:9pt;")
        info.addWidget(self.cap)
        info.addWidget(self.name)
        info.addWidget(self.sub)
        info.addWidget(self.meta)
        info.addSpacing(10)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.btn_play_all = CircleButton(48, "play", style="outline")
        self.btn_play_all.clicked.connect(
            lambda: self._emit(0) if self._entries else None)
        btn_row.addWidget(self.btn_play_all)
        self.btn_all = pill_button("▶  播放全部")
        self.btn_all.clicked.connect(
            lambda: self._emit(0) if self._entries else None)
        btn_row.addWidget(self.btn_all)
        self.btn_rand = pill_button("⤨  随机播放")
        self.btn_rand.clicked.connect(lambda: self._emit(-1) if self._entries else None)
        btn_row.addWidget(self.btn_rand)
        btn_row.addStretch(1)
        info.addLayout(btn_row)
        info.addStretch(1)
        head.addLayout(info, 1)
        root.addLayout(head)

        self.table = SongsTable()
        self.table.rowActivated.connect(self._on_row)
        self.table.rowDoubleActivated.connect(self._on_row_full)
        self.table.queueNextRequested.connect(self.queueNextRequested.emit)
        self.table.queueAddRequested.connect(self.queueAddRequested.emit)
        self.table.showPlayerRequested.connect(self.showPlayerRequested.emit)
        root.addWidget(self.table, 1)

    def _on_row(self, payload):
        self.playRequested.emit(payload[1], payload[0])

    def _on_row_full(self, payload):
        self.playAndShowRequested.emit(payload[1], payload[0])

    def _emit(self, which):
        paths = [e["path"] for e in self._entries]
        if not paths:
            return
        if which < 0:
            import random as _r
            start = _r.choice(paths)
        else:
            start = paths[0]
        self.playRequested.emit(paths, start)

    def set_detail(self, name, entries, cap="ALBUM", cover=None):
        self._entries = list(entries)
        self.name.setText(name)
        self.cap.setText(cap)
        self.table.load_entries(self._entries)
        self.table.apply_filter("")
        artists = sorted({e["artist"] for e in self._entries if e["artist"]})
        albums = sorted({e["album"] for e in self._entries if e["album"]})
        if cap.startswith("ALBUM"):
            sub = " · ".join(artists[:2]) if artists else "未知艺术家"
        else:
            sub = f"{len(albums)} 张专辑" if len(albums) > 1 else (
                albums[0] if albums else "未知专辑")
        self.sub.setText(sub)
        total = sum(e["duration"] for e in self._entries)
        fmts = sorted({e["format"] for e in self._entries if e["format"]})
        self.meta.setText(f"{len(self._entries)} 首  ·  {fmt_total(total)}"
                          + (f"  ·  {'/'.join(fmts)}" if fmts else ""))
        if cover is not None:
            self.cover.setPixmap(rounded_pixmap(cover, 178, 16))
            self.cover.setText("")
        else:
            self.cover.setPixmap(QPixmap())
            self.cover.setText("♪")

    def refresh_cover(self, path=None, image=None):
        """封面迟到时刷新详情页大图与曲目行封面"""
        for e in self._entries:
            if e.get("cover") is None:
                continue
            try:
                self.cover.setPixmap(rounded_pixmap(e["cover"], 178, 16))
                self.cover.setText("")
            except Exception:
                pass
            break
        if path and image is not None:
            return self.table.update_cover(path, image)
        return 0


# ============================================================================
# 首页（主菜单）
# ============================================================================
class HomePage(QWidget):
    playRequested = Signal(list, str)   # context, start
    openFolderRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.last_path = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setStyleSheet("background:transparent;" + scrollbar_style())
        outer.addWidget(scroll, 1)
        body = QWidget()
        body.setStyleSheet("background:transparent;")
        scroll.setWidget(body)
        root = QVBoxLayout(body)
        root.setContentsMargins(34, 26, 34, 24)
        root.setSpacing(20)

        g, date_s = greet()
        head = QVBoxLayout()
        head.setSpacing(2)
        head.addWidget(_cap_label(date_s.upper()))
        head.addWidget(_title_label(g, size=36))
        root.addLayout(head)

        # ---- 上次播放卡片 ----
        self.card = QWidget()
        self.card.setStyleSheet(f"""
            QWidget#homeCard {{ background:#fff; border:1px solid {LINE};
                                border-radius:16px; }}
        """)
        self.card.setObjectName("homeCard")
        card_lay = QHBoxLayout(self.card)
        card_lay.setContentsMargins(28, 24, 28, 24)
        card_lay.setSpacing(24)
        left = QVBoxLayout()
        left.setSpacing(6)
        tag = QLabel("·  上次播放")
        tag.setStyleSheet(f"color:{RED}; font-size:9pt; font-weight:600;")
        self.last_title = _title_label("—", size=26)
        self.last_title.setWordWrap(True)
        self.last_artist = QLabel("")
        self.last_artist.setStyleSheet(f"color:{SOFT_INK}; font-size:10.5pt;")
        self.last_meta = QLabel("")
        self.last_meta.setStyleSheet(f"color:{GRAY}; font-size:9pt;")
        left.addWidget(tag)
        left.addWidget(self.last_title)
        left.addWidget(self.last_artist)
        left.addWidget(self.last_meta)
        left.addSpacing(10)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.btn_play_last = CircleButton(52, "play", style="outline")
        self.btn_play_last.clicked.connect(self._play_last)
        btn_row.addWidget(self.btn_play_last)
        self.btn_shuffle = QPushButton("⤨  随机听一首")
        self.btn_shuffle.setStyleSheet(f"""
            QPushButton {{ color:{SOFT_INK}; background:transparent; border:1px solid {LINE};
                            border-radius:16px; padding:8px 18px; font-size:9.5pt; }}
            QPushButton:hover {{ color:{RED}; border:1px solid {RED}; }}
        """)
        self.btn_shuffle.clicked.connect(self._play_random)
        btn_row.addWidget(self.btn_shuffle)
        btn_row.addStretch(1)
        left.addLayout(btn_row)
        card_lay.addLayout(left, 1)
        self.last_cover = QLabel()
        self.last_cover.setFixedSize(180, 180)
        self.last_cover.setAlignment(Qt.AlignCenter)
        self.last_cover.setStyleSheet(f"""
            background:{ART_BG}; border-radius:14px; font-size:34pt; color:{GRAY};
        """)
        card_lay.addWidget(self.last_cover)
        root.addWidget(self.card)

        self.no_music_hint = QLabel("")
        self.no_music_hint.setStyleSheet(f"""
            color:{GRAY}; border:1px dashed {LIGHT}; border-radius:12px;
            padding:26px; font-size:10pt; background:{PANEL};
        """)
        self.no_music_hint.setAlignment(Qt.AlignCenter)
        self.no_music_hint.setText("音乐库还是空的 —— 点击左上角「打开文件夹」\n选择你的音乐目录，稍等片刻即可建立曲库")
        root.addWidget(self.no_music_hint)

        # ---- 统计 ----
        stats = QHBoxLayout()
        stats.setSpacing(0)
        self.stat_labels = {}
        for key, cap in (("songs", "首歌曲"), ("albums", "张专辑"),
                         ("artists", "位艺术家"), ("time", "听歌时长")):
            box = QVBoxLayout()
            box.setSpacing(2)
            val = QLabel("--")
            val.setStyleSheet(f"color:{INK}; font-size:22pt; font-weight:700;")
            c = QLabel(cap)
            c.setStyleSheet(f"color:{GRAY}; font-size:9pt;")
            self.stat_labels[key] = val
            box.addWidget(val)
            box.addWidget(c)
            wrap = QWidget()
            wrap.setLayout(box)
            stats.addWidget(wrap, 1)
        root.addLayout(stats)

        # ---- 最近播放 ----
        recent_head = QHBoxLayout()
        recent_head.addWidget(_cap_label("RECENT  ·  最近播放"))
        recent_head.addStretch(1)
        root.addLayout(recent_head)
        self.recent_row = QHBoxLayout()
        self.recent_row.setSpacing(14)
        self.recent_row.addStretch(1)
        self._recent_wrap = QWidget()
        self._recent_wrap.setLayout(self.recent_row)
        root.addWidget(self._recent_wrap)
        root.addStretch(1)

    def update_home(self, library, store, lib_map):
        self._lib_map = lib_map
        albums = {e["album"] for e in library if e["album"]}
        artists = {e["artist"] for e in library if e["artist"]}
        self.stat_labels["songs"].setText(str(len(library)))
        self.stat_labels["albums"].setText(str(len(albums)))
        self.stat_labels["artists"].setText(str(len(artists)))
        # 第四项统计：用这个软件累计听歌的时长（不是曲库总时长）
        listened = float(getattr(store, "listened", 0.0) or 0.0)
        self.stat_labels["time"].setText(
            fmt_total(listened) if listened >= 60 else f"{int(listened)} 秒")
        has_music = len(library) > 0
        self.no_music_hint.setVisible(not has_music)

        # 上次播放
        self.last_path = store.last
        e = lib_map.get(store.last) if store.last else None
        if e is None and library:
            e = library[0]
            self.last_path = e["path"]
        if e is not None:
            self.last_title.setText(e["title"])
            self.last_artist.setText(e["artist"])
            self.last_meta.setText(f"{e['album'] or '—'}  ·  {e['format']}"
                                   + (f" · {e['bit_depth']}" if e["bit_depth"] != "--" else "")
                                   + (f" · {e['sample_rate']}" if e["sample_rate"] != "--" else ""))
            self.btn_play_last.setVisible(True)
        else:
            self.last_title.setText("还没有播放记录")
            self.last_artist.setText("去「所有歌曲」挑一首开始吧")
            self.last_meta.setText("")
            self.btn_play_last.setVisible(False)
        if e is not None and e.get("cover") is not None:
            self.last_cover.setPixmap(rounded_pixmap(e["cover"], 180, 16))
            self.last_cover.setText("")
        else:
            self.last_cover.setPixmap(QPixmap())
            self.last_cover.setText("♪")

        # 最近播放小卡
        while self.recent_row.count() > 1:
            it = self.recent_row.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
        count = 0
        self._recent_cards = []
        for path in store.recent:
            e = lib_map.get(path)
            if e is None:
                continue
            card = QWidget()
            card.setFixedWidth(120)
            v = QVBoxLayout(card)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(6)
            cov = QLabel()
            cov.setFixedSize(120, 120)
            cov.setAlignment(Qt.AlignCenter)
            cov.setStyleSheet(f"background:{ART_BG}; border-radius:10px; color:{GRAY}; font-size:22pt;")
            if e.get("cover") is not None:
                cov.setPixmap(rounded_pixmap(e["cover"], 120, 10))
                cov.setText("")
            else:
                cov.setText("♪")
            self._recent_cards.append((path, cov))
            t = QLabel(e["title"])
            t.setStyleSheet(f"color:{INK}; font-size:8.5pt;")
            t.setWordWrap(True)
            t.setMaximumWidth(120)
            v.addWidget(cov)
            v.addWidget(t)
            card.setCursor(Qt.PointingHandCursor)
            card.mousePressEvent = lambda ev, p=path: self.playRequested.emit([p], p)
            self.recent_row.insertWidget(self.recent_row.count() - 1, card)
            count += 1
            if count >= 6:
                break

    def _play_last(self):
        if self.last_path:
            self.playRequested.emit([self.last_path], self.last_path)

    def refresh_covers(self):
        """封面迟到：就地更新「上次播放」大图与最近播放小卡"""
        lm = getattr(self, "_lib_map", None) or {}
        e = lm.get(getattr(self, "last_path", None))
        if e is not None and e.get("cover") is not None:
            try:
                self.last_cover.setPixmap(rounded_pixmap(e["cover"], 180, 16))
                self.last_cover.setText("")
            except Exception:
                pass
        hit = 0
        for path, lab in getattr(self, "_recent_cards", []):
            entry = lm.get(path)
            if entry is None or entry.get("cover") is None:
                continue
            try:
                lab.setPixmap(rounded_pixmap(entry["cover"], 120, 10))
                lab.setText("")
                hit += 1
            except Exception:
                pass
        return hit

    def _play_random(self):
        import random as _r
        # 交给主窗口在整个库里随机
        self.playRequested.emit([], "__random__")


# ============================================================================
# 可视化播放页（原播放器）
# ============================================================================
class PlayerPage(QWidget):
    """全屏沉浸式播放页：左可视化 / 右歌词 + 可隐藏频谱 / 队列抽屉 / 联网开关

    背景为「流体幻彩 / 自定义图片」时，页面进入**毛玻璃模式**：所有卡片与
    按钮都改用「背景虚化背板 + 浅色着色 + 高光描边」，真正融进背景，而不是
    一块块突兀的纯白色块。
    """
    prevClicked = Signal()
    playClicked = Signal()
    nextClicked = Signal()
    volumeChanged = Signal(int)
    exitRequested = Signal()
    settingChanged = Signal(str, bool)     # (key, value)  auto_lyrics / auto_cover
    queuePlayRequested = Signal(str)
    queueNextRequested = Signal(str)
    queueRemoveRequested = Signal(str)
    lyricRefreshRequested = Signal()
    lyricStyleChanged = Signal(dict)
    bgChanged = Signal(dict)           # {bg_mode, bg_image, bg_veil, weather}

    _imm_on = False                    # 类属性 → 构造期间查找也安全

    def __init__(self, parent=None):
        super().__init__(parent)
        # 背景层（最先创建 → 位于所有子控件之下）
        self.bg = FluidBackground(self)
        self.spec_hidden = False
        self.queue_open = False
        self._bg_vals = {"bg_mode": "fluid", "bg_image": "", "bg_veil": 0.22,
                         "weather": "none"}
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 10, 26, 12)
        root.setSpacing(9)

        # ---------------- 顶部条：退出 / 标题 / 开关 / 背景 / 样式 / 频谱 / 队列 ----
        top = QHBoxLayout()
        top.setSpacing(10)
        self.btn_exit = QPushButton("←  返回")
        self.btn_exit.setCursor(Qt.PointingHandCursor)
        self.btn_exit.clicked.connect(self.exitRequested.emit)
        top.addWidget(self.btn_exit)

        self.top_title = QLabel("正在播放")
        self.top_title.setStyleSheet(
            f"color:{SOFT_INK}; font-size:10pt; letter-spacing:2px;")
        self.top_title.setAlignment(Qt.AlignCenter)
        top.addWidget(self.top_title, 1)

        self.sw_lyrics = ToggleSwitch("自动歌词", True)
        self.sw_cover = ToggleSwitch("自动封面", True)
        # 「全屏」默认关：进入播放页只收起侧栏/底栏，保持窗口大小
        self.sw_full = ToggleSwitch("全屏", False)
        self.sw_lyrics.toggled.connect(
            lambda v: self.settingChanged.emit("auto_lyrics", v))
        self.sw_cover.toggled.connect(
            lambda v: self.settingChanged.emit("auto_cover", v))
        self.sw_full.toggled.connect(
            lambda v: self.settingChanged.emit("auto_fullscreen", v))
        top.addWidget(self.sw_lyrics)
        top.addWidget(self.sw_cover)
        top.addWidget(self.sw_full)

        self.btn_style = QPushButton("字")
        self.btn_style.setToolTip("歌词样式：颜色 / 字号 / 对齐")
        self.btn_bg = QPushButton("背景")
        self.btn_bg.setToolTip("播放页背景：流体 / 自定义图片 / 飘雪 / 下雨")
        self.btn_spec = QPushButton("隐藏频谱")
        self.btn_queue = QPushButton("队列")
        for b in (self.btn_style, self.btn_bg, self.btn_spec, self.btn_queue):
            b.setCursor(Qt.PointingHandCursor)
        self.btn_queue.setCheckable(True)
        self.btn_spec.setCheckable(True)
        self.btn_spec.clicked.connect(self.toggle_spectrum)
        self.btn_queue.clicked.connect(lambda c: self.toggle_queue(c))
        self.btn_style.clicked.connect(self.open_style_panel)
        self.btn_bg.clicked.connect(self.open_bg_panel)
        top.addWidget(self.btn_bg)
        top.addWidget(self.btn_style)
        top.addWidget(self.btn_spec)
        top.addWidget(self.btn_queue)
        root.addLayout(top)
        self._chrome_btns = [self.btn_style, self.btn_bg, self.btn_spec,
                             self.btn_queue]

        # ---------------- 主体 ----------------
        body = QHBoxLayout()
        body.setSpacing(20)

        # ---- 左：环形可视化（直接画在背景上）+ 曲目卡 + 参数卡 ----
        left = QVBoxLayout()
        left.setSpacing(10)
        self.viz = VisualizerWidget()
        left.addWidget(self.viz, 1)

        self.track_card = GlassPanel(radius=20, margins=(20, 14, 20, 15),
                                     spacing=3)
        tcb = self.track_card.body()
        self.src_label = QLabel("")
        self.src_label.setStyleSheet(
            f"color:{RED}; font-size:8.5pt; letter-spacing:1px;")
        self.title_lbl = QLabel("未在播放")
        self.title_lbl.setStyleSheet(
            f"color:{INK}; font-size:23pt; font-weight:600;")
        self.title_lbl.setWordWrap(True)
        self.artist_lbl = QLabel("从曲库里挑一首开始")
        self.artist_lbl.setStyleSheet(f"color:{SOFT_INK}; font-size:11pt;")
        tcb.addWidget(self.src_label)
        tcb.addWidget(self.title_lbl)
        tcb.addWidget(self.artist_lbl)
        left.addWidget(self.track_card)

        self.info_card = GlassPanel(radius=18, margins=(20, 12, 20, 12))
        self.info = InfoGrid()
        self.info_card.body().addWidget(self.info)
        left.addWidget(self.info_card)
        body.addLayout(left, 40)

        # ---- 右：歌词卡 + 频谱卡 ----
        right = QVBoxLayout()
        right.setSpacing(10)

        self.lyric_card = GlassPanel(radius=20, margins=(20, 12, 20, 10),
                                     spacing=6)
        lcb = self.lyric_card.body()
        rhead = QHBoxLayout()
        self.lyric_cap = QLabel("LYRICS  ·  歌词")
        self.lyric_cap.setStyleSheet(
            f"color:{GRAY}; font-size:8pt; letter-spacing:2px;")
        rhead.addWidget(self.lyric_cap)
        rhead.addStretch(1)
        self.btn_lyric_refresh = QPushButton("重新获取")
        self.btn_lyric_refresh.setCursor(Qt.PointingHandCursor)
        self.btn_lyric_refresh.setStyleSheet(f"""
            QPushButton {{ color:{GRAY}; background:transparent; border:none;
                            font-size:9pt; padding:3px 6px; }}
            QPushButton:hover {{ color:{RED}; }}
        """)
        self.btn_lyric_refresh.clicked.connect(self.lyricRefreshRequested.emit)
        rhead.addWidget(self.btn_lyric_refresh)
        lcb.addLayout(rhead)
        self.lyrics = LyricsWidget()
        self.lyrics.set_card_alpha(255)   # 玻璃由外层卡片承担，内部不再自绘
        lcb.addWidget(self.lyrics, 1)
        right.addWidget(self.lyric_card, 1)

        self.spec_card = GlassPanel(radius=18, margins=(12, 10, 12, 6),
                                    spacing=2)
        scb = self.spec_card.body()
        self.spec = SpectrumWidget()
        self.spec.setMinimumHeight(150)
        self.spec.setFixedHeight(178)
        scb.addWidget(self.spec)
        self.timeline = TimelineWidget()
        scb.addWidget(self.timeline)
        right.addWidget(self.spec_card)
        body.addLayout(right, 60)

        # ---- 队列抽屉（可展开，内容同样走毛玻璃） ----
        self.drawer = QWidget()
        self.drawer.setMaximumWidth(0)
        dl = QVBoxLayout(self.drawer)
        dl.setContentsMargins(6, 0, 0, 0)
        self.queue_card = GlassPanel(radius=18, margins=(14, 12, 12, 12),
                                     spacing=6)
        qcb = self.queue_card.body()
        self.queue_cap = qcap = QLabel("QUEUE  ·  播放队列")
        qcap.setStyleSheet(f"color:{GRAY}; font-size:8pt; letter-spacing:2px;")
        qcb.addWidget(qcap)
        self.queue_list = QueueList()
        self.queue_list.playRequested.connect(self.queuePlayRequested.emit)
        self.queue_list.insertNextRequested.connect(self.queueNextRequested.emit)
        self.queue_list.removeRequested.connect(self.queueRemoveRequested.emit)
        qcb.addWidget(self.queue_list, 1)
        self.queue_tip = tip = QLabel("右键歌曲：立即播放 / 插入到下一首 / 移除")
        tip.setStyleSheet(f"color:{LIGHT}; font-size:8pt;")
        tip.setWordWrap(True)
        qcb.addWidget(tip)
        dl.addWidget(self.queue_card)
        body.addWidget(self.drawer)
        root.addLayout(body, 1)

        # ---------------- 底部控制（整条玻璃栏） ----------------
        self.bottom_card = GlassPanel(radius=22, margins=(18, 8, 18, 8))
        bottom = QHBoxLayout()
        bottom.setSpacing(14)
        self.btn_prev = CircleButton(42, "prev", style="ghost")
        self.btn_play = CircleButton(54, "play", style="outline")
        self.btn_next = CircleButton(42, "next", style="ghost")
        self.btn_prev.clicked.connect(self.prevClicked.emit)
        self.btn_play.clicked.connect(self.playClicked.emit)
        self.btn_next.clicked.connect(self.nextClicked.emit)
        bottom.addWidget(self.btn_prev)
        bottom.addWidget(self.btn_play)
        bottom.addWidget(self.btn_next)
        bottom.addSpacing(16)
        self.bpm_lbl = QLabel("BPM  --          GENRES  --          DURATION  --")
        self.bpm_lbl.setStyleSheet(
            f"color:{GRAY}; font-size:8.5pt; letter-spacing:1px;")
        bottom.addWidget(self.bpm_lbl)
        bottom.addStretch(1)
        self.quality_lbl = QLabel("")
        self.quality_lbl.setStyleSheet(f"color:{SOFT_INK}; font-size:9.5pt;")
        self.quality_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        bottom.addWidget(self.quality_lbl)
        bottom.addSpacing(14)
        self.vol_cap = vol_cap = QLabel("VOL")
        vol_cap.setStyleSheet(f"color:{GRAY}; font-size:8pt; letter-spacing:1px;")
        bottom.addWidget(vol_cap)
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setValue(85)
        self.vol.setFixedWidth(104)
        self.vol.setStyleSheet(slider_style(104))
        self.vol.valueChanged.connect(self.volumeChanged.emit)
        bottom.addWidget(self.vol)
        self.bottom_card.body().addLayout(bottom)
        root.addWidget(self.bottom_card)

        self._drawer_anim = QVariantAnimation(self)
        self._drawer_anim.setDuration(240)
        self._drawer_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._drawer_anim.valueChanged.connect(self._on_drawer)

        # 氛围特效层（最后创建 + 置顶 → 覆盖整个播放界面，鼠标穿透）
        self.weather = WeatherOverlay(self)
        self.weather.raise_()
        self.bg.lower()

        self._apply_chrome()
        self._apply_text_theme()
        self._sync_imm_widgets()

    # ---------------- 尺寸 / 显示 ----------------
    def resizeEvent(self, e):
        try:
            self.bg.setGeometry(0, 0, self.width(), self.height())
            self.weather.setGeometry(0, 0, self.width(), self.height())
        except Exception:
            pass
        super().resizeEvent(e)

    def showEvent(self, e):
        super().showEvent(e)
        self._apply_text_theme()
        self._sync_imm_widgets()

    # ---------------- 沉浸模式 ----------------
    # 背景一开就整页切到「沉浸」：所有卡片/底板都不再绘制（GlassPanel 直接
    # return），文字与图标转成浅色直接压在流动背景上 —— 这是"组件完全融进
    # 背景"的关键。关掉背景（纯净白）则整体退回原来的雪白风格。
    def _set_imm(self, on):
        on = bool(on)
        # 幂等：无论是否切换都保证全局配色与当前模式一致（外部可能改过主题）
        _W.set_theme(on)
        if on == self._imm_on:
            return
        self._imm_on = on
        self._apply_chrome()
        self._apply_text_theme()
        self._sync_imm_widgets()
        for c in self.findChildren(QWidget):
            c.update()

    def _apply_chrome(self):
        """顶部胶囊按钮：沉浸模式下静止时完全透明，hover 才浮起一层极淡的白"""
        c = _W
        if self._imm_on:
            css = f"""
                QPushButton {{ color:{c.INK}; background:transparent;
                                border:1px solid transparent;
                                border-radius:16px; padding:7px 16px;
                                font-size:9.5pt; }}
                QPushButton:hover {{ color:#ffffff;
                                     background:rgba(255,255,255,20%);
                                     border:1px solid rgba(255,255,255,36%); }}
                QPushButton:checked {{ color:#ffffff;
                                       background:rgba(255,255,255,26%);
                                       border:1px solid rgba(255,255,255,50%); }}
            """
            exit_css = f"""
                QPushButton {{ color:{c.INK}; background:transparent;
                                border:1px solid transparent;
                                border-radius:16px; padding:7px 18px;
                                font-size:10pt; }}
                QPushButton:hover {{ color:#ffffff;
                                     background:rgba(255,255,255,20%);
                                     border:1px solid rgba(255,255,255,36%); }}
            """
        else:
            css = f"""
                QPushButton {{ color:{c.INK}; background:#fff;
                                border:1px solid {c.LINE};
                                border-radius:16px; padding:7px 16px;
                                font-size:9.5pt; }}
                QPushButton:hover {{ color:{c.RED}; border:1px solid {c.RED}; }}
                QPushButton:checked {{ color:#fff; background:{c.RED};
                                       border:1px solid {c.RED}; }}
            """
            exit_css = f"""
                QPushButton {{ color:{c.SOFT_INK}; background:#fff;
                                border:1px solid {c.LINE}; border-radius:16px;
                                padding:7px 18px; font-size:10pt; }}
                QPushButton:hover {{ color:{c.RED}; border:1px solid {c.RED}; }}
            """
        for b in self._chrome_btns:
            b.setStyleSheet(css)
        self.btn_exit.setStyleSheet(exit_css)

    def _apply_text_theme(self, force=False):
        """播放页所有文字/说明跟随当前配色（沉浸=浅色，明亮=深色）

        每次进入播放页都会走一遍这里，而 setStyleSheet 会触发控件 polish +
        重绘（连同 info 的 16 个标签、队列每一行）。配色没变就直接跳过。
        """
        key = (self._imm_on, _W.INK, _W.SOFT_INK, _W.GRAY, _W.LIGHT)
        if not force and key == getattr(self, "_theme_key", None):
            return
        self._theme_key = key
        c = _W
        self.top_title.setStyleSheet(
            f"color:{c.SOFT_INK}; font-size:10pt; letter-spacing:2px;")
        self.src_label.setStyleSheet(
            f"color:{c.RED}; font-size:8.5pt; letter-spacing:1px;")
        self.title_lbl.setStyleSheet(
            f"color:{c.INK}; font-size:23pt; font-weight:600;")
        self.artist_lbl.setStyleSheet(f"color:{c.SOFT_INK}; font-size:11pt;")
        self.lyric_cap.setStyleSheet(
            f"color:{c.GRAY}; font-size:8pt; letter-spacing:2px;")
        self.btn_lyric_refresh.setStyleSheet(f"""
            QPushButton {{ color:{c.GRAY}; background:transparent; border:none;
                            font-size:9pt; padding:3px 6px; }}
            QPushButton:hover {{ color:{c.RED}; }}
        """)
        self.bpm_lbl.setStyleSheet(
            f"color:{c.GRAY}; font-size:8.5pt; letter-spacing:1px;")
        self.quality_lbl.setStyleSheet(f"color:{c.SOFT_INK}; font-size:9.5pt;")
        self.vol_cap.setStyleSheet(
            f"color:{c.GRAY}; font-size:8pt; letter-spacing:1px;")
        self.queue_cap.setStyleSheet(
            f"color:{c.GRAY}; font-size:8pt; letter-spacing:2px;")
        self.queue_tip.setStyleSheet(f"color:{c.LIGHT}; font-size:8pt;")
        self.info.restyle()
        self.queue_list.restyle()

    def _sync_imm_widgets(self):
        """沉浸模式 → 圆钮 / 滑杆 / 可视化配色同步切换"""
        g = self._imm_on
        self.btn_prev.set_style("glass" if g else "ghost")
        self.btn_play.set_style("solid" if g else "outline")
        self.btn_next.set_style("glass" if g else "ghost")
        if getattr(self, "_slider_key", None) != g:
            self._slider_key = g
            self.vol.setStyleSheet(slider_style_glass(104) if g
                                   else slider_style(104))
        self.viz.set_on_background(g)
        self.spec.set_on_background(g)
        self.timeline.set_on_background(g)

    # ---------------- 背景 / 氛围特效 ----------------
    def set_bg_cover(self, image):
        """封面变化时同步刷新流体背景主色"""
        self.bg.set_cover(image)

    def apply_background(self, d):
        d = d or {}
        mode = d.get("bg_mode", self._bg_vals.get("bg_mode", "fluid"))
        image = d.get("bg_image", self._bg_vals.get("bg_image", ""))
        veil = d.get("bg_veil", self._bg_vals.get("bg_veil", 0.22))
        weather = d.get("weather", self._bg_vals.get("weather", "none"))
        self._bg_vals = {"bg_mode": mode, "bg_image": image, "bg_veil": veil,
                         "weather": weather}
        self.bg.set_dim(veil)
        if image:
            self.bg.set_image(image)
        self.bg.set_mode(mode)
        self.weather.set_mode(weather)
        self._set_imm(mode != "none")
        if getattr(self, "_bg_panel", None) is not None:
            self._bg_panel.set_values(mode, image, veil, weather)

    def open_bg_panel(self):
        if getattr(self, "_bg_panel", None) is None:
            self._bg_panel = BackgroundPanel(self)
            self._bg_panel.bgChanged.connect(self._on_bg)
            self._bg_panel.imagePickRequested.connect(self._pick_bg_image)
        v = self._bg_vals
        self._bg_panel.set_values(v["bg_mode"], v["bg_image"], v["bg_veil"],
                                  v["weather"])
        self._bg_panel.adjustSize()
        btn = self.btn_bg
        pos = btn.mapToGlobal(QPoint(0, btn.height() + 8))
        self._bg_panel.move(pos.x() - self._bg_panel.width() + btn.width(),
                            pos.y())
        self._bg_panel.show()

    def _pick_bg_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择背景图片", "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.webp *.gif);;所有文件 (*.*)")
        if not path:
            return
        ok = self.bg.set_image(path)
        if ok:
            self._bg_vals["bg_image"] = path
            self._bg_vals["bg_mode"] = "image"
            self.bg.set_mode("image")
            self._set_imm(True)
            if getattr(self, "_bg_panel", None) is not None:
                self._bg_panel.set_image(path)
            self._emit_bg()
        else:
            self.lyric_cap.setText("LYRICS  ·  背景图片加载失败")

    def _on_bg(self, d):
        self.apply_background(d)
        self._emit_bg()

    def _emit_bg(self):
        self.bgChanged.emit(dict(self._bg_vals))

    # ---------------- 频谱 / 队列 显隐 ----------------
    def toggle_spectrum(self):
        self.spec_hidden = not self.spec_hidden
        self.spec.setVisible(not self.spec_hidden)
        self.timeline.setVisible(not self.spec_hidden)
        self.spec_card.setVisible(not self.spec_hidden)
        self.btn_spec.setText("显示频谱" if self.spec_hidden else "隐藏频谱")
        self.btn_spec.setChecked(self.spec_hidden)

    def toggle_queue(self, on=None):
        self.queue_open = (not self.queue_open) if on is None else bool(on)
        self.btn_queue.setChecked(self.queue_open)
        self._drawer_anim.stop()
        self._drawer_anim.setStartValue(float(self.drawer.maximumWidth()))
        self._drawer_anim.setEndValue(330.0 if self.queue_open else 0.0)
        self._drawer_anim.start()

    def _on_drawer(self, v):
        self.drawer.setMaximumWidth(int(v))
        self.drawer.setMinimumWidth(int(v) if v > 2 else 0)

    # ---------------- 歌词 / 队列数据 ----------------
    def set_lyrics(self, lines):
        self.lyrics.set_lyrics(lines)

    def set_plain_lyrics(self, text):
        self.lyrics.set_plain_text(text)

    def set_lyric_hint(self, text):
        self.lyrics.set_hint(text)
        self.lyrics.clear()
        self.lyrics.update()

    def apply_lyric_style(self, color, active, size, align):
        self.lyrics.set_style(color=color, active=active, size=size, align=align)
        if getattr(self, "_style_panel", None) is not None:
            self._style_panel.set_values(color, active, size, align)

    def open_style_panel(self):
        lw = self.lyrics
        if getattr(self, "_style_panel", None) is None:
            self._style_panel = LyricStylePanel(self)
            self._style_panel.styleChanged.connect(self._on_style)
        cur = {"color": lw.color.name(), "active": lw.active_color.name(),
               "size": lw.font_size, "align": lw.align}
        self._style_panel.set_values(cur["color"], cur["active"],
                                    cur["size"], cur["align"])
        self._style_panel.adjustSize()
        btn = self.btn_style
        pos = btn.mapToGlobal(QPoint(0, btn.height() + 8))
        self._style_panel.move(pos.x() - self._style_panel.width() + btn.width(),
                               pos.y())
        self._style_panel.show()

    def _on_style(self, d):
        self.lyrics.set_style(color=d.get("color"), active=d.get("active"),
                              size=d.get("size"), align=d.get("align"))
        self.lyricStyleChanged.emit(d)

    def sync_lyrics(self, pos):
        self.lyrics.set_time(pos)

    def update_queue(self, paths, meta, current=None):
        self.queue_list.load_queue(paths, meta, current)
        self.btn_queue.setText(f"队列 {len(paths)}")

    def set_settings(self, auto_lyrics=True, auto_cover=True,
                     auto_fullscreen=False):
        self.sw_lyrics.set_checked(auto_lyrics)
        self.sw_cover.set_checked(auto_cover)
        self.sw_full.set_checked(auto_fullscreen)

    def fade_titles(self):
        for lbl in (self.title_lbl, self.artist_lbl):
            eff = QGraphicsOpacityEffect(lbl)
            eff.setOpacity(0.25)
            lbl.setGraphicsEffect(eff)
            from PySide6.QtCore import QPropertyAnimation, QEasingCurve
            a = QPropertyAnimation(eff, b"opacity", lbl)
            a.setStartValue(0.25)
            a.setEndValue(1.0)
            a.setDuration(220)
            a.setEasingCurve(QEasingCurve.OutCubic)
            a.finished.connect(lambda: lbl.setGraphicsEffect(None))
            a.finished.connect(a.deleteLater)
            a.start()
            self._fade_refs.append(a)

    _fade_refs = []
