# Debug and Performance Configuration
# Set DEBUG_ENABLED to False to disable all debug prints for production
DEBUG_ENABLED = False

# Set PERF_ENABLED to True to enable performance measurements
PERF_ENABLED = True

def debug_print(tag, message, *args):
    """Print debug messages if debugging is enabled"""
    if DEBUG_ENABLED:
        formatted_message = message % args if args else message
        print(f"[DEBUG {tag}] {formatted_message}")

def log_pose_data(pose_data, source="unknown"):
    """Log pose data for debugging"""
    if DEBUG_ENABLED and pose_data:
        print(f"[DEBUG POSE {source}] Label: {pose_data.get('label', 'N/A')}")
        print(f"[DEBUG POSE {source}] Torso angle: {pose_data.get('torso_angle', 'N/A')}")
        print(f"[DEBUG POSE {source}] Thigh uprightness: {pose_data.get('thigh_uprightness', 'N/A')}")
        
        # Check for fall detection data
        for method in ['method1', 'method2', 'method3', 'fall_detected_old', 'fall_detected_new']:
            if method in pose_data:
                print(f"[DEBUG POSE {source}] {method}: {pose_data[method]}")

def log_fall_detection(fall_detection, algorithm=3):
    """Log fall detection data for debugging"""
    if DEBUG_ENABLED:
        method_key = f"method{algorithm}"
        if fall_detection and method_key in fall_detection:
            data = fall_detection[method_key]
            print(f"[DEBUG FALL Algorithm {algorithm}] Detected: {data.get('detected')}, Counter: {data.get('counter')}")

def perf_measure(func_name, duration_ms):
    """Log performance measurements if enabled"""
    if PERF_ENABLED and duration_ms > 5:  # Only log if operation takes more than 5ms
        print(f"[PERF] {func_name}: {duration_ms:.1f}ms")

def perf_summary(frame_num, total_time, breakdown):
    """Log frame performance summary"""
    if PERF_ENABLED:
        breakdown_str = ", ".join([f"{k}={v:.1f}ms" for k, v in breakdown.items()])
        print(f"[PERF] Frame {frame_num}: Total={total_time:.1f}ms, {breakdown_str}")
