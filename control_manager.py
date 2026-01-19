# control_manager.py - Control flags and safe areas management

import os
import json
import time

# ============================================
# FILE PATHS (defined locally to avoid circular imports)
# ============================================

# Local file paths for persistent storage
LOCAL_FLAGS_FILE = "/root/control_flags.json"
SAFE_AREA_FILE = "/root/safe_areas.json"

# ============================================
# CAMERA STATE MANAGER
# ============================================

class CameraStateManager:
    """Singleton manager for camera state (ID and registration status)
    
    This class ensures that camera state changes are properly reflected
    across all modules that need it. It uses a callback system to notify
    interested modules when the registration status changes.
    """
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._camera_id = "camera_000"
            cls._instance._registration_status = "unregistered"
            cls._instance._camera_name = "Unnamed Camera"
            cls._instance._local_ip = ""
            cls._instance._status_change_callbacks = []
        return cls._instance
    
    def get_camera_id(self):
        """Get current camera ID"""
        return self._camera_id
    
    def set_camera_id(self, camera_id, notify=True):
        """Set camera ID and optionally notify callbacks"""
        old_id = self._camera_id
        self._camera_id = camera_id
        if old_id != camera_id:
            print(f"[CameraStateManager] Camera ID updated: {old_id} -> {camera_id}")
    
    def get_registration_status(self):
        """Get current registration status"""
        return self._registration_status
    
    def set_registration_status(self, status, notify=True):
        """Set registration status and notify callbacks if changed"""
        old_status = self._registration_status
        self._registration_status = status
        if old_status != status:
            print(f"[CameraStateManager] Registration status updated: {old_status} -> {status}")
            if notify:
                self._notify_status_change(status)
    
    def get_camera_name(self):
        """Get current camera name"""
        return self._camera_name
    
    def set_camera_name(self, camera_name):
        """Set camera name"""
        self._camera_name = camera_name
    
    def get_local_ip(self):
        """Get local IP address"""
        return self._local_ip
    
    def set_local_ip(self, local_ip):
        """Set local IP address"""
        self._local_ip = local_ip
    
    def register_status_change_callback(self, callback):
        """Register a callback to be called when registration status changes
        
        Args:
            callback: Function that takes (new_status) as argument
        """
        if callback not in self._status_change_callbacks:
            self._status_change_callbacks.append(callback)
            print(f"[CameraStateManager] Registered status change callback")
    
    def unregister_status_change_callback(self, callback):
        """Unregister a status change callback"""
        if callback in self._status_change_callbacks:
            self._status_change_callbacks.remove(callback)
    
    def _notify_status_change(self, new_status):
        """Notify all registered callbacks of status change"""
        for callback in self._status_change_callbacks:
            try:
                callback(new_status)
            except Exception as e:
                print(f"[CameraStateManager] Callback error: {e}")
    
    def get_state(self):
        """Get complete camera state as a dictionary"""
        return {
            "camera_id": self._camera_id,
            "camera_name": self._camera_name,
            "registration_status": self._registration_status,
            "local_ip": self._local_ip
        }
    
    def set_state(self, state_dict, notify=True):
        """Set complete camera state from dictionary"""
        if "camera_id" in state_dict:
            self.set_camera_id(state_dict["camera_id"], notify=notify)
        if "camera_name" in state_dict:
            self._camera_name = state_dict.get("camera_name", "Unnamed Camera")
        if "registration_status" in state_dict:
            self.set_registration_status(state_dict["registration_status"], notify=notify)
        if "local_ip" in state_dict:
            self._local_ip = state_dict.get("local_ip", "")


# Global camera state manager instance
camera_state_manager = CameraStateManager()


# ============================================
# CONTROL FLAGS MANAGEMENT
# ============================================

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
    """Get current camera ID (uses CameraStateManager)"""
    return camera_state_manager.get_camera_id()

def set_camera_id(camera_id):
    """Set camera ID (uses CameraStateManager)"""
    camera_state_manager.set_camera_id(camera_id)

# Convenience functions for CameraStateManager
def get_registration_status():
    """Get current registration status"""
    return camera_state_manager.get_registration_status()

def set_registration_status(status):
    """Set registration status"""
    camera_state_manager.set_registration_status(status)

def register_status_change_callback(callback):
    """Register callback for status changes"""
    camera_state_manager.register_status_change_callback(callback)

def get_camera_name():
    """Get current camera name"""
    return camera_state_manager.get_camera_name()

def set_camera_name(camera_name):
    """Set camera name"""
    camera_state_manager.set_camera_name(camera_name)

def get_camera_state():
    """Get complete camera state"""
    return camera_state_manager.get_state()

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
    global safety_checker
    """Update the safety checker with safe areas"""
    try:
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
    global safety_checker
    
    safety_checker.add_safe_polygon(polygon)
    print(f"Added safe area polygon with {len(polygon)} points")
    
    # Save all safe areas
    save_all_safe_areas()

def clear_safe_areas():
    global safety_checker
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
    global safety_checker
    """Check if a point is in a safe area"""
    if safety_checker is None:
        return True  # No safe areas = everywhere is safe
    return safety_checker.is_point_safe((x, y))

def body_in_safe_zone(body_keypoints, check_method=CheckMethod.TORSO_HEAD):
    global safety_checker
    """Check if body keypoints are in safe zone"""
    if safety_checker is None:
        return True  # No safe areas = everywhere is safe
    return safety_checker.body_in_safe_zone(body_keypoints, check_method)


# ============================================
# STREAMING SERVER COMMUNICATION (Camera State & Safe Areas)
# ============================================

import requests
from debug_config import debug_print

# Import STREAMING_HTTP_URL here to avoid circular import
# This is done at the end so that config.py can safely import from control_manager
def _get_streaming_http_url():
    """Get STREAMING_HTTP_URL from config (lazy import to avoid circular dependency)"""
    from config import STREAMING_HTTP_URL
    return STREAMING_HTTP_URL

# Get camera_id from CameraStateManager instead of direct import
def get_current_camera_id():
    """Get current camera ID from CameraStateManager"""
    return camera_state_manager.get_camera_id()

def send_background_updated(timestamp):
    """Notify streaming server that background was updated"""
    try:
        camera_id = get_current_camera_id()
        STREAMING_HTTP_URL = _get_streaming_http_url()
        url = f"{STREAMING_HTTP_URL}/api/stream/command"
        payload = {
            "CameraId": camera_id,
            "Command": "background_updated",
            "Value": {"timestamp": timestamp}
        }
        debug_print("API_REQUEST", "%s | endpoint: /api/stream/command | payload: %s", "POST", str(payload)[:100])
        response = requests.post(
            url,
            json=payload,
            headers={'Content-Type': 'application/json'},
            timeout=2.0
        )
        return response.status_code == 200
    except Exception as e:
        print(f"Background update notification error: {e}")
        return False

def get_camera_state_from_server():
    """Get camera state (including control flags) from streaming server"""
    try:
        camera_id = get_current_camera_id()
        STREAMING_HTTP_URL = _get_streaming_http_url()
        url = f"{STREAMING_HTTP_URL}/api/stream/camera-state?camera_id={camera_id}"
        debug_print("API_REQUEST", "%s | endpoint: /api/stream/camera-state | params: camera_id=%s", "GET", camera_id)
        response = requests.get(url, timeout=2.0)
        if response.status_code == 200:
            return response.json()
        return None
    except Exception as e:
        print(f"Get camera state error: {e}")
        return None

def get_safe_areas_from_server():
    """Get safe areas from streaming server"""
    try:
        camera_id = get_current_camera_id()
        STREAMING_HTTP_URL = _get_streaming_http_url()
        url = f"{STREAMING_HTTP_URL}/api/stream/safe-areas?camera_id={camera_id}"
        debug_print("API_REQUEST", "%s | endpoint: /api/stream/safe-areas | params: camera_id=%s", "GET", camera_id)
        response = requests.get(url, timeout=2.0)
        if response.status_code == 200:
            return response.json()
        return []
    except Exception as e:
        print(f"Get safe areas error: {e}")
        return []

def report_state(rtmp_connected=False, is_recording=False):
    """Report camera state to streaming server"""
    try:
        camera_id = get_current_camera_id()
        STREAMING_HTTP_URL = _get_streaming_http_url()
        state_report = {
            "CameraId": camera_id,
            "Status": "online",
            "IsRecording": is_recording,
            "RtmpConnected": rtmp_connected
        }
        url = f"{STREAMING_HTTP_URL}/api/stream/report-state"
        debug_print("API_REQUEST", "%s | endpoint: /api/stream/report-state | payload: %s", "POST", str(state_report)[:100])
        response = requests.post(
            url,
            json=state_report,
            headers={'Content-Type': 'application/json'},
            timeout=2.0
        )
        return response.status_code == 200
    except Exception as e:
        print(f"State report error: {e}")
        return False

