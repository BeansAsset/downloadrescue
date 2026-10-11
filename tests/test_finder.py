"""Finding failed downloads in a Chrome-style History database."""

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gi  # noqa: E402

gi.require_version("GLib", "2.0")
from rescue import finder  # noqa: E402


class FinderTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        h = self.home.name
        self.downloads = os.path.join(h, "Downloads")
        os.makedirs(self.downloads)
        profile = os.path.join(h, ".config/BraveSoftware/Brave-Browser/Default")
        os.makedirs(profile)
        db = sqlite3.connect(os.path.join(profile, "History"))
        db.executescript("""
            CREATE TABLE downloads (id INTEGER, current_path TEXT, target_path TEXT, received_bytes INTEGER,
                total_bytes INTEGER, state INTEGER, etag TEXT, end_time INTEGER);
            CREATE TABLE downloads_slices (download_id INTEGER, offset INTEGER, received_bytes INTEGER);
            CREATE TABLE downloads_url_chains (id INTEGER, chain_index INTEGER, url TEXT);
        """)
        self.parallel = os.path.join(self.downloads, "Unconfirmed 123.crdownload")
        with open(self.parallel, "wb") as f:
            f.truncate(5000)
        self.plain = os.path.join(self.downloads, "linux.iso.crdownload")
        with open(self.plain, "wb") as f:
            f.write(b"x" * 3000)
        rows = [
            (1, self.parallel, os.path.join(self.downloads, "game.zip"), 2500, 9000, 4, '"e1"', 13370000000000000),
            (2, self.plain, os.path.join(self.downloads, "linux.iso"), 3000, 8000, 4, None, 13370000000000001),
            (3, os.path.join(self.downloads, "done.pdf"), os.path.join(self.downloads, "done.pdf"), 10, 10, 1, None, 0),
        ]
        db.executemany("INSERT INTO downloads VALUES (?,?,?,?,?,?,?,?)", rows)
        db.executemany("INSERT INTO downloads_slices VALUES (?,?,?)", [(1, 0, 1000), (1, 3000, 1500), (1, 4500, 0)])
        db.executemany("INSERT INTO downloads_url_chains VALUES (?,?,?)",
                       [(1, 0, "https://a.example/start"), (1, 1, "https://cdn.example/game.zip?sig=old")])
        db.commit()
        db.close()
        with open(os.path.join(self.downloads, "notes.txt.part"), "wb") as f:
            f.write(b"loose")
        self.patches = [mock.patch.object(finder, "HOME", h),
                        mock.patch.object(finder, "downloads_dir", lambda: self.downloads)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.home.cleanup()

    def test_finds_failed_downloads_with_what_the_browser_knows(self):
        found = {os.path.basename(f.path): f for f in finder.find_all()}
        self.assertEqual(set(found), {"Unconfirmed 123.crdownload", "linux.iso.crdownload", "notes.txt.part"})
        game = found["Unconfirmed 123.crdownload"]
        self.assertEqual(game.name, "game.zip")
        self.assertEqual(game.total, 9000)
        self.assertEqual(game.good, [(0, 1000), (3000, 4500)])     # the empty slice is left out
        self.assertEqual(game.old_url, "https://cdn.example/game.zip?sig=old")
        self.assertEqual(game.browser, "Brave")
        iso = found["linux.iso.crdownload"]
        self.assertEqual((iso.good, iso.total), ([(0, 3000)], 8000))
        loose = found["notes.txt.part"]
        self.assertEqual((loose.name, loose.total, loose.browser), ("notes.txt", None, None))


if __name__ == "__main__":
    unittest.main()
