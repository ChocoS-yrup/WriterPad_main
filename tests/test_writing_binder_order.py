import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QTreeWidgetItem

from mode_writing import BinderTreeWidget
from writing_tree import WritingTreeMixin


class WritingBinderOrderTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tree = BinderTreeWidget()

    def tearDown(self):
        self.tree.close()

    @staticmethod
    def _root(name, path):
        item = QTreeWidgetItem([name])
        item.setData(0, Qt.ItemDataRole.UserRole, path)
        return item

    def _root_paths(self):
        return [
            self.tree.topLevelItem(index).data(0, Qt.ItemDataRole.UserRole)
            for index in range(self.tree.topLevelItemCount())
            if not self.tree.is_bottom_spacer(self.tree.topLevelItem(index))
        ]

    def test_existing_order_is_repaired_with_trash_at_bottom(self):
        trash = self._root("🗑️ 휴지통", "메인/휴지통")
        custom = self._root("충돌", "메인/충돌")
        self.tree.addTopLevelItems([trash, custom])
        self.tree.add_bottom_spacer()

        self.assertTrue(self.tree.ensure_trash_at_bottom())
        self.assertEqual(
            self._root_paths(),
            ["메인/충돌", "메인/휴지통"],
        )
        self.assertTrue(
            self.tree.is_bottom_spacer(
                self.tree.topLevelItem(self.tree.topLevelItemCount() - 1)
            )
        )

    def test_new_root_is_inserted_before_trash_and_spacer(self):
        manuscript = self._root("📚 원고", "메인/원고")
        trash = self._root("🗑️ 휴지통", "메인/휴지통")
        self.tree.addTopLevelItems([manuscript, trash])
        self.tree.add_bottom_spacer()

        custom = self._root("새 폴더", "메인/새 폴더")
        self.tree.insert_root_item(custom)

        self.assertEqual(
            self._root_paths(),
            ["메인/원고", "메인/새 폴더", "메인/휴지통"],
        )

    def test_saved_root_order_also_keeps_trash_last(self):
        trash = self._root("🗑️ 휴지통", "메인/휴지통")
        custom = self._root("충돌", "메인/충돌")
        self.tree.addTopLevelItems([trash, custom])
        self.tree.add_bottom_spacer()
        wpm = SimpleNamespace(
            project_settings={},
            save_settings=MagicMock(),
        )
        panel = SimpleNamespace(binder_tree=self.tree, wpm=wpm)

        WritingTreeMixin.save_tree_order(panel)

        self.assertEqual(
            wpm.project_settings["tree_order"]["<root>"],
            ["충돌", "휴지통"],
        )
        wpm.save_settings.assert_called_once_with()

    def test_materialized_empty_folders_reload_in_exact_mixed_binder_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            writing_root = Path(temp_dir, "집필모드")
            ordered_parent = Path(writing_root, "메인", "메모장", "정렬 테스트")
            ordered_parent.mkdir(parents=True)
            for folder_name in ("폴더 B", "빈 폴더", "폴더 A"):
                Path(ordered_parent, folder_name).mkdir()
            for document_name in ("문서 B.txt", "문서 A.txt"):
                Path(ordered_parent, document_name).write_text(
                    document_name, encoding="utf-8"
                )
            expected = [
                "폴더 B",
                "문서 B.txt",
                "빈 폴더",
                "문서 A.txt",
                "폴더 A",
            ]
            wpm = SimpleNamespace(
                writing_root_path=str(writing_root),
                project_settings={
                    "tree_order": {"메인/메모장/정렬 테스트": expected}
                },
                list_trash_items=lambda: [],
                save_settings=MagicMock(),
            )
            panel = WritingTreeMixin()
            panel.binder_tree = self.tree
            panel.wpm = wpm

            for _reload in range(2):
                panel.load_tree_data()
                memo_root = next(
                    self.tree.topLevelItem(index)
                    for index in range(self.tree.topLevelItemCount())
                    if self.tree.topLevelItem(index).data(
                        0, Qt.ItemDataRole.UserRole
                    ) == "메인/메모장"
                )
                ordered_item = next(
                    memo_root.child(index)
                    for index in range(memo_root.childCount())
                    if memo_root.child(index).data(
                        0, Qt.ItemDataRole.UserRole
                    ) == "메인/메모장/정렬 테스트"
                )
                panel.on_tree_item_expanded(ordered_item)
                actual = [
                    os.path.basename(
                        ordered_item.child(index).data(
                            0, Qt.ItemDataRole.UserRole
                        )
                    )
                    for index in range(ordered_item.childCount())
                ]
                self.assertEqual(actual, expected)
                self.assertTrue(
                    any(
                        ordered_item.child(index).data(
                            0, Qt.ItemDataRole.UserRole
                        ).endswith("/빈 폴더")
                        for index in range(ordered_item.childCount())
                    )
                )

    def test_snapshot_preserves_collapsed_volumes_and_discovers_new_chapters(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            writing_root = Path(temp_dir, "집필모드")
            first_names = [f"{number:03d}화.txt" for number in range(1, 4)]
            second_names = [f"{number:03d}화.txt" for number in range(4, 7)]
            third_names = [f"{number:03d}화.txt" for number in range(7, 10)]
            for volume, names in (
                ("1권", first_names),
                ("2권", second_names),
                ("3권", third_names),
            ):
                volume_path = Path(writing_root, "메인", "원고", volume)
                volume_path.mkdir(parents=True)
                for name in reversed(names):
                    Path(volume_path, name).write_text("", encoding="utf-8")

            saved_order = {
                "<root>": ["원고", "휴지통"],
                "메인/원고": ["1권", "2권"],
                "메인/원고/1권": first_names,
                "메인/원고/2권": second_names,
            }
            wpm = SimpleNamespace(
                writing_root_path=str(writing_root),
                project_settings={"tree_order": saved_order},
                list_trash_items=lambda: [],
                save_settings=MagicMock(),
            )
            panel = WritingTreeMixin()
            panel.binder_tree = self.tree
            panel.wpm = wpm
            panel.load_tree_data()

            snapshot = panel._current_tree_order_snapshot()

            self.assertEqual(snapshot["메인/원고/1권"], first_names)
            self.assertEqual(snapshot["메인/원고/2권"], second_names)
            self.assertEqual(snapshot["메인/원고/3권"], third_names)
            self.assertEqual(snapshot["메인/원고"], ["1권", "2권", "3권"])

            wpm.project_settings["tree_order"]["메인/원고/1권"] = [
                "003화.txt", "001화.txt", "002화.txt"
            ]
            panel.load_tree_data()
            manuscript_root = next(
                self.tree.topLevelItem(index)
                for index in range(self.tree.topLevelItemCount())
                if self.tree.topLevelItem(index).data(
                    0, Qt.ItemDataRole.UserRole
                ) == "메인/원고"
            )
            first_volume = manuscript_root.child(0)
            panel.on_tree_item_expanded(first_volume)
            self.assertEqual(
                [
                    os.path.basename(first_volume.child(index).data(
                        0, Qt.ItemDataRole.UserRole
                    ))
                    for index in range(first_volume.childCount())
                ],
                first_names,
            )

    def test_root_order_uses_logical_main_storage_names_for_custom_empty_folder(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            writing_root = Path(temp_dir, "집필모드")
            for relative_path in (
                "메인/원고",
                "메인/메모장",
                "메인/휴지통",
                "메인/루트 빈 폴더",
            ):
                Path(writing_root, relative_path).mkdir(parents=True, exist_ok=True)
            wpm = SimpleNamespace(
                writing_root_path=str(writing_root),
                project_settings={
                    "tree_order": {
                        "<root>": ["원고", "메모장", "루트 빈 폴더"]
                    }
                },
                list_trash_items=lambda: [],
                save_settings=MagicMock(),
            )
            panel = WritingTreeMixin()
            panel.binder_tree = self.tree
            panel.wpm = wpm

            panel.load_tree_data()

            custom_folder = next(
                self.tree.topLevelItem(index)
                for index in range(self.tree.topLevelItemCount())
                if self.tree.topLevelItem(index).data(
                    0, Qt.ItemDataRole.UserRole
                ) == "메인/루트 빈 폴더"
            )

            self.assertEqual(
                self._root_paths()[:3],
                ["메인/원고", "메인/메모장", "메인/루트 빈 폴더"],
            )
            self.assertEqual(self._root_paths()[-1], "메인/휴지통")
            self.assertIs(
                custom_folder.data(0, Qt.ItemDataRole.UserRole + 1), True
            )

    def test_ipad_story_plot_alias_keeps_exact_root_position(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            writing_root = Path(temp_dir, "집필모드")
            for relative_path in (
                "메인/원고",
                "메인/메모장",
                "메인/플롯",
                "메인/휴지통",
                "메인/윈_빈폴더",
            ):
                Path(writing_root, relative_path).mkdir(
                    parents=True, exist_ok=True
                )
            wpm = SimpleNamespace(
                writing_root_path=str(writing_root),
                project_settings={
                    "tree_order": {
                        "<root>": [
                            "원고",
                            "메모장",
                            "스토리 플롯",
                            "윈_빈폴더",
                            "휴지통",
                        ]
                    }
                },
                list_trash_items=lambda: [],
                save_settings=MagicMock(),
            )
            panel = WritingTreeMixin()
            panel.binder_tree = self.tree
            panel.wpm = wpm

            panel.load_tree_data()

            root_paths = self._root_paths()
            plot_index = root_paths.index("메인/플롯")
            custom_index = root_paths.index("메인/윈_빈폴더")
            self.assertEqual(plot_index + 1, custom_index)
            self.assertEqual(root_paths[-1], "메인/휴지통")

    def test_all_story_plot_aliases_share_one_root_order_key(self):
        aliases = (
            "플롯",
            "스토리 플롯",
            "🗺️ 스토리 플롯",
            "🗺️ 메인 스토리 틀",
        )

        for alias in aliases:
            with self.subTest(alias=alias):
                self.assertEqual(
                    WritingTreeMixin._normalize_fixed_root_order([alias]),
                    ["플롯"],
                )

        self.assertEqual(
            WritingTreeMixin._normalize_fixed_root_order(list(aliases)),
            ["플롯"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
