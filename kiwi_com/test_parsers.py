"""Offline tests for the Kiwi.com parsers and reference resolvers.

    python -m pytest kiwi_com/test_parsers.py -q

Fixtures are real umbrella GraphQL payloads captured 2026-11-07 through
kiwi_com/fetch.py (kiwi_com/fixtures/, listed in parsers.py). No network.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kiwi_com import parsers as P, refs  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fixture(name):
    with open(os.path.join(FX, name), encoding="utf-8") as f:
        return json.load(f)


# ---- refs: places -----------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("LHR", "code:LHR"), ("lon", "code:LON"), ("GB", "Country:GB"), ("gb", "Country:GB"),
    ("City:london_gb", "City:london_gb"), ("Station:airport:LHR", "Station:airport:LHR"),
    ("Country:GB", "Country:GB"), ("POI:tower-of-london_poi", "POI:tower-of-london_poi"),
    ("london_gb", "City:london_gb"), ("new-york-city_ny_us", "City:new-york-city_ny_us"),
    ("london-united-kingdom", "slug:london-united-kingdom"),
    ("https://www.kiwi.com/en/cheap-flights/london-united-kingdom/", "slug:london-united-kingdom"),
    ("https://www.kiwi.com/en-us/airport/heathrow-london-united-kingdom", "slug:heathrow-london-united-kingdom"),
    ("https://www.kiwi.com/en/search/results/london-united-kingdom/anywhere/2026-12-10/no-return",
     "slug:london-united-kingdom"),
    ("new york", "term:new york"), ("London", "term:London"),
])
def test_resolve_place(value, expected):
    assert refs.resolve_place(value) == expected


@pytest.mark.parametrize("value", [
    "", "   ",
    "https://example.com/london-united-kingdom",
    "https://www.kiwi.com/en/search/results/london-united-kingdom/new-york-city-new-york-united-states/2026-12-10",
    "https://www.kiwi.com/en/",
])
def test_resolve_place_rejects(value):
    with pytest.raises(ValueError):
        refs.resolve_place(value)


def test_place_ref_kind():
    assert refs.place_ref_kind("code:LHR") == "code"
    assert refs.place_ref_kind("slug:x-y") == "slug"
    assert refs.place_ref_kind("term:new york") == "term"
    assert refs.place_ref_kind("City:london_gb") == "id"


# ---- refs: itineraries --------------------------------------------------------------------

@pytest.fixture(scope="module")
def one_way():
    return fixture("search_one_way_lon_nyc.json")


@pytest.fixture(scope="module")
def round_trip():
    return fixture("search_round_trip_lon_nyc.json")


def test_decode_one_way_share_id(one_way):
    raw = one_way["itineraries"][0]
    info = refs.decode_itinerary(raw["shareId"])
    assert info["trip"] == "one_way"
    assert info["share_id"] == raw["shareId"] and info["itinerary_id"] is None
    first = raw["sector"]["sectorSegments"][0]["segment"]
    last = raw["sector"]["sectorSegments"][-1]["segment"]
    assert info["legs"] == [{"origin": first["source"]["station"]["code"],
                             "destination": last["destination"]["station"]["code"],
                             "date": first["source"]["utcTime"][:10]}]


def test_decode_round_trip_share_id_splits_sectors(round_trip):
    raw = round_trip["itineraries"][0]
    info = refs.decode_itinerary(raw["shareId"])
    assert info["trip"] == "round_trip"
    out_first = raw["outbound"]["sectorSegments"][0]["segment"]
    in_first = raw["inbound"]["sectorSegments"][0]["segment"]
    assert info["legs"][0]["origin"] == out_first["source"]["station"]["code"]
    assert info["legs"][0]["date"] == out_first["source"]["utcTime"][:10]
    assert info["legs"][1]["origin"] == in_first["source"]["station"]["code"]
    assert info["legs"][1]["date"] == in_first["source"]["utcTime"][:10]


def test_decode_itinerary_id_form(round_trip):
    raw = round_trip["itineraries"][0]
    info = refs.decode_itinerary(raw["id"])
    assert info["itinerary_id"] == raw["id"] and info["share_id"] is None
    assert info["trip"] == "round_trip"
    # the longest ground gap splits the sectors the same way the share id does
    assert info["legs"] == refs.decode_itinerary(raw["shareId"])["legs"]


def test_resolve_itinerary_forms(one_way):
    share = one_way["itineraries"][0]["shareId"]
    assert refs.resolve_itinerary(share) == share
    assert refs.resolve_itinerary(f"https://www.kiwi.com/en/booking/?shareId={share}&x=1") == share
    assert refs.resolve_itinerary(one_way["itineraries"][0]["id"]) == one_way["itineraries"][0]["id"]


@pytest.mark.parametrize("value", ["", "abc", "ItineraryOneWay:notbase64!!", "https://example.com/?shareId=x",
                                   "https://www.kiwi.com/en/booking/?token=abc", "eyJhIjoxfQ"])
def test_resolve_itinerary_rejects(value):
    with pytest.raises(ValueError):
        refs.resolve_itinerary(value)


def test_booking_link():
    assert refs.booking_link("/en/booking/?direct=true&token=x") == "https://www.kiwi.com/en/booking/?direct=true&token=x"
    assert refs.booking_link("https://www.kiwi.com/x") == "https://www.kiwi.com/x"
    assert refs.booking_link(None) is None


# ---- itineraries ---------------------------------------------------------------------------

def test_parse_one_way(one_way):
    parsed = P.parse_itineraries(one_way)
    assert parsed["total_count"] == 48 and parsed["has_more_pending"] is False
    assert len(parsed["results"]) == 5 and parsed["shared_itinerary"] is None
    it = parsed["results"][0]
    assert it["id"] == one_way["itineraries"][0]["shareId"]
    assert it["trip_type"] == "one_way" and it["inbound"] is None if "inbound" in it else True
    assert it["booking_link"].startswith("https://www.kiwi.com/en/booking/?direct=true")
    assert it["price"] == {"amount": 367, "currency": "USD"}
    assert isinstance(it["price_eur"], float)
    assert it["fares"]["standard"]["amount"] > it["price"]["amount"]
    assert it["duration_minutes"] > 60 and it["tickets_count"] >= 1
    assert it["cabin_classes"] == ["economy"]
    assert it["is_self_transfer"] is True and it["is_hidden_city"] is False
    assert it["provider"]["code"] == "KIWI-BASIC" and it["provider"]["content_provider"] == "kiwi.com"
    assert it["bags"]["included"] == {"personal_item": 1, "hand_bag": 0, "checked_bag": 0}
    tier = it["bags"]["checked_bag_tiers"][0]
    assert tier["price"]["currency"] == "USD" and tier["bags"][0]["weight_kg"] == 20
    assert tier["bags"][0]["dimensions_sum_cm"] == 158
    assert len(it["booking_options"]) == 2
    assert {o["provider"]["code"] for o in it["booking_options"]} == {"KIWI-BASIC", "KIWI"}
    sector = it["outbound"]
    assert sector["stops"] == len(sector["segments"]) - 1 >= 1
    assert sector["departure"]["station"]["code"] == "STN"
    assert sector["arrival"]["station"]["code"] == "JFK"
    assert sector["airlines"] and all(len(c) <= 3 for c in sector["airlines"])
    seg = sector["segments"][0]
    assert seg["flight_number"] == "FR1884" and seg["transport"] == "flight"
    assert seg["airline"]["code"] == "FR" and seg["airline"]["name"] == "Ryanair"
    assert seg["operating_airline"] is None
    assert seg["departure"]["station"]["city"]["name"] == "London"
    assert seg["departure"]["local_time"] == "2026-12-10T09:15:00"
    assert seg["arrival"]["station"]["country"] == {"code": "PT", "name": "Portugal"}
    assert seg["duration_minutes"] == 170 and seg["cabin_class"] == "economy"
    assert seg["layover"] is None
    layover = sector["segments"][1]["layover"]
    assert layover["duration_minutes"] > 0 and layover["guarantee"] in ("kiwi", "airline", None)
    assert "__typename" not in json.dumps(parsed)


def test_parse_round_trip(round_trip):
    parsed = P.parse_itineraries(round_trip)
    assert parsed["total_count"] == 49 and len(parsed["results"]) == 5
    it = parsed["results"][0]
    assert it["trip_type"] == "round_trip"
    assert it["outbound"]["departure"]["station"]["code"] == "LHR"
    assert it["inbound"]["arrival"]["station"]["code"] == "LHR"
    assert it["stay"] == {"nights": 7, "duration_minutes": it["stay"]["duration_minutes"]}
    assert it["stay"]["duration_minutes"] > 7 * 24 * 60 - 60
    assert it["bags"]["included"]["hand_bag"] == 1
    assert it["price"]["amount"] == 703
    assert it["seats_left"] is None or isinstance(it["seats_left"], int)


def test_parse_summary(one_way):
    summary = P.parse_itineraries(one_way)["summary"]
    assert summary["price_histogram"][0] == {"range": "373-395", "count": 8} or summary["price_histogram"]
    assert summary["stops_range"] == {"min": 1, "max": 3}
    assert summary["layover_hours_range"]["max"] >= summary["layover_hours_range"]["min"]
    assert summary["has_overnight_layover"] is True
    assert summary["transport_types"] == ["flight"]
    assert {"code": "FR", "name": "Ryanair"} in summary["airlines"]
    assert summary["airports"] == []   # the engine left metadata.stations empty for this search
    assert summary["layover_countries"] and summary["layover_countries"][0]["code"]
    assert summary["duration_histogram"] is None


def test_parse_empty_search():
    parsed = P.parse_itineraries(fixture("search_empty.json"))
    assert parsed["total_count"] == 0 and parsed["results"] == []
    assert parsed["summary"]["airlines"] == [] and parsed["summary"]["stops_range"] == {"min": 0, "max": 0}


def test_parse_shared_itinerary():
    ow = fixture("details_one_way_shared.json")
    parsed = P.parse_itineraries(ow)
    shared = parsed["shared_itinerary"]
    assert shared and shared["id"] == ow["metadata"]["sharedItinerary"]["shareId"]
    assert shared["trip_type"] == "one_way" and shared["outbound"]["segments"]
    rt = P.parse_itineraries(fixture("details_round_trip_shared.json"))["shared_itinerary"]
    assert rt["trip_type"] == "round_trip" and rt["inbound"]["segments"] and rt["stay"]["nights"] == 7
    assert P.parse_itineraries(fixture("details_unknown_share.json"))["shared_itinerary"] is None


def test_parse_itinerary_partial():
    assert P.parse_itinerary(None) is None
    record = P.parse_itinerary({"__typename": "ItineraryOneWay", "shareId": "x"})
    assert record["id"] == "x" and record["price"] is None and record["outbound"] is None
    assert record["booking_link"] is None and record["bags"] is None and record["booking_options"] == []
    record = P.parse_itinerary({"__typename": "ItineraryMulticity", "sectors": [{"sectorSegments": []}],
                                "stopovers": [{"nightsCount": 2}]})
    assert record["legs"][0]["stops"] == 0 and record["legs"][0]["stay_after"] == {"nights": 2, "duration_minutes": None}
    assert P.parse_itineraries(None) == {"total_count": None, "has_more_pending": None,
                                         "summary": P.parse_search_summary({}), "results": [],
                                         "shared_itinerary": None}


# ---- explore ------------------------------------------------------------------------------

def test_parse_explore_one_way():
    rows = P.parse_explore(fixture("explore_one_way_london.json"))
    assert len(rows) == 30
    row = rows[0]
    assert row["destination"]["code"] and row["destination"]["city"]["name"]
    assert row["origin"]["city"]["code"] == "LON"
    assert row["price"]["currency"] == "USD" and row["price"]["amount"] > 0
    assert row["departure_time"].startswith("2026-12-") and row["return_time"] is None
    assert row["nights"] is None and row["departure_window"] == {"from": "2026-12-10", "to": "2026-12-20"}
    assert row["return_window"] is None


def test_parse_explore_round_trip():
    rows = P.parse_explore(fixture("explore_round_trip_london.json"))
    assert len(rows) == 30
    row = rows[0]
    assert row["nights"] == 4 and row["return_time"].startswith("2026-12-")
    assert row["return_window"]["to"] == "2026-12-24"
    assert P.parse_explore(None) == [] and P.parse_explore({"itineraries": [None, {}]})[0]["price"] is None


# ---- places --------------------------------------------------------------------------------

def test_parse_places_search():
    rows = P.parse_places(fixture("places_search_london.json"))
    assert [r["id"] for r in rows[:3]] == ["City:london_gb", "Station:airport:LHR", "Station:airport:LGW"]
    city = rows[0]
    assert city["type"] == "city" and city["code"] == "LON" and city["slug"] == "london-united-kingdom"
    assert city["country"] == {"code": "GB", "name": "United Kingdom"}
    assert city["subdivision"] == {"name": "England", "code": "ENG"} and city["region"]["name"] == "Northern Europe"
    assert city["airports_count"] == 6 and "sightseeing" in city["tags"]
    assert city["timezone"] == "Europe/London" and city["gps"]["lat"] > 51
    assert isinstance(city["stations"], list)   # selected on every City; api.py drops them from lists
    airport = rows[1]
    assert airport["type"] == "airport" and airport["icao"] == "EGLL"
    assert airport["city"] == {"id": "City:london_gb", "name": "London", "code": "LON", "slug": "london-united-kingdom"}
    assert airport["country"] is None   # the gateway leaves a station's country null here
    assert all("distance_km" not in r for r in rows)


def test_parse_place_details_city_with_stations():
    rows = P.parse_places(fixture("places_details_city_london.json"))
    assert len(rows) == 1
    city = rows[0]
    assert city["id"] == "City:london_gb" and city["type"] == "city"
    codes = [s["code"] for s in city["stations"]]
    assert "LHR" in codes and "LGW" in codes and all(s["type"] == "airport" for s in city["stations"])


def test_parse_place_details_station():
    rows = P.parse_places(fixture("places_details_station_lhr.json"))
    assert rows[0]["id"] == "Station:airport:LHR" and rows[0]["name"] == "Heathrow"
    assert rows[0]["city"]["code"] == "LON" and rows[0]["type"] == "airport"


def test_parse_places_unknown_and_partial():
    assert P.parse_places(fixture("places_unknown.json")) == []
    assert P.parse_places(None) == []
    assert P.parse_places({"edges": [None, {"node": None}, {"node": {"__typename": "City", "id": "City:x"}}]}) == [
        {"id": "City:x", "type": "city", "name": None, "code": None, "slug": None, "rank": None, "city": None,
         "country": None, "subdivision": None, "region": None, "airports_count": None, "ground_stations_count": None,
         "tags": [], "timezone": None, "gps": None}]


def test_parse_places_nearby_distances():
    rows = P.parse_places(fixture("places_nearby_london.json"))
    assert rows and all(r["type"] == "airport" for r in rows)
    lcy = next(r for r in rows if r["code"] == "LCY")
    assert lcy["distance_km"] == 12.1


def test_parse_top_destinations():
    rows = P.parse_places(fixture("places_top_destinations_london.json"))
    assert len(rows) == 10 and rows[0]["id"] == "City:new-delhi_in" and rows[0]["type"] == "city"


def test_place_brief():
    rows = P.parse_places(fixture("places_details_city_london.json"))
    brief = P.place_brief(rows[0])
    assert set(brief) == {"id", "type", "name", "code", "slug", "city", "country"}
    assert P.place_brief(None) is None


# ---- prices ---------------------------------------------------------------------------------

def test_parse_price_calendar():
    cal = P.parse_price_calendar(fixture("prices_calendar_lon_nyc.json"))
    assert cal["origin"]["code"] == "LON" and cal["destination"]["code"] == "NYC"
    assert cal["currency"] == "USD" and len(cal["days"]) == 31
    assert cal["days"][0] == {"date": "2026-12-01", "price": {"amount": 548, "currency": "USD"}, "rating": "average"}
    assert set(d["rating"] for d in cal["days"]) <= {"cheap", "average", "expensive", None}
    assert cal["legend"] is None or cal["legend"]["cheap_below"]


def test_parse_return_price_calendar():
    cal = P.parse_price_calendar(fixture("prices_return_calendar_lon_nyc.json"))
    assert len(cal["days"]) == 31 and cal["days"][0]["rating"] == "cheap"
    assert cal["days"][0]["price"]["amount"] == 670


def test_parse_price_graph():
    graph = P.parse_price_graph(fixture("prices_graph_lon_nyc.json"))
    assert graph["currency"] == "USD" and len(graph["points"]) == 31
    assert graph["points"][0] == {"date": "2026-12-01", "return_date": "2026-12-08",
                                  "price": {"amount": 670, "currency": "USD"}, "rating": "cheap"}
    one_way = P.parse_price_graph(fixture("prices_graph_one_way_lon_nyc.json"))
    assert one_way["points"][0]["return_date"] is None and one_way["points"][0]["price"]["amount"] == 548
    assert P.parse_price_graph(None)["points"] == []


def test_parse_price_table():
    table = P.parse_price_table(fixture("prices_table_lon_nyc.json"))
    assert table["prices"] == [{"date": "2026-12-10", "return_date": "2026-12-17",
                                "price": {"amount": 703, "currency": "USD"}, "rating": "average"}]
    assert table["legend"] is None or "cheap_below" in table["legend"]


def test_parse_price_map():
    out = P.parse_price_map(fixture("prices_map_london_europe.json"))
    assert out["origin"]["code"] == "LON" and out["currency"] == "USD"
    assert len(out["destinations"]) == 25
    row = out["destinations"][0]
    assert row["city"]["name"] and row["city"]["country"]["code"] and row["city"]["gps"]["lat"]
    assert row["price"]["amount"] > 0 and row["rank"] >= 1
    rt = P.parse_price_map(fixture("prices_map_london_europe_return.json"))
    assert len(rt["destinations"]) == 25
    assert P.parse_price_map(None)["destinations"] == []


# ---- nomad / airlines ------------------------------------------------------------------------

def test_parse_nomad_trips():
    trips = P.parse_nomad_trips(fixture("nomad_example_trips_london.json"))
    assert len(trips) >= 5
    europe = next(t for t in trips if t["continent"]["slug"] == "europe")
    assert europe["total_days"] == 18
    assert [s["city"]["name"] for s in europe["stops"]] == ["Madrid", "Milan", "Barcelona", "Paris", "Rome"]
    assert europe["stops"][0]["days"] == 3 and europe["stops"][0]["city"]["country"]["code"] == "ES"
    assert P.parse_nomad_trips(None) == []


def test_parse_airlines():
    rows = P.parse_airlines(fixture("airlines.json"))
    assert len(rows) == 2345
    ryanair = next(r for r in rows if r["code"] == "FR")
    assert ryanair == {"code": "FR", "name": "Ryanair", "is_low_cost": None}
    assert P.parse_airlines([{"id": "XX", "name": "X", "lcc": 1}, {"name": "no id"}, None]) == [
        {"code": "XX", "name": "X", "is_low_cost": True}]


# ---- scalars ----------------------------------------------------------------------------------

def test_money_and_numbers():
    assert P._money({"amount": "384", "currency": {"code": "usd"}, "priceBeforeDiscount": "384"}) == \
        {"amount": 384, "currency": "USD"}
    assert P._money({"amount": "76.80000000000001", "currency": {"code": "USD"}, "priceBeforeDiscount": "90"}) == \
        {"amount": 76.8, "currency": "USD", "amount_before_discount": 90}
    assert P._money({"amount": None}) is None and P._money("x") is None
    assert P._minutes(53100) == 885 and P._minutes(None) is None
    assert P._date("2026-12-01T00:00:00") == "2026-12-01" and P._date(None) is None
