# -*- coding: utf-8 -*-
"""AudioScope 核心层：配色 / 音频引擎 / 解码 / 音乐库扫描 / 持久化"""
import os
import sys
import re
import json
import math
import time
import hashlib
import tempfile
import threading
import urllib.request
import urllib.parse

import numpy as np

from PySide6.QtCore import QObject, QThread, Signal, Qt
from PySide6.QtGui import QImage


# ----------------------------------------------------------------------------
# 重依赖按需加载
# 这两个包在导入时要加载 PortAudio / 解码器动态库，实测各占约 180ms 启动时间，
# 但只在「真正开始播放」和「解码音频」时才用得到 —— 延后到首次使用时再导入，
# 让窗口更快出来。
# ----------------------------------------------------------------------------
_sd_mod = None
_miniaudio_mod = None


def sounddevice_mod():
    global _sd_mod
    if _sd_mod is None:
        import sounddevice as m
        _sd_mod = m
    return _sd_mod


def miniaudio_mod():
    global _miniaudio_mod
    if _miniaudio_mod is None:
        import miniaudio as m
        _miniaudio_mod = m
    return _miniaudio_mod


def decode_thumb(data, side=96):
    """按目标尺寸直接解码内嵌封面，返回 QImage 或 None。

    旧写法 `QImage.fromData()` 会先把整张封面解出来（常见 1400x1400，约 40ms）
    再缩到 96px，一个几百首的库光这一步就要几十秒。改用 QImageReader 的
    setScaledSize，让解码器直接产出小图（JPEG 支持按比例 DCT 解码），快数倍，
    扫描时占用的 CPU/GIL 也少得多。任何异常都退回整图解出，行为保持一致。
    """
    try:
        from PySide6.QtCore import QBuffer, QSize
        from PySide6.QtGui import QImageReader
        buf = QBuffer()
        buf.setData(data)
        if not buf.open(QBuffer.ReadOnly):
            return None
        rd = QImageReader(buf)
        try:
            rd.setAutoTransform(True)
        except Exception:
            pass
        sz = rd.size()
        if sz.isValid() and sz.width() > 0 and sz.height() > 0:
            scale = max(side / float(sz.width()), side / float(sz.height()))
            if scale < 1.0:
                rd.setScaledSize(QSize(max(1, int(round(sz.width() * scale))),
                                       max(1, int(round(sz.height() * scale)))))
        img = rd.read()
        return None if img.isNull() else img
    except Exception:
        try:
            img = QImage.fromData(data)
            return None if img.isNull() else img
        except Exception:
            return None

# ----------------------------------------------------------------------------
# 雪白配色
# ----------------------------------------------------------------------------
BG        = "#ffffff"
PANEL     = "#fbfbfc"
INK       = "#17181c"
SOFT_INK  = "#4a4b52"
GRAY      = "#9a9ba3"
LIGHT     = "#d8d9de"
LINE      = "#ededf0"
HOVER     = "#f5f5f7"
RED       = "#e03131"
ART_BG    = "#f2f2f4"
BAR_INK   = "#26262b"

SR = 48000
N_BANDS = 64
N_SPOKES = 128
FFT_SIZE = 2048

AUDIO_EXTS = {".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac", ".opus"}


def fmt_time(sec: float) -> str:
    sec = max(0, int(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def fmt_size(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f} MB"
    return f"{n / 1024:.0f} KB"


def fmt_total(sec: float) -> str:
    h = sec / 3600.0
    if h >= 48:
        return f"{h / 24:.1f} 天"
    if h >= 1:
        return f"{int(h)} 小时 {int((sec % 3600) / 60)} 分"
    return f"{int(sec / 60)} 分钟"


# ----------------------------------------------------------------------------
# 频谱分析器
# ----------------------------------------------------------------------------
class Analyzer:
    def __init__(self, n_bands=N_BANDS):
        self.n_bands = n_bands
        self.window = np.hanning(FFT_SIZE).astype(np.float32)
        freqs = np.fft.rfftfreq(FFT_SIZE, 1.0 / SR)
        edges = np.logspace(math.log10(30), math.log10(min(20000, SR / 2 - 100)),
                            n_bands + 1)
        self.matrix = np.zeros((n_bands, len(freqs)), dtype=np.float32)
        for i in range(n_bands):
            lo, hi = edges[i], edges[i + 1]
            idx = np.where((freqs >= lo) & (freqs < hi))[0]
            if len(idx) == 0:
                idx = np.array([int(np.argmin(np.abs(freqs - lo)))])
            self.matrix[i, idx] = 1.0 / len(idx)
        self.spectrum = np.zeros(n_bands, dtype=np.float32)
        self.display = np.zeros(n_bands, dtype=np.float32)
        self._buf = np.zeros(0, dtype=np.float32)

    ATTACK_T = 0.025
    RELEASE_T = 0.16

    def process(self, mono: np.ndarray):
        self._buf = np.concatenate([self._buf, mono.astype(np.float32)])[-FFT_SIZE:]
        if len(self._buf) < FFT_SIZE:
            frame = np.pad(self._buf, (FFT_SIZE - len(self._buf), 0))
        else:
            frame = self._buf
        mag = np.abs(np.fft.rfft(frame * self.window)).astype(np.float32) / (FFT_SIZE / 2)
        bands = self.matrix @ mag
        db = 20.0 * np.log10(bands + 1e-8)
        self.spectrum = np.clip((db + 66.0) / 66.0, 0.0, 1.0).astype(np.float32)
        return self.spectrum

    def advance(self, dt: float) -> np.ndarray:
        dt = min(max(dt, 0.001), 0.1)
        up = 1.0 - math.exp(-dt / self.ATTACK_T)
        down = 1.0 - math.exp(-dt / self.RELEASE_T)
        t, d = self.spectrum, self.display
        k = np.where(t > d, up, down).astype(np.float32)
        self.display = d + (t - d) * k
        return self.display

    def decay(self):
        self.spectrum *= 0.9
        self.display *= 0.9

    def resampled(self, n: int) -> np.ndarray:
        x_old = np.linspace(0, 1, self.n_bands)
        x_new = np.linspace(0, 1, n)
        return np.interp(x_new, x_old, self.display).astype(np.float32)


# ----------------------------------------------------------------------------
# 播放引擎
# ----------------------------------------------------------------------------
class AudioEngine(QObject):
    ended = Signal()
    stateChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.samples = None
        self.pos = 0
        self.playing = False
        self.volume = 0.85
        self.duration = 0.0
        self.path = None
        self.stream = None
        self.lock = threading.Lock()
        self.analyzer = Analyzer()

    def set_track(self, samples, duration: float, path):
        with self.lock:
            self.samples = samples
            self.duration = duration
            self.path = path
            self.pos = 0
        self.playing = False
        self.stateChanged.emit()

    def play(self):
        if self.samples is None:
            return
        if self.stream is None:
            try:
                self.stream = sounddevice_mod().OutputStream(
                    samplerate=SR, channels=2, dtype="float32",
                    blocksize=512, callback=self._callback)
            except Exception as e:
                raise RuntimeError(f"无法打开播放设备：{e}")
        if self.pos >= len(self.samples):
            self.pos = 0
        self.stream.start()
        self.playing = True
        self.stateChanged.emit()

    def pause(self):
        if self.stream is not None:
            try:
                self.stream.stop()
            except Exception:
                pass
        self.playing = False
        self.stateChanged.emit()

    def toggle(self):
        self.pause() if self.playing else self.play()

    def seek(self, sec: float):
        if self.samples is None:
            return
        with self.lock:
            self.pos = int(np.clip(sec, 0, len(self.samples) / SR - 0.05) * SR)

    def position(self) -> float:
        return self.pos / SR

    def stop_stream(self):
        self.pause()
        if self.stream is not None:
            try:
                self.stream.close()
            except Exception:
                pass
            self.stream = None

    def _callback(self, outdata, frames, time_info, status):
        finished = False
        with self.lock:
            if self.samples is None:
                outdata[:] = 0
            else:
                n = self.samples.shape[0]
                start = self.pos
                end = start + frames
                if start >= n:
                    outdata[:] = 0
                    if self.playing:
                        self.playing = False
                        finished = True
                else:
                    chunk = self.samples[start:min(end, n)].copy()
                    if chunk.shape[0] < frames:
                        pad = np.zeros((frames - chunk.shape[0], 2), np.float32)
                        chunk = np.vstack([chunk, pad])
                    outdata[:] = chunk * self.volume
                    self.pos = min(end, n)
        block = outdata.copy()
        mono = block.mean(axis=1)
        if np.abs(mono).max() > 1e-5:
            self.analyzer.process(mono)
        else:
            self.analyzer.decay()
        if finished:
            self.ended.emit()


class MicEngine:
    def __init__(self):
        self.analyzer = Analyzer()
        self.stream = None
        self.active = False

    def start(self):
        if self.active:
            return
        self.stream = sounddevice_mod().InputStream(
            samplerate=SR, channels=1, dtype="float32",
            blocksize=512, callback=self._cb)
        self.stream.start()
        self.active = True

    def stop(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
        self.stream = None
        self.active = False
        self.analyzer.decay()

    def _cb(self, indata, frames, time_info, status):
        mono = indata[:, 0]
        if np.abs(mono).max() > 1e-4:
            self.analyzer.process(mono)
        else:
            self.analyzer.decay()


# ----------------------------------------------------------------------------
# 单曲加载线程（播放用：解码 + 元数据 + 分析）
# ----------------------------------------------------------------------------
class LoadThread(QThread):
    loaded = Signal(dict)
    failed = Signal(str)

    def __init__(self, path, seq=0, parent=None):
        super().__init__(parent)
        self.path = path
        self.seq = seq

    def run(self):
        try:
            path = self.path
            arr = self._decode(path)
            duration = arr.shape[0] / SR
            mono = arr.mean(axis=1)

            n_env = min(1200, mono.shape[0])
            hop = max(1, mono.shape[0] // n_env)
            usable = mono[: hop * n_env].reshape(-1, hop)
            wave_env = np.abs(usable).max(axis=1).astype(np.float32)

            rms = float(np.sqrt((mono ** 2).mean()))
            loudness = 20 * math.log10(rms + 1e-9)
            win = SR // 2
            n_win = mono.shape[0] // win
            if n_win >= 4:
                fr = np.sqrt((mono[: n_win * win].reshape(n_win, win) ** 2).mean(axis=1))
                fdB = 20 * np.log10(fr + 1e-9)
                dyn = float(np.percentile(fdB, 95) - np.percentile(fdB, 10))
            else:
                dyn = 0.0

            bpm = 0.0
            try:
                hop2 = 480
                n2 = (mono.shape[0] // hop2) * hop2
                env = np.abs(mono[:n2]).reshape(-1, hop2).max(axis=1)
                env = env - env.mean()
                best_lag, best_c = 0, -1
                for lag in range(33, 100):
                    c = float(np.dot(env[:-lag], env[lag:]) / (len(env) - lag))
                    if c > best_c:
                        best_c, best_lag = c, lag
                if best_lag:
                    bpm = 6000.0 / best_lag
            except Exception:
                pass

            meta = self._read_tags(path, duration)
            self.loaded.emit({
                "seq": self.seq, "samples": arr, "duration": duration,
                "path": path, "wave_env": wave_env, "loudness": loudness,
                "dyn": dyn, "bpm": bpm, "meta": meta,
            })
        except Exception as e:
            self.failed.emit(f"{self.seq}|{e}")

    # ---------- 解码 ----------
    @staticmethod
    def _resample_to(data: np.ndarray, sr_in: int) -> np.ndarray:
        if sr_in == SR or data.shape[0] == 0:
            return data.astype(np.float32)
        n = int(data.shape[0] * SR / sr_in)
        x_old = np.arange(data.shape[0], dtype=np.float64)
        x_new = np.linspace(0.0, data.shape[0] - 1, n)
        out = np.empty((n, data.shape[1]), np.float32)
        for c in range(data.shape[1]):
            out[:, c] = np.interp(x_new, x_old, data[:, c])
        return out

    @classmethod
    def _decode(cls, path: str) -> np.ndarray:
        try:
            ma = miniaudio_mod()
            dec = ma.decode_file(path, output_format=ma.SampleFormat.SIGNED16,
                                 nchannels=2, sample_rate=SR,
                                 dither=ma.DitherMode.TRIANGLE)
            return np.asarray(dec.samples, dtype=np.float32).reshape(-1, 2) / 32768.0
        except Exception:
            pass
        try:
            import soundfile as sf
            data, sr = sf.read(path, dtype="float32", always_2d=True)
            data = data[:, :2] if data.shape[1] >= 2 else np.repeat(data, 2, axis=1)
            return cls._resample_to(data, sr)
        except Exception:
            pass
        try:
            import subprocess
            import imageio_ffmpeg
            exe = imageio_ffmpeg.get_ffmpeg_exe()
            fd, tmp = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            try:
                subprocess.run(
                    [exe, "-y", "-i", path, "-ar", str(SR), "-ac", "2",
                     "-c:a", "pcm_s16le", tmp],
                    capture_output=True, timeout=180, check=True)
                data, sr = sf.read(tmp, dtype="float32", always_2d=True)
            finally:
                try:
                    os.remove(tmp)
                except Exception:
                    pass
            data = data[:, :2] if data.shape[1] >= 2 else np.repeat(data, 2, axis=1)
            return cls._resample_to(data, sr)
        except Exception:
            pass
        raise RuntimeError("无法解码该文件（已尝试内置解码器、libsndfile、ffmpeg）。")

    @staticmethod
    def _read_tags(path, duration):
        ext = os.path.splitext(path)[1].lstrip(".").upper()
        info = {
            "title": os.path.splitext(os.path.basename(path))[0],
            "artist": "未知艺术家",
            "album": "",
            "format": ext,
            "bit_depth": "--",
            "sample_rate": "--",
            "bitrate": "--",
            "file_size": fmt_size(os.path.getsize(path)),
            "channels": "--",
            "cover": None,
        }
        try:
            from mutagen import File as MFile
            m = MFile(path, easy=True)
            if m is None:
                return info
            if m.get("title"):
                info["title"] = m["title"][0]
            if m.get("artist"):
                info["artist"] = m["artist"][0]
            if m.get("album"):
                info["album"] = m["album"][0]
            if duration > 0:
                info["bitrate"] = f"{int(os.path.getsize(path) * 8 / duration / 1000)} kbps"
            try:
                if m.info.sample_rate:
                    srate = m.info.sample_rate
                    info["sample_rate"] = f"{srate / 1000:g} kHz" if srate % 1000 == 0 else f"{srate} Hz"
                if getattr(m.info, "bits_per_sample", 0):
                    info["bit_depth"] = f"{m.info.bits_per_sample}-bit"
                ch = getattr(m.info, "channels", 0)
                if ch == 1:
                    info["channels"] = "单声道"
                elif ch == 2:
                    info["channels"] = "立体声"
                elif ch:
                    info["channels"] = f"{ch} 声道"
            except Exception:
                pass
            cover = None
            try:
                if hasattr(m, "pictures") and m.pictures:
                    cover = m.pictures[0].data
                elif m.tags:
                    apics = m.tags.getall("APIC") if hasattr(m.tags, "getall") else []
                    if apics:
                        cover = apics[0].data
                    covr = m.tags.get("covr") if hasattr(m.tags, "get") else None
                    if cover is None and covr:
                        cover = bytes(covr[0])
            except Exception:
                pass
            if cover:
                img = QImage.fromData(cover)
                if not img.isNull():
                    info["cover"] = img
        except Exception:
            pass
        return info


# ----------------------------------------------------------------------------
# 持久化（最近播放 / 上次播放 / 库目录）
# ----------------------------------------------------------------------------
class Store:
    DEFAULTS = {"auto_lyrics": True, "auto_cover": True, "auto_fullscreen": False,
                # 播放页背景 / 氛围特效
                "bg_mode": "fluid", "bg_image": "", "bg_veil": 0.22,
                "weather": "none"}

    def __init__(self):
        if getattr(sys, "frozen", False):
            base = os.path.dirname(sys.executable)
        else:
            base = os.path.dirname(os.path.abspath(__file__))
        self.base = base
        self.path = os.path.join(base, "audiostore.json")
        self.cache_dir = os.path.join(base, "audiocache")
        self.recent = []      # 最近播放路径（新→旧）
        self.last = None      # 上次播放
        self.folders = []     # 扫描目录
        self.listened = 0.0   # 累计听歌时长（秒）
        self.settings = dict(self.DEFAULTS)
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.recent = data.get("recent", [])
            self.last = data.get("last")
            self.folders = data.get("folders", [])
            self.listened = float(data.get("listened", 0.0) or 0.0)
            self.settings.update(data.get("settings") or {})
        except Exception:
            pass
        # 一次性迁移：auto_fullscreen 在这之前是「死配置」（全项目从未读取，
        # 播放页无条件 showFullScreen），老存档里的 true 只是 DEFAULTS 带出来
        # 的、并非用户的选择 —— 归零一次，让新版默认「跟随窗口大小」。之后
        # 用户在播放页手动开启即正常持久化，不会再被重置。
        if self.settings.get("fs_default_v2") is None:
            self.settings["auto_fullscreen"] = False
            self.settings["fs_default_v2"] = True

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"recent": self.recent[:200], "last": self.last,
                           "folders": self.folders,
                           "listened": round(self.listened, 1),
                           "settings": self.settings},
                          f, ensure_ascii=False)
        except Exception:
            pass

    def get(self, key, default=None):
        return self.settings.get(key, self.DEFAULTS.get(key, default))

    def set_setting(self, key, value):
        self.settings[key] = bool(value)
        self.save()

    def push_recent(self, path: str):
        if path in self.recent:
            self.recent.remove(path)
        self.recent.insert(0, path)
        self.recent = self.recent[:200]
        self.last = path
        self.save()

    # ---------------- 歌词 / 封面 缓存 ----------------
    @staticmethod
    def cache_key(artist, title, album=""):
        raw = f"{(artist or '').strip().lower()}|{(title or '').strip().lower()}|{(album or '').strip().lower()}"
        return hashlib.md5(raw.encode("utf-8", "ignore")).hexdigest()[:16]

    def _ensure_cache(self):
        try:
            os.makedirs(os.path.join(self.cache_dir, "lyrics"), exist_ok=True)
            os.makedirs(os.path.join(self.cache_dir, "covers"), exist_ok=True)
        except Exception:
            pass

    def lyric_path(self, key):
        self._ensure_cache()
        return os.path.join(self.cache_dir, "lyrics", key + ".lrc")

    def cover_path(self, key):
        self._ensure_cache()
        return os.path.join(self.cache_dir, "covers", key + ".jpg")

    def read_cached_lyric(self, key):
        try:
            p = self.lyric_path(key)
            if os.path.isfile(p):
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    return f.read()
        except Exception:
            pass
        return ""

    def write_cached_lyric(self, key, text):
        try:
            with open(self.lyric_path(key), "w", encoding="utf-8") as f:
                f.write(text)
        except Exception:
            pass

    def read_cached_cover(self, key):
        p = self.cover_path(key)
        return p if os.path.isfile(p) and os.path.getsize(p) > 1024 else None

    def write_cached_cover(self, key, data: bytes):
        try:
            with open(self.cover_path(key), "wb") as f:
                f.write(data)
            return self.cover_path(key)
        except Exception:
            return None


# ----------------------------------------------------------------------------
# 音乐库扫描线程（批量读标签，含小封面）
# ----------------------------------------------------------------------------
class LibraryThread(QThread):
    batch = Signal(list)          # list[dict]
    progress = Signal(int, int)
    scanDone = Signal()

    def __init__(self, folders, parent=None, cache_dir=None):
        super().__init__(parent)
        self.folders = list(folders)
        # 封面/歌词缓存目录：扫描时用来回填联网匹配过的封面
        self.cache_dir = cache_dir

    @staticmethod
    def read_entry(path, cache_dir=None):
        entry = {
            "path": path,
            "title": os.path.splitext(os.path.basename(path))[0],
            "artist": "未知艺术家",
            "album": "",
            "duration": 0.0,
            "format": os.path.splitext(path)[1].lstrip(".").upper(),
            "bit_depth": "--",
            "sample_rate": "--",
            "cover": None,
        }
        try:
            from mutagen import File as MF
            m = MF(path, easy=True)
            if m is not None:
                if m.get("title"):
                    entry["title"] = m["title"][0]
                if m.get("artist"):
                    entry["artist"] = m["artist"][0]
                if m.get("album"):
                    entry["album"] = m["album"][0]
                try:
                    entry["duration"] = float(getattr(m.info, "length", 0) or 0)
                    if m.info.sample_rate:
                        srate = m.info.sample_rate
                        entry["sample_rate"] = (f"{srate / 1000:g} kHz"
                                                if srate % 1000 == 0 else f"{srate} Hz")
                    if getattr(m.info, "bits_per_sample", 0):
                        entry["bit_depth"] = f"{m.info.bits_per_sample}-bit"
                except Exception:
                    pass
                cover = None
                try:
                    if hasattr(m, "pictures") and m.pictures:
                        cover = m.pictures[0].data
                    elif m.tags:
                        apics = m.tags.getall("APIC") if hasattr(m.tags, "getall") else []
                        if apics:
                            cover = apics[0].data
                        covr = m.tags.get("covr") if hasattr(m.tags, "get") else None
                        if cover is None and covr:
                            cover = bytes(covr[0])
                except Exception:
                    pass
                if cover:
                    img = decode_thumb(cover, 96)
                    if img is not None:
                        entry["cover"] = img
        except Exception:
            pass
        # 内嵌封面缺失时，回填「联网匹配后写进缓存」的那张 —— 缓存里明明
        # 存着（audiocache/covers/<key>.jpg），但重扫时从不去读它，导致
        # 重启后匹配到的封面全部"消失"，用户看到的就是"匹配封面后无法
        # 保存"。
        if entry["cover"] is None and cache_dir:
            try:
                cp = os.path.join(
                    cache_dir, "covers",
                    Store.cache_key(entry["artist"], entry["title"],
                                    entry["album"]) + ".jpg")
                if os.path.isfile(cp) and os.path.getsize(cp) > 1024:
                    img = QImage(cp)
                    if not img.isNull():
                        entry["cover"] = img.scaled(
                            96, 96,
                            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                            Qt.TransformationMode.SmoothTransformation)
            except Exception:
                pass
        return entry

    def run(self):
        files = []
        seen = set()
        for root_dir in self.folders:
            if not os.path.isdir(root_dir):
                continue
            for dirpath, dirnames, filenames in os.walk(root_dir):
                dirnames.sort()
                for fn in sorted(filenames):
                    if os.path.splitext(fn)[1].lower() in AUDIO_EXTS:
                        p = os.path.join(dirpath, fn)
                        if p not in seen:
                            seen.add(p)
                            files.append(p)
        total = len(files)
        out = []
        for i, p in enumerate(files):
            if self.isInterruptionRequested():
                break
            out.append(self.read_entry(p, self.cache_dir))
            if len(out) >= 40:
                self.batch.emit(out)
                out = []
            self.progress.emit(i + 1, total)
        if out:
            self.batch.emit(out)
        self.scanDone.emit()


# ----------------------------------------------------------------------------
# 歌词：LRC 解析 / 本地同名 lrc / 联网获取
# ----------------------------------------------------------------------------
LRC_TIME = re.compile(r"\[(\d{1,3}):(\d{1,2}(?:[.:]\d{1,3})?)\]")


def parse_lrc(text: str):
    """解析 LRC 文本 → [(秒, 文本), ...]（按时间升序）。非时间轴歌词返回 None。"""
    if not text:
        return None
    out = []
    for raw in text.splitlines():
        raw = raw.strip().lstrip("﻿")
        if not raw:
            continue
        times = []
        rest = raw
        while True:
            m = LRC_TIME.match(rest)
            if not m:
                break
            mm = int(m.group(1))
            ss = float(m.group(2).replace(":", "."))
            times.append(mm * 60 + ss)
            rest = rest[m.end():].strip()
        if not times:
            continue
        for t in times:
            out.append((t, rest))
    if not out:
        return None
    out.sort(key=lambda x: x[0])
    return out


LRC_SUBDIRS = ("lyrics", "Lyrics", "LYRICS", "歌词", "lrc")


def find_local_lrc(path: str):
    """同名 .lrc：同目录 → 常见歌词子目录；找不到返回 None"""
    try:
        d = os.path.dirname(path)
        stem = os.path.splitext(os.path.basename(path))[0]
        cands = [os.path.join(d, stem + ".lrc")]
        for sub in LRC_SUBDIRS:
            cands.append(os.path.join(d, sub, stem + ".lrc"))
        for cand in cands:
            if os.path.isfile(cand):
                with open(cand, "r", encoding="utf-8", errors="ignore") as f:
                    return f.read()
        # 大小写 / 前后空格差异
        for fn in os.listdir(d):
            if fn.lower().strip() == (stem + ".lrc").lower().strip():
                with open(os.path.join(d, fn), "r", encoding="utf-8",
                          errors="ignore") as f:
                    return f.read()
    except Exception:
        pass
    return None


def http_get(url: str, timeout=8.0, headers=None, referer=None) -> bytes:
    h = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/122.0 Safari/537.36"),
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if referer:
        h["Referer"] = referer
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ----------------------------------------------------------------------------
# 文本归一化 + 候选匹配打分（决定「搜到的哪一条才是这首歌」）
# ----------------------------------------------------------------------------
NOISE = ("cover", "翻唱", "remix", "dj", "伴奏", "纯音乐", "live", "现场",
         "inst.", "instrumental", "demo", "翻奏", "钢琴版", "吉他版",
         "女声版", "男声版", "深情版", "原唱", "剪辑版", "片段", "铃声")


def norm_text(s: str) -> str:
    """归一化：去括号内容 / 版本噪声 / 空格标点，转小写"""
    if not s:
        return ""
    s = str(s)
    s = re.sub(r"[（(\[【][^）)\]】]*[）)\]】]", "", s)   # 去掉括号及其内容
    s = s.replace("－", "-").replace("–", "-").replace("—", "-")
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "", s)            # 只留中英文数字
    return s.lower().strip()


def split_filename(title: str):
    """「周杰伦 - 晴天」「01. 晴天」这类文件名拆出歌手/标题"""
    t = (title or "").strip()
    for sep in (" - ", " – ", " — ", "-", "－"):
        if sep in t:
            a, b = t.split(sep, 1)
            if a.strip() and b.strip() and len(a) <= 24:
                return b.strip(), a.strip()
    return re.sub(r"^\d{1,3}[\s._、]+", "", t).strip(), ""


def match_score(cand_title, cand_artist, cand_dur, title, artist, duration):
    """候选歌曲打分（0~100+）：标题 / 歌手 / 时长三重匹配"""
    nt, na = norm_text(title), norm_text(artist)
    ct, ca = norm_text(cand_title), norm_text(cand_artist)
    if not nt and not ct:
        return -999
    s = 0
    # 标题
    if nt and ct:
        if nt == ct:
            s += 100
        elif nt in ct or ct in nt:
            s += 62
        else:
            # 逐字重合率
            common = len(set(nt) & set(ct))
            s += int(38 * common / max(1, len(set(nt))))
    # 歌手
    if na and ca:
        if na == ca:
            s += 70
        elif na in ca or ca in na:
            s += 45
        else:
            common = len(set(na) & set(ca))
            s += int(24 * common / max(1, len(set(na))))
    else:
        s += 8
    # 时长（本地元数据常缺，缺失则不计）
    if duration and cand_dur:
        d = abs(float(cand_dur) - float(duration))
        if d <= 3:
            s += 34
        elif d <= 8:
            s += 18
        elif d <= 15:
            s += 6
        else:
            s -= 26
    # 版本噪声惩罚
    low = (cand_title or "").lower()
    for w in NOISE:
        if w in low:
            s -= 45
            break
    return s


def pick_best(cands, title, artist, duration):
    """cands: [(title, artist, duration, payload)] → 最佳 payload 或 None"""
    best, bs = None, -9999
    for c in cands:
        sc = match_score(c[0], c[1], c[2], title, artist, duration)
        if sc > bs:
            bs, best = sc, c
    return (best, bs) if best is not None else (None, -9999)


class LyricsThread(QThread):
    """多源歌词聚合：网易云（主，中文曲库最全）→ LRCLIB（外网兜底）
    每条候选都按「标题+歌手+时长」打分，避免匹配到翻唱/深情版。
    """
    fetched = Signal(str, str, str)   # key, lrc_text(失败为空), 来源说明
    status = Signal(str)              # 过程提示（正在搜索…）

    def __init__(self, key, title, artist, album="", duration=0.0, parent=None):
        super().__init__(parent)
        self.key, self.title = key, title
        self.artist, self.album = artist, album
        self.duration = duration
        self.song_id = None

    # ---------------- 网易云 ----------------
    def _ne_search(self, keyword):
        url = ("https://music.163.com/api/search/get?s="
               + urllib.parse.quote(keyword) + "&type=1&offset=0&limit=20")
        data = json.loads(http_get(url, 8, referer="https://music.163.com/")
                          .decode("utf-8", "ignore"))
        cands = []
        for s in ((data.get("result") or {}).get("songs") or []):
            artists = "/".join(a.get("name", "") for a in (s.get("artists") or []))
            cands.append((s.get("name", ""), artists,
                          (s.get("duration") or 0) / 1000.0, s.get("id")))
        return cands

    def _ne_lyric(self, sid):
        for url in (f"https://music.163.com/api/song/lyric?os=pc&id={sid}&lv=-1&kv=-1&tv=-1",
                    f"https://music.163.com/api/song/lyric?id={sid}&lv=1&kv=1&tv=-1"):
            try:
                r = json.loads(http_get(url, 8, referer="https://music.163.com/")
                               .decode("utf-8", "ignore"))
            except Exception:
                continue
            txt = ((r.get("lrc") or {}).get("lyric") or "").strip()
            if txt:
                return txt
        return ""

    def _try_netease(self):
        t, a = self.title, self.artist
        queries = []
        if a and a != "未知艺术家":
            queries.append(f"{a} {t}")
        queries.append(t)
        if self.album:
            queries.append(f"{a} {self.album} {t}")
        best_id, best_score = None, -9999
        for kw in queries:
            try:
                cands = self._ne_search(kw)
            except Exception:
                continue
            c, sc = pick_best(cands, t, a, self.duration)
            if c is not None and sc > best_score:
                best_id, best_score = c[3], sc
            if best_score >= 140:      # 标题+歌手都对上了，够好
                break
        if best_id is None or best_score < 40:
            return ""
        self.song_id = best_id
        return self._ne_lyric(best_id)

    # ---------------- LRCLIB ----------------
    def _try_lrclib(self):
        base = "https://lrclib.net/api"
        t = urllib.parse.quote((self.title or "").strip())
        a = urllib.parse.quote((self.artist or "").strip())
        try:
            url = (f"{base}/get?track_name={t}&artist_name={a}"
                   f"&album_name={urllib.parse.quote((self.album or '').strip())}"
                   f"&duration={int(self.duration or 0)}")
            d = json.loads(http_get(url, 8).decode("utf-8", "ignore"))
            txt = d.get("syncedLyrics") or d.get("plainLyrics") or ""
            if txt:
                return txt
        except Exception:
            pass
        try:
            url = f"{base}/search?track_name={t}&artist_name={a}&limit=8"
            arr = json.loads(http_get(url, 8).decode("utf-8", "ignore")) or []
            cands = [(it.get("trackName", ""), it.get("artistName", ""),
                      it.get("duration") or 0, it) for it in arr]
            c, sc = pick_best(cands, self.title, self.artist, self.duration)
            if c is not None:
                it = c[3]
                if it.get("syncedLyrics"):
                    return it["syncedLyrics"]
                if sc >= 55 and it.get("plainLyrics"):
                    return it["plainLyrics"]
            for it in arr:                       # 兜底：任一条带时间轴的
                if it.get("syncedLyrics"):
                    return it["syncedLyrics"]
        except Exception:
            pass
        return ""

    def run(self):
        text, source = "", ""
        try:
            self.status.emit("网易云搜索中…")
            text = self._try_netease()
            if text:
                source = "网易云"
        except Exception:
            text = ""
        if not text:
            try:
                self.status.emit("LRCLIB 搜索中…")
                text = self._try_lrclib()
                if text:
                    source = "LRCLIB"
            except Exception:
                text = ""
        self.fetched.emit(self.key, text or "", source)


class CoverThread(QThread):
    """专辑封面：网易云歌曲详情 picUrl（中文最准）→ iTunes 兜底"""
    fetched = Signal(str, bytes)

    def __init__(self, key, title, artist, album="", duration=0.0, parent=None):
        super().__init__(parent)
        self.key, self.title = key, title
        self.artist, self.album = artist, album
        self.duration = duration

    def _q(self, s):
        return urllib.parse.quote((s or "").strip())

    def _netease(self):
        queries = []
        if self.artist and self.artist != "未知艺术家":
            queries.append(f"{self.artist} {self.title}")
        queries.append(self.title)
        if self.album:
            queries.append(f"{self.artist} {self.album}")
        for kw in queries:
            try:
                url = ("https://music.163.com/api/search/get?s=" + self._q(kw)
                       + "&type=1&offset=0&limit=20")
                d = json.loads(http_get(url, 8, referer="https://music.163.com/")
                               .decode("utf-8", "ignore"))
                songs = (d.get("result") or {}).get("songs") or []
            except Exception:
                continue
            cands = []
            for s in songs:
                artists = "/".join(a.get("name", "") for a in (s.get("artists") or []))
                cands.append((s.get("name", ""), artists,
                              (s.get("duration") or 0) / 1000.0, s.get("id")))
            c, sc = pick_best(cands, self.title, self.artist, self.duration)
            if c is None or sc < 40:
                continue
            try:
                det = json.loads(http_get(
                    f"https://music.163.com/api/song/detail?ids=[{c[3]}]", 8,
                    referer="https://music.163.com/").decode("utf-8", "ignore"))
                ss = (det.get("songs") or [])
                pic = ""
                if ss:
                    pic = ((ss[0].get("album") or {}).get("picUrl") or "")
                if not pic:
                    continue
                if "?" in pic:
                    pic = pic.split("?")[0]
                return http_get(pic + "?param=600y600", 10,
                                referer="https://music.163.com/")
            except Exception:
                continue
        return b""

    def _itunes(self):
        for term, entity in (
            (f"{self.artist} {self.album}" if self.album else
             f"{self.artist} {self.title}", "album"),
            (f"{self.artist} {self.title}", "song"),
            (self.title, "song"),
        ):
            try:
                url = ("https://itunes.apple.com/search?term=" + self._q(term)
                       + f"&entity={entity}&limit=3&country=CN")
                data = json.loads(http_get(url, 8).decode("utf-8", "ignore"))
            except Exception:
                continue
            for it in (data.get("results") or []):
                art = it.get("artworkUrl100") or it.get("artworkUrl60") or ""
                if not art:
                    continue
                big = re.sub(r"/\d+x\d+bb\.jpg$", "/600x600bb.jpg", art)
                try:
                    return http_get(big, 10)
                except Exception:
                    continue
        return b""

    def run(self):
        img = b""
        try:
            img = self._netease()
        except Exception:
            img = b""
        if not img:
            try:
                img = self._itunes()
            except Exception:
                img = b""
        self.fetched.emit(self.key, img)
