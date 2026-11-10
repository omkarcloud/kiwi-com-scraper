"""Cache TTL per /kiwi-com/* endpoint (cache.py, keyed on the validated
params). Kiwi's search engine re-prices itineraries continuously, so the
live-fare endpoints cache for minutes; the reference data (places, the
airline directory, nomad example trips) for days.
"""
from datetime import timedelta

# Live fares: a search result set (one-way / round-trip / multi-city legs)
# — the engine's own result cache is a few minutes, prices move hourly.
SEARCH_CACHE = timedelta(minutes=10)
# One re-priced itinerary looked up by share id: same horizon as a search.
DETAILS_CACHE = timedelta(minutes=10)
# Cheapest-per-city explore rows and the price calendars / graph / table /
# map are aggregate caches on Kiwi's side (priceCalendarCacheHit) and move
# a few times a day.
EXPLORE_CACHE = timedelta(hours=1)
PRICES_CACHE = timedelta(hours=1)
# A place resolved from a code / slug / term / id: ids never change.
PLACE_CACHE = timedelta(days=7)
# Autocomplete / radius / top-destination lists: the place graph is static,
# top destinations are recomputed from booking stats slowly.
PLACES_CACHE = timedelta(days=1)
# The /airlines directory (2,345 rows) moves when a carrier is added.
AIRLINES_CACHE = timedelta(days=7)
# Nomad example trips are editorial.
NOMAD_CACHE = timedelta(days=1)
