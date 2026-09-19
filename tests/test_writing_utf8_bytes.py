"""File bytes must match the text handed to the sync queue on Windows too."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from project_manager_writing import WritingProjectManager


class WritingUTF8BytesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.wpm = object.__new__(WritingProjectManager)
        self.wpm.writing_root_path = str(self.root)

    def test_lf_text_matches_queue_bytes(self):
        text = '일반 편집 검증\niPad와 Windows\n끝.\n'
        self.assertTrue(self.wpm.write_text_file('메인/원고/시험.txt', text))
        self.assertEqual((self.root / '메인/원고/시험.txt').read_bytes(), text.encode('utf-8'))

    def test_explicit_crlf_is_not_doubled(self):
        text = '보존할 본문\r\n둘째 줄\r\n'
        self.assertTrue(self.wpm.write_text_file('시험.txt', text))
        self.assertEqual((self.root / '시험.txt').read_bytes(), text.encode('utf-8'))

    def test_empty_and_unterminated_text_preserve_exact_bytes(self):
        for text in ('', '마지막 줄에 개행 없음'):
            with self.subTest(text=text):
                self.assertTrue(self.wpm.write_text_file('시험.txt', text))
                self.assertEqual((self.root / '시험.txt').read_bytes(), text.encode('utf-8'))

    def test_replace_failure_preserves_previous_body(self):
        path = self.root / '시험.txt'
        path.write_bytes('이전 본문\n'.encode('utf-8'))
        with patch('project_manager_writing.os.replace', side_effect=OSError('isolated replace failure')):
            self.assertFalse(self.wpm.write_text_file(path.name, '새 본문\n'))
        self.assertEqual(path.read_bytes(), '이전 본문\n'.encode('utf-8'))


if __name__ == '__main__':
    unittest.main()
