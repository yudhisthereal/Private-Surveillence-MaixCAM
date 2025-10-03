# judge_fall.py

FALL_COUNT_THRES = 2  # how many consecutive falls required to confirm

# persistent counter for consecutive falls
fall_counter = 0  

def get_fall_info(online_targets_det, online_targets, index, fallParam, queue_size, fps):
    global fall_counter
    fall_down = False

    # Case: no detection available
    if online_targets["bbox"][index].empty():
        if fall_counter > 0:
            fall_counter = max(0, fall_counter - 1)
            return True  # still report fall during detection gaps
        return False

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

    if v_top > fallParam["v_bbox_y"] or v_height > fallParam["v_bbox_y"]:
        fall_counter = min(FALL_COUNT_THRES, fall_counter + 1)  # increment, cap at threshold
    else:
        fall_counter = max(0, fall_counter - 1)  # decay if not falling

    if fall_counter >= FALL_COUNT_THRES:
        print(f"[FALL] Confirmed fall (counter={fall_counter})")
        fall_down = True

    return fall_down
