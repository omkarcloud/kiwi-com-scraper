"""The one network helper the kiwi_com module needs: the Accept-Language its
sessions send. Requests go out direct from your own connection (or through
KIWI_COM_PROXY), so every session announces the plain English list a
desktop Chrome in the US sends. Place names follow the `locale` param, not
this header."""

ACCEPT_LANGUAGE = "en-US,en;q=0.9"


def site_accept_language(site=None):
    return ACCEPT_LANGUAGE


def accept_language(proxy=None, country=None):
    return ACCEPT_LANGUAGE
