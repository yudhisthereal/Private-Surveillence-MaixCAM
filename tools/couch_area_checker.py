from typing import List, Tuple
from tools.safe_area import BodyInPolygonChecker, CheckMethod


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
                        body_keypoints: List[Tuple[float, float, float]],
                        check_method: CheckMethod = CheckMethod.TORSO) -> bool:
        """
        Check if person is in couch area.

        Args:
            body_keypoints: List of (x, y, confidence) coordinates for COCO keypoints
            check_method: Which keypoints to check (should be TORSO for lying, HIP for sitting)

        Returns:
            True if all required keypoints are inside any couch polygon, False otherwise
        """
        return self._polygon_checker.body_in_polygons(body_keypoints, check_method)

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
        return self.check_couch_area(body_keypoints, check_method)

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
