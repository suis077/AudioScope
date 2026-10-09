# -*- coding: utf-8 -*-
"""导出软件图标资源 icon.ico（多尺寸，PNG 压缩，Windows Vista+ 支持）。

绘制逻辑与运行时用的 QIcon 完全共用 widgets.app_icon_pixmap，所以程序里
看到的图标和 exe 文件图标是同一个画面。改了图标设计只要重跑本脚本。
"""
import os
import struct
import sys

from PySide6.QtCore import QBuffer, QByteArray
from PySide6.QtWidgets import QApplication

from widgets import app_icon_pixmap

SIZES = (16, 24, 32, 48, 64, 128, 256)


def png_bytes(pm):
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.WriteOnly)
    pm.save(buf, "PNG")
    buf.close()
    return bytes(ba)


def write_ico(path, sizes):
    """手写 ICO 容器，把各尺寸 PNG 塞进去（PNG-in-ICO）。"""
    imgs = [(s, png_bytes(app_icon_pixmap(s))) for s in sizes]
    head = struct.pack("<HHH", 0, 1, len(imgs))
    offset = 6 + 16 * len(imgs)
    entries, blobs = b"", b""
    for s, data in imgs:
        dim = 0 if s >= 256 else s          # 0 表示 256
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32,
                               len(data), offset)
        blobs += data
        offset += len(data)
    payload = head + entries + blobs
    with open(path, "wb") as f:
        f.write(payload)
    return len(payload)


def main():
    app = QApplication(sys.argv)            # noqa: F841  (QPixmap 需要 QGuiApplication)
    here = os.path.dirname(os.path.abspath(__file__))
    ico = os.path.join(here, "icon.ico")
    n = write_ico(ico, SIZES)
    print("icon.ico  %d bytes  (%s)" % (n, ", ".join(str(s) for s in SIZES)))
    app_icon_pixmap(256).save(os.path.join(here, "_icon_preview.png"), "PNG")
    print("preview   _icon_preview.png")


if __name__ == "__main__":
    main()
