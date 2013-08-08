import xml.etree.cElementTree as ElementTree
from base64 import b64encode, b64decode
from urllib.request import urlopen
import hmac
from hashlib import sha256
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
from .parser import xml_text_elements

def fetch(*pos, dest_file, frontend=None, abort=None, player=None, key=None,
**kw):
    url = manifest_url(*pos, **kw)
    
    with PersistentConnectionHandler() as connection:
        session = urllib.request.build_opener(connection)
        
        manifest = get_manifest(url, session)
        url = manifest["baseURL"]
        player = player_verification(manifest, player, key)
        
        # TODO: determine preferred bitrate, max bitrate, etc
        media = manifest["media"][0]  # Just pick the first one!
        href = media.get("href")
        if href is not None:
            href = urljoin(url, href)
            bitrate = media.get("bitrate")  # Save this in case the child manifest does not specify a bitrate
            raise NotImplementedError("/manifest/media/@href -> child manifest")
        
        bootstrap = media["bootstrapInfo"]
        bsurl = bootstrap.get("url")
        if bsurl is not None:
            bsurl = urljoin(url, bsurl)
            if player:
                bsurl = urljoin(bsurl, "?" + player)
            with session.open(bsurl) as response:
                bootstrap = response.read()
        else:
            bootstrap = BytesIO(bootstrap["data"])
        
        (type, _) = read_box_header(bootstrap)
        assert type == b"abst"
        
        # Version, flags, bootstrap info version
        streamcopy(bootstrap, nullwriter, 1 + 3 + 4)
        
        flags = read_int(bootstrap, 1)
        flags >> 6  # Profile
        bool(flags & 0x20)  # Live flag
        bool(flags & 0x10)  # Update flag
        
        # Time scale, media time at end of bootstrapped stream, SMPTE
        # timecode offset
        streamcopy(bootstrap, nullwriter, 4 + 8 + 8)
        
        movie_identifier = read_string(bootstrap).decode("utf-8")
        
        count = read_int(bootstrap, 1)  # Server table
        server_base_url = None
        for _ in range(count):
            entry = read_string(bootstrap)
            if server_base_url is None:
                server_base_url = entry.decode("utf-8")
        
        count = read_int(bootstrap, 1)  # Quality table
        highest_quality = None
        for _ in range(count):
            quality = read_string(bootstrap)
            if highest_quality is None:
                highest_quality = quality.decode("utf-8")
        
        read_string(bootstrap)  # DRM data
        read_string(bootstrap)  # Metadata
        
        # Read segment and fragment run tables. Read the first table of each
        # type that is understood, and skip any subsequent ones.
        count = read_int(bootstrap, 1)
        seg_runs = None
        for _ in range(count):
            if seg_runs is None:
                (qualities, runs) = read_asrt(bootstrap)
                if not qualities or highest_quality in qualities:
                    seg_runs = runs
            else:
                skip_box(bootstrap, abort=abort)
        if seg_runs is None:
            raise LookupError("Segment run table not found (quality = {!r})".
                format(highest_quality))
        
        count = read_int(bootstrap, 1)
        frag_runs = None
        for _ in range(count):
            if frag_runs is None:
                (qualities, runs) = read_afrt(bootstrap)
                if not qualities or highest_quality in qualities:
                    frag_runs = runs
            else:
                skip_box(bootstrap, abort=abort)
        if frag_runs is None:
            raise LookupError("Fragment run table not found (quality = {!r})"
                .format(highest_quality))
        
        last_frag_run = frag_runs[-1]
        if last_frag_run.get("discontinuity") == DISCONT_END:
            last_frag_run = frag_runs[-2]
        
        # Assume the last fragment run entry is for a single fragment
        frag_stop = last_frag_run["first"] + 1
        frag_start = frag_runs[0]["first"]
        
        media_url = media["url"] + movie_identifier + (highest_quality or "")
        if server_base_url is not None:
            media_url = urljoin(server_base_url, media_url)
        media_url = urljoin(url, media_url)
        
        metadata = media["metadata"]
        with open(dest_file, "wb") as flv:
            flv.write(b"FLV")  # Signature
            flv.write(bytes((1,)))  # File version
            
            # Assume audio and video tags will be present
            flv.write(bytes((True << 2 | True << 0,)))
            
            flv.write((9).to_bytes(4, "big"))  # Body offset
            flv.write((0).to_bytes(4, "big"))  # Previous tag size
            
            if metadata:
                flv.write(bytes((18,)))  # Script data tag
                flv.write(len(metadata).to_bytes(3, "big"))
                flv.write((0).to_bytes(3, "big"))  # Timestamp
                flv.write(bytes((0,)))  # Timestamp extension
                flv.write((0).to_bytes(3, "big"))  # Stream id
                flv.write(metadata)
                tagsize = 1 + 3 + 3 + 1 + 3 + len(metadata)
                flv.write(tagsize.to_bytes(4, "big"))
            
            progress_update(frontend, flv, frag_start, frag_start, frag_stop)
            
            segs = iter_segs(seg_runs)
            for frag in range(frag_start, frag_stop):
                seg = next(segs)
                frag_url = "{}Seg{}-Frag{}".format(media_url, seg, frag)
                if player:
                    frag_url = urljoin(frag_url, "?" + player)
                response = session.open(frag_url)
                
                while True:
                    (boxtype, boxsize) = read_box_header(response)
                    if not boxtype:
                        break
                    
                    if boxtype == b"mdat":
                        if frag > frag_start:
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
                
                progress_update(frontend, flv, frag, frag_start, frag_stop)
            if not frontend:
                print(file=stderr)

def iter_segs(seg_runs):
    # For each run of segments
    for (i, run) in enumerate(seg_runs):
        # For each segment in the run
        seg = run["first"]
        if i + 1 < len(seg_runs):
            end = seg_runs[i + 1]["first"]
        else:
            end = None
        while end is None or seg < end:
            # For each fragment in the segment
            for _ in range(run["frags"]):
                yield seg
            seg += 1

def progress_update(frontend, flv, frag, first, stop):
    frags = stop - first
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

def get_manifest(url, session):
    """Downloads the manifest specified by the URL and parses it
    
    Returns a dict() representing the top-level XML element names and text
    values of the manifest. Special items are:
    
    "baseURL": Defaults to the "url" parameter if the manifest is missing a
        <baseURL> element.
    "media": A sequence of dict() objects representing the attributes and
        text values of the <media> elements.
    
    A "media" dictionary contain the special key "bootstrapInfo", which holds
    a dict() object representing the attributes of the associated <bootstrap-
    Info> element. Each "bootstrapInfo" dictionary may be shared by more than
    one "media" item. A "bootstrapInfo" dictionary may contain the special
    key "data", which holds the associated bootstrap data."""
    
    with session.open(url) as response:
        manifest = ElementTree.parse(response).getroot()
    
    parsed = xml_text_elements(manifest, F4M_NAMESPACE)
    parsed.setdefault("baseURL", url)
    
    bootstraps = dict()
    for bootstrap in manifest.findall(F4M_NAMESPACE + "bootstrapInfo"):
        item = dict(bootstrap.items())
        
        bootstrap = bootstrap.text
        if bootstrap is not None:
            bootstrap = b64decode(bootstrap.encode("ascii"), validate=True)
            item["data"] = bootstrap
        
        bootstraps[item.get("id")] = item
    
    parsed["media"] = list()
    for media in manifest.findall(F4M_NAMESPACE + "media"):
        item = dict(media.items())
        item.update(xml_text_elements(media, F4M_NAMESPACE))
        item["bootstrapInfo"] = bootstraps[item.get("bootstrapInfoId")]
        metadata = item["metadata"].encode("ascii")
        item["metadata"] = b64decode(metadata, validate=True)
        parsed["media"].append(item)
    
    return parsed

F4M_NAMESPACE = "{http://ns.adobe.com/f4m/1.0}"

def read_asrt(bootstrap):
    (type, size) = read_box_header(bootstrap)
    if type != b"asrt":
        streamcopy(bootstrap, nullwriter, size)
        return ((), None)
    
    streamcopy(bootstrap, nullwriter, 1 + 3)  # Version, flags
    size -= 1 + 3
    
    qualities = set()
    count = read_int(bootstrap, 1)  # Quality segment URL modifier table
    size -= 1
    for _ in range(count):
        quality = read_string(bootstrap)
        size -= len(quality)
        qualities.add(quality.decode("utf-8"))
    
    seg_runs = list()
    count = read_int(bootstrap, 4)
    size -= 4
    for _ in range(count):
        run = dict()
        run["first"] = read_int(bootstrap, 4)  # First segment number in run
        run["frags"] = read_int(bootstrap, 4)  # Fragments per segment
        size -= 8
        seg_runs.append(run)
    assert not size
    return (qualities, seg_runs)

def read_afrt(bootstrap):
    (type, size) = read_box_header(bootstrap)
    if type != b"afrt":
        streamcopy(bootstrap, nullwriter, size)
        return ((), None)
    
    # Version, flags, time scale
    streamcopy(bootstrap, nullwriter, 1 + 3 + 4)
    size -= 1 + 3 + 4
    
    qualities = set()
    count = read_int(bootstrap, 1)  # Quality segment URL modifier table
    size -= 1
    for _ in range(count):
        quality = read_string(bootstrap)
        size -= len(quality)
        qualities.add(quality.decode("utf-8"))
    
    frag_runs = list()
    count = read_int(bootstrap, 4)
    size -= 4
    for _ in range(count):
        run = dict()
        run["first"] = read_int(bootstrap, 4)  # First fragment number in run
        run["timestamp"] = read_int(bootstrap, 8)  # Timestamp at start
        run["duration"] = read_int(bootstrap, 4)  # Duration of each fragment
        size -= 16
        if not run["duration"]:
            run["discontinuity"] = read_int(bootstrap, 1)
            size -= 1
        frag_runs.append(run)
    assert not size
    return (qualities, frag_runs)

# Discontinuity indicator values
DISCONT_END = 0
DISCONT_FRAG = 1
DISCONT_TIME = 2

def player_verification(manifest, player, key):
    pv = manifest.get("pv-2.0")
    if not pv:
        return
    (data, hdntl) = pv.split(";")
    msg = "st=0~exp=9999999999~acl=*~data={}!{}".format(data, player)
    sig = hmac.new(key, msg.encode("ascii"), sha256)
    pvtoken = "{}~hmac={}".format(msg, sig.hexdigest())
    
    # The "hdntl" parameter must be passed either in the URL or as a cookie
    return "pvtoken={}&{}".format(
        urlencode_param(pvtoken), urlencode_param(hdntl))

def skip_box(stream, abort=None):
    (_, size) = read_box_header(stream)
    streamcopy(stream, nullwriter, size, abort=abort)

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
