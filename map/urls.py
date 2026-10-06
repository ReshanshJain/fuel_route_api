from django.urls import path
from .views import RouteView

urlpatterns = [
    path("map", RouteView.as_view(), name="route"),
    path("map/", RouteView.as_view(), name="route-with-slash"),
]
