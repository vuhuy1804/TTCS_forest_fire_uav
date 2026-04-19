from django.db import models

class ForestArea(models.Model):
    name = models.CharField(max_length=200, default="Forest Area")
    # GeoJSON Polygon
    geometry = models.JSONField(null=True, blank=True)
    # bbox: [minLng, minLat, maxLng, maxLat] (tự tính từ geometry)
    bbox = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.id} - {self.name}"


class GridCell(models.Model):
    area = models.ForeignKey(ForestArea, on_delete=models.CASCADE, related_name="cells")
    row = models.IntegerField()
    col = models.IntegerField()
    # polygon GeoJSON (rectangle)
    polygon = models.JSONField()
    center = models.JSONField()  # {"lat":..., "lng":...}
    risk = models.FloatField(default=1.0)  # trọng số rủi ro (có thể dùng sau) :contentReference[oaicite:3]{index=3}

    # realtime labels từ AI: 0 bình thường, 1 khói, 2 lửa
    label = models.IntegerField(default=0)
    confidence = models.FloatField(default=0.0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("area", "row", "col")


class UAV(models.Model):
    STATUS_CHOICES = (
        ("idle", "idle"),
        ("assigned", "assigned"),
        ("running", "running"),
        ("paused", "paused"),
        ("offline", "offline"),
    )

    name = models.CharField(max_length=50)
    color = models.CharField(max_length=20, default="#3388ff")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="idle")

    def __str__(self):
        return self.name


class Mission(models.Model):
    STATUS_CHOICES = (
        ("draft", "draft"),
        ("planned", "planned"),
        ("running", "running"),
        ("paused", "paused"),
        ("finished", "finished"),
    )

    area = models.ForeignKey(ForestArea, on_delete=models.CASCADE, related_name="missions")
    k = models.IntegerField(default=3)  # số UAV = số cụm K-means :contentReference[oaicite:4]{index=4}
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="draft")
    created_at = models.DateTimeField(auto_now_add=True)


class ClusterAssignment(models.Model):
    mission = models.ForeignKey(Mission, on_delete=models.CASCADE, related_name="assignments")
    cell = models.ForeignKey(GridCell, on_delete=models.CASCADE)
    cluster_id = models.IntegerField()
    uav = models.ForeignKey(UAV, on_delete=models.SET_NULL, null=True, blank=True)


class RouteWaypoint(models.Model):
    mission = models.ForeignKey(Mission, on_delete=models.CASCADE, related_name="waypoints")
    uav = models.ForeignKey(UAV, on_delete=models.CASCADE)
    order = models.IntegerField()
    point = models.JSONField()  # {"lat":..., "lng":...}

    class Meta:
        unique_together = ("mission", "uav", "order")
        ordering = ["order"]


class DetectionEvent(models.Model):
    LABEL_CHOICES = (
        (0, "normal"),
        (1, "smoke"),
        (2, "fire"),
    )

    SOURCE_CHOICES = (
        ("mock", "mock"),
        ("ai_service", "ai_service"),
        ("manual", "manual"),
    )

    mission = models.ForeignKey(Mission, on_delete=models.CASCADE, related_name="detections")
    cell = models.ForeignKey(GridCell, on_delete=models.CASCADE, related_name="detection_events")
    uav = models.ForeignKey(UAV, on_delete=models.SET_NULL, null=True, blank=True)
    label = models.IntegerField(choices=LABEL_CHOICES)
    confidence = models.FloatField(default=0.0)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default="mock")
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"DetectionEvent(id={self.id}, cell={self.cell_id}, label={self.label}, source={self.source})"