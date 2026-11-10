"""Configuration for the Kiwi Com Scraper. Everything can be set with an
environment variable; the defaults work out of the box.

    PORT            port the API listens on (default 8000)
    KIWI_COM_PROXY  proxy URL for every request, e.g. http://user:pass@host:port
                    (default: none — direct). Kiwi.com's flight-search API
                    answered home, office and cloud server IPs alike (25
                    requests in a burst from one IP, no rate limit), so you
                    very likely don't need this.

Everything else below is a plain constant with a working default — edit it
here if you need to.
"""
import os

PORT = int(os.environ.get("PORT", "8000"))

# Retry policy for transport errors and blocks (every request).
MAX_RETRIES = 3
RETRY_BACKOFF = 2          # seconds, multiplied by the attempt number

# The search options Kiwi.com's own website sends with every itinerary query:
# the partner tag the flight-search API requires, and the fare sources it asks for.
KIWI_COM_PARTNER = "skypicker"
KIWI_COM_CONTENT_PROVIDERS = ("KIWI", "FRESH", "KAYAK")

KIWI_COM_PROXY = os.environ.get("KIWI_COM_PROXY") or None


def kiwi_com_proxy():
    """The proxy for one curl session (None = direct)."""
    return os.environ.get("KIWI_COM_PROXY") or None
