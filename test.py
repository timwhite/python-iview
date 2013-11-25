#! /usr/bin/env python3

from unittest import TestCase
import os.path
import imp
from contextlib import contextmanager
from tempfile import TemporaryDirectory
import sys
from io import BytesIO, TextIOWrapper
import iview.hds

class TestCli(TestCase):
    def setUp(self):
        path = os.path.join(os.path.dirname(__file__), "iview-cli")
        with open(path, "rb") as file:
            self.iview_cli = imp.load_module("iview-cli", file, path,
                ("", "rb", imp.PY_SOURCE))
        self.iview_cli.set_proxy()
    
    def test_subtitles(self):
        class iview_comm:
            def get_config():
                pass
            def get_captions(url):
                return "dummy captions"
        
        with substattr(self.iview_cli.iview, "comm", iview_comm), \
        TemporaryDirectory(prefix="subtitles.") as dir:
            output = os.path.join(dir, "programme.srt")
            self.iview_cli.subtitles("programme.mp4", output)
            with substattr(sys, "stdout", TextIOWrapper(BytesIO())):
                self.iview_cli.subtitles("programme.mp4", "-")

class TestF4v(TestCase):
    def test_read_box(self):
        stream = BytesIO(bytes.fromhex("0000 000E") + b"mdat")
        self.assertEqual((b"mdat", 6), iview.hds.read_box_header(stream))
        stream = BytesIO(bytes.fromhex("0000 0001") + b"mdat" +
            bytes.fromhex("0000 0000 0000 0016"))
        self.assertEqual((b"mdat", 6), iview.hds.read_box_header(stream))
        self.assertEqual((None, None), iview.hds.read_box_header(BytesIO()))

@contextmanager
def substattr(obj, name, value):
    orig = getattr(obj, name)
    try:
        setattr(obj, name, value)
        yield value
    finally:
        setattr(obj, name, orig)

if __name__ == "__main__":
    import unittest
    unittest.main(buffer=True)
