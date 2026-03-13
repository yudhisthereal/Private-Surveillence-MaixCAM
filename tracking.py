# tracking.py - Tracking utilities and fall detection helpers

import queue
import numpy as np
from maix import tracker, image
from pose.judge_fall import get_fall_info, FALL_COUNT_THRES
from pose.pose_estimation import PoseEstimation
from config import INPUT_WIDTH, INPUT_HEIGHT
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
    "v_bbox_y": 0.4, # amount of shrinkage in bounding box relative to the original size in percent (30%)
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

# Pose-recovery settings (tolerate short pose-estimation gaps)
# If a new skeleton appears after a short gap, we can reuse the most recent
# snapshot (bbox + pose_label + status) whose bbox bottom is spatially similar.
POSE_RECOVERY_MAX_GAP_FRAMES = 3
POSE_RECOVERY_BBOX_BOTTOM_TOLERANCE_PX = 20
POSE_RECOVERY_CACHE_SIZE = 64

# Frame index used for temporal matching in recovery logic
tracking_frame_index = 0

# Recent pose snapshots used for recovery
# Each item: {
#   frame_idx, bbox, bbox_bottom, pose_label, status, safety_reason,
#   safety_details, int_features
# }
recent_pose_snapshots = []


def _bbox_bottom_y(bbox):
    """Get bottom-y coordinate from bbox [x, y, w, h]."""
    if not bbox or len(bbox) < 4:
        return None
    return float(bbox[1]) + float(bbox[3])


def _store_pose_snapshot(frame_idx, bbox, pose_label, status, safety_reason, safety_details, int_features):
    """Store a recent pose snapshot for short-gap recovery."""
    global recent_pose_snapshots

    if bbox is None or pose_label in (None, "", "unknown"):
        return

    bbox_bottom = _bbox_bottom_y(bbox)
    if bbox_bottom is None:
        return

    snapshot = {
        "frame_idx": frame_idx,
        "bbox": [bbox[0], bbox[1], bbox[2], bbox[3]],
        "bbox_bottom": bbox_bottom,
        "pose_label": pose_label,
        "status": status,
        "safety_reason": safety_reason,
        "safety_details": safety_details,
        "int_features": int_features,
    }
    recent_pose_snapshots.append(snapshot)

    if len(recent_pose_snapshots) > POSE_RECOVERY_CACHE_SIZE:
        recent_pose_snapshots = recent_pose_snapshots[-POSE_RECOVERY_CACHE_SIZE:]


def _find_recovery_snapshot(current_bbox, current_frame_idx):
    """Find best recent snapshot by bbox-bottom similarity within allowed frame gap."""
    current_bottom = _bbox_bottom_y(current_bbox)
    if current_bottom is None:
        return None

    best_snapshot = None
    best_bottom_diff = None

    for snap in reversed(recent_pose_snapshots):
        gap = current_frame_idx - snap.get("frame_idx", -999999)

        # Must be from previous frames and within tolerance window
        if gap <= 0:
            continue
        if gap > POSE_RECOVERY_MAX_GAP_FRAMES:
            # older snapshots (further in reversed order) will only be older
            break

        bottom_diff = abs(current_bottom - snap.get("bbox_bottom", current_bottom))
        if bottom_diff <= POSE_RECOVERY_BBOX_BOTTOM_TOLERANCE_PX:
            if best_snapshot is None or bottom_diff < best_bottom_diff:
                best_snapshot = snap
                best_bottom_diff = bottom_diff

    return best_snapshot

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

def should_process_track(keypoints, input_width, input_height):
    """Check if keypoints are complete enough for pose classification and fall detection.
    
    Args:
        keypoints: Flat list of keypoints [x1, y1, x2, y2, ...]
        input_width: Width of the input frame
        input_height: Height of the input frame
    
    Returns:
        bool: True if at least one side (left or right) has all 4 required keypoints visible,
              False otherwise
    """
    if not keypoints or len(keypoints) < 28:  # Need at least 14 keypoints (x,y pairs)
        return False
    
    def is_visible(k_idx, points):
        """Check if a keypoint is visible (within frame bounds).
        
        Args:
            k_idx: 1-based COCO keypoint index
            points: Flat list of keypoints
        
        Returns:
            bool: True if keypoint is visible (within frame bounds)
        """
        # k_idx is 1-based COCO index
        # 0-based index in list is (k_idx - 1) * 2
        idx_x = (k_idx - 1) * 2
        idx_y = idx_x + 1
        if idx_x >= len(points) or idx_y >= len(points):
            return False
        x, y = points[idx_x], points[idx_y]
        # Check if coordinates are within frame bounds
        return x > 0 and x <= input_width and y > 0 and y <= input_height
    
    # Left side: Left Eye (1), Left Shoulder (5), Left Hip (11), Left Knee (13)
    left_visible = (is_visible(1, keypoints) and   # Left Eye
                    is_visible(5, keypoints) and   # Left Shoulder
                    is_visible(11, keypoints) and  # Left Hip
                    is_visible(13, keypoints))     # Left Knee
                    
    # Right side: Right Eye (2), Right Shoulder (6), Right Hip (12), Right Knee (14)
    right_visible = (is_visible(2, keypoints) and   # Right Eye
                     is_visible(6, keypoints) and   # Right Shoulder
                     is_visible(12, keypoints) and  # Right Hip
                     is_visible(14, keypoints))     # Right Knee
    
    return left_visible or right_visible

def update_tracks(objs, current_time_ms=None):
    """Update tracking with new detections"""
    global online_targets, fall_ids, unsafe_ids, tracking_frame_index

    # Increment frame index once per tracking update
    tracking_frame_index += 1
    
    # Convert YOLO objects to tracker objects
    out_bbox = yolo_objs_to_tracker_objs(objs, valid_class_id)
    
    # Update tracker
    tracks = tracker0.update(out_bbox)
    
    return tracks

def process_track(track, objs, camera_id="unknown", is_recording=False, skeleton_saver=None, frame_id=0, fps=30, safety_judgment=None):
    """Process a single track - handle fall detection and safety checking

    Args:
        track: The track object from tracker
        objs: Detected objects from pose extractor
        is_recording: Whether recording is active
        skeleton_saver: SkeletonSaver2D instance for recording
        frame_id: Current frame ID
        fps: Current FPS for fall detection
        safety_judgment: SafetyJudgment instance that combines all area checkers
    """
    global online_targets, fall_ids, unsafe_ids, current_fps, fall_states, tracking_frame_index
    
    pose_label = "unknown"
    safety_reason = "normal"
    safety_details = {}
    
    # Update global FPS
    current_fps = fps
    
    if track.lost:
        return None
    
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
        
        # Always use the best match if we have one
        if best_obj:
            obj = best_obj
            keypoints_np = to_keypoints_np(obj.points)
            
            # Check if keypoints are complete enough for processing
            # This prevents pose classification and fall detection when keypoints are incomplete
            can_process = should_process_track(obj.points, INPUT_WIDTH, INPUT_HEIGHT)
            
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
            
            # Skip pose classification and fall detection if keypoints are incomplete
            if not can_process:
                logger.print("TRACKING", "Track %d: Keypoints incomplete, skipping pose classification and fall detection.", track.id)

                current_bbox = [tracker_obj.x, tracker_obj.y, tracker_obj.w, tracker_obj.h]
                recovered = _find_recovery_snapshot(current_bbox, tracking_frame_index)

                if recovered is not None:
                    logger.print(
                        "TRACKING",
                        "Track %d: Recovered pose from recent snapshot (gap=%d, bottom_diff=%.1f)",
                        track.id,
                        tracking_frame_index - recovered.get("frame_idx", tracking_frame_index),
                        abs(_bbox_bottom_y(current_bbox) - recovered.get("bbox_bottom", _bbox_bottom_y(current_bbox)))
                    )
                
                # Still return track result with default values
                track_result = {
                    "track_id": track.id,
                    "bbox": recovered.get("bbox") if recovered is not None else current_bbox,
                    "keypoints": obj.points,
                    "keypoints_np": keypoints_np,
                    "pose_label": recovered.get("pose_label", "unknown") if recovered is not None else "unknown",
                    "status": recovered.get("status", "normal") if recovered is not None else "normal",
                    "int_features": recovered.get("int_features") if recovered is not None else None,
                    "safety_reason": recovered.get("safety_reason", "normal") if recovered is not None else "normal",
                    "safety_details": recovered.get("safety_details", {}) if recovered is not None else {}
                }
                return track_result
                
            # Get fall_algorithm flag to determine which algorithm to use
            fall_algorithm = 1  # Default: Algorithm 1 (BBox motion only)
            try:
                from control_manager import get_flag
                fall_algorithm = get_flag("fall_algorithm", 1)
            except ImportError:
                pass

            # Determine status
            pose_label = "unknown"
            int_features = None
            
            # Use local fall detection
            if online_targets["bbox"][idx].qsize() >= 2:
                state = fall_states.get(track.id)
                fall_result = check_fall(tracker_obj, online_targets, idx, fps, state=state)
                if fall_result:
                    fall_info, pose_label, int_features, updated_state = fall_result
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
                    # Using INPUT_WIDTH and INPUT_HEIGHT from config
                    normalized_keypoints = normalize_keypoints(obj.points, INPUT_WIDTH, INPUT_HEIGHT)

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
                "status": "fall" if track.id in fall_ids else ("unsafe" if track.id in unsafe_ids else "normal"),
                "safety_reason": safety_reason,
                "safety_details": safety_details
            }

            _store_pose_snapshot(
                tracking_frame_index,
                track_result["bbox"],
                track_result["pose_label"],
                track_result["status"],
                track_result["safety_reason"],
                track_result["safety_details"],
                int_features
            )
            # print(f"process_track() -> {track_result}")
            
            return track_result
    
    return None

def check_fall(tracker_obj, track_history, idx, fps=30, state=None):
    """Check for fall using track history
    
    Returns:
        tuple: (fall_info, pose_label, updated_state)
               - fall_info: Fall detection result tuple
               - pose_label: Pose classification label
               - updated_state: Updated fall detection state dict
    """
    global current_fps, pose_estimator
    
    # Use provided fps or global fps
    effective_fps = fps if fps > 0 else current_fps
    
    pose_data = None
    pose_label = "unknown"
    int_features = None
    
    # Get keypoints from history
    if not track_history["points"][idx].empty():
        keypoints_flat = list(track_history["points"][idx].queue)
        if keypoints_flat:
            # Get the most recent keypoints
            latest_keypoints = keypoints_flat[-1]
            
            # Evaluate pose using PoseEstimation
            try:
                # Evaluate pose with keypoints
                pose_data = pose_estimator.evaluate_pose(np.array(latest_keypoints))

                # Extract label from pose_data
                if pose_data is not None:
                    pose_label = pose_data.get('plain_label', 'unknown')
                    # Get features when HME is enabled for Caregiver payload or Analytics
                    # Since use_hme is hardcoded to True, we always get int_features
                    int_features = pose_estimator.get_int_features()
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
    
    # Actually in judge_fall.py, it expects the exact return. We must just return what is needed.
    # Original get_fall_info returns either 4 or 6. We unpack based on the caller of check_fall.
    # The caller of check_fall is process_track, which unpacks 4 values:
    # fall_info, pose_label, updated_state = fall_result
    # We will expand it to 5 to pass int_features.

    # fall_info can just be whatever get_fall_info returned, minus the 'state' which is the last element
    fall_info = result[:-1] 

    return fall_info, pose_label, int_features, updated_state

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
    global online_targets, fall_ids, unsafe_ids, fall_states, recent_pose_snapshots, tracking_frame_index
    online_targets = {
        "id": [],
        "bbox": [],
        "points": []
    }
    fall_ids.clear()
    unsafe_ids.clear()
    fall_states.clear()
    recent_pose_snapshots.clear()
    tracking_frame_index = 0

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

