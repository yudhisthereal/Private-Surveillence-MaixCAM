from typing import List, Tuple
from tools.polygon_checker import BodyInPolygonChecker, CheckMethod


class BenchAreaChecker:
    """
    Checker for bench areas.

    Bench areas are zones where sitting or lying down is considered safe.
    This checker uses BodyInPolygonChecker internally.
    
    Note: Bench areas should use conditional check methods:
    - If lying_down pose: use CheckMethod.TORSO
    - If sitting pose: use CheckMethod.HIP
    """

    def __init__(self):
        """Initialize BenchAreaChecker"""
        self._polygon_checker = BodyInPolygonChecker()

    @property
    def bench_polygons(self):
        return self._polygon_checker.polygons

    @bench_polygons.setter
    def bench_polygons(self, polygons: List[List[Tuple[float, float]]]):
        self._polygon_checker.polygons = polygons

    def update_bench_areas(self, polygons: List[List[Tuple[float, float]]]):
        """Update bench area polygons"""
        self._polygon_checker.polygons = polygons

    def add_bench_polygon(self, polygon: List[Tuple[float, float]]):
        """Add a bench polygon to the list"""
        self._polygon_checker.add_polygon(polygon)

    def clear_bench_polygons(self):
        """Clear all bench polygons"""
        self._polygon_checker.clear_polygons()

    def check_bench_area(self,
                        body_keypoints: List[Tuple[float, float, float]],
                        check_method: CheckMethod = CheckMethod.TORSO) -> bool:
        """
        Check if person is in bench area.

        Args:
            body_keypoints: List of (x, y, confidence) coordinates for COCO keypoints
            check_method: Which keypoints to check (should be TORSO for lying, HIP for sitting)

        Returns:
            True if all required keypoints are inside any bench polygon, False otherwise
        """
        return self._polygon_checker.body_in_polygons(body_keypoints, check_method)

    def is_in_bench_area(self,
                        body_keypoints: List[Tuple[float, float, float]],
                        check_method: CheckMethod = CheckMethod.TORSO) -> bool:
        """
        Alias for check_bench_area() for convenience.

        Args:
            body_keypoints: List of (x, y, confidence) coordinates for COCO keypoints
            check_method: Which keypoints to check (should be TORSO for lying, HIP for sitting)

        Returns:
            True if person is in bench area, False otherwise
        """
        return self.check_bench_area(body_keypoints, check_method)

    def get_containing_polygons(self,
                                body_keypoints: List[Tuple[float, float, float]],
                                check_method: CheckMethod = CheckMethod.TORSO) -> List[int]:
        """
        Get indices of bench polygons that contain all required body keypoints.

        Args:
            body_keypoints: List of (x, y, confidence) coordinates for COCO keypoints
            check_method: Which keypoints to check (should be TORSO for lying, HIP for sitting)

        Returns:
            List of indices of bench polygons that contain the body
        """
        return self._polygon_checker.get_containing_polygons(body_keypoints, check_method)
