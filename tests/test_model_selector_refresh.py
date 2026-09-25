import os
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QApplication

import llm_provider
from model_selector import ModelSelector


class _Settings:
    def __init__(self):
        self.values = {}

    def get_project_setting(self, key, default=None):
        return self.values.get(key, default)

    def set_project_setting(self, key, value):
        self.values[key] = value


class ModelSelectorRefreshTestCase(unittest.TestCase):
    """두 번째 새로고침이 이미 지워진 작업 스레드를 건드려 앱이 강제 종료되던 문제."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _finish_refresh(self, selector):
        deadline = time.monotonic() + 10
        while not selector.btn_refresh_models.isEnabled() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        # 사용자가 다시 누르기 전에 이벤트 루프가 처리하는 finished 와 deleteLater 를 돌린다.
        for _ in range(100):
            self.app.processEvents()
            self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            if selector._model_worker is None:
                break
            time.sleep(0.01)

    def test_second_refresh_after_first_worker_is_deleted(self):
        selector = ModelSelector(_Settings())
        messages = []
        selector.refreshStateChanged.connect(messages.append)
        with patch.object(llm_provider, "discover_available_models", return_value=[]):
            selector.refresh_account_models()
            self._finish_refresh(selector)
            self.assertIsNone(selector._model_worker)

            # 고치기 전에는 여기서 RuntimeError(wrapped C/C++ object ... has been deleted)가
            # 났고, 버튼 클릭으로 불리면 PyQt 가 그 예외로 앱을 끝냈다.
            selector.refresh_account_models()
            self._finish_refresh(selector)

        self.assertIsNone(selector._model_worker)
        self.assertTrue(selector.btn_refresh_models.isEnabled())
        self.assertEqual(sum("찾지 못했습니다" in message for message in messages), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
