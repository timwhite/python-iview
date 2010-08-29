import gtk
import comm
import config
from BeautifulSoup import BeautifulStoneSoup

def parse_config(soup):
	"""	There are lots of goodies in the config we get back from the ABC.
		In particular, it gives us the URLs of all the other XML data we
		need.
	"""

	soup = soup.replace('&amp;', '&#38;')

	xml = BeautifulStoneSoup(soup)

	# should look like "rtmp://cp53909.edgefcs.net/ondemand"
	rtmp_url = xml.find('param', attrs={'name':'server_streaming'}).get('value')
	categories_url = str(xml.find('param', attrs={'name':'categories'}).get('value'))
	highlights_url = str(xml.find('param', attrs={'name':'highlights'}).get('value'))
	categories_url = "http://www.abc.net.au/iview/" + categories_url
	print "CAtegories: " + categories_url
	rtmp_chunks = rtmp_url.split('/')

	return {
		'rtmp_url'  : rtmp_url,
		'rtmp_host' : rtmp_chunks[2],
		'rtmp_app'  : rtmp_chunks[3],
		'index_url' : xml.find('param', attrs={'name':'index'}).get('value'),
		'categories_url' : categories_url,
		'highlights_url' : highlights_url,
	}

def parse_auth(soup):
	"""	There are lots of goodies in the auth handshake we get back,
		but the only ones we are interested in are the RTMP URL, the auth
		token, and whether the connection is unmetered.
	"""

	xml = BeautifulStoneSoup(soup)

	# should look like "rtmp://203.18.195.10/ondemand"
	rtmp_url = xml.find('server').string

	playpath_prefix = ''

	if rtmp_url is not None:
		# Being directed to a custom streaming server (i.e. for unmetered services).
		# Currently this includes Hostworks for all unmetered ISPs except iiNet.

		rtmp_chunks = rtmp_url.split('/')
		rtmp_host = rtmp_chunks[2]
		rtmp_app = rtmp_chunks[3]
	else:
		# We are a bland generic ISP using Akamai, or we are iiNet.

		if not comm.iview_config:
			comm.get_config()

		playpath_prefix = config.akamai_playpath_prefix

		rtmp_url = comm.iview_config['rtmp_url']
		rtmp_host = comm.iview_config['rtmp_host']
		rtmp_app = comm.iview_config['rtmp_app']

	token = xml.find("token").string
	token = token.replace('&amp;', '&') # work around BeautifulSoup bug

	return {
		'rtmp_url'        : rtmp_url,
		'rtmp_host'       : rtmp_host,
		'rtmp_app'        : rtmp_app,
		'playpath_prefix' : playpath_prefix,
		'token'           : token,
		'free'            : (xml.find("free").string == "yes")
	}

def parse_index(soup, programme):
	"""	This function parses the index, which is an overall listing
		of all programs available in iView. The index is divided into
		'series' and 'items'. Series are things like 'beached az', while
		items are things like 'beached az Episode 8'.
	"""
	xml = BeautifulStoneSoup(soup)

	for series in xml.findAll('series'):
		series_iter = programme.append(None, [series.find('title').string, series.get('id'), None, None])
		programme.append(series_iter, ['Loading...', None, None, None])

def parse_series_items(series_iter, soup, programme):
	# HACK: replace <abc: with < because BeautifulSoup doesn't have
	# any (obvious) way to inspect inside namespaces.
	soup = soup \
		.replace('<abc:', '<') \
		.replace('</abc:', '</')

	# HACK: replace &amp; with &#38; because HTML entities aren't
	# valid in plain XML, but the ABC doesn't know that.
	soup = soup.replace('&amp;', '&#38;')

	series_xml = BeautifulStoneSoup(soup)

	for program in series_xml.findAll('item'):
		programme.append(series_iter, [
				program.find('title').string,
				None,
				program.find('videoasset').string,
				program.find('description').string,
			])

def parse_highlights(xml):

	soup = BeautifulStoneSoup(xml)

	highlightList = []

	for series in soup('series'):
                #print "found a series: " + str(series('title')[0].contents)
                #series_iter = programme.append(None, [series.find('title').string, series.get('id'), None, None])
                tempSeries = dict()
                tempSeries['title'] = str(strip_CDATA(series('title')[0].contents[0]))
                tempSeries['thumbURL'] = str(series('thumb')[0].contents[0])
		tempSeries['keywords'] = series('keywords')
		tempSeries['seriesID'] = str(series['id'])

		highlightList.append(tempSeries)

	return highlightList

def parse_categories(xml):
	# soup = BeautifulStoneSoup(xml)
	xml = xml.replace("\n", "")
	xml = xml.replace("\t", "")
	xml = xml.replace('\^M', "")
	xml = xml.replace("\^M", "")
	print "length of xml: " + str(len(xml))
	xml = xml.replace(xml[38], "")
	
	from xml.dom.minidom import parseString
	
	doc = parseString(xml)
	

	categories = {}
	subcategories = {}
	subIDs = []

	for category in doc.getElementsByTagName("category"):
		if (not category.getAttribute("id") == "test") and (not category.getAttribute("id") in subIDs):
			#print category.getAttribute("id")
			tempCategory = dict()
			tempCategory['categoryID'] = str(category.getAttribute("id"))
			tempCategory['isGenre'] = category.getAttribute("genre") == "true"
			tempCategory['name'] = category.firstChild.firstChild.nodeValue
			#tempCategory['series'] = []
			tempCategory['children'] = []

			if tempCategory['isGenre']:
				for subCategory in category.getElementsByTagName("category"):
					tempSubCategory = dict()
					tempSubCategory['categoryID'] = str(subCategory.getAttribute("id"))
					tempSubCategory['name'] = str(subCategory.firstChild.firstChild.nodeValue)
					tempSubCategory['parent'] = tempCategory
					tempCategory['children'].append(tempSubCategory)

					#print "\tFound a sub-category: " + tempSubCategory.name
					subIDs.append(subCategory.getAttribute("id"))
					subcategories[tempSubCategory['categoryID']] = tempSubCategory

			categories[tempCategory['categoryID']] = tempCategory
			
	return (categories, subcategories)

def strip_CDATA(string):
        ret = string.replace('<![CDATA[','')
        ret = ret.replace(']]>','')
 
        return ret
        
