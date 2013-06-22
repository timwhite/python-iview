#~ from __future__ import print_function

# TODO: fix imports &c for Python 2
from . import comm
from xml.etree.cElementTree import XML
from base64 import b64encode, b64decode
from urllib.request import urlopen
import hmac
from hashlib import sha256
from . import config
import zlib
from io import BufferedIOBase
from shutil import copyfileobj
import urllib.request
from http.client import HTTPConnection
from urllib.parse import quote_plus
import http.client
from sys import stderr

try:  # Python < 3
    from urlparse import urljoin, urlsplit
except ImportError:  # Python 3
    from urllib.parse import urljoin, urlsplit

def fetch(*pos, dest_file, **kw):
    url = manifest_url(*pos, **kw)
    print(url, file=stderr)
    
    with PersistentConnectionHandler() as connection:
        session = urllib.request.build_opener(connection)
        
        with session.open(url) as response:
            manifest = response.read()
        
        manifest = XML(manifest)
        # TODO: this should be determined from bootstrap info presumably
        (frags, _) = manifest.findtext(F4M_NAMESPACE + "duration").split(".")
        frags = (int(frags) + 2) // 3
        
        # TODO: determine preferred bitrate, max bitrate, etc
        media = manifest.find(F4M_NAMESPACE + "media")  # Just pick the first one!
        
        player = player_verification(manifest)
        print(player, file=stderr)
        media_url = media.get("url")
        metadata = media.findtext(F4M_NAMESPACE + "metadata")
        metadata = b64decode(metadata.encode("ascii"), validate=True)
        with open(dest_file, "wb") as flv:
            flv.write(bytes.fromhex("464C560105000000090000000012"))
            flv.write(len(metadata).to_bytes(3, "big"))
            flv.write(bytes(3 + 4))
            flv.write(metadata)
            flv.write(bytes.fromhex("00019209"))
            
            for frag in range(frags):
                frag += 1
                frag_url = "{}Seg1-Frag{}?{}".format(media_url, frag, player)
                frag_url = urljoin(url, frag_url)
                
                response = session.open(frag_url)
                
                while True:
                    boxsize = response.read(4)
                    if not boxsize:
                        break
                    boxtype = response.read(4)
                    assert len(boxsize) == 4 and len(boxtype) == 4
                    boxsize = int.from_bytes(boxsize, "big")
                    if boxsize == 1:
                        boxsize = response.read(8)
                        assert len(boxsize) == 8
                        boxsize = int.from_bytes(boxsize, "big")
                        boxsize -= 16
                    else:
                        boxsize -= 8
                    assert boxsize >= 0
                    
                    if boxtype == b"mdat":
                        if frag > 1:
                            for _ in range(2):
                                streamcopy(response, null_writer, 1)
                                packetsize = response.read(3)
                                assert len(packetsize) == 3
                                packetsize = int.from_bytes(packetsize, "big")
                                packetsize += 11 + 4
                                streamcopy(response, null_writer, packetsize - 4)
                                boxsize -= packetsize
                                assert boxsize >= 0
                        streamcopy(response, flv, boxsize)
                    else:
                        streamcopy(response, null_writer, boxsize)
                
                stderr.write("Frag {}/{} {:.1F} MB\r".format(frag, frags, flv.tell() / 1e6))
                stderr.flush()

def manifest_url(url, file, hdnea):
    file += "/manifest.f4m?hdcore&hdnea=" + urlencode_param(hdnea)
    return urljoin(url, file)

def player_verification(manifest):
    (pvtoken, hdntl) = manifest.findtext(F4M_NAMESPACE + "pv-2.0").split(";")
    pvtoken = "st=0~exp=9999999999~acl=*~data={}!{}".format(
        pvtoken, config.akamaihd_player)
    mac = hmac.new(config.akamaihd_key, pvtoken.encode("ascii"), sha256)
    pvtoken = "{}~hmac={}".format(pvtoken, mac.hexdigest())
    
    # The "hdntl" parameter must be passed either in the URL or as a cookie
    return "pvtoken={}&{}".format(
        urlencode_param(pvtoken), urlencode_param(hdntl))

F4M_NAMESPACE = "{http://ns.adobe.com/f4m/1.0}"

class PersistentConnectionHandler(urllib.request.BaseHandler):
    def __init__(self, *pos, **kw):
        self.type = None
        self.host = None
        self.pos = pos
        self.kw = kw
        self.connection = None
    
    def default_open(self, req):
        if req.type != "http":
            return None
        
        if req.type != self.type or req.host != self.host:
            if self.connection:
                self.connection.close()
            self.connection = HTTPConnection(req.host, *self.pos, **self.kw)
            self.type = req.type
            self.host = req.host
        
        headers = dict(req.header_items())
        try:
            return self.open1(req, headers)
        except http.client.BadStatusLine as err:
            # If the server closed the connection before receiving our reply,
            # the "http.client" module raises an exception with the "line"
            # attribute set to repr("")!
            if err.line != repr(""):
                raise
        self.connection.close()
        return self.open1(req)
    
    def open1(self, req, headers):
        self.connection.request(req.get_method(), req.selector, req.data,
            headers)
        response = self.connection.getresponse()
        
        # Odd impedance mismatch between "http.client" and "urllib.request"
        response.msg = response.reason
        
        return response
    
    def close(self):
        if self.connection:
            self.connection.close()
    
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        self.close()

value_unsafe = '%+&;#'
VALUE_SAFE = ''.join(chr(c) for c in range(33, 127)
    if chr(c) not in value_unsafe)
def urlencode_param(value):
    """Minimal URL encoding for query parameter"""
    return quote_plus(value, safe=VALUE_SAFE)

SWF_VERIFICATION_KEY = b"Genuine Adobe Flash Player 001"

def swf_hash(url):
    try:
        from types import SimpleNamespace
    except ImportError:
        from shorthand import SimpleNamespace
    
    with urlopen(url) as swf:
        assert swf.read(3) == b"CWS"
        
        counter = CounterWriter()
        swf_hash = hmac.new(SWF_VERIFICATION_KEY, digestmod=sha256)
        player = sha256()
        uncompressed = TeeWriter(
            counter,
            SimpleNamespace(write=swf_hash.update),
            SimpleNamespace(write=player.update),
        )
        
        uncompressed.write(b"FWS")
        uncompressed.write(swf.read(5))
        decompressor = ZlibDecompressorWriter(uncompressed)
        copyfileobj(swf, decompressor)
        decompressor.close()
        
        print(counter.length)
        print(swf_hash.hexdigest())
        print(b64encode(player.digest()).decode('ascii'))

class CounterWriter(BufferedIOBase):
    def __init__(self):
        self.length = 0
    def write(self, b):
        self.length += len(b)

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

class NullWriter(BufferedIOBase):
    def write(self, b):
        pass
null_writer = NullWriter()

def streamcopy(input, output, length):
    while length:
        chunk = input.read(min(length, 0x10000))
        assert chunk
        output.write(chunk)
        length -= len(chunk)
