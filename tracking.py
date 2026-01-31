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
    "v_bbox_y": 0.12,
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

def process_track(track, objs, is_recording=False, skeleton_saver=None, frame_id=0, fps=30, analytics_mode=False, safety_judgment=None):
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
    global online_targets, fall_ids, unsafe_ids, current_fps
    
    # Initialize encrypted_features for all code paths
    encrypted_features = None
    pose_label = "unknown"
    
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
                    
                    # Update fall_ids and unsafe_ids based on analytics results
                    if fall_detected_method3 or fall_detected_method2 or fall_detected_method1:
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
                        fall_result = check_fall(tracker_obj, online_targets, idx, fps)
                        if fall_result:
                            fall_info, pose_label, encrypted_features = fall_result
                            (fall_detected_method1, counter_method1,
                             fall_detected_method2, counter_method2,
                             fall_detected_method3, counter_method3) = fall_info
                            
                            # Update fall_ids based on detection results
                            if fall_detected_method3 or fall_detected_method2 or fall_detected_method1:
                                fall_ids.add(track.id)
                                unsafe_ids.discard(track.id)
                            else:
                                # No fall detected - remove from fall_ids
                                if track.id in fall_ids:
                                    fall_ids.discard(track.id)
            else:
                # Not in analytics mode, use local fall detection
                if online_targets["bbox"][idx].qsize() >= 2:
                    fall_result = check_fall(tracker_obj, online_targets, idx, fps)
                    if fall_result:
                        fall_info, pose_label, encrypted_features = fall_result
                        (fall_detected_method1, counter_method1,
                         fall_detected_method2, counter_method2,
                         fall_detected_method3, counter_method3) = fall_info
                        
                        # Update fall_ids based on detection results
                        if fall_detected_method3 or fall_detected_method2 or fall_detected_method1:
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

                    # Use SafetyJudgment to evaluate safety
                    is_safe, reason, details = safety_judgment.evaluate_safety(
                        track.id, body_keypoints, pose_label
                    )

                    if not is_safe:
                        # Person is unsafe - add to unsafe_ids
                        unsafe_ids.add(track.id)
                        logger.print("TRACKING", "Track %d unsafe: %s | details: %s", track.id, reason, details)
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
                "use_hme": use_hme  # Whether HME is enabled (True only when analytics server is available)
            }
            # print(f"process_track() -> {track_result}")
            
            return track_result
    
    return None

def check_fall(tracker_obj, track_history, idx, fps=30):
    """Check for fall using track history
    
    Returns:
        tuple: (fall_info, pose_label, encrypted_features)
               - fall_info: Fall detection result tuple
               - pose_label: Pose classification label
               - encrypted_features: Dict of encrypted features when use_hme=True, else None
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
    fall_info = get_fall_info(
        tracker_obj,
        track_history,
        idx,
        fallParam,
        queue_size,
        effective_fps,
        pose_data
    )
    
    return fall_info, pose_label, encrypted_features

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
    global online_targets, fall_ids, unsafe_ids
    online_targets = {
        "id": [],
        "bbox": [],
        "points": []
    }
    fall_ids.clear()
    unsafe_ids.clear()

def get_online_targets():
    """Get current online targets"""
    return online_targets.copy()

def reset_tracker():
    """Reset the tracker and all tracking state"""
    global tracker0, online_targets, fall_ids, unsafe_ids
    tracker0 = tracker.ByteTracker(max_lost_buff_time, track_thresh, high_thresh, match_thresh, max_history_num)
    clear_track_history()

def get_fall_threshold():
    """Get the fall detection threshold"""
    return FALL_COUNT_THRES

def get_fall_param():
    """Get fall detection parameters"""
    return fallParam.copy()

