"""Rescues that must succeed, and rescues that must be refused without
changing a single byte of the partial file."""

import hashlib
import os
import random
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import server  # noqa: E402
from rescue import engine  # noqa: E402
from rescue.engine import Failed, Partial, Refused, Rescue  # noqa: E402

SIZE = 3 * 1024 * 1024 + 12345     # a bit over 3 MB, not a round number


def data(seed, size=SIZE):
    return random.Random(seed).randbytes(size)


def sha(b):
    return hashlib.sha256(b).hexdigest()


class RescueTest(unittest.TestCase):
    def setUp(self):
        self.b = server.Behaviour()
        self.httpd, self.base = server.start(self.b)
        self.dir = tempfile.TemporaryDirectory()
        self.file = data(1)
        self.b.files["/files/big.iso"] = self.file
        self.sleep = mock.patch("time.sleep", lambda s: None)
        self.sleep.start()

    def tearDown(self):
        self.sleep.stop()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.dir.cleanup()

    def partial(self, content, name="big.iso.crdownload", **kw):
        path = os.path.join(self.dir.name, name)
        with open(path, "wb") as f:
            f.write(content)
        return Partial(path=path, **kw)

    def run_rescue(self, p, path="/files/big.iso", **kw):
        return Rescue(p, self.base + path, **kw).run()

    def assert_untouched(self, p, before):
        with open(p.path, "rb") as f:
            self.assertEqual(f.read(), before, "the partial file was changed")
        self.assertFalse(os.path.exists(p.path + ".rescue"))
        self.assertFalse(os.path.exists(p.path + ".rescue-backup"))

    def read(self, path):
        with open(path, "rb") as f:
            return f.read()

    # -- it works ---------------------------------------------------------------------

    def test_finishes_a_download_from_a_new_link(self):
        p = self.partial(self.file[:2_000_000], total=SIZE)
        final, how = self.run_rescue(p)
        self.assertEqual(self.read(final), self.file)
        self.assertEqual(os.path.basename(final), "big.iso")
        self.assertFalse(os.path.exists(p.path))
        self.assertEqual(how, "overlap and spot checks")

    def test_uses_a_published_checksum(self):
        self.b.files["/files/SHA256SUMS"] = f"{sha(self.file)}  big.iso\n{sha(b'x')}  other.iso\n".encode()
        final, how = self.run_rescue(self.partial(self.file[:1_500_000], total=SIZE))
        self.assertEqual(how, "SHA-256 checksum")
        self.assertEqual(self.read(final), self.file)

    def test_size_unknown_locally_comes_from_the_server(self):
        final, _ = self.run_rescue(self.partial(self.file[:900_000]))
        self.assertEqual(self.read(final), self.file)

    def test_damaged_end_is_fetched_again(self):
        broken = self.file[:2_000_000 - 3000] + os.urandom(3000)   # an interrupted last write
        final, _ = self.run_rescue(self.partial(broken, total=SIZE))
        self.assertEqual(self.read(final), self.file)

    def test_parallel_download_with_empty_gaps(self):
        # A browser fetched three slices at once; between them the file is empty.
        path = os.path.join(self.dir.name, "big.iso.crdownload")
        slices = [(0, 700_000), (1_200_000, 1_800_000), (2_500_000, 2_600_000)]
        with open(path, "wb") as f:
            f.truncate(2_600_000)
            for s, e in slices:
                f.seek(s)
                f.write(self.file[s:e])
        p = Partial(path=path, total=SIZE, good=slices)
        final, _ = self.run_rescue(p)
        self.assertEqual(self.read(final), self.file)

    def test_server_without_ranges_reads_the_whole_file_and_compares(self):
        self.b.ranges = False
        final, _ = self.run_rescue(self.partial(self.file[:1_000_000], total=SIZE))
        self.assertEqual(self.read(final), self.file)

    def test_connection_drops_are_retried(self):
        self.b.drop_after = 400_000
        final, _ = self.run_rescue(self.partial(self.file[:1_000_000], total=SIZE))
        self.assertEqual(self.read(final), self.file)

    def test_an_interrupted_rescue_carries_on(self):
        p = self.partial(self.file[:1_000_000], total=SIZE)
        r = Rescue(p, self.base + "/files/big.iso")
        calls = {"n": 0}
        real = engine.remote.stream

        def stream_then_stop(*a, **k):
            for chunk in real(*a, **k):
                calls["n"] += 1
                if calls["n"] == 10:
                    r.cancelled = True
                yield chunk
        with mock.patch.object(engine.remote, "stream", stream_then_stop):
            with self.assertRaises(Failed):
                r.run()
        self.assertTrue(os.path.exists(p.path + ".rescue"))
        final, _ = Rescue(p, self.base + "/files/big.iso").run()
        self.assertEqual(self.read(final), self.file)

    def test_placeholder_name_is_replaced_by_the_links_file_name(self):
        p = self.partial(self.file[:1_000_000], name="Unconfirmed 375703.crdownload", total=SIZE)
        final, _ = self.run_rescue(p)
        self.assertEqual(os.path.basename(final), "big.iso")

    def test_never_replaces_an_existing_file(self):
        with open(os.path.join(self.dir.name, "big.iso"), "wb") as f:
            f.write(b"someone else's file")
        final, _ = self.run_rescue(self.partial(self.file[:1_000_000], total=SIZE))
        self.assertEqual(os.path.basename(final), "big (2).iso")
        self.assertEqual(self.read(os.path.join(self.dir.name, "big.iso")), b"someone else's file")

    # -- it refuses, and changes nothing ---------------------------------------------------

    def test_different_file_of_the_same_size_is_refused(self):
        self.b.files["/files/big.iso"] = data(2)
        before = self.file[:2_000_000]
        p = self.partial(before, total=SIZE)
        with self.assertRaisesRegex(Refused, "doesn't match|differs"):
            self.run_rescue(p)
        self.assert_untouched(p, before)

    def test_new_version_with_a_small_change_is_caught(self):
        changed = bytearray(self.file)
        changed[300_000:300_064] = os.urandom(64)          # one small edit early in the file
        self.b.files["/files/big.iso"] = bytes(changed)
        self.b.files["/files/SHA256SUMS"] = f"{sha(bytes(changed))}  big.iso\n".encode()
        before = self.file[:2_000_000]
        p = self.partial(before, total=SIZE)
        # Spot checks can miss a 64-byte change; the checksum can't.
        with self.assertRaises((Refused, Failed)):
            self.run_rescue(p)
        self.assert_untouched(p, before)

    def test_different_size_is_refused(self):
        self.b.files["/files/big.iso"] = self.file + b"more"
        before = self.file[:2_000_000]
        p = self.partial(before, total=SIZE)
        with self.assertRaisesRegex(Refused, "different file"):
            self.run_rescue(p)
        self.assert_untouched(p, before)

    def test_wrong_checksum_puts_everything_back(self):
        self.b.files["/files/big.iso.sha256"] = (sha(b"not this file") + "\n").encode()
        before = self.file[:2_000_000 - 3000] + os.urandom(3000)   # with a damaged end, to undo too
        p = self.partial(before, total=SIZE)
        with self.assertRaisesRegex(Failed, "put back"):
            self.run_rescue(p)
        self.assert_untouched(p, before)

    def test_server_that_lies_about_which_part_it_sent(self):
        self.b.lie_offset = 4096
        before = self.file[:2_000_000]
        p = self.partial(before, total=SIZE)
        with self.assertRaises(Refused):
            self.run_rescue(p)
        self.assert_untouched(p, before)

    def test_whole_file_mode_refuses_a_different_file(self):
        self.b.ranges = False
        self.b.files["/files/big.iso"] = data(3)
        before = self.file[:1_000_000]
        p = self.partial(before, total=SIZE)
        with self.assertRaises(Refused):
            self.run_rescue(p)
        self.assert_untouched(p, before)

    def test_dead_new_link(self):
        before = self.file[:1_000_000]
        p = self.partial(before, total=SIZE)
        with self.assertRaisesRegex(Refused, "didn't work"):
            self.run_rescue(p, path="/nothing-here")
        self.assert_untouched(p, before)


class ChecksumTest(unittest.TestCase):
    def test_formats(self):
        from rescue.checksum import digest_for
        d = "a" * 64
        self.assertEqual(digest_for(f"{d}  big.iso\n", "big.iso"), d)
        self.assertEqual(digest_for(f"{d} *big.iso\n", "big.iso"), d)
        self.assertEqual(digest_for(f"SHA256 (big.iso) = {d}\n", "big.iso"), d)
        self.assertEqual(digest_for(f"{d}\n", "big.iso"), d)
        self.assertIsNone(digest_for(f"{d}  other.iso\n", "big.iso"))
        self.assertIsNone(digest_for("b" * 64 + "  x\n" + d + "  y\n", "big.iso"))


if __name__ == "__main__":
    unittest.main()
