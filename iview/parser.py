from __future__ import unicode_literals, print_function

from . import config
from xml.etree.cElementTree import XML
import json
import sys
from datetime import datetime

try:  # Python < 3
	from urlparse import urlsplit
except ImportError:  # Python 3
	from urllib.parse import urlsplit

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
	categories_url = params['categories']
	highlights_url = params['highlights']
	rtmp_chunks = rtmp_url.split('/')

	return {
		'rtmp_url'  : rtmp_url,
		'rtmp_host' : rtmp_chunks[2],
		'rtmp_app'  : rtmp_chunks[3],
		'auth_url'  : params['auth'],
		'api_url' : params['api'],
		'categories_url' : categories_url,
		'highlights_url' : highlights_url,
		'captions_url' : params['captions'],
	}

def parse_auth(soup, iview_config):
	"""	There are lots of goodies in the auth handshake we get back,
		but the only ones we are interested in are the RTMP URL, the auth
		token, and whether the connection is unmetered.
	"""

	xml = XML(soup)
	xmlns = "http://www.abc.net.au/iView/Services/iViewHandshaker"

	default_host = config.override_host == 'default'
	if not default_host and config.override_host:
		rtmp_url = config.stream_servers[config.override_host]
		stream_host = config.override_host
	if not default_host and not config.override_host:
		# should look like "rtmp://203.18.195.10/ondemand"
		rtmp_url = xml.find('{%s}server' % (xmlns,)).text
		default_host = rtmp_url is None

		# at time of writing, either 'Akamai' (usually metered) or 'Hostworks' (usually unmetered)
		stream_host = xml.find('{%s}host' % (xmlns,)).text
	
	if not default_host and urlsplit(rtmp_url).scheme != 'rtmp':
		print(
			'{0}: Not an RTMP server\n'
			'Using fallback from config (possibly metered)'.
			format(rtmp_url), file=sys.stderr)
		default_host = True

	if default_host or stream_host == 'Akamai':
		playpath_prefix = config.akamai_playpath_prefix
	else:
		playpath_prefix = ''

	if default_host:
		# We are a bland generic ISP using Akamai, or we are iiNet.
		rtmp_url  = iview_config['rtmp_url']

	rtmp_chunks = rtmp_url.split('/')
	rtmp_host = rtmp_chunks[2]
	rtmp_app = rtmp_chunks[3]

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
	# casefold() is new in Python 3.3
	casefold = getattr(type(''), "casefold", type('').lower)
	index_json.sort(key=lambda series: casefold(series['b']))

	index_dict = []

	for series in index_json:
		# https://iviewdownloaders.wikia.com/wiki/ABC_iView_Downloaders_Wiki#Series_JSON_format
		result = api_attributes(series, (
			('id', 'a'),
			('title', 'b'),
			('description', 'c'),
			('thumb', 'd'),
			('keywords', 'e'),
			('category', 't'),
		))
		result['items'] = parse_series_items(series['f'])
		index_dict.append(result)

	return index_dict

def parse_categories(soup):
	categories_list = []

	"""
	<category id="pre-school" genre="true">
		<name>ABC 4 Kids</name>
	</category>
	"""

	xml = XML(soup)

	# Get all the top level categories
	for cat in xml.findall('category'):

		id = cat.get('id')

		item = {}
		item['keyword'] = id
		item['isGenre'] = cat.get("genre") == "true"
		item['name']    = cat.find('name').text;
		item['children'] = []

		if item['isGenre']:
			for subCategory in cat.findall("category"):
				tempSubCategory = dict()
				tempSubCategory['categoryID'] = subCategory.get("id")
				tempSubCategory['name'] = subCategory.find('name').text
				tempSubCategory['parent'] = item
				item['children'].append(tempSubCategory)
		
		categories_list.append(item);

	return categories_list

def parse_series_items(series_json):
	items = []

	for item in series_json:
		# https://iviewdownloaders.wikia.com/wiki/ABC_iView_Downloaders_Wiki#Series_JSON_format
		for optional_key in ('d', 'r', 's', 'l'):
			item.setdefault(optional_key, '')
		
		result = api_attributes(item, (
			('id', 'a'),
			('title', 'b'),
			('description', 'd'),
			('category', 'e'),
			('date', 'f'),  # Date added to Iview
			('expires', 'g'),
			('broadcast', 'h'),
			('size', 'i'),
			('duration', 'j'),
			('hyperlink', 'k'),
			('home', 'l'), # program website
			('url', 'n'),
			('rating', 'm'),
			('livestream', 'r'),
			('thumb', 's'),
			('series', 'u'),
			('episode', 'v'),
		))
		
		duration = result.get('duration')
		if duration:
			result['duration'] = int(duration)
		
		size = result.get('size')
		if size:
			result['size'] = float(size) * 1e6
		
		fmt = '%Y-%m-%d %H:%M:%S'
		for field in ('date', 'expires', 'broadcast'):
			date = result.get(field)
			if date:
				result[field] = datetime.strptime(date, fmt)
		
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

def parse_highlights(xml):

	soup = XML(xml)

	highlightList = []

	for series in soup.findall('series'):
		tempSeries = dict()
		tempSeries['title'] = series.find('title').text
		tempSeries['thumbURL'] = series.find('thumb').text
		tempSeries['keywords'] = series.findall('keywords')
		tempSeries['seriesID'] = series.get('id')

		highlightList.append(tempSeries)

	return highlightList

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
