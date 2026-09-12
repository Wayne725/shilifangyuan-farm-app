from urllib.parse import urlparse


FANYU_TEST_BASE_URL = 'https://webtest.einvoice.com.tw/einv'
FANYU_PRODUCTION_BASE_URL = 'https://api01.einvoice.com.tw/einv'
FANYU_PRODUCTION_HOSTS = frozenset({'api01.einvoice.com.tw', 'web.einvoice.com.tw', 'web2.einvoice.com.tw'})
FANYU_HOSTS = FANYU_PRODUCTION_HOSTS | {'webtest.einvoice.com.tw'}


def is_fanyu_api_base_url(value: str, *, production_only: bool = False) -> bool:
    if any(character.isspace() for character in value):
        return False
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError:
        return False
    hosts = FANYU_PRODUCTION_HOSTS if production_only else FANYU_HOSTS
    return (
        parsed.scheme == 'https'
        and parsed.hostname in hosts
        and parsed.path in {'/einv', '/einv/'}
        and port in {None, 443}
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )
