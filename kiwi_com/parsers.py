"""Kiwi.com umbrella GraphQL payloads -> clean dicts. Pure functions, no I/O.

Fixtures (kiwi_com/fixtures/, captured 2026-11-07 through kiwi_com/fetch.py
with the lean selections it sends):
  search_one_way_lon_nyc.json          onewayItineraries LON->NYC 2026-12-10, limit 5 (48 in the set)
  search_round_trip_lon_nyc.json       returnItineraries LON->NYC 12-10 / 12-17, limit 5
  search_empty.json                    onewayItineraries on a past date: 200, 0 rows (the engine's "not found")
  details_one_way_shared.json          onewayItineraries + options.sharedItineraryId -> metadata.sharedItinerary
  details_round_trip_shared.json       the same for a return itinerary
  details_unknown_share.json           a tampered share id -> sharedItinerary null
  explore_one_way_london.json          onewayOnePerCityItineraries from London, 30 rows
  explore_round_trip_london.json       returnOnePerCityItineraries from London, 3-7 nights
  places_search_london.json            places(term "london") airports + cities + countries
  places_details_city_london.json      places(ids City:london_gb) with its stations
  places_details_station_lhr.json      places(ids Station:airport:LHR)
  places_unknown.json                  places(ids City:nowhere_zz) -> empty connection
  places_nearby_london.json            places(radius 100 km around London) airports with distances
  places_top_destinations_london.json  places(topDestinations London, SEARCHES)
  prices_calendar_lon_nyc.json         itineraryPricesCalendar December 2026
  prices_return_calendar_lon_nyc.json  returnItineraryPricesCalendar, 7 nights
  prices_graph_lon_nyc.json            itineraryPriceGraph with nightsCount 7
  prices_graph_one_way_lon_nyc.json    itineraryPriceGraph without nights (one-way curve)
  prices_table_lon_nyc.json            itineraryPriceTable for one date pair
  prices_map_london_europe.json        itineraryPricesMap, Europe geo box (25-row cap)
  prices_map_london_europe_return.json the same with returnDates
  nomad_example_trips_london.json      nomadExampleTrips from London
  airlines.json                        GET /airlines (2,345 rows)

Conventions: snake_case keys, `link` not `url`, booleans read as questions,
prices as {amount, currency}, durations in minutes, timestamps as the
site's ISO strings (local wall-clock time at the station, plus UTC), null
for anything missing. `__typename`, tracking tokens, formatted price
strings, Kiwi-internal flags (isVanilla, partnerships, searchFingerprint,
serverToken) are dropped.
"""
from kiwi_com.refs import booking_link

_STATION_TYPES = {"AIRPORT": "airport", "BUS_STATION": "bus_station", "TRAIN_STATION": "train_station"}
_PLACE_TYPES = {
    "Station": "station", "City": "city", "Country": "country", "Region": "region",
    "Continent": "continent", "Subdivision": "subdivision", "AutonomousTerritory": "autonomous_territory",
    "POI": "point_of_interest", "TouristRegion": "tourist_region",
}


# ---- scalars ----------------------------------------------------------------------

def _num(value):
    """'384' / '76.80000000000001' / 23 -> float (ints stay ints); None otherwise."""
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else round(number, 2)


def _lower(value):
    return value.lower() if isinstance(value, str) and value else None


def _minutes(seconds):
    return int(round(seconds / 60)) if isinstance(seconds, (int, float)) else None


def _date(value):
    """'2026-12-01T00:00:00' -> '2026-12-01'."""
    return value[:10] if isinstance(value, str) and len(value) >= 10 else None


def _money(money):
    """Money -> {amount, currency} (+ amount_before_discount when it differs)."""
    if not isinstance(money, dict):
        return None
    amount = _num(money.get("amount"))
    if amount is None:
        return None
    out = {"amount": amount, "currency": (money.get("currency") or {}).get("code")}
    if out["currency"]:
        out["currency"] = out["currency"].upper()
    before = _num(money.get("priceBeforeDiscount"))
    if before is not None and before != amount:
        out["amount_before_discount"] = before
    return out


def _gps(gps):
    if not isinstance(gps, dict) or gps.get("lat") is None or gps.get("lng") is None:
        return None
    return {"lat": gps["lat"], "lng": gps["lng"]}


def _brief(node, *keys):
    if not isinstance(node, dict):
        return None
    out = {k: node.get(k) for k in keys}
    return out if any(v is not None for v in out.values()) else None


def _city_brief(city):
    return _brief(city, "id", "name", "code", "slug")


def _country_brief(country):
    return _brief(country, "code", "name")


# ---- stations / carriers ------------------------------------------------------------

def parse_station(station):
    if not isinstance(station, dict):
        return None
    return {
        "id": station.get("id"),
        "code": station.get("code"),
        "name": station.get("name"),
        "slug": station.get("slug"),
        "type": _STATION_TYPES.get(station.get("type"), _lower(station.get("type"))),
        "city": _city_brief(station.get("city")),
        "country": _country_brief(station.get("country")),
        "timezone": station.get("timezone"),
        "gps": _gps(station.get("gps")),
    }


def parse_carrier(carrier):
    if not isinstance(carrier, dict):
        return None
    logo = carrier.get("logo") or {}
    return {
        "code": carrier.get("code"),
        "name": carrier.get("name"),
        "icao": carrier.get("icao"),
        "alliance": carrier.get("alliance"),
        "website": carrier.get("website"),
        "logo_link": logo.get("url") if isinstance(logo, dict) else None,
    }


def _stop(stop):
    if not isinstance(stop, dict):
        return None
    return {
        "station": parse_station(stop.get("station")),
        "local_time": stop.get("localTime"),
        "utc_time": stop.get("utcTime"),
    }


# ---- itineraries ---------------------------------------------------------------------

def _layover(layover):
    if not isinstance(layover, dict):
        return None
    return {
        "duration_minutes": _minutes(layover.get("duration")),
        "guarantee": _guarantee(layover.get("guarantee")),
        "is_station_change": layover.get("isStationChange"),
        "is_baggage_recheck": layover.get("isBaggageRecheck"),
        "is_walking_distance": layover.get("isWalkingDistance"),
        "transfer_duration_minutes": _minutes(layover.get("transferDuration")),
        "transfer_type": _lower(layover.get("transferType")),
    }


def _guarantee(value):
    """KIWI_COM -> 'kiwi', CARRIER -> 'airline', None -> None."""
    return {"KIWI_COM": "kiwi", "CARRIER": "airline"}.get(value, _lower(value))


def _segment(sector_segment):
    segment = sector_segment.get("segment") or {}
    carrier = parse_carrier(segment.get("carrier"))
    number = segment.get("code")
    return {
        "flight_number": f"{carrier['code']}{number}" if carrier and carrier.get("code") and number else number,
        "transport": _lower(segment.get("type")),
        "airline": carrier,
        "operating_airline": parse_carrier(segment.get("operatingCarrier")),
        "departure": _stop(segment.get("source")),
        "arrival": _stop(segment.get("destination")),
        "duration_minutes": _minutes(segment.get("duration")),
        "cabin_class": _lower(segment.get("cabinClass")),
        "has_technical_stop": segment.get("followingTechnicalStop"),
        "hidden_destination": parse_station(segment.get("hiddenDestination")),
        "throwaway_destination": parse_station(segment.get("throwawayDestination")),
        "connection_guarantee": _guarantee(sector_segment.get("guarantee")),
        "layover": _layover(sector_segment.get("layover")),
    }


def _stay(stopover):
    if not isinstance(stopover, dict):
        return None
    # Stopover.duration is already in minutes (10220 for a 7-night stay),
    # unlike every other duration on the itinerary, which is seconds.
    duration = stopover.get("duration")
    out = {"nights": stopover.get("nightsCount"),
           "duration_minutes": int(duration) if isinstance(duration, (int, float)) else None}
    if "isStationChange" in stopover:
        out["is_station_change"] = stopover.get("isStationChange")
    if stopover.get("arrival") or stopover.get("departure"):
        out["arrival_station"] = parse_station(stopover.get("arrival"))
        out["departure_station"] = parse_station(stopover.get("departure"))
    return out if any(v is not None for v in out.values()) else None


def parse_sector(sector):
    """One direction of travel (a chain of segments with their layovers)."""
    if not isinstance(sector, dict):
        return None
    segments = [_segment(ss) for ss in (sector.get("sectorSegments") or []) if isinstance(ss, dict)]
    first = segments[0] if segments else {}
    last = segments[-1] if segments else {}
    airlines = []
    for carrier in sector.get("carriers") or []:
        code = (carrier or {}).get("code")
        if code and code not in airlines:
            airlines.append(code)
    return {
        "departure": first.get("departure"),
        "arrival": last.get("arrival"),
        "duration_minutes": _minutes(sector.get("duration")),
        "stops": max(len(segments) - 1, 0),
        "airlines": airlines,
        "segments": segments,
        "stay_after": _stay(sector.get("stopover")),
    }


def _tier(tier):
    bags = []
    for bag in tier.get("bags") or []:
        if not isinstance(bag, dict):
            continue
        bags.append({
            "weight_kg": _num((bag.get("weight") or {}).get("value")),
            "length_cm": _num((bag.get("length") or {}).get("value")),
            "width_cm": _num((bag.get("width") or {}).get("value")),
            "height_cm": _num((bag.get("height") or {}).get("value")),
            "dimensions_sum_cm": _num((bag.get("dimensionsSum") or {}).get("value")),
        })
    return {"price": _money(tier.get("tierPrice")), "included_count": tier.get("included"), "bags": bags}


def _bags(info):
    if not isinstance(info, dict):
        return None
    return {
        "included": {
            "personal_item": info.get("includedPersonalItem"),
            "hand_bag": info.get("includedHandBags"),
            "checked_bag": info.get("includedCheckedBags"),
        },
        "has_bag_options": info.get("hasBagsSupported"),
        "has_no_checked_bag_option": info.get("hasNoCheckedBags"),
        "personal_item_tiers": [_tier(t) for t in info.get("personalItemTiers") or [] if isinstance(t, dict)],
        "hand_bag_tiers": [_tier(t) for t in info.get("handBagTiers") or [] if isinstance(t, dict)],
        "checked_bag_tiers": [_tier(t) for t in info.get("checkedBagTiers") or [] if isinstance(t, dict)],
    }


def _provider(provider):
    if not isinstance(provider, dict):
        return None
    content = provider.get("contentProvider") or {}
    return {
        "code": provider.get("code"),
        "name": provider.get("name"),
        "category": _lower(provider.get("providerCategory")),
        "content_provider": content.get("name") or content.get("code"),
        "has_high_probability_of_price_change": provider.get("hasHighProbabilityOfPriceChange"),
    }


def _booking_options(options):
    out = []
    for edge in ((options or {}).get("edges") or []):
        node = (edge or {}).get("node") or {}
        out.append({
            "provider": _provider(node.get("itineraryProvider")),
            "price": _money(node.get("price")),
            "price_eur": _num((node.get("priceEur") or {}).get("amount")),
            "booking_link": booking_link(node.get("bookingUrl")),
            "has_us_rules": node.get("usRulesApply"),
        })
    return out


def parse_itinerary(itinerary):
    """One Itinerary (OneWay / Return / Multicity / Nomad) -> a flat record."""
    if not isinstance(itinerary, dict):
        return None
    kind = itinerary.get("__typename") or ""
    hack = itinerary.get("travelHack") or {}
    highlights = itinerary.get("highlights") or {}
    fares = itinerary.get("extendedFareOptionsPricing") or {}
    options = _booking_options(itinerary.get("bookingOptions"))
    record = {
        "id": itinerary.get("shareId"),
        "trip_type": {"ItineraryOneWay": "one_way", "ItineraryReturn": "round_trip",
                      "ItineraryMulticity": "multi_city", "ItineraryNomad": "nomad"}.get(kind),
        "booking_link": options[0]["booking_link"] if options else None,
        "price": _money(itinerary.get("price")),
        "price_eur": _num((itinerary.get("priceEur") or {}).get("amount")),
        "fares": {
            "standard": _money(fares.get("standardFarePrice")),
            "flexi": _money(fares.get("flexiFarePrice")),
        },
        "kiwi_guarantee_price": _money(itinerary.get("paidGuaranteePrice")),
        "duration_minutes": _minutes(itinerary.get("duration")),
        "tickets_count": itinerary.get("pnrCount"),
        "cabin_classes": [_lower(c) for c in itinerary.get("cabinClasses") or [] if c],
        "seats_left": (itinerary.get("lastAvailable") or {}).get("seatsLeft"),
        "is_best": highlights.get("isBest"),
        "is_cheapest": highlights.get("isCheapest"),
        "is_fastest": highlights.get("isFastest"),
        "is_self_transfer": hack.get("isVirtualInterlining"),
        "is_hidden_city": hack.get("isTrueHiddenCity"),
        "is_throwaway_ticket": hack.get("isThrowawayTicket"),
        "is_travel_hack": itinerary.get("isTravelHack"),
        "technical_stops_count": itinerary.get("technicalStops"),
        "provider": _provider(itinerary.get("provider")),
        "bags": _bags(itinerary.get("bagsInfo")),
        "booking_options": options,
    }
    if kind == "ItineraryReturn":
        record["outbound"] = parse_sector(itinerary.get("outbound"))
        record["inbound"] = parse_sector(itinerary.get("inbound"))
        record["stay"] = _stay(itinerary.get("stopover"))
    elif kind in ("ItineraryMulticity", "ItineraryNomad"):
        sectors = [parse_sector(s) for s in itinerary.get("sectors") or [] if isinstance(s, dict)]
        stays = itinerary.get("stopovers") or []
        for index, sector in enumerate(sectors):
            if sector and index < len(stays):
                sector["stay_after"] = _stay(stays[index])
        record["legs"] = sectors
    else:
        record["outbound"] = parse_sector(itinerary.get("sector"))
    return record


def _histogram(items):
    if not isinstance(items, list):
        return None
    return [{"range": i.get("label"), "count": i.get("frequency")} for i in items if isinstance(i, dict)]


def _range(value):
    if not isinstance(value, dict):
        return None
    return {"min": value.get("start"), "max": value.get("end")}


def parse_search_summary(metadata):
    """The result-set facets the site draws its filter panel from."""
    metadata = metadata or {}
    prices = metadata.get("prices") or {}
    airlines = [parse_carrier(c) for c in metadata.get("carriers") or [] if isinstance(c, dict)]
    stations = [parse_station(s) for s in metadata.get("stations") or [] if isinstance(s, dict)]
    return {
        "price_histogram": _histogram(prices.get("items")),
        "duration_histogram": _histogram(metadata.get("durations")),
        "layover_hours_histogram": _histogram(metadata.get("stopoverTimes")),
        "outbound_departure_hours_histogram": _histogram(metadata.get("outboundDepartureTimes")),
        "outbound_arrival_hours_histogram": _histogram(metadata.get("outboundArrivalTimes")),
        "inbound_departure_hours_histogram": _histogram(metadata.get("inboundDepartureTimes")),
        "inbound_arrival_hours_histogram": _histogram(metadata.get("inboundArrivalTimes")),
        "stops_range": _range(metadata.get("stopoverCountRange")),
        "layover_hours_range": _range(metadata.get("stopoverDurationRange")),
        "has_overnight_layover": metadata.get("hasOvernightStopover"),
        "has_checked_bag_options": metadata.get("hasCheckedBaggage"),
        "has_cabin_bag_options": metadata.get("hasCabinBaggage"),
        "transport_types": [_lower(t) for t in metadata.get("transportTypes") or [] if t],
        "outbound_days": [_lower(d) for d in metadata.get("outboundDays") or [] if d],
        "inbound_days": [_lower(d) for d in metadata.get("inboundDays") or [] if d],
        "airlines": [{"code": a["code"], "name": a["name"]} for a in airlines if a],
        "airports": [{"code": s["code"], "name": s["name"], "city": (s.get("city") or {}).get("name"),
                      "country": (s.get("country") or {}).get("code")} for s in stations if s],
        "layover_countries": [_country_brief(c) for c in metadata.get("stopoverCountries") or [] if isinstance(c, dict)],
        "missing_providers": [(p or {}).get("code") for p in metadata.get("missingProviders") or [] if p],
    }


def parse_itineraries(payload):
    """An Itineraries payload -> {total_count, has_more_pending, summary,
    results, shared_itinerary}. An empty set is not an error (the engine
    answers a past date or an unknown place with 0 rows)."""
    payload = payload or {}
    metadata = payload.get("metadata") or {}
    results = [parse_itinerary(it) for it in payload.get("itineraries") or [] if isinstance(it, dict)]
    return {
        "total_count": metadata.get("itinerariesCount"),
        "has_more_pending": metadata.get("hasMorePending"),
        "summary": parse_search_summary(metadata),
        "results": [r for r in results if r],
        "shared_itinerary": parse_itinerary(metadata.get("sharedItinerary")),
    }


# ---- explore (one per city) ---------------------------------------------------------

def _window(value):
    if not isinstance(value, dict):
        return None
    return {"from": _date(value.get("start")), "to": _date(value.get("end"))}


def parse_explore(payload):
    """OnePerCityItineraries -> rows of the cheapest fare per destination city."""
    rows = []
    for it in (payload or {}).get("itineraries") or []:
        if not isinstance(it, dict):
            continue
        source, destination = _stop(it.get("source")), _stop(it.get("destination"))
        rows.append({
            "destination": (destination or {}).get("station"),
            "origin": (source or {}).get("station"),
            "price": _money(it.get("price")),
            "price_eur": _num((it.get("priceEur") or {}).get("amount")),
            "discount_percent": _num(it.get("discountPercentage")),
            "departure_time": it.get("departureDate"),
            "return_time": it.get("returnDate"),
            "nights": it.get("nightsCount"),
            "departure_window": _window(it.get("outboundDepartureDateRange")),
            "return_window": _window(it.get("inboundDepartureDateRange")),
        })
    return rows


# ---- places -------------------------------------------------------------------------

def parse_place(node, distance_m=None):
    """One Place node (any concrete type) -> a record; `type` names it."""
    if not isinstance(node, dict):
        return None
    kind = node.get("__typename")
    record = {
        "id": node.get("id"),
        "type": _PLACE_TYPES.get(kind, _lower(kind)),
        "name": node.get("name"),
        "code": node.get("code"),
        "slug": node.get("slug"),
        "rank": node.get("rank"),
    }
    if kind == "Station":
        record["type"] = _STATION_TYPES.get(node.get("type"), "station")
        record["icao"] = node.get("icao")
    record["city"] = _city_brief(node.get("city")) if kind == "Station" else None
    record["country"] = _country_brief(node.get("country")) if "country" in node else None
    if kind == "City":
        subdivision = node.get("subdivision") or {}
        region = node.get("region") or {}
        record["subdivision"] = _brief(subdivision, "name", "code")
        record["region"] = _brief(region, "name", "slug")
        record["airports_count"] = node.get("airportsCount")
        record["ground_stations_count"] = node.get("groundStationsCount")
        record["tags"] = node.get("tags") or []
        stations = node.get("stations")
        if isinstance(stations, dict):
            record["stations"] = [parse_station(e.get("node")) for e in stations.get("edges") or []
                                  if isinstance(e, dict) and isinstance(e.get("node"), dict)]
    if kind == "Country":
        record["region"] = _brief(node.get("region") or {}, "name", "slug")
        record["major_city"] = _city_brief(node.get("majorCity"))
    if kind == "Region":
        record["continent"] = _brief(node.get("continent") or {}, "name", "slug")
    if kind == "Subdivision":
        record["subdivision_code"] = node.get("code")
    record["timezone"] = node.get("timezone")
    record["gps"] = _gps(node.get("gps"))
    if distance_m is not None:
        record["distance_km"] = round(distance_m / 1000, 1)
    return record


def parse_places(payload):
    """PlaceConnection -> [place records] (edge distance folded in)."""
    rows = []
    for edge in (payload or {}).get("edges") or []:
        if not isinstance(edge, dict):
            continue
        distance = edge.get("distance") or {}
        meters = distance.get("distance") if isinstance(distance, dict) else None
        place = parse_place(edge.get("node"), distance_m=meters)
        if place:
            rows.append(place)
    return rows


def place_brief(place):
    """The short origin / destination echo the search endpoints carry."""
    if not isinstance(place, dict):
        return None
    return {k: place.get(k) for k in ("id", "type", "name", "code", "slug", "city", "country")
            if k in place}


# ---- prices -------------------------------------------------------------------------

def _price_place(node):
    if not isinstance(node, dict):
        return None
    return {"id": node.get("id"), "name": node.get("name"), "code": node.get("code"), "slug": node.get("slug"),
            "city": _city_brief(node.get("city")), "country": _country_brief(node.get("country"))}


def _legend(legend):
    if not isinstance(legend, dict):
        return None
    out = {
        "cheap_below": _money(legend.get("cheapThreshold")),
        "average_below": _money(legend.get("averageThreshold")),
        "expensive_above": _money(legend.get("expensiveThreshold")),
    }
    return out if any(out.values()) else None


def parse_price_calendar(payload):
    """ItineraryPricesCalendar -> {origin, destination, currency, legend, days}."""
    payload = payload or {}
    days = []
    for item in payload.get("calendar") or []:
        if not isinstance(item, dict):
            continue
        rated = item.get("ratedPrice") or {}
        days.append({"date": _date(item.get("date")), "price": _money(rated.get("price")),
                     "rating": _lower(rated.get("rating"))})
    sources, destinations = payload.get("sources") or [], payload.get("destinations") or []
    return {
        "origin": _price_place(sources[0]) if sources else None,
        "destination": _price_place(destinations[0]) if destinations else None,
        "currency": ((payload.get("currency") or {}).get("code") or "").upper() or None,
        "legend": _legend(payload.get("priceRatingLegend")),
        "days": days,
    }


def parse_price_graph(payload):
    payload = payload or {}
    points = []
    for item in payload.get("prices") or []:
        if not isinstance(item, dict):
            continue
        points.append({"date": _date(item.get("date")), "return_date": _date(item.get("inboundDate")),
                       "price": _money(item.get("price")), "rating": _lower(item.get("ratedPrice"))})
    sources, destinations = payload.get("sources") or [], payload.get("destinations") or []
    return {
        "origin": _price_place(sources[0]) if sources else None,
        "destination": _price_place(destinations[0]) if destinations else None,
        "currency": ((payload.get("currency") or {}).get("code") or "").upper() or None,
        "points": points,
    }


def parse_price_table(payload):
    payload = payload or {}
    rows = []
    for item in payload.get("priceList") or []:
        if not isinstance(item, dict):
            continue
        rows.append({"date": _date(item.get("outboundDate")), "return_date": _date(item.get("inboundDate")),
                     "price": _money(item.get("price")), "rating": _lower(item.get("rating"))})
    return {"legend": _legend(payload.get("priceRatingLegend")), "prices": rows}


def parse_price_map(payload):
    payload = payload or {}
    rows = []
    for item in payload.get("map") or []:
        if not isinstance(item, dict):
            continue
        city = item.get("city") or {}
        rows.append({
            "city": {
                "id": city.get("id"), "name": city.get("name"), "code": city.get("code"), "slug": city.get("slug"),
                "country": _country_brief(city.get("country")),
                "region": _brief(city.get("region") or {}, "name", "slug"),
                "timezone": city.get("timezone"), "airports_count": city.get("airportsCount"),
                "tags": city.get("tags") or [], "gps": _gps(city.get("gps")),
            },
            "price": _money(item.get("price")),
            "rank": item.get("rank"),
        })
    sources = payload.get("sources") or []
    return {
        "origin": _price_place(sources[0]) if sources else None,
        "currency": ((payload.get("currency") or {}).get("code") or "").upper() or None,
        "destinations": rows,
    }


# ---- nomad ----------------------------------------------------------------------------

def parse_nomad_trips(payload):
    trips = []
    for trip in (payload or {}).get("trips") or []:
        if not isinstance(trip, dict):
            continue
        continent = trip.get("continent") or {}
        days = trip.get("days") or []
        stops = []
        for index, city in enumerate(trip.get("cities") or []):
            if not isinstance(city, dict):
                continue
            stops.append({
                "city": {
                    "id": city.get("id"), "name": city.get("name"), "code": city.get("code"), "slug": city.get("slug"),
                    "country": _country_brief(city.get("country")),
                    "region": _brief(city.get("region") or {}, "name", "slug"),
                    "timezone": city.get("timezone"), "airports_count": city.get("airportsCount"),
                    "tags": city.get("tags") or [], "gps": _gps(city.get("gps")),
                },
                "days": days[index] if index < len(days) else None,
            })
        trips.append({
            "continent": _brief(continent, "id", "name", "slug"),
            "total_days": sum(d for d in days if isinstance(d, int)) if days else None,
            "stops": stops,
        })
    return trips


# ---- airlines ---------------------------------------------------------------------------

def parse_airlines(rows):
    """GET /airlines list -> [{code, name, is_low_cost}]."""
    out = []
    for row in rows or []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        lcc = row.get("lcc")
        out.append({
            "code": row.get("id"),
            "name": row.get("name"),
            "is_low_cost": None if lcc is None else bool(lcc),
        })
    return out
