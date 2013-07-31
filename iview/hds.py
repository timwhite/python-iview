from . import comm
import xml.etree.cElementTree as ElementTree
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
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
from io import BytesIO

def fetch(*pos, dest_file, frontend=None, abort=None, **kw):
    url = manifest_url(*pos, **kw)
    
    with PersistentConnectionHandler() as connection:
        session = urllib.request.build_opener(connection)
        
        with session.open(url) as response:
            manifest = ElementTree.parse(response)
        
        url = manifest.findtext(F4M_NAMESPACE + "baseURL", url)
        manifest.findtext(F4M_NAMESPACE + "duration")
        player = player_verification(manifest)
        
        # TODO: determine preferred bitrate, max bitrate, etc
        media = manifest.find(F4M_NAMESPACE + "media")  # Just pick the first one!
        
        href = media.get("href")
        if href is not None:
            href = urljoin(url, href)
            bitrate = media.get("bitrate")  # Save this in case the child manifest does not specify a bitrate
            raise NotImplementedError("/manifest/media/@href -> child manifest")
        bitrate = media.get("bitrate")  # Not necessarily specified
        
        bsid = media.get("bootstrapInfoId")
        for bootstrap in manifest.findall(F4M_NAMESPACE + "bootstrapInfo"):
            if bsid is None or bsid == bootstrap.get("id"):
                break
        else:
            msg = "/manifest/bootstrapInfoId[@id={!r}]".format(bsid)
            raise LookupError(msg)
        
        bsurl = bootstrap.get("bootstrapUrl")
        if bsurl is not None:
            bsurl = urljoin(urljoin(url, bsurl), "?" + player)
            msg = "manifest/bootstrapInfo/@bootstrapUrl"
            raise NotImplementedError(msg)
        else:
            bootstrap = bootstrap.text
            bootstrap = b64decode(bootstrap.encode("ascii"), validate=True)
            bootstrap = BytesIO(bootstrap)
        
        (type, _) = read_box_header(bootstrap)
        assert type == b"abst"
        streamcopy(bootstrap, nullwriter, 1 + 3 + 4)
        byte = read_int(bootstrap, 1)
        live = bool(byte & 0x20)
        if not byte & 0x10:
            # start fresh seg, frag tables?
            pass
        streamcopy(bootstrap, nullwriter, 4 + 8 + 8)
        read_string(bootstrap)
        count = read_int(bootstrap, 1)
        for _ in range(count):
            read_string(bootstrap)
        count = read_int(bootstrap, 1)
        for _ in range(count):
            read_string(bootstrap)
        read_string(bootstrap)
        read_string(bootstrap)
        
        assert read_int(bootstrap, 1) == 1
        (type, size) = read_box_header(bootstrap)
        assert type == b"asrt"
        seg_runs = list()
        streamcopy(bootstrap, nullwriter, 1 + 3)
        count = read_int(bootstrap, 1)
        size -= 1 + 3 + 1
        for _ in range(count):
            size -= len(read_string(bootstrap))
        count = read_int(bootstrap, 4)
        size -= 4
        for _ in range(count):
            run = dict()
            run["first"] = read_int(bootstrap, 4)
            run["frags"] = read_int(bootstrap, 4) & 0x7FFFFFFF
            size -= 8
            seg_runs.append(run)
        assert not size
        
        assert read_int(bootstrap, 1) == 1
        (type, size) = read_box_header(bootstrap)
        assert type == b"afrt"
        frag_runs = list()
        streamcopy(bootstrap, nullwriter, 1 + 3 + 4)
        count = read_int(bootstrap, 1)
        size -= 1 + 3 + 4 + 1
        for _ in range(count):
            size -= len(read_string(bootstrap))
        count = read_int(bootstrap, 4)
        size -= 4
        for _ in range(count):
            run = dict()
            run["first"] = read_int(bootstrap, 4)
            run["timestamp"] = read_int(bootstrap, 8)  # (ms?)
            run["duration"] = read_int(bootstrap, 4)
            size -= 16
            if not run["duration"]:
                run["discontinuity"] = read_int(bootstrap, 1)
                size -= 1
            frag_runs.append(run)
        assert not size
        
        last_frag_run = frag_runs[-1]
        if not last_frag_run.get("discontinuity", True):
            live = False
            last_frag_run = frag_runs[-2]
        
        frags = seg_runs[-1]["frags"]
        for (i, run) in enumerate(seg_runs[:-1]):
            frags += (seg_runs[i + 1]["first"] - run["first"]) * run["frags"]
        
        invalid_count = not frags
        if not invalid_count:
            frags += frag_runs[0]["first"] - 1
        frags = max(frags, last_frag_run["first"])
        
        if live:
            start_seg = seg_runs[-1]["first"]
        else:
            start_seg = seg_runs[0]["first"]
        
        if live and not invalid_count:
            start_frag = frags - 2
        else:
            start_frag = frag_runs[0]["first"] - 1
        start_frag = max(start_frag, 0)
        assert start_frag < frags
        
        media_url = media.get("url")
        metadata = media.findtext(F4M_NAMESPACE + "metadata")
        metadata = b64decode(metadata.encode("ascii"), validate=True)
        with open(dest_file, "wb") as flv:
            flv.write(bytes.fromhex("464C560105000000090000000012"))
            flv.write(len(metadata).to_bytes(3, "big"))
            flv.write(bytes(3 + 4))
            flv.write(metadata)
            flv.write(bytes.fromhex("00019209"))
            progress_update(frontend, flv, start_frag, start_frag, frags)
            
            for frag in range(start_frag, frags):
                frag += 1
                frag_url = "{}Seg{}-Frag{}?{}".format(
                    media_url, start_seg, frag, player)
                frag_url = urljoin(url, frag_url)
                response = session.open(frag_url)
                
                while True:
                    (boxtype, boxsize) = read_box_header(response)
                    if not boxtype:
                        break
                    
                    if boxtype == b"mdat":
                        if frag > 1:
                            for _ in range(2):
                                streamcopy(response, nullwriter, 1,
                                    abort=abort)
                                packetsize = read_int(response, 3)
                                packetsize += 11 + 4
                                streamcopy(response, nullwriter,
                                    packetsize - 4, abort=abort)
                                boxsize -= packetsize
                                assert boxsize >= 0
                        streamcopy(response, flv, boxsize, abort=abort)
                    else:
                        streamcopy(response, nullwriter, boxsize,
                            abort=abort)
                
                progress_update(frontend, flv, frag, start_frag, frags)
            if not frontend:
                print(file=stderr)

def progress_update(frontend, flv, frag, first, frags):
    size = flv.tell()
    if frontend:
        frontend.set_fraction((frag - first) / frags)
        frontend.set_size(size)
    else:
        stderr.write("\rFrag {}/{}; {:.1F} MB\r".format(
            frag, frags, size / 1e6))
        stderr.flush()

def manifest_url(url, file, hdnea):
    file += "/manifest.f4m?hdcore&hdnea=" + urlencode_param(hdnea)
    return urljoin(url, file)

def player_verification(manifest):
    (data, hdntl) = manifest.findtext(F4M_NAMESPACE + "pv-2.0").split(";")
    msg = "st=0~exp=9999999999~acl=*~data={}!{}".format(
        data, config.akamaihd_player)
    sig = hmac.new(config.akamaihd_key, msg.encode("ascii"), sha256)
    pvtoken = "{}~hmac={}".format(msg, sig.hexdigest())
    
    # The "hdntl" parameter must be passed either in the URL or as a cookie
    return "pvtoken={}&{}".format(
        urlencode_param(pvtoken), urlencode_param(hdntl))

F4M_NAMESPACE = "{http://ns.adobe.com/f4m/1.0}"

def read_box_header(stream):
    """Returns (type, size) tuple, or (None, None) at EOF"""
    boxsize = stream.read(4)
    if not boxsize:
        return (None, None)
    boxtype = stream.read(4)
    assert len(boxsize) == 4 and len(boxtype) == 4
    boxsize = int.from_bytes(boxsize, "big")
    if boxsize == 1:
        boxsize = read_int(response, 8)
        boxsize -= 16
    else:
        boxsize -= 8
    assert boxsize >= 0
    return (boxtype, boxsize)

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
            return self.open_existing(req, headers)
        except http.client.BadStatusLine as err:
            # If the server closed the connection before receiving our reply,
            # the "http.client" module raises an exception with the "line"
            # attribute set to repr("")!
            if err.line != repr(""):
                raise
        self.connection.close()
        return self.open_existing(req)
    
    def open_existing(self, req, headers):
        """Make a request using any existing connection"""
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
        
        print(counter.tell())
        print(swf_hash.hexdigest())
        print(b64encode(player.digest()).decode('ascii'))

class CounterWriter(BufferedIOBase):
    def __init__(self):
        self.length = 0
    def write(self, b):
        self.length += len(b)
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

class NullWriter(BufferedIOBase):
    def write(self, b):
        pass
nullwriter = NullWriter()

def streamcopy(input, output, length, abort=None):
    assert length >= 0
    while length:
        if abort and abort.is_set():
            raise SystemExit()
        chunk = input.read(min(length, 0x10000))
        assert chunk
        output.write(chunk)
        length -= len(chunk)
