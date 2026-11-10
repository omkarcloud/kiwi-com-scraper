"""Use the scraper straight from Python — no server needed.

    python main.py

Every function returns the same JSON the API does; results are written to
output/*.json. Params go through the same schemas the API uses, so defaults
are filled in (dates default to tomorrow) and an IATA code, a city name or a
kiwi.com link all work as a place.
"""
import json
import os

from kiwi_com import api, schemas

os.makedirs("output", exist_ok=True)


def save(name, data):
    path = os.path.join("output", name)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"saved {path}")


def run(schema_cls, fn, **params):
    return fn(**schema_cls().load(params))


if __name__ == "__main__":
    # every flight tomorrow with price, segments, bag fees, seats left and the booking link
    save("flights_jfk_lax.json",
         run(schemas.OneWaySearchSchema, api.search_one_way, origin="JFK", destination="LAX"))

    # the cheapest fare for every departure day of the next 30 days, rated cheap / average / expensive
    save("price_calendar_jfk_lax.json",
         run(schemas.PriceCalendarSchema, api.price_calendar, origin="JFK", destination="LAX"))

    # the cheapest places to fly from New York in the next 30 days
    save("explore_new_york.json",
         run(schemas.ExploreSchema, api.explore, origin="new york"))
