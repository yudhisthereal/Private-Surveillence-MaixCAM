# judge_fall.py

FALL_COUNT_THRES = 2  # how many consecutive falls required to confirm

# persistent counter for consecutive falls
fall_counter = 0  # the current method (bbox only)
fall_counter_new = 0 # experimental method (bbox + pose)

def get_fall_info(online_targets_det, online_targets, index, fallParam, queue_size, fps, pose_data=None):
    global fall_counter
    global fall_counter_new

    fall_down_old = False
    fall_down_new = False

    # Skip fall judgment entirely if pose_data is None or label is "None"
    if pose_data is None or (isinstance(pose_data, dict) and pose_data.get('label') == "None"):
        # Reset counters when pose data is invalid
        fall_counter = max(0, fall_counter - 1)
        fall_counter_new = max(0, fall_counter_new - 1)
        return False, fall_counter, False, fall_counter_new

    # Case: no detection available
    if online_targets["bbox"][index].empty():
        if fall_counter > 0:
            fall_counter = max(0, fall_counter - 1)
            fall_counter_new = max(0, fall_counter_new - 1)
            return True, fall_counter, False, fall_counter_new  # still report fall during detection gaps
        return False, fall_counter, False, fall_counter_new

    # Get current and previous bounding boxes
    cur_bbox = [online_targets_det.x, online_targets_det.y, online_targets_det.w, online_targets_det.h]
    pre_bbox = online_targets["bbox"][index].get()
    _ = online_targets["points"][index].get()  # keep points queue in sync with bbox

    elapsed_ms = queue_size * 1000 / fps if fps > 0 else queue_size * 1000

    # 1. Vertical speed of top (y) coordinate — downward movement = positive
    dy_top = cur_bbox[1] - pre_bbox[1]
    v_top = dy_top / elapsed_ms

    # 2. Vertical change of height (shrinking = falling)
    dh = pre_bbox[3] - cur_bbox[3]
    v_height = dh / elapsed_ms

    print(f"[DEBUG] v_top = {v_top:.6f}, v_height = {v_height:.6f}, threshold = {fallParam['v_bbox_y']}")

    # Extract pose information if available
    is_lying_down = False
    torso_angle = None
    thigh_uprightness = None
    
    if pose_data and isinstance(pose_data, dict):
        torso_angle = pose_data.get('torso_angle')
        thigh_uprightness = pose_data.get('thigh_uprightness')
        pose_label = pose_data.get('label', '')
        
        # Determine if lying down based on both angles and label
        if torso_angle is not None and thigh_uprightness is not None:
            is_lying_down = (torso_angle > 80 and thigh_uprightness > 60)
        else:
            is_lying_down = ("lying" in pose_label.lower())
            
        print(f"[DEBUG POSE] torso_angle={torso_angle}, thigh_uprightness={thigh_uprightness}, is_lying_down={is_lying_down}, label={pose_label}")
    elif pose_data and isinstance(pose_data, str):
        # If we only have the pose label
        is_lying_down = ("lying" in pose_data.lower())
        print(f"[DEBUG POSE] pose_label={pose_data}, is_lying_down={is_lying_down}")

    # Original method: only bounding box dynamics
    if v_top > fallParam["v_bbox_y"] or v_height > fallParam["v_bbox_y"]:
        fall_counter = min(FALL_COUNT_THRES, fall_counter + 1)
    else:
        fall_counter = max(0, fall_counter - 1)

    # New method: bounding box dynamics + pose confirmation
    bbox_fall_detected = (v_top > fallParam["v_bbox_y"] or v_height > fallParam["v_bbox_y"])
    
    if bbox_fall_detected and is_lying_down:
        # Strong evidence: both motion and pose indicate fall
        fall_counter_new = min(FALL_COUNT_THRES, fall_counter_new + 2)  # faster confirmation
    elif bbox_fall_detected:
        # Only motion detected - moderate evidence
        fall_counter_new = min(FALL_COUNT_THRES, fall_counter_new + 1)
    elif is_lying_down and torso_angle is not None and thigh_uprightness is not None:
        # Only pose detected - weak evidence (only if we have angle data)
        fall_counter_new = min(FALL_COUNT_THRES, fall_counter_new + 1)
    else:
        # No evidence - decay counter
        fall_counter_new = max(0, fall_counter_new - 1)

    # Determine fall status
    if fall_counter >= FALL_COUNT_THRES:
        print(f"[FALL] Confirmed fall (counter={fall_counter})")
        fall_down_old = True
    
    if fall_counter_new >= FALL_COUNT_THRES:
        print(f"[FALL NEW] Confirmed fall (counter_new={fall_counter_new})")
        fall_down_new = True

    return fall_down_old, fall_counter, fall_down_new, fall_counter_new