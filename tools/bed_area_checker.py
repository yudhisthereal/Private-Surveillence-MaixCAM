import time
from typing import List, Tuple, Dict
from tools.polygon_checker import BodyInPolygonChecker, CheckMethod
from tools.time_utils import time_ms


class BedAreaChecker:
    """
    Checker for bed areas with time tracking.

    Monitors how long a person stays in bed areas and flags as unsafe
    if they remain in bed for too long (configurable threshold).
    """

    def __init__(self, too_long_threshold_ms: float = 5000):
        """
        Initialize BedAreaChecker.

        Args:
            too_long_threshold_sec: Time in seconds before being in bed is considered too long (default: 5.0)
        """
        self._polygon_checker = BodyInPolygonChecker()
        self._too_long_threshold_ms = too_long_threshold_ms

        # Track entry times for each track_id
        # Format: {track_id: entry_timestamp}
        self._bed_entry_times: Dict[int, float] = {}

        # Track current bed status for each track_id
        # Format: {track_id: is_in_bed}
        self._current_bed_status: Dict[int, bool] = {}

    @property
    def bed_polygons(self):
        return self._polygon_checker.polygons

    @bed_polygons.setter
    def bed_polygons(self, polygons: List[List[Tuple[float, float]]]):
        self._polygon_checker.polygons = polygons

    @property
    def too_long_threshold_sec(self) -> float:
        return self._too_long_threshold_ms

    @too_long_threshold_sec.setter
    def too_long_threshold_sec(self, value: float):
        self._too_long_threshold_ms = value

    def update_bed_areas(self, polygons: List[List[Tuple[float, float]]]):
        """Update bed area polygons"""
        self._polygon_checker.polygons = polygons

    def add_bed_polygon(self, polygon: List[Tuple[float, float]]):
        """Add a bed polygon to the list"""
        self._polygon_checker.add_polygon(polygon)

    def clear_bed_polygons(self):
        """Clear all bed polygons"""
        self._polygon_checker.clear_polygons()

    def check_bed_area(self,
                      track_id: int,
                      body_keypoints: List[Tuple[float, float, float]],
                      current_time_str: str = "12:00",
                      max_sleep_duration_min: int = 0,
                      bedtime_str: str = "",
                      wakeup_time_str: str = "",
                      check_method: CheckMethod = CheckMethod.FULL_BODY) -> Tuple[bool, float, bool, str]:
        """
        Check if person is in bed area and for how long.

        Args:
            track_id: The track ID to check
            body_keypoints: List of (x, y, confidence) coordinates for COCO keypoints
            current_time_str: "HH:MM"
            max_sleep_duration_min: 0 = disabled
            bedtime_str: "HH:MM" start of night sleep
            wakeup_time_str: "HH:MM" end of night sleep
            check_method: Which keypoints to check

        Returns:
            Tuple of (is_in_bed, time_in_bed_sec, is_unsafe, unsafe_reason)
        """
        is_in_bed = self._polygon_checker.body_in_polygons(body_keypoints, check_method)
        current_time = time_ms()

        # Update bed status and time tracking
        if is_in_bed:
            # Person is in bed area
            if track_id not in self._bed_entry_times:
                # Just entered bed area
                self._bed_entry_times[track_id] = current_time
                self._current_bed_status[track_id] = True

            time_in_bed_sec = (current_time - self._bed_entry_times[track_id]) / 1000.0
            
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
                    if time_in_bed_sec > (max_sleep_duration_min * 60):
                        is_unsafe = True
                        unsafe_reason = "sleep_too_long"

            return True, time_in_bed_sec, is_unsafe, unsafe_reason
        else:
            # Person is not in bed area
            if track_id in self._bed_entry_times:
                # Just left bed area
                del self._bed_entry_times[track_id]
                self._current_bed_status[track_id] = False

            return False, 0.0, False, "normal"

    def get_bed_time(self, track_id: int) -> float:
        """
        Get how long the track has been in bed area.

        Args:
            track_id: The track ID to check

        Returns:
            Time in seconds in bed area, or 0.0 if not in bed
        """
        if track_id in self._bed_entry_times:
            return time_ms() - self._bed_entry_times[track_id]
        return 0.0

    def is_in_bed(self, track_id: int) -> bool:
        """
        Check if track is currently in bed area.

        Args:
            track_id: The track ID to check

        Returns:
            True if currently in bed area, False otherwise
        """
        return self._current_bed_status.get(track_id, False)

    def is_in_bed_too_long(self, track_id: int) -> bool:
        """
        Check if track has been in bed area for too long.

        Args:
            track_id: The track ID to check

        Returns:
            True if in bed for longer than threshold, False otherwise
        """
        time_in_bed = self.get_bed_time(track_id)
        return time_in_bed > self._too_long_threshold_ms

    def reset_track(self, track_id: int):
        """
        Reset time tracking for a specific track.

        Args:
            track_id: The track ID to reset
        """
        if track_id in self._bed_entry_times:
            del self._bed_entry_times[track_id]
        if track_id in self._current_bed_status:
            del self._current_bed_status[track_id]

    def clear_all_times(self):
        """Clear all time tracking data"""
        self._bed_entry_times.clear()
        self._current_bed_status.clear()

    def get_all_entry_times(self) -> Dict[int, float]:
        """
        Get all track entry times (for debugging/monitoring).

        Returns:
            Dictionary mapping track_id to entry timestamp
        """
        return self._bed_entry_times.copy()

    def get_all_bed_status(self) -> Dict[int, bool]:
        """
        Get all current bed statuses (for debugging/monitoring).

        Returns:
            Dictionary mapping track_id to bed status
        """
        return self._current_bed_status.copy()
