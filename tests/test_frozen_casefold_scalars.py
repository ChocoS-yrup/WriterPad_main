"""The host Unicode version cannot reach storage-name-v2's case folding.

This replaces test_storage_name_unicode15_scalar_digest.py, which was retired
with storage-name-v1 in contract 0.3.0. That module imported unicodedata2 at
module scope and used the v1 implementation as its oracle, so it could survive
neither the 3.14 move nor the v2 switch.

What it was really protecting is kept here. v2 folds case from the vendored
Unicode 15.0.0 table in unicode15_casefold instead of the host database, so two
machines on different Python versions have to derive the same key for the same
name. The host still disagrees with the frozen table somewhere -- 0 scalars on
3.11 (stdlib Unicode 14.0.0), 27 on 3.14 (stdlib Unicode 16.0.0), and some other
count on whatever ships next.

Asserting the two simply agree would therefore be wrong, and would have passed on
3.11 only by accident. The invariant that actually holds on every version is that
each scalar they disagree about is already refused by the assigned-baseline gate,
which runs before folding. Divergence exists but is unreachable through the
contract.
"""

import unittest

from storage_name_tables import is_assigned_baseline, is_excluded_scalar
from sync_contract import SyncContractError, normalize_storage_name_v2
from unicode15_casefold import frozen_casefold


EXPECTED_SCALAR_COUNT = 0x110000 - 0x800


def unicode_scalars():
    for codepoint in range(0x110000):
        if 0xD800 <= codepoint <= 0xDFFF:
            continue
        yield codepoint, chr(codepoint)


def host_divergence():
    """Scalars the running Python folds differently from the frozen table."""
    return [
        codepoint
        for codepoint, character in unicode_scalars()
        if character.casefold() != frozen_casefold(character)
    ]


class FrozenCasefoldReachabilityTests(unittest.TestCase):
    def test_every_scalar_is_visited(self):
        self.assertEqual(sum(1 for _ in unicode_scalars()), EXPECTED_SCALAR_COUNT)

    def test_host_disagreement_is_outside_the_assigned_baseline(self):
        reachable = [
            f"U+{codepoint:04X}"
            for codepoint in host_divergence()
            if is_assigned_baseline(codepoint) and not is_excluded_scalar(codepoint)
        ]
        self.assertEqual(reachable, [], "host casefold divergence reachable through v2")

    def test_v2_refuses_every_scalar_the_host_disagrees_about(self):
        for codepoint in host_divergence():
            with self.subTest(scalar=f"U+{codepoint:04X}"):
                with self.assertRaises(SyncContractError) as raised:
                    normalize_storage_name_v2(chr(codepoint))
                self.assertIn(
                    raised.exception.code,
                    ("STORAGE_NAME_UNASSIGNED", "STORAGE_NAME_UNSUPPORTED_SCALAR"),
                )

    def test_the_frozen_table_folds_the_scalars_the_contract_does_admit(self):
        """A live sample, so the table cannot quietly become a no-op."""
        for source, expected in (("A", "a"), ("Ä", "ä"), ("ẞ", "ss"), ("Σ", "σ")):
            with self.subTest(source=source):
                self.assertEqual(frozen_casefold(source), expected)


if __name__ == "__main__":
    unittest.main()
