from enum import Enum
from typing import List, Tuple

class CheckMethod(Enum):
    HIP = 1
    TORSO = 2
    TORSO_HEAD = 3
    TORSO_HEAD_KNEES = 4
    FULL_BODY = 5

# COCO Keypoints indices (0-based)
class COCOKeypoints:
    NOSE = 0
    LEFT_EYE = 1
    RIGHT_EYE = 2
    LEFT_EAR = 3
    RIGHT_EAR = 4
    LEFT_SHOULDER = 5
    RIGHT_SHOULDER = 6
    LEFT_ELBOW = 7
    RIGHT_ELBOW = 8
    LEFT_WRIST = 9
    RIGHT_WRIST = 10
    LEFT_HIP = 11
    RIGHT_HIP = 12
    LEFT_KNEE = 13
    RIGHT_KNEE = 14
    LEFT_ANKLE = 15
    RIGHT_ANKLE = 16

class Point:
    def __init__(self, x: float, y: float):
        self.x = x
        self.y = y

class BodyInPolygonChecker:
    """
    General-purpose checker for determining if body keypoints are inside defined polygons.

    This is a base class that provides polygon checking functionality without any
    safety-specific logic. It can be used for safe areas, bed areas, floor areas, etc.
    """

    def __init__(self):
        self._polygons = []

    @property
    def polygons(self):
        return self._polygons

    @polygons.setter
    def polygons(self, polygons: List[List[Tuple[float, float]]]):
        self._polygons = polygons

    def add_polygon(self, polygon: List[Tuple[float, float]]):
        """Add a polygon to the list"""
        self._polygons.append(polygon)

    def clear_polygons(self):
        """Clear all polygons"""
        self._polygons.clear()

    def point_in_polygon(self, point: Point, polygon: List[Point]) -> bool:
        """
        Ray casting algorithm to check if point is inside polygon
        """
        num_vertices = len(polygon)
        x, y = point.x, point.y
        inside = False

        if num_vertices == 0:
            return False

        p1 = polygon[0]
        for i in range(1, num_vertices + 1):
            p2 = polygon[i % num_vertices]

            if y > min(p1.y, p2.y):
                if y <= max(p1.y, p2.y):
                    if x <= max(p1.x, p2.x):
                        if p1.y != p2.y:  # Avoid division by zero
                            x_intersection = (y - p1.y) * (p2.x - p1.x) / (p2.y - p1.y) + p1.x

                        if p1.x == p2.x or x <= x_intersection:
                            inside = not inside
            p1 = p2

        return inside

    def body_in_polygons(self,
                         body_keypoints: List[Tuple[float, float, float]],
                         check_method: CheckMethod = CheckMethod.FULL_BODY) -> bool:
        """
        Check if body keypoints are inside any of the defined polygons.

        Args:
            body_keypoints: List of (x, y, confidence) coordinates for COCO keypoints
            check_method: Which keypoints to check

        Returns:
            bool: True if all required keypoints are inside any polygon, False otherwise
        """
        if not body_keypoints or len(body_keypoints) < 17:
            return False

        if not self._polygons:
            return False  # No polygons defined, no guaranteed safe area.

        # Filter valid keypoints (confidence > 0.1)
        valid_keypoints = [(x, y) for x, y, conf in body_keypoints if conf > 0.1 and x > 0 and y > 0]

        if not valid_keypoints:
            return True  # If no valid keypoints, assume True

        # Select keypoints based on check method
        check_indices = self._get_check_indices(check_method)

        # Filter to only include valid keypoints within our available points
        points_to_check = []
        for idx in check_indices:
            if idx < len(valid_keypoints):
                x, y = valid_keypoints[idx]
                points_to_check.append(Point(x, y))

        if not points_to_check:
            return True  # If no points to check, assume True

        # Check if all points are inside any of the polygons
        for point in points_to_check:
            point_inside_any_polygon = False

            for polygon_coords in self._polygons:
                # Convert polygon coordinates to Point objects
                polygon_points = [Point(x, y) for x, y in polygon_coords]

                if self.point_in_polygon(point, polygon_points):
                    point_inside_any_polygon = True
                    break

            # If any required point is not inside any polygon, body is not in polygons
            if not point_inside_any_polygon:
                return False

        return True

    def get_containing_polygons(self,
                                body_keypoints: List[Tuple[float, float, float]],
                                check_method: CheckMethod = CheckMethod.FULL_BODY) -> List[int]:
        """
        Get indices of polygons that contain all required body keypoints.

        Returns:
            List of indices of polygons that contain the body
        """
        if not body_keypoints or len(body_keypoints) < 17:
            return []

        if not self._polygons:
            return []  # No polygons defined

        # Filter valid keypoints
        valid_keypoints = [(x, y) for x, y, conf in body_keypoints if conf > 0.1 and x > 0 and y > 0]

        if not valid_keypoints:
            return []

        # Get points to check
        check_indices = self._get_check_indices(check_method)

        points_to_check = []
        for idx in check_indices:
            if idx < len(valid_keypoints):
                x, y = valid_keypoints[idx]
                points_to_check.append(Point(x, y))

        if not points_to_check:
            return []

        # Find polygons that contain all points
        containing_polygon_indices = []

        for poly_idx, polygon_coords in enumerate(self._polygons):
            polygon_points = [Point(x, y) for x, y in polygon_coords]
            all_points_inside = True

            for point in points_to_check:
                if not self.point_in_polygon(point, polygon_points):
                    all_points_inside = False
                    break

            if all_points_inside:
                containing_polygon_indices.append(poly_idx)

        return containing_polygon_indices

    def _get_check_indices(self, check_method: CheckMethod) -> List[int]:
        """Get the keypoint indices to check based on the check method"""
        if check_method == CheckMethod.HIP:
            return [COCOKeypoints.LEFT_HIP, COCOKeypoints.RIGHT_HIP]

        elif check_method == CheckMethod.TORSO:
            return [
                COCOKeypoints.LEFT_SHOULDER, COCOKeypoints.RIGHT_SHOULDER,
                COCOKeypoints.LEFT_HIP, COCOKeypoints.RIGHT_HIP
            ]

        elif check_method == CheckMethod.TORSO_HEAD:
            return [
                COCOKeypoints.NOSE,
                COCOKeypoints.LEFT_SHOULDER, COCOKeypoints.RIGHT_SHOULDER,
                COCOKeypoints.LEFT_HIP, COCOKeypoints.RIGHT_HIP
            ]

        elif check_method == CheckMethod.TORSO_HEAD_KNEES:
            return [
                COCOKeypoints.NOSE,
                COCOKeypoints.LEFT_SHOULDER, COCOKeypoints.RIGHT_SHOULDER,
                COCOKeypoints.LEFT_HIP, COCOKeypoints.RIGHT_HIP,
                COCOKeypoints.LEFT_KNEE, COCOKeypoints.RIGHT_KNEE
            ]

        elif check_method == CheckMethod.FULL_BODY:
            return list(range(17))  # All COCO keypoints

        return []


class SafeAreaChecker:
    """
    Checker for safe zones - areas where the person is allowed to be.

    This uses BodyInPolygonChecker internally and adds safety-specific logic.
    """

    def __init__(self):
        self._polygon_checker = BodyInPolygonChecker()

    @property
    def safe_polygons(self):
        return self._polygon_checker.polygons

    @safe_polygons.setter
    def safe_polygons(self, polygons: List[List[Tuple[float, float]]]):
        self._polygon_checker.polygons = polygons

    def add_safe_polygon(self, polygon: List[Tuple[float, float]]):
        """Add a safe polygon to the list"""
        self._polygon_checker.add_polygon(polygon)

    def clear_safe_polygons(self):
        """Clear all safe polygons"""
        self._polygon_checker.clear_polygons()

    def body_in_safe_zone(self,
                         body_keypoints: List[Tuple[float, float, float]],
                         check_method: CheckMethod = CheckMethod.FULL_BODY) -> bool:
        """
        Check if body keypoints are inside any safe polygon.

        Args:
            body_keypoints: List of (x, y, confidence) coordinates for COCO keypoints
            check_method: Which keypoints to check for safety

        Returns:
            bool: True if all required keypoints are inside any safe polygon, False otherwise
        """
        return self._polygon_checker.body_in_polygons(body_keypoints, check_method)

    def get_containing_polygons(self,
                                body_keypoints: List[Tuple[float, float, float]],
                                check_method: CheckMethod = CheckMethod.FULL_BODY) -> List[int]:
        """
        Get indices of safe polygons that contain all required body keypoints.

        Returns:
            List of indices of safe polygons that contain the body
        """
        return self._polygon_checker.get_containing_polygons(body_keypoints, check_method)


# Backward compatibility alias
BodySafetyChecker = SafeAreaChecker
