#! /usr/bin/env python3

from unittest import TestCase
import os.path
import imp
from contextlib import contextmanager
from tempfile import TemporaryDirectory
import sys
from io import BytesIO, TextIOWrapper, StringIO

class TestCli(TestCase):
    def setUp(self):
        path = os.path.join(os.path.dirname(__file__), "iview-cli")
        self.iview_cli = load_script(path, "iview-cli")
        self.iview_cli.set_proxy()
    
    def test_subtitles(self):
        class comm:
            def get_config():
                pass
            def get_captions(url):
                return "dummy captions"
        
        with substattr(self.iview_cli.iview, "comm", comm), \
        substattr(self.iview_cli, "stderr", StringIO()), \
        TemporaryDirectory(prefix="python-iview.") as dir:
            output = os.path.join(dir, "programme.srt")
            self.iview_cli.subtitles("programme.mp4", output)
            with substattr(sys, "stdout", TextIOWrapper(BytesIO())):
                self.iview_cli.subtitles("programme.mp4", "-")

class TestF4v(TestCase):
    def test_read_box(self):
        import iview.hds
        stream = BytesIO(bytes.fromhex("0000 000E") + b"mdat")
        self.assertEqual((b"mdat", 6), iview.hds.read_box_header(stream))
        stream = BytesIO(bytes.fromhex("0000 0001") + b"mdat" +
            bytes.fromhex("0000 0000 0000 0016"))
        self.assertEqual((b"mdat", 6), iview.hds.read_box_header(stream))
        self.assertEqual((None, None), iview.hds.read_box_header(BytesIO()))

class TestGui(TestCase):
    def setUp(self):
        path = os.path.join(os.path.dirname(__file__), "iview-gtk")
        self.iview_gtk = load_script(path, "iview-gtk")
    
    def test_livestream(self):
        """Item with "livestream" (r) key but no "url" (n) key"""
        class view:
            def get_model():
                return model
        class model:
            def iter_children(iter):
                return (None, None)
            def get_value(iter, index):
                return iter[index]
            def append(iter, item):
                pass
            def remove(iter):
                pass
        
        def series_api(key, value=""):
            json = b"""[{
                "a": "100",
                "b": "Dummy series",
                "f": [
                    {"b": "Relative URL programme", "r": "dummy.mp4"},
                    {
                        "b": "Absolute URL programme",
                        "r": "rtmp://host/live/stream-qual@999"
                    }
                ]
            }]"""
            return self.iview_gtk.iview.comm.parser.parse_series_api(json)
        
        with substattr(self.iview_gtk.iview.comm, series_api):
            iter = (None, dict(id="100"))
            self.iview_gtk.load_series_items(view, iter, None)

class TestParse(TestCase):
    def test_date(self):
        """Test various date formats that have been seen"""
        
        import iview.parser
        from datetime import datetime
        for (input, expected) in (
            ("2014-02-07 21:00:00", datetime(2014, 2, 7, 21)),  # Normal
            ("2014-02-13", datetime(2014, 2, 13)),  # News 24
            ("0000-00-00 00:00:00", None),  # QI series 6 episode 11
        ):
            self.assertEqual(expected, iview.parser.parse_date(input))

@contextmanager
def substattr(obj, attr, *value):
    if value:
        (value,) = value
    else:
        value = attr
        attr = attr.__name__
    
    orig = getattr(obj, attr)
    try:
        setattr(obj, attr, value)
        yield value
    finally:
        setattr(obj, attr, orig)

def load_script(path, name):
    with open(path, "rb") as file:
        return imp.load_module(name, file, path,
                ("", "rb", imp.PY_SOURCE))

if __name__ == "__main__":
    import unittest
    unittest.main()
