import os
import tempfile
import unittest
from pathlib import Path

from project_paths import (
    INVALID_PROJECT_NAME,
    PROJECT_NAME_CONFLICT,
    LocalProjectPathError,
    normalize_local_entry_name,
    resolve_local_project_destination,
    validate_local_project_name,
)


class LocalProjectPathTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.workspace = Path(self.temp_dir.name, "작품목록")
        self.workspace.mkdir()

    def assert_invalid_name(self, name):
        with self.assertRaises(LocalProjectPathError) as caught:
            validate_local_project_name(name)
        self.assertEqual(caught.exception.code, INVALID_PROJECT_NAME)

    def test_valid_korean_project_name_resolves_inside_workspace(self):
        destination = resolve_local_project_destination(
            str(self.workspace), "새로운 서버 작품"
        )

        self.assertEqual(destination.project_name, "새로운 서버 작품")
        self.assertEqual(
            destination.project_path,
            str(Path(self.workspace, "새로운 서버 작품")),
        )
        self.assertEqual(
            destination.writing_root_path,
            str(Path(self.workspace, "새로운 서버 작품", "집필모드")),
        )
        self.assertFalse(Path(destination.project_path).exists())

    def test_traversal_separators_and_absolute_paths_are_rejected(self):
        invalid_names = (
            ".",
            "..",
            "../탈출",
            "..\\탈출",
            "폴더/작품",
            "폴더\\작품",
            str(Path(self.temp_dir.name, "절대경로")),
            "C:\\절대경로",
            "\\\\server\\share\\작품",
            "C:드라이브상대경로",
        )
        for name in invalid_names:
            with self.subTest(name=name):
                self.assert_invalid_name(name)

    def test_windows_reserved_names_are_rejected_case_insensitively(self):
        invalid_names = (
            "CON",
            "con",
            "PRN.txt",
            "AUX.작품",
            "NUL",
            "COM1",
            "com9.txt",
            "LPT1",
            "lpt9.원고",
            "CLOCK$",
        )
        for name in invalid_names:
            with self.subTest(name=name):
                self.assert_invalid_name(name)

    def test_control_characters_invalid_characters_and_unsafe_endings_are_rejected(self):
        invalid_names = (
            "작품\x00",
            "작품\n이름",
            "작품\x7f",
            "작품:이름",
            "작품?이름",
            "작품*이름",
            "작품 ",
            "작품.",
            " 앞공백",
        )
        for name in invalid_names:
            with self.subTest(name=repr(name)):
                self.assert_invalid_name(name)

    def test_local_entry_trims_only_trailing_whitespace_before_validation(self):
        self.assertEqual(normalize_local_entry_name("가 나 다   "), "가 나 다")
        with self.assertRaises(LocalProjectPathError):
            normalize_local_entry_name(" 앞공백")

    def test_local_entry_rejects_unsupported_and_overlong_names(self):
        for name in ("CON.txt", "금지:이름.txt", "이름?.txt", "가" * 256):
            with self.subTest(name=name):
                with self.assertRaises(LocalProjectPathError):
                    normalize_local_entry_name(name)

    def test_existing_folder_collision_preserves_all_files(self):
        existing = Path(self.workspace, "같은 작품")
        existing.mkdir()
        manuscript = Path(existing, "집필모드", "메인", "원고", "001화.txt")
        manuscript.parent.mkdir(parents=True)
        manuscript.write_text("절대로 바뀌면 안 되는 기존 원고", encoding="utf-8")

        with self.assertRaises(LocalProjectPathError) as caught:
            resolve_local_project_destination(str(self.workspace), "같은 작품")

        self.assertEqual(caught.exception.code, PROJECT_NAME_CONFLICT)
        self.assertEqual(
            manuscript.read_text(encoding="utf-8"),
            "절대로 바뀌면 안 되는 기존 원고",
        )

    def test_existing_file_collision_is_not_replaced_by_a_project_folder(self):
        existing = Path(self.workspace, "파일과 충돌")
        existing.write_text("기존 파일", encoding="utf-8")

        with self.assertRaises(LocalProjectPathError) as caught:
            resolve_local_project_destination(str(self.workspace), "파일과 충돌")

        self.assertEqual(caught.exception.code, PROJECT_NAME_CONFLICT)
        self.assertTrue(existing.is_file())
        self.assertEqual(existing.read_text(encoding="utf-8"), "기존 파일")

    def test_validation_never_creates_or_changes_workspace_entries(self):
        before = set(os.listdir(self.workspace))

        resolve_local_project_destination(str(self.workspace), "검증 전용 작품")

        self.assertEqual(set(os.listdir(self.workspace)), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
