from typing import List, Tuple
from tools.polygon_checker import BodyInPolygonChecker, CheckMethod


class FloorAreaChecker:
    """
    Checker for floor areas.

    Floor areas are zones where lying down is considered unsafe.
    This checker uses BodyInPolygonChecker internally.
    """

    def __init__(self):
        """Initialize FloorAreaChecker"""
        self._polygon_checker = BodyInPolygonChecker()

    @property
    def floor_polygons(self):
        return self._polygon_checker.polygons

    @floor_polygons.setter
    def floor_polygons(self, polygons: List[List[Tuple[float, float]]]):
        self._polygon_checker.polygons = polygons

    def update_floor_areas(self, polygons: List[List[Tuple[float, float]]]):
        """Update floor area polygons"""
        self._polygon_checker.polygons = polygons

    def add_floor_polygon(self, polygon: List[Tuple[float, float]]):
        """Add a floor polygon to the list"""
        self._polygon_checker.add_polygon(polygon)

    def clear_floor_polygons(self):
        """Clear all floor polygons"""
        self._polygon_checker.clear_polygons()

    def check_floor_area(self,
                        body_keypoints: List[Tuple[float, float, float]],
                        check_method: CheckMethod = CheckMethod.FULL_BODY) -> bool:
        """
        Check if person is in floor area.

        Args:
            body_keypoints: List of (x, y, confidence) coordinates for COCO keypoints
            check_method: Which keypoints to check

        Returns:
            True if all required keypoints are inside any floor polygon, False otherwise
        """
        return self._polygon_checker.body_in_polygons(body_keypoints, check_method)

    def is_in_floor_area(self,
                        body_keypoints: List[Tuple[float, float, float]],
                        check_method: CheckMethod = CheckMethod.FULL_BODY) -> bool:
        """
        Alias for check_floor_area() for convenience.

        Args:
            body_keypoints: List of (x, y, confidence) coordinates for COCO keypoints
            check_method: Which keypoints to check

        Returns:
            True if person is in floor area, False otherwise
        """
        return self.check_floor_area(body_keypoints, check_method)

    def get_containing_polygons(self,
                                body_keypoints: List[Tuple[float, float, float]],
                                check_method: CheckMethod = CheckMethod.FULL_BODY) -> List[int]:
        """
        Get indices of floor polygons that contain all required body keypoints.

        Args:
            body_keypoints: List of (x, y, confidence) coordinates for COCO keypoints
            check_method: Which keypoints to check

        Returns:
            List of indices of floor polygons that contain the body
        """
        return self._polygon_checker.get_containing_polygons(body_keypoints, check_method)
