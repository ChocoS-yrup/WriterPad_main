"""Run the released storage-name-v2 conformance vectors from the sync contract.

The vector file is the contract's own `conformance_vectors/storage-name-v2.json`
copied verbatim. iPad runs the same file, so a green result on both sides is the
evidence that the two clients produce identical collision keys.
"""

import json
import pathlib
import unittest

from storage_name_tables import (
    BASELINE_CANONICAL_SHA256,
    BASELINE_RANGE_COUNT,
    BASELINE_UNICODE_VERSION,
    EXCLUDED_CANONICAL_SHA256,
    EXCLUDED_RANGE_COUNT,
    is_assigned_baseline,
    is_excluded_scalar,
)
from sync_contract import SyncContractError, normalize_storage_name_v2


VECTOR_PATH = pathlib.Path(__file__).with_name("storage_name_v2_vectors.json")
VECTORS = json.loads(VECTOR_PATH.read_text(encoding="utf-8"))


class StorageNameV2VectorTestCase(unittest.TestCase):
    def test_vector_file_matches_the_released_contract(self):
        self.assertEqual(VECTORS["algorithm_id"], "storage-name-v2")
        self.assertEqual(VECTORS["contract_version"], "0.3.0")
        self.assertEqual(
            VECTORS["baseline_unicode_version"], BASELINE_UNICODE_VERSION
        )
        self.assertEqual(len(VECTORS["vectors"]), 29)
        identifiers = [vector["vector_id"] for vector in VECTORS["vectors"]]
        self.assertEqual(identifiers, sorted(identifiers))
        self.assertEqual(identifiers[0], "SN-001")
        self.assertEqual(identifiers[-1], "SN-029")

    def test_frozen_tables_carry_the_contract_digests(self):
        self.assertEqual(BASELINE_RANGE_COUNT, 698)
        self.assertEqual(EXCLUDED_RANGE_COUNT, 5)
        self.assertEqual(len(BASELINE_CANONICAL_SHA256), 64)
        self.assertEqual(len(EXCLUDED_CANONICAL_SHA256), 64)

    def test_every_conformance_vector(self):
        for vector in VECTORS["vectors"]:
            with self.subTest(vector=vector["vector_id"]):
                if vector["valid"]:
                    result = normalize_storage_name_v2(vector["input"])
                    self.assertEqual(result.normalized, vector["normalized"])
                    self.assertEqual(result.utf8_hex, vector["utf8_hex"])
                else:
                    with self.assertRaises(SyncContractError) as raised:
                        normalize_storage_name_v2(vector["input"])
                    self.assertEqual(raised.exception.code, vector["error_code"])

    def test_nfc_and_nfd_korean_names_share_one_collision_key(self):
        # The whole reason this algorithm exists: Windows stores 각 composed,
        # iOS decomposes it. Both must resolve to the same folder.
        composed = "각집"
        decomposed = "각집"
        self.assertNotEqual(composed, decomposed)
        self.assertEqual(
            normalize_storage_name_v2(composed).utf8,
            normalize_storage_name_v2(decomposed).utf8,
        )

    def test_variation_selector_is_not_an_excluded_scalar(self):
        # Excluding U+FE0F would reject ordinary emoji folder names because
        # IMEs insert it automatically for emoji presentation.
        self.assertFalse(is_excluded_scalar(0xFE0F))
        self.assertTrue(is_assigned_baseline(0xFE0F))

    def test_private_use_and_tag_scalars_are_rejected(self):
        for codepoint in (0xE000, 0xE0041, 0xE0100, 0xF0000):
            with self.subTest(codepoint=codepoint):
                with self.assertRaises(SyncContractError) as raised:
                    normalize_storage_name_v2(chr(codepoint))
                self.assertIn(
                    raised.exception.code,
                    {"STORAGE_NAME_UNSUPPORTED_SCALAR", "STORAGE_NAME_UNASSIGNED"},
                )

    def test_scalar_assigned_after_the_baseline_is_rejected(self):
        # U+1CCD6 is assigned in Unicode 16, not in the 14.0.0 baseline.
        with self.assertRaises(SyncContractError) as raised:
            normalize_storage_name_v2("\U0001CCD6")
        self.assertEqual(raised.exception.code, "STORAGE_NAME_UNASSIGNED")

    def test_supplementary_adjacency_is_rejected_before_normalization(self):
        for following in ("́", "ﾞ", "ﾟ"):
            with self.subTest(following=repr(following)):
                with self.assertRaises(SyncContractError) as raised:
                    normalize_storage_name_v2("\U00013046" + following)
                self.assertEqual(raised.exception.code, "STORAGE_NAME_INVALID")

    def test_supplementary_scalar_with_a_safe_neighbour_passes(self):
        self.assertTrue(normalize_storage_name_v2("\U00013046a").normalized)

    def test_separator_check_runs_after_normalization(self):
        # U+FF0F FULLWIDTH SOLIDUS normalizes into a real path separator, so a
        # pre-normalization check would let it through.
        with self.assertRaises(SyncContractError) as raised:
            normalize_storage_name_v2("a／b")
        self.assertEqual(raised.exception.code, "STORAGE_NAME_INVALID")

    def test_windows_device_basenames_stay_reserved(self):
        for name in ("CON", "con.txt", "LPT9", "NUL"):
            with self.subTest(name=name):
                with self.assertRaises(SyncContractError) as raised:
                    normalize_storage_name_v2(name)
                self.assertEqual(raised.exception.code, "STORAGE_NAME_RESERVED")

    def test_leading_space_is_preserved_and_trailing_is_removed(self):
        self.assertEqual(normalize_storage_name_v2(" name ").normalized, " name")


if __name__ == "__main__":
    unittest.main()
