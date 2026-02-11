# tracking.py - Tracking utilities and fall detection helpers

import queue
import numpy as np
from maix import tracker, image
from pose.judge_fall import get_fall_info, FALL_COUNT_THRES
from pose.pose_estimation import PoseEstimation
from debug_config import DebugLogger

# Module-level debug logger instance
logger = DebugLogger(tag="TRACKING", instance_enable=False)

pose_estimator = PoseEstimation()

# Tracking parameters
max_lost_buff_time = 30
track_thresh = 0.4
high_thresh = 0.6
match_thresh = 0.8
max_history_num = 5
valid_class_id = [0]

# Fall detection parameters
fallParam = {
    "v_bbox_y": 0.17,
    "angle": 70
}

queue_size = 5

# Initialize tracker
tracker0 = tracker.ByteTracker(max_lost_buff_time, track_thresh, high_thresh, match_thresh, max_history_num)

# Online targets storage
online_targets = {
    "id": [],
    "bbox": [],
    "points": []
}

# Fall and unsafe IDs tracking
fall_ids = set()
unsafe_ids = set()
fall_states = {}  # Per-track fall detection state: {track_id: state_dict}

# FPS tracking for fall detection
current_fps = 30.0

def set_fps(fps):
    """Set the current FPS for fall detection calculations"""
    global current_fps
    current_fps = fps if fps > 0 else 30.0

def get_fps():
    """Get the current FPS for fall detection calculations"""
    return current_fps

def yolo_objs_to_tracker_objs(objs, valid_class_id=[0]):
    """Convert YOLO objects to tracker objects"""
    out = []
    for obj in objs:
        if obj.class_id in valid_class_id:
            out.append(tracker.Object(obj.x, obj.y, obj.w, obj.h, obj.class_id, obj.score))
    return out

def to_keypoints_np(obj_points):
    """Convert flat list [x1, y1, x2, y2, ...] to numpy array"""
    keypoints = np.array(obj_points)
    return keypoints.reshape(-1, 2)

def flat_keypoints_to_pairs(keypoints_flat):
    """Convert flat list [x1, y1, x2, y2, ...] to list of tuples [(x1,y1), (x2,y2), ...]"""
    if len(keypoints_flat) % 2 != 0:
        keypoints_flat = keypoints_flat[:len(keypoints_flat)//2*2]
    
    pairs = []
    for i in range(0, len(keypoints_flat), 2):
        if i + 1 < len(keypoints_flat):
            pairs.append((keypoints_flat[i], keypoints_flat[i+1]))
    return pairs

def normalize_keypoints(keypoints_flat, img_width, img_height):
    """Normalize keypoints to 0-1 range for safe area checking"""
    normalized = []
    pairs = flat_keypoints_to_pairs(keypoints_flat)
    
    for x, y in pairs:
        if x > 0 and y > 0:
            x_norm = x / img_width
            y_norm = y / img_height
            normalized.append((x_norm, y_norm, 1.0))
        else:
            normalized.append((0.0, 0.0, 0.0))
    
    return normalized

def update_tracks(objs, current_time_ms=None):
    """Update tracking with new detections"""
    global online_targets, fall_ids, unsafe_ids
    
    # Convert YOLO objects to tracker objects
    out_bbox = yolo_objs_to_tracker_objs(objs, valid_class_id)
    
    # Update tracker
    tracks = tracker0.update(out_bbox)
    
    return tracks

def process_track(track, objs, camera_id="unknown", is_recording=False, skeleton_saver=None, frame_id=0, fps=30, analytics_mode=False, safety_judgment=None):
    """Process a single track - handle fall detection and safety checking

    Args:
        track: The track object from tracker
        objs: Detected objects from pose extractor
        is_recording: Whether recording is active
        skeleton_saver: SkeletonSaver2D instance for recording
        frame_id: Current frame ID
        fps: Current FPS for fall detection
        analytics_mode: If True, use AnalyticsWorker results; if False, use local fall detection
        safety_judgment: SafetyJudgment instance that combines all area checkers
    """
    global online_targets, fall_ids, unsafe_ids, current_fps, fall_states
    
    # Initialize encrypted_features for all code paths
    encrypted_features = None
    pose_label = "unknown"
    safety_reason = "normal"
    safety_details = {}
    
    # Update global FPS
    current_fps = fps
    
    if track.lost:
        return None
    
    # Import analytics functions here to avoid circular imports
    from workers import get_analytics_pose_data, get_analytics_fall_data, is_analytics_server_available
    
    # Determine if we should use HME (only when analytics server is available)
    # use_hme is True only when:
    # 1. Analytics server is reachable (is_analytics_server_available())
    # 2. hme flag is set in control_manager
    # Otherwise, use_hme is False and we do local processing only
    use_hme = False
    try:
        from control_manager import get_flag
        if is_analytics_server_available():
            use_hme = get_flag("hme", False)
    except ImportError:
        pass
    
    # Find corresponding YOLO object
    # Find corresponding YOLO object
    # We want to associate the Tracked Object (detector) with a Pose Object (extractor)
    # Strategy: Find the closest Pose Object to the current Tracked Object
    if len(track.history) > 0 and len(objs) > 0:
        tracker_obj = track.history[-1]
        
        # Find closest pose object (simple Euclidean distance of top-left corner)
        # Ideally center distance is better, but this matches original logic style
        best_obj = None
        min_dist = float('inf')
        
        for obj in objs:
            dist = (obj.x - tracker_obj.x)**2 + (obj.y - tracker_obj.y)**2
            if dist < min_dist:
                min_dist = dist
                best_obj = obj
        
        # Always use the best match if we have one (User Request: "Always allow")
        if best_obj:
            obj = best_obj
            keypoints_np = to_keypoints_np(obj.points)
            
            # Local tracking - add to history
            if track.id not in online_targets["id"]:
                online_targets["id"].append(track.id)
                online_targets["bbox"].append(queue.Queue(maxsize=queue_size))
                online_targets["points"].append(queue.Queue(maxsize=queue_size))
            
            idx = online_targets["id"].index(track.id)
            
            if online_targets["bbox"][idx].qsize() >= queue_size:
                online_targets["bbox"][idx].get()
                online_targets["points"][idx].get()
            
            online_targets["bbox"][idx].put([tracker_obj.x, tracker_obj.y, tracker_obj.w, tracker_obj.h])
            online_targets["points"][idx].put(obj.points)
                
            # Get fall_algorithm flag to determine which algorithm to use
            fall_algorithm = 1  # Default: Algorithm 1 (BBox motion only)
            try:
                from control_manager import get_flag
                fall_algorithm = get_flag("fall_algorithm", 1)
            except ImportError:
                pass

            # Determine status based on analytics_mode and use_analytics result
            if analytics_mode:
                # In analytics mode, use AnalyticsWorker results
                use_analytics = False

                # Get analytics data from shared state
                analytics_pose = get_analytics_pose_data(track.id)
                analytics_fall = get_analytics_fall_data(track.id)

                if analytics_pose and analytics_fall:
                    # Analytics server has results for this track
                    pose_label = analytics_pose.get("label", "unknown")

                    # Check fall detection results from analytics server
                    fall_detected_method1 = analytics_fall.get("fall_detected_method1", False)
                    fall_detected_method2 = analytics_fall.get("fall_detected_method2", False)
                    fall_detected_method3 = analytics_fall.get("fall_detected_method3", False)
                    counter_method1 = analytics_fall.get("counter_method1", 0)
                    counter_method2 = analytics_fall.get("counter_method2", 0)
                    counter_method3 = analytics_fall.get("counter_method3", 0)

                    # Update fall_ids based on selected fall algorithm
                    fall_detected = False
                    if fall_algorithm == 1:
                        fall_detected = fall_detected_method1
                    elif fall_algorithm == 2:
                        fall_detected = fall_detected_method2

                    if fall_detected:
                        fall_ids.add(track.id)
                        unsafe_ids.discard(track.id)
                        use_analytics = True
                    else:
                        # No fall detected - remove from fall_ids
                        if track.id in fall_ids:
                            fall_ids.discard(track.id)
                        use_analytics = True

                if not use_analytics:
                    # Analytics server not available or no data yet, fall back to local processing
                    if online_targets["bbox"][idx].qsize() >= 2:
                        state = fall_states.get(track.id)
                        fall_result = check_fall(tracker_obj, online_targets, idx, fps, state=state)
                        if fall_result:
                            fall_info, pose_label, encrypted_features, updated_state = fall_result
                            fall_states[track.id] = updated_state
                            
                            (fall_detected_method1, counter_method1,
                             fall_detected_method2, counter_method2) = fall_info

                            # Update fall_ids based on selected fall algorithm
                            fall_detected = False
                            if fall_algorithm == 1:
                                fall_detected = fall_detected_method1
                            elif fall_algorithm == 2:
                                fall_detected = fall_detected_method2

                            if fall_detected:
                                fall_ids.add(track.id)
                                unsafe_ids.discard(track.id)
                            else:
                                # No fall detected - remove from fall_ids
                                if track.id in fall_ids:
                                    fall_ids.discard(track.id)
            else:
                # Not in analytics mode, use local fall detection
                if online_targets["bbox"][idx].qsize() >= 2:
                    state = fall_states.get(track.id)
                    fall_result = check_fall(tracker_obj, online_targets, idx, fps, state=state)
                    if fall_result:
                        fall_info, pose_label, encrypted_features, updated_state = fall_result
                        fall_states[track.id] = updated_state
                        
                        (fall_detected_method1, counter_method1,
                         fall_detected_method2, counter_method2) = fall_info

                        # Update fall_ids based on selected fall algorithm
                        fall_detected = False
                        if fall_algorithm == 1:
                            fall_detected = fall_detected_method1
                        elif fall_algorithm == 2:
                            fall_detected = fall_detected_method2

                        if fall_detected:
                            fall_ids.add(track.id)
                            unsafe_ids.discard(track.id)
                        else:
                            # No fall detected - remove from fall_ids
                            if track.id in fall_ids:
                                fall_ids.discard(track.id)

            # Safety checking using SafetyJudgment (only if not already marked as fall)
            if track.id not in fall_ids:
                # Check if safety checking is enabled
                use_safety_check = False
                try:
                    from control_manager import get_flag
                    use_safety_check = get_flag("use_safety_check", False)
                except ImportError:
                    pass

                if use_safety_check and safety_judgment is not None:
                    # Convert keypoints to the format expected by SafetyJudgment
                    # SafetyJudgment expects: List[Tuple[float, float, float]] (x, y, confidence)
                    body_keypoints = []
                    for i in range(0, len(obj.points), 2):
                        if i + 1 < len(obj.points):
                            x = float(obj.points[i])
                            y = float(obj.points[i + 1])
                            # Use a default confidence of 1.0 since pose extractor doesn't provide it
                            body_keypoints.append((x, y, 1.0))

                    # Normalize keypoints for safety check (expects 0-1 range)
                    # Assuming typical MaixCAM resolution 320x224
                    normalized_keypoints = normalize_keypoints(obj.points, 320, 224)

                    # Get sleep monitoring configuration
                    max_sleep_duration = 0
                    bedtime = ""
                    wakeup_time = ""
                    current_time_str = "12:00" # Default/Fallback
                    
                    try:
                        from control_manager import get_flag
                        from tools.time_utils import get_current_time_str
                        import os
                        
                        max_sleep_duration = get_flag("max_sleep_duration", 0)
                        bedtime = get_flag("bedtime", "")
                        wakeup_time = get_flag("wakeup_time", "")

                        # Fetch current time from server (with local fallback)
                        current_time_str = get_current_time_str(camera_id)
                    except (ImportError, Exception):
                        pass

                    # Use SafetyJudgment to evaluate safety
                    is_safe, safety_reason, details = safety_judgment.evaluate_safety(
                        track.id, normalized_keypoints, pose_label,
                        current_time_str=current_time_str,
                        max_sleep_duration_min=max_sleep_duration,
                        bedtime_str=bedtime,
                        wakeup_time_str=wakeup_time
                    )

                    if not is_safe:
                        # Person is unsafe - add to unsafe_ids
                        unsafe_ids.add(track.id)
                        safety_details = details
                        logger.print("TRACKING", "Track %d unsafe: %s | details: %s", track.id, safety_reason, details)
                    else:
                        # Person is safe - remove from unsafe_ids
                        if track.id in unsafe_ids:
                            unsafe_ids.discard(track.id)
                else:
                    # Safety checking disabled - remove from unsafe_ids
                    if track.id in unsafe_ids:
                        unsafe_ids.discard(track.id)
            else:
                # Already marked as fall - ensure not in unsafe_ids (fall takes precedence)
                if track.id in unsafe_ids:
                    unsafe_ids.discard(track.id)

            # Save to skeleton if recording
            if is_recording and skeleton_saver:
                safety_status = 1 if track.id in fall_ids else (2 if track.id in unsafe_ids else 0)
                skeleton_saver.add_keypoints(frame_id, track.id, obj.points, safety_status)
            
            track_result = {
                "track_id": track.id,
                "bbox": [tracker_obj.x, tracker_obj.y, tracker_obj.w, tracker_obj.h],
                "keypoints": obj.points,
                "keypoints_np": keypoints_np,
                "pose_label": pose_label,  # Return pose label for analytics/local processing
                "status": "fall" if track.id in fall_ids else ("unsafe" if track.id in unsafe_ids else "tracking"),
                "encrypted_features": encrypted_features,  # Encrypted features for analytics server (when use_hme=True)
                "use_hme": use_hme,  # Whether HME is enabled (True only when analytics server is available)
                "safety_reason": safety_reason,
                "safety_details": safety_details
            }
            # print(f"process_track() -> {track_result}")
            
            return track_result
    
    return None

def check_fall(tracker_obj, track_history, idx, fps=30, state=None):
    """Check for fall using track history
    
    Returns:
        tuple: (fall_info, pose_label, encrypted_features, updated_state)
               - fall_info: Fall detection result tuple
               - pose_label: Pose classification label
               - encrypted_features: Dict of encrypted features when use_hme=True, else None
               - updated_state: Updated fall detection state dict
    """
    global current_fps, pose_estimator
    
    # Use provided fps or global fps
    effective_fps = fps if fps > 0 else current_fps
    
    pose_data = None
    pose_label = "unknown"
    encrypted_features = None
    
    # Get keypoints from history
    if not track_history["points"][idx].empty():
        keypoints_flat = list(track_history["points"][idx].queue)
        if keypoints_flat:
            # Get the most recent keypoints
            latest_keypoints = keypoints_flat[-1]
            
            # Evaluate pose using PoseEstimation
            try:
                # Check if HME mode is enabled (for analytics/encrypted features only)
                use_hme_for_analytics = False
                try:
                    from control_manager import get_flag
                    use_hme_for_analytics = get_flag("hme", False)
                except ImportError:
                    pass

                # Evaluate pose with keypoints (always use plain mode for fall detection)
                pose_data = pose_estimator.evaluate_pose(np.array(latest_keypoints), use_hme=False)

                # Extract label from pose_data
                if pose_data is not None:
                    pose_label = pose_data.get('plain_label', 'unknown')
                    # Get encrypted features when HME is enabled for analytics
                    if use_hme_for_analytics:
                        encrypted_features = pose_estimator.get_encrypted_features()
            except ImportError:
                # Fallback if pose_estimation not available
                pass

    # Call fall detection with the tracker object and pose data
    # get_fall_info returns: (fall_detected_bbox_only, counter_bbox_only, fall_detected_motion_pose_and, counter_motion_pose_and, state)
    result = get_fall_info(
        tracker_obj,
        track_history,
        idx,
        fallParam,
        queue_size,
        effective_fps,
        pose_data,
        state
    )
    
    # Unpack result
    fall_detected_bbox_only = result[0]
    counter_bbox_only = result[1]
    fall_detected_motion_pose_and = result[2]
    counter_motion_pose_and = result[3]
    updated_state = result[4]
    
    # Reconstruct fall_info tuple expected by other parts of the system
    # Note: get_fall_info used to return 4 values, now 5.
    # The caller expects fall_info to be used in update_fall_counters.
    # update_fall_counters expects 6 values? Wait, let's check judge_fall.py again.
    # judge_fall.py returned 4 values. update_fall_counters unpacks 6?
    # Let's check update_fall_counters in tracking.py.
    
    # tracking.py:
    # def update_fall_counters(fall_info):
    # (fall_detected_method1, counter_method1,
    #  fall_detected_method2, counter_method2,
    #  fall_detected_method3, counter_method3) = fall_info
    
    # Wait, get_fall_info in judge_fall.py (ORIGNAL) returned 4 values.
    #     return (
    #         fall_detected_bbox_only, counter_bbox_only,
    #         fall_detected_motion_pose_and, counter_motion_pose_and
    #     )
    
    # But update_fall_counters unpacks 6. This implies there's a mismatch or I misread something.
    # Let's check update_fall_counters again.
    # It likely expects more, or get_fall_info logic has changed in the past.
    # The user didn't mention this was broken, but it looks suspicious.
    # Or maybe update_fall_counters is used with analytics data which has 3 methods.
    
    # In process_track (lines 241-242):
    # (fall_detected_method1, counter_method1,
    #  fall_detected_method2, counter_method2) = fall_info
    # Process track unpacks 4 values!
    
    # So update_fall_counters unpacking 6 is likely for analytics return or unused/broken code?
    # Ah, update_fall_counters is defined but where is it used?
    # I grepped get_fall_info usage, but not update_fall_counters usage.
    # It's not called in process_track.
    
    # Anyway, I should return fall_info as a tuple of 4 to match process_track expectation.
    
    fall_info = (
        fall_detected_bbox_only, counter_bbox_only,
        fall_detected_motion_pose_and, counter_motion_pose_and
    )
    
    return fall_info, pose_label, encrypted_features, updated_state

def update_fall_counters(fall_info):
    """Update fall counters and IDs based on fall detection result"""
    global fall_ids, unsafe_ids
    
    (fall_detected_method1, counter_method1,
     fall_detected_method2, counter_method2,
     fall_detected_method3, counter_method3) = fall_info
    
    # Update fall IDs based on method 3 (most conservative)
    # This is a simplified version - in practice, you'd track per-track IDs
    return {
        "method1": {
            "detected": fall_detected_method1,
            "counter": counter_method1
        },
        "method2": {
            "detected": fall_detected_method2,
            "counter": counter_method2
        },
        "method3": {
            "detected": fall_detected_method3,
            "counter": counter_method3
        }
    }

def clear_track_history():
    """Clear all track history"""
    global online_targets, fall_ids, unsafe_ids, fall_states
    online_targets = {
        "id": [],
        "bbox": [],
        "points": []
    }
    fall_ids.clear()
    unsafe_ids.clear()
    fall_states.clear()

def get_online_targets():
    """Get current online targets"""
    return online_targets.copy()

def reset_tracker():
    """Reset the tracker and all tracking state"""
    global tracker0, online_targets, fall_ids, unsafe_ids, fall_states
    tracker0 = tracker.ByteTracker(max_lost_buff_time, track_thresh, high_thresh, match_thresh, max_history_num)
    clear_track_history()

def get_fall_threshold():
    """Get the fall detection threshold"""
    return FALL_COUNT_THRES

def get_fall_param():
    """Get fall detection parameters"""
    return fallParam.copy()

