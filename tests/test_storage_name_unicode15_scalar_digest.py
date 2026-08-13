import hashlib
import sys
import unittest
import unicodedata

import unicodedata2

from sync_contract import (
    SyncContractError,
    _RESERVED_BASENAMES,
    _unicode15_module,
    normalize_storage_name,
)
from unicode15_casefold import frozen_casefold


EXPECTED_PRIMARY_SHA256 = (
    "b9368cb675858781cc42633c87b5501aa784b28a7aba858124aef5e2861dc1af"
)
EXPECTED_AUXILIARY_SHA256 = (
    "c07ed7a48a03f83688e8e05896fc3fee68d2554b992efe1af559448a986a1f27"
)
EXPECTED_SCALAR_COUNT = 0x110000 - 0x800
EXPECTED_REJECTED_COUNT = 59


def unicode_scalars():
    for codepoint in range(0x110000):
        if 0xD800 <= codepoint <= 0xDFFF:
            continue
        yield codepoint, chr(codepoint)


def runtime_casefold_storage_name(value, unicode_data):
    """Reproduce the pre-frozen-table storage-name implementation."""
    for character in value:
        codepoint = ord(character)
        if character in "/\\" or codepoint <= 31 or codepoint == 127:
            raise SyncContractError("STORAGE_NAME_INVALID")
    normalized = unicode_data.normalize("NFKC", value)
    normalized = normalized.casefold()
    normalized = unicode_data.normalize("NFKC", normalized).rstrip(" .")
    if normalized in {"", ".", ".."}:
        raise SyncContractError("STORAGE_NAME_INVALID")
    if normalized.split(".", 1)[0] in _RESERVED_BASENAMES:
        raise SyncContractError("STORAGE_NAME_RESERVED")
    return normalized


def outcome(callable_, value):
    try:
        return "success", callable_(value)
    except SyncContractError as error:
        return "error", error.code


def scalar_digest_snapshot():
    unicode_data = _unicode15_module()
    primary = hashlib.sha256()
    auxiliary = hashlib.sha256()
    reference_primary = hashlib.sha256()
    reference_auxiliary = hashlib.sha256()
    rejected = 0
    difference_count = 0
    first_difference = None
    scalar_count = 0

    for codepoint, character in unicode_scalars():
        scalar_count += 1
        reference_kind, reference_body = outcome(
            lambda value: runtime_casefold_storage_name(value, unicode_data),
            character,
        )
        current_kind, current_body = outcome(
            lambda value: normalize_storage_name(value).normalized,
            character,
        )
        if (reference_kind, reference_body) != (current_kind, current_body):
            difference_count += 1
            if first_difference is None:
                first_difference = {
                    "scalar": f"U+{codepoint:04X}",
                    "input": ascii(character),
                    "existing_result": (
                        reference_body if reference_kind == "success" else None
                    ),
                    "new_result": current_body if current_kind == "success" else None,
                    "existing_error": (
                        reference_body if reference_kind == "error" else None
                    ),
                    "new_error": current_body if current_kind == "error" else None,
                }

        prefix = f"{codepoint:04X}:"
        current_auxiliary_body = current_body if current_kind == "success" else ""
        reference_auxiliary_body = (
            reference_body if reference_kind == "success" else ""
        )
        primary.update(f"{prefix}{current_body}\n".encode("utf-8"))
        auxiliary.update(f"{prefix}{current_auxiliary_body}\n".encode("utf-8"))
        reference_primary.update(f"{prefix}{reference_body}\n".encode("utf-8"))
        reference_auxiliary.update(
            f"{prefix}{reference_auxiliary_body}\n".encode("utf-8")
        )
        if current_kind == "error":
            rejected += 1

    return {
        "scalar_count": scalar_count,
        "rejected_count": rejected,
        "difference_count": difference_count,
        "first_difference": first_difference,
        "primary_sha256": primary.hexdigest(),
        "auxiliary_sha256": auxiliary.hexdigest(),
        "reference_primary_sha256": reference_primary.hexdigest(),
        "reference_auxiliary_sha256": reference_auxiliary.hexdigest(),
    }


class StorageNameUnicode15ScalarDigestTests(unittest.TestCase):
    def test_frozen_casefold_matches_cpython_for_every_scalar(self):
        difference_count = 0
        first_difference = None
        for codepoint, character in unicode_scalars():
            existing = character.casefold()
            frozen = frozen_casefold(character)
            if existing != frozen:
                difference_count += 1
                if first_difference is None:
                    first_difference = {
                        "scalar": f"U+{codepoint:04X}",
                        "input": ascii(character),
                        "existing_result": existing,
                        "new_result": frozen,
                    }
        print(
            "casefold_scalar_difference_count="
            f"{difference_count} first_difference={first_difference}"
        )
        self.assertEqual(difference_count, 0, first_difference)

    def test_storage_name_scalar_digests(self):
        snapshot = scalar_digest_snapshot()
        print(
            f"python={sys.version.split()[0]} "
            f"stdlib_unicode={unicodedata.unidata_version} "
            f"unicodedata2={unicodedata2.unidata_version} "
            f"scalar_count={snapshot['scalar_count']} "
            f"rejected_count={snapshot['rejected_count']} "
            f"difference_count={snapshot['difference_count']} "
            f"primary={snapshot['primary_sha256']} "
            f"auxiliary={snapshot['auxiliary_sha256']} "
            f"first_difference={snapshot['first_difference']}"
        )
        self.assertEqual(snapshot["scalar_count"], EXPECTED_SCALAR_COUNT)
        self.assertEqual(snapshot["rejected_count"], EXPECTED_REJECTED_COUNT)
        self.assertEqual(
            snapshot["difference_count"],
            0,
            snapshot["first_difference"],
        )
        self.assertEqual(
            snapshot["primary_sha256"],
            snapshot["reference_primary_sha256"],
        )
        self.assertEqual(
            snapshot["auxiliary_sha256"],
            snapshot["reference_auxiliary_sha256"],
        )
        self.assertEqual(snapshot["primary_sha256"], EXPECTED_PRIMARY_SHA256)
        self.assertEqual(snapshot["auxiliary_sha256"], EXPECTED_AUXILIARY_SHA256)


if __name__ == "__main__":
    unittest.main()
