import unittest

from sync_contract import _unicode15_module, normalize_storage_name
from unicode15_casefold import frozen_casefold


# Characterization only: storage-name-v1 currently checks separators before
# NFKC, so these generated separators are accepted. A future contract revision
# should reject them after normalization; this test must not implement that fix.
NFKC_GENERATED_SEPARATOR_VECTORS = (
    (0x2100, "a/c"),
    (0x2101, "a/s"),
    (0x2105, "c/o"),
    (0x2106, "c/u"),
    (0xFE68, "\\"),
    (0xFF0F, "/"),
    (0xFF3C, "\\"),
)


# Each input has ccc=0 but NFKC produces one or more combining characters.
# U+FF9E and U+FF9F are specifically reserved for a future iPad S8 exhaustive
# measurement. This Windows characterization adds no combining-mark rejection.
NFKC_GENERATED_COMBINING_VECTORS = (
    (0x0F73, (0x0F71, 0x0F72), (129, 130), "future-contract-candidate"),
    (0x0F75, (0x0F71, 0x0F74), (129, 132), "future-contract-candidate"),
    (0x0F81, (0x0F71, 0x0F80), (129, 130), "future-contract-candidate"),
    (0xFF9E, (0x3099,), (8,), "future-ipad-s8-measurement"),
    (0xFF9F, (0x309A,), (8,), "future-ipad-s8-measurement"),
)


# These BMP compatibility ideographs normalize to supplementary-plane scalars.
# Current analysis found no combining counterpart, so they are deliberately not
# candidates for a new rejection rule.
BMP_TO_SUPPLEMENTARY_NFKC_VECTORS = (
    (0xFA6C, 0x242EE),
    (0xFACF, 0x2284A),
    (0xFAD0, 0x22844),
    (0xFAD1, 0x233D5),
    (0xFAD5, 0x25249),
    (0xFAD6, 0x25CD0),
    (0xFAD7, 0x27ED3),
)


CROSS_PLATFORM_CASEFOLD_VECTORS = (
    (0x13A0, (0x13A0,)),
    (0xAB70, (0x13A0,)),
    (0x13F8, (0x13F0,)),
    (0x1C80, (0x0432,)),
    (0x1C81, (0x0434,)),
    (0x1C88, (0xA64B,)),
)


def codepoints(value):
    return tuple(ord(character) for character in value)


class StorageNameUnicode15BoundaryCharacterizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.unicode_data = _unicode15_module()

    def test_current_contract_accepts_nfkc_generated_separators(self):
        """Characterize the current pre-NFKC separator-check ordering."""
        for source, expected in NFKC_GENERATED_SEPARATOR_VECTORS:
            with self.subTest(source=f"U+{source:04X}"):
                input_value = chr(source)
                nfkc = self.unicode_data.normalize("NFKC", input_value)
                normalized = normalize_storage_name(input_value).normalized
                self.assertNotIn(input_value, {"/", "\\"})
                self.assertEqual(nfkc, expected)
                self.assertEqual(normalized, expected)
                self.assertTrue("/" in normalized or "\\" in normalized)

    def test_current_contract_accepts_nfkc_generated_combining_marks(self):
        """Characterize ccc=0 inputs that become combining marks after NFKC."""
        for source, expected, expected_cccs, disposition in (
            NFKC_GENERATED_COMBINING_VECTORS
        ):
            with self.subTest(
                source=f"U+{source:04X}",
                disposition=disposition,
            ):
                input_value = chr(source)
                nfkc = self.unicode_data.normalize("NFKC", input_value)
                self.assertEqual(self.unicode_data.combining(input_value), 0)
                self.assertEqual(codepoints(nfkc), expected)
                self.assertEqual(
                    tuple(self.unicode_data.combining(char) for char in nfkc),
                    expected_cccs,
                )
                self.assertEqual(normalize_storage_name(input_value).normalized, nfkc)
                if source in {0xFF9E, 0xFF9F}:
                    self.assertEqual(disposition, "future-ipad-s8-measurement")

    def test_bmp_nfkc_to_supplementary_scalars_remains_accepted(self):
        """Pin accepted mappings that are not new rejection-rule candidates."""
        for source, expected in BMP_TO_SUPPLEMENTARY_NFKC_VECTORS:
            with self.subTest(source=f"U+{source:04X}"):
                input_value = chr(source)
                nfkc = self.unicode_data.normalize("NFKC", input_value)
                self.assertEqual(codepoints(nfkc), (expected,))
                self.assertGreater(expected, 0xFFFF)
                self.assertEqual(self.unicode_data.combining(nfkc), 0)
                self.assertEqual(normalize_storage_name(input_value).normalized, nfkc)

    def test_frozen_cross_platform_casefold_representatives(self):
        for source, expected in CROSS_PLATFORM_CASEFOLD_VECTORS:
            with self.subTest(source=f"U+{source:04X}"):
                input_value = chr(source)
                folded = frozen_casefold(input_value)
                self.assertEqual(codepoints(folded), expected)
                self.assertEqual(
                    codepoints(normalize_storage_name(input_value).normalized),
                    expected,
                )


if __name__ == "__main__":
    unittest.main()
