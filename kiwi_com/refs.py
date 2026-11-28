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
  * a kiwi.com link carrying the share id: the `share_link` we return and
    kiwi.com's own share link (?sharedItineraryId=), the link the site opens
    when a visitor picks a result (?prebooking=), or ?shareId=
decode_itinerary() turns any of them into the route (stations, dates, trip
type) the details lookup needs: the engine finds an itinerary by its share
id only inside a search for the same route and date.

Links out (booking_link / share_link): see share_link() for what kiwi.com
opens and for how long.

Resolvers raise ValueError with a user-facing message (RefField -> 400).
"""
import base64
import json
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, quote, urlencode, urlparse

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
# Query params of a kiwi.com link that carry an itinerary's share id:
# sharedItineraryId (the site's Share button and our share_link), prebooking
# (the results page after a visitor picks a result), shareId (accepted; the
# site itself never opens /booking/?shareId=, see share_link()).
SHARE_LINK_PARAMS = ("sharedItineraryId", "prebooking", "shareId", "shareid", "share_id")

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
        share = next((query[k][0].strip() for k in SHARE_LINK_PARAMS if (query.get(k) or [""])[0].strip()), "")
        if not share:
            raise ValueError("Must be a kiwi.com link carrying the itinerary (the `share_link` a search returned, "
                             "or a kiwi.com link with ?sharedItineraryId=), a share id or an itinerary id.")
        value = share
    try:
        decode_itinerary(value)
    except ValueError as e:
        raise ValueError(f"Not a Kiwi.com itinerary reference ({e}): pass the share id, the "
                         "itinerary id (ItineraryOneWay:…) or the itinerary's `share_link`.")
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
    """The relative bookingUrl the API returns -> an absolute kiwi.com link:
    /<locale>/booking/?direct=true&locale=…&currency=…&passengers=a-c-i&token=…
    &searchType=…&searchBags=…, the same link the site's own "Continue with
    Basic" button carries (minus its searchSessionId / deeplink_uuid /
    backToSearchUrl). It opens kiwi.com's booking form at the quoted price
    for any visitor, from any IP and browser, until the token's booking
    session expires; see share_link() for the measurements."""
    if not booking_url:
        return None
    if booking_url.startswith("http"):
        return booking_url
    return SITE + ("" if booking_url.startswith("/") else "/") + booking_url


def _place_segment(city_id):
    """City:belfast_gb -> belfast_gb, the path form every locale accepts."""
    if not isinstance(city_id, str) or ":" not in city_id:
        return None
    kind, _, legacy = city_id.partition(":")
    return legacy if kind == "City" and legacy else None


# Cabin -> kiwi.com's cabinClass URL value (applyMixedClasses false). First
# class under both of its names: "first" (our cabin_class param) and
# "first_class" (an itinerary's own cabin_classes, the API's FIRST_CLASS).
_CABIN_URL = {"premium_economy": "PREMIUM_ECONOMY-false", "business": "BUSINESS-false",
              "first": "FIRST_CLASS-false", "first_class": "FIRST_CLASS-false"}


def share_link(share_id, origin_city_id, destination_city_id, date, return_date=None, booking_url=None,
               cabin_class=None):
    """kiwi.com's own link to one itinerary, as its Share button makes it:
    the search-results page of the itinerary's route and dates with that
    itinerary's trip-details dialog open (its flights, today's price and
    the "Continue" buttons that start a fresh booking session), pinned as
    "Shared itinerary" above the results.

        https://www.kiwi.com/en/search/results/belfast_gb/edinburgh_gb/2026-12-26/2027-01-01/
            ?sharedItineraryId=<share id>&prebookingShared=1&currency=gbp

    It carries no token, so it keeps working while the flights are on sale
    (kiwi.com searches again when it opens); booking_link carries a token
    and stops working when that booking session expires.

    origin_city_id / destination_city_id: the outbound's first and last city
    (City:belfast_gb); the path takes the bare id (belfast_gb), which every
    locale path accepts, where slugs are localized (/de/ wants
    belfast-vereinigtes-konigreich). date / return_date: local departure
    dates (no return date -> no-return). booking_url: the itinerary's own
    bookingUrl; its locale path, currency and passengers (a-c-i) are copied
    so the link repeats the same search. cabin_class: the itinerary's cabin;
    without it kiwi.com searches economy. Bags are left out: with bags=…
    the dialog does not open (the visitor picks bags on the booking form).

    Evidence, 2026-11-28, Jane's itinerary (BreaksFromHere, chat 627):
    BFS -> EDI, RK5968 on 26 Dec 2026, RK179 back on 1 Jan 2027, £35, each
    link opened in a fresh browser context:
      * https://www.kiwi.com/en/booking/?shareId=<share id> (the form the
        spec gave as a details example): "Hmm, this link isn't right". The
        same page for a bare /en/booking/ (her screenshot's address), a
        token link cut at 1,000 characters, or one with &amp; for &.
      * booking_link (token minted by the production pod, opened from a
        browser on another IP): the booking form, "Total (GBP) £35"; a
        token minted at 06:23 UTC still opened it at 07:10 (47 minutes);
        the 2026-11-07 token in fixtures/search_one_way_lon_nyc.json: "Your
        booking session expired" (the exact lifetime was not measured).
      * kiwi.com's own links for that result: picking it opens
        /en/search/results/belfast-united-kingdom/edinburgh-united-kingdom/
        2026-12-26/2027-01-01/?sortBy=price&prebooking=<share id>; its Share
        button gives https://kiwi.com/u/r8csu5749v, a short form of the same
        path with ?shared=1&utm_…&sharedItineraryId=<share id>&sharePrice=…
        &shareDestId=edinburgh_gb&shareDestName=Edinburgh&currency=inr
        &prebookingShared=1.
      * this link: the trip-details dialog, 19:50 / 16:10, "Tickets starting
        at £35", in /en/ and /de/ and for NYC -> LON (new-york-city_ny_us).
        Without prebookingShared=1 only the result list shows; with bags=…
        no dialog; a LON -> NYC business itinerary without cabinClass showed
        $594 (the economy fare of the same flights) instead of $1,412.
    """
    origin, destination = _place_segment(origin_city_id), _place_segment(destination_city_id)
    if not (share_id and origin and destination and date):
        return None
    locale, query = "en", {}
    if booking_url:
        parsed = urlparse(booking_url)
        first = parsed.path.strip("/").split("/")[0]
        if LOCALE_SEG_RE.match(first):
            locale = first
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    params = {"prebookingShared": "1"}
    if query.get("currency"):
        params["currency"] = query["currency"].lower()
    counts = (query.get("passengers") or "").split("-")
    for name, value, default in zip(("adults", "children", "infants"), counts, ("1", "0", "0")):
        if value.isdigit() and value != default:
            params[name] = value
    if _CABIN_URL.get(cabin_class):
        params["cabinClass"] = _CABIN_URL[cabin_class]
    path = f"/{locale}/search/results/{origin}/{destination}/{date}/{return_date or 'no-return'}/"
    return (f"{SITE}{path}?sharedItineraryId={quote(share_id, safe='=')}&"
            + urlencode(params))
