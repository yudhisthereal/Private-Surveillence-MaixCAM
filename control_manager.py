# control_manager.py - Control flags and safe areas management

import os
import json
import time
from config import LOCAL_FLAGS_FILE, SAFE_AREA_FILE

# Control flags (will be synced from streaming server)
control_flags = {
    "record": False,
    "show_raw": False,
    "set_background": False,
    "auto_update_bg": False,
    "show_safe_area": False,
    "use_safety_check": True,
    "analytics_mode": True,
    "fall_algorithm": 3,
    "hme": False
}

# Flag change callbacks
_flag_change_callbacks = []

def register_flag_change_callback(callback):
    """Register a callback to be called when flags change"""
    _flag_change_callbacks.append(callback)

def notify_flag_change(flag_name, value):
    """Notify all registered callbacks of flag change"""
    for callback in _flag_change_callbacks:
        callback(flag_name, value)

def save_control_flags():
    """Save control flags to local storage"""
    try:
        flags_data = {
            "control_flags": control_flags,
            "timestamp": int(time.time() * 1000),
            "camera_id": get_camera_id(),
            "saved_locally": True
        }
        
        with open(LOCAL_FLAGS_FILE, 'w') as f:
            json.dump(flags_data, f, indent=2)
        print(f"Control flags saved locally to {LOCAL_FLAGS_FILE}")
        return True
        
    except Exception as e:
        print(f"Error saving control flags: {e}")
        return False

def load_initial_flags():
    """Load initial flags from local storage"""
    try:
        if os.path.exists(LOCAL_FLAGS_FILE):
            with open(LOCAL_FLAGS_FILE, 'r') as f:
                data = json.load(f)
                if "control_flags" in data:
                    # Update only if flags are newer (5 minute threshold)
                    saved_time = data.get("timestamp", 0)
                    current_time = int(time.time() * 1000)
                    
                    if current_time - saved_time < 300000:  # 5 minutes
                        for key in control_flags.keys():
                            if key in data["control_flags"]:
                                control_flags[key] = data["control_flags"][key]
                        print(f"Loaded flags from local storage (saved {((current_time - saved_time)/1000):.0f}s ago)")
                        return True
                    else:
                        print(f"Local flags too old ({((current_time - saved_time)/1000):.0f}s), ignoring")
    except Exception as e:
        print(f"Error loading initial flags from local file: {e}")
    
    return False

def get_control_flags():
    """Get current control flags"""
    return control_flags.copy()

def update_control_flag(flag_name, value):
    """Update a control flag"""
    if flag_name in control_flags:
        old_value = control_flags[flag_name]
        if old_value != value:
            control_flags[flag_name] = value
            print(f"[SYNC] Flag updated: {flag_name} = {value}")
            notify_flag_change(flag_name, value)
            save_control_flags()
        return True
    return False

def update_control_flags_from_server(flags_data):
    """Update control flags from server data"""
    flags_updated = False
    for key in control_flags.keys():
        if key in flags_data:
            old_value = control_flags[key]
            new_value = flags_data[key]
            if old_value != new_value:
                control_flags[key] = new_value
                print(f"[SYNC] Flag updated: {key} = {new_value}")
                flags_updated = True
    
    if flags_updated:
        save_control_flags()
    
    return flags_updated

def get_flag(flag_name, default=None):
    """Get a specific flag value"""
    return control_flags.get(flag_name, default)

def set_flag(flag_name, value):
    """Set a specific flag value"""
    return update_control_flag(flag_name, value)

def get_camera_id():
    """Get current camera ID"""
    return control_flags.get("_camera_id", "camera_000")

def set_camera_id(camera_id):
    """Set camera ID"""
    control_flags["_camera_id"] = camera_id

# ============================================
# SAFE AREAS MANAGEMENT
# ============================================

# Safety checker instance (will be initialized in main)
safety_checker = None

class CheckMethod:
    """Check methods for safety checking"""
    HIP = 1
    TORSO = 2
    TORSO_HEAD = 3
    TORSO_HEAD_KNEES = 4
    FULL_BODY = 5

def initialize_safety_checker(safety_checker_instance):
    """Initialize safety checker instance"""
    global safety_checker
    safety_checker = safety_checker_instance

def get_safety_checker():
    """Get safety checker instance"""
    return safety_checker

def load_safe_areas():
    """Load safe areas from JSON file"""
    try:
        if os.path.exists(SAFE_AREA_FILE):
            with open(SAFE_AREA_FILE, 'r') as f:
                safe_areas = json.load(f)
            print(f"Loaded {len(safe_areas)} safe area(s) from file")
            return safe_areas
        else:
            print("No safe areas file found, using default")
            return []
    except Exception as e:
        print(f"Error loading safe areas: {e}")
        return []

def save_safe_areas(safe_areas):
    """Save safe areas to JSON file"""
    try:
        with open(SAFE_AREA_FILE, 'w') as f:
            json.dump(safe_areas, f, indent=2)
        print(f"Saved {len(safe_areas)} safe area(s) to file")
        return True
    except Exception as e:
        print(f"Error saving safe areas: {e}")
        return False

def update_safety_checker_polygons(safe_areas):
    """Update the safety checker with safe areas"""
    try:
        if safety_checker is None:
            from tools.safe_area import BodySafetyChecker
            safety_checker = BodySafetyChecker()
        
        safety_checker.clear_safe_polygons()
        for polygon in safe_areas:
            if isinstance(polygon, list) and len(polygon) >= 3:
                safety_checker.add_safe_polygon(polygon)
        
        print(f"Updated safety checker with {len(safe_areas)} polygon(s)")
        
        # Save to local file
        save_safe_areas(safe_areas)
        
        return True
        
    except Exception as e:
        print(f"Error updating safety checker: {e}")
        return False

def add_safe_area(polygon):
    """Add a safe area polygon"""
    from tools.safe_area import BodySafetyChecker
    
    global safety_checker
    
    if safety_checker is None:
        safety_checker = BodySafetyChecker()
    
    safety_checker.add_safe_polygon(polygon)
    print(f"Added safe area polygon with {len(polygon)} points")
    
    # Save all safe areas
    save_all_safe_areas()

def clear_safe_areas():
    """Clear all safe areas"""
    if safety_checker:
        safety_checker.clear_safe_polygons()
        print("Cleared all safe areas")
        save_safe_areas([])

def save_all_safe_areas():
    """Save all current safe areas from safety checker"""
    if safety_checker:
        save_safe_areas(safety_checker.safe_polygons)

def is_point_safe(x, y):
    """Check if a point is in a safe area"""
    if safety_checker is None:
        return True  # No safe areas = everywhere is safe
    return safety_checker.is_point_safe((x, y))

def body_in_safe_zone(body_keypoints, check_method=CheckMethod.TORSO_HEAD):
    """Check if body keypoints are in safe zone"""
    if safety_checker is None:
        return True  # No safe areas = everywhere is safe
    return safety_checker.body_in_safe_zone(body_keypoints, check_method)

