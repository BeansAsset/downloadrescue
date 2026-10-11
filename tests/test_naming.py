"""Naming the finished file when the browser only had a placeholder."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rescue.naming import name_for, sniff  # noqa: E402

ISO = bytes(32769) + b"CD001" + bytes(100)


class NamingTest(unittest.TestCase):
    def test_server_name_wins(self):
        self.assertEqual(name_for("report.pdf", "https://x.example/get?id=1", None, b"", "download"), "report.pdf")

    def test_name_in_the_link(self):
        self.assertEqual(name_for(None, "https://cdimage.example/ubuntu-26.04.1-desktop-arm64.iso?sig=abc",
                                  None, ISO, "download"), "ubuntu-26.04.1-desktop-arm64.iso")
        self.assertEqual(name_for(None, "https://x.example/My%20Photos.zip", None, b"", "download"), "My Photos.zip")

    def test_script_link_gets_the_type_from_the_contents(self):
        self.assertEqual(name_for(None, "https://x.example/download.php?id=7", None, ISO, "download"), "download.iso")
        self.assertEqual(name_for(None, "https://x.example/get.php?f=9", None, b"%PDF-1.7", "download"), "download.pdf")

    def test_or_from_what_the_server_says_it_is(self):
        self.assertEqual(name_for(None, "https://x.example/fetch.aspx", "application/pdf", b"???", "download"),
                         "fetch.pdf")

    def test_link_without_any_name(self):
        self.assertEqual(name_for(None, "https://x.example/", None, b"PK\x03\x04", "download"), "download.zip")

    def test_sniffing(self):
        self.assertEqual(sniff(b"\x89PNG\r\n\x1a\n...."), ".png")
        self.assertEqual(sniff(b"!<arch>\ndebian-binary   "), ".deb")
        self.assertEqual(sniff(b"\x7fELF\x02\x01\x01\x00AI\x02"), ".AppImage")
        self.assertIsNone(sniff(b"plain text"))


if __name__ == "__main__":
    unittest.main()
