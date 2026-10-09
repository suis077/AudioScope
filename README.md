# AudioScope · 声波观测台

一个用 **Python + PySide6** 编写的本地音乐播放器与音频可视化工具。雪白极简风格，带一个会流动的沉浸式播放页。

> 纯本地运行：不登录、不上传，音乐文件与播放记录都留在你自己的机器上。

---

## 功能一览

### 音乐库
- 扫描本地文件夹，自动读取标题 / 艺术家 / 专辑 / 时长 / 封面（`mutagen`）
- 按专辑、艺术家自动归类（网格视图 + 详情页曲目表）
- 列表排序、关键字搜索、最近播放持久化
- 拖拽音频文件或文件夹直接载入

### 播放
- 频谱柱状图 + 环形可视化，60fps 平滑包络（attack/release 指数平滑）
- 音频元数据面板：采样率 / 位深 / 声道 / BPM / 响度 / 动态范围
- 解码三级兜底链：`miniaudio` → `soundfile` → `ffmpeg`（`imageio-ffmpeg` 自带二进制）
- 麦克风实时监控
- 空格暂停、`←/→` 快退快进、播放队列（可插入到下一首播放）

### 沉浸式播放页
- **流体背景**：把参考实现的 HLSL 像素着色器移植成 numpy，运行时按调色板着色、随时间连续流动；用「像素预算」(FLUID_PIXELS) 让不同窗口尺寸下的单帧开销基本恒定
- 前景组件完全不画底色，直接嵌在流体背景上（hover 才浮起一点点白）
- **歌词**：LRC 解析 + 本地 `.lrc` 查找 + 网易云 / LRCLIB 多源联网匹配；字号、双色、对齐方式可自定义
- **封面**：联网匹配（网易云 / iTunes Search），匹配结果写入本地缓存并在重启后回填
- 特效：飘雪 / 下雨（预渲染精灵、真实 dt 推进、雨滴落地涟漪）
- 背景模式：流体 / 自定义图片（虚化浓度可调）/ 纯净白

### 界面
- 无边框窗口，边缘拖拽缩放，标题栏拖动
- 首页问候 + 上次播放 + 曲库统计 + 最近播放卡片
- 主题：浅色雪白 ↔ 沉浸深色（进入播放页自动切换）

---

## 运行环境

- **Python 3.10+**（开发与验证基于 3.13）
- Windows（已在 Windows 上打包验证；代码为跨平台写法，macOS / Linux 未做测试）

## 快速开始

### 方式一：直接下载（免安装，推荐普通用户）

下载仓库里的 [`dist/AudioScope.exe`](dist/AudioScope.exe) 双击即可运行，**不需要装 Python 或任何依赖**。

- Windows 64 位，单文件约 89 MB
- 首次启动稍慢（需要自解压，约 3 秒），之后正常
- 首次运行会在 exe 同级目录生成 `audiostore.json`（设置与最近播放）和 `audiocache/`（歌词与封面缓存），可以直接删掉，程序会重建

### 方式二：从源码运行

```bash
# 1) 安装依赖
pip install -r requirements.txt

# 2) 启动
python app.py
```

首次启动后，在「所有歌曲」页选择音乐文件夹即可开始扫描。

## 打包为 exe（Windows）

仓库内提供了两份 PyInstaller 配置：

```bash
pip install pyinstaller

# 单文件版（启动稍慢，体积小）
pyinstaller AudioScope.spec

# 目录版（启动快，推荐分发）
pyinstaller AudioScope_dir.spec
```

`icon.ico` 已随仓库提供；如需重新生成：

```bash
python _mkeico.py
```

## 回归自测

项目自带一套 28 组自测，覆盖配色、着色器、歌词定位、队列、封面刷新、性能不变量等：

```bash
python -u selftest.py
```

---

## 项目结构

```
AudioScope/
├── app.py                # 主窗口、页面路由、播放调度、设置持久化
├── core.py               # 配色常量、音频引擎、解码链、Store、后台线程
├── widgets.py            # 自绘组件：流体背景、环形可视化、歌词、开关、图标…
├── pages.py              # 各功能页：首页 / 列表 / 网格 / 详情 / 沉浸播放页
├── selftest.py           # 28 组回归自测
├── _mkeico.py            # 生成 icon.ico（PNG-in-ICO 容器）
├── icon.ico              # 应用图标
├── AudioScope.spec       # PyInstaller 单文件打包配置
├── AudioScope_dir.spec   # PyInstaller 目录版打包配置
└── requirements.txt
```

运行时会在程序目录生成 `audiostore.json`（设置 / 最近播放）与 `audiocache/`（歌词与封面缓存），两者均已加入 `.gitignore`。

---

## 技术要点

| 方向 | 做法 |
| --- | --- |
| 流体背景 | HLSL 值噪声着色器 → numpy 向量化；`FLUID_PIXELS` 像素预算反推内部分辨率 |
| 视觉一致性 | 4 主色按 HSL 色相分散提取，逐色明度硬上限，保证说明文字在背景上的对比度 |
| 高 DPI | 离屏图层按 `devicePixelRatioF()` 建立物理像素画布，避免 150% 缩放下文字发虚 |
| 性能 | 曲库重建合并调度、圆角/封面/图标缓存、空闲降频、`QImageReader` 直接解小图 |

## 已知限制

- 联网歌词 / 封面依赖第三方公开接口（网易云、LRCLIB、iTunes Search），接口变动可能影响匹配率
- 仅针对浅色与深色两套主题做过视觉校验，未做完整无障碍适配

## 许可

仓库暂未附带开源许可证文件，如需开源请自行补充（例如 MIT / Apache-2.0）。
