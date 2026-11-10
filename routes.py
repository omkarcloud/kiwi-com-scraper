"""The 16 Kiwi.com endpoints. Every path is served with and without the
`/kiwi-com` prefix, so code generated against the hosted API on RapidAPI
(paths like /flights/search-one-way) runs unchanged against this server.

Params are validated by the marshmallow schemas in kiwi_com/schemas.py (ONE
param per input: `origin` / `destination` / `place` take an IATA code, a
Kiwi place id, a kiwi.com slug or link, or a place name; `itinerary` takes
the id a search returned or a kiwi.com share link); unknown params are
rejected."""
import json

from bottle import request, response, route

from kiwi_com import api, schemas
from schema_fields import load_query
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError


def json_response(data, status=200):
    response.status = status
    response.content_type = "application/json"
    return json.dumps(data, ensure_ascii=False)


def query_dict():
    """The request query as unicode strings (bottle 0.12's .get() hands back
    latin-1 decoded bytes, so getunicode is mandatory)."""
    return {key: request.query.getunicode(key) for key in request.query.keys()}


def call(label, schema_cls, impl):
    """Validate, run, map errors: bad params -> 400, missing entity -> 404,
    blocks / transport -> 502, anything else -> 500."""
    data, error = load_query(schema_cls, query_dict())
    if error:
        return json_response(error, 400)
    try:
        result = impl(**data)
    except ValueError as e:
        return json_response({"error": str(e)}, 400)
    except BadRequest as e:
        return json_response({"error": f"kiwi-com rejected the request: {e}"}, 400)
    except NotFound as e:
        return json_response({"error": str(e) or "not found"}, 404)
    except Blocked as e:
        return json_response({"error": f"kiwi-com blocked the request, retry later: {e}"}, 502)
    except UpstreamError as e:
        return json_response({"error": f"kiwi-com {label} failed: {e}"}, 502)
    except Exception as e:                 # parser surprises
        return json_response({"error": f"kiwi-com {label} failed: {type(e).__name__}: {e}"}, 500)
    return json_response(result)


ENDPOINTS = [
    ("/flights/search-one-way", schemas.OneWaySearchSchema, api.search_one_way),
    ("/places/search", schemas.PlacesSearchSchema, api.places_search),
    ("/flights/search-round-trip", schemas.RoundTripSearchSchema, api.search_round_trip),
    ("/flights/search-multi-city", schemas.MultiCitySearchSchema, api.search_multi_city),
    ("/flights/details", schemas.ItineraryDetailsSchema, api.itinerary_details),
    ("/flights/explore", schemas.ExploreSchema, api.explore),
    ("/prices/calendar", schemas.PriceCalendarSchema, api.price_calendar),
    ("/prices/return-calendar", schemas.ReturnPriceCalendarSchema, api.return_price_calendar),
    ("/prices/graph", schemas.PriceGraphSchema, api.price_graph),
    ("/prices/table", schemas.PriceTableSchema, api.price_table),
    ("/prices/map", schemas.PriceMapSchema, api.price_map),
    ("/places/details", schemas.PlaceDetailsSchema, api.place_details),
    ("/places/nearby", schemas.PlacesNearbySchema, api.places_nearby),
    ("/places/top-destinations", schemas.TopDestinationsSchema, api.top_destinations),
    ("/nomad/example-trips", schemas.NomadTripsSchema, api.nomad_trips),
    ("/airlines/search", schemas.AirlinesSearchSchema, api.airlines_search),
]


def mount(path, schema_cls, impl):
    """Serve a handler at /path and /kiwi-com/path."""
    label = path.strip("/")

    def handler():
        return call(label, schema_cls, impl)

    handler.__name__ = "kiwi_com_" + label.replace("/", "_").replace("-", "_")
    route(path, method="GET")(handler)
    route("/kiwi-com" + path, method="GET")(handler)


for _path, _schema, _impl in ENDPOINTS:
    mount(_path, _schema, _impl)


@route("/", method="GET")
@route("/health", method="GET")
def health():
    return json_response({"status": "ok", "endpoints": [p for p, *_ in ENDPOINTS]})
