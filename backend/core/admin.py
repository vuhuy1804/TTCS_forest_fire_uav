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

@admin.register(UAV)
class UAVAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "status", "color")
    list_filter = ("status",)

@admin.register(Mission)
class MissionAdmin(admin.ModelAdmin):
    list_display = ("id", "area", "k", "status", "created_at")
    list_filter = ("status",)

admin.site.register(ForestArea)
admin.site.register(GridCell)
admin.site.register(ClusterAssignment)
admin.site.register(RouteWaypoint)
admin.site.register(DetectionEvent)