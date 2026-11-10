"""The /kiwi-com/* endpoint functions (route_glue's route_table calls them
with the schema's loaded kwargs). Every endpoint caches its own response
here (cache.cached_call keyed on the validated params); the place a code /
slug / name resolves to is cached once per reference (kiwi_com.place, 7
days) and shared by every endpoint, so a client searching LON->NYC ten ways
resolves each place once.

Multi-city: Kiwi's own multicityItineraries field answered 200 with 0 rows
for every option set tried on 2026-11-07, so /flights/search-multi-city is
a chain of one-way searches, one per leg. Airline filters are applied here
because `filter.carriers` returns 0 rows for every id form upstream.
"""
import json
from datetime import date, timedelta

import config
from cache import cached_call
from kiwi_com import cache_config as ttl
from kiwi_com import fetch, parsers, refs
from kiwi_com.fetch import KiwiComNotFound

DEFAULT_WINDOW_DAYS = 30
FILTERED_UPSTREAM_LIMIT = 300    # airline post-filters read the whole engine set first


# ---- request building ----------------------------------------------------------------

def _options(currency, locale, market, sort="quality"):
    partner = config.KIWI_COM_PARTNER
    return {"sortBy": refs.SORTS.get(sort, sort), "currency": currency.lower(), "locale": locale, "partner": partner,
            "partnerMarket": market.lower(), "affilID": partner, "storeSearch": False,
            "searchStrategy": "REDUCED", "mergePriceDiffRule": "INCREASED", "market": market.lower()}


def _passengers(adults=1, children=0, infants=0, hand_bags=0, checked_bags=0, **_):
    return {"adults": adults, "children": children, "infants": infants,
            "adultsHoldBags": [checked_bags] * adults, "adultsHandBags": [hand_bags] * adults,
            "childrenHoldBags": [checked_bags] * children, "childrenHandBags": [hand_bags] * children}


def _cabin(cabin_class="economy"):
    return {"cabinClass": refs.CABIN_CLASSES.get(cabin_class, cabin_class), "applyMixedClasses": False}


def _span(start, end=None):
    return {"start": f"{start}T00:00:00", "end": f"{end or start}T23:59:59"}


def _plus_days(day, days):
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()


def _day_window(day):
    """The departure-date span for a date decoded from a share id. The id
    carries UTC epochs while the engine indexes local departure dates (an
    EWR 22:20 local departure is the next UTC day), so the lookup spans the
    day either side of the decoded date (never before today)."""
    start = max(_plus_days(day, -1), date.today().isoformat())
    return _span(start, _plus_days(day, 1))


def _base_filter(limit, self_transfer=True, travel_hacks=True, transport=None, max_stops=None,
                 max_duration_hours=None, max_price=None, max_layover_hours=None,
                 departure_hours=None, arrival_hours=None, **_):
    filt = {
        "allowReturnFromDifferentCity": True, "allowChangeInboundDestination": True,
        "allowChangeInboundSource": True, "allowDifferentStationConnection": True,
        "enableSelfTransfer": bool(self_transfer), "enableThrowAwayTicketing": bool(travel_hacks),
        "enableTrueHiddenCity": bool(travel_hacks),
        "transportTypes": transport or ["FLIGHT"],
        "contentProviders": list(config.KIWI_COM_CONTENT_PROVIDERS),
        "limit": limit,
    }
    if max_stops is not None:
        filt["maxStopsCount"] = max_stops
    if max_duration_hours:
        filt["maxDuration"] = max_duration_hours
    if max_price:
        filt["price"] = {"start": 0, "end": int(max_price)}
    if max_layover_hours:
        filt["stopoverTime"] = {"start": 0, "end": max_layover_hours}
    outbound = {}
    if departure_hours:
        outbound["departureHours"] = departure_hours
    if arrival_hours:
        outbound["arrivalHours"] = arrival_hours
    if outbound:
        filt["outbound"] = outbound
    return filt


def _passenger_echo(adults, children, infants, hand_bags, checked_bags, cabin_class, **_):
    return {"adults": adults, "children": children, "infants": infants,
            "hand_bags": hand_bags, "checked_bags": checked_bags, "cabin_class": cabin_class.lower()}


# ---- places -------------------------------------------------------------------------------

_LOOKUP_TYPES = ["AIRPORT", "CITY", "COUNTRY", "REGION", "CONTINENT", "SUBDIVISION",
                 "BUS_STATION", "TRAIN_STATION"]


def _find_place(ref, locale):
    """resolve_place() output -> the first matching place record, or NotFound.
    Codes, slugs and names are matched in English (a slug is the English
    one; a localized lookup of it finds nothing), then the record is
    re-read by id in the requested locale for localized names."""
    kind = refs.place_ref_kind(ref)
    value = ref.split(":", 1)[1] if kind != "id" else ref
    options = {"locale": "en", "sortBy": "RANK"}
    if kind == "id":
        rows = parsers.parse_places(fetch.fetch_places({"ids": [value]}, {}, options, 1))
    elif kind == "slug":
        rows = parsers.parse_places(fetch.fetch_places({"slugs": [value]}, {}, options, 1))
        if not rows:
            # the autocomplete needs a short term: the slug's first two words
            rows = parsers.parse_places(fetch.fetch_places(
                {"term": " ".join(value.split("-")[:2])}, {"onlyTypes": _LOOKUP_TYPES}, options, 1))
    elif kind == "code":
        rows = parsers.parse_places(fetch.fetch_places(
            {"term": value}, {"onlyTypes": ["AIRPORT", "CITY", "BUS_STATION", "TRAIN_STATION"]}, options, 10))
        rows = [r for r in rows if (r.get("code") or "").upper() == value]
    else:
        rows = parsers.parse_places(fetch.fetch_places({"term": value}, {"onlyTypes": _LOOKUP_TYPES}, options, 1))
    if not rows:
        raise KiwiComNotFound(f"place not found: {value}")
    place = rows[0]
    if locale != "en" and place.get("id"):
        localized = parsers.parse_places(fetch.fetch_places({"ids": [place["id"]]}, {}, {"locale": locale}, 1))
        if localized:
            place = localized[0]
    return place


def _place(ref, locale="en"):
    return cached_call("kiwi_com.place", {"ref": ref, "locale": locale}, _find_place, ttl.PLACE_CACHE)


def _brief(ref, locale="en"):
    return parsers.place_brief(_place(ref, locale))


def _places_search(query, types, limit, currency, locale, market):
    filt = {"onlyTypes": types} if types else {}
    payload = fetch.fetch_places({"term": query}, filt, {"locale": locale, "sortBy": "RANK"}, limit)
    rows = parsers.parse_places(payload)
    for row in rows:
        row.pop("stations", None)
    return {"query": query, "count": len(rows), "results": rows}


def places_search(**data):
    """Autocomplete airports, cities, countries and regions by name or IATA
    code: id, code, slug, type, city, country, timezone and coordinates."""
    return cached_call("kiwi_com.places_search", data, _places_search, ttl.PLACES_CACHE)


def _place_details(place, currency, locale, market):
    return _place(place, locale)


def place_details(**data):
    """One place by IATA code, Kiwi id, slug, kiwi.com link or name: id, code,
    type, city, country, region, timezone, coordinates and, for a city, its
    airports."""
    # the shared place row is the cache; no second namespace
    return _place_details(**data)


def _places_nearby(lat, lng, radius_km, types, limit, currency, locale, market):
    position = {"lat": lat, "lng": lng}
    filt = {"onlyTypes": types or ["AIRPORT", "CITY"]}
    payload = fetch.fetch_places({"radius": {"position": position, "radius": radius_km}}, filt,
                                 {"locale": locale, "sortBy": "RANK_DISTANCE", "position": position}, limit)
    rows = parsers.parse_places(payload)
    for row in rows:
        row.pop("stations", None)
    return {"center": position, "radius_km": radius_km, "count": len(rows), "results": rows}


def places_nearby(**data):
    """Airports and cities around a point, nearest first, with the distance
    in km."""
    return cached_call("kiwi_com.places_nearby", data, _places_nearby, ttl.PLACES_CACHE)


def _top_destinations(place, popularity, limit, currency, locale, market):
    origin = _place(place, locale)
    payload = fetch.fetch_places({"topDestinations": {"id": origin["id"],
                                                      "popularity": refs.POPULARITY.get(popularity, popularity)}},
                                 {}, {"locale": locale}, limit)
    rows = parsers.parse_places(payload)
    for row in rows:
        row.pop("stations", None)
    return {"origin": parsers.place_brief(origin), "popularity": popularity,
            "count": len(rows), "results": rows}


def top_destinations(**data):
    """The most searched, booked or clicked destinations from a place on
    Kiwi.com: city, country, region, tags and coordinates."""
    return cached_call("kiwi_com.top_destinations", data, _top_destinations, ttl.PLACES_CACHE)


# ---- flights: search --------------------------------------------------------------------

def _itinerary_airlines(record):
    codes = set()
    sectors = [record.get("outbound"), record.get("inbound")] + list(record.get("legs") or [])
    for sector in sectors:
        for code in (sector or {}).get("airlines") or []:
            codes.add(code)
    return codes


def _apply_airline_filters(results, airlines, exclude_airlines):
    keep = set(airlines or [])
    drop = set(exclude_airlines or [])
    out = []
    for record in results:
        codes = _itinerary_airlines(record)
        if keep and not codes <= keep:
            continue
        if drop and codes & drop:
            continue
        out.append(record)
    return out


def _run_search(trip, itinerary, data):
    """One itinerary search; returns (parsed payload, results after the
    airline post-filters and the caller's limit)."""
    limit = data["limit"]
    filtering = bool(data.get("airlines") or data.get("exclude_airlines"))
    filt = _base_filter(FILTERED_UPSTREAM_LIMIT if filtering else limit,
                        **{k: v for k, v in data.items() if k != "limit"})
    search = {"itinerary": itinerary, "passengers": _passengers(**data), "cabinClass": _cabin(data["cabin_class"])}
    payload = fetch.fetch_itineraries(trip, search, filt, _options(data["currency"], data["locale"],
                                                                   data["market"], data["sort"]))
    parsed = parsers.parse_itineraries(payload)
    results = _apply_airline_filters(parsed["results"], data.get("airlines"), data.get("exclude_airlines"))
    return parsed, results[:limit]


def _search_response(trip, data, origin, destination, parsed, results, **dates):
    return {
        "trip_type": trip,
        "origin": parsers.place_brief(origin),
        "destination": parsers.place_brief(destination),
        **dates,
        "passengers": _passenger_echo(**data),
        "currency": data["currency"],
        "sort": data["sort"],
        "count": len(results),
        "total_count": parsed["total_count"],
        "has_more_pending": parsed["has_more_pending"],
        "results": results,
        "summary": parsed["summary"],
    }


def _search_one_way(**data):
    origin, destination = _place(data["origin"]), _place(data["destination"])
    itinerary = {"source": {"ids": [origin["id"]]}, "destination": {"ids": [destination["id"]]},
                 "outboundDepartureDate": _span(data["date"], data.get("date_to"))}
    parsed, results = _run_search("one_way", itinerary, data)
    return _search_response("one_way", data, origin, destination, parsed, results,
                            date=data["date"], date_to=data.get("date_to"))


def search_one_way(**data):
    """One-way flight search: every itinerary with price, fare options,
    segments, airlines, layovers, bag allowances and fees, seats left,
    self-transfer and hidden-city flags and the kiwi.com booking link, plus
    the result-set facets (price histogram, stops, airlines)."""
    return cached_call("kiwi_com.search_one_way", data, _search_one_way, ttl.SEARCH_CACHE)


def _search_round_trip(**data):
    origin, destination = _place(data["origin"]), _place(data["destination"])
    itinerary = {"source": {"ids": [origin["id"]]}, "destination": {"ids": [destination["id"]]},
                 "outboundDepartureDate": _span(data["date"], data.get("date_to"))}
    nights = None
    if data.get("return_date"):
        itinerary["inboundDepartureDate"] = _span(data["return_date"], data.get("return_date_to"))
    else:
        nights = {"min": data["nights_min"], "max": data.get("nights_max") or data["nights_min"]}
        itinerary["nightsCount"] = {"start": nights["min"], "end": nights["max"]}
    parsed, results = _run_search("round_trip", itinerary, data)
    return _search_response("round_trip", data, origin, destination, parsed, results,
                            date=data["date"], date_to=data.get("date_to"),
                            return_date=data.get("return_date"), return_date_to=data.get("return_date_to"),
                            nights=nights)


def search_round_trip(**data):
    """Round-trip flight search: outbound and inbound sectors per itinerary
    with price, segments, layovers, bags, stay length and the booking link,
    for exact dates, flexible windows or a nights range."""
    return cached_call("kiwi_com.search_round_trip", data, _search_round_trip, ttl.SEARCH_CACHE)


def _search_multi_city(**data):
    legs_in = json.loads(data["legs"])
    legs = []
    for leg in legs_in:
        origin, destination = _place(leg["origin"]), _place(leg["destination"])
        itinerary = {"source": {"ids": [origin["id"]]}, "destination": {"ids": [destination["id"]]},
                     "outboundDepartureDate": _span(leg["date"])}
        parsed, results = _run_search("one_way", itinerary, data)
        legs.append({
            "origin": parsers.place_brief(origin), "destination": parsers.place_brief(destination),
            "date": leg["date"], "count": len(results), "total_count": parsed["total_count"],
            "results": results, "summary": parsed["summary"],
        })
    cheapest = []
    for leg in legs:
        prices = [r["price"]["amount"] for r in leg["results"] if (r.get("price") or {}).get("amount") is not None]
        cheapest.append(min(prices) if prices else None)
    return {
        "trip_type": "multi_city",
        "passengers": _passenger_echo(**data),
        "currency": data["currency"],
        "sort": data["sort"],
        "legs_count": len(legs),
        "cheapest_total": sum(cheapest) if cheapest and all(p is not None for p in cheapest) else None,
        "legs": legs,
    }


def search_multi_city(**data):
    """Multi-city flight search: 2-6 legs, each with its own itinerary list
    (price, segments, layovers, bags, booking link), plus the cheapest total."""
    return cached_call("kiwi_com.search_multi_city", data, _search_multi_city, ttl.SEARCH_CACHE)


# ---- flights: details -----------------------------------------------------------------

def _itinerary_details(itinerary, currency, locale, market, **data):
    info = refs.decode_itinerary(itinerary)
    legs = info["legs"]
    trip = info["trip"]
    passengers = _passengers(**data)
    cabin = _cabin(data["cabin_class"])
    # The share id carries bare station codes; the engine matches a shared
    # itinerary only when the search names full place ids (a bare LHR found
    # nothing on 2026-11-07 where Station:airport:LHR did), so each code is
    # resolved through the cached place lookup.
    ids = {code: _station_id(code) for leg in legs for code in (leg["origin"], leg["destination"])}
    if trip == "multi_city":
        search = {"itinerary": [{"source": {"ids": [ids[leg["origin"]]]}, "destination": {"ids": [ids[leg["destination"]]]},
                                 "outboundDepartureDate": _day_window(leg["date"])} for leg in legs],
                  "passengers": passengers, "cabinClass": cabin}
    else:
        route = {"source": {"ids": [ids[legs[0]["origin"]]]}, "destination": {"ids": [ids[legs[0]["destination"]]]},
                 "outboundDepartureDate": _day_window(legs[0]["date"])}
        if trip == "round_trip":
            route["inboundDepartureDate"] = _day_window(legs[-1]["date"])
        search = {"itinerary": route, "passengers": passengers, "cabinClass": cabin}
    options = _options(currency, locale, market)
    filt = _base_filter(5)
    if info["share_id"]:
        options["sharedItineraryId"] = info["share_id"]
        payload = fetch.fetch_itineraries(trip, search, filt, options)
        record = parsers.parse_itinerary(((payload or {}).get("metadata") or {}).get("sharedItinerary"))
    else:
        search["knownItineraryIds"] = [info["itinerary_id"]]
        payload = fetch.fetch_itineraries(trip, search, filt, options)
        raw = next((it for it in (payload or {}).get("itineraries") or []
                    if isinstance(it, dict) and it.get("id") == info["itinerary_id"]), None)
        record = parsers.parse_itinerary(raw)
    if not record:
        raise KiwiComNotFound("itinerary not found: it is no longer on sale, or the id is not a Kiwi.com "
                              "share id")
    return {
        **record,
        "route": legs,
        "passengers": _passenger_echo(**data),
        "currency": currency,
    }


def _station_id(code):
    try:
        return _place(f"code:{code}")["id"]
    except KiwiComNotFound:
        return code


def itinerary_details(**data):
    """One itinerary by the id a search returned (or a kiwi.com share link),
    re-priced live: price, fares, every segment and layover, bag tiers and
    the booking link."""
    return cached_call("kiwi_com.itinerary_details", data, _itinerary_details, ttl.DETAILS_CACHE)


# ---- flights: explore ------------------------------------------------------------------

def _explore(origin, trip_type, date_from, date_to, nights_min, nights_max, max_price, max_stops, limit,
             currency, locale, market, **data):
    place = _place(origin)
    date_to = date_to or _plus_days(date_from, DEFAULT_WINDOW_DAYS)
    itinerary = {"source": {"ids": [place["id"]]}, "outboundDepartureDate": _span(date_from, date_to)}
    if trip_type == "round_trip":
        itinerary["nightsCount"] = {"start": nights_min, "end": nights_max}
    search = {"itinerary": itinerary, "passengers": _passengers(**data), "cabinClass": _cabin(data["cabin_class"])}
    filt = _base_filter(limit, max_price=max_price, max_stops=max_stops)
    payload = fetch.fetch_explore(trip_type, search, filt, _options(currency, locale, market))
    rows = parsers.parse_explore(payload)
    return {
        "origin": parsers.place_brief(place),
        "trip_type": trip_type,
        "date_from": date_from, "date_to": date_to,
        "nights": {"min": nights_min, "max": nights_max} if trip_type == "round_trip" else None,
        "passengers": _passenger_echo(**data),
        "currency": currency,
        "count": len(rows),
        "results": rows,
    }


def explore(**data):
    """Cheapest destinations from a place: the lowest fare per destination
    city with its departure time, return time and stay length."""
    return cached_call("kiwi_com.explore", data, _explore, ttl.EXPLORE_CACHE)


# ---- prices ------------------------------------------------------------------------------

def _route_ids(origin, destination):
    o, d = _place(origin), _place(destination)
    return o, d, {"source": {"ids": [o["id"]]}, "destination": {"ids": [d["id"]]}}


def _price_calendar(origin, destination, date_from, date_to, currency, locale, market, **data):
    o, d, route = _route_ids(origin, destination)
    date_to = date_to or _plus_days(date_from, DEFAULT_WINDOW_DAYS)
    search = {**route, "dates": _span(date_from, date_to), "passengers": _passengers(**data),
              "cabinClass": _cabin(data["cabin_class"])}
    parsed = parsers.parse_price_calendar(fetch.fetch_price_calendar(search, {}, _options(currency, locale, market)))
    return {"origin": parsers.place_brief(o), "destination": parsers.place_brief(d), "trip_type": "one_way",
            "date_from": date_from, "date_to": date_to, "passengers": _passenger_echo(**data),
            "currency": parsed["currency"] or currency, "legend": parsed["legend"],
            "count": len(parsed["days"]), "days": parsed["days"]}


def price_calendar(**data):
    """One-way price per departure day between two places, rated cheap,
    average or expensive, with the rating thresholds."""
    return cached_call("kiwi_com.price_calendar", data, _price_calendar, ttl.PRICES_CACHE)


def _return_price_calendar(origin, destination, date_from, date_to, nights_min, nights_max,
                           currency, locale, market, **data):
    o, d, route = _route_ids(origin, destination)
    date_to = date_to or _plus_days(date_from, DEFAULT_WINDOW_DAYS)
    nights_max = nights_max if nights_max is not None else nights_min
    search = {**route, "visibleDates": _span(date_from, date_to),
              "nightsCount": {"start": nights_min, "end": nights_max},
              "passengers": _passengers(**data), "cabinClass": _cabin(data["cabin_class"])}
    parsed = parsers.parse_price_calendar(
        fetch.fetch_return_price_calendar(search, {}, _options(currency, locale, market)))
    return {"origin": parsers.place_brief(o), "destination": parsers.place_brief(d), "trip_type": "round_trip",
            "date_from": date_from, "date_to": date_to, "nights": {"min": nights_min, "max": nights_max},
            "passengers": _passenger_echo(**data), "currency": parsed["currency"] or currency,
            "legend": parsed["legend"], "count": len(parsed["days"]), "days": parsed["days"]}


def return_price_calendar(**data):
    """Round-trip price per departure day for a stay length, rated cheap,
    average or expensive."""
    return cached_call("kiwi_com.return_price_calendar", data, _return_price_calendar, ttl.PRICES_CACHE)


def _price_graph(origin, destination, date_from, date_to, nights_min, nights_max, currency, locale, market, **data):
    o, d, route = _route_ids(origin, destination)
    date_to = date_to or _plus_days(date_from, DEFAULT_WINDOW_DAYS)
    itinerary = {**route, "outboundDepartureDate": _span(date_from, date_to)}
    nights = None
    if nights_min is not None:
        nights = {"min": nights_min, "max": nights_max if nights_max is not None else nights_min}
        itinerary["nightsCount"] = {"start": nights["min"], "end": nights["max"]}
    search = {"itinerary": itinerary, "passengers": _passengers(**data), "cabinClass": _cabin(data["cabin_class"])}
    parsed = parsers.parse_price_graph(fetch.fetch_price_graph(search, {}, _options(currency, locale, market)))
    return {"origin": parsers.place_brief(o), "destination": parsers.place_brief(d),
            "trip_type": "round_trip" if nights else "one_way",
            "date_from": date_from, "date_to": date_to, "nights": nights,
            "passengers": _passenger_echo(**data), "currency": parsed["currency"] or currency,
            "count": len(parsed["points"]), "points": parsed["points"]}


def price_graph(**data):
    """The price curve between two places: the cheapest fare per departure
    day, with its return date for round trips."""
    return cached_call("kiwi_com.price_graph", data, _price_graph, ttl.PRICES_CACHE)


def _price_table(origin, destination, date, return_date, currency, locale, market, **data):
    o, d, route = _route_ids(origin, destination)
    search = {"itinerary": {**route, "outboundDepartureDate": _span(date), "inboundDepartureDate": _span(return_date)},
              "passengers": _passengers(**data), "cabinClass": _cabin(data["cabin_class"])}
    parsed = parsers.parse_price_table(fetch.fetch_price_table(search, {}, _options(currency, locale, market)))
    return {"origin": parsers.place_brief(o), "destination": parsers.place_brief(d), "trip_type": "round_trip",
            "date": date, "return_date": return_date, "passengers": _passenger_echo(**data),
            "currency": currency, "legend": parsed["legend"], "count": len(parsed["prices"]),
            "prices": parsed["prices"]}


def price_table(**data):
    """The round-trip price for one departure and return date pair, with its
    cheap / average / expensive rating."""
    return cached_call("kiwi_com.price_table", data, _price_table, ttl.PRICES_CACHE)


WORLD = {"sw_lat": -90.0, "sw_lng": -180.0, "ne_lat": 90.0, "ne_lng": 180.0}


def _price_map(origin, date_from, date_to, return_from, return_to, nights_min, nights_max, return_anytime,
               bounds, currency, locale, market, **data):
    place = _place(origin)
    date_to = date_to or _plus_days(date_from, DEFAULT_WINDOW_DAYS)
    box = bounds or WORLD
    search = {
        "source": {"ids": [place["id"]]},
        "destinationGeoBox": {"southWestGeoBoxCorner": {"lat": box["sw_lat"], "lng": box["sw_lng"]},
                              "northEastGeoBoxCorner": {"lat": box["ne_lat"], "lng": box["ne_lng"]}},
        "dates": _span(date_from, date_to),
        "passengers": _passengers(**data), "cabinClass": _cabin(data["cabin_class"]),
    }
    trip_type, nights = "one_way", None
    if return_from:
        return_to = return_to or _plus_days(return_from, DEFAULT_WINDOW_DAYS)
        search["returnDates"] = _span(return_from, return_to)
        trip_type = "round_trip"
    elif nights_min is not None:
        nights = {"min": nights_min, "max": nights_max if nights_max is not None else nights_min}
        search["nightsCount"] = {"start": nights["min"], "end": nights["max"]}
        trip_type = "round_trip"
    elif return_anytime:
        # sent only when true: an explicit false empties the map (2026-11-07)
        search["returnAnytime"] = True
        trip_type = "round_trip"
    options = _options(currency, locale, market)
    parsed = parsers.parse_price_map(fetch.fetch_price_map(search, {}, options))
    if bounds and not parsed["destinations"]:
        # A bounded box answers 25 rows the first time and 0 on a repeat of
        # the same box + dates for a while (2026-11-07); the world box is
        # steady, so fall back to it and keep the cities inside the box.
        search["destinationGeoBox"] = {"southWestGeoBoxCorner": {"lat": WORLD["sw_lat"], "lng": WORLD["sw_lng"]},
                                       "northEastGeoBoxCorner": {"lat": WORLD["ne_lat"], "lng": WORLD["ne_lng"]}}
        world = parsers.parse_price_map(fetch.fetch_price_map(search, {}, options))
        parsed["destinations"] = [row for row in world["destinations"] if _inside(row["city"].get("gps"), box)]
        parsed["currency"] = parsed["currency"] or world["currency"]
    return {"origin": parsers.place_brief(place), "trip_type": trip_type,
            "date_from": date_from, "date_to": date_to,
            "return_from": return_from if trip_type == "round_trip" and return_from else None,
            "return_to": return_to if trip_type == "round_trip" and return_from else None,
            "nights": nights, "is_return_anytime": "returnAnytime" in search,
            "bounds": box, "passengers": _passenger_echo(**data), "currency": parsed["currency"] or currency,
            "count": len(parsed["destinations"]), "destinations": parsed["destinations"]}


def _inside(gps, box):
    if not gps:
        return False
    return box["sw_lat"] <= gps["lat"] <= box["ne_lat"] and box["sw_lng"] <= gps["lng"] <= box["ne_lng"]


def price_map(**data):
    """The cheapest fare to every city inside a map box from one origin,
    ranked, with city details and coordinates (up to 25 cities per box)."""
    return cached_call("kiwi_com.price_map", data, _price_map, ttl.PRICES_CACHE)


# ---- nomad / airlines ----------------------------------------------------------------------

def _nomad_trips(origin, currency, locale, market):
    place = _place(origin, locale)
    trips = parsers.parse_nomad_trips(fetch.fetch_nomad_trips([place["id"]], locale))
    return {"origin": parsers.place_brief(place), "count": len(trips), "trips": trips}


def nomad_trips(**data):
    """Kiwi.com's suggested multi-stop trips from a place, one per continent:
    the cities in order and the days to spend in each."""
    return cached_call("kiwi_com.nomad_trips", data, _nomad_trips, ttl.NOMAD_CACHE)


def _airline_directory():
    return parsers.parse_airlines(fetch.fetch_airlines())


def _airlines_search(query, limit):
    rows = cached_call("kiwi_com.airlines", {}, _airline_directory, ttl.AIRLINES_CACHE)
    if query:
        needle = query.lower()
        exact = [r for r in rows if (r["code"] or "").lower() == needle]
        partial = [r for r in rows if r not in exact and needle in (r["name"] or "").lower()]
        rows = exact + partial
    rows = rows[:limit]
    return {"query": query, "count": len(rows), "results": rows}


def airlines_search(**data):
    """Search the Kiwi.com airline directory by name or IATA code: code, name
    and low-cost flag."""
    return _airlines_search(**data)
