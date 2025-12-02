import numpy as np
from collections import deque

class PoseEstimation:
    """Very small pose classifier: returns one of 'standing', 'sitting', 'bending_down', 'lying_down', or None"""
    
    def __init__(self, keypoints_window_size=5, missing_value=-1):
        self.keypoints_map_deque = deque(maxlen=keypoints_window_size)
        self.status = []
        self.pose_data = {}  # Store detailed pose data for external access
        self.missing_value = missing_value
        
        # Thresholds for limb length ratios
        self.thigh_calf_ratio_threshold = 0.7  # If thigh is significantly shorter than calf
        self.torso_leg_ratio_threshold = 0.5   # If torso is significantly shorter than leg

    def feed_keypoints_17(self, keypoints_17):
        # keypoints_17: flattened list/array [x0,y0, x1,y1, ..., x16,y16]
        try:
            keypoints = np.array(keypoints_17).reshape((-1, 2))
            if keypoints.shape != (17, 2):
                return None
        except:
            return None

        kp_map = {
            'Left Shoulder': keypoints[5],
            'Right Shoulder': keypoints[6],
            'Left Hip': keypoints[11],
            'Right Hip': keypoints[12],
            'Left Knee': keypoints[13],
            'Right Knee': keypoints[14],
            'Left Ankle': keypoints[15],
            'Right Ankle': keypoints[16]
        }

        return self.feed_keypoints_map(kp_map)

    def _is_frame_complete(self, keypoints_map):
        """Return True if none of the required keypoints contain the missing_value sentinel."""
        for k, v in keypoints_map.items():
            # v is an array-like [x, y]
            if v is None:
                return False
            # check both coordinates for sentinel
            if v[0] == self.missing_value or v[1] == self.missing_value:
                return False
        return True

    def _calculate_limb_lengths(self, km):
        """Calculate limb lengths and ratios for posture classification."""
        try:
            # Calculate thigh length (hip to knee)
            left_thigh = np.linalg.norm(km['Left Hip'] - km['Left Knee'])
            right_thigh = np.linalg.norm(km['Right Hip'] - km['Right Knee'])
            thigh_length = (left_thigh + right_thigh) / 2.0
            
            # Calculate calf length (knee to ankle)
            left_calf = np.linalg.norm(km['Left Knee'] - km['Left Ankle'])
            right_calf = np.linalg.norm(km['Right Knee'] - km['Right Ankle'])
            calf_length = (left_calf + right_calf) / 2.0
            
            # Calculate torso height (shoulder to hip)
            left_torso = np.linalg.norm(km['Left Shoulder'] - km['Left Hip'])
            right_torso = np.linalg.norm(km['Right Shoulder'] - km['Right Hip'])
            torso_height = (left_torso + right_torso) / 2.0
            
            # Calculate leg length (hip to ankle)
            left_leg = np.linalg.norm(km['Left Hip'] - km['Left Ankle'])
            right_leg = np.linalg.norm(km['Right Hip'] - km['Right Ankle'])
            leg_length = (left_leg + right_leg) / 2.0
            
            # Calculate ratios
            thigh_calf_ratio = thigh_length / calf_length if calf_length > 0 else 1.0
            torso_leg_ratio = torso_height / leg_length if leg_length > 0 else 1.0
            
            return thigh_calf_ratio, torso_leg_ratio
        except:
            return 1.0, 1.0

    def feed_keypoints_map(self, keypoints_map):
        # If current frame is incomplete, clear status and do not append — return None
        if not self._is_frame_complete(keypoints_map):
            self.status = []
            self.pose_data = {}
            return None

        # append the verified-complete frame for temporal smoothing
        self.keypoints_map_deque.append(keypoints_map)

        # compute averaged keypoints over the deque
        try:
            km = {
                key: sum(d[key] for d in self.keypoints_map_deque) / len(self.keypoints_map_deque)
                for key in self.keypoints_map_deque[0].keys()
            }

            # compute centers
            shoulder_center = (km['Left Shoulder'] + km['Right Shoulder']) / 2.0
            hip_center = (km['Left Hip'] + km['Right Hip']) / 2.0
            knee_center = (km['Left Knee'] + km['Right Knee']) / 2.0

            torso_vec = shoulder_center - hip_center
            thigh_vec = knee_center - hip_center

            up_vector = np.array([0.0, -1.0])

            # safe angle computation: if vector norm is zero, return None
            torso_norm = np.linalg.norm(torso_vec)
            thigh_norm = np.linalg.norm(thigh_vec)
            if torso_norm == 0 or thigh_norm == 0:
                self.status = []
                self.pose_data = {}
                return None

            torso_angle = np.degrees(np.arccos(np.clip(
                np.dot(torso_vec, up_vector) / (torso_norm * np.linalg.norm(up_vector)), -1.0, 1.0)))

            thigh_angle = np.degrees(np.arccos(np.clip(
                np.dot(thigh_vec, up_vector) / (thigh_norm * np.linalg.norm(up_vector)), -1.0, 1.0)))

            # convert thigh angle to "uprightness" where smaller is more upright
            thigh_uprightness = abs(thigh_angle - 180.0)

            # Calculate limb length ratios
            thigh_calf_ratio, torso_leg_ratio = self._calculate_limb_lengths(km)

            # Classification with limb length ratios
            if torso_angle < 30 and thigh_uprightness < 40:
                # Check if angles suggest standing but limb ratios suggest otherwise
                if thigh_calf_ratio < self.thigh_calf_ratio_threshold:
                    label = "sitting"  # Thigh is significantly shorter than calf
                elif torso_leg_ratio < self.torso_leg_ratio_threshold:
                    label = "bending_down"  # Torso is significantly shorter than leg
                else:
                    label = "standing"
            elif torso_angle < 30 and thigh_uprightness >= 40:
                label = "sitting"
            elif 30 <= torso_angle < 80 and thigh_uprightness < 60:
                label = "bending_down"
            else:
                label = "lying_down"

            # Store detailed pose data for external access (e.g., fall detection)
            self.pose_data = {
                'label': label,
                'torso_angle': torso_angle,
                'thigh_uprightness': thigh_uprightness,
                'thigh_calf_ratio': thigh_calf_ratio,
                'torso_leg_ratio': torso_leg_ratio,
                'thigh_angle': thigh_angle
            }
            
            self.status = [label]
            return self.pose_data
            
        except Exception as e:
            print(f"Pose estimation error: {e}")
            self.status = []
            self.pose_data = {}
            return None

    def evaluate_pose(self, keypoints):
        # returns pose data dict or None
        res = self.feed_keypoints_17(keypoints)
        if res is None:
            return None
        return self.pose_data

    def get_pose_data(self):
        """Get the latest pose data for external use"""
        return self.pose_data