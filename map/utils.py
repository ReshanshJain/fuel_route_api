import csv
import math
import re
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

import requests
from django.conf import settings


US_STATE_CODES = (
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
)
US_STATE_RE = re.compile(
    rf"^(?:{'|'.join(US_STATE_CODES)})$",
    re.IGNORECASE,
)
US_STATE_ABBREVIATIONS = {
    "ALABAMA": "AL",
    "ALASKA": "AK",
    "ARIZONA": "AZ",
    "ARKANSAS": "AR",
    "CALIFORNIA": "CA",
    "COLORADO": "CO",
    "CONNECTICUT": "CT",
    "DELAWARE": "DE",
    "FLORIDA": "FL",
    "GEORGIA": "GA",
    "HAWAII": "HI",
    "IDAHO": "ID",
    "ILLINOIS": "IL",
    "INDIANA": "IN",
    "IOWA": "IA",
    "KANSAS": "KS",
    "KENTUCKY": "KY",
    "LOUISIANA": "LA",
    "MAINE": "ME",
    "MARYLAND": "MD",
    "MASSACHUSETTS": "MA",
    "MICHIGAN": "MI",
    "MINNESOTA": "MN",
    "MISSISSIPPI": "MS",
    "MISSOURI": "MO",
    "MONTANA": "MT",
    "NEBRASKA": "NE",
    "NEVADA": "NV",
    "NEW HAMPSHIRE": "NH",
    "NEW JERSEY": "NJ",
    "NEW MEXICO": "NM",
    "NEW YORK": "NY",
    "NORTH CAROLINA": "NC",
    "NORTH DAKOTA": "ND",
    "OHIO": "OH",
    "OKLAHOMA": "OK",
    "OREGON": "OR",
    "PENNSYLVANIA": "PA",
    "RHODE ISLAND": "RI",
    "SOUTH CAROLINA": "SC",
    "SOUTH DAKOTA": "SD",
    "TENNESSEE": "TN",
    "TEXAS": "TX",
    "UTAH": "UT",
    "VERMONT": "VT",
    "VIRGINIA": "VA",
    "WASHINGTON": "WA",
    "WEST VIRGINIA": "WV",
    "WISCONSIN": "WI",
    "WYOMING": "WY",
    "DISTRICT OF COLUMBIA": "DC",
}
EARTH_RADIUS_METERS = 6_371_000
MILES_PER_METER = 1 / 1609.344


@lru_cache(maxsize=1)
def load_fuel_stations():
    path = Path(settings.FUEL_PRICE_FILE)
    with path.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    for row in rows:
        row["Retail Price"] = float(row["Retail Price"])
    return rows


def validate_usa_location(location):
    location = location.strip()
    if not location or "," not in location:
        raise ValueError("Enter a city and state, for example: Atlanta, GA")
    city, state = location.rsplit(",", 1)
    if not city.strip() or not US_STATE_RE.fullmatch(state.strip()):
        raise ValueError(
            "Locations must be within the United States and include a state"
        )
    return location


def state_code(value):
    if not value:
        return None
    value = value.strip().upper()
    value = US_STATE_ABBREVIATIONS.get(value, value)
    return value if US_STATE_RE.fullmatch(value) else None


@lru_cache(maxsize=256)
def geocode_location(location):
    if not settings.FREEROUTE_API_KEY:
        raise ValueError("FreeRoute API key is not configured")
    expected_state = location.rsplit(",", 1)[1].strip().upper()

    # Build a list of query variants to try, from most specific to least
    queries_to_try = [location]
    # Append ", USA" if not already present to improve geocoding of small US towns
    if "usa" not in location.lower() and "united states" not in location.lower():
        queries_to_try.append(f"{location}, USA")

    for query in queries_to_try:
        response = requests.get(
            settings.FREEROUTE_GEOCODE_URL,
            params={"q": query, "limit": 5},
            headers={"X-API-Key": settings.FREEROUTE_API_KEY},
            timeout=settings.REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
        places = data.get("results") or data.get("features") or []
        for place in places:
            if "geometry" in place:
                coordinates = place.get("geometry", {}).get("coordinates", [])
                if len(coordinates) < 2:
                    continue
                properties = place.get("properties", {})
                country_code = properties.get("countrycode")
                if country_code and country_code.upper() not in {"US", "USA"}:
                    continue
                if state_code(properties.get("state")) != expected_state:
                    continue
                return {
                    "latitude": float(coordinates[1]),
                    "longitude": float(coordinates[0]),
                    "display_name": properties.get("label") or properties.get(
                        "name", location
                    ),
                }
            if state_code(
                place.get("stateCode") or place.get("state")
            ) != expected_state:
                continue
            return {
                "latitude": float(place["latitude"]),
                "longitude": float(place["longitude"]),
                "display_name": place.get("displayName", location),
            }

    raise ValueError(f"Could not find a location for {location}")


@lru_cache(maxsize=512)
def reverse_geocode_road_point(latitude, longitude):
    response = requests.get(
        settings.FREEROUTE_REVERSE_GEOCODE_URL,
        params={"lat": latitude, "lon": longitude},
        headers={"X-API-Key": settings.FREEROUTE_API_KEY},
        timeout=settings.REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()
    places = data.get("results") or data.get("features") or []
    if not places:
        raise ValueError("Could not identify the state at a fuel-stop location")
    place = places[0]
    address = place.get("properties", place.get("address", {}))
    state = state_code(address.get("stateCode") or address.get("state"))
    if not state:
        raise ValueError("Could not identify the state at a fuel-stop location")
    return state


def decode_polyline(encoded):
    if not encoded:
        raise ValueError("The routing API returned no route geometry")

    coordinates = []
    latitude = 0
    longitude = 0
    index = 0

    def read_value():
        nonlocal index
        result = 0
        shift = 0
        while True:
            value = ord(encoded[index]) - 63
            index += 1
            result |= (value & 31) << shift
            if not value & 32:
                break
            shift += 5
        return result

    while index < len(encoded):
        latitude_delta = read_value()
        if latitude_delta & 1:
            latitude_delta = -(latitude_delta >> 1)
        else:
            latitude_delta >>= 1
        longitude_delta = read_value()
        if longitude_delta & 1:
            longitude_delta = -(longitude_delta >> 1)
        else:
            longitude_delta >>= 1
        latitude += latitude_delta / 100000
        longitude += longitude_delta / 100000
        coordinates.append([longitude, latitude])

    return coordinates


def get_route(start, finish):
    if not settings.FREEROUTE_API_KEY:
        raise ValueError("FreeRoute API key is not configured")
    with ThreadPoolExecutor(max_workers=2) as executor:
        start_request = executor.submit(geocode_location, start)
        finish_request = executor.submit(geocode_location, finish)
        start_location = start_request.result()
        finish_location = finish_request.result()
    response = requests.post(
        settings.FREEROUTE_DIRECTIONS_URL,
        json={
            "coordinates": [
                [start_location["longitude"], start_location["latitude"]],
                [finish_location["longitude"], finish_location["latitude"]],
            ],
            "instructions": False,
        },
        headers={"X-API-Key": settings.FREEROUTE_API_KEY},
        timeout=settings.REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()
    routes = data.get("routes") or data.get("features") or []
    if not routes:
        raise ValueError("The routing API returned no usable route")
    route = routes[0]
    properties = route.get("properties", {})
    summary = properties.get("summary", {})
    geometry = route.get("geometry")
    distance_meters = float(route.get("distance", summary.get("distance", 0)))
    if not geometry or distance_meters <= 0:
        raise ValueError("The routing API returned no usable route")
    if isinstance(geometry, str):
        geometry = {"type": "LineString", "coordinates": decode_polyline(geometry)}
    elif not geometry.get("coordinates"):
        raise ValueError("The routing API returned no route geometry")
    return distance_meters * MILES_PER_METER, geometry


def haversine_meters(point_a, point_b):
    lat1, lon1 = map(math.radians, point_a)
    lat2, lon2 = map(math.radians, point_b)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_METERS * math.asin(math.sqrt(a))


def point_along_route(geometry, distance_meters):
    coordinates = geometry["coordinates"]
    if not coordinates or len(coordinates) < 2:
        raise ValueError("Route geometry is incomplete")
    if geometry.get("type") != "LineString":
        raise ValueError("Only LineString routes are supported")

    total_length = 0.0
    segments = []
    for start_point, end_point in zip(coordinates, coordinates[1:]):
        segment_length = haversine_meters(start_point[::-1], end_point[::-1])
        segments.append((total_length, segment_length, start_point, end_point))
        total_length += segment_length
    if distance_meters < 0 or distance_meters > total_length + 1:
        raise ValueError("Requested route distance is outside the route")
    distance_meters = min(distance_meters, total_length)

    for segment_start, segment_length, start_point, end_point in segments:
        if distance_meters <= segment_start + segment_length:
            fraction = (
                0
                if segment_length == 0
                else (distance_meters - segment_start) / segment_length
            )
            return [
                start_point[0] + (end_point[0] - start_point[0]) * fraction,
                start_point[1] + (end_point[1] - start_point[1]) * fraction,
            ]
    return list(coordinates[-1])


def find_fuel_stops(
    distance_miles,
    geometry,
    stations=None,
    resolve_state_fn=None,
    finish_state=None,
):
    stations = stations or load_fuel_stations()
    resolve_state_fn = resolve_state_fn or reverse_geocode_road_point
    stations_by_state = {}
    for station in stations:
        state = station["State"].strip().upper()
        stations_by_state.setdefault(state, []).append(station)
    for state_stations in stations_by_state.values():
        state_stations.sort(
            key=lambda station: (
                station["Retail Price"],
                station["Truckstop Name"],
            )
        )

    stops = []
    existing_station_ids = set()
    stop_count = math.ceil(distance_miles / settings.VEHICLE_RANGE_MILES)
    stop_points = [
        point_along_route(
            geometry,
            min(
                distance_miles * 1609.344,
                (index + 1) * settings.VEHICLE_RANGE_MILES * 1609.344,
            ),
        )
        for index in range(stop_count)
    ]
    states = [None] * stop_count
    if stop_count and finish_state:
        states[-1] = state_code(finish_state)

    intermediate_indices = [
        index for index, state in enumerate(states) if state is None
    ]
    if intermediate_indices:
        with ThreadPoolExecutor(
            max_workers=min(8, len(intermediate_indices))
        ) as executor:
            state_requests = {
                index: executor.submit(
                    resolve_state_fn,
                    stop_points[index][1],
                    stop_points[index][0],
                )
                for index in intermediate_indices
            }
            for index, request in state_requests.items():
                states[index] = request.result()

    for index in range(stop_count):
        state = states[index]
        candidates = stations_by_state.get(state, [])
        chosen = next(
            (
                station
                for station in candidates
                if station.get("OPIS Truckstop ID") not in existing_station_ids
            ),
            None,
        )
        if chosen is None:
            raise ValueError(f"No fuel stations are available in {state}")
        detour_miles = None
        gallons = min(
            settings.VEHICLE_RANGE_MILES,
            distance_miles - index * settings.VEHICLE_RANGE_MILES,
        ) / settings.MPG
        if gallons <= 0:
            break
        stops.append(
            {
                "location": chosen["Address"],
                "city": chosen["City"],
                "state": chosen["State"],
                "truckstop": chosen["Truckstop Name"],
                "station_id": chosen.get("OPIS Truckstop ID"),
                "price_per_gallon": float(chosen["Retail Price"]),
                "gallons": round(gallons, 3),
                "cost": round(gallons * float(chosen["Retail Price"]), 2),
                "distance_from_route_miles": detour_miles,
                "route_mileage": round(
                    min((index + 1) * settings.VEHICLE_RANGE_MILES, distance_miles), 2
                ),
            }
        )
        station_id = chosen.get("OPIS Truckstop ID")
        if station_id is not None:
            existing_station_ids.add(station_id)
    return stops


def calculate_total_cost(stops):
    return sum(float(stop["cost"]) for stop in stops)
