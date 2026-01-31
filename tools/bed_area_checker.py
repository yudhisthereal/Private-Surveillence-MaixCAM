import time
from typing import List, Tuple, Dict, Optional
from tools.safe_area import BodyInPolygonChecker, CheckMethod


class BedAreaChecker:
    """
    Checker for bed areas with time tracking.

    Monitors how long a person stays in bed areas and flags as unsafe
    if they remain in bed for too long (configurable threshold).
    """

    def __init__(self, too_long_threshold_sec: float = 5.0):
        """
        Initialize BedAreaChecker.

        Args:
            too_long_threshold_sec: Time in seconds before being in bed is considered too long (default: 5.0)
        """
        self._polygon_checker = BodyInPolygonChecker()
        self._too_long_threshold_sec = too_long_threshold_sec

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
        return self._too_long_threshold_sec

    @too_long_threshold_sec.setter
    def too_long_threshold_sec(self, value: float):
        self._too_long_threshold_sec = value

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
                      check_method: CheckMethod = CheckMethod.FULL_BODY) -> Tuple[bool, float, bool]:
        """
        Check if person is in bed area and for how long.

        Args:
            track_id: The track ID to check
            body_keypoints: List of (x, y, confidence) coordinates for COCO keypoints
            check_method: Which keypoints to check

        Returns:
            Tuple of (is_in_bed, time_in_bed_sec, is_too_long)
        """
        is_in_bed = self._polygon_checker.body_in_polygons(body_keypoints, check_method)
        current_time = time.time()

        # Update bed status and time tracking
        if is_in_bed:
            # Person is in bed area
            if track_id not in self._bed_entry_times:
                # Just entered bed area
                self._bed_entry_times[track_id] = current_time
                self._current_bed_status[track_id] = True

            time_in_bed = current_time - self._bed_entry_times[track_id]
            is_too_long = time_in_bed > self._too_long_threshold_sec

            return True, time_in_bed, is_too_long
        else:
            # Person is not in bed area
            if track_id in self._bed_entry_times:
                # Just left bed area
                del self._bed_entry_times[track_id]
                self._current_bed_status[track_id] = False

            return False, 0.0, False

    def get_bed_time(self, track_id: int) -> float:
        """
        Get how long the track has been in bed area.

        Args:
            track_id: The track ID to check

        Returns:
            Time in seconds in bed area, or 0.0 if not in bed
        """
        if track_id in self._bed_entry_times:
            return time.time() - self._bed_entry_times[track_id]
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
        return time_in_bed > self._too_long_threshold_sec

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
