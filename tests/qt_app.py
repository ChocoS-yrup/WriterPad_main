"""Process-lifetime Qt application used by mixed core/widget unittest modules."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication


APP = QApplication.instance() or QApplication([])
