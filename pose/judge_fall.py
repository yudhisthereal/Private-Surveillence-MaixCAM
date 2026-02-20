# judge_fall.py

from debug_config import DebugLogger

# Module-level debug logger instance
logger = DebugLogger(tag="FALL_DETECT", instance_enable=False)

FALL_COUNT_THRES = 2  # how many consecutive falls required to confirm



def get_fall_info(online_targets_det, online_targets, index, fallParam, queue_size, fps, pose_data=None, state=None):
    if state is None:
        state = {
            "counter_bbox_only": 0,
            "counter_motion_pose_and": 0,
            "v_top_max": -1
        }
    
    counter_bbox_only = state.get("counter_bbox_only", 0)
    counter_motion_pose_and = state.get("counter_motion_pose_and", 0)
    v_top_max = state.get("v_top_max", -1)

    fall_detected_bbox_only = False      # Algorithm 1
    fall_detected_motion_pose_and = False # Algorithm 2

    # Note: Visibility check is now handled in tracking.py's should_process_track()
    # before calling pose classification and fall detection.
    # This function now only handles fall detection logic.

    # Reset all counters when pose data is invalid
    if pose_data is None or (isinstance(pose_data, dict) and pose_data.get('label') == "None"):
        counter_bbox_only = max(0, counter_bbox_only - 1)
        counter_motion_pose_and = max(0, counter_motion_pose_and - 1)
        
        state["counter_bbox_only"] = counter_bbox_only
        state["counter_motion_pose_and"] = counter_motion_pose_and
        
        return fall_detected_bbox_only, counter_bbox_only, fall_detected_motion_pose_and, counter_motion_pose_and, state

    # Case: no detection available
    if online_targets["bbox"][index].empty():
        if counter_bbox_only > 0:
            counter_bbox_only = max(0, counter_bbox_only - 1)
            counter_motion_pose_and = max(0, counter_motion_pose_and - 1)
            
            state["counter_bbox_only"] = counter_bbox_only
            state["counter_motion_pose_and"] = counter_motion_pose_and
            
            return True, counter_bbox_only, False, counter_motion_pose_and, state  # still report bbox_only during detection gaps
        return False, counter_bbox_only, False, counter_motion_pose_and, state

    # Get current and previous bounding boxes
    cur_bbox = [online_targets_det.x, online_targets_det.y, online_targets_det.w, online_targets_det.h]
    pre_bbox = online_targets["bbox"][index].get()
    _ = online_targets["points"][index].get()  # keep points queue in sync with bbox

    elapsed_ms = queue_size * 1000 / fps if fps > 0 else queue_size * 1000

    # 1. Vertical speed of top (y) coordinate — downward movement = positive
    dy_top = cur_bbox[1] - pre_bbox[1]
    v_top = dy_top / elapsed_ms
    v_top_max = max(v_top, v_top_max)
    
    state["v_top_max"] = v_top_max

    # 2. Percentage of shrinkage of bbox height
    # Logic: if previous is higher than current y (moved down), check shrinkage
    shrinkage = 0.0
    if dy_top > 0 and pre_bbox[3] > 0:
        shrinkage = (pre_bbox[3] - cur_bbox[3]) / pre_bbox[3]

    logger.print("FALL_DETECT", "dy_top=%.2f, shrinkage=%.4f, threshold=%s, fps=%s", dy_top, shrinkage, fallParam['v_bbox_y'], fps)

    # Extract from pose_data
    torso_angle = None
    thigh_uprightness = None

    if pose_data and isinstance(pose_data, dict):
        torso_angle = pose_data.get('torso_angle')
        thigh_uprightness = pose_data.get('thigh_uprightness')
        logger.print("FALL_DETECT", "Pose mode: torso_angle=%s, thigh_uprightness=%s", torso_angle, thigh_uprightness)

    # Calculate bbox motion evidence
    # User Request: if moved down AND shrinkage > threshold -> Fall
    bbox_motion_detected = (dy_top > 0 and abs(shrinkage) > fallParam["v_bbox_y"])
    
    # Calculate pose condition
    strict_pose_condition = False

    if torso_angle is not None and thigh_uprightness is not None:
        # Strict condition: clearly lying down
        strict_pose_condition = (torso_angle > 80 and thigh_uprightness > 60)
    
    # Algorithm 1: BBox Only
    if bbox_motion_detected:
        counter_bbox_only = min(FALL_COUNT_THRES, counter_bbox_only + 1)
    else:
        counter_bbox_only = max(0, counter_bbox_only - 1)

    # Algorithm 2: BBox Motion AND Strict Pose
    if bbox_motion_detected and strict_pose_condition:
        # Strong evidence: both motion AND clearly lying down
        counter_motion_pose_and = min(FALL_COUNT_THRES, counter_motion_pose_and + 2)
    elif bbox_motion_detected or strict_pose_condition:
        # Moderate evidence: one or the other
        counter_motion_pose_and = min(FALL_COUNT_THRES, counter_motion_pose_and + 1)
    else:
        # No evidence
        counter_motion_pose_and = max(0, counter_motion_pose_and - 1)

    # Determine fall status for each algorithm
    if counter_bbox_only >= FALL_COUNT_THRES:
        logger.print("FALL_DETECT", "Fall detected (BBox only, counter=%d)", counter_bbox_only)
        fall_detected_bbox_only = True
    if counter_motion_pose_and >= FALL_COUNT_THRES:
        logger.print("FALL_DETECT", "Fall detected (Motion+Pose AND, counter=%d)", counter_motion_pose_and)
        fall_detected_motion_pose_and = True

    state["counter_bbox_only"] = counter_bbox_only
    state["counter_motion_pose_and"] = counter_motion_pose_and

    return (
        fall_detected_bbox_only, counter_bbox_only,
        fall_detected_motion_pose_and, counter_motion_pose_and,
        state
    )
