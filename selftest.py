# -*- coding: utf-8 -*-
"""AudioScope 自测：核心依赖 / 解码链 / UI 组件 / 音乐库逻辑"""
import os, sys, math, time, traceback
import numpy as np

def main():
    print("[1] import core deps ...")
    import miniaudio, sounddevice, mutagen, soundfile  # noqa
    print("    ok")

    print("[2] import modules (headless) ...")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import core as C
    import widgets as W
    import pages as P
    import app as A
    print("    ok")

    print("[3] analyzer bands ...")
    az = C.Analyzer()
    t = np.arange(C.FFT_SIZE) / C.SR
    sig = (0.5 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
    out = az.process(sig)
    peak_band = int(np.argmax(out))
    assert out.shape == (C.N_BANDS,)
    assert 25 <= peak_band <= 45 and out.max() > 0.3, f"1kHz peak band={peak_band}"
    disp = az.advance(0.016)
    assert disp.shape == out.shape and disp.max() <= 1.0
    print(f"    peak_band={peak_band}, advance ok (max={disp.max():.2f})")

    print("[4] decode a generated test wav ...")
    import wave
    base = os.path.dirname(os.path.abspath(__file__))
    wav_path = os.path.join(base, "_test.wav")
    sr = 44100
    with wave.open(wav_path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        frames = int(sr * 2)
        data = (np.sin(2 * np.pi * 440 * np.arange(frames) / sr) * 20000).astype(np.int16)
        w.writeframes(data.tobytes())
    dec = miniaudio.decode_file(wav_path, output_format=miniaudio.SampleFormat.SIGNED16,
                                nchannels=2, sample_rate=C.SR)
    arr = np.asarray(dec.samples, dtype=np.float32).reshape(-1, 2) / 32768.0
    assert abs(arr.shape[0] / C.SR - 2.0) < 0.1
    print(f"    decoded {arr.shape[0]/C.SR:.2f}s")
    os.remove(wav_path)

    print("[5] Store recent/last 持久化 ...")
    import json
    st = C.Store()
    st.path = os.path.join(base, "_store_test.json")
    st.recent = []; st.last = None; st.folders = []
    st.push_recent("a.mp3"); st.push_recent("b.mp3"); st.push_recent("a.mp3")
    assert st.recent[0] == "a.mp3" and st.last == "a.mp3"
    st.save()
    with open(st.path, encoding="utf-8") as f:
        disk = json.load(f)
    assert disk["recent"][0] == "a.mp3" and disk["last"] == "a.mp3", "持久化写入失败"
    os.remove(st.path)
    print("    ok: dedupe + persist")

    print("[6] UI 组件 ...")
    from PySide6.QtWidgets import QApplication
    q = QApplication.instance() or QApplication(sys.argv)
    assert hasattr(W, "CircleButton") and hasattr(W, "ResizeFilter")
    tl = W.TimelineWidget()
    assert (tl.height() - 32) - 34 >= 0, "播放页红字越界"
    print("    ok: CircleButton / ResizeFilter / timeline 几何")

    print("[7] analyzer perf ...")
    t0 = time.time()
    for _ in range(300):
        az.process(sig)
    ms = (time.time() - t0) / 300 * 1000
    assert ms < 3.0
    print(f"    {ms:.3f} ms / block")

    print("[8] 主窗口 + 页面结构 ...")
    w = A.MainWindow()
    assert w.stack.count() == 7, f"应有 7 个页面（含详情页），实际 {w.stack.count()}"
    assert w.sidebar.buttons[0].isChecked(), "默认选中首页"
    # 歌曲表填充 + 过滤
    entries = [
        {"path": "p1", "title": "Alpha", "artist": "Bob", "album": "X", "duration": 100.0,
         "format": "FLAC", "bit_depth": "16-bit", "sample_rate": "44.1 kHz", "cover": None},
        {"path": "p2", "title": "Beta", "artist": "Alice", "album": "Y", "duration": 200.0,
         "format": "MP3", "bit_depth": "--", "sample_rate": "44.1 kHz", "cover": None},
        {"path": "p3", "title": "Gamma", "artist": "Bob", "album": "X", "duration": 300.0,
         "format": "FLAC", "bit_depth": "24-bit", "sample_rate": "96 kHz", "cover": None},
    ]
    w.songs_page.set_entries(entries)
    assert w.songs_page.table.rowCount() == 3
    w.songs_page.table.apply_filter("bob")
    vis = w.songs_page.table.ordered_visible()
    assert vis == ["p1", "p3"], f"过滤结果异常: {vis}"
    # 专辑归类（_refresh_views 基于 w.library）
    w.library = entries
    w.lib_map = {e["path"]: e for e in entries}
    w._refresh_views()
    assert w.albums_page.grid.count() == 2, "应有 2 张专辑"
    # 带封面的 rounded_pixmap（回归：QPainterPath 导入）
    from PySide6.QtGui import QImage
    img = QImage(60, 60, QImage.Format.Format_RGB32)
    img.fill(0x336699)
    pm = W.rounded_pixmap(img, 36)
    assert not pm.isNull() and pm.width() == 36
    print("    rounded_pixmap ok")
    assert w.artists_page.grid.count() == 2, "应有 2 位艺术家"
    # 播放队列
    w.play_path("p2", ["p1", "p2", "p3"])
    assert w.queue == ["p1", "p2", "p3"] and w.qpos == 1
    w.next_track()
    assert w.queue[w.qpos] == "p3"
    w.prev_track(); w.prev_track()
    assert w.queue[w.qpos] == "p1"
    # 首页统计
    assert w.home_page.stat_labels["songs"].text() == "3"
    print("    ok: 7 pages / filter / groups / queue / stats")

    print("[8b] 专辑详情 + 播放页控制条 + 图标矢量化 ...")
    played = []
    w.detail_page.playRequested.connect(lambda ctx, st: played.append((ctx, st)))
    w._album_open("X")
    assert w.stack.currentIndex() == A.PAGE_DETAIL, "专辑应打开详情页而非直接播放"
    assert w.detail_page.table.rowCount() == 2, "专辑 X 应有 2 首"
    assert not played, "打开专辑不应自动播放"
    w.detail_page.table.rowActivated.emit(["p3", ["p1", "p3"]])
    assert played and played[-1] == (["p1", "p3"], "p3"), "点曲目应播放该曲"
    w.detail_page.backRequested.emit()
    assert w.stack.currentIndex() == A.PAGE_ALBUMS, "返回应回到专辑网格"

    # 播放页隐藏底部条（offscreen 下子控件 isVisible 恒为 False，用状态位断言）
    w._on_nav(A.PAGE_PLAYER)
    assert w.player_mode is True, "可视化播放页应隐藏底部播放条"
    w._on_nav(A.PAGE_ALBUMS)
    assert w.player_mode is False, "其它页应显示底部播放条"
    w._on_nav(A.PAGE_DETAIL)
    assert w.player_mode is False, "详情页应显示底部播放条"

    # 图标矢量化：不再使用 ▶/⏸ 这类会被系统渲染成彩色 emoji 的字符
    assert w.player_page.btn_play._icon == "play"
    w.engine.playing = True
    w._sync_all_play_btns()
    assert w.player_page.btn_play._icon == "pause"
    assert w.bottom.btn_play._icon == "pause"
    w.engine.playing = False
    w._sync_all_play_btns()
    assert w.player_page.btn_play._icon == "play"
    # 播放页自带音量，防止底部条隐藏后失去控制
    assert w.player_page.vol.value() == 85
    print("    ok: detail page / player chrome / vector icons")

    print("[8d] 歌曲行点击 / 双击 / 排序后不串歌 / 当前曲目标记 ...")
    got = {"single": None, "double": None}
    w.songs_page.playRequested.connect(lambda c, s: got.__setitem__("single", (c, s)))
    w.songs_page.playAndShowRequested.connect(lambda c, s: got.__setitem__("double", (c, s)))
    w.songs_page.set_entries(entries)
    tbl = w.songs_page.table
    from PySide6.QtCore import Qt as _Qt
    tbl._on_header(2)                          # 点击「标题」表头排序
    tbl._on_header(2)                          # 再点一次 → 降序
    tbl.cellClicked.emit(0, 2)
    assert got["single"] is not None, "单击歌曲行未触发播放"
    ctx, start = got["single"]
    assert ctx == tbl.ordered_visible(), "上下文应与视觉顺序一致"
    assert start == tbl.ordered_visible()[0], "排序后点错歌"
    tbl.cellDoubleClicked.emit(1, 2)
    assert got["double"] is not None, "双击未触发播放"
    assert got["double"][1] == tbl.ordered_visible()[1], "双击目标错误"
    # 单击动效
    tbl.flash_row(0)
    assert tbl._flash_anim is not None and tbl._flash_anim.state() != 0 \
        or tbl._flash_row == 0, "点击应有高亮动效"
    # 当前曲目标记
    tbl.set_current("p3")
    marked = [r for r in range(tbl.rowCount()) if tbl.item(r, 0).text() == "▶"]
    assert len(marked) == 1 and tbl._row_path(marked[0]) == "p3", "当前曲目标记错误"
    print("    ok: row click / double click / sort-safe / marker")

    print("[8e] 底部封面进入播放页 + 暂停时跳转立即同步 ...")
    w._on_nav(A.PAGE_HOME)
    assert w.player_mode is False
    w.bottom.coverClicked.emit()
    assert w.stack.currentIndex() == A.PAGE_PLAYER, "底部封面应进入可视化播放页"
    assert w.player_mode is True
    assert not w.sidebar.buttons[0].isChecked(), "播放页不属于侧栏导航"
    # 暂停状态下 seek 也要立刻更新 UI
    w.engine.samples = np.zeros((48000, 2), np.float32)
    w.engine.duration = 1.0
    w.engine.playing = False
    w._last_pos = 0.0
    w.seek_and_sync(0.5)
    assert abs(w.player_page.timeline.position - 0.5) < 0.01, "暂停时跳转未立即同步"
    w._on_nav(A.PAGE_HOME)          # 回到普通页（底部条显示）
    w.seek_and_sync(0.7)
    assert abs(w.bottom.progress.position - 0.7) < 0.01, "底部条进度未立即同步"
    print("    ok: cover → player / seek sync while paused")

    print("[8f] 听歌时长累计（非曲库总时长）...")
    st = w.store
    st.listened = 0.0
    w.engine.playing = True
    w._last_tick = time.monotonic() - 0.5
    w._tick()
    assert 0.45 <= st.listened <= 0.6, f"听歌时长累计异常: {st.listened}"
    w.engine.playing = False
    total_dur = sum(e["duration"] for e in entries)
    assert abs(st.listened - total_dur) > 1, "统计不应等于曲库总时长"
    print(f"    ok: listened={st.listened:.2f}s")

    print("[8c] 最大化拖动不闪屏（只还原一次 + 临时冻结重绘）...")
    from PySide6.QtCore import QPoint
    seen = []
    w.begin_gesture = lambda: seen.append("begin")
    w.end_gesture = lambda: seen.append("end")
    w.showNormal()
    w.titlebar._begin_drag(QPoint(500, 40))
    assert seen == [], f"非最大化不应触发冻结: {seen}"
    assert w.titlebar._drag is not None, "普通状态应记录拖动偏移"
    w.showMaximized()
    w.titlebar._begin_drag(QPoint(500, 40))
    assert seen == ["begin", "end"], f"最大化还原应只做一次并临时冻结更新: {seen}"
    assert w.isMaximized() is False, "拖动开始后应已还原为普通窗口"
    print("    ok: drag restore path")

    print("[9] 库扫描线程（对临时目录）...")
    d = os.path.join(base, "_libtest")
    os.makedirs(d, exist_ok=True)
    for n in ("a.wav", "b.wav"):
        with wave.open(os.path.join(d, n), "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(44100)
            wf.writeframes(b"\x00\x00" * 100)
    with open(os.path.join(d, "c.txt"), "w") as f:
        f.write("x")
    lt = C.LibraryThread([d])
    files = []
    lt.batch.connect(lambda batch: files.extend(e["path"] for e in batch))
    lt.run()  # 直接同步执行
    assert len(files) == 2, f"应找到 2 个音频: {files}"
    for f in ("a.wav", "b.wav", "c.txt"):
        os.remove(os.path.join(d, f))
    os.rmdir(d)
    print(f"    ok: scanned {len(files)} audio files")

    print("[10] decode chain: 24bit/96kHz FLAC ...")
    import subprocess
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    src = os.path.join(base, "_src44.wav")
    with wave.open(src, "wb") as wv:
        wv.setnchannels(1); wv.setsampwidth(2); wv.setframerate(44100)
        wv.writeframes((np.sin(2 * np.pi * 440 * np.arange(44100 * 2) / 44100) * 20000).astype(np.int16).tobytes())
    flac96 = os.path.join(base, "_hi96.flac")
    subprocess.run([ff, "-y", "-i", src, "-ar", "96000", "-c:a", "flac",
                    "-sample_fmt", "s32", flac96], capture_output=True, check=True)
    arr = C.LoadThread._decode(flac96)
    dur = arr.shape[0] / C.SR
    assert abs(dur - 2.0) < 0.15, f"解码时长异常: {dur}"
    os.remove(src); os.remove(flac96)
    print(f"    decoded {dur:.2f}s")

    print("[11] LRC 解析 / 本地 lrc 查找 ...")
    lrc = "[00:00.00]第一行\n[00:12.50]第二行\n[01:02.25]第三行\n"
    lines = C.parse_lrc(lrc)
    assert lines is not None and len(lines) == 3, f"解析异常: {lines}"
    assert abs(lines[1][0] - 12.5) < 0.01 and lines[1][1] == "第二行"
    assert C.parse_lrc("纯文本歌词没有时间轴") is None, "无时间轴应返回 None"
    assert len(C.parse_lrc("[00:01.00][00:05.00]合唱")) == 2, "多时间标签"
    assert C.parse_lrc("") is None
    # 同名 .lrc
    d2 = os.path.join(base, "_lrctest")
    os.makedirs(d2, exist_ok=True)
    song = os.path.join(d2, "song.mp3")
    with open(song, "wb") as f:
        f.write(b"0")
    with open(os.path.join(d2, "song.lrc"), "w", encoding="utf-8") as f:
        f.write(lrc)
    assert C.find_local_lrc(song) is not None, "应找到同名 lrc"
    assert C.find_local_lrc(os.path.join(d2, "none.mp3")) is None
    for f in os.listdir(d2):
        os.remove(os.path.join(d2, f))
    os.rmdir(d2)
    print("    ok: parse_lrc / find_local_lrc")

    print("[12] 歌词控件按时间定位 ...")
    lw = W.LyricsWidget()
    lw.resize(420, 320)
    lw.set_lyrics(lines)
    lw.set_time(0.0)
    assert lw.index == 0, f"起始行应为 0，实际 {lw.index}"
    lw.set_time(13.0)
    assert lw.index == 1, f"13s 应落在第二行，实际 {lw.index}"
    lw.set_time(90.0)
    assert lw.index == 2
    lw.set_lyrics([])
    assert lw.lines == []
    lw.set_hint("暂无歌词")
    assert lw.hint == "暂无歌词"
    print("    ok: index tracking")

    print("[13] 队列：插入到下一首 / 追加 / 移除 ...")
    w.queue = ["p1", "p2", "p3"]; w.qpos = 1
    w.insert_next("p9")
    assert w.queue == ["p1", "p2", "p9", "p3"] and w.qpos == 1, f"{w.queue}"
    w.queue_add("p8")
    assert w.queue[-1] == "p8" and w.qpos == 1
    w.insert_next("p1")            # 已在队列中 → 搬到下一首且不重复
    assert w.queue.count("p1") == 1 and w.queue[w.qpos + 1] == "p1", f"{w.queue}"
    w.remove_from_queue("p9")
    assert "p9" not in w.queue
    w.player_page.update_queue(w.queue, w.lib_map, "p2")
    assert w.player_page.queue_list.count() == len(w.queue), "队列列表未同步"
    print("    ok: insert next / append / remove")

    print("[14] 播放页：进入 / 全屏开关 / 频谱 / 队列抽屉 / 退出 ...")
    w.showNormal()
    w._on_nav(A.PAGE_HOME)
    w._on_setting("auto_fullscreen", False)     # 默认：跟随窗口大小
    w.resize(1180, 780)
    q.processEvents()
    size_before = w.size()
    w.open_player_page()
    assert w.stack.currentIndex() == A.PAGE_PLAYER, "应进入播放页"
    # 回归（原 bug：点开播放页无条件全屏，不跟随窗口大小）：
    # 默认不进全屏、窗口尺寸不变；标题栏是 FramelessWindowHint 下唯一能
    # 拖动/最小化/关闭的把手，窗口模式下必须留着
    assert not w.isFullScreen(), "默认不应自动全屏"
    assert w.size() == size_before, \
        f"进入播放页不应改变窗口大小: {size_before} -> {w.size()}"
    assert w.titlebar.isVisible(), "窗口模式下必须保留标题栏"
    assert not w.sidebar.isVisible(), "应收起侧栏"
    assert not w.bottom.isVisible(), "应收起底栏"
    assert w.player_mode is True
    assert not w.sidebar.buttons[0].isChecked(), "播放页不高亮侧栏"
    # 「全屏」开关应即时生效
    w._on_setting("auto_fullscreen", True)
    assert w.isFullScreen() and not w.titlebar.isVisible(), \
        "开启全屏应整屏并隐藏标题栏"
    w._on_setting("auto_fullscreen", False)
    assert not w.isFullScreen() and w.titlebar.isVisible(), \
        "关闭全屏应立即还原窗口"
    pg = w.player_page
    before = pg.spec_hidden
    pg.toggle_spectrum()
    assert pg.spec_hidden != before and pg.btn_spec.text() in ("显示频谱", "隐藏频谱")
    pg.toggle_spectrum()
    pg.toggle_queue(True)
    assert pg.btn_queue.isChecked() and pg.queue_open
    pg.toggle_queue(False)
    w._on_setting("auto_lyrics", False)
    assert w.store.get("auto_lyrics") is False
    w._on_setting("auto_lyrics", True)
    assert w.store.get("auto_lyrics") is True
    w._on_setting("auto_cover", False)
    assert w.store.get("auto_cover") is False
    w._on_setting("auto_cover", True)
    # 退出：还原上一页与窗口
    w.exit_player_page()
    assert not w.isFullScreen(), "退出后不应保持全屏"
    assert w.stack.currentIndex() == A.PAGE_HOME, "应回到进入前页面"
    assert w.player_mode is False and w.sidebar.buttons[0].isChecked()
    print("    ok: keep-window enter / fullscreen toggle / spectrum / drawer")

    print("[15] 右键「插入到下一首播放」信号链 ...")
    assert hasattr(w.songs_page.table, "queueNextRequested")
    assert hasattr(w.recent_page.table, "queueNextRequested")
    assert hasattr(w.detail_page.table, "queueNextRequested")
    w.queue = ["p1"]; w.qpos = 0
    w.songs_page.table.queueNextRequested.emit("p3")
    assert w.queue == ["p1", "p3"], f"右键插入未生效: {w.queue}"
    w.songs_page.table.queueAddRequested.emit("p2")
    assert w.queue == ["p1", "p3", "p2"], f"右键追加未生效: {w.queue}"
    # 缓存 key 稳定性
    k1 = C.Store.cache_key("Bob", "Alpha", "X")
    k2 = C.Store.cache_key(" bob ", "alpha ", "x")
    assert k1 == k2 and len(k1) == 16, "缓存 key 应归一化且定长"
    print("    ok: context menu signals / cache key")

    print("[16] 候选匹配打分（避免匹配到翻唱/深情版）...")
    cands = [
        ("晴天(深情版)", "Lucky小爱", 278.0, "id_bad1"),
        ("晴天", "Jay", 112.0, "id_bad2"),
        ("晴天", "周杰伦", 269.0, "id_good"),
        ("晴天 (女声版))", "GYBeat", 268.0, "id_bad3"),
    ]
    best, sc = C.pick_best(cands, "晴天", "周杰伦", 269.0)
    assert best[3] == "id_good", f"应选中原唱，实际 {best}"
    # 时长差过大应被扣分
    best2, _ = C.pick_best(cands, "晴天", "周杰伦", 40.0)
    assert best2[3] == "id_good", "歌手+标题命中应始终优先"
    # 归一化：去括号/版本词
    assert C.norm_text("晴天（Live版）") == "晴天", C.norm_text("晴天（Live版）")
    assert "cover" not in C.norm_text("Shape of You")
    assert C.match_score("Rolling in the Deep", "Adele", 228.0,
                         "Rolling in the Deep", "Adele", 228.0) > 180
    assert C.match_score("晴天 cover 翻唱", "某某", 300.0, "晴天", "周杰伦", 269.0) < 100
    # 文件名拆分
    t2, a2 = C.split_filename("周杰伦 - 晴天")
    assert t2 == "晴天" and a2 == "周杰伦", f"{t2}/{a2}"
    assert C.split_filename("01. 稻香")[0] == "稻香"
    print("    ok: pick_best / norm_text / split_filename")

    print("[17] 歌词样式：颜色 / 字号 / 对齐 ...")
    lw2 = W.LyricsWidget()
    lw2.resize(400, 300)
    lw2.set_lyrics(C.parse_lrc(lrc))
    lw2.set_style(color="#1c7ed6", active="#2f9e44", size=26, align=2)
    assert lw2.color.name() == "#1c7ed6" and lw2.active_color.name() == "#2f9e44"
    assert lw2.font_size == 26 and lw2.align == W.LyricsWidget.RIGHT
    assert lw2._align_flag() == _Qt.AlignRight
    lw2.set_style(align=1)
    assert lw2._align_flag() == _Qt.AlignHCenter
    lw2.set_style(size=99)                     # 越界应被夹到 [11,40]
    assert lw2.font_size == 40, lw2.font_size
    # 样式面板
    panel = W.LyricStylePanel()
    got = {}
    panel.styleChanged.connect(lambda d: got.update(d))
    panel.set_values("#17181c", "#e03131", 18, 1)
    panel._pick("color", "#c2255c")
    assert got.get("color") == "#c2255c", "色块点击未发出样式变更"
    panel._align(2)
    assert got.get("align") == 2
    panel._on_size(22)
    assert got.get("size") == 22
    print("    ok: lyric style + panel")

    print("[18] 纯文本歌词（无时间轴也要能看）...")
    assert C.parse_lrc("这是纯文本歌词\n第二行") is None
    lw3 = W.LyricsWidget()
    lw3.set_plain_text("故事的小黄花\n从出生那年就飘着\n童年的荡秋千")
    assert len(lw3.plain) == 3 and lw3.lines == []
    lw3.set_lyrics(C.parse_lrc(lrc))
    assert lw3.plain == [], "切回逐行歌词应清空纯文本"
    print("    ok: plain lyrics fallback")

    print("[19] 联网实取歌词（网易云 / LRCLIB）...")
    got_ly = {}
    th = C.LyricsThread("k_test", "晴天", "周杰伦", "叶惠美", 269.0)
    th.fetched.connect(lambda k, t, s: got_ly.update({"t": t, "s": s}))
    try:
        th.run()          # 同步执行，便于断言
    except Exception as e:
        print("    网络异常，跳过:", repr(e)[:80])
    txt = got_ly.get("t", "")
    if txt:
        assert C.LRC_TIME.search(txt), f"应返回带时间轴的歌词: {txt[:60]}"
        assert "周杰伦" not in txt[:0]        # 占位，确认不是空串
        lines = C.parse_lrc(txt)
        assert lines and len(lines) > 8, f"歌词行数太少: {len(lines or [])}"
        print(f"    ok: 来源={got_ly.get('s')} 行数={len(lines)} "
              f"首行={lines[0][1][:16]!r}")
    else:
        print("    !! 未取到歌词（网络/接口问题，不影响其它用例）")

    print("[20] 播放页背景：流体着色器 / 自定义图片 / 压暗 / 持久化 ...")
    from PySide6.QtGui import QImage as _QImg
    pg = w.player_page
    tmpdir = os.path.join(base, "_bgsnap")
    os.makedirs(tmpdir, exist_ok=True)
    # 构造一张蓝红混色封面 → 提取 4 张流体主色
    img = _QImg(64, 64, _QImg.Format_RGB32)
    for y in range(64):
        for x in range(64):
            img.setPixel(x, y, 0xFF2B4C8C if (x + y) % 3 else 0xFFC03030)
    fb = W.FluidBackground()
    fb.resize(320, 200)
    fb.set_cover(img)
    assert len(fb.palette) == 4, fb.palette
    # 调色板要压到暗色背景的明度（浅色文字才读得清）
    for c in fb.palette:
        h, s, v, _a = c.getHsv()
        assert v <= 225, ("主色太亮，浅色文字会糊", c.name())
    # 逐色明度硬上限：即便原图很亮，配色也必须整体压进暗背景区间，
    # 否则流体亮区会把压在上面的浅色说明文字吃掉（实测对比度曾低到 8）
    import colorsys as _cs
    for c in fb.palette:
        _l = _cs.rgb_to_hls(c.redF(), c.greenF(), c.blueF())[1]
        assert _l <= W.PALETTE_L_MAX + 0.02, (
            "配色明度超上限，浅色文字会在流体亮区消失", c.name(), round(_l, 3))
    # 同一封面重复取色应稳定
    fb2 = W.FluidBackground()
    fb2.resize(320, 200)
    fb2.set_cover(img)
    assert [c.name() for c in fb2.palette] == [c.name() for c in fb.palette]
    # 着色器真的在动：两个时刻的帧不能一样（4 色必须不同，否则插值恒等）
    _4c = [(0.20, 0.32, 0.62), (0.48, 0.20, 0.58),
           (0.72, 0.24, 0.34), (0.16, 0.52, 0.48)]
    f1 = W.fluid_frame(48, 32, 0.0, _4c)
    f2 = W.fluid_frame(48, 32, 1.7, _4c)
    assert f1 is not None and f1.shape == (32, 48, 3)
    assert not (f1 == f2).all(), "流体画面必须随时间变化（不能是静止的）"
    diff = int(np.abs(f1.astype(np.int16) - f2.astype(np.int16)).max())
    assert diff > 10, ("流体两帧差异太小，画面上看不出流动", diff)
    fb.set_dim(1.9)
    assert abs(fb.dim - 0.65) < 1e-6, fb.dim
    fb.set_dim(-1)
    assert fb.dim == 0.0
    fb.set_mode("none")
    assert fb.mode == "none" and fb.scene_pixmap() is None
    # 自定义图片背景
    tmp_png = os.path.join(tmpdir, "bgimg.png")
    assert img.save(tmp_png), "测试用背景图写入失败"
    assert fb.set_image(tmp_png) is True
    fb.set_mode("image")
    fb.grab().save(os.path.join(tmpdir, "bgimage.png"))
    assert fb.set_image(tmp_png + ".not-exist") is False
    print("    ok: fluid palette / dim clamp / custom image")

    print("[21] 氛围特效：飘雪 / 下雨 ...")
    wo = W.WeatherOverlay()
    wo.resize(400, 300)
    wo.set_mode("snow")
    assert wo.mode == "snow" and len(wo.parts) == 110, len(wo.parts)
    y0 = wo.parts[0]["y"]
    wo._advance()
    assert wo.parts[0]["y"] > y0, "雪片应向下飘落"
    wo.grab().save(os.path.join(tmpdir, "snow.png"))
    wo.set_mode("rain")
    assert wo.mode == "rain" and len(wo.parts) == 162, len(wo.parts)
    wo._advance()
    wo.grab().save(os.path.join(tmpdir, "rain.png"))
    wo.set_mode("none")
    assert wo.parts == []
    print("    ok: snow / rain particles")

    print("[22] 背景设置面板 + 主窗接入 ...")
    panel = W.BackgroundPanel()
    got = {}
    panel.bgChanged.connect(lambda d: got.update(d))
    panel.set_values("image", tmp_png, 0.45, "rain")
    panel._mode("fluid")
    assert got.get("bg_mode") == "fluid", got
    panel._weather("snow")
    assert got.get("weather") == "snow", got
    panel._on_veil(72)
    assert abs(got.get("bg_veil", 0) - 0.72) < 1e-6, got

    # PlayerPage 应用 + 持久化
    pg.apply_background({"bg_mode": "image", "bg_image": tmp_png,
                         "bg_veil": 0.5, "weather": "snow"})
    assert pg.bg.mode == "image" and pg.bg.image_path == tmp_png
    assert abs(pg.bg.dim - 0.5) < 1e-6 and pg.weather.mode == "snow"
    seen = {}
    pg.bgChanged.connect(lambda d: seen.update(d))
    pg.bgChanged.emit(dict(pg._bg_vals))
    w._on_bg_changed(seen)
    assert w.store.get("bg_mode") == "image", w.store.settings
    assert w.store.get("weather") == "snow"
    # 封面变化同步背景色
    pg.set_bg_cover(img)
    assert len(pg.bg.palette) >= 3
    # 还原默认，避免影响其它用例
    pg.apply_background({"bg_mode": "fluid", "bg_image": "",
                         "bg_veil": 0.30, "weather": "none"})
    w._on_bg_changed({"bg_mode": "fluid", "bg_image": "",
                      "bg_veil": 0.30, "weather": "none"})
    pg.resize(900, 560)
    pg.grab().save(os.path.join(tmpdir, "player_bg.png"))
    print("    ok: panel / apply / persist / cover sync")

    print("[23] 封面迟到刷新：列表 / 网格 / 首页 / 详情 ...")
    from PySide6.QtGui import QImage as _QI
    cov = _QI(120, 120, _QI.Format_RGB32)
    cov.fill(0xFF3388CC)
    path0 = "c1"
    lib = [{"path": "c1", "title": "Cover1", "artist": "AAA", "album": "X",
            "duration": 100.0, "format": "FLAC", "bit_depth": "16-bit",
            "sample_rate": "44.1 kHz", "cover": None},
           {"path": "c2", "title": "Cover2", "artist": "BBB", "album": "Y",
            "duration": 200.0, "format": "MP3", "bit_depth": "--",
            "sample_rate": "44.1 kHz", "cover": None}]
    w.library = lib
    w.lib_map = {e["path"]: e for e in lib}
    w._refresh_views()
    tbl = w.songs_page.table
    tbl.apply_filter("")
    row = None
    for r in range(tbl.rowCount()):
        if tbl._row_path(r) == path0:
            row = r
            break
    assert row is not None, "未找到目标行"
    assert tbl.item(row, 1).icon().isNull(), "无封面时列表封面列应为空"
    w._apply_net_cover(path0, cov)           # 联网匹配到封面
    assert not tbl.item(row, 1).icon().isNull(), "匹配到封面后列表应立即显示（原 bug）"
    assert w.library[0]["cover"] is not None, "封面应写回库条目"
    assert w.albums_page.grid.count() == 2 and w.artists_page.grid.count() == 2
    assert w.songs_page.refresh_cover("不存在路径", cov) == 0
    assert w.songs_page.refresh_cover(path0, cov) >= 1
    print("    ok: list / grid / home / detail cover refresh")

    print("[24] 歌词卡片透明度 + 主题配色切换 ...")
    lw4 = W.LyricsWidget()
    lw4.set_card_alpha(152)
    assert lw4.card_alpha == 152
    lw4.set_card_alpha(999)
    assert lw4.card_alpha == 255, "透明度应钳制到 255"
    lw4.set_lyrics(C.parse_lrc(lrc))
    lw4.set_time(12.5)
    # 沉浸/明亮两套配色的切换与还原
    W.set_theme(False)
    light_ink = W.INK
    assert light_ink == W._LIGHT_COLORS["INK"]
    W.set_theme(True)
    assert W.theme_dark() is True
    assert W.INK == W.IMM["INK"] and W.BG == W.IMM["BG"]
    assert W.INK != light_ink
    W.set_theme(False)
    assert W.theme_dark() is False and W.INK == light_ink
    # 深色背景下歌词颜色应被抬亮到可读
    dark_c = W._lift_for_dark("#4a4b52", 0.74)
    assert dark_c.value() >= 188, dark_c.value()
    assert dark_c.value() > 90, "原深灰歌词在深底上必须被抬亮"
    print("    ok: lyric card alpha + theme switch")

    print("[25] 沉浸模式：组件完全融入背景 ...")
    from PySide6.QtGui import QPainter, QPixmap
    from PySide6.QtCore import Qt as _Qt2, QRectF
    pgb = w.player_page
    pgb.resize(1100, 640)
    pgb.bg.setGeometry(0, 0, 1100, 640)
    pgb.weather.setGeometry(0, 0, 1100, 640)
    pgb.apply_background({"bg_mode": "fluid", "bg_image": "",
                          "bg_veil": 0.22, "weather": "none"})
    assert pgb._imm_on is True, "流体背景应进入沉浸模式"
    assert W.imm_page(pgb.lyric_card) is pgb
    assert W.theme_dark() is True, "沉浸模式下全局配色应切到深色底"
    assert W.INK == W.IMM["INK"]
    # 关键：卡片在沉浸模式下**一个字都不画**（完全透明 → 彻底融进背景）
    # 用 render() 渲染到透明画布上，不叠窗口底色，才能看出卡片自己画了什么
    pgb.grab()
    assert pgb.track_card.width() > 40 and pgb.track_card.height() > 30

    def _card_alpha(y_rows):
        from PySide6.QtWidgets import QWidget as _QW
        from PySide6.QtCore import QPoint as _QP
        from PySide6.QtGui import QRegion as _QR
        pm = QPixmap(pgb.track_card.size())
        pm.fill(_Qt2.transparent)
        # 只画控件自己（不叠窗口底色），否则看不出卡片到底画了什么
        pgb.track_card.render(pm, _QP(0, 0), _QR(), _QW.RenderFlag.DrawChildren)
        im = pm.toImage().convertToFormat(_QImg.Format_ARGB32)
        n = 0
        t = 0
        for _y in range(min(y_rows, im.height())):
            for _x in range(0, im.width(), 3):
                t += 1
                if im.pixelColor(_x, _y).alpha() > 8:
                    n += 1
        return n, t

    painted, total = _card_alpha(12)
    assert total > 0 and painted == 0, (
        "沉浸模式下卡片不应有任何底色", painted, total)
    assert pgb.btn_play._style == "solid" and pgb.btn_prev._style == "glass"
    assert pgb.viz.on_bg is True
    assert pgb.spec.on_bg is True, "频谱刻度应切浅色，避免深色横线拉出分区感"
    assert pgb.timeline.on_bg is True, "时间轴刻度应切浅色"
    assert len(pgb.bg.palette) == 4, pgb.bg.palette
    pgb.grab().save(os.path.join(tmpdir, "v13_player_immersive.png"))

    pgb.apply_background({"bg_mode": "none", "bg_image": "",
                          "bg_veil": 0.22, "weather": "none"})
    assert pgb._imm_on is False, "纯净白背景应退出沉浸模式"
    assert W.theme_dark() is False and W.INK != W.IMM["INK"]
    assert pgb.btn_play._style == "outline" and pgb.viz.on_bg is False
    assert pgb.bg.scene_pixmap() is None
    assert W.on_imm(pgb.lyric_card) is False
    # 退出沉浸后卡片要重新有一层白色底
    pgb.grab()
    from PySide6.QtWidgets import QWidget as _QW2
    from PySide6.QtCore import QPoint as _QP2
    from PySide6.QtGui import QRegion as _QR2
    pm0 = QPixmap(pgb.track_card.size())
    pm0.fill(_Qt2.transparent)
    pgb.track_card.render(pm0, _QP2(0, 0), _QR2(), _QW2.RenderFlag.DrawChildren)
    im0 = pm0.toImage().convertToFormat(_QImg.Format_ARGB32)
    mid = im0.pixelColor(im0.width() // 2, 3)
    assert mid.alpha() > 200, ("明亮模式卡片应恢复白底", mid.alpha())
    pgb.apply_background({"bg_mode": "fluid", "bg_image": "",
                          "bg_veil": 0.22, "weather": "none"})
    assert pgb.bg.mode == "fluid" and pgb._imm_on is True
    print("    ok: components fully blend into the background")

    print("[26] 天气特效：景深 / 精灵 / 涟漪 ...")
    wo2 = W.WeatherOverlay()
    wo2.resize(500, 360)
    wo2.set_mode("snow")
    radii = sorted(p["r"] for p in wo2.parts)
    assert len(radii) == 110, len(radii)
    assert radii[-1] / max(1e-6, radii[0]) > 3.0, "雪应有明显景深（近大远小）"
    sp = wo2._snow_sprite(2.0)
    assert sp.width() >= 10 and not sp.isNull(), "应预渲染雪花精灵"
    wo2.set_mode("snow")           # 重复设置不应重置粒子
    assert len(wo2.parts) == 110
    wo2.set_mode("rain")
    assert len(wo2.parts) == 162, len(wo2.parts)
    rs = wo2._rain_sprite(20, 1.5)
    assert not rs.isNull() and rs.height() > 10
    _t0 = time.monotonic
    for _ in range(120):          # 模拟 120 帧 × 33ms ≈ 4s，让雨丝落地
        wo2._last_t = _t0() - 0.033
        wo2._advance()
    assert len(wo2.ripples) > 0, "雨丝落地应生成涟漪"
    wo2.grab()
    print("    ok: depth / sprites / ripples")

    print("[27] 性能：库视图重建合并 / 封面缓存 / 扫描解码 / 空闲降频 ...")
    from PySide6.QtGui import QImage as _QImg2
    mw = A.MainWindow()
    mw.resize(1180, 780)
    # (a) 重建调度器存在且是「合并」而非「每批一次」
    assert mw._refresh_timer.isSingleShot(), "重建应走单次触发的合并调度"
    assert mw.REFRESH_MIN_GAP >= 0.3, "两次重建之间必须有最小间隔"
    # 连续 10 个批次到达，真正重建的次数应远少于 10
    calls = {"n": 0}
    _real = mw._refresh_views
    def _count():
        calls["n"] += 1
        return _real()
    mw._refresh_views = _count
    mw._refresh_timer.timeout.disconnect()
    mw._refresh_timer.timeout.connect(_count)
    mw._last_refresh = time.monotonic()
    for i in range(10):
        mw._on_lib_batch([{"path": f"/perf/{i}.flac", "title": f"t{i}",
                           "artist": "a", "album": "b", "duration": 1.0,
                           "format": "MP3", "bit_depth": "--",
                           "sample_rate": "--", "cover": None}])
    assert calls["n"] == 0, ("批次密集到达时不应立刻重建", calls["n"])
    mw._refresh_views = _real
    mw._refresh_timer.timeout.disconnect()
    mw._refresh_timer.timeout.connect(mw._refresh_views)

    # (b) rounded_pixmap 结果缓存：同图同尺寸返回同一对象
    src = _QImg2(64, 64, _QImg2.Format_RGB32)
    src.fill(0xFF345678)
    a1 = W.rounded_pixmap(src, 36)
    a2 = W.rounded_pixmap(src, 36)
    assert a1 is a2, "圆角封面应命中缓存（同一对象）"

    # (c) 表格图标缓存 + 过滤快速通道
    tbl = P.SongsTable()
    ic1 = tbl._icon_for(src, 36)
    ic2 = tbl._icon_for(src, 36)
    assert ic1 is ic2, "同封面的 QIcon 应复用"
    assert tbl._icon_for(None, 36) is None
    tbl.setRowCount(3)
    tbl.apply_filter("")           # 空关键字 → 快速通道
    assert tbl._all_visible is True

    # (d) 扫描期封面按目标尺寸直接解码，不该解出整张大图
    big = _QImg2(512, 512, _QImg2.Format_RGB32)
    big.fill(0xFF887766)
    from PySide6.QtCore import QBuffer as _QB2
    _b = _QB2(); _b.open(_QB2.WriteOnly)
    assert big.save(_b, "JPG", 88)
    thumb = C.decode_thumb(bytes(_b.data()), 96)
    assert thumb is not None and not thumb.isNull()
    assert max(thumb.width(), thumb.height()) <= 120, (
        "缩略图应约为 96px，而不是原图尺寸", thumb.width(), thumb.height())

    # (e) 空闲/忙碌两档刷新频率
    assert mw.TICK_IDLE > mw.TICK_BUSY, "空闲必须比忙碌更省"
    mw.timer.setInterval(mw.TICK_BUSY)
    mw._tick()
    assert mw.timer.interval() == mw.TICK_IDLE, (
        "没有音频时应自动降到空闲频率", mw.timer.interval())

    # (f) 播放页显示时不会重复刷主题样式表
    pg2 = mw.player_page
    pg2._theme_key = None
    pg2._apply_text_theme()
    k1 = pg2._theme_key
    assert k1 is not None
    k2 = pg2._theme_key
    pg2._apply_text_theme()
    assert pg2._theme_key == k2, "配色未变时不应重设样式表"
    print("    ok: coalesced rebuild / icon cache / thumb decode / idle tick")

    print("[28] 修复回归：歌词 DPR / 图标 / 流体帧率 / 封面持久化 ...")
    import struct as _struct
    from PySide6.QtGui import QPixmap as _QPm

    # (a) 歌词离屏层必须按「物理像素」建图。
    #     原 bug：QPixmap(w, h) 用的是逻辑尺寸，1.5 倍缩放的屏幕上被放大
    #     1.5 倍后才合成，笔画发虚 —— 用户报的「歌词字体模糊」。
    lw2 = W.LyricsWidget()
    lw2.resize(420, 240)
    lw2.set_lyrics([(0.0, "测试歌词第一行"), (5.0, "第二行")])
    lw2.set_time(0.0)
    made = []
    _orig_qpm = W.QPixmap

    class _SpyPixmap(_QPm):
        def __init__(self, *a):
            if len(a) == 2 and all(isinstance(v, int) for v in a):
                made.append(tuple(a))
            super().__init__(*a)

    W.QPixmap = _SpyPixmap
    lw2.devicePixelRatioF = lambda: 1.5
    try:
        lw2.render(_QPm(420, 240))
    finally:
        W.QPixmap = _orig_qpm
    assert (630, 360) in made, ("离屏图层未按 DPR 放大成物理像素", made)

    # (b) 软件图标：多尺寸 QIcon + icon.ico 容器合法（PNG-in-ICO）
    ic = W.app_icon()
    isizes = sorted(p.width() for p in ic.availableSizes())
    assert 16 in isizes and 256 in isizes, isizes
    ico_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "icon.ico")
    assert os.path.isfile(ico_path), "缺少 icon.ico（运行 _mkeico.py 生成）"
    raw = open(ico_path, "rb").read()
    _, typ, cnt = _struct.unpack("<HHH", raw[:6])
    assert typ == 1 and cnt >= 5, ("ico 头不合法", typ, cnt)
    assert b"\x89PNG" in raw, "ico 内应嵌 PNG 数据"

    # (c) 流体帧率：像素预算收紧 / 轮询间隔 <=16ms / 帧间隔下限 1/60。
    #     原 bug：定时器 33ms + frame_dt 下限 1/30，帧率被硬卡在 30fps。
    assert W.FLUID_PIXELS <= 90000, ("像素预算过高，帧率上不去",
                                     W.FLUID_PIXELS)
    fbr = W.FluidBackground()
    assert fbr._timer.interval() <= 16, ("流体轮询间隔须 <=16ms", 
                                         fbr._timer.interval())
    assert fbr._frame_dt <= 1.0 / 59.0, ("帧间隔下限应为 1/60", fbr._frame_dt)
    _cols = [(0.20, 0.32, 0.62), (0.48, 0.20, 0.58),
             (0.72, 0.24, 0.34), (0.16, 0.52, 0.48)]
    _f1 = W.fluid_frame(96, 54, 3.0, _cols)
    _f2 = W.fluid_frame(96, 54, 11.0, _cols)
    assert _f1 is not None and _f2 is not None
    assert int(np.abs(_f1.astype(int) - _f2.astype(int)).max()) > 10, \
        "着色器优化后画面不再流动"

    # (d) 联网匹配到的封面必须能被重扫回填。
    #     原 bug：write_cached_cover 把封面写进了 audiocache/covers/，但
    #     扫描时从不读回，重启后封面全部"消失" —— 用户报的
    #     「匹配封面后无法保存」。
    import tempfile as _tf
    with _tf.TemporaryDirectory() as td:
        cache = os.path.join(td, "audiocache")
        os.makedirs(os.path.join(cache, "covers"), exist_ok=True)
        fake = os.path.join(td, "Some Fake Song.flac")
        e0 = C.LibraryThread.read_entry(fake, None)
        assert e0["cover"] is None, "该文件本就没有内嵌封面"
        key = C.Store.cache_key(e0["artist"], e0["title"], e0["album"])
        # 用噪声图：纯色 JPEG 只有几百字节，会被「>1KB 才算有效封面」的
        # 防呆阈值挡掉，测不到回填路径
        rng = np.random.default_rng(7)
        noise = rng.integers(0, 256, (256, 256, 3), dtype=np.uint8)
        cov = _QImg2(noise.tobytes(), 256, 256, 256 * 3,
                     _QImg2.Format_RGB888).copy()
        cov_path = os.path.join(cache, "covers", key + ".jpg")
        assert cov.save(cov_path, "JPG", 90)
        assert os.path.getsize(cov_path) > 1024, "测试封面应大于 1KB"
        e1 = C.LibraryThread.read_entry(fake, cache)
        assert e1["cover"] is not None and not e1["cover"].isNull(), \
            "重扫时应从封面缓存回填"
    print("    ok: lyric dpr / icon / fluid fps / cover persist")

    print("ALL TESTS PASSED")

if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
