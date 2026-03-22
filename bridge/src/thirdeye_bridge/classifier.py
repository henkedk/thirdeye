"""Detection type mapping, debounce, and start/end tracking."""

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Reolink Baichuan AI type -> Protect smartDetectObject type
TYPE_MAP: dict[str, str] = {
    "people": "person",
    "vehicle": "vehicle",
    "dog_cat": "animal",
}

# All Reolink AI types we monitor
WATCHED_TYPES = list(TYPE_MAP.keys())


@dataclass
class ActiveDetection:
    """Tracks an in-progress detection interval."""
    protect_type: str
    event_id: str
    started_at: float  # monotonic time


@dataclass
class CameraDetectionState:
    """Per-camera detection state for debouncing and start/end tracking."""
    # reolink_type -> ActiveDetection (if currently active)
    active: dict[str, ActiveDetection] = field(default_factory=dict)
    # reolink_type -> monotonic time of last detection end (for debounce)
    last_end: dict[str, float] = field(default_factory=dict)


class Classifier:
    """Maps Reolink AI events to Protect types with debounce logic.

    Tracks detection intervals per camera per type. Merges detections
    within debounce_sec of each other into a single event.
    """

    def __init__(self, debounce_sec: float = 2.0):
        self.debounce_sec = debounce_sec
        # camera_id -> CameraDetectionState
        self._states: dict[str, CameraDetectionState] = {}

    def get_state(self, camera_id: str) -> CameraDetectionState:
        if camera_id not in self._states:
            self._states[camera_id] = CameraDetectionState()
        return self._states[camera_id]

    def remove_camera(self, camera_id: str) -> None:
        self._states.pop(camera_id, None)

    def process_detection(
        self,
        camera_id: str,
        reolink_type: str,
        is_detected: bool,
    ) -> tuple[str, str] | None:
        """Process a detection state change.

        Returns:
            ("start", protect_type) - new detection started, caller should send /event/start
            ("end", protect_type) - detection ended, caller should send /event/end
            None - no action needed (debounced or unchanged)
        """
        protect_type = TYPE_MAP.get(reolink_type)
        if protect_type is None:
            return None

        state = self.get_state(camera_id)
        now = time.monotonic()

        if is_detected:
            if reolink_type in state.active:
                # Already active, no action
                return None
            # Check debounce: if we recently ended, suppress the start
            last_end = state.last_end.get(reolink_type, 0)
            if now - last_end < self.debounce_sec and reolink_type in state.last_end:
                # Within debounce window — this is a continuation, not a new event.
                # Re-activate with the previous event (caller handles re-linking).
                return None
            return ("start", protect_type)
        else:
            if reolink_type not in state.active:
                # Not active, no action
                return None
            state.last_end[reolink_type] = now
            return ("end", protect_type)

    def mark_started(self, camera_id: str, reolink_type: str, event_id: str, protect_type: str) -> None:
        """Record that a detection event has been started with the injector."""
        state = self.get_state(camera_id)
        state.active[reolink_type] = ActiveDetection(
            protect_type=protect_type,
            event_id=event_id,
            started_at=time.monotonic(),
        )

    def mark_ended(self, camera_id: str, reolink_type: str) -> ActiveDetection | None:
        """Record that a detection has ended. Returns the active detection that was ended."""
        state = self.get_state(camera_id)
        return state.active.pop(reolink_type, None)

    def get_active(self, camera_id: str, reolink_type: str) -> ActiveDetection | None:
        """Get the active detection for a camera/type, if any."""
        state = self.get_state(camera_id)
        return state.active.get(reolink_type)
