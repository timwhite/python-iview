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
	return ''.join((
		'.'.join(url.split('.')[:-1]).split('/')[-1],
		'.flv',
	))

def rtmpdump(execvp=False, resume=False, quiet=False, live=False,
frontend=None, **kw):
	"""Wrapper around "rtmpdump" or "flvstreamer" command
	
	Accepts the following extra keyword arguments, which map to the
	corresponding "rtmpdump" options:
	
	rtmp, host, app, playpath, flv, resume, live"""
	
	executables = (
			'rtmpdump',
			'rtmpdump_x86',
			'flvstreamer',
			'flvstreamer_x86',
		)

	args = [
			None, # Name of executable; written to later.
			'--swfVfy', urljoin(config.base_url, config.swf_url),
		#	'-V', # verbose
		]
	
	for param in ("flv", "rtmp", "host", "app", "playpath"):
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
See the README file for more information about setting this up properly.""",
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
		encoding = getpreferredencoding()
		progress_pattern = re.compile(br'\d+\.\d%')
		size_pattern = re.compile(br'\d+\.\d+ kB', re.IGNORECASE)

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
			if progress_search is None and size_search is None:
				msg = 'Backend debug:\t' + r.decode(encoding)
				print(msg, file=sys.stderr)

		self.job.stderr.close()
		returncode = self.job.wait()

		if returncode == 0: # EXIT_SUCCESS
			self.frontend.done()
		else:
			print('Backend aborted with code %d (either it crashed, or you paused it)' % returncode, file=sys.stderr)
			if returncode == 1: # connection timeout results in code 1
				self.frontend.done(failed=True)
			else:
				self.frontend.done(stopped=True)

def fetch_program(url=None, *, item=None,
execvp=False, dest_file=None, quiet=False, frontend=None):
	if item is not None:
		url = item["url"]
	if dest_file is None:
		dest_file = get_filename(url)
	
	if item is not None and item["livestream"]:
		return iview.fetch.rtmpdump(
			rtmp=item["livestream"],
			flv=dest_file,
			live=True,
			execvp=execvp, quiet=quiet, frontend=frontend)

	auth = comm.get_auth()
	protocol = urlsplit(auth['server']).scheme
	if protocol in {'rtmp', 'rtmpt', 'rtmpe', 'rtmpte'}:
		method = fetch_rtmp
	else:
		method = fetch_hds
	return method(url, auth, execvp=execvp, dest_file=dest_file,
		quiet=quiet, frontend=frontend)

def fetch_rtmp(url, auth, dest_file, **kw):
	resume = dest_file != '-'
	if resume:
		# "rtmpdump" fails to resume an empty file
		try:
			if not os.path.getsize(dest_file):
				os.remove(dest_file)
		except EnvironmentError:
			# No problem if file did not exist, and if there is
			# some other error, let "rtmpdump" itself fail later
			# on
			pass

	ext = url.split('.')[-1]
	url = '.'.join(url.split('.')[:-1]) # strip the extension (.flv or .mp4)

	url = auth['playpath_prefix'] + url

	if ext == 'mp4':
		url = ''.join(('mp4:', url))

	return rtmpdump(
			host=auth['rtmp_host'],
			app=auth['rtmp_app'] + '?auth=' + auth['token'],
			playpath=url,
			flv=dest_file,
			resume=resume,
		**kw)

def fetch_hds(file, auth, dest_file, frontend, execvp, quiet, **kw):
	url = urljoin(auth['server'], auth['path'])
	if frontend is None:
		call = hds.fetch
	else:
		call = HdsThread
	return call(url, file, auth['tokenhd'], dest_file=dest_file,
		frontend=frontend,
		player=config.akamaihd_player, key=config.akamaihd_key, **kw)

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
			hds.fetch(*self.pos, frontend=self.frontend,
				abort=self.abort, **self.kw)
		except Exception:
			self.frontend.done(failed=True)
			raise
		except BaseException:
			self.frontend.done(stopped=True)
			raise
		else:
			self.frontend.done()
