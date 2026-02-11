from typing import List, Tuple
from tools.safe_area import BodyInPolygonChecker, CheckMethod
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
                        current_time_str: str = "12:00",
                        max_sleep_duration_min: int = 0,
                        bedtime_str: str = "",
                        wakeup_time_str: str = "",
                        check_method: CheckMethod = CheckMethod.TORSO) -> Tuple[bool, float, bool, str]:
        """
        Check if person is in couch area and for how long.

        Args:
            track_id: The track ID to check
            body_keypoints: List of (x, y, confidence) coordinates for COCO keypoints
            current_time_str: "HH:MM"
            max_sleep_duration_min: 0 = disabled
            bedtime_str: "HH:MM" start of night sleep
            wakeup_time_str: "HH:MM" end of night sleep
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
            
            # Helper to parse HH:MM to minutes from midnight
            def to_mins(t_str):
                try:
                    h, m = map(int, t_str.split(':'))
                    return h * 60 + m
                except:
                    return None

            cur_mins = to_mins(current_time_str)
            bed_result = to_mins(bedtime_str)
            wake_result = to_mins(wakeup_time_str)
            
            # Determine if it is "Night Window" (Safe to sleep)
            is_night = False
            if cur_mins is not None and bed_result is not None and wake_result is not None:
                if bed_result > wake_result:
                    if cur_mins >= bed_result or cur_mins < wake_result:
                        is_night = True
                else:
                    if bed_result <= cur_mins < wake_result:
                        is_night = True
            
            if is_night:
                # Night sleep is SAFE
                is_unsafe = False
            else:
                # Day Window
                # 1. Check Oversleeping (Sleeping past wakeup time)
                if cur_mins is not None and wake_result is not None:
                    # Check if "Past Wakeup" (e.g. within 3 hours after wakeup)
                    diff = cur_mins - wake_result
                    if 0 < diff < 180: # 3 hours window to call it "Oversleeping" vs "Afternoon Nap"
                         is_unsafe = True
                         unsafe_reason = "oversleeping" # Will map to enum later
                
                # Check Duration (Napping too long)
                if not is_unsafe and max_sleep_duration_min > 0:
                    if time_in_couch_sec > (max_sleep_duration_min * 60):
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
        Alias for check_couch_area() for convenience.

        Args:
            body_keypoints: List of (x, y, confidence) coordinates for COCO keypoints
            check_method: Which keypoints to check (should be TORSO for lying, HIP for sitting)

        Returns:
            True if person is in couch area, False otherwise
        """
        # Helper for compatibility
        return self.check_couch_area(0, body_keypoints, check_method=check_method)[0]

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
