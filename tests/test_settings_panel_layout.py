import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QFrame

from settings_panel import SettingsPanel


class _ProjectManagerStub:
    def __init__(self):
        self.global_config = {}

    def get_project_setting(self, _key, default=None):
        return default

    def set_project_setting(self, _key, _value):
        pass

    def save_global_config(self):
        pass

    def get_aggregated_cost_history(self):
        return []


class SettingsPanelLayoutTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.auth_patch = patch(
            "sync_manager.SyncManager.authenticated_email",
            return_value="writer@example.com",
        )
        self.key_patch = patch(
            "security_manager.SecurityManager.get_api_key",
            return_value="",
        )
        self.auth_patch.start()
        self.key_patch.start()
        self.panel = SettingsPanel(_ProjectManagerStub())

    def tearDown(self):
        self.panel.close()
        self.key_patch.stop()
        self.auth_patch.stop()

    def test_cloud_account_has_its_own_tab(self):
        tab_names = [
            self.panel.main_tabs.tabText(index)
            for index in range(self.panel.main_tabs.count())
        ]
        self.assertEqual(
            tab_names,
            ["프로그램 설정", "클라우드 계정", "프롬프트 설정", "API · 비용"],
        )

        program_tab = self.panel.main_tabs.widget(0)
        cloud_tab = self.panel.main_tabs.widget(1)
        self.assertTrue(cloud_tab.isAncestorOf(self.panel.edit_supabase_email))
        self.assertTrue(cloud_tab.isAncestorOf(self.panel.edit_supabase_password))
        self.assertFalse(program_tab.isAncestorOf(self.panel.edit_supabase_email))

    def test_each_settings_area_uses_the_shared_card_style(self):
        for tab_index in range(self.panel.main_tabs.count()):
            cards = self.panel.main_tabs.widget(tab_index).findChildren(
                QFrame, "SettingsCard"
            )
            self.assertGreaterEqual(len(cards), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
