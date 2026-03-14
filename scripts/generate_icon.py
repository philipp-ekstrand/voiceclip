#!/usr/bin/env python3
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap


def render_master_png(path: Path) -> None:
    app = QGuiApplication([])

    size = 1024
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    rect = QRectF(86, 86, 852, 852)
    bg_path = QPainterPath()
    bg_path.addRoundedRect(rect, 190, 190)

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#ff5a1f"))
    painter.drawPath(bg_path)

    capsule_gradient = QLinearGradient(512, 286, 512, 546)
    capsule_gradient.setColorAt(0.0, QColor("#f7f7f7"))
    capsule_gradient.setColorAt(1.0, QColor("#dcdcdc"))
    painter.setBrush(capsule_gradient)
    painter.drawRoundedRect(QRectF(410, 258, 204, 308), 102, 102)

    pen_white = QPen(QColor("#f3f3f3"), 46, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen_white)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    smile = QPainterPath()
    smile.moveTo(360, 548)
    smile.cubicTo(396, 676, 628, 676, 664, 548)
    painter.drawPath(smile)
    painter.drawLine(512, 704, 512, 786)

    side_pen = QPen(QColor("#f2f2f2"), 30, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(side_pen)
    painter.drawLine(244, 392, 244, 538)
    painter.drawLine(780, 392, 780, 538)

    side_pen_small = QPen(QColor("#f1f1f1"), 24, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(side_pen_small)
    painter.drawLine(316, 436, 316, 496)
    painter.drawLine(708, 436, 708, 496)

    painter.end()
    pixmap.save(str(path), "PNG")
    app.quit()


def generate_icns(output_icns: Path) -> None:
    if not shutil.which("iconutil"):
        raise RuntimeError("iconutil fehlt")
    if not shutil.which("sips"):
        raise RuntimeError("sips fehlt")

    output_icns.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="voiceclip-icon-") as tmpdir:
        tmp = Path(tmpdir)
        master_png = tmp / "voiceClip_1024.png"
        iconset = tmp / "voiceClip.iconset"
        iconset.mkdir(parents=True, exist_ok=True)

        render_master_png(master_png)

        def make_icon(px: int, filename: str) -> None:
            subprocess.run(
                ["sips", "-z", str(px), str(px), str(master_png), "--out", str(iconset / filename)],
                check=True,
                capture_output=True,
                text=True,
            )

        make_icon(16, "icon_16x16.png")
        make_icon(32, "icon_16x16@2x.png")
        make_icon(32, "icon_32x32.png")
        make_icon(64, "icon_32x32@2x.png")
        make_icon(128, "icon_128x128.png")
        make_icon(256, "icon_128x128@2x.png")
        make_icon(256, "icon_256x256.png")
        make_icon(512, "icon_256x256@2x.png")
        make_icon(512, "icon_512x512.png")
        make_icon(1024, "icon_512x512@2x.png")

        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(output_icns)], check=True)


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("assets/voiceClip.icns")
    generate_icns(out)
    print(f"Icon erstellt: {out}")
