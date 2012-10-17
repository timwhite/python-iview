from __future__ import print_function

from . import config
from . import comm
import os
import subprocess

def get_filename(url):
	return ''.join((
		'.'.join(url.split('.')[:-1]).split('/')[-1],
		'.flv',
	))

def rtmpdump(execvp=False, quiet=False, **kw):
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
			'--swfhash',  config.swf_hash,
			'--swfsize',  config.swf_size,
			'--swfUrl',   config.swf_url,
		#	'-V', # verbose
		]
	
	for param in ("rtmp", "host", "app", "playpath", "flv"):
		try:
			arg = kw[param]
		except LookupError:
			continue
		args.extend(("--" + param, arg))

	for opt in ("resume", "live"):
		if kw.get(opt, False):
			args.append("--" + opt)

	# I added a 'quiet' option so that when run in batch mode, iview-cli can just emit nofications
	# for newly downloaded files.
	if quiet:
		args.append('-q')

	if config.socks_proxy_host is not None:
		args.append('--socks')
		args.append(config.socks_proxy_host + ':' + str(config.socks_proxy_port))

	for exec_attempt in executables:
		if not quiet:
			print('Starting %s...' % exec_attempt)
		args[0] = exec_attempt
		try:
			if execvp:
				os.execvp(args[0], args)
			else:
				return subprocess.Popen(args, stderr=subprocess.PIPE)
		except OSError:
			print('Could not load %s, trying another...' % exec_attempt)
			continue

	print("It looks like you don't have a compatible downloader backend installed.")
	print("See the README file for more information about setting this up properly.")
	return False

def fetch_program(url, execvp=False, dest_file=None, quiet=False):
	if dest_file is None:
		dest_file = get_filename(url)

	if dest_file is not '-':
		resume = os.path.isfile(dest_file)
	else:
		resume = False

	auth = comm.get_auth()

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
			execvp=execvp,
			quiet=quiet,
		)
