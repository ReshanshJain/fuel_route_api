from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import RouteRequestSerializer
from .utils import calculate_total_cost, find_fuel_stops, get_route


class RouteView(APIView):
    def post(self, request):
        serializer = RouteRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            distance_miles, geometry = get_route(
                serializer.validated_data["start"],
                serializer.validated_data["finish"],
            )
            finish_state = serializer.validated_data["finish"].rsplit(",", 1)[1].strip()
            stops = find_fuel_stops(
                distance_miles,
                geometry,
                finish_state=finish_state,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        return Response(
            {
                "total_distance_miles": round(distance_miles, 2),
                "fuel_stops": stops,
                "total_cost": round(calculate_total_cost(stops), 2),
                "route_geometry": geometry,
            }
        )
