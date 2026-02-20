# control_manager.py - Control flags and safe areas management

import os
import json
import time
import threading
from debug_config import DebugLogger
from tools.polygon_checker import CheckMethod

# Module-level debug logger instance
logger = DebugLogger(tag="CTRL_MGR", instance_enable=False)

# ============================================
# FILE PATHS (defined locally to avoid circular imports)
# ============================================

# Local file paths for persistent storage
LOCAL_FLAGS_FILE = "/root/control_flags.json"
BED_AREA_FILE = "/root/bed_areas.json"
FLOOR_AREA_FILE = "/root/floor_areas.json"
CHAIR_AREA_FILE = "/root/chair_areas.json"
COUCH_AREA_FILE = "/root/couch_areas.json"
BENCH_AREA_FILE = "/root/bench_areas.json"

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
            cls._instance._check_method = 3  # Default: TORSO_HEAD
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
            logger.print("CAM_STATE", "Camera ID updated: %s -> %s", old_id, camera_id)
    
    def get_registration_status(self):
        """Get current registration status"""
        return self._registration_status
    
    def set_registration_status(self, status, notify=True):
        """Set registration status and notify callbacks if changed"""
        old_status = self._registration_status
        self._registration_status = status
        if old_status != status:
            logger.print("CAM_STATE", "Registration status updated: %s -> %s", old_status, status)
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

    def get_check_method(self):
        """Get current safety check method"""
        return self._check_method

    def set_check_method(self, check_method, notify=True):
        """Set safety check method and optionally notify callbacks"""
        old_method = self._check_method
        self._check_method = check_method
        if old_method != check_method:
            logger.print("CAM_STATE", "Check method updated: %s -> %s", old_method, check_method)

    def register_status_change_callback(self, callback):
        """Register a callback to be called when registration status changes
        
        Args:
            callback: Function that takes (new_status) as argument
        """
        if callback not in self._status_change_callbacks:
            self._status_change_callbacks.append(callback)
            logger.print("CAM_STATE", "Registered status change callback")
    
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
                logger.print("CAM_STATE", "Callback error: %s", e)
    
    def get_state(self):
        """Get complete camera state as a dictionary"""
        return {
            "camera_id": self._camera_id,
            "camera_name": self._camera_name,
            "registration_status": self._registration_status,
            "local_ip": self._local_ip,
            "check_method": self._check_method
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
        if "check_method" in state_dict:
            self._check_method = state_dict.get("check_method", 3)


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
    "show_safe_areas": False,
    "show_bed_areas": False,
    "show_floor_areas": False,
    "use_safety_check": True,
    "analytics_mode": True,
    "fall_algorithm": 1,
    "check_method": 3,  # CheckMethod.TORSO_HEAD
    "hme": False,
    "max_sleep_duration": 0,  # Minutes, 0 = disabled
    "bedtime": "", # "HH:MM", e.g. "22:00"
    "wakeup_time": "" # "HH:MM", e.g. "07:00"
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
        logger.print("FLAGS", "Control flags saved locally to %s", LOCAL_FLAGS_FILE)
        return True
        
    except Exception as e:
        logger.print("FLAGS", "Error saving control flags: %s", e)
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
                        logger.print("FLAGS", "Loaded flags from local storage (saved %ds ago)", (current_time - saved_time) / 1000)
                        return True
                    else:
                        logger.print("FLAGS", "Local flags too old (%ds), ignoring", (current_time - saved_time) / 1000)
    except Exception as e:
        logger.print("FLAGS", "Error loading initial flags from local file: %s", e)
    
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
            logger.print("FLAGS", "Flag updated: %s = %s", flag_name, value)
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
                logger.print("FLAGS", "Flag updated: %s = %s", key, new_value)
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
def body_in_safe_zone(body_keypoints, check_method=CheckMethod.TORSO_HEAD):
    global safety_checker
    """Check if body keypoints are in safe zone"""
    if safety_checker is None:
        return True  # No safe areas = everywhere is safe
    return safety_checker.body_in_safe_zone(body_keypoints, check_method)


# ============================================
# BED AREAS MANAGEMENT
# ============================================

# Bed area checker instance (will be initialized in main)
bed_area_checker = None

def initialize_bed_area_checker(bed_area_checker_instance):
    """Initialize bed area checker instance"""
    global bed_area_checker
    bed_area_checker = bed_area_checker_instance

def get_bed_area_checker():
    """Get bed area checker instance"""
    return bed_area_checker

def load_bed_areas():
    """Load bed areas from JSON file"""
    try:
        if os.path.exists(BED_AREA_FILE):
            with open(BED_AREA_FILE, 'r') as f:
                bed_areas = json.load(f)
            logger.print("BED_AREA", "Loaded %d bed area(s) from file", len(bed_areas))
            return bed_areas
        else:
            logger.print("BED_AREA", "No bed areas file found, using default")
            return []
    except Exception as e:
        logger.print("BED_AREA", "Error loading bed areas: %s", e)
        return []

def save_bed_areas(bed_areas):
    """Save bed areas to JSON file"""
    try:
        with open(BED_AREA_FILE, 'w') as f:
            json.dump(bed_areas, f, indent=2)
        logger.print("BED_AREA", "Saved %d bed area(s) to file", len(bed_areas))
        return True
    except Exception as e:
        logger.print("BED_AREA", "Error saving bed areas: %s", e)
        return False

def update_bed_area_polygons(bed_areas):
    global bed_area_checker
    """Update the bed area checker with bed areas"""
    try:
        bed_area_checker.clear_bed_polygons()
        for polygon in bed_areas:
            if isinstance(polygon, list) and len(polygon) >= 3:
                bed_area_checker.add_bed_polygon(polygon)

        logger.print("BED_AREA", "Updated bed area checker with %d polygon(s)", len(bed_areas))

        # Save to local file
        save_bed_areas(bed_areas)

        return True

    except Exception as e:
        logger.print("BED_AREA", "Error updating bed area checker: %s", e)
        return False

def add_bed_area(polygon):
    """Add a bed area polygon"""
    global bed_area_checker

    bed_area_checker.add_bed_polygon(polygon)
    logger.print("BED_AREA", "Added bed area polygon with %d points", len(polygon))

    # Save all bed areas
    save_all_bed_areas()

def clear_bed_areas():
    global bed_area_checker
    """Clear all bed areas"""
    if bed_area_checker:
        bed_area_checker.clear_bed_polygons()
        logger.print("BED_AREA", "Cleared all bed areas")
        save_bed_areas([])

def save_all_bed_areas():
    """Save all current bed areas from bed area checker"""
    if bed_area_checker:
        save_bed_areas(bed_area_checker.bed_polygons)


# ============================================
# FLOOR AREAS MANAGEMENT
# ============================================

# Floor area checker instance (will be initialized in main)
floor_area_checker = None

def initialize_floor_area_checker(floor_area_checker_instance):
    """Initialize floor area checker instance"""
    global floor_area_checker
    floor_area_checker = floor_area_checker_instance

def get_floor_area_checker():
    """Get floor area checker instance"""
    return floor_area_checker

def load_floor_areas():
    """Load floor areas from JSON file"""
    try:
        if os.path.exists(FLOOR_AREA_FILE):
            with open(FLOOR_AREA_FILE, 'r') as f:
                floor_areas = json.load(f)
            logger.print("FLOOR_AREA", "Loaded %d floor area(s) from file", len(floor_areas))
            return floor_areas
        else:
            logger.print("FLOOR_AREA", "No floor areas file found, using default")
            return []
    except Exception as e:
        logger.print("FLOOR_AREA", "Error loading floor areas: %s", e)
        return []

def save_floor_areas(floor_areas):
    """Save floor areas to JSON file"""
    try:
        with open(FLOOR_AREA_FILE, 'w') as f:
            json.dump(floor_areas, f, indent=2)
        logger.print("FLOOR_AREA", "Saved %d floor area(s) to file", len(floor_areas))
        return True
    except Exception as e:
        logger.print("FLOOR_AREA", "Error saving floor areas: %s", e)
        return False

def update_floor_area_polygons(floor_areas):
    global floor_area_checker
    """Update the floor area checker with floor areas"""
    try:
        floor_area_checker.clear_floor_polygons()
        for polygon in floor_areas:
            if isinstance(polygon, list) and len(polygon) >= 3:
                floor_area_checker.add_floor_polygon(polygon)

        logger.print("FLOOR_AREA", "Updated floor area checker with %d polygon(s)", len(floor_areas))

        # Save to local file
        save_floor_areas(floor_areas)

        return True

    except Exception as e:
        logger.print("FLOOR_AREA", "Error updating floor area checker: %s", e)
        return False

def add_floor_area(polygon):
    """Add a floor area polygon"""
    global floor_area_checker

    floor_area_checker.add_floor_polygon(polygon)
    logger.print("FLOOR_AREA", "Added floor area polygon with %d points", len(polygon))

    # Save all floor areas
    save_all_floor_areas()

def clear_floor_areas():
    global floor_area_checker
    """Clear all floor areas"""
    if floor_area_checker:
        floor_area_checker.clear_floor_polygons()
        logger.print("FLOOR_AREA", "Cleared all floor areas")
        save_floor_areas([])

def save_all_floor_areas():
    """Save all current floor areas from floor area checker"""
    if floor_area_checker:
        save_floor_areas(floor_area_checker.floor_polygons)


# ============================================
# CHAIR AREAS MANAGEMENT
# ============================================

# Chair area checker instance (will be initialized in main)
chair_area_checker = None

def initialize_chair_area_checker(chair_area_checker_instance):
    """Initialize chair area checker instance"""
    global chair_area_checker
    chair_area_checker = chair_area_checker_instance

def get_chair_area_checker():
    """Get chair area checker instance"""
    return chair_area_checker

def load_chair_areas():
    """Load chair areas from JSON file"""
    try:
        if os.path.exists(CHAIR_AREA_FILE):
            with open(CHAIR_AREA_FILE, 'r') as f:
                chair_areas = json.load(f)
            logger.print("CHAIR_AREA", "Loaded %d chair area(s) from file", len(chair_areas))
            return chair_areas
        else:
            logger.print("CHAIR_AREA", "No chair areas file found, using default")
            return []
    except Exception as e:
        logger.print("CHAIR_AREA", "Error loading chair areas: %s", e)
        return []

def save_chair_areas(chair_areas):
    """Save chair areas to JSON file"""
    try:
        with open(CHAIR_AREA_FILE, 'w') as f:
            json.dump(chair_areas, f, indent=2)
        logger.print("CHAIR_AREA", "Saved %d chair area(s) to file", len(chair_areas))
        return True
    except Exception as e:
        logger.print("CHAIR_AREA", "Error saving chair areas: %s", e)
        return False

def update_chair_area_polygons(chair_areas):
    global chair_area_checker
    """Update the chair area checker with chair areas"""
    try:
        chair_area_checker.clear_chair_polygons()
        for polygon in chair_areas:
            if isinstance(polygon, list) and len(polygon) >= 3:
                chair_area_checker.add_chair_polygon(polygon)

        logger.print("CHAIR_AREA", "Updated chair area checker with %d polygon(s)", len(chair_areas))

        # Save to local file
        save_chair_areas(chair_areas)

        return True

    except Exception as e:
        logger.print("CHAIR_AREA", "Error updating chair area checker: %s", e)
        return False

def add_chair_area(polygon):
    """Add a chair area polygon"""
    global chair_area_checker

    chair_area_checker.add_chair_polygon(polygon)
    logger.print("CHAIR_AREA", "Added chair area polygon with %d points", len(polygon))

    # Save all chair areas
    save_all_chair_areas()

def clear_chair_areas():
    global chair_area_checker
    """Clear all chair areas"""
    if chair_area_checker:
        chair_area_checker.clear_chair_polygons()
        logger.print("CHAIR_AREA", "Cleared all chair areas")
        save_chair_areas([])

def save_all_chair_areas():
    """Save all current chair areas from chair area checker"""
    if chair_area_checker:
        save_chair_areas(chair_area_checker.chair_polygons)


# ============================================
# COUCH AREAS MANAGEMENT
# ============================================

# Couch area checker instance (will be initialized in main)
couch_area_checker = None

def initialize_couch_area_checker(couch_area_checker_instance):
    """Initialize couch area checker instance"""
    global couch_area_checker
    couch_area_checker = couch_area_checker_instance

def get_couch_area_checker():
    """Get couch area checker instance"""
    return couch_area_checker

def load_couch_areas():
    """Load couch areas from JSON file"""
    try:
        if os.path.exists(COUCH_AREA_FILE):
            with open(COUCH_AREA_FILE, 'r') as f:
                couch_areas = json.load(f)
            logger.print("COUCH_AREA", "Loaded %d couch area(s) from file", len(couch_areas))
            return couch_areas
        else:
            logger.print("COUCH_AREA", "No couch areas file found, using default")
            return []
    except Exception as e:
        logger.print("COUCH_AREA", "Error loading couch areas: %s", e)
        return []

def save_couch_areas(couch_areas):
    """Save couch areas to JSON file"""
    try:
        with open(COUCH_AREA_FILE, 'w') as f:
            json.dump(couch_areas, f, indent=2)
        logger.print("COUCH_AREA", "Saved %d couch area(s) to file", len(couch_areas))
        return True
    except Exception as e:
        logger.print("COUCH_AREA", "Error saving couch areas: %s", e)
        return False

def update_couch_area_polygons(couch_areas):
    global couch_area_checker
    """Update the couch area checker with couch areas"""
    try:
        couch_area_checker.clear_couch_polygons()
        for polygon in couch_areas:
            if isinstance(polygon, list) and len(polygon) >= 3:
                couch_area_checker.add_couch_polygon(polygon)

        logger.print("COUCH_AREA", "Updated couch area checker with %d polygon(s)", len(couch_areas))

        # Save to local file
        save_couch_areas(couch_areas)

        return True

    except Exception as e:
        logger.print("COUCH_AREA", "Error updating couch area checker: %s", e)
        return False

def add_couch_area(polygon):
    """Add a couch area polygon"""
    global couch_area_checker

    couch_area_checker.add_couch_polygon(polygon)
    logger.print("COUCH_AREA", "Added couch area polygon with %d points", len(polygon))

    # Save all couch areas
    save_all_couch_areas()

def clear_couch_areas():
    global couch_area_checker
    """Clear all couch areas"""
    if couch_area_checker:
        couch_area_checker.clear_couch_polygons()
        logger.print("COUCH_AREA", "Cleared all couch areas")
        save_couch_areas([])

def save_all_couch_areas():
    """Save all current couch areas from couch area checker"""
    if couch_area_checker:
        save_couch_areas(couch_area_checker.couch_polygons)


# ============================================
# BENCH AREAS MANAGEMENT
# ============================================

# Bench area checker instance (will be initialized in main)
bench_area_checker = None

def initialize_bench_area_checker(bench_area_checker_instance):
    """Initialize bench area checker instance"""
    global bench_area_checker
    bench_area_checker = bench_area_checker_instance

def get_bench_area_checker():
    """Get bench area checker instance"""
    return bench_area_checker

def load_bench_areas():
    """Load bench areas from JSON file"""
    try:
        if os.path.exists(BENCH_AREA_FILE):
            with open(BENCH_AREA_FILE, 'r') as f:
                bench_areas = json.load(f)
            logger.print("BENCH_AREA", "Loaded %d bench area(s) from file", len(bench_areas))
            return bench_areas
        else:
            logger.print("BENCH_AREA", "No bench areas file found, using default")
            return []
    except Exception as e:
        logger.print("BENCH_AREA", "Error loading bench areas: %s", e)
        return []

def save_bench_areas(bench_areas):
    """Save bench areas to JSON file"""
    try:
        with open(BENCH_AREA_FILE, 'w') as f:
            json.dump(bench_areas, f, indent=2)
        logger.print("BENCH_AREA", "Saved %d bench area(s) to file", len(bench_areas))
        return True
    except Exception as e:
        logger.print("BENCH_AREA", "Error saving bench areas: %s", e)
        return False

def update_bench_area_polygons(bench_areas):
    global bench_area_checker
    """Update the bench area checker with bench areas"""
    try:
        bench_area_checker.clear_bench_polygons()
        for polygon in bench_areas:
            if isinstance(polygon, list) and len(polygon) >= 3:
                bench_area_checker.add_bench_polygon(polygon)

        logger.print("BENCH_AREA", "Updated bench area checker with %d polygon(s)", len(bench_areas))

        # Save to local file
        save_bench_areas(bench_areas)

        return True

    except Exception as e:
        logger.print("BENCH_AREA", "Error updating bench area checker: %s", e)
        return False

def add_bench_area(polygon):
    """Add a bench area polygon"""
    global bench_area_checker

    bench_area_checker.add_bench_polygon(polygon)
    logger.print("BENCH_AREA", "Added bench area polygon with %d points", len(polygon))

    # Save all bench areas
    save_all_bench_areas()

def clear_bench_areas():
    global bench_area_checker
    """Clear all bench areas"""
    if bench_area_checker:
        bench_area_checker.clear_bench_polygons()
        logger.print("BENCH_AREA", "Cleared all bench areas")
        save_bench_areas([])

def save_all_bench_areas():
    """Save all current bench areas from bench area checker"""
    if bench_area_checker:
        save_bench_areas(bench_area_checker.bench_polygons)




# ============================================
# STREAMING SERVER COMMUNICATION (Camera State & Safe Areas)
# ============================================

import requests

# Import STREAMING_HTTP_URL here to avoid circular import
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
        logger.print("API_REQUEST", "%s | endpoint: /api/stream/command | payload: %s", "POST", str(payload)[:100])
        response = requests.post(
            url,
            json=payload,
            headers={'Content-Type': 'application/json'},
            timeout=2.0
        )
        return response.status_code == 200
    except Exception as e:
        logger.print("CTRL_MGR", "Background update notification error: %s", e)
        return False

def get_camera_state_from_server():
    """Get camera state (including control flags) from streaming server"""
    try:
        camera_id = get_current_camera_id()
        STREAMING_HTTP_URL = _get_streaming_http_url()
        url = f"{STREAMING_HTTP_URL}/api/stream/camera-state?camera_id={camera_id}"
        logger.print("API_REQUEST", "%s | endpoint: /api/stream/camera-state | params: camera_id=%s", "GET", camera_id)
        response = requests.get(url, timeout=2.0)
        if response.status_code == 200:
            return response.json()
        return None
    except Exception as e:
        logger.print("CTRL_MGR", "Get camera state error: %s", e)
        return None


def get_bed_areas_from_server():
    """Get bed areas from streaming server"""
    try:
        camera_id = get_current_camera_id()
        STREAMING_HTTP_URL = _get_streaming_http_url()
        url = f"{STREAMING_HTTP_URL}/api/stream/bed-areas?camera_id={camera_id}"
        logger.print("API_REQUEST", "%s | endpoint: /api/stream/bed-areas | params: camera_id=%s", "GET", camera_id)
        response = requests.get(url, timeout=2.0)
        if response.status_code == 200:
            return response.json()
        return []
    except Exception as e:
        logger.print("CTRL_MGR", "Get bed areas error: %s", e)
        return []

def get_floor_areas_from_server():
    """Get floor areas from streaming server"""
    try:
        camera_id = get_current_camera_id()
        STREAMING_HTTP_URL = _get_streaming_http_url()
        url = f"{STREAMING_HTTP_URL}/api/stream/floor-areas?camera_id={camera_id}"
        logger.print("API_REQUEST", "%s | endpoint: /api/stream/floor-areas | params: camera_id=%s", "GET", camera_id)
        response = requests.get(url, timeout=2.0)
        if response.status_code == 200:
            return response.json()
        return []
    except Exception as e:
        logger.print("CTRL_MGR", "Get floor areas error: %s", e)
        return []

def get_chair_areas_from_server():
    """Get chair areas from streaming server"""
    try:
        camera_id = get_current_camera_id()
        STREAMING_HTTP_URL = _get_streaming_http_url()
        url = f"{STREAMING_HTTP_URL}/api/stream/chair-areas?camera_id={camera_id}"
        logger.print("API_REQUEST", "%s | endpoint: /api/stream/chair-areas | params: camera_id=%s", "GET", camera_id)
        response = requests.get(url, timeout=2.0)
        if response.status_code == 200:
            return response.json()
        return []
    except Exception as e:
        logger.print("CTRL_MGR", "Get chair areas error: %s", e)
        return []

def get_couch_areas_from_server():
    """Get couch areas from streaming server"""
    try:
        camera_id = get_current_camera_id()
        STREAMING_HTTP_URL = _get_streaming_http_url()
        url = f"{STREAMING_HTTP_URL}/api/stream/couch-areas?camera_id={camera_id}"
        logger.print("API_REQUEST", "%s | endpoint: /api/stream/couch-areas | params: camera_id=%s", "GET", camera_id)
        response = requests.get(url, timeout=2.0)
        if response.status_code == 200:
            return response.json()
        return []
    except Exception as e:
        logger.print("CTRL_MGR", "Get couch areas error: %s", e)
        return []

def get_bench_areas_from_server():
    """Get bench areas from streaming server"""
    try:
        camera_id = get_current_camera_id()
        STREAMING_HTTP_URL = _get_streaming_http_url()
        url = f"{STREAMING_HTTP_URL}/api/stream/bench-areas?camera_id={camera_id}"
        logger.print("API_REQUEST", "%s | endpoint: /api/stream/bench-areas | params: camera_id=%s", "GET", camera_id)
        response = requests.get(url, timeout=2.0)
        if response.status_code == 200:
            return response.json()
        return []
    except Exception as e:
        logger.print("CTRL_MGR", "Get bench areas error: %s", e)
        return []

def report_state(rtmp_connected=False, is_recording=False):
    """Report camera state to streaming server (async)
    
    Note: This is called from StateReporterWorker which already runs in a separate thread,
    but we make the request itself async to avoid blocking the worker thread.
    """
    def _send():
        try:
            camera_id = get_current_camera_id()
            STREAMING_HTTP_URL = _get_streaming_http_url()
            state_report = {
                "camera_id": camera_id,
                "status": "online",
                "timestamp": int(time.time() * 1000),
                "is_recording": is_recording
            }
            url = f"{STREAMING_HTTP_URL}/api/stream/report-state"
            logger.print("API_REQUEST", "%s | endpoint: /api/stream/report-state | payload: %s", "POST", str(state_report)[:100])
            requests.post(
                url,
                json=state_report,
                headers={'Content-Type': 'application/json'},
                timeout=2.0
            )
        except Exception as e:
            logger.print("CTRL_MGR", "State report error: %s", e)
    
    # Run in background thread for true async behavior
    thread = threading.Thread(target=_send, daemon=True)
    thread.start()
    return True

