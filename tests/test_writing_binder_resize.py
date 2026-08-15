import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from mode_writing import WritingModeWidget


class WritingBinderResizeTestCase(unittest.TestCase):
    def test_resized_binder_width_is_saved(self):
        panel = SimpleNamespace(
            main_splitter=SimpleNamespace(sizes=lambda: [315, 885]),
            pm=SimpleNamespace(global_config={}, save_global_config=MagicMock()),
        )

        WritingModeWidget._save_binder_width(panel)

        self.assertEqual(panel.pm.global_config["writing_binder_width"], 315)
        panel.pm.save_global_config.assert_called_once_with()

    def test_saved_binder_width_never_drops_below_minimum(self):
        panel = SimpleNamespace(
            main_splitter=SimpleNamespace(sizes=lambda: [20, 1180]),
            pm=SimpleNamespace(global_config={}, save_global_config=MagicMock()),
        )

        WritingModeWidget._save_binder_width(panel)

        self.assertEqual(panel.pm.global_config["writing_binder_width"], 140)


class WritingSelectionCountTestCase(unittest.TestCase):
    def test_status_shows_selected_and_total_character_counts(self):
        status = WritingModeWidget._format_editor_statistics(
            "좌측", "한 줄\n두 번째 줄", 5
        )

        self.assertEqual(
            status,
            "[좌측] 공백 포함 10자 / 제외 6자 · 선택 : 5자",
        )

    def test_selection_status_is_hidden_when_nothing_is_selected(self):
        status = WritingModeWidget._format_editor_statistics(
            "우측", "선택하지 않은 문장", 0
        )

        self.assertEqual(status, "[우측] 공백 포함 10자 / 제외 8자")


if __name__ == "__main__":
    unittest.main(verbosity=2)
