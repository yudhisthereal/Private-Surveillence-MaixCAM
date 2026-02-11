from typing import List, Tuple
from tools.safe_area import BodyInPolygonChecker, CheckMethod


class ChairAreaChecker:
    """
    Checker for chair areas.

    Chair areas are zones where sitting is considered safe.
    This checker uses BodyInPolygonChecker internally.
    
    Note: Chair areas should only be checked when pose_label is "sitting"
    and should use CheckMethod.HIP (hips keypoints only).
    """

    def __init__(self):
        """Initialize ChairAreaChecker"""
        self._polygon_checker = BodyInPolygonChecker()

    @property
    def chair_polygons(self):
        return self._polygon_checker.polygons

    @chair_polygons.setter
    def chair_polygons(self, polygons: List[List[Tuple[float, float]]]):
        self._polygon_checker.polygons = polygons

    def update_chair_areas(self, polygons: List[List[Tuple[float, float]]]):
        """Update chair area polygons"""
        self._polygon_checker.polygons = polygons

    def add_chair_polygon(self, polygon: List[Tuple[float, float]]):
        """Add a chair polygon to the list"""
        self._polygon_checker.add_polygon(polygon)

    def clear_chair_polygons(self):
        """Clear all chair polygons"""
        self._polygon_checker.clear_polygons()

    def check_chair_area(self,
                        body_keypoints: List[Tuple[float, float, float]],
                        check_method: CheckMethod = CheckMethod.HIP) -> bool:
        """
        Check if person is in chair area.

        Args:
            body_keypoints: List of (x, y, confidence) coordinates for COCO keypoints
            check_method: Which keypoints to check (default: HIP for chairs)

        Returns:
            True if all required keypoints are inside any chair polygon, False otherwise
        """
        return self._polygon_checker.body_in_polygons(body_keypoints, check_method)

    def is_in_chair_area(self,
                        body_keypoints: List[Tuple[float, float, float]],
                        check_method: CheckMethod = CheckMethod.HIP) -> bool:
        """
        Alias for check_chair_area() for convenience.

        Args:
            body_keypoints: List of (x, y, confidence) coordinates for COCO keypoints
            check_method: Which keypoints to check (default: HIP for chairs)

        Returns:
            True if person is in chair area, False otherwise
        """
        return self.check_chair_area(body_keypoints, check_method)

    def get_containing_polygons(self,
                                body_keypoints: List[Tuple[float, float, float]],
                                check_method: CheckMethod = CheckMethod.HIP) -> List[int]:
        """
        Get indices of chair polygons that contain all required body keypoints.

        Args:
            body_keypoints: List of (x, y, confidence) coordinates for COCO keypoints
            check_method: Which keypoints to check (default: HIP for chairs)

        Returns:
            List of indices of chair polygons that contain the body
        """
        return self._polygon_checker.get_containing_polygons(body_keypoints, check_method)
