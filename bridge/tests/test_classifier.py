"""Tests for thirdeye_bridge.classifier."""

import time
from unittest.mock import patch

from thirdeye_bridge.classifier import Classifier, TYPE_MAP, WATCHED_TYPES


class TestTypeMapping:
    def test_people_maps_to_person(self):
        assert TYPE_MAP["people"] == "person"

    def test_vehicle_maps_to_vehicle(self):
        assert TYPE_MAP["vehicle"] == "vehicle"

    def test_dog_cat_maps_to_animal(self):
        assert TYPE_MAP["dog_cat"] == "animal"

    def test_unknown_type_not_in_map(self):
        assert "face" not in TYPE_MAP
        assert "visitor" not in TYPE_MAP

    def test_watched_types_matches_map_keys(self):
        assert set(WATCHED_TYPES) == set(TYPE_MAP.keys())


class TestClassifierDetectionLifecycle:
    def test_start_returns_start_action(self):
        c = Classifier(debounce_sec=2.0)
        result = c.process_detection("cam1", "people", True)
        assert result == ("start", "person")

    def test_duplicate_start_returns_none(self):
        c = Classifier(debounce_sec=2.0)
        c.process_detection("cam1", "people", True)
        c.mark_started("cam1", "people", "evt-1", "person")
        result = c.process_detection("cam1", "people", True)
        assert result is None

    def test_end_returns_end_action(self):
        c = Classifier(debounce_sec=2.0)
        c.process_detection("cam1", "people", True)
        c.mark_started("cam1", "people", "evt-1", "person")
        result = c.process_detection("cam1", "people", False)
        assert result == ("end", "person")

    def test_end_without_start_returns_none(self):
        c = Classifier(debounce_sec=2.0)
        result = c.process_detection("cam1", "people", False)
        assert result is None

    def test_mark_ended_returns_active_detection(self):
        c = Classifier(debounce_sec=2.0)
        c.process_detection("cam1", "people", True)
        c.mark_started("cam1", "people", "evt-1", "person")
        active = c.mark_ended("cam1", "people")
        assert active is not None
        assert active.event_id == "evt-1"
        assert active.protect_type == "person"

    def test_mark_ended_without_active_returns_none(self):
        c = Classifier(debounce_sec=2.0)
        assert c.mark_ended("cam1", "people") is None


class TestClassifierDebounce:
    def test_restart_within_debounce_is_suppressed(self):
        c = Classifier(debounce_sec=2.0)
        # Start
        c.process_detection("cam1", "people", True)
        c.mark_started("cam1", "people", "evt-1", "person")
        # End
        c.process_detection("cam1", "people", False)
        c.mark_ended("cam1", "people")
        # Immediately restart — should be debounced
        result = c.process_detection("cam1", "people", True)
        assert result is None

    @patch("thirdeye_bridge.classifier.time")
    def test_restart_after_debounce_is_allowed(self, mock_time):
        mock_time.monotonic = time.monotonic
        c = Classifier(debounce_sec=0.01)
        # Start
        c.process_detection("cam1", "people", True)
        c.mark_started("cam1", "people", "evt-1", "person")
        # End
        c.process_detection("cam1", "people", False)
        c.mark_ended("cam1", "people")
        # Wait past debounce
        import time as _time
        _time.sleep(0.02)
        result = c.process_detection("cam1", "people", True)
        assert result == ("start", "person")


class TestClassifierMultiCamera:
    def test_different_cameras_independent(self):
        c = Classifier(debounce_sec=2.0)
        r1 = c.process_detection("cam1", "people", True)
        r2 = c.process_detection("cam2", "people", True)
        assert r1 == ("start", "person")
        assert r2 == ("start", "person")

    def test_different_types_independent(self):
        c = Classifier(debounce_sec=2.0)
        r1 = c.process_detection("cam1", "people", True)
        r2 = c.process_detection("cam1", "vehicle", True)
        assert r1 == ("start", "person")
        assert r2 == ("start", "vehicle")

    def test_unknown_type_returns_none(self):
        c = Classifier(debounce_sec=2.0)
        result = c.process_detection("cam1", "face", True)
        assert result is None

    def test_remove_camera_clears_state(self):
        c = Classifier(debounce_sec=2.0)
        c.process_detection("cam1", "people", True)
        c.mark_started("cam1", "people", "evt-1", "person")
        c.remove_camera("cam1")
        # After removal, start should work again
        result = c.process_detection("cam1", "people", True)
        assert result == ("start", "person")
