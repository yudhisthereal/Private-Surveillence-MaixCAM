from typing import List, Tuple
from tools.polygon_checker import BodyInPolygonChecker, CheckMethod
from tools.time_utils import time_ms


class CouchAreaChecker:
    """
    Checker for couch areas.

    Couch areas are zones where sitting or lying down is considered safe.
    This checker uses BodyInPolygonChecker internally.
    
    Note: Couch areas should use conditional check methods:
    - If lying_down pose: use CheckMethod.TORSO
    - If sitting pose: use CheckMethod.HIP
    """

    def __init__(self):
        """Initialize CouchAreaChecker"""
        self._polygon_checker = BodyInPolygonChecker()
        
        # Track entry times for each track_id
        # Format: {track_id: entry_timestamp}
        self._couch_entry_times = {}

        # Track current couch status for each track_id
        # Format: {track_id: is_in_couch}
        self._current_couch_status = {}

    @property
    def couch_polygons(self):
        return self._polygon_checker.polygons

    @couch_polygons.setter
    def couch_polygons(self, polygons: List[List[Tuple[float, float]]]):
        self._polygon_checker.polygons = polygons

    def update_couch_areas(self, polygons: List[List[Tuple[float, float]]]):
        """Update couch area polygons"""
        self._polygon_checker.polygons = polygons

    def add_couch_polygon(self, polygon: List[Tuple[float, float]]):
        """Add a couch polygon to the list"""
        self._polygon_checker.add_polygon(polygon)

    def clear_couch_polygons(self):
        """Clear all couch polygons"""
        self._polygon_checker.clear_polygons()

    def check_couch_area(self,
                        track_id: int,
                        body_keypoints: List[Tuple[float, float, float]],
                        max_sleep_duration_min: int = 0,
                        check_method: CheckMethod = CheckMethod.TORSO) -> Tuple[bool, float, bool, str]:
        """
        Check if person is in couch area and for how long.

        Args:
            track_id: The track ID to check
            body_keypoints: List of (x, y, confidence) coordinates for COCO keypoints
            max_sleep_duration_min: Maximum allowed sleep duration in minutes (0 = disabled)
            check_method: Which keypoints to check

        Returns:
            Tuple of (is_in_couch, time_in_couch_sec, is_unsafe, unsafe_reason)
        """
        is_in_couch = self._polygon_checker.body_in_polygons(body_keypoints, check_method)
        current_time = time_ms()

        # Update couch status and time tracking
        if is_in_couch:
            # Person is in couch area
            if track_id not in self._couch_entry_times:
                # Just entered couch area
                self._couch_entry_times[track_id] = current_time
                self._current_couch_status[track_id] = True

            time_in_couch_sec = (current_time - self._couch_entry_times[track_id]) / 1000.0
            
            # --- Check Logic ---
            is_unsafe = False
            unsafe_reason = "normal"
            
            # Check Duration (Sleeping too long)
            if max_sleep_duration_min > 0:
                max_sleep_duration_sec = max_sleep_duration_min * 60
                if time_in_couch_sec > max_sleep_duration_sec:
                    is_unsafe = True
                    unsafe_reason = "sleep_too_long"

            return True, time_in_couch_sec, is_unsafe, unsafe_reason
        else:
            # Person is not in couch area
            if track_id in self._couch_entry_times:
                # Just left couch area
                del self._couch_entry_times[track_id]
                self._current_couch_status[track_id] = False

            return False, 0.0, False, "normal"

    def is_in_couch_area(self,
                        body_keypoints: List[Tuple[float, float, float]],
                        check_method: CheckMethod = CheckMethod.TORSO) -> bool:
        """
        Check if person is in couch area (without time tracking).

        Args:
            body_keypoints: List of (x, y, confidence) coordinates for COCO keypoints
            check_method: Which keypoints to check

        Returns:
            True if person is in couch area, False otherwise
        """
        return self._polygon_checker.body_in_polygons(body_keypoints, check_method)

    def reset_track(self, track_id: int):
        """Reset time tracking for a specific track"""
        self._couch_entry_times.pop(track_id, None)
        self._current_couch_status.pop(track_id, None)

    def clear_all_times(self):
        """Clear all time tracking data"""
        self._couch_entry_times.clear()
        self._current_couch_status.clear()

    def get_containing_polygons(self,
                                body_keypoints: List[Tuple[float, float, float]],
                                check_method: CheckMethod = CheckMethod.TORSO) -> List[int]:
        """
        Get indices of couch polygons that contain all required body keypoints.

        Args:
            body_keypoints: List of (x, y, confidence) coordinates for COCO keypoints
            check_method: Which keypoints to check (should be TORSO for lying, HIP for sitting)

        Returns:
            List of indices of couch polygons that contain the body
        """
        return self._polygon_checker.get_containing_polygons(body_keypoints, check_method)