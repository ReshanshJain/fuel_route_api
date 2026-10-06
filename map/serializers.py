from rest_framework import serializers
from .utils import validate_usa_location


class RouteRequestSerializer(serializers.Serializer):
    start = serializers.CharField(trim_whitespace=True, min_length=3)
    finish = serializers.CharField(trim_whitespace=True, min_length=3)

    def validate_start(self, value):
        try:
            validate_usa_location(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return value

    def validate_finish(self, value):
        try:
            validate_usa_location(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return value
