from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings
from django.urls import resolve
from rest_framework.test import APIRequestFactory

from .serializers import RouteRequestSerializer
from .utils import (
    calculate_total_cost,
    find_fuel_stops,
    geocode_location,
    get_route,
    haversine_meters,
    point_along_route,
    reverse_geocode_road_point,
    validate_usa_location,
)
from .views import RouteView


class UrlTests(SimpleTestCase):
    def test_route_endpoint_is_registered(self):
        match = resolve("/api/map")
        self.assertEqual(match.func.view_class, RouteView)


class SerializerTests(SimpleTestCase):
    def test_location_fields_are_required_and_trimmed(self):
        serializer = RouteRequestSerializer(
            data={"start": "  Atlanta, GA  ", "finish": "Miami, FL"}
        )
        self.assertTrue(serializer.is_valid())
        self.assertEqual(serializer.validated_data["start"], "Atlanta, GA")

    def test_empty_locations_are_rejected(self):
        serializer = RouteRequestSerializer(data={"start": " ", "finish": "Miami, FL"})
        self.assertFalse(serializer.is_valid())
        self.assertIn("start", serializer.errors)


class GeocodingTests(SimpleTestCase):
    @override_settings(FREEROUTE_API_KEY="test-key")
    @patch("map.utils.requests.get")
    def test_geocode_location_reads_geojson_feature(self, get_request):
        geocode_location.cache_clear()
        response = Mock()
        response.json.return_value = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [-85.1394, 41.0793]},
                    "properties": {
                        "label": "Fort Wayne, Indiana, United States",
                        "state": "Indiana",
                        "countrycode": "US",
                    },
                }
            ],
        }
        get_request.return_value = response

        location = geocode_location("Fort Wayne, IN")

        self.assertEqual(location["latitude"], 41.0793)
        self.assertEqual(location["longitude"], -85.1394)
        self.assertEqual(location["display_name"], "Fort Wayne, Indiana, United States")

    @override_settings(FREEROUTE_API_KEY="test-key")
    @patch("map.utils.requests.get")
    def test_reverse_geocode_location_reads_geojson_state(self, get_request):
        response = Mock()
        response.json.return_value = {
            "type": "FeatureCollection",
            "features": [
                {"properties": {"state": "Indiana"}},
            ],
        }
        get_request.return_value = response

        state = reverse_geocode_road_point(41.0793, -85.1394)

        self.assertEqual(state, "IN")


class RouteUtilityTests(SimpleTestCase):
    @override_settings(FREEROUTE_API_KEY="test-key")
    @patch("map.utils.requests.post")
    @patch("map.utils.geocode_location")
    def test_get_route_reads_geojson_feature(self, geocode, post_request):
        geocode.side_effect = lambda location: {
            "Fort Wayne, IN": {"latitude": 41.0, "longitude": -85.0},
            "Lake Station, IN": {"latitude": 41.5, "longitude": -87.0},
        }[location]
        response = Mock()
        response.json.return_value = {
            "type": "FeatureCollection",
            "features": [
                {
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[-85.0, 41.0], [-87.0, 41.5]],
                    },
                    "properties": {"summary": {"distance": 10000}},
                }
            ],
        }
        post_request.return_value = response

        distance, geometry = get_route("Fort Wayne, IN", "Lake Station, IN")

        self.assertAlmostEqual(distance, 10000 / 1609.344)
        self.assertEqual(geometry["type"], "LineString")
        self.assertEqual(len(geometry["coordinates"]), 2)

    def test_point_along_route_interpolates_distance(self):
        geometry = {
            "type": "LineString",
            "coordinates": [[0, 0], [0, 1]],
        }
        point = point_along_route(geometry, 50_000)
        self.assertAlmostEqual(point[0], 0.0, places=6)
        self.assertAlmostEqual(point[1], 0.45, places=2)

    def test_point_along_route_clamps_rounding_overshoot_at_endpoint(self):
        geometry = {"type": "LineString", "coordinates": [[0, 0], [0, 1]]}
        route_length = haversine_meters([0, 0], [1, 0])

        point = point_along_route(geometry, route_length + 0.5)

        self.assertEqual(point, [0, 1])

    def test_point_along_route_rejects_real_overshoot(self):
        geometry = {"type": "LineString", "coordinates": [[0, 0], [0, 1]]}
        route_length = haversine_meters([0, 0], [1, 0])

        with self.assertRaises(ValueError):
            point_along_route(geometry, route_length + 2)

    @patch("map.utils.point_along_route", return_value=[-85.0, 41.0])
    def test_fuel_stop_reverse_geocoding_uses_latitude_longitude_order(
        self, point_along_route_mock
    ):
        resolve_state = Mock(return_value="IN")
        stations = [
            {
                "Truckstop Name": "Test Stop",
                "Address": "I-69",
                "City": "Fort Wayne",
                "State": "IN",
                "Retail Price": 3.5,
            }
        ]

        find_fuel_stops(
            distance_miles=100,
            geometry={
                "type": "LineString",
                "coordinates": [[-85.0, 41.0], [-84.0, 41.0]],
            },
            stations=stations,
            resolve_state_fn=resolve_state,
        )

        resolve_state.assert_called_once_with(41.0, -85.0)

    def test_fuel_stops_are_sorted_by_price_and_cost(self):
        stations = [
            {
                "Truckstop Name": "Expensive",
                "Address": "I-64",
                "City": "A",
                "State": "VA",
                "Retail Price": 4.5,
            },
            {
                "Truckstop Name": "Cheap",
                "Address": "I-64",
                "City": "B",
                "State": "VA",
                "Retail Price": 3.0,
            },
        ]

        stops = find_fuel_stops(
            distance_miles=1000,
            geometry={"type": "LineString", "coordinates": [[0, 0], [0, 15]]},
            stations=stations,
            resolve_state_fn=lambda latitude, longitude: "VA",
        )

        self.assertEqual(len(stops), 2)
        self.assertEqual(stops[0]["truckstop"], "Cheap")
        self.assertEqual(stops[0]["gallons"], 50)
        self.assertEqual(calculate_total_cost(stops), 300)

    def test_final_fuel_stop_mileage_is_capped_at_route_distance(self):
        stations = [
            {
                "Truckstop Name": "Stop",
                "Address": "I-64",
                "City": "A",
                "State": "VA",
                "Retail Price": 3.0,
            }
        ]

        stops = find_fuel_stops(
            distance_miles=100,
            geometry={"type": "LineString", "coordinates": [[0, 0], [0, 2]]},
            stations=stations,
            resolve_state_fn=lambda latitude, longitude: "VA",
            finish_state="VA",
        )

        self.assertEqual(stops[0]["route_mileage"], 100)

    def test_final_stop_uses_known_finish_state_without_reverse_geocoding(self):
        stations = [
            {
                "Truckstop Name": "Stop",
                "Address": "I-69",
                "City": "Fort Wayne",
                "State": "IN",
                "Retail Price": 3.0,
            }
        ]
        resolve_state = Mock(
            side_effect=AssertionError("final state should already be known")
        )

        stops = find_fuel_stops(
            distance_miles=25,
            geometry={
                "type": "LineString",
                "coordinates": [[-85.0, 41.0], [-84.0, 41.0]],
            },
            stations=stations,
            resolve_state_fn=resolve_state,
            finish_state="Indiana",
        )

        self.assertEqual(stops[0]["state"], "IN")
        resolve_state.assert_not_called()

    def test_routes_over_five_ranges_include_all_fuel_stops(self):
        stations = [
            {
                "Truckstop Name": f"Stop {index}",
                "Address": "I-64",
                "City": "A",
                "State": "VA",
                "Retail Price": 3.0,
                "OPIS Truckstop ID": index,
            }
            for index in range(7)
        ]

        stops = find_fuel_stops(
            distance_miles=3000,
            geometry={"type": "LineString", "coordinates": [[0, 0], [0, 50]]},
            stations=stations,
            resolve_state_fn=lambda latitude, longitude: "VA",
        )

        self.assertEqual(len(stops), 6)

    def test_validate_usa_location_requires_state(self):
        with self.assertRaises(ValueError):
            validate_usa_location("London, UK")


class RouteViewTests(SimpleTestCase):
    @override_settings(ORS_API_KEY="test-key")
    def test_view_returns_400_for_invalid_location(self):
        request = APIRequestFactory().post(
            "/api/map",
            {"start": "London, UK", "finish": "Miami, FL"},
            content_type="application/json",
        )
        response = RouteView.as_view()(request)
        self.assertEqual(response.status_code, 400)
        self.assertIn("start", response.data)
