"""Kiwi.com transport: plain curl_cffi with browser-impersonated TLS against
the site's own GraphQL gateway. No browser, no cookies, no token.

www.kiwi.com itself is behind Shape Security (F5) bot protection: a
"Client Challenge" shell on every path (robots.txt included, every curl
profile), so it is never fetched. All data lives on the "umbrella" gateway
the site's JavaScript calls,

    POST https://api.skypicker.com/umbrella/v2/graphql?featureName=<name>
    {"query": "...", "variables": {...}}

which answers without cookies or keys and allows full introspection
(research 2026-11-07). Egress: the pod's own IP (config.KIWI_COM_EXIT =
direct) — pod probe 2026-11-07 09:57 UTC, scrapers-patchright-depl-74fddd498d-rfmk6:
every surface 200 from the pod's own IP, 2/2 datacenter and 2/2 US
residential ports (table in config.py's kiwi com block). A retryable
failure drops the thread's session so the retry rides a fresh exit when the
knob points at a proxy tier.

Upstream surfaces (one GraphQL field each, plus one legacy REST list):
  * onewayItineraries / returnItineraries / multicityItineraries  -> Itineraries
    (search; `options.sharedItineraryId` adds metadata.sharedItinerary =
    the details lookup; `limit` caps at what the engine holds, no cursor)
  * onewayOnePerCityItineraries / returnOnePerCityItineraries     -> explore
  * places(search{term|ids|slugs|radius|topDestinations})          -> places
  * itineraryPricesCalendar / returnItineraryPricesCalendar /
    itineraryPriceGraph / itineraryPriceTable / itineraryPricesMap  -> prices
  * nomadExampleTrips                                              -> nomad
  * GET https://api.skypicker.com/airlines                         -> airlines

Failure taxonomy (scraper_errors, mapped to HTTP by route_glue):
  KiwiComUpstreamError  transport failure / 5xx / GraphQL errors   — retryable
  KiwiComBlocked        401 / 403 / 429 / challenge page           — retryable
  KiwiComBadRequest     upstream 400 / AppError (bad arguments)    — never retried
  KiwiComNotFound       unknown place / itinerary                  — never retried
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import egress
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError
from kiwi_com.refs import API, SITE

IMPERSONATE = "chrome"
GRAPHQL_URL = API + "/umbrella/v2/graphql"
AIRLINES_URL = API + "/airlines"
SEARCH_TIMEOUT = 60     # a full result set is up to ~800 KB and the engine takes 1-3 s
QUICK_TIMEOUT = 30

HEADERS = {
    "accept": "*/*",
    # accept-language lives on the session (_session), where it follows the
    # exit; a request-level copy here would override it
    "content-type": "application/json",
    "origin": SITE,
    "referer": SITE + "/",
}


class KiwiComUpstreamError(UpstreamError):
    """Transport failure, 5xx or a GraphQL-level error — retryable."""


class KiwiComBlocked(KiwiComUpstreamError, Blocked):
    """401 / 403 / 429 or a challenge page where JSON was expected — retryable."""


class KiwiComBadRequest(BadRequest):
    """Upstream 400 or an AppError (the engine rejected the arguments) — never retried."""


class KiwiComNotFound(NotFound):
    """Place / itinerary does not exist — never retried."""


# ---- sessions ------------------------------------------------------------------

_local = threading.local()


def _session():
    sess = getattr(_local, "session", None)
    if sess is None:
        from curl_cffi import requests as curl_requests
        sess = curl_requests.Session(impersonate=IMPERSONATE)
        proxy = config.kiwi_com_proxy()
        if proxy:
            sess.proxies = {"http": proxy, "https": proxy}
        sess.headers["accept-language"] = egress.accept_language(proxy)
        _local.session = sess
    return sess


def _drop_session():
    """Close the thread's session so a poisoned keep-alive connection (and
    its exit) dies; the next call opens a fresh one."""
    sess = getattr(_local, "session", None)
    _local.session = None
    if sess is not None:
        try:
            sess.close()
        except Exception:
            pass


def _retrying(fn):
    """Run fn() under the shared retry policy (KiwiComUpstreamError only;
    NotFound / BadRequest surface immediately)."""
    last = None
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            return fn()
        except KiwiComUpstreamError as e:
            last = e
            _drop_session()
            if attempt < config.MAX_RETRIES:
                time.sleep(config.RETRY_BACKOFF * attempt)
    raise last


def _classify(resp, label):
    if resp.status_code == 400:
        raise KiwiComBadRequest(resp.text[:300])
    if resp.status_code == 404:
        raise KiwiComNotFound(f"{label} not found")
    if resp.status_code in (401, 403, 429):
        raise KiwiComBlocked(f"HTTP {resp.status_code} for {label}")
    if resp.status_code != 200:
        raise KiwiComUpstreamError(f"HTTP {resp.status_code} for {label}")
    try:
        return resp.json()
    except Exception:
        raise KiwiComBlocked(f"non-JSON response for {label}: {resp.text[:120]}")


# ---- GraphQL -------------------------------------------------------------------

def gql(feature, field, query, variables, timeout=SEARCH_TIMEOUT):
    """POST one query; return data[field]. GraphQL `errors` are an
    UpstreamError (ours to fix or a transient engine fault) unless the
    message says the arguments were rejected; an AppError payload is a
    BadRequest carrying the engine's own message."""
    def once():
        try:
            resp = _session().post(f"{GRAPHQL_URL}?featureName={feature}",
                                   json={"query": query, "variables": variables},
                                   headers=HEADERS, timeout=timeout)
        except Exception as e:
            raise KiwiComUpstreamError(f"request failed: {type(e).__name__}: {e}")
        payload = _classify(resp, feature)
        errors = payload.get("errors") if isinstance(payload, dict) else None
        data = (payload.get("data") or {}).get(field) if isinstance(payload, dict) else None
        if errors and data is None:
            message = "; ".join(str(e.get("message") or e) for e in errors[:3])
            raise KiwiComUpstreamError(f"{feature}: {message[:300]}")
        if isinstance(data, dict) and data.get("__typename") == "AppError":
            raise KiwiComBadRequest(f"{data.get('message') or data.get('code') or 'AppError'}")
        return data
    return _retrying(once)


# -- itinerary selection (the lean production-style query; the schema-built
#    one is 600 KB and trips a non-null pageInfo under metadata.topResults)

STATION = "{ id legacyId name slug code type timezone gps { lat lng } city { id legacyId name slug code } country { id code name slug } }"
CARRIER = "{ id name slug code icao alliance website logo { url } }"
MONEY = "{ amount roundedAmount formattedValue currency { code } priceBeforeDiscount }"
SEGMENT = f"""{{ id code type duration cabinClass followingTechnicalStop
  source {{ station {STATION} localTime utcTime }}
  destination {{ station {STATION} localTime utcTime }}
  carrier {CARRIER} operatingCarrier {CARRIER}
  hiddenDestination {STATION} throwawayDestination {STATION} }}"""
SECTOR = f"""{{ id duration carriers {CARRIER}
  stopover {{ nightsCount duration isStationChange arrival {STATION} departure {STATION} }}
  sectorSegments {{ guarantee segment {SEGMENT} layover {{ id duration guarantee isStationChange isBaggageRecheck isWalkingDistance transferDuration transferType }} }} }}"""
TIER = "{ tierPrice { amount currency { code } } included bags { weight { value unit } dimensionsSum { value unit } height { value unit } length { value unit } width { value unit } } }"
BAGS = f"{{ hasBagsSupported hasNoCheckedBags includedHandBags includedCheckedBags includedPersonalItem checkedBagTiers {TIER} handBagTiers {TIER} personalItemTiers {TIER} }}"
BOOKING = f"""{{ edges {{ node {{ token bookingUrl price {MONEY} priceEur {{ amount }} usRulesApply
  itineraryProvider {{ id name code subprovider providerCategory contentProvider {{ code name }} hasHighProbabilityOfPriceChange }} }} }} }}"""
COMMON = f"""id shareId duration pnrCount technicalStops isTravelHack isRyanair isVanilla cabinClasses partnerships
  price {MONEY} priceEur {{ amount }} paidGuaranteePrice {MONEY}
  extendedFareOptionsPricing {{ standardFarePrice {MONEY} flexiFarePrice {MONEY} }}
  provider {{ id name code subprovider providerCategory contentProvider {{ code name }} hasHighProbabilityOfPriceChange }}
  bagsInfo {BAGS} travelHack {{ isTrueHiddenCity isVirtualInterlining isThrowawayTicket }}
  highlights {{ isBest isFastest isCheapest }}
  bookingOptions {BOOKING}"""
ITINERARY = f"""{{ __typename
  ... on ItineraryOneWay {{ legacyId lastAvailable {{ seatsLeft }} {COMMON} sector {SECTOR} }}
  ... on ItineraryReturn {{ legacyId lastAvailable {{ seatsLeft }} {COMMON} outbound {SECTOR} inbound {SECTOR} stopover {{ nightsCount duration }} }}
  ... on ItineraryMulticity {{ lastAvailable {{ seatsLeft }} {COMMON} sectors {SECTOR} stopovers {{ nightsCount duration }} }}
  ... on ItineraryNomad {{ {COMMON} sectors {SECTOR} stopovers {{ nightsCount duration }} }} }}"""
METADATA = f"""{{ itinerariesCount hasMorePending missingProviders {{ code }} transportTypes
  stopoverCountRange {{ start end }} stopoverDurationRange {{ start end }} hasOvernightStopover hasCheckedBaggage hasCabinBaggage
  prices {{ currency {{ code }} items {{ frequency label }} }} durations {{ frequency label }} stopoverTimes {{ frequency label }}
  outboundDepartureTimes {{ frequency label }} outboundArrivalTimes {{ frequency label }} inboundDepartureTimes {{ frequency label }} inboundArrivalTimes {{ frequency label }}
  carriers {CARRIER} stations {STATION} stopoverCountries {{ id code name }} outboundDays inboundDays
  sharedItinerary {ITINERARY} }}"""
ITINERARIES_RESULT = f"""{{ __typename ... on AppError {{ code message }}
  ... on Itineraries {{ server {{ requestId }} metadata {METADATA} itineraries {ITINERARY} }} }}"""

SEARCH_FIELDS = {
    "one_way": ("onewayItineraries", "SearchOnewayInput", "SearchOneWayItinerariesQuery"),
    "round_trip": ("returnItineraries", "SearchReturnInput", "SearchReturnItinerariesQuery"),
    "multi_city": ("multicityItineraries", "SearchMulticityInput", "SearchMultiCityItinerariesQuery"),
}


def _search_query(field, search_type):
    return (f"query {field}($search: {search_type}, $filter: ItinerariesFilterInput, "
            f"$options: ItinerariesOptionsInput) {{ {field}(search: $search, filter: $filter, "
            f"options: $options) {ITINERARIES_RESULT} }}")


def fetch_itineraries(trip, search, filt, options):
    """One itinerary search (trip = one_way | round_trip | multi_city) ->
    the raw Itineraries payload {server, metadata, itineraries}."""
    field, search_type, feature = SEARCH_FIELDS[trip]
    return gql(feature, field, _search_query(field, search_type),
               {"search": search, "filter": filt, "options": options})


# -- explore: cheapest itinerary per destination city

STOP = f"{{ station {STATION} localTime utcTime }}"
ONE_PER_CITY = f"""{{ __typename ... on AppError {{ code message }}
  ... on OnePerCityItineraries {{ metadata {{ itinerariesCount }} itineraries {{ __typename
    ... on OnewayOnePerCityItinerary {{ price {MONEY} priceEur {{ amount }} discountPercentage departureDate
        outboundDepartureDateRange {{ start end }} source {STOP} destination {STOP} }}
    ... on ReturnOnePerCityItinerary {{ price {MONEY} priceEur {{ amount }} discountPercentage departureDate returnDate nightsCount
        outboundDepartureDateRange {{ start end }} inboundDepartureDateRange {{ start end }} source {STOP} destination {STOP} }} }} }} }}"""
EXPLORE_FIELDS = {
    "one_way": ("onewayOnePerCityItineraries", "SearchOnewayInput", "SearchOneWayOnePerCityQuery"),
    "round_trip": ("returnOnePerCityItineraries", "SearchReturnInput", "SearchReturnOnePerCityQuery"),
}


def fetch_explore(trip, search, filt, options):
    field, search_type, feature = EXPLORE_FIELDS[trip]
    query = (f"query {field}($search: {search_type}, $filter: ItinerariesFilterInput, "
             f"$options: ItinerariesOptionsInput) {{ {field}(search: $search, filter: $filter, "
             f"options: $options) {ONE_PER_CITY} }}")
    return gql(feature, field, query, {"search": search, "filter": filt, "options": options})


# -- places

PLACE_CORE = "id legacyId name slug slugEn rank gps { lat lng }"
CITY_BRIEF = "{ id legacyId name slug code }"
COUNTRY_BRIEF = "{ id code name slug }"
PLACE = f"""{{ __typename
  ... on Station {{ {PLACE_CORE} code type timezone icao city {CITY_BRIEF} country {COUNTRY_BRIEF} }}
  ... on City {{ {PLACE_CORE} code timezone airportsCount groundStationsCount tags
      country {COUNTRY_BRIEF} subdivision {{ id name code }} region {{ id name slug }}
      stations(first: 30) {{ edges {{ node {{ id legacyId name slug code type timezone icao gps {{ lat lng }} }} }} }} }}
  ... on Country {{ {PLACE_CORE} code region {{ id name slug }} majorCity {CITY_BRIEF} }}
  ... on Region {{ {PLACE_CORE} continent {{ id name slug }} }}
  ... on Continent {{ {PLACE_CORE} }}
  ... on Subdivision {{ {PLACE_CORE} code country {COUNTRY_BRIEF} }}
  ... on AutonomousTerritory {{ {PLACE_CORE} country {COUNTRY_BRIEF} }}
  ... on POI {{ {PLACE_CORE} }}
  ... on TouristRegion {{ {PLACE_CORE} }} }}"""
PLACES_QUERY = f"""query places($search: PlacesSearchInput, $filter: PlacesFilterInput, $options: PlacesOptionsInput, $first: Int) {{
  places(search: $search, filter: $filter, options: $options, first: $first) {{ __typename
    ... on AppError {{ code message }}
    ... on PlaceConnection {{ pageInfo {{ hasNextPage }} edges {{ rank isAmbiguous
      distance {{ __typename ... on SphericalDistance {{ distance }} }} node {PLACE} }} }} }} }}"""


def fetch_places(search, filt=None, options=None, first=20):
    """places(...) -> the raw PlaceConnection {pageInfo, edges}."""
    return gql("PlacesQuery", "places", PLACES_QUERY,
               {"search": search, "filter": filt or {}, "options": options or {}, "first": first},
               timeout=QUICK_TIMEOUT)


# -- prices

PLACE_BRIEF = f"{{ __typename ... on City {{ id name slug code country {COUNTRY_BRIEF} }} ... on Station {{ id name slug code city {CITY_BRIEF} country {COUNTRY_BRIEF} }} ... on Country {{ id name slug code }} ... on Region {{ id name slug }} ... on Continent {{ id name slug }} }}"
LEGEND = f"{{ cheapThreshold {MONEY} averageThreshold {MONEY} expensiveThreshold {MONEY} }}"
PRICE_CALENDAR_QUERY = f"""query itineraryPricesCalendar($search: SearchPricesCalendarInput, $filter: ItinerariesFilterInput, $options: ItinerariesOptionsInput) {{
  itineraryPricesCalendar(search: $search, filter: $filter, options: $options) {{ __typename
    ... on AppError {{ code message }}
    ... on ItineraryPricesCalendar {{ sources {PLACE_BRIEF} destinations {PLACE_BRIEF} currency {{ code }}
      calendar {{ date ratedPrice {{ price {MONEY} rating }} }} priceRatingLegend {LEGEND} }} }} }}"""
RETURN_CALENDAR_QUERY = f"""query returnItineraryPricesCalendar($search: SearchReturnPricesCalendarInput, $filter: ItinerariesFilterInput, $options: ItinerariesOptionsInput) {{
  returnItineraryPricesCalendar(search: $search, filter: $filter, options: $options) {{ __typename
    ... on AppError {{ code message }}
    ... on ItineraryPricesCalendar {{ sources {PLACE_BRIEF} destinations {PLACE_BRIEF} currency {{ code }}
      calendar {{ date ratedPrice {{ price {MONEY} rating }} }} priceRatingLegend {LEGEND} }} }} }}"""
PRICE_GRAPH_QUERY = f"""query itineraryPriceGraph($search: SearchPriceGraphInput, $filter: ItinerariesFilterInput, $options: ItinerariesOptionsInput) {{
  itineraryPriceGraph(search: $search, filter: $filter, options: $options) {{ __typename
    ... on AppError {{ code message }}
    ... on ItineraryPriceGraph {{ sources {PLACE_BRIEF} destinations {PLACE_BRIEF} currency {{ code }}
      prices {{ date inboundDate price {MONEY} ratedPrice }} }} }} }}"""
PRICE_TABLE_QUERY = f"""query itineraryPriceTable($search: SearchReturnInput, $filter: ItinerariesFilterInput, $options: ItinerariesOptionsInput) {{
  itineraryPriceTable(search: $search, filter: $filter, options: $options) {{ __typename
    ... on AppError {{ code message }}
    ... on ItineraryPriceTable {{ priceList {{ outboundDate inboundDate price {MONEY} rating }} priceRatingLegend {LEGEND} }} }} }}"""
PRICE_MAP_QUERY = f"""query itineraryPricesMap($search: SearchPricesMapInput, $filter: ItinerariesFilterInput, $options: ItinerariesOptionsInput) {{
  itineraryPricesMap(search: $search, filter: $filter, options: $options) {{ __typename
    ... on AppError {{ code message }}
    ... on ItineraryPricesMap {{ sources {PLACE_BRIEF} currency {{ code }}
      map {{ rank price {MONEY} city {{ id legacyId name slug code gps {{ lat lng }} timezone airportsCount tags country {COUNTRY_BRIEF} region {{ id name slug }} }} }} }} }} }}"""


def _price_call(feature, field, query, search, filt, options):
    return gql(feature, field, query, {"search": search, "filter": filt, "options": options})


def fetch_price_calendar(search, filt, options):
    return _price_call("PriceCalendarQuery", "itineraryPricesCalendar", PRICE_CALENDAR_QUERY, search, filt, options)


def fetch_return_price_calendar(search, filt, options):
    return _price_call("ReturnPriceCalendarQuery", "returnItineraryPricesCalendar", RETURN_CALENDAR_QUERY, search, filt, options)


def fetch_price_graph(search, filt, options):
    return _price_call("PriceGraphQuery", "itineraryPriceGraph", PRICE_GRAPH_QUERY, search, filt, options)


def fetch_price_table(search, filt, options):
    return _price_call("PriceTableQuery", "itineraryPriceTable", PRICE_TABLE_QUERY, search, filt, options)


def fetch_price_map(search, filt, options):
    return _price_call("PricesMapQuery", "itineraryPricesMap", PRICE_MAP_QUERY, search, filt, options)


# -- nomad example trips

CITY_FULL = f"{{ id legacyId name slug code gps {{ lat lng }} timezone airportsCount tags country {COUNTRY_BRIEF} region {{ id name slug }} }}"
NOMAD_TRIPS_QUERY = f"""query nomadExampleTrips($source: IdsAndSlugsInput, $locale: Locale!) {{
  nomadExampleTrips(source: $source, locale: $locale) {{ __typename
    ... on AppError {{ code message }}
    ... on NomadExampleTrips {{ trips {{ continent {{ id name slug gps {{ lat lng }} }} days cities {CITY_FULL} }} }} }} }}"""


def fetch_nomad_trips(source_ids, locale="en"):
    return gql("NomadExampleTripsQuery", "nomadExampleTrips", NOMAD_TRIPS_QUERY,
               {"source": {"ids": source_ids}, "locale": locale}, timeout=QUICK_TIMEOUT)


# -- airlines (legacy REST list, ~1,100 rows / 150 KB)

def fetch_airlines():
    """GET /airlines -> the raw list [{id, name, lcc, type}]."""
    def once():
        try:
            resp = _session().get(AIRLINES_URL, headers={k: v for k, v in HEADERS.items() if k != "content-type"},
                                  timeout=QUICK_TIMEOUT)
        except Exception as e:
            raise KiwiComUpstreamError(f"request failed: {type(e).__name__}: {e}")
        payload = _classify(resp, "airlines")
        if not isinstance(payload, list):
            raise KiwiComUpstreamError(f"unexpected airlines payload: {str(payload)[:120]}")
        return payload
    return _retrying(once)
