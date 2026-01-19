import numpy as np
from collections import deque
import random
import math
import time


class PoseEstimation:
    """
    Camera-side pose estimation with optional HME feature encryption
    and mandatory plain-domain pose classification fallback.
    """

    p1 = 234406548094233827948571379965547188853
    q1 = 583457592311129510314141861330330044443
    u = 2355788435550222327802749264573303139783

    def __init__(self, keypoints_window_size=5, missing_value=-1, hme_enabled=True):
        self.keypoints_map_deque = deque(maxlen=keypoints_window_size)
        self.status = []
        self.pose_data = {}
        self.missing_value = missing_value
        self.hme_enabled = hme_enabled

        self.thigh_calf_ratio_threshold = 0.7
        self.torso_leg_ratio_threshold = 0.5

    def feed_keypoints_17(self, keypoints_17, use_hme=None):
        try:
            keypoints = np.array(keypoints_17).reshape((-1, 2))
            if keypoints.shape != (17, 2):
                return None
        except Exception:
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

        hme_mode = use_hme if use_hme is not None else self.hme_enabled
        return self.feed_keypoints_map(kp_map, hme_mode)

    def _is_frame_complete(self, keypoints_map):
        for v in keypoints_map.values():
            if v is None:
                return False
            if v[0] == self.missing_value or v[1] == self.missing_value:
                return False
        return True

    def _calculate_limb_lengths_and_ratios(self, km):
        try:
            thigh = (
                np.linalg.norm(km['Left Hip'] - km['Left Knee']) +
                np.linalg.norm(km['Right Hip'] - km['Right Knee'])
            ) / 2.0

            calf = (
                np.linalg.norm(km['Left Knee'] - km['Left Ankle']) +
                np.linalg.norm(km['Right Knee'] - km['Right Ankle'])
            ) / 2.0

            torso = (
                np.linalg.norm(km['Left Shoulder'] - km['Left Hip']) +
                np.linalg.norm(km['Right Shoulder'] - km['Right Hip'])
            ) / 2.0

            leg = (
                np.linalg.norm(km['Left Hip'] - km['Left Ankle']) +
                np.linalg.norm(km['Right Hip'] - km['Right Ankle'])
            ) / 2.0

            thigh_calf_ratio = thigh / calf if calf > 0 else 1.0
            torso_leg_ratio = torso / leg if leg > 0 else 1.0

            return thigh, calf, torso, leg, thigh_calf_ratio, torso_leg_ratio
        except Exception:
            return 0.0, 0.0, 0.0, 0.0, 1.0, 1.0

    def _classify_pose_plain(self, torso_angle, thigh_uprightness, thigh_calf_ratio, torso_leg_ratio):
        a = 1 if torso_angle > 30.0 else 0
        b = 1 if thigh_uprightness > 40.0 else 0
        c = 1 if torso_angle > 80.0 else 0
        d = 1 if thigh_calf_ratio < 0.7 else 0
        e = 1 if torso_leg_ratio < 0.5 else 0
        f = 1 if thigh_uprightness > 60.0 else 0

        not_a = 1 - a
        not_b = 1 - b
        not_c = 1 - c
        not_f = 1 - f

        lsb = (a & b & d) | (a & not_b) | (not_a & c & not_f)
        msb = (a & b & (1 - d) & e) | not_a | not_c

        pose_code = (msb << 1) | lsb

        pose_map = {
            0: "standing",
            1: "sitting",
            2: "bending_down",
            3: "lying_down"
        }

        return pose_map.get(pose_code, "unknown"), pose_code, {
            'a': a, 'b': b, 'c': c, 'd': d, 'e': e, 'f': f
        }

    def feed_keypoints_map(self, keypoints_map, use_hme=True):
        if not self._is_frame_complete(keypoints_map):
            self.status = []
            self.pose_data = {}
            return None

        self.keypoints_map_deque.append(keypoints_map)

        try:
            km = {
                k: sum(d[k] for d in self.keypoints_map_deque) / len(self.keypoints_map_deque)
                for k in keypoints_map.keys()
            }

            shoulder_center = (km['Left Shoulder'] + km['Right Shoulder']) / 2.0
            hip_center = (km['Left Hip'] + km['Right Hip']) / 2.0
            knee_center = (km['Left Knee'] + km['Right Knee']) / 2.0

            torso_vec = shoulder_center - hip_center
            thigh_vec = knee_center - hip_center
            up = np.array([0.0, -1.0])

            torso_norm = np.linalg.norm(torso_vec)
            thigh_norm = np.linalg.norm(thigh_vec)
            if torso_norm == 0 or thigh_norm == 0:
                return None

            torso_angle = np.degrees(np.arccos(
                np.clip(np.dot(torso_vec, up) / torso_norm, -1.0, 1.0)
            ))

            thigh_angle = np.degrees(np.arccos(
                np.clip(np.dot(thigh_vec, up) / thigh_norm, -1.0, 1.0)
            ))

            thigh_uprightness = abs(thigh_angle - 180.0)

            (
                thigh_len, calf_len, torso_h, leg_len,
                thigh_calf_ratio, torso_leg_ratio
            ) = self._calculate_limb_lengths_and_ratios(km)

            plain_label, plain_code, flags = self._classify_pose_plain(
                torso_angle, thigh_uprightness,
                thigh_calf_ratio, torso_leg_ratio
            )

            self.pose_data = {
                'raw_features': {
                    'torso_angle': torso_angle,
                    'thigh_uprightness': thigh_uprightness,
                    'thigh_length': thigh_len,
                    'calf_length': calf_len,
                    'torso_height': torso_h,
                    'leg_length': leg_len,
                    'thigh_calf_ratio': thigh_calf_ratio,
                    'torso_leg_ratio': torso_leg_ratio
                },
                'plain_label': plain_label,
                'plain_pose_code': plain_code,
                'plain_comparison_flags': flags,
                'label': plain_label,
                'pose_code': plain_code,
                'hme_enabled': use_hme,
                'hme_label_received': False,
                'hme_label': None,
                'timestamp': time.time(),
                'frame_complete': True
            }

            if use_hme:
                Tra = self._truncate(torso_angle)
                Tha = self._truncate(thigh_uprightness)
                Thl = self._truncate(thigh_len)
                cl = self._truncate(calf_len)
                Trl = self._truncate(torso_h)
                ll = self._truncate(leg_len)

                self.pose_data['int_features'] = {
                    'Tra': Tra, 'Tha': Tha,
                    'Thl': Thl, 'cl': cl,
                    'Trl': Trl, 'll': ll
                }

                self.pose_data['encrypted_features'] = {
                    'Tra': self._encrypt_simple(Tra),
                    'Tha': self._encrypt_simple(Tha),
                    'Thl': self._encrypt_simple(Thl),
                    'cl': self._encrypt_simple(cl),
                    'Trl': self._encrypt_simple(Trl),
                    'll': self._encrypt_simple(ll)
                }

                self.status = ['plain_fallback', 'hme_pending']
            else:
                self.status = ['plain_only']

            return self.pose_data

        except Exception:
            self.status = []
            self.pose_data = {}
            return None

    def _truncate(self, num):
        return math.trunc(num * 100)

    def _encrypt_simple(self, m):
        g = random.randint(1, 2**32 - 1)
        return [
            ((g * self.u) + m) % self.p1,
            ((g * self.u) + m) % self.q1
        ]

    def evaluate_pose(self, keypoints, use_hme=None):
        return self.feed_keypoints_17(keypoints, use_hme)

    def get_encrypted_features(self):
        return self.pose_data.get('encrypted_features') if self.pose_data else None

    def get_plain_label(self):
        return self.pose_data.get('plain_label') if self.pose_data else None

    def set_hme_pose_label(self, hme_label, pose_code=None):
        if not self.pose_data:
            return
        self.pose_data['hme_label'] = hme_label
        self.pose_data['hme_label_received'] = True
        self.pose_data['label'] = hme_label
        if pose_code is not None:
            self.pose_data['pose_code'] = pose_code
        self.status = ['hme_verified'] if hme_label == self.pose_data['plain_label'] else ['hme_updated']

    def get_pose_data(self):
        return self.pose_data

    def get_current_label(self):
        return self.pose_data.get('label', 'unknown') if self.pose_data else 'unknown'

    def enable_hme(self, enabled=True):
        self.hme_enabled = enabled

    def reset(self):
        self.keypoints_map_deque.clear()
        self.status = []
        self.pose_data = {}

    def get_status(self):
        return self.status
