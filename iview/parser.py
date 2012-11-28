from __future__ import unicode_literals

from . import config
from xml.etree.cElementTree import XML
import json

def parse_config(soup):
	"""	There are lots of goodies in the config we get back from the ABC.
		In particular, it gives us the URLs of all the other XML data we
		need.
	"""

	xml = XML(soup)
	params = dict()
	for param in xml.getiterator('param'):
		params.setdefault(param.get('name'), param.get('value'))

	# should look like "rtmp://cp53909.edgefcs.net/ondemand"
	# Looks like the ABC don't always include this field.
	# If not included, that's okay -- ABC usually gives us the server in the auth result as well.
	rtmp_url = params['server_streaming']
	rtmp_chunks = rtmp_url.split('/')

	return {
		'rtmp_url'  : rtmp_url,
		'rtmp_host' : rtmp_chunks[2],
		'rtmp_app'  : rtmp_chunks[3],
		'auth_url'  : params['auth'],
		'api_url' : params['api'],
		'categories_url' : params['categories'],
		'captions_url' : params['captions'],
	}

def parse_auth(soup, iview_config):
	"""	There are lots of goodies in the auth handshake we get back,
		but the only ones we are interested in are the RTMP URL, the auth
		token, and whether the connection is unmetered.
	"""

	xml = XML(soup)
	xmlns = "http://www.abc.net.au/iView/Services/iViewHandshaker"

	# should look like "rtmp://203.18.195.10/ondemand"
	rtmp_url = xml.find('{%s}server' % (xmlns,)).text

	# at time of writing, either 'Akamai' (usually metered) or 'Hostworks' (usually unmetered)
	stream_host = xml.find('{%s}host' % (xmlns,)).text

	if stream_host == 'Akamai':
		playpath_prefix = config.akamai_playpath_prefix
	else:
		playpath_prefix = ''

	if rtmp_url is not None:
		# Being directed to a custom streaming server (i.e. for unmetered services).
		# Currently this includes Hostworks for all unmetered ISPs except iiNet.

		rtmp_chunks = rtmp_url.split('/')
		rtmp_host = rtmp_chunks[2]
		rtmp_app = rtmp_chunks[3]
	else:
		# We are a bland generic ISP using Akamai, or we are iiNet.
		rtmp_url  = iview_config['rtmp_url']
		rtmp_host = iview_config['rtmp_host']
		rtmp_app  = iview_config['rtmp_app']

	token = xml.find("{%s}token" % (xmlns,)).text

	return {
		'rtmp_url'        : rtmp_url,
		'rtmp_host'       : rtmp_host,
		'rtmp_app'        : rtmp_app,
		'playpath_prefix' : playpath_prefix,
		'token'           : token,
		'free'            :
			(xml.find("{%s}free" % (xmlns,)).text == "yes")
	}

def parse_series_api(soup):
	"""	This function parses the index, which is an overall listing
		of all programs available in iView. The index is divided into
		'series' and 'items'. Series are things like 'beached az', while
		items are things like 'beached az Episode 8'.
	"""
	
	# TODO: Check charset from HTTP response or cache
	index_json = json.loads(soup.decode("UTF-8"))
	
	# alphabetically sort by title
	try:
		casefold = type('').casefold  # New in Python 3.3
	except AttributeError:
		casefold = type('').lower
	index_json.sort(key=lambda series: casefold(series['b']))

	index_dict = []

	for series in index_json:
		result = api_attributes(series, (
			('id', 'a'),
			('title', 'b'),
			('thumb', 'd'),
		))
		result['items'] = parse_series_items(series['f'])
		index_dict.append(result)

	return index_dict

def parse_series_items(series_json):
	items = []

	for item in series_json:
		for optional_key in ('d', 'r', 's', 'l'):
			item.setdefault(optional_key, '')
		
		result = api_attributes(item, (
			('id', 'a'),
			('title', 'b'),
			('description', 'd'),
			('url', 'n'),
			('livestream', 'r'),
			('thumb', 's'),
			('date', 'f'),
			('home', 'l'), # program website
		))
		items.append(result)

	return items

def api_attributes(input, attributes):
	result = dict()
	for (key, code) in attributes:
		value = input.get(code)
		# Some queries return a limited set of fields, for example
		# the thumbnail is missing from "seriesIndex"
		if value is not None:
			result[key] = value
	
	# HACK: replace &amp; with & because HTML entities don't make
	# the slightest bit of sense inside a JSON structure.
	for key in ('title', 'description'):
		value = result.get(key)
		if value is not None:
			result[key] = value.replace('&amp;', '&')
	
	return result

def parse_captions(soup):
	"""	Converts custom iView captions into SRT format, usable in most
		decent media players.
	"""
	xml = XML(soup)

	output = ''

	i = 1
	for title in xml.getiterator('title'):
		start = title.get('start')
		ids = start.rfind(':')
		end = title.get('end')
		ide = end.rfind(':')
		output = output + str(i) + '\n'
		output = output + start[:ids] + ',' + start[ids+1:] + ' --> ' + end[:ide] + ',' + end[ide+1:] + '\n'
		output = output + title.text.replace('|','\n') + '\n\n'
		i += 1

	return output
