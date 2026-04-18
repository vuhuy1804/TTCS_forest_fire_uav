from rest_framework import serializers
from .models import ForestArea, GridCell, UAV, Mission, ClusterAssignment, RouteWaypoint, DetectionEvent

class ForestAreaSerializer(serializers.ModelSerializer):
    class Meta:
        model = ForestArea
        fields = ["id", "name", "bbox", "created_at"]

class GridCellSerializer(serializers.ModelSerializer):
    class Meta:
        model = GridCell
        fields = ["id", "area", "row", "col", "polygon", "center", "risk", "label", "confidence", "updated_at"]

class UAVSerializer(serializers.ModelSerializer):
    class Meta:
        model = UAV
        fields = ["id", "name", "color"]

class MissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Mission
        fields = ["id", "area", "k", "created_at"]

class ClusterAssignmentSerializer(serializers.ModelSerializer):
    cell = GridCellSerializer(read_only=True)
    uav = UAVSerializer(read_only=True)

    class Meta:
        model = ClusterAssignment
        fields = ["id", "mission", "cell", "cluster_id", "uav"]

class RouteWaypointSerializer(serializers.ModelSerializer):
    class Meta:
        model = RouteWaypoint
        fields = ["id", "mission", "uav", "order", "point"]

class DetectionEventSerializer(serializers.ModelSerializer):
    uav = UAVSerializer(read_only=True)
    cell = GridCellSerializer(read_only=True)

    class Meta:
        model = DetectionEvent
        fields = ["id", "mission", "cell", "uav", "label", "confidence", "source", "payload", "created_at"]