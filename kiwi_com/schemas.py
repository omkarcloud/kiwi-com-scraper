"""Marshmallow request schemas for every /kiwi-com/* route.

ONE param per input (tripadvisor QueryOrIdField convention, never a sibling
`url` / `id` pair):

    origin / destination / place   an IATA code (LHR, LON, JFK), a Kiwi place
                                   id (City:london_gb, Station:airport:LHR,
                                   Country:GB), a kiwi.com place slug
                                   (london-united-kingdom), a kiwi.com link
                                   with that slug, or a free-text place name
    itinerary                      a share id, an itinerary id
                                   (ItineraryOneWay:…) or a kiwi.com link
                                   carrying ?shareId=

Dates are YYYY-MM-DD, today or later. Money bounds are in the request's
`currency`. Every endpoint takes `currency` (ISO 4217, default USD),
`locale` (ISO 639-1, default en: station / city names come back in that
language) and `market` (ISO 3166-1 country, default US: the storefront
whose fares and booking rules apply).
"""
import json
import re
from datetime import date, datetime, timedelta

from marshmallow import ValidationError, fields, post_load, validate, validates_schema

from schema_fields import (BaseSchema, ChoiceField, CommaListField, CountryCodeField, CurrencyField,
                           DateField, Flag, LanguageCodeField, LimitField, Price, QueryField, RefField,
                           StrippedString)
from kiwi_com import refs

MAX_LEGS = 6
MAX_WINDOW_DAYS = 62      # a flexible departure window (date .. date_to)
MAX_CALENDAR_DAYS = 92    # price calendar / graph span
MAX_EXPLORE_DAYS = 365

# Public choice values (what callers send and responses echo); refs.py maps
# them to the gateway's tokens.
CABIN_CLASSES = list(refs.CABIN_CLASSES)
SORTS = list(refs.SORTS)
POPULARITY = list(refs.POPULARITY)
TRANSPORT = refs.TRANSPORT
PLACE_TYPES = refs.PLACE_TYPES
DEFAULT_RETURN_AFTER_DAYS = 7    # round trips without return_date / nights: a week later
DEFAULT_LEG_GAP_DAYS = 3         # multi-city legs without a date: previous leg + 3 days

_HOURS_RE = re.compile(r"^(\d{1,2})-(\d{1,2})$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---- fields -------------------------------------------------------------------------

class PlaceRefField(RefField):
    resolver = staticmethod(refs.resolve_place)


class ItineraryRefField(RefField):
    resolver = staticmethod(refs.resolve_itinerary)


class HoursRangeField(StrippedString):
    """'6-12' -> {"start": 6, "end": 12} (hours of the day, 0-24)."""

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        if value is None:
            return None
        match = _HOURS_RE.match(value.replace(" ", ""))
        if not match:
            raise ValidationError("Must be an hour range like 6-12 (0-24).")
        start, end = int(match.group(1)), int(match.group(2))
        if not (0 <= start < end <= 24):
            raise ValidationError("Must be an hour range like 6-12 with start before end (0-24).")
        return {"start": start, "end": end}


class LegsField(StrippedString):
    """Multi-city legs as a JSON array of {origin, destination, date} objects
    (2-6 of them, in travel order; date YYYY-MM-DD, today or later). `date`
    is optional per leg: the first leg defaults to tomorrow, every later one
    to the previous leg + 3 days, so an example never goes stale. Each
    origin / destination takes any place form. Re-serialized canonically so
    equivalent spellings share one cache row."""

    def __init__(self, **kwargs):
        kwargs.setdefault("required", True)
        kwargs.setdefault("validate", validate.Length(max=4000))
        super().__init__(**kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs) or ""
        try:
            legs = json.loads(value)
        except ValueError:
            raise ValidationError('Must be a JSON array, e.g. [{"origin":"LON","destination":"NYC",'
                                  '"date":"2026-12-10"},{"origin":"NYC","destination":"LAX","date":"2026-12-15"}].')
        if not isinstance(legs, list) or not 2 <= len(legs) <= MAX_LEGS:
            raise ValidationError(f"Must be a JSON array of 2 to {MAX_LEGS} legs.")
        cleaned, previous = [], None
        for index, leg in enumerate(legs, start=1):
            if not isinstance(leg, dict):
                raise ValidationError(f"Leg {index} must be an object with origin, destination and date.")
            origin = str(leg.get("origin") or "").strip()
            destination = str(leg.get("destination") or "").strip()
            leg_date = str(leg.get("date") or "").strip()
            if not origin or not destination:
                raise ValidationError(f"Leg {index} needs origin and destination.")
            if not leg_date:
                leg_date = (_plus_days(previous, DEFAULT_LEG_GAP_DAYS) if previous
                            else (date.today() + timedelta(days=1)).isoformat())
            try:
                origin, destination = refs.resolve_place(origin), refs.resolve_place(destination)
            except ValueError as e:
                raise ValidationError(f"Leg {index}: {e}")
            if not _DATE_RE.match(leg_date):
                raise ValidationError(f"Leg {index} date must be YYYY-MM-DD.")
            try:
                parsed = datetime.strptime(leg_date, "%Y-%m-%d").date()
            except ValueError:
                raise ValidationError(f"Leg {index} date is not a real calendar date.")
            if parsed < date.today():
                raise ValidationError(f"Leg {index} date is in the past.")
            if previous and leg_date < previous:
                raise ValidationError(f"Leg {index} date must be on or after leg {index - 1}'s.")
            previous = leg_date
            cleaned.append({"origin": origin, "destination": destination, "date": leg_date})
        return json.dumps(cleaned, sort_keys=True, separators=(",", ":"))


class BoundsField(StrippedString):
    """'sw_lat,sw_lng,ne_lat,ne_lng' -> a geo box dict; default the world."""

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        if value is None:
            return None
        parts = [p.strip() for p in value.split(",")]
        if len(parts) != 4:
            raise ValidationError("Must be four numbers: sw_lat,sw_lng,ne_lat,ne_lng (e.g. 35,-10,60,30).")
        try:
            sw_lat, sw_lng, ne_lat, ne_lng = (float(p) for p in parts)
        except ValueError:
            raise ValidationError("Must be four numbers: sw_lat,sw_lng,ne_lat,ne_lng (e.g. 35,-10,60,30).")
        if not (-90 <= sw_lat < ne_lat <= 90 and -180 <= sw_lng < ne_lng <= 180):
            raise ValidationError("Latitudes must be within -90..90 and longitudes within -180..180, "
                                  "with the south-west corner below and left of the north-east one.")
        return {"sw_lat": sw_lat, "sw_lng": sw_lng, "ne_lat": ne_lat, "ne_lng": ne_lng}


class IntField(fields.Integer):
    """Optional integer with a default and inclusive bounds."""

    def __init__(self, default=None, min_value=None, max_value=None, **kwargs):
        kwargs.setdefault("load_default", default)
        kwargs.setdefault("validate", validate.Range(min=min_value, max=max_value))
        super().__init__(strict=False, **kwargs)


def _place(description):
    return PlaceRefField(metadata={"description": description})


# ---- shared schema parts ---------------------------------------------------------------

class _MarketSchema(BaseSchema):
    currency = CurrencyField(load_default="USD", metadata={"description": "ISO 4217 currency for every price (default USD)"})
    locale = LanguageCodeField(allow_locale=False, load_default="en",
                               metadata={"description": "ISO 639-1 language of place names (default en)"})
    market = CountryCodeField(load_default="US", metadata={
        "description": "ISO 3166-1 country of the Kiwi.com storefront whose fares apply (default US)"})


class _PassengersSchema(_MarketSchema):
    adults = IntField(default=1, min_value=1, max_value=9, metadata={"description": "adult passengers (default 1)"})
    children = IntField(default=0, min_value=0, max_value=8, metadata={"description": "child passengers aged 2-11 (default 0)"})
    infants = IntField(default=0, min_value=0, max_value=4, metadata={"description": "infant passengers under 2, at most one per adult (default 0)"})
    cabin_class = ChoiceField(CABIN_CLASSES, load_default="economy", metadata={
        "description": "economy (default), premium_economy, business or first"})
    hand_bags = IntField(default=0, min_value=0, max_value=1, metadata={"description": "cabin bags per passenger to price in (0 or 1, default 0)"})
    checked_bags = IntField(default=0, min_value=0, max_value=2, metadata={"description": "checked bags per passenger to price in (0-2, default 0)"})

    @validates_schema
    def _infants_need_adults(self, data, **kwargs):
        if data.get("infants", 0) > data.get("adults", 1):
            raise ValidationError("At most one infant per adult.", "infants")


class _FiltersSchema(_PassengersSchema):
    max_stops = IntField(min_value=0, max_value=3, metadata={"description": "maximum number of stops per direction (0 = direct only)"})
    max_duration_hours = IntField(min_value=1, max_value=120, metadata={"description": "maximum total travel time per direction, in hours"})
    max_price = Price(metadata={"description": "maximum price per itinerary, in `currency`"})
    max_layover_hours = IntField(min_value=1, max_value=72, metadata={"description": "maximum layover length, in hours"})
    departure_hours = HoursRangeField(metadata={"description": "outbound departure hour window, e.g. 6-12"})
    arrival_hours = HoursRangeField(metadata={"description": "outbound arrival hour window, e.g. 12-20"})
    airlines = CommaListField(max_items=20, metadata={
        "description": "comma-separated IATA airline codes; only itineraries flown entirely by them"})
    exclude_airlines = CommaListField(max_items=20, metadata={
        "description": "comma-separated IATA airline codes to leave out"})
    transport = CommaListField(value_map=TRANSPORT, max_items=3, metadata={
        "description": "comma-separated transport types: flight (default), bus, train"})
    self_transfer = Flag(load_default=True, metadata={
        "description": "include Kiwi.com self-transfer (virtual interlining) itineraries (default true)"})
    travel_hacks = Flag(load_default=True, metadata={
        "description": "include hidden-city and throwaway-ticket itineraries (default true)"})
    sort = ChoiceField(SORTS, load_default="quality", metadata={
        "description": "quality (default), price, duration, departure, arrival or popularity"})
    limit = LimitField(default=20, max_size=100, metadata={
        "description": "itineraries to return (default 20, max 100; the engine holds up to ~200)"})


def _plus_days(day, days):
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()


def _window_ok(data, start_key, end_key, max_days, field):
    start, end = data.get(start_key), data.get(end_key)
    if start and end:
        if end < start:
            raise ValidationError(f"{end_key} must be on or after {start_key}.", field)
        span = (date.fromisoformat(end) - date.fromisoformat(start)).days
        if span > max_days:
            raise ValidationError(f"{start_key}..{end_key} may span at most {max_days} days.", field)


# ---- flights ---------------------------------------------------------------------------

class OneWaySearchSchema(_FiltersSchema):
    origin = _place("departure place: IATA code, Kiwi place id, kiwi.com slug / link or a place name")
    destination = _place("arrival place: IATA code, Kiwi place id, kiwi.com slug / link or a place name")
    date = DateField(bound="future", default_days_ahead=1, metadata={
        "description": "departure date YYYY-MM-DD (default tomorrow)"})
    date_to = DateField(bound="future", metadata={
        "description": "end of a flexible departure window (date .. date_to, at most 62 days)"})

    @validates_schema
    def _window(self, data, **kwargs):
        _window_ok(data, "date", "date_to", MAX_WINDOW_DAYS, "date_to")


class RoundTripSearchSchema(OneWaySearchSchema):
    return_date = DateField(bound="future", metadata={
        "description": "return departure date YYYY-MM-DD (default a week after date, unless nights_min is given)"})
    return_date_to = DateField(bound="future", metadata={
        "description": "end of a flexible return window (return_date .. return_date_to)"})
    nights_min = IntField(min_value=0, max_value=365, metadata={"description": "instead of return_date: shortest stay in nights"})
    nights_max = IntField(min_value=0, max_value=365, metadata={"description": "instead of return_date: longest stay in nights (default nights_min)"})

    @validates_schema
    def _return(self, data, **kwargs):
        has_return = bool(data.get("return_date"))
        has_nights = data.get("nights_min") is not None
        if has_return and has_nights:
            raise ValidationError("Give either return_date (optionally return_date_to) or nights_min "
                                  "(optionally nights_max), not both.", "return_date")
        if bool(data.get("return_date_to")) and not has_return:
            raise ValidationError("return_date_to needs return_date.", "return_date_to")
        if data.get("nights_max") is not None and not has_nights:
            raise ValidationError("nights_max needs nights_min.", "nights_max")
        if has_return:
            if data.get("return_date") < data.get("date", ""):
                raise ValidationError("return_date must be on or after date.", "return_date")
            _window_ok(data, "return_date", "return_date_to", MAX_WINDOW_DAYS, "return_date_to")
        else:
            nights_max = data.get("nights_max")
            if has_nights and nights_max is not None and nights_max < data["nights_min"]:
                raise ValidationError("nights_max must be at least nights_min.", "nights_max")

    @post_load
    def _default_return(self, data, **kwargs):
        if not data.get("return_date") and data.get("nights_min") is None:
            data["return_date"] = _plus_days(data["date"], DEFAULT_RETURN_AFTER_DAYS)
        return data


class MultiCitySearchSchema(_FiltersSchema):
    legs = LegsField(metadata={
        "description": 'JSON array of 2-6 legs in travel order, e.g. [{"origin":"NYC","destination":"LON",'
                       '"date":"2026-12-10"},{"origin":"LON","destination":"PAR"}]; a leg without a date '
                       'departs 3 days after the previous one (the first: tomorrow)'})


class ItineraryDetailsSchema(_PassengersSchema):
    itinerary = ItineraryRefField(metadata={
        "description": "the itinerary `id` (Kiwi share id) from a search, an ItineraryOneWay:… id, "
                       "or a kiwi.com link carrying ?shareId="})


class ExploreSchema(_PassengersSchema):
    origin = _place("departure place: IATA code, Kiwi place id, kiwi.com slug / link or a place name")
    trip_type = ChoiceField(["one_way", "round_trip"], load_default="one_way", metadata={
        "description": "one_way (default) or round_trip (then nights_min / nights_max apply)"})
    date_from = DateField(bound="future", default_days_ahead=1, metadata={
        "description": "earliest departure date (default tomorrow)"})
    date_to = DateField(bound="future", metadata={
        "description": "latest departure date (default date_from + 30 days, at most 365 days after it)"})
    nights_min = IntField(default=3, min_value=0, max_value=365, metadata={"description": "round trips: shortest stay in nights (default 3)"})
    nights_max = IntField(default=7, min_value=0, max_value=365, metadata={"description": "round trips: longest stay in nights (default 7)"})
    max_price = Price(metadata={"description": "maximum price, in `currency`"})
    max_stops = IntField(min_value=0, max_value=3, metadata={"description": "maximum stops per direction (0 = direct only)"})
    limit = LimitField(default=30, max_size=100, metadata={
        "description": "destination cities to return (default 30, max 100)"})

    @validates_schema
    def _window(self, data, **kwargs):
        _window_ok(data, "date_from", "date_to", MAX_EXPLORE_DAYS, "date_to")
        if data.get("nights_max", 0) < data.get("nights_min", 0):
            raise ValidationError("nights_max must be at least nights_min.", "nights_max")


# ---- prices ----------------------------------------------------------------------------

class _RouteDatesSchema(_PassengersSchema):
    origin = _place("departure place: IATA code, Kiwi place id, kiwi.com slug / link or a place name")
    destination = _place("arrival place: IATA code, Kiwi place id, kiwi.com slug / link or a place name")
    date_from = DateField(bound="future", default_days_ahead=0, metadata={
        "description": "first departure date to price (default today)"})
    date_to = DateField(bound="future", metadata={
        "description": "last departure date to price (default date_from + 30 days, at most 92 days after it)"})

    @validates_schema
    def _window(self, data, **kwargs):
        _window_ok(data, "date_from", "date_to", MAX_CALENDAR_DAYS, "date_to")


class PriceCalendarSchema(_RouteDatesSchema):
    """One-way price per departure day."""


class ReturnPriceCalendarSchema(_RouteDatesSchema):
    nights_min = IntField(default=7, min_value=0, max_value=365, metadata={"description": "shortest stay in nights (default 7)"})
    nights_max = IntField(min_value=0, max_value=365, metadata={"description": "longest stay in nights (default nights_min)"})

    @validates_schema
    def _nights(self, data, **kwargs):
        nights_max = data.get("nights_max")
        if nights_max is not None and nights_max < data.get("nights_min", 0):
            raise ValidationError("nights_max must be at least nights_min.", "nights_max")


class PriceGraphSchema(_RouteDatesSchema):
    nights_min = IntField(min_value=0, max_value=365, metadata={"description": "round-trip curve: shortest stay in nights (omit for a one-way curve)"})
    nights_max = IntField(min_value=0, max_value=365, metadata={"description": "round-trip curve: longest stay in nights (default nights_min)"})

    @validates_schema
    def _nights(self, data, **kwargs):
        nights_max = data.get("nights_max")
        if nights_max is not None and data.get("nights_min") is None:
            raise ValidationError("nights_max needs nights_min.", "nights_max")
        if nights_max is not None and nights_max < data.get("nights_min", 0):
            raise ValidationError("nights_max must be at least nights_min.", "nights_max")


class PriceTableSchema(_PassengersSchema):
    origin = _place("departure place: IATA code, Kiwi place id, kiwi.com slug / link or a place name")
    destination = _place("arrival place: IATA code, Kiwi place id, kiwi.com slug / link or a place name")
    date = DateField(bound="future", default_days_ahead=1, metadata={
        "description": "departure date YYYY-MM-DD (default tomorrow)"})
    return_date = DateField(bound="future", metadata={
        "description": "return date YYYY-MM-DD (default a week after date)"})

    @validates_schema
    def _order(self, data, **kwargs):
        if data.get("return_date") and data["return_date"] < data.get("date", ""):
            raise ValidationError("return_date must be on or after date.", "return_date")

    @post_load
    def _default_return(self, data, **kwargs):
        if not data.get("return_date"):
            data["return_date"] = _plus_days(data["date"], DEFAULT_RETURN_AFTER_DAYS)
        return data


class PriceMapSchema(_PassengersSchema):
    origin = _place("departure place: IATA code, Kiwi place id, kiwi.com slug / link or a place name")
    date_from = DateField(bound="future", default_days_ahead=1, metadata={
        "description": "earliest departure date (default tomorrow)"})
    date_to = DateField(bound="future", metadata={
        "description": "latest departure date (default date_from + 30 days, at most 365 days after it)"})
    return_from = DateField(bound="future", metadata={
        "description": "round trips: earliest return date (one-way prices when no return window / nights)"})
    return_to = DateField(bound="future", metadata={"description": "round trips: latest return date"})
    nights_min = IntField(min_value=0, max_value=365, metadata={"description": "round trips, instead of return dates: shortest stay in nights"})
    nights_max = IntField(min_value=0, max_value=365, metadata={"description": "round trips, instead of return dates: longest stay (default nights_min)"})
    return_anytime = Flag(load_default=False, metadata={
        "description": "true = round-trip prices with any return date"})
    bounds = BoundsField(metadata={
        "description": "map corners sw_lat,sw_lng,ne_lat,ne_lng (default the whole world); "
                       "Kiwi returns at most 25 cities per box, tile the box for more"})

    @validates_schema
    def _window(self, data, **kwargs):
        _window_ok(data, "date_from", "date_to", MAX_EXPLORE_DAYS, "date_to")
        if bool(data.get("return_to")) and not data.get("return_from"):
            raise ValidationError("return_to needs return_from.", "return_to")
        _window_ok(data, "return_from", "return_to", MAX_EXPLORE_DAYS, "return_to")
        if data.get("return_from") and data.get("nights_min") is not None:
            raise ValidationError("Give either return_from / return_to or nights_min / nights_max.", "nights_min")
        nights_max = data.get("nights_max")
        if nights_max is not None and nights_max < (data.get("nights_min") or 0):
            raise ValidationError("nights_max must be at least nights_min.", "nights_max")


# ---- places ----------------------------------------------------------------------------

class PlacesSearchSchema(_MarketSchema):
    query = QueryField(max_length=100, metadata={"description": "place name, IATA code or part of one"})
    types = CommaListField(value_map=PLACE_TYPES, max_items=10, metadata={
        "description": "comma-separated place types to return: airport, city, country, region, continent, "
                       "subdivision, bus_station, train_station, point_of_interest, tourist_region (default all)"})
    limit = LimitField(default=10, max_size=50, metadata={"description": "places to return (default 10, max 50)"})


class PlaceDetailsSchema(_MarketSchema):
    place = _place("IATA code, Kiwi place id (City:london_gb, Station:airport:LHR, Country:GB), "
                   "kiwi.com slug / link or a place name")


class PlacesNearbySchema(_MarketSchema):
    lat = fields.Float(required=True, validate=validate.Range(min=-90, max=90),
                       metadata={"description": "latitude of the centre point"})
    lng = fields.Float(required=True, validate=validate.Range(min=-180, max=180),
                       metadata={"description": "longitude of the centre point"})
    radius_km = IntField(default=100, min_value=1, max_value=1000, metadata={"description": "search radius in km (default 100, max 1000)"})
    types = CommaListField(value_map=PLACE_TYPES, max_items=10, metadata={
        "description": "comma-separated place types (default airport,city)"})
    limit = LimitField(default=10, max_size=50, metadata={"description": "places to return (default 10, max 50)"})


class TopDestinationsSchema(_MarketSchema):
    place = _place("origin: IATA code, Kiwi place id, kiwi.com slug / link or a place name")
    popularity = ChoiceField(POPULARITY, load_default="searches", metadata={
        "description": "rank by searches (default), bookings or clicks on Kiwi.com"})
    limit = LimitField(default=10, max_size=50, metadata={"description": "destinations to return (default 10, max 50)"})


# ---- nomad / airlines ------------------------------------------------------------------

class NomadTripsSchema(_MarketSchema):
    origin = _place("home place: IATA code, Kiwi place id, kiwi.com slug / link or a place name")


class AirlinesSearchSchema(BaseSchema):
    query = QueryField(max_length=60, required=False, load_default=None, metadata={
        "description": "airline name or IATA code (omit for the whole directory)"})
    limit = LimitField(default=50, max_size=500, metadata={"description": "airlines to return (default 50, max 500)"})
