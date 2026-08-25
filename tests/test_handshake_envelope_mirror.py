"""The envelope mirror is a copy, and a copy that can be edited is not one.

`sync-contract/handshake-envelope.md` is held byte for byte identical in two
repositories. The rule that it may only be updated by fetching, never by
editing, lived as a sentence in the shared register, and a rule with nothing
enforcing it is the shape this whole review has been finding over and over.
This is the thing that enforces it.

It catches the half that is ours: somebody editing this copy. The other half --
the original moving and nobody mirroring it -- needs the network and a ref to
compare against, and the document does not sit on the source repository's
default branch yet. That check waits for it to land there.

To update the mirror, in one commit:

    gh api repos/ChocoS-yrup/Writerpad/contents/sync-contract/handshake-envelope.md?ref=<sha> \
        --jq .content | tr -d '\\n' | base64 -d > sync-contract/handshake-envelope.md

then put the new commit and blob id below. The blob id is what
`git hash-object` prints, and GitHub's contents API returns the same value as
`.sha`, so the two can be compared without cloning anything.
"""

import hashlib
import unittest
from pathlib import Path


MIRROR_PATH = "sync-contract/handshake-envelope.md"

# ChocoS-yrup/Writerpad, sync-contract/handshake-envelope.md
SOURCE_REPOSITORY = "ChocoS-yrup/Writerpad"
SOURCE_COMMIT = "ad4ae9b"
SOURCE_BLOB_ID = "cb39119a7a5087ebb9beab63d9c924a7f599fc8b"


def git_blob_id(raw: bytes) -> str:
    """The id git stores this content under, from the bytes on disk.

    Line endings are normalized first, and that is the whole subtlety. Git
    keeps a text file with LF and hands the working tree whatever the platform
    asked for; this repository is cloned on Windows with core.autocrlf on, so
    the same committed file reads back as CRLF there and LF elsewhere. Hashing
    what is on disk therefore answers a different question on each machine.

    The identity being compared is the stored content, so the presentation is
    undone before hashing. This was found by the check failing on CI while
    passing locally -- loudly, which is the direction a check should fail.
    """
    stored = raw.replace(b"\r\n", b"\n")
    header = b"blob %d\0" % len(stored)
    return hashlib.sha1(header + stored).hexdigest()


class HandshakeEnvelopeMirrorTests(unittest.TestCase):
    def _mirror(self) -> Path:
        # Deliberately not the contract_root() helper the other contract tests
        # use. That prefers WRITERPAD_SYNC_CONTRACT_DIR, which CI points at the
        # upstream checkout pinned to 7bcb5d25 -- a commit from before this
        # document existed. The mirror under test is this repository's own copy.
        return Path(__file__).resolve().parents[1] / MIRROR_PATH

    def test_the_mirror_is_present(self):
        self.assertTrue(
            self._mirror().is_file(),
            f"{MIRROR_PATH} is missing. It is a mirror of "
            f"{SOURCE_REPOSITORY} at {SOURCE_COMMIT}; fetch it rather than "
            f"writing one.",
        )

    def test_the_mirror_is_byte_identical_to_what_it_was_copied_from(self):
        raw = self._mirror().read_bytes()
        self.assertEqual(
            git_blob_id(raw),
            SOURCE_BLOB_ID,
            f"{MIRROR_PATH} no longer matches {SOURCE_REPOSITORY} at "
            f"{SOURCE_COMMIT}. If the original changed, fetch it again and "
            f"update SOURCE_COMMIT and SOURCE_BLOB_ID here in the same commit. "
            f"If this copy was edited directly, that is the thing a mirror "
            f"must not allow -- the change belongs in the original.",
        )

    def test_the_recorded_blob_id_is_a_blob_id(self):
        """A constant nobody can recompute is a constant nobody will update."""
        self.assertEqual(len(SOURCE_BLOB_ID), 40)
        self.assertTrue(
            all(character in "0123456789abcdef" for character in SOURCE_BLOB_ID)
        )
        # And the function that produces it agrees with git on a known case:
        # the empty blob is a fixed value every git implementation shares.
        self.assertEqual(
            git_blob_id(b""), "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
        )


if __name__ == "__main__":
    unittest.main()
