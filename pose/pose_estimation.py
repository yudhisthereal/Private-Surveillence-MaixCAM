import numpy as np
from collections import deque
import random
import math

class PoseEstimation:
    """Pose classifier for CAMERA SIDE ONLY (camera → analytics → caregiver architecture)
    
    This version implements the camera side of the HME workflow:
    1. Camera: Calculates pose features and encrypts them with simple 2-component encryption
    2. Analytics: Performs comparisons on encrypted data (not in this file)
    3. Caregiver: Decrypts results and determines pose (not in this file)
    
    Camera should ONLY have the public parameters for encryption, NOT decryption keys.
    """
    
    # ============================================
    # PUBLIC PARAMETERS FOR ENCRYPTION (camera has these)
    # ============================================
    # These are the public parameters needed for encryption
    # Camera should NOT have decryption keys or other private parameters
    p1 = 234406548094233827948571379965547188853
    q1 = 583457592311129510314141861330330044443
    u = 2355788435550222327802749264573303139783
    
    def __init__(self, keypoints_window_size=5, missing_value=-1):
        self.keypoints_map_deque = deque(maxlen=keypoints_window_size)
        self.status = []
        self.pose_data = {}  # Store detailed pose data
        self.missing_value = missing_value
        
        print(f"[Camera] Pose Estimation initialized (window={keypoints_window_size})")
    
    # ============================================
    # KEYPOINT PROCESSING (Camera)
    # ============================================
    
    def feed_keypoints_17(self, keypoints_17):
        """Process 17 keypoints: flattened list/array [x0,y0, x1,y1, ..., x16,y16]"""
        try:
            keypoints = np.array(keypoints_17).reshape((-1, 2))
            if keypoints.shape != (17, 2):
                print(f"[Camera] Invalid keypoints shape: {keypoints.shape}")
                return None
        except Exception as e:
            print(f"[Camera] Error parsing keypoints: {e}")
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
        """Check if all required keypoints are present (no missing values)"""
        for k, v in keypoints_map.items():
            if v is None:
                return False
            if v[0] == self.missing_value or v[1] == self.missing_value:
                return False
        return True
    
    def _calculate_limb_lengths(self, km):
        """Calculate limb lengths from keypoints"""
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
            
            return thigh_length, calf_length, torso_height, leg_length
        except Exception as e:
            print(f"[Camera] Error calculating limb lengths: {e}")
            return 0.0, 0.0, 0.0, 0.0
    
    def feed_keypoints_map(self, keypoints_map):
        """Main processing: extract features and prepare encrypted data for analytics"""
        # Step 1: Check if frame is complete
        if not self._is_frame_complete(keypoints_map):
            self.status = []
            self.pose_data = {}
            return None
        
        # Step 2: Add to smoothing deque
        self.keypoints_map_deque.append(keypoints_map)
        
        try:
            # Step 3: Compute averaged keypoints (temporal smoothing)
            km = {
                key: sum(d[key] for d in self.keypoints_map_deque) / len(self.keypoints_map_deque)
                for key in self.keypoints_map_deque[0].keys()
            }
            
            # Step 4: Compute centers for angle calculations
            shoulder_center = (km['Left Shoulder'] + km['Right Shoulder']) / 2.0
            hip_center = (km['Left Hip'] + km['Right Hip']) / 2.0
            knee_center = (km['Left Knee'] + km['Right Knee']) / 2.0
            
            # Step 5: Calculate vectors
            torso_vec = shoulder_center - hip_center
            thigh_vec = knee_center - hip_center
            up_vector = np.array([0.0, -1.0])  # Upward direction (negative y)
            
            # Step 6: Safe angle computations
            torso_norm = np.linalg.norm(torso_vec)
            thigh_norm = np.linalg.norm(thigh_vec)
            
            if torso_norm == 0 or thigh_norm == 0:
                self.status = []
                self.pose_data = {}
                return None
            
            # Torso angle with vertical (0° = upright, 90° = horizontal)
            torso_angle = np.degrees(np.arccos(np.clip(
                np.dot(torso_vec, up_vector) / (torso_norm * np.linalg.norm(up_vector)), -1.0, 1.0)))
            
            # Thigh angle with vertical
            thigh_angle = np.degrees(np.arccos(np.clip(
                np.dot(thigh_vec, up_vector) / (thigh_norm * np.linalg.norm(up_vector)), -1.0, 1.0)))
            
            # Convert thigh angle to "uprightness" (0° = upright, 180° = inverted)
            thigh_uprightness = abs(thigh_angle - 180.0)
            
            # Step 7: Calculate limb lengths
            thigh_length, calf_length, torso_height, leg_length = self._calculate_limb_lengths(km)
            
            # Step 8: Convert real numbers to integers (2 decimal precision)
            # This is necessary for homomorphic encryption
            Thl = self._truncate(thigh_length)      # Thigh length
            cl = self._truncate(calf_length)        # Calf length
            Trl = self._truncate(torso_height)      # Torso height
            ll = self._truncate(leg_length)         # Leg length
            Tra = self._truncate(torso_angle)       # Torso angle
            Tha = self._truncate(thigh_uprightness) # Thigh uprightness
            
            # Step 9: Encrypt features using 2-component encryption
            # Camera only knows p1, q1, u (public parameters)
            encrypted_features = {
                'Tra': self._encrypt_simple(Tra),  # Torso angle
                'Tha': self._encrypt_simple(Tha),  # Thigh uprightness
                'Thl': self._encrypt_simple(Thl),  # Thigh length
                'cl': self._encrypt_simple(cl),    # Calf length
                'Trl': self._encrypt_simple(Trl),  # Torso height
                'll': self._encrypt_simple(ll)     # Leg length
            }
            
            # Step 10: Store all data for this frame
            self.pose_data = {
                # Raw calculated values (for debugging/visualization)
                'raw_features': {
                    'torso_angle': torso_angle,
                    'thigh_uprightness': thigh_uprightness,
                    'thigh_length': thigh_length,
                    'calf_length': calf_length,
                    'torso_height': torso_height,
                    'leg_length': leg_length,
                    'thigh_angle': thigh_angle
                },
                
                # Integer representations (before encryption)
                'int_features': {
                    'Tra': Tra,
                    'Tha': Tha,
                    'Thl': Thl,
                    'cl': cl,
                    'Trl': Trl,
                    'll': ll
                },
                
                # Encrypted features (to send to analytics server)
                'encrypted_features': encrypted_features,
                
                # Pose label will be filled later by caregiver
                'label': None,
                
                # Metadata
                'timestamp': time.time(),
                'frame_complete': True
            }
            
            # Camera status is just that we have features ready
            self.status = ['features_ready']
            
            print(f"[Camera] Features extracted and encrypted:")
            print(f"  Torso angle: {torso_angle:.2f}° → {Tra}")
            print(f"  Thigh uprightness: {thigh_uprightness:.2f}° → {Tha}")
            print(f"  Thigh length: {thigh_length:.2f} → {Thl}")
            print(f"  Calf length: {calf_length:.2f} → {cl}")
            
            return self.pose_data
            
        except Exception as e:
            print(f"[Camera] Error processing keypoints: {e}")
            self.status = []
            self.pose_data = {}
            return None
    
    # ============================================
    # ENCRYPTION METHODS (Camera only has these)
    # ============================================
    
    def _truncate(self, num):
        """Convert real number to integer with 2 decimal precision"""
        factor = 100
        return math.trunc(num * factor)
    
    def _encrypt_simple(self, m):
        """Simple 2-component encryption (camera → analytics)
        
        Args:
            m: Integer value to encrypt
            
        Returns:
            list: [c1, c2] encrypted values using public parameters p1, q1, u
        """
        # Random value for encryption
        g = random.randint(1, 2**32 - 1)
        
        # Encrypt using Chinese Remainder Theorem with 2 primes
        c1 = ((g * self.u) + m) % self.p1
        c2 = ((g * self.u) + m) % self.q1
        
        return [c1, c2]
    
    # ============================================
    # PUBLIC INTERFACE METHODS
    # ============================================
    
    def evaluate_pose(self, keypoints):
        """Main entry point for camera: returns pose data with encrypted features
        
        Args:
            keypoints: List of 34 floats (17 keypoints × 2 coordinates)
            
        Returns:
            dict: Pose data including raw features, integer features, and encrypted features
                  Returns None if keypoints are invalid
        """
        res = self.feed_keypoints_17(keypoints)
        if res is None:
            return None
        return self.pose_data
    
    def get_encrypted_features(self):
        """Get encrypted features for transmission to analytics server
        
        Returns:
            dict: Encrypted features ready for transmission, or None if not available
        """
        if self.pose_data and 'encrypted_features' in self.pose_data:
            return self.pose_data['encrypted_features']
        return None
    
    def get_raw_features(self):
        """Get raw calculated features (for debugging/visualization only)
        
        Note: In a real deployment, raw features should not leave the camera
              for privacy reasons. This is for testing only.
        """
        if self.pose_data and 'raw_features' in self.pose_data:
            return self.pose_data['raw_features']
        return None
    
    def get_int_features(self):
        """Get integer features (before encryption, for debugging only)"""
        if self.pose_data and 'int_features' in self.pose_data:
            return self.pose_data['int_features']
        return None
    
    def reset(self):
        """Reset the pose estimator (clear history)"""
        self.keypoints_map_deque.clear()
        self.status = []
        self.pose_data = {}
        print("[Camera] Pose estimator reset")
    
    def get_status(self):
        """Get current status"""
        return self.status
    
    def set_pose_label(self, label):
        """Set the pose label (called when caregiver returns decrypted result)
        
        Args:
            label: The pose label determined by caregiver ("standing", "sitting", etc.)
        """
        if self.pose_data:
            self.pose_data['label'] = label
            self.status = [label]
            print(f"[Camera] Pose label received from caregiver: {label}")
    
    def get_pose_data(self):
        """Get complete pose data (including any label from caregiver)"""
        return self.pose_data


# ============================================
# TESTING/DEMO CODE
# ============================================

def test_camera_side():
    """Test the camera-side pose estimation"""
    print("\n" + "="*60)
    print("Testing Camera-Side Pose Estimation")
    print("="*60)
    
    # Create pose estimator
    pose_estimator = PoseEstimationCamera(keypoints_window_size=3)
    
    # Create dummy keypoints (simulating a standing person)
    # Format: [x0,y0, x1,y1, ..., x16,y16]
    dummy_keypoints = []
    
    # Fill with dummy coordinates (simplified skeleton)
    # Shoulders (indices 5,6)
    dummy_keypoints.extend([320, 100])  # Left shoulder
    dummy_keypoints.extend([400, 100])  # Right shoulder
    
    # Add other keypoints (not used in pose estimation)
    for _ in range(5):
        dummy_keypoints.extend([0, 0])
    
    # Hips (indices 11,12)
    dummy_keypoints.extend([330, 200])  # Left hip
    dummy_keypoints.extend([390, 200])  # Right hip
    
    # Knees (indices 13,14)
    dummy_keypoints.extend([335, 300])  # Left knee
    dummy_keypoints.extend([385, 300])  # Right knee
    
    # Ankles (indices 15,16)
    dummy_keypoints.extend([335, 400])  # Left ankle
    dummy_keypoints.extend([385, 400])  # Right ankle
    
    # Process keypoints
    pose_data = pose_estimator.evaluate_pose(dummy_keypoints)
    
    if pose_data:
        print("\n[SUCCESS] Camera processed keypoints successfully")
        print("\nRaw Features:")
        for key, value in pose_data['raw_features'].items():
            print(f"  {key}: {value:.2f}")
        
        print("\nInteger Features (before encryption):")
        for key, value in pose_data['int_features'].items():
            print(f"  {key}: {value}")
        
        print("\nEncrypted Features (first few values):")
        encrypted = pose_data['encrypted_features']
        for key, value in encrypted.items():
            print(f"  {key}: [{value[0]:.3e}, {value[1]:.3e}]")
        
        print("\nThese encrypted features should be sent to the Analytics Server.")
        print("\nAnalytics Server will:")
        print("  1. Receive encrypted features")
        print("  2. Perform homomorphic comparisons")
        print("  3. Send comparison results to Caregiver")
        print("\nCaregiver will:")
        print("  1. Decrypt comparison results")
        print("  2. Determine pose using polynomial logic")
        print("  3. Send pose label back to Camera")
    else:
        print("\n[FAILED] Could not process keypoints")
    
    return pose_estimator


if __name__ == "__main__":
    # Import time for testing
    import time
    
    # Run test
    estimator = test_camera_side()
    
    print("\n" + "="*60)
    print("Architecture Summary")
    print("="*60)
    print("\nCORRECT HME ARCHITECTURE:")
    print("1. CAMERA (this file):")
    print("   • Calculates features from keypoints")
    print("   • Encrypts with public parameters (p1, q1, u)")
    print("   • Sends encrypted features to Analytics")
    print("\n2. ANALYTICS SERVER (separate file):")
    print("   • Receives encrypted features")
    print("   • Performs comparisons (priv_comp_an)")
    print("   • Evaluates polynomial")
    print("   • Sends encrypted results to Caregiver")
    print("\n3. CAREGIVER (separate file):")
    print("   • Decrypts comparison results")
    print("   • Determines pose label")
    print("   • Sends pose label back to Camera")
    print("\nNOTE: Camera NEVER has decryption keys!")
    print("      Camera ONLY has public encryption parameters.")