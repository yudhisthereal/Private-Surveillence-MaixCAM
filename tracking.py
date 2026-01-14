# tracking.py - Tracking utilities and fall detection helpers

import queue
import numpy as np
from maix import tracker, image
from pose.judge_fall import get_fall_info, FALL_COUNT_THRES
from pose.pose_estimation import PoseEstimation

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
    "v_bbox_y": 0.43,
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

def process_track(track, objs, pose_extractor, img, is_recording=False, skeleton_saver=None, frame_id=0, safety_checker=None, check_method=None, fps=30):
    """Process a single track - draw tracking info and handle fall detection"""
    global online_targets, fall_ids, unsafe_ids, current_fps
    
    # Update global FPS
    current_fps = fps
    
    if track.lost:
        return None
    
    # Find corresponding YOLO object
    for tracker_obj in track.history[-1:]:
        for obj in objs:
            if abs(obj.x - tracker_obj.x) < 10 and abs(obj.y - tracker_obj.y) < 10:
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
                
                # Check for fall detection if we have enough history
                pose_label = "unknown"
                if online_targets["bbox"][idx].qsize() >= 2:
                    fall_result = check_fall(tracker_obj, online_targets, idx, fps)
                    if fall_result:
                        fall_info, pose_label = fall_result
                        (fall_detected_method1, counter_method1,
                         fall_detected_method2, counter_method2,
                         fall_detected_method3, counter_method3) = fall_info
                        
                        # Update fall_ids and unsafe_ids based on detection results
                        # Method 3 is the most conservative/fallback
                        if fall_detected_method3 or fall_detected_method2 or fall_detected_method1:
                            fall_ids.add(track.id)
                            unsafe_ids.discard(track.id)
                        elif counter_method2 > 0 or counter_method1 > 0:
                            unsafe_ids.add(track.id)
                            fall_ids.discard(track.id)
                        else:
                            # No detection, check if we should remove from unsafe
                            if track.id in unsafe_ids:
                                unsafe_ids.discard(track.id)
                
                # Determine status color and message based on updated IDs
                if track.id in fall_ids:
                    msg = f"[{track.id}] FALL"
                    color = image.COLOR_RED
                elif track.id in unsafe_ids:
                    msg = f"[{track.id}] UNSAFE"
                    color = image.COLOR_ORANGE
                else:
                    msg = f"[{track.id}] TRACKING"
                    color = image.COLOR_GREEN
                
                # Draw pose label above tracking info
                img.draw_string(int(tracker_obj.x), int(tracker_obj.y) - 12, f"POSE: {pose_label}", color=image.COLOR_GREEN, scale=0.4)
                
                # Draw tracking info
                img.draw_string(int(tracker_obj.x), int(tracker_obj.y), msg, color=color, scale=0.5)
                pose_extractor.draw_pose(img, obj.points, 8 if pose_extractor.input_width() > 480 else 4, color=color)
                
                # Save to skeleton if recording
                if is_recording and skeleton_saver:
                    safety_status = 1 if track.id in fall_ids else (2 if track.id in unsafe_ids else 0)
                    skeleton_saver.add_keypoints(frame_id, track.id, obj.points, safety_status)
                
                return {
                    "track_id": track.id,
                    "bbox": [tracker_obj.x, tracker_obj.y, tracker_obj.w, tracker_obj.h],
                    "keypoints": obj.points,
                    "keypoints_np": keypoints_np,
                    "status": "fall" if track.id in fall_ids else ("unsafe" if track.id in unsafe_ids else "tracking")
                }
    
    return None

def check_fall(tracker_obj, track_history, idx, fps=30):
    """Check for fall using track history"""
    global current_fps, pose_estimator
    
    # Use provided fps or global fps
    effective_fps = fps if fps > 0 else current_fps
    
    pose_data = None
    pose_label = "unknown"
    
    # Get keypoints from history
    if not track_history["points"][idx].empty():
        keypoints_flat = list(track_history["points"][idx].queue)
        if keypoints_flat:
            # Get the most recent keypoints
            latest_keypoints = keypoints_flat[-1]
            
            # Evaluate pose using PoseEstimation
            try:
                
                # Check if HME mode is enabled
                use_hme = False
                try:
                    from control_manager import get_flag
                    use_hme = get_flag("hme", False)
                except ImportError:
                    pass
                
                # Evaluate pose with keypoints
                pose_data = pose_estimator.evaluate_pose(np.array(latest_keypoints), use_hme)
                
                # Extract label from pose_data
                if pose_data is not None:
                    pose_label = pose_data['label']
            except ImportError:
                # Fallback if pose_estimation not available
                pass
    
    # Get HME flag from control manager
    use_hme = False
    try:
        from control_manager import get_flag
        use_hme = get_flag("hme", False)
    except ImportError:
        pass
    
    # Call fall detection with the tracker object and pose data
    fall_info = get_fall_info(
        tracker_obj,
        track_history,
        idx,
        fallParam,
        queue_size,
        effective_fps,
        pose_data,
        use_hme
    )
    print(f"POSE DATA: {pose_data}")
    
    return fall_info, pose_label

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

