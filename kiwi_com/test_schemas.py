"""Offline tests for the /kiwi-com/* request schemas.

    python -m pytest kiwi_com/test_schemas.py -q
"""
import json
import os
import sys
from datetime import date, timedelta

import pytest
from marshmallow import ValidationError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kiwi_com import schemas  # noqa: E402

TOMORROW = (date.today() + timedelta(days=1)).isoformat()
D1 = (date.today() + timedelta(days=30)).isoformat()
D2 = (date.today() + timedelta(days=37)).isoformat()
PAST = (date.today() - timedelta(days=1)).isoformat()


def load(schema, **params):
    return schema().load(params)


def rejects(schema, field, **params):
    with pytest.raises(ValidationError) as info:
        schema().load(params)
    assert field in info.value.normalized_messages(), info.value.normalized_messages()
    return info.value.normalized_messages()[field]


# ---- one way ----------------------------------------------------------------------------

def test_one_way_defaults_and_place_forms():
    data = load(schemas.OneWaySearchSchema, origin="lhr", destination="City:new-york-city_ny_us", date=D1)
    assert data["origin"] == "code:LHR" and data["destination"] == "City:new-york-city_ny_us"
    assert data["date"] == D1 and data["date_to"] is None
    assert data["adults"] == 1 and data["children"] == 0 and data["infants"] == 0
    assert data["cabin_class"] == "economy" and data["sort"] == "quality" and data["limit"] == 20
    assert data["currency"] == "USD" and data["locale"] == "en" and data["market"] == "US"
    assert data["self_transfer"] is True and data["travel_hacks"] is True and data["transport"] is None
    link = "https://www.kiwi.com/en/cheap-flights/london-united-kingdom/"
    assert load(schemas.OneWaySearchSchema, origin=link, destination="new york", date=D1)["origin"] == \
        "slug:london-united-kingdom"
    # the date is optional: tomorrow, so a spec example never goes stale
    assert load(schemas.OneWaySearchSchema, origin="JFK", destination="LHR")["date"] == TOMORROW


def test_one_way_filters():
    data = load(schemas.OneWaySearchSchema, origin="LON", destination="NYC", date=D1, date_to=D2,
                adults="2", children="1", infants="1", cabin_class="Business", hand_bags="1", checked_bags="2",
                max_stops="1", max_duration_hours="20", max_price="800", max_layover_hours="5",
                departure_hours="6-12", arrival_hours="12-23", airlines="ba, fr", exclude_airlines="w9",
                transport="flight,train", self_transfer="no", travel_hacks="0", sort="price", limit="50",
                currency="eur", locale="DE", market="de")
    assert data["cabin_class"] == "business" and data["sort"] == "price"
    assert data["departure_hours"] == {"start": 6, "end": 12} and data["arrival_hours"] == {"start": 12, "end": 23}
    assert data["airlines"] == ["BA", "FR"] and data["exclude_airlines"] == ["W9"]
    assert data["transport"] == ["FLIGHT", "TRAIN"]
    assert data["self_transfer"] is False and data["travel_hacks"] is False
    assert data["max_price"] == 800.0 and data["max_stops"] == 1
    assert data["currency"] == "EUR" and data["locale"] == "de" and data["market"] == "DE"


@pytest.mark.parametrize("field,params", [
    ("origin", {"destination": "NYC", "date": D1}),
    ("date", {"origin": "LON", "destination": "NYC", "date": PAST}),
    ("date", {"origin": "LON", "destination": "NYC", "date": "12/10/2026"}),
    ("date_to", {"origin": "LON", "destination": "NYC", "date": D2, "date_to": D1}),
    ("date_to", {"origin": "LON", "destination": "NYC", "date": D1,
                 "date_to": (date.today() + timedelta(days=200)).isoformat()}),
    ("cabin_class", {"origin": "LON", "destination": "NYC", "date": D1, "cabin_class": "coach"}),
    ("max_stops", {"origin": "LON", "destination": "NYC", "date": D1, "max_stops": "5"}),
    ("departure_hours", {"origin": "LON", "destination": "NYC", "date": D1, "departure_hours": "12-6"}),
    ("departure_hours", {"origin": "LON", "destination": "NYC", "date": D1, "departure_hours": "morning"}),
    ("infants", {"origin": "LON", "destination": "NYC", "date": D1, "adults": "1", "infants": "2"}),
    ("transport", {"origin": "LON", "destination": "NYC", "date": D1, "transport": "boat"}),
    ("sort", {"origin": "LON", "destination": "NYC", "date": D1, "sort": "best"}),
    ("limit", {"origin": "LON", "destination": "NYC", "date": D1, "limit": "101"}),
    ("currency", {"origin": "LON", "destination": "NYC", "date": D1, "currency": "dollars"}),
    ("market", {"origin": "LON", "destination": "NYC", "date": D1, "market": "XX"}),
    ("origin", {"origin": "https://example.com/x", "destination": "NYC", "date": D1}),
    ("url", {"origin": "LON", "destination": "NYC", "date": D1, "url": "x"}),
])
def test_one_way_rejects(field, params):
    rejects(schemas.OneWaySearchSchema, field, **params)


def test_one_way_link_with_two_places_is_rejected():
    link = "https://www.kiwi.com/en/search/results/london-united-kingdom/new-york-city-new-york-united-states/2026-12-10"
    message = rejects(schemas.OneWaySearchSchema, "origin", origin=link, destination="NYC", date=D1)
    assert "several places" in message[0]


# ---- round trip ------------------------------------------------------------------------

def test_round_trip_return_date_or_nights():
    data = load(schemas.RoundTripSearchSchema, origin="LON", destination="NYC", date=D1, return_date=D2)
    assert data["return_date"] == D2 and data["nights_min"] is None
    data = load(schemas.RoundTripSearchSchema, origin="LON", destination="NYC", date=D1, nights_min="3", nights_max="7")
    assert data["nights_min"] == 3 and data["nights_max"] == 7 and data["return_date"] is None
    # neither return_date nor nights: a week after the departure date
    assert load(schemas.RoundTripSearchSchema, origin="LON", destination="NYC", date=D1)["return_date"] == \
        (date.fromisoformat(D1) + timedelta(days=7)).isoformat()
    data = load(schemas.RoundTripSearchSchema, origin="JFK", destination="LHR")
    assert data["date"] == TOMORROW and data["return_date"] == (date.today() + timedelta(days=8)).isoformat()
    rejects(schemas.RoundTripSearchSchema, "return_date_to", origin="LON", destination="NYC", date=D1,
            return_date_to=D2)
    rejects(schemas.RoundTripSearchSchema, "nights_max", origin="LON", destination="NYC", date=D1,
            nights_max="3")
    rejects(schemas.RoundTripSearchSchema, "return_date", origin="LON", destination="NYC", date=D1,
            return_date=D2, nights_min="3")
    rejects(schemas.RoundTripSearchSchema, "return_date", origin="LON", destination="NYC", date=D2, return_date=D1)
    rejects(schemas.RoundTripSearchSchema, "nights_max", origin="LON", destination="NYC", date=D1,
            nights_min="7", nights_max="3")


# ---- multi city --------------------------------------------------------------------------

def test_multi_city_legs():
    legs = json.dumps([{"origin": "LON", "destination": "new-york-city_ny_us", "date": D1},
                       {"origin": "NYC", "destination": "LAX", "date": D2}])
    data = load(schemas.MultiCitySearchSchema, legs=legs)
    assert json.loads(data["legs"]) == [
        {"date": D1, "destination": "City:new-york-city_ny_us", "origin": "code:LON"},
        {"date": D2, "destination": "code:LAX", "origin": "code:NYC"}]
    assert data["limit"] == 20


def test_multi_city_leg_dates_default():
    legs = json.dumps([{"origin": "NYC", "destination": "LON"}, {"origin": "LON", "destination": "PAR"},
                       {"origin": "PAR", "destination": "NYC", "date": D2}])
    loaded = json.loads(load(schemas.MultiCitySearchSchema, legs=legs)["legs"])
    assert [leg["date"] for leg in loaded] == [TOMORROW, (date.today() + timedelta(days=4)).isoformat(), D2]


@pytest.mark.parametrize("legs", [
    "", "not json", "[]", json.dumps([{"origin": "LON", "destination": "NYC", "date": D1}]),
    json.dumps([{"origin": "LON"}, {"origin": "NYC", "destination": "LAX", "date": D2}]),
    json.dumps([{"origin": "LON", "destination": "NYC", "date": D2}, {"origin": "NYC", "destination": "LAX"},
                {"origin": "LAX", "destination": "NYC", "date": D1}]),
    json.dumps([{"origin": "LON", "destination": "NYC", "date": PAST}, {"origin": "NYC", "destination": "LAX", "date": D2}]),
    json.dumps([{"origin": "LON", "destination": "NYC", "date": D2}, {"origin": "NYC", "destination": "LAX", "date": D1}]),
    json.dumps([{"origin": "https://example.com", "destination": "NYC", "date": D1}, {"origin": "NYC", "destination": "LAX", "date": D2}]),
    json.dumps([{"origin": "LON", "destination": "NYC", "date": D1}] * 7),
])
def test_multi_city_rejects(legs):
    rejects(schemas.MultiCitySearchSchema, "legs", legs=legs)


# ---- details ---------------------------------------------------------------------------

SHARE = ("eyJyIjoiVzk6NTM3NzpMVE46MTc5NjkwNDkwMDpCQ046MTc5NjkxMjcwMDplY29ub215OkZhbHNlOjo6fExMOjI2Mjc6QkNO"
         "OjE3OTY5MjQ3MDA6SkZLOjE3OTY5NTgwMDA6ZWNvbm9teTpUcnVlOjpUcnVlOkxMIiwiaSI6WyIxOGZmMDFhZjUxM2QwMDAw"
         "ZTIyOGUwMjdfMHwwMWFmMjBhMTUxM2QwMDAwYmFjYzczMDJfMCJdLCJ0aGMiOjAsInRhdCI6MCwidmkiOjEsInQiOiJPVyJ9")


def test_details_schema():
    data = load(schemas.ItineraryDetailsSchema, itinerary=SHARE)
    assert data["itinerary"] == SHARE and data["adults"] == 1 and data["cabin_class"] == "economy"
    link = f"https://www.kiwi.com/en/booking/?shareId={SHARE}"
    assert load(schemas.ItineraryDetailsSchema, itinerary=link)["itinerary"] == SHARE
    message = rejects(schemas.ItineraryDetailsSchema, "itinerary", itinerary="abc")
    assert "share id" in message[0]
    rejects(schemas.ItineraryDetailsSchema, "itinerary", itinerary="https://www.kiwi.com/en/booking/?token=x")
    rejects(schemas.ItineraryDetailsSchema, "adults", itinerary=SHARE, adults="0")


# ---- explore -----------------------------------------------------------------------------

def test_explore_schema():
    data = load(schemas.ExploreSchema, origin="LON")
    assert data["trip_type"] == "one_way" and data["date_from"] == (date.today() + timedelta(days=1)).isoformat()
    assert data["date_to"] is None and data["nights_min"] == 3 and data["nights_max"] == 7 and data["limit"] == 30
    data = load(schemas.ExploreSchema, origin="LON", trip_type="Round_Trip", date_from=D1, date_to=D2,
                nights_min="2", nights_max="4", max_price="100", max_stops="0", limit="100")
    assert data["trip_type"] == "round_trip" and data["max_stops"] == 0
    rejects(schemas.ExploreSchema, "trip_type", origin="LON", trip_type="nomad")
    rejects(schemas.ExploreSchema, "date_to", origin="LON", date_from=D2, date_to=D1)
    rejects(schemas.ExploreSchema, "nights_max", origin="LON", nights_min="8", nights_max="2")
    rejects(schemas.ExploreSchema, "origin")


# ---- prices --------------------------------------------------------------------------------

def test_price_calendar_schema():
    data = load(schemas.PriceCalendarSchema, origin="LON", destination="NYC")
    assert data["date_from"] == date.today().isoformat() and data["date_to"] is None
    rejects(schemas.PriceCalendarSchema, "date_to", origin="LON", destination="NYC", date_from=D1,
            date_to=(date.today() + timedelta(days=200)).isoformat())
    rejects(schemas.PriceCalendarSchema, "destination", origin="LON")


def test_return_price_calendar_schema():
    data = load(schemas.ReturnPriceCalendarSchema, origin="LON", destination="NYC")
    assert data["nights_min"] == 7 and data["nights_max"] is None
    rejects(schemas.ReturnPriceCalendarSchema, "nights_max", origin="LON", destination="NYC",
            nights_min="7", nights_max="3")


def test_price_graph_schema():
    data = load(schemas.PriceGraphSchema, origin="LON", destination="NYC", date_from=D1)
    assert data["nights_min"] is None
    rejects(schemas.PriceGraphSchema, "nights_max", origin="LON", destination="NYC", nights_max="3")


def test_price_table_schema():
    data = load(schemas.PriceTableSchema, origin="LON", destination="NYC", date=D1, return_date=D2)
    assert data["date"] == D1 and data["return_date"] == D2
    data = load(schemas.PriceTableSchema, origin="LON", destination="NYC", date=D1)
    assert data["return_date"] == (date.fromisoformat(D1) + timedelta(days=7)).isoformat()
    data = load(schemas.PriceTableSchema, origin="LON", destination="NYC")
    assert data["date"] == TOMORROW
    rejects(schemas.PriceTableSchema, "return_date", origin="LON", destination="NYC", date=D2, return_date=D1)


def test_price_map_schema():
    data = load(schemas.PriceMapSchema, origin="LON")
    assert data["bounds"] is None and data["return_anytime"] is False
    data = load(schemas.PriceMapSchema, origin="LON", bounds="35, -10, 60, 30", return_from=D1, return_to=D2)
    assert data["bounds"] == {"sw_lat": 35.0, "sw_lng": -10.0, "ne_lat": 60.0, "ne_lng": 30.0}
    rejects(schemas.PriceMapSchema, "bounds", origin="LON", bounds="1,2,3")
    rejects(schemas.PriceMapSchema, "bounds", origin="LON", bounds="60,-10,35,30")
    rejects(schemas.PriceMapSchema, "bounds", origin="LON", bounds="a,b,c,d")
    rejects(schemas.PriceMapSchema, "return_to", origin="LON", return_to=D2)
    rejects(schemas.PriceMapSchema, "nights_min", origin="LON", return_from=D1, nights_min="3")


# ---- places ----------------------------------------------------------------------------------

def test_places_schemas():
    data = load(schemas.PlacesSearchSchema, query=" lond ", types="Airport,city")
    assert data["query"] == "lond" and data["types"] == ["AIRPORT", "CITY"] and data["limit"] == 10
    rejects(schemas.PlacesSearchSchema, "types", query="x", types="planet")
    rejects(schemas.PlacesSearchSchema, "query")
    assert load(schemas.PlaceDetailsSchema, place="Station:airport:LHR")["place"] == "Station:airport:LHR"
    rejects(schemas.PlaceDetailsSchema, "place", place="")
    data = load(schemas.PlacesNearbySchema, lat="51.5", lng="-0.12")
    assert data["radius_km"] == 100 and data["types"] is None
    rejects(schemas.PlacesNearbySchema, "lat", lat="91", lng="0")
    rejects(schemas.PlacesNearbySchema, "lng", lat="0")
    data = load(schemas.TopDestinationsSchema, place="LON", popularity="Bookings")
    assert data["popularity"] == "bookings"
    assert load(schemas.TopDestinationsSchema, place="LON")["popularity"] == "searches"
    rejects(schemas.TopDestinationsSchema, "popularity", place="LON", popularity="likes")


def test_nomad_and_airlines_schemas():
    assert load(schemas.NomadTripsSchema, origin="london_gb")["origin"] == "City:london_gb"
    assert load(schemas.AirlinesSearchSchema) == {"query": None, "limit": 50}
    assert load(schemas.AirlinesSearchSchema, query="ryan", limit="5") == {"query": "ryan", "limit": 5}
    rejects(schemas.AirlinesSearchSchema, "limit", limit="501")
    rejects(schemas.AirlinesSearchSchema, "page", page="2")
