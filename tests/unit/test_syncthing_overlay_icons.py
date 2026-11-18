from pathlib import Path
import subprocess
import sys

from PySide6.QtGui import QImage


def test_status_badges_leave_original_icon_visible(tmp_path):
    subprocess.run([sys.executable, 'scripts/make_overlay_icons.py', str(tmp_path)], check=True)
    for color in ('blue', 'green', 'red'):
        image = QImage(str(tmp_path / (color + '.ico')))
        assert not image.isNull()
        pixels = [(x, y) for y in range(image.height()) for x in range(image.width())
                  if image.pixelColor(x, y).alpha()]
        assert pixels
        assert max(x for x, y in pixels) < 15
        assert min(y for x, y in pixels) >= 18
        assert max(y for x, y in pixels) < 32
