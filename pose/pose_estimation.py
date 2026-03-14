import numpy as np
from collections import deque
import random
import math
import time
from debug_config import DebugLogger

_logger = DebugLogger(tag="INT_FEATURES", instance_enable=True)


class PoseEstimation:
    """
    Camera-side pose estimation with optional HME feature encryption
    and mandatory plain-domain pose classification fallback.
    """

    p1 = 234406548094233827948571379965547188853
    q1 = 583457592311129510314141861330330044443
    u = 2355788435550222327802749264573303139783
    
    r = 696522972436164062959242838052087531431
    s = 374670603170509799404699393785831797599
    t = 443137959904584298054176676987615849169
    w = 391475886865055383118586393345880578361

    n1 = p1 * q1 * r * s * t * w
    pinvq = 499967064455987294076532081570894386372
    qinvp = 33542671637141449679641257954160235148
    n11 = p1 * q1
    gu = u.bit_length() // 2

    np1prod = q1 * r * s * t * w
    nq1prod = p1 * r * s * t * w
    nrprod = p1 * q1 * s * t * w
    nsprod = p1 * q1 * r * t * w
    ntprod = p1 * q1 * r * s * w
    nwprod = p1 * q1 * r * t * s
    invnp1 = 205139046479782337030801215788009754117
    invnq1 = 429235397156384978572995593851807405098
    invnr = 592155359269217457562309991915739180471
    invns = 115186784058467557094932562011798848762
    invnt = 51850665316568177665825586294193267244
    invnw = 44855536902472009823152313099539628632

    def __init__(self, keypoints_window_size=5, missing_value=-1, hme_enabled=True):
        self.keypoints_map_deque = deque(maxlen=keypoints_window_size)
        self.status = []
        self.pose_data = {}
        self.missing_value = missing_value
        self.hme_enabled = hme_enabled

        self.thigh_calf_ratio_threshold = 0.7
        self.torso_leg_ratio_threshold = 0.5

    def feed_keypoints_17(self, keypoints_17):
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

        return self.feed_keypoints_map(kp_map)

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
        """Classify pose using simple if-else logic from pose_estimation_old.py.
        
        Returns:
            tuple: (label, pose_code, flags)
                label: One of 'standing', 'sitting', 'bending_down', 'lying_down'
                pose_code: 0=standing, 1=sitting, 2=bending_down, 3=lying_down
                flags: Dictionary of classification flags for debugging
        """
        # Classification with limb length ratios (from pose_estimation_old.py)
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

        # Map label to pose code
        pose_map = {
            "standing": 0,
            "sitting": 1,
            "bending_down": 2,
            "lying_down": 3
        }
        pose_code = pose_map.get(label, 0)

        # Create flags for debugging
        flags = {
            'torso_angle': torso_angle,
            'thigh_uprightness': thigh_uprightness,
            'thigh_calf_ratio': thigh_calf_ratio,
            'torso_leg_ratio': torso_leg_ratio
        }

        return label, pose_code, flags

    def feed_keypoints_map(self, keypoints_map):
        # Note: Visibility check is now handled in tracking.py's should_process_track()
        # before calling pose classification. This function now only handles pose estimation.
        
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
            up_vector = np.array([0.0, -1.0])

            torso_norm = np.linalg.norm(torso_vec)
            thigh_norm = np.linalg.norm(thigh_vec)
            if torso_norm == 0 or thigh_norm == 0:
                return None

            # Exact angle calculation from pose_estimation_old.py (lines 111-115)
            torso_angle = np.degrees(np.arccos(np.clip(
                np.dot(torso_vec, up_vector) / (torso_norm * np.linalg.norm(up_vector)), -1.0, 1.0)))

            thigh_angle = np.degrees(np.arccos(np.clip(
                np.dot(thigh_vec, up_vector) / (thigh_norm * np.linalg.norm(up_vector)), -1.0, 1.0)))

            thigh_uprightness = abs(thigh_angle - 180.0)

            (
                thigh_len, calf_len, torso_h, leg_len,
                thigh_calf_ratio, torso_leg_ratio
            ) = self._calculate_limb_lengths_and_ratios(km)

            _logger.print("RAW_INPUT",
                          "torso_angle=%.2f  thigh_uprightness=%.2f  "
                          "thigh_len=%.2f  calf_len=%.2f  torso_h=%.2f  leg_len=%.2f",
                          torso_angle, thigh_uprightness,
                          thigh_len, calf_len, torso_h, leg_len)

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
                'hme_enabled': True,  # HME always enabled
                'hme_label_received': False,
                'hme_label': None,
                'timestamp': time.time(),
                'frame_complete': True
            }

            # Production flow: extract actual characteristics for caregiver/analytics
            Tra = self._truncate(torso_angle)
            Tha = self._truncate(thigh_uprightness)
            Thl = self._truncate(thigh_len)
            cl = self._truncate(calf_len)
            Trl = self._truncate(torso_h)
            ll = self._truncate(leg_len)

            # Store as array in exact order: [Tra, Tha, Thl, cl, Trl, ll]
            self.pose_data['int_features'] = [Tra, Tha, Thl, cl, Trl, ll]
            _logger.print("COMPUTED",
                          "int_features → [Tra=%d, Tha=%d, Thl=%d, cl=%d, Trl=%d, ll=%d]",
                          Tra, Tha, Thl, cl, Trl, ll)

            # Testing flow: Local simulation of Caregiver and Analytics Server operations
            
            # Caregiver: Encrypting skeleton features
            Tra1, Tra2 = self._Enc1(Tra)
            Tha1, Tha2 = self._Enc1(Tha)
            Thl1, Thl2 = self._Enc1(Thl)
            cl1, cl2 = self._Enc1(cl)
            Trl1, Trl2 = self._Enc1(Trl)
            ll1, ll2 = self._Enc1(ll)

            # Analytics: Comparison operations 
            T301, T302 = self._priv_comp_an(Tra1, Tra2, 3000)        
            T401, T402 = self._priv_comp_an(Tha1, Tha2, 4000)      
            T801, T802 = self._priv_comp_an(Tra1, Tra2, 8000)
            T601, T602 = self._priv_comp_an(Tha1, Tha2, 6000)
            TC1, TC2 = self._priv_comp1_an(Thl1 * 10, Thl2 * 10, cl1 * 7, cl2 * 7)
            TL1, TL2 = self._priv_comp1_an(Trl1 * 10, Trl2 * 10, ll1 * 5, ll2 * 5)      

            # Caregiver: Comparison Operations and encrypting comparison result
            T30 = self._priv_comp_cg(T301, T302)
            T40 = self._priv_comp_cg(T401, T402)
            T80 = self._priv_comp_cg(T801, T802)
            T60 = self._priv_comp_cg(T601, T602)
            TC = self._priv_comp1_cg(TC1, TC2)
            TL = self._priv_comp1_cg(TL1, TL2)
            
            c11, c21, c31, c41, c51, c61 = self._Enc(T30)  # a
            c12, c22, c32, c42, c52, c62 = self._Enc(T40)  # b
            c13, c23, c33, c43, c53, c63 = self._Enc(T80)  # c
            c14, c24, c34, c44, c54, c64 = self._Enc(TC)   # d
            c15, c25, c35, c45, c55, c65 = self._Enc(TL)   # e
            c16, c26, c36, c46, c56, c66 = self._Enc(T60)  # f

            # Analytics: Polynomial evaluation
            # Polynomial evaluation for LSB
            pr1l = (c11 * c12 * c14) + (c11 * (1 - c12)) + (1 - c13) + ((1 - c11) * c13 * (1 - c16))
            pr2l = (c21 * c22 * c24) + (c21 * (1 - c22)) + (1 - c23) + ((1 - c21) * c23 * (1 - c26))
            pr3l = (c31 * c32 * c34) + (c31 * (1 - c32)) + (1 - c33) + ((1 - c31) * c33 * (1 - c36))
            pr4l = (c41 * c42 * c44) + (c41 * (1 - c42)) + (1 - c43) + ((1 - c41) * c43 * (1 - c46))
            pr5l = (c51 * c52 * c54) + (c51 * (1 - c52)) + (1 - c53) + ((1 - c51) * c53 * (1 - c56))
            pr6l = (c61 * c62 * c64) + (c61 * (1 - c62)) + (1 - c63) + ((1 - c61) * c63 * (1 - c66))
            
            # Polynomial evaluation for MSB
            pr1m = (c11 * c12 * (1 - c14) * c15) + ((1 - c11) * c13 * c16) + (1 - c13) + ((1 - c11) * c13 * (1 - c16))
            pr2m = (c21 * c22 * (1 - c24) * c25) + ((1 - c21) * c23 * c26) + (1 - c23) + ((1 - c21) * c23 * (1 - c26))
            pr3m = (c31 * c32 * (1 - c34) * c35) + ((1 - c31) * c33 * c36) + (1 - c33) + ((1 - c31) * c33 * (1 - c36))
            pr4m = (c41 * c42 * (1 - c44) * c45) + ((1 - c41) * c43 * c46) + (1 - c43) + ((1 - c41) * c43 * (1 - c46))
            pr5m = (c51 * c52 * (1 - c54) * c55) + ((1 - c51) * c53 * c56) + (1 - c53) + ((1 - c51) * c53 * (1 - c56))
            pr6m = (c61 * c62 * (1 - c64) * c65) + ((1 - c61) * c63 * c66) + (1 - c63) + ((1 - c61) * c63 * (1 - c66))
            
            # Computing the class from MSB and LSB
            pr1 = pr1m * 2 + pr1l
            pr2 = pr2m * 2 + pr2l
            pr3 = pr3m * 2 + pr3l
            pr4 = pr4m * 2 + pr4l
            pr5 = pr5m * 2 + pr5l
            pr6 = pr6m * 2 + pr6l

            # Caregiver: Decryption to get the pose
            mout = self._decmul(pr1, pr2, pr3, pr4, pr5, pr6)
            if mout == 0:
                pose = "standing"
            elif mout == 1:
                pose = "sitting"
            elif mout == 2:
                pose = "bending_down"
            elif mout == 3:
                pose = "lying_down" 
            else:
                pose = "unknown"

            # Store both the test label and mark the fallback/pending status as handled!
            self.pose_data['hme_testing_label'] = pose

            self.status = ['hme_testing_verified']
            
            return self.pose_data

        except Exception:
            self.status = []
            self.pose_data = {}
            return None

    def _truncate(self, num):
        return math.trunc(num * 100)

    def _Enc(self, m):
        g = random.randint(1, 2**32 - 1)
        c1 = ((g * self.u) + m) % self.p1
        c2 = ((g * self.u) + m) % self.q1
        c3 = ((g * self.u) + m) % self.r
        c4 = ((g * self.u) + m) % self.s
        c5 = ((g * self.u) + m) % self.t
        c6 = ((g * self.u) + m) % self.w
        return c1, c2, c3, c4, c5, c6

    def _decmul(self, c1, c2, c3, c4, c5, c6):
        mout = ((((c1 % self.p1) * self.invnp1 * self.np1prod) + 
                 ((c2 % self.q1) * self.invnq1 * self.nq1prod) + 
                 ((c3 % self.r) * self.invnr * self.nrprod) + 
                 ((c4 % self.s) * self.invns * self.nsprod) + 
                 ((c5 % self.t) * self.invnt * self.ntprod) + 
                 ((c6 % self.w) * self.invnw * self.nwprod)) % self.n1) 
        if mout > self.n1 // 2:
            mout = mout - self.n1
        mout = mout % self.u
        return mout

    def _Enc1(self, m):
        g = random.randint(1, 2**32 - 1)
        cth1 = ((g * self.u) + m) % self.p1
        cth2 = ((g * self.u) + m) % self.q1
        return cth1, cth2

    def _priv_comp_an(self, cth1, cth2, cs):
        r1 = random.randint(1, 2**22 - 1)
        r2 = random.randint(1, 2**10 - 1)
        c111 = r2 + (r1 * 2 * (cth1 - cs))
        c121 = r2 + (r1 * 2 * (cth2 - cs))
        return c111, c121 

    def _priv_comp_cg(self, c111, c121):
        mout = ((((c111 % self.p1) * self.qinvp * self.q1) + ((c121 % self.q1) * self.pinvq * self.p1)) % self.n11) % self.u
        rn = (mout + self.u) % self.u
        tg = rn.bit_length()
        if self.gu > tg:
            gcomp = 0
        elif self.gu < tg:
            gcomp = 1
        else: 
            gcomp = -1
        return gcomp       

    def _priv_comp1_an(self, cth11, cth21, cth3, cth4):
        r1 = random.randint(1, 2**22 - 1)
        r2 = random.randint(1, 2**10 - 1)
        c11 = r2 + (r1 * 2 * (cth11 - cth3))
        c12 = r2 + (r1 * 2 * (cth21 - cth4))
        return c11, c12 
       
    def _priv_comp1_cg(self, c11, c12):
        mout = ((((c11 % self.p1) * self.qinvp * self.q1) + ((c12 % self.q1) * self.pinvq * self.p1)) % self.n11) 
        if mout > self.n11 // 2:
            mout = mout - self.n11
        mout = mout % self.u
        tg = mout.bit_length()
        if self.gu > tg:
            gcomp1 = 0
        elif self.gu < tg:
            gcomp1 = 1
        else:
            gcomp1 = -1
        return gcomp1

    def evaluate_pose(self, keypoints):
        return self.feed_keypoints_17(keypoints)
    
    def get_int_features(self):
        """Get integer features (before encryption) as a JSON-serializable list.
        
        Returns:
            list: Integer features scaled by 100, or None if not available.
                  Format: [Tra, Tha, Thl, cl, Trl, ll]
        """
        if not self.pose_data:
            return None
        int_features = self.pose_data.get('int_features')
        if not int_features:
            return None
        return [int(v) for v in int_features]

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
