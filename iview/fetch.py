from . import config
from . import comm
import os
import subprocess
import threading
import re
from locale import getpreferredencoding
from . import hds
from urllib.parse import urlsplit, urljoin
import sys

def get_filename(url):
	return url.rsplit('/', 1)[-1].rsplit('.', 1)[0] + '.flv'

def rtmpdump(execvp=False, resume=False, quiet=False, live=False,
frontend=None, **kw):
	"""Wrapper around "rtmpdump" or "flvstreamer" command
	
	Accepts the following extra keyword arguments, which map to the
	corresponding "rtmpdump" options:
	
	rtmp, host, app, playpath, flv, swfVfy, resume, live"""
	
	executables = (
			'rtmpdump',
			'rtmpdump_x86',
			'flvstreamer',
			'flvstreamer_x86',
		)

	args = [
			None, # Name of executable; written to later.
		#	'-V', # verbose
		]
	
	for param in ("flv", "rtmp", "host", "app", "playpath", "swfVfy"):
		arg = kw.pop(param, None)
		if arg is None:
			continue
		args.extend(("--" + param, arg))

	if live:
		args.append("--live")
	
	if kw:
		raise TypeError("Invalid keyword arguments to rtmpdump()")

	# I added a 'quiet' option so that when run in batch mode, iview-cli can just emit nofications
	# for newly downloaded files.
	if quiet:
		args.append('-q')

	if config.socks_proxy_host is not None:
		args.append('--socks')
		args.append(config.socks_proxy_host + ':' + str(config.socks_proxy_port))

	if resume:
		args.append('--resume')
	
	if frontend:
		frontend.resumable = not live
	
	for exec_attempt in executables:
		args[0] = exec_attempt
		if not quiet:
			print('+', ' '.join(args), file=sys.stderr)
		try:
			if frontend:
				return RtmpWorker(args, frontend)
			elif execvp:
				os.execvp(args[0], args)
			else:
				subprocess.check_call(args)
		except OSError:
			print('Could not execute %s, trying another...' % exec_attempt, file=sys.stderr)
			continue

	print("""\
It looks like you don't have a compatible downloader backend installed.
See the README.md file for more information about setting this up properly.""",
		file=sys.stderr)
	return False

def readupto(fh, upto):
	"""	Reads up to (and not including) the character
		specified by arg 'upto'.
	"""
	result = bytearray()
	while True:
		char = fh.read(1)
		if not char or char == upto:
			return bytes(result)
		else:
			result.extend(char)

class RtmpWorker(threading.Thread):
	def __init__(self, args, frontend):
		threading.Thread.__init__(self)
		self.frontend = frontend
		self.job = subprocess.Popen(args, stderr=subprocess.PIPE)

	def terminate(self):
		try:
			self.job.terminate()
		except OSError: # this would trigger if it was
			pass        # already killed for some reason
	
	def run(self):
		with self.job:
			encoding = getpreferredencoding()
			progress_pattern = re.compile(br'\d+\.\d%')
			size_pattern = re.compile(br'\d+\.\d+ kB',
				re.IGNORECASE)

			while True:
				r = readupto(self.job.stderr, b'\r')
				if not r: # i.e. EOF, the process has quit
					break
				progress_search = progress_pattern.search(r)
				size_search = size_pattern.search(r)
				if progress_search is not None:
					p = float(progress_search.group()[:-1]) / 100. # [:-1] shaves the % off the end
					self.frontend.set_fraction(p)
				if size_search is not None:
					self.frontend.set_size(float(size_search.group()[:-3]) * 1024)
				if (progress_search is None and
				size_search is None):
					msg = 'Backend debug:\t'
					msg += r.decode(encoding)
					print(msg, file=sys.stderr)

		returncode = self.job.returncode
		if returncode == 0: # EXIT_SUCCESS
			self.frontend.done()
		else:
			print('Backend aborted with code %d (either it crashed, or you paused it)' % returncode, file=sys.stderr)
			if returncode == 1: # connection timeout results in code 1
				self.frontend.done(failed=True)
			else:
				self.frontend.done(stopped=True)

def fetch_program(url=None, *, item=dict(),
execvp=False, dest_file=None, quiet=False, frontend=None):
	if dest_file is None:
		dest_file = get_filename(item.get("url", url))
	
	fetcher = get_fetcher(url, item=item)
	return fetcher.fetch(execvp=execvp, dest_file=dest_file,
		quiet=quiet, frontend=frontend)

def get_fetcher(url=None, *, item=dict()):
	RTMP_PROTOCOLS = {'rtmp', 'rtmpt', 'rtmpe', 'rtmpte'}
	
	url = item.get("url", url)
	if urlsplit(url).scheme in RTMP_PROTOCOLS:
		return RtmpFetcher(url, live=True)
	
	auth = comm.get_auth()
	protocol = urlsplit(auth['server']).scheme
	if protocol in RTMP_PROTOCOLS:
		(url, ext) = url.rsplit('.', 1) # strip the extension (.flv or .mp4)
		url = auth['playpath_prefix'] + url

		if ext == 'mp4':
			url = 'mp4:' + url

		rtmp_url = auth['rtmp_url']
		token = auth.get('token')
		if token:
		    # Cannot use urljoin() because
		    # the RTMP scheme would have to be added to its whitelist
		    rtmp_url += '?auth=' + token
		
		return RtmpFetcher(rtmp_url, playpath=url)
	else:
		return HdsFetcher(url, auth)

class RtmpFetcher:
	def __init__(self, url, **params):
		params["rtmp"] = url
		params["swfVfy"] = urljoin(config.base_url, config.swf_url)
		self.params = params
	
	def fetch(self, *, dest_file, **kw):
		resume = (not self.params.get("live", False) and
			dest_file != '-')
		if resume:
			# "rtmpdump" can leave an empty file if it fails, and
			# then consistently fails to resume it
			try:
				if not os.path.getsize(dest_file):
					os.remove(dest_file)
			except EnvironmentError:
				# No problem if file did not exist, and if
				# there is some other error, let "rtmpdump"
				# itself fail later on
				pass
		kw.update(self.params)
		return rtmpdump(flv=dest_file, resume=resume, **kw)

class HdsFetcher:
	def __init__(self, file, auth):
		self.url = urljoin(auth['server'], auth['path'])
		self.file = file
		self.tokenhd = auth.get('tokenhd')
	
	def fetch(self, *, frontend, execvp, quiet, **kw):
		if frontend is None:
			call = hds_open_file
		else:
			call = HdsThread
		return call(self.url, self.file, self.tokenhd,
			frontend=frontend,
			player=config.akamaihd_player,
			key=config.akamaihd_key, **kw)

class HdsThread(threading.Thread):
	def __init__(self, *pos, frontend, **kw):
		threading.Thread.__init__(self)
		self.frontend = frontend
		self.pos = pos
		self.kw = kw
		self.abort = threading.Event()
	
	def terminate(self):
		self.abort.set()
	
	def run(self):
		try:
			hds_open_file(*self.pos, frontend=self.frontend,
				abort=self.abort, **self.kw)
		except Exception:
			self.frontend.done(failed=True)
			raise
		except BaseException:
			self.frontend.done(stopped=True)
			raise
		else:
			self.frontend.done()

def hds_open_file(*pos, dest_file, **kw):
	'''Handle special file name "-" representing "stdout"'''
	if dest_file == "-":
		return hds.fetch(*pos, dest_file=sys.stdout.buffer, **kw)
	else:
		with open(dest_file, "wb") as dest_file:
			return hds.fetch(*pos, dest_file=dest_file, **kw)
