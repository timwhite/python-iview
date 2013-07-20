import os

version     = '0.2'
api_version = 383

# os.uname() is not available on Windows, so we make this optional.
try:
	uname = os.uname()
	os_string = ' (%s %s %s)' % (uname[0], uname[2], uname[4])
except AttributeError:
	os_string = ' (non-Unix OS)'

user_agent = 'Python-iView %s%s' % (version, os_string)

config_url   = 'http://www.abc.net.au/iview/xml/config.xml?r=%d' % api_version

akamai_playpath_prefix = 'flash/playback/_definst_/'

# Used for "SWF verification", a stream obfuscation technique
swf_hash    = '96cc76f1d5385fb5cda6e2ce5c73323a399043d0bb6c687edd807e5c73c42b37'
swf_size    = '2122'

swf_url     = 'http://www.abc.net.au/iview/images/iview.jpg'

# AkamaiHD player verification key
akamaihd_key = bytes.fromhex(
	"bd938d5ee6d9f42016f9c56577b6fdcf415fe4b184932b785ab32bcadc9bb592")

# SHA-256 hash of uncompressed iview_<version>.swf file, base-64 encoded
akamaihd_player = "7ob1gDzeD6B33Q6WHsCoIlv6HQhCmcM4WGc36Y6bD+Q="

# Default configuration for SOCKS proxy.  If host is specified
# as 'None' then no proxy will be used.  The default port number
# will be used if only a host name is specified for the proxy.
socks_proxy_host = None
socks_proxy_port = 1080

# Name of streaming host to override.  If 'None', the host from the auth URL
# is not overridden.  Otherwise, this should be one of the keys in 'stream_
# hosts', or the special value 'default', which invokes a default server from
# the config URL, probably the same as 'Akamai'.
override_host = None

stream_hosts = {
	'Akamai': dict(
		server='rtmp://cp53909.edgefcs.net/ondemand',
		bwtest='rtmp://cp44823.edgefcs.net/ondemand',
		path=akamai_playpath_prefix,
	),
	'AkamaiHDUnmetered': dict(
		server='http://iviewum-vh.akamaihd.net/z/',
		bwtest='http://iviewum-vh.akamaihd.net/z/',
		path='playback/_definst_/',
	),
	'Hostworks': dict(
		server='rtmp://203.18.195.10/ondemand',
		bwtest='rtmp://203.18.195.10/live',
		path='',
	),
}
