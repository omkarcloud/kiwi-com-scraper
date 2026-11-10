"""Reference resolvers for /kiwi-com/*: ONE param per input (tripadvisor
QueryOrIdField convention, never a sibling `url` / `id` pair).

Places (origin / destination / place):
  * a Kiwi place id          City:london_gb, Station:airport:LHR, Country:GB,
                             Region:northern-europe, Continent:europe,
                             Subdivision:EN_GB, POI:tower-of-london_poi,
                             TouristRegion:greater-london_poi
  * a legacy city id         london_gb, new-york-city_ny_us  -> City:<id>
  * a 2-letter country code  GB                              -> Country:GB
  * a 3-letter IATA code     LHR / LON                       -> code:LHR
  * a kiwi.com place slug    london-united-kingdom           -> slug:<slug>
  * a kiwi.com link          https://www.kiwi.com/en/cheap-flights/london-united-kingdom/
                             (one place slug in the path)    -> slug:<slug>
  * anything else            "new york"                      -> term:<text>
The `code:` / `slug:` / `term:` forms are resolved against the places query
by api.py (cached); ids pass straight through.

Itineraries (flights/details):
  * a share id               eyJyIjoi…  (base64 JSON {"r": route, "t": OW|RT|MC})
  * an itinerary id          ItineraryOneWay:eyJ… / ItineraryReturn:eyJ… /
                             ItineraryMulticity:eyJ…  (base64 {"route_data"})
  * a kiwi.com link carrying ?shareId=<share id>
decode_itinerary() turns any of them into the route (stations, dates, trip
type) the details lookup needs: the engine finds an itinerary by its share
id only inside a search for the same route and date.

Resolvers raise ValueError with a user-facing message (RefField -> 400).
"""
import base64
import json
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

SITE = "https://www.kiwi.com"
API = "https://api.skypicker.com"

# Public choice value -> the umbrella gateway's enum token (schema tables,
# 2026-11-07 introspection).
CABIN_CLASSES = {"economy": "ECONOMY", "premium_economy": "PREMIUM_ECONOMY",
                 "business": "BUSINESS", "first": "FIRST_CLASS"}
SORTS = {"quality": "QUALITY", "price": "PRICE", "duration": "DURATION",
         "departure": "SOURCE_TAKEOFF", "arrival": "DESTINATION_LANDING", "popularity": "POPULARITY"}
POPULARITY = {"searches": "SEARCHES", "bookings": "BOOKINGS", "clicks": "CLICKS"}
TRANSPORT = {"flight": "FLIGHT", "bus": "BUS", "train": "TRAIN"}
PLACE_TYPES = {"airport": "AIRPORT", "city": "CITY", "country": "COUNTRY", "region": "REGION",
               "continent": "CONTINENT", "subdivision": "SUBDIVISION", "bus_station": "BUS_STATION",
               "train_station": "TRAIN_STATION", "point_of_interest": "POINT_OF_INTEREST",
               "tourist_region": "TOURIST_REGION"}

PLACE_ID_RE = re.compile(
    r"^(City|Station|Country|Region|Continent|Subdivision|POI|TouristRegion|AutonomousTerritory)"
    r":[A-Za-z0-9_:.-]{1,80}$")
LEGACY_CITY_RE = re.compile(r"^[a-z0-9-]+_[a-z]{2}(_[a-z]{2})?$")
IATA_RE = re.compile(r"^[A-Za-z]{3}$")
COUNTRY_RE = re.compile(r"^[A-Za-z]{2}$")
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)+$")
DATE_SEG_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LOCALE_SEG_RE = re.compile(r"^[a-z]{2}(-[a-z]{2})?$")
SHARE_ID_RE = re.compile(r"^[A-Za-z0-9_=-]{20,}$")
ITINERARY_ID_RE = re.compile(r"^Itinerary(OneWay|Return|Multicity|Nomad):([A-Za-z0-9_=+/-]{20,})$")

# Path words on www.kiwi.com that are never a place slug.
_LINK_NOISE = {
    "search", "results", "cheap-flights", "no-return", "deals", "airport", "airports", "country",
    "city", "flights", "nomad", "stays", "booking", "anywhere", "anytime", "flight", "to", "from",
    "www.kiwi.com", "kiwi.com", "explore", "destinations", "travel-guide", "airlines", "routes",
}


def _host_is_kiwi(url):
    host = (urlparse(url).hostname or "").lower()
    return host == "kiwi.com" or host.endswith(".kiwi.com")


def _as_url(value):
    if value.startswith(("http://", "https://", "//")):
        return value if not value.startswith("//") else "https:" + value
    return None


def _link_slugs(url):
    """The place-slug path segments of a kiwi.com link."""
    out = []
    for seg in urlparse(url).path.split("/"):
        seg = seg.strip().lower()
        if not seg or seg in _LINK_NOISE or DATE_SEG_RE.match(seg) or LOCALE_SEG_RE.match(seg):
            continue
        if SLUG_RE.match(seg) or LEGACY_CITY_RE.match(seg):
            out.append(seg)
    return out


def resolve_place(value):
    """A place in any accepted form -> id | code:X | slug:X | term:X."""
    value = (value or "").strip()
    if not value:
        raise ValueError("Must not be empty.")
    url = _as_url(value)
    if url:
        if not _host_is_kiwi(url):
            raise ValueError("Must be a kiwi.com link.")
        slugs = _link_slugs(url)
        if len(slugs) == 1:
            return "slug:" + slugs[0]
        if len(slugs) > 1:
            raise ValueError("This kiwi.com link names several places; pass one place "
                             "(an IATA code, a Kiwi place id or a place slug).")
        raise ValueError("Must be a kiwi.com link with a place slug in its path, e.g. "
                         "https://www.kiwi.com/en/cheap-flights/london-united-kingdom/.")
    if PLACE_ID_RE.match(value):
        return value
    if IATA_RE.match(value):
        return "code:" + value.upper()
    if COUNTRY_RE.match(value):
        return "Country:" + value.upper()
    low = value.lower()
    if LEGACY_CITY_RE.match(low):
        return "City:" + low
    if SLUG_RE.match(low):
        return "slug:" + low
    return "term:" + value


def place_ref_kind(ref):
    """'id' | 'code' | 'slug' | 'term' for a resolve_place() result."""
    for kind in ("code", "slug", "term"):
        if ref.startswith(kind + ":"):
            return kind
    return "id"


def _b64_json(token):
    token = token.strip()
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except Exception:
        raw = None
    if raw is None:
        try:
            raw = base64.b64decode(token + "=" * (-len(token) % 4))
        except Exception:
            raise ValueError("not base64")
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        raise ValueError("not JSON")
    if not isinstance(data, dict):
        raise ValueError("not an object")
    return data


def resolve_itinerary(value):
    """Share id / itinerary id / kiwi.com share link -> the share id or the
    itinerary id, verified to decode to a route."""
    value = (value or "").strip()
    if not value:
        raise ValueError("Must not be empty.")
    url = _as_url(value)
    if url:
        if not _host_is_kiwi(url):
            raise ValueError("Must be a kiwi.com link.")
        query = parse_qs(urlparse(url).query)
        share = (query.get("shareId") or query.get("shareid") or query.get("share_id") or [""])[0].strip()
        if not share:
            raise ValueError("Must be a kiwi.com link carrying ?shareId=…, a share id or an itinerary id.")
        value = share
    try:
        decode_itinerary(value)
    except ValueError as e:
        raise ValueError(f"Not a Kiwi.com itinerary reference ({e}): pass the share id, the "
                         "itinerary id (ItineraryOneWay:…) or a kiwi.com link with ?shareId=.")
    return value


def _parse_route(route):
    """'W9:5377:LTN:1796904900:BCN:1796912700:economy:…|…' -> segment dicts."""
    segments = []
    for part in (route or "").split("|"):
        bits = part.split(":")
        if len(bits) < 6:
            raise ValueError("unknown route encoding")
        try:
            dep, arr = int(bits[3]), int(bits[5])
        except ValueError:
            raise ValueError("unknown route encoding")
        segments.append({
            "carrier": bits[0], "flight_number": bits[1],
            "from": bits[2].upper(), "departure_ts": dep,
            "to": bits[4].upper(), "arrival_ts": arr,
            "cabin": bits[6] if len(bits) > 6 else None,
        })
    if not segments:
        raise ValueError("empty route")
    return segments


def _date_of(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def decode_itinerary(value):
    """Share id or itinerary id -> {trip: one_way|round_trip|multi_city,
    share_id, itinerary_id, legs: [{origin, destination, date}]}."""
    value = (value or "").strip()
    share_id = itinerary_id = None
    match = ITINERARY_ID_RE.match(value)
    if match:
        itinerary_id = value
        payload = _b64_json(match.group(2))
        route = payload.get("route_data")
        kind = {"OneWay": "OW", "Return": "RT", "Multicity": "MC", "Nomad": "MC"}[match.group(1)]
        outbound_count = None
    elif SHARE_ID_RE.match(value):
        share_id = value
        payload = _b64_json(value)
        route = payload.get("r")
        kind = payload.get("t") or "OW"
        sectors = payload.get("i") or []
        outbound_count = len(str(sectors[0]).split("|")) if sectors else None
    else:
        raise ValueError("unrecognised id")
    if not isinstance(route, str) or not route:
        raise ValueError("no route inside the id")
    segments = _parse_route(route)
    legs = []
    if kind == "RT" and len(segments) > 1:
        if not outbound_count or outbound_count >= len(segments):
            # No sector split in the id: the inbound starts after the longest
            # ground gap (the stay at the destination).
            gaps = [segments[i + 1]["departure_ts"] - segments[i]["arrival_ts"]
                    for i in range(len(segments) - 1)]
            outbound_count = gaps.index(max(gaps)) + 1
        out, back = segments[:outbound_count], segments[outbound_count:]
        legs.append({"origin": out[0]["from"], "destination": out[-1]["to"],
                     "date": _date_of(out[0]["departure_ts"])})
        legs.append({"origin": back[0]["from"], "destination": back[-1]["to"],
                     "date": _date_of(back[0]["departure_ts"])})
        trip = "round_trip"
    elif kind == "MC":
        # Multi-city sectors are consecutive runs that end where the next one
        # starts from a different station after a day or more.
        runs, run = [], [segments[0]]
        for prev, cur in zip(segments, segments[1:]):
            if cur["from"] != prev["to"] or cur["departure_ts"] - prev["arrival_ts"] >= 20 * 3600:
                runs.append(run)
                run = []
            run.append(cur)
        runs.append(run)
        for run in runs:
            legs.append({"origin": run[0]["from"], "destination": run[-1]["to"],
                         "date": _date_of(run[0]["departure_ts"])})
        trip = "multi_city"
    else:
        legs.append({"origin": segments[0]["from"], "destination": segments[-1]["to"],
                     "date": _date_of(segments[0]["departure_ts"])})
        trip = "one_way"
    return {"trip": trip, "share_id": share_id, "itinerary_id": itinerary_id,
            "legs": legs, "segments": segments}


def booking_link(booking_url):
    """The relative bookingUrl the API returns -> an absolute kiwi.com link."""
    if not booking_url:
        return None
    if booking_url.startswith("http"):
        return booking_url
    return SITE + ("" if booking_url.startswith("/") else "/") + booking_url
