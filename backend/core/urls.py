from django.urls import path
from . import views

urlpatterns = [
    path("areas/", views.create_area),
    path("areas/<int:area_id>/grid/", views.generate_grid),
    path("areas/<int:area_id>/missions/", views.create_mission),

    path("missions/<int:mission_id>/kmeans/", views.run_kmeans),
    path("missions/<int:mission_id>/routes/", views.generate_routes),
    path("missions/<int:mission_id>/state/", views.mission_state),
    path("missions/<int:mission_id>/status/", views.update_mission_status),
    path("missions/<int:mission_id>/tick/", views.simulate_mission_tick),
    path("missions/<int:mission_id>/auto-simulation/", views.set_mission_auto_simulation),
    path("missions/<int:mission_id>/restart/", views.restart_mission),
    path("missions/<int:mission_id>/experiments/compare/", views.compare_experiment_strategies),

    path("detections/", views.ingest_detection),
]