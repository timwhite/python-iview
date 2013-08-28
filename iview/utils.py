import zlib
from io import BufferedIOBase
from urllib.parse import quote_plus
from io import SEEK_CUR

def xml_text_elements(parent, namespace=""):
	"""Extracts text from Element Tree into a dict()
	
	Each key is the tag name of a child of the given parent element, and
	the value is the text of that child. Only tags with no attributes are
	included. If the "namespace" parameter is given, it should specify an
	XML namespace enclosed in curly brackets {. . .}, and only tags in
	that namespace are included."""
	
	d = dict()
	for child in parent:
		if child.tag.startswith(namespace) and not child.keys():
			tag = child.tag[len(namespace):]
			d[tag] = child.text
	return d

def read_int(stream, size):
    bytes = stream.read(size)
    assert len(bytes) == size
    return int.from_bytes(bytes, "big")

def read_string(stream):
    buf = bytearray()
    while True:
        b = stream.read(1)
        assert b
        if not ord(b):
            return buf
        buf.extend(b)

value_unsafe = '%+&;#'
VALUE_SAFE = ''.join(chr(c) for c in range(33, 127)
    if chr(c) not in value_unsafe)
def urlencode_param(value):
    """Minimal URL encoding for query parameter"""
    return quote_plus(value, safe=VALUE_SAFE)

class CounterWriter(BufferedIOBase):
    def __init__(self, output):
        self.length = 0
        self.output = output
    def write(self, b):
        self.length += len(b)
        return self.output.write(b)
    def tell(self):
        return self.length

class ZlibDecompressorWriter(BufferedIOBase):
    def __init__(self, output, *pos, buffer_size=0x10000, **kw):
        self.output = output
        self.buffer_size = buffer_size
        self.decompressor = zlib.decompressobj(*pos, **kw)
    def write(self, b):
        while b:
            data = self.decompressor.decompress(b, self.buffer_size)
            self.output.write(data)
            b = self.decompressor.unconsumed_tail
    def close(self):
        self.decompressor.flush()

class TeeWriter(BufferedIOBase):
    def __init__(self, *outputs):
        self.outputs = outputs
    def write(self, b):
        for output in self.outputs:
            output.write(b)

def streamcopy(input, output, length):
    assert length >= 0
    while length:
        chunk = input.read(min(length, 0x10000))
        assert chunk
        output.write(chunk)
        length -= len(chunk)

def fastforward(stream, offset):
    assert offset >= 0
    if stream.seekable():
        stream.seek(offset, SEEK_CUR)
    else:
        while offset:
            chunk = stream.read(min(offset, 0x10000))
            assert chunk
            offset -= len(chunk)
