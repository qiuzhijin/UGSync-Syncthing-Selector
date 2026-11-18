"""Create our own simple status glyphs, not third-party product icons."""
from pathlib import Path
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen

output = Path(sys.argv[1])
for name, color in [('blue', '#1689ed'), ('green', '#27ae60'), ('red', '#df4040')]:
    image = QImage(32, 32, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    # Explorer composites the entire canvas over the file's original icon.
    painter.translate(0, 18)
    painter.scale(0.44, 0.44)
    painter.setPen(QPen(QColor('white'), 2))
    painter.setBrush(QColor(color))
    painter.drawEllipse(2, 2, 28, 28)
    painter.setPen(QPen(QColor('white'), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    if name == 'green':
        painter.drawLine(8, 16, 13, 21)
        painter.drawLine(13, 21, 24, 10)
    elif name == 'red':
        painter.drawLine(12, 10, 12, 22)
        painter.drawLine(20, 10, 20, 22)
    else:
        painter.drawArc(8, 8, 16, 16, 30 * 16, 270 * 16)
        painter.drawLine(23, 10, 24, 16)
        painter.drawLine(24, 16, 18, 15)
    painter.end()
    if not image.save(str(output / (name + '.ico'))):
        raise RuntimeError('icon_write_failed')
