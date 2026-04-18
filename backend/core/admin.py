from django.contrib import admin
from .models import (
    ForestArea,
    GridCell,
    UAV,
    Mission,
    ClusterAssignment,
    RouteWaypoint,
    DetectionEvent,
)

admin.site.register(ForestArea)
admin.site.register(GridCell)
admin.site.register(UAV)
admin.site.register(Mission)
admin.site.register(ClusterAssignment)
admin.site.register(RouteWaypoint)
admin.site.register(DetectionEvent)