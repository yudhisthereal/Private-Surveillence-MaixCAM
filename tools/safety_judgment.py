from typing import List, Tuple, Optional, Dict
from tools.safe_area import CheckMethod
from tools.bed_area_checker import BedAreaChecker
from tools.floor_area_checker import FloorAreaChecker
from tools.safe_area import SafeAreaChecker


class SafetyReason:
    """Reason codes for safety status changes"""
    LYING_ON_FLOOR = "lying_on_floor"
    IN_BED_TOO_LONG = "in_bed_too_long"
    LYING_OUTSIDE_SAFE = "lying_outside_safe"
    SAFE_IN_SAFE_AREA = "safe_in_safe_area"
    SAFE_TRACKING = "safe_tracking"


class SafetyJudgment:
    """
    Combines results from BedAreaChecker, FloorAreaChecker, and SafeAreaChecker
    to make the final safe/unsafe determination.

    Rules (in order of priority):
    1. Fall detection always takes precedence (handled by caller)
    2. If lying_down AND in_floor_area → UNSAFE (lying on floor)
    3. If in_bed_area AND time > threshold → UNSAFE (in bed too long)
    4. If lying_down AND not_in_safe_area → UNSAFE (lying down outside safe zone)
    5. Otherwise → SAFE (tracking)
    """

    def __init__(self,
                 bed_area_checker: Optional[BedAreaChecker] = None,
                 floor_area_checker: Optional[FloorAreaChecker] = None,
                 safe_area_checker: Optional[SafeAreaChecker] = None,
                 check_method: CheckMethod = CheckMethod.TORSO_HEAD):
        """
        Initialize SafetyJudgment.

        Args:
            bed_area_checker: BedAreaChecker instance (optional, can be None)
            floor_area_checker: FloorAreaChecker instance (optional, can be None)
            safe_area_checker: SafeAreaChecker instance (optional, can be None)
            check_method: CheckMethod to use for all area checkers
        """
        self.bed_area_checker = bed_area_checker
        self.floor_area_checker = floor_area_checker
        self.safe_area_checker = safe_area_checker
        self.check_method = check_method

    def evaluate_safety(self,
                       track_id: int,
                       body_keypoints: List[Tuple[float, float, float]],
                       pose_label: str) -> Tuple[bool, Optional[str], Dict]:
        """
        Evaluate safety status based on all area checkers.

        Reads check_method from control_flags dynamically every call to allow
        runtime changes via web interface.

        Args:
            track_id: The track ID to evaluate
            body_keypoints: List of (x, y, confidence) coordinates for COCO keypoints
            pose_label: Pose classification label (e.g., "lying_down", "standing", etc.)

        Returns:
            Tuple of (is_safe, reason, details)
            - is_safe: False if unsafe, True if safe
            - reason: SafetyReason code or None if safe
            - details: Dictionary with detailed check results for debugging
        """
        # Read check_method from control_flags dynamically
        try:
            from control_manager import get_flag
            check_method_value = get_flag("check_method", 3)
            # Map integer value to CheckMethod enum
            check_method_map = {
                1: CheckMethod.HIP,
                2: CheckMethod.TORSO,
                3: CheckMethod.TORSO_HEAD,
                4: CheckMethod.TORSO_HEAD_KNEES,
                5: CheckMethod.FULL_BODY
            }
            check_method = check_method_map.get(check_method_value, CheckMethod.TORSO_HEAD)
        except ImportError:
            # Fallback to instance check_method if control_manager not available
            check_method = self.check_method

        is_lying_down = pose_label == "lying_down"

        # Initialize details dictionary
        details = {
            "pose_label": pose_label,
            "is_lying_down": is_lying_down,
            "in_bed_area": False,
            "time_in_bed": 0.0,
            "in_bed_too_long": False,
            "in_floor_area": False,
            "in_safe_area": False,
            "check_method": check_method,
        }

        # Rule 2: If lying_down AND in_floor_area → UNSAFE (lying on floor)
        if self.floor_area_checker is not None and is_lying_down:
            in_floor = self.floor_area_checker.check_floor_area(body_keypoints, check_method)
            details["in_floor_area"] = in_floor

            if in_floor:
                # Person is lying on the floor - UNSAFE
                return False, SafetyReason.LYING_ON_FLOOR, details

        # Rule 3: If in_bed_area AND time > threshold → UNSAFE (in bed too long)
        if self.bed_area_checker is not None:
            is_in_bed, time_in_bed, is_too_long = self.bed_area_checker.check_bed_area(
                track_id, body_keypoints, check_method
            )
            details["in_bed_area"] = is_in_bed
            details["time_in_bed"] = time_in_bed
            details["in_bed_too_long"] = is_too_long

            if is_too_long:
                # Person has been in bed for too long - UNSAFE
                return False, SafetyReason.IN_BED_TOO_LONG, details

        # Rule 4: If lying_down AND not_in_safe_area → UNSAFE (lying down outside safe zone)
        if self.safe_area_checker is not None and is_lying_down:
            in_safe = self.safe_area_checker.body_in_safe_zone(body_keypoints, check_method)
            details["in_safe_area"] = in_safe

            if not in_safe:
                # Person is lying down outside safe zone - UNSAFE
                return False, SafetyReason.LYING_OUTSIDE_SAFE, details

        # Update in_safe_area in details even if not lying down
        if self.safe_area_checker is not None and not is_lying_down:
            details["in_safe_area"] = self.safe_area_checker.body_in_safe_zone(
                body_keypoints, check_method
            )

        # Rule 5: Otherwise → SAFE
        if details.get("in_safe_area", False):
            return True, SafetyReason.SAFE_IN_SAFE_AREA, details
        else:
            return True, SafetyReason.SAFE_TRACKING, details

    def reset_bed_tracking(self, track_id: int):
        """
        Reset bed time tracking for a specific track.

        Call this when a track is lost or re-identified.

        Args:
            track_id: The track ID to reset
        """
        if self.bed_area_checker is not None:
            self.bed_area_checker.reset_track(track_id)

    def clear_all_bed_tracking(self):
        """
        Clear all bed time tracking data.

        Call this when resetting the tracking system.
        """
        if self.bed_area_checker is not None:
            self.bed_area_checker.clear_all_times()

    def get_check_method(self) -> CheckMethod:
        """Get the current check method"""
        return self.check_method

    def set_check_method(self, check_method: CheckMethod):
        """Set the check method for all area checks"""
        self.check_method = check_method
