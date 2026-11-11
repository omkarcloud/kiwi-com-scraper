# ✈️ Kiwi Com Scraper

Kiwi Com Scraper is a **free and open-source** scraper that gets you **unlimited** detailed Kiwi.com flight data for free.

## ✨ What Can I Get?

- ✈️ **Live fares on 1,100+ airlines** — price, fare options, every segment & layover, seats left & booking link
- 🧳 **One-way, round-trip & 6-leg multi-city searches** — bag fees by tier, self-transfer & hidden-city deals flagged
- 📅 **92-day price calendars** — cheapest fare per day rated cheap, average or expensive, plus price graphs & fare maps
- 🌍 **Up to 100 cheapest destinations from any city** — plus top destinations, nomad trip ideas & 2,300+ carriers

## 🎥 Example: A Full Kiwi.com Flight

```json
{
  "id": "eyJyIjoiQjY6MTQxMTpKRks6MTc5NDQ1MjcwMDpMQVM6MTc5NDQ3Mzc2MDplY29ub215…",
  "trip_type": "one_way",
  "booking_link": "https://www.kiwi.com/en/booking/?direct=true&locale=en&currency=usd&passengers=1-0-0&token=…",
  "price": { "amount": 395, "currency": "USD" },
  "kiwi_guarantee_price": { "amount": 79, "currency": "USD" },
  "duration_minutes": 763,
  "tickets_count": 2,
  "is_self_transfer": true,
  "bags": {
    "included": { "personal_item": 1, "hand_bag": 0, "checked_bag": 0 },
    "hand_bag_tiers": [{ "price": { "amount": 95, "currency": "USD" }, "bags": [{ "weight_kg": 10, "length_cm": 55, "width_cm": 22, "height_cm": 35 }] }],
    "checked_bag_tiers": [{ "price": { "amount": 161.04, "currency": "USD" }, "bags": [{ "weight_kg": 18, "length_cm": 78, "width_cm": 27, "height_cm": 52 }] }]
  },
  "outbound": {
    "departure": { "station": { "code": "JFK", "name": "John F. Kennedy International" }, "local_time": "2026-11-11T22:05:00" },
    "arrival": { "station": { "code": "LAX", "name": "Los Angeles International" }, "local_time": "2026-11-12T07:48:00" },
    "stops": 1,
    "airlines": ["B6", "F9"],
    "segments": [
      {
        "flight_number": "B61411",
        "airline": { "code": "B6", "name": "JetBlue Airways" },
        "arrival": { "station": { "code": "LAS", "name": "Harry Reid International Airport" }, "local_time": "2026-11-12T00:56:00" },
        "duration_minutes": 351
      },
      {
        "flight_number": "F93291",
        "airline": { "code": "F9", "name": "Frontier Airlines" },
        "departure": { "station": { "code": "LAS" }, "local_time": "2026-11-12T06:30:00" },
        "layover": { "duration_minutes": 334, "guarantee": "kiwi", "is_baggage_recheck": true }
      }
    ]
  }
}
```

*Trimmed for readability.*

## 🚀 Unlimited Free Kiwi.com Data — Get It in 60 Seconds

1️⃣ Clone and install:
```bash
git clone https://github.com/omkarcloud/kiwi-com-scraper
cd kiwi-com-scraper
python -m pip install -r requirements.txt
```

2️⃣ Start the API:
```bash
python run.py
```

3️⃣ Get your first data:
```bash
curl "http://localhost:8000/flights/search-one-way?origin=JFK&destination=LAX"
```

```json
{
  "trip_type": "one_way",
  "origin": { "id": "Station:airport:JFK", "type": "airport", "name": "John F. Kennedy International", "code": "JFK" },
  "destination": { "id": "Station:airport:LAX", "type": "airport", "name": "Los Angeles International", "code": "LAX" },
  "date": "2026-11-11",
  "count": 19,
  "total_count": 19,
  "results": [
    {
      "price": { "amount": 316, "currency": "USD" },
      "duration_minutes": 376,
      "booking_link": "https://www.kiwi.com/en/booking/?direct=true&locale=en&currency=usd&passengers=1-0-0&token=…",
      "bags": { "included": { "personal_item": 1, "hand_bag": 1, "checked_bag": 0 } },
      "outbound": {
        "departure": { "station": { "code": "JFK" }, "local_time": "2026-11-11T07:00:00" },
        "arrival": { "station": { "code": "LAX" }, "local_time": "2026-11-11T10:16:00" },
        "stops": 0,
        "airlines": ["B6"],
        "segments": [{ "flight_number": "B6523", "airline": { "code": "B6", "name": "JetBlue Airways" } }]
      }
    }
  ]
}
```

All 16 endpoints are now live at `http://localhost:8000`.

## 📚 Endpoints

16 endpoints cover everything you need.

| Endpoint | Path | Returns |
|---|---|---|
| Search One-Way Flights | `/flights/search-one-way` | Every flight with price, segments, bag fees, seats left and booking link |
| Search Places | `/places/search` | Autocomplete airports, cities and countries with codes and coordinates |
| Search Round-Trip Flights | `/flights/search-round-trip` | Both directions by exact dates, flexible windows or stay length |
| Search Multi-City Flights | `/flights/search-multi-city` | 2-6 legs, each with its own flights, plus the cheapest total |
| Flight Details | `/flights/details` | One itinerary re-priced live, every segment, layover and bag tier |
| Explore Cheapest Destinations | `/flights/explore` | The cheapest fare to up to 100 cities from anywhere |
| One-Way & Round-Trip Price Calendars | `/prices/calendar`, `/prices/return-calendar` | Cheapest fare per day, rated cheap, average or expensive |
| Price Graph | `/prices/graph` | The price curve per departure day, one-way or round trip |
| Round-Trip Price for Dates | `/prices/table` | Price and rating for one exact departure and return date |
| Price Map | `/prices/map` | Cheapest fares to up to 25 cities in any map area, ranked |
| Place Details | `/places/details` | One airport, city or country with timezone, coordinates and airports |
| Nearby Places | `/places/nearby` | Airports and cities around any point, nearest first |
| Top Destinations | `/places/top-destinations` | Most searched, booked or clicked destinations from any city |
| Nomad Trip Ideas | `/nomad/example-trips` | Ready-made multi-city trips per continent with days per stop |
| Search Airlines | `/airlines/search` | 2,300+ airlines, bus and train lines by name or code |

## 🔍 Exploring Parameters

The same API is published on RapidAPI, and its playground is the easiest place to try parameters and see raw responses. Once a request looks right, run it locally for **unlimited free** data.

1. [Subscribe to the free plan](https://rapidapi.com/OmkarCloud/api/best-kiwi-com-scraper-free-1000-calls/pricing) — 1,000 calls/month, no credit card.
2. [Try the endpoints in the playground](https://rapidapi.com/OmkarCloud/api/best-kiwi-com-scraper-free-1000-calls/playground) — every param is pre-filled, so you see real data in one click.
3. Copy the generated code and replace `https://best-kiwi-com-scraper-free-1000-calls.p.rapidapi.com` with `http://localhost:8000`. It will now run against your local API.

```python
import requests

# generated by the playground, host swapped for the local API
response = requests.get(
    "http://localhost:8000/flights/search-round-trip",
    params={"origin": "NYC", "destination": "LON", "max_stops": "0", "sort": "price"},
)
print(response.json())
```

## 💬 Have Questions? We Have Answers.

You're a developer — we know how hard completing a project can be. So we offer full support: just message us and we'll reply ✅ with a solution within 1 working day.

[![Message Us on WhatsApp about Kiwi Com Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/whatsapp-us.png)](https://www.omkar.cloud/l/kiwi-com-scraper-github-whatsapp)

[![Ask Us by Email about Kiwi Com Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/ask-on-email.png)](mailto:happy.to.help@omkar.cloud?subject=Help%20with%20Kiwi%20Com%20Scraper%20API&body=I%20need%20help%20using%20the%20Kiwi%20Com%20Scraper%20API.)

## ⚡ Popular Scrapers by Omkar Cloud

- [**Google Maps Scraper (3,100+ GitHub Stars)**](https://github.com/omkarcloud/google-maps-scraper) — type "dentists in New York", get every business as a ready-to-call lead list: phones, emails, websites & reviews. Up to 100K free leads/month.
- [**G2 Scraper**](https://www.omkar.cloud/tools/g2-scraper) — G2 product details, ratings & AI-found contacts
- [**Website Email Contact Scraper**](https://www.omkar.cloud/tools/website-email-contact-scraper) — emails, phones & socials from any website
- [**AliExpress Scraper**](https://www.omkar.cloud/tools/aliexpress-scraper-api) — live product details, SKU variants, stock & shipping
- [**Booking Scraper**](https://www.omkar.cloud/tools/booking-scraper) — Booking.com hotels: prices, ratings, rooms & amenities
- [**Etsy Scraper**](https://www.omkar.cloud/tools/etsy-scraper) — Etsy products: prices, discounts, shops & variations
- [**Waze Scraper**](https://github.com/omkarcloud/waze-scraper) — live Waze traffic jams, road closures, driving routes with tolls & places

## ⭐ Love It? [Star It ⭐!](https://github.com/omkarcloud/kiwi-com-scraper)

Star the repo ⭐ and become my star hero!

It's just 1 click, but it means the world to me.

[![Star us on GitHub](https://raw.githubusercontent.com/omkarcloud/google-maps-scraper/master/screenshots/star-us.png)](https://github.com/omkarcloud/kiwi-com-scraper)
