# config.py - Server configuration, camera identity, and constants

import os
import json
import time
import socket
import requests

# Import CameraStateManager from control_manager for proper state management
# This must be done early to ensure singleton pattern works correctly
from control_manager import camera_state_manager

# ============================================
# SERVER CONFIGURATION
# ============================================
STREAMING_SERVER_IP = "103.150.93.198"
STREAMING_SERVER_PORT = 8000
STREAMING_HTTP_URL = f"http://{STREAMING_SERVER_IP}:{STREAMING_SERVER_PORT}"
# OBSOLETE: RTMP functionality has been removed
# RTMP_SERVER_URL = f"rtmp://{STREAMING_SERVER_IP}:1935"

# ============================================
# CAMERA IDENTITY
# ============================================
CAMERA_INFO_FILE = "/root/camera_info.json"
CAMERA_ID = None
CAMERA_NAME = None

def get_local_ip():
    """Get local IP address"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception as e:
        print(f"Error getting local IP: {e}")
        return "unknown"

def load_camera_info():
    """Load camera ID and name from local file"""
    try:
        if os.path.exists(CAMERA_INFO_FILE):
            with open(CAMERA_INFO_FILE, 'r') as f:
                data = json.load(f)
                camera_id = data.get("camera_id")
                camera_name = data.get("camera_name")
                registration_status = data.get("status", "unregistered")
                ip_address = data.get("ip_address", "")
                last_registration = data.get("last_registration", 0)
                
                if camera_id and camera_name:
                    print(f"Loaded camera info: {camera_name} ({camera_id}) - Status: {registration_status}")
                    return camera_id, camera_name, registration_status, ip_address, last_registration
    except Exception as e:
        print(f"Error loading camera info: {e}")
    
    return None, None, "unregistered", "", 0

def save_camera_info(camera_id, camera_name, registration_status, ip_address=""):
    """Save camera ID and name to local file"""
    try:
        data = {
            "camera_id": camera_id,
            "camera_name": camera_name,
            "status": registration_status,
            "ip_address": ip_address,
            "last_registration": int(time.time() * 1000),
            "saved_at": int(time.time() * 1000),
            "saved_locally": True
        }
        
        with open(CAMERA_INFO_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"Camera info saved: {camera_name} ({camera_id}) - Status: {registration_status}")
        return True
    except Exception as e:
        print(f"Error saving camera info: {e}")
        return False

def register_with_streaming_server(server_ip, existing_camera_id=None):
    """Register camera with streaming server (now handles all camera management)"""
    try:
        # First get our local IP
        local_ip = get_local_ip()
        if local_ip == "unknown":
            print("❌ Cannot determine local IP address")
            return "camera_000", "Unnamed Camera", "unregistered", local_ip
        
        url = f"http://{server_ip}:{STREAMING_SERVER_PORT}/api/stream/register"
        
        params = {}
        if existing_camera_id:
            params["camera_id"] = existing_camera_id

        print(f"Registering with streaming server: {url} params={params} from IP: {local_ip}")

        response = requests.post(
            url,
            params=params,
            timeout=5.0
        )

        if response.status_code != 200:
            print(f"❌ Registration failed: HTTP {response.status_code}")
            return "camera_000", "Unnamed Camera", "unregistered", local_ip

        result = response.json()

        status = result.get("status", "unknown")
        camera_id = result.get("camera_id", "camera_000")
        
        if status == "registered":
            camera_name = result.get("camera_name", f"Camera {camera_id.split('_')[-1]}")
            print(f"✅ Camera registered: {camera_name} ({camera_id})")
        elif status == "pending":
            camera_name = f"Camera {camera_id.split('_')[-1]}"
            print(f"⏳ Camera pending approval: {camera_id}")
            print(f"   Please approve registration on the web dashboard")
        else:
            camera_name = f"Camera {camera_id.split('_')[-1]}"
            print(f"⚠️ Unknown status: {status}")

        return camera_id, camera_name, status, local_ip

    except Exception as e:
        print(f"❌ Registration error: {e}")
        local_ip = get_local_ip()
        return "camera_000", "Unnamed Camera", "unregistered", local_ip

def check_registration_status(server_ip, camera_id, local_ip):
    """Check if camera is already registered with server"""
    try:
        url = f"http://{server_ip}:{STREAMING_SERVER_PORT}/api/stream/registered"
        
        print(f"Checking registration status on streaming server...")
        
        response = requests.get(
            url,
            timeout=3.0
        )

        if response.status_code == 200:
            result = response.json()
            cameras = result.get("cameras", {})
            
            # Check if our camera ID is in the registered list
            if camera_id in cameras:
                camera_data = cameras[camera_id]
                if camera_data.get("ip_address") == local_ip:
                    print(f"✅ Camera {camera_id} is registered on server")
                    return "registered"
                else:
                    print(f"⚠️ Camera ID {camera_id} exists but IP doesn't match")
                    return "unregistered"
            else:
                print(f"ℹ️ Camera {camera_id} not found in server registry")
                return "unregistered"
        else:
            print(f"❌ Failed to check registration: HTTP {response.status_code}")
            return "unknown"
            
    except Exception as e:
        print(f"❌ Registration check error: {e}")
        return "unknown"

def initialize_camera():
    global CAMERA_ID, CAMERA_NAME
    """Initialize camera by loading existing info or registering new"""
    # Load or register camera
    CAMERA_ID, CAMERA_NAME, registration_status, saved_ip, last_reg = load_camera_info()
    local_ip = get_local_ip()

    print(f"=== Camera Initialization ===")
    print(f"Loaded from storage: {CAMERA_NAME} ({CAMERA_ID}) - Status: {registration_status}")
    print(f"Local IP: {local_ip}")
    print(f"Streaming Server: {STREAMING_HTTP_URL}")

    # Always check registration status with server on startup
    if CAMERA_ID and CAMERA_ID != "camera_000":
        server_status = check_registration_status(STREAMING_SERVER_IP, CAMERA_ID, local_ip)
        
        if server_status == "registered":
            # Camera is registered on server
            registration_status = "registered"
            print(f"✅ Confirmed: Camera is registered on server")
        elif server_status == "unregistered":
            # Need to re-register
            print(f"⚠️ Camera not registered on server, attempting registration...")
            CAMERA_ID, CAMERA_NAME, registration_status, local_ip = register_with_streaming_server(
                STREAMING_SERVER_IP, 
                existing_camera_id=CAMERA_ID
            )
            # Save the new registration info
            save_camera_info(CAMERA_ID, CAMERA_NAME, registration_status, local_ip)
        else:
            # Server unavailable, use saved status
            print(f"⚠️ Cannot reach server, using saved status: {registration_status}")
    else:
        # First time registration
        print(f"🔄 First time registration...")
        CAMERA_ID, CAMERA_NAME, registration_status, local_ip = register_with_streaming_server(
            STREAMING_SERVER_IP
        )
        save_camera_info(CAMERA_ID, CAMERA_NAME, registration_status, local_ip)

    print(f"=== Final Camera Status ===")
    print(f"Camera: {CAMERA_NAME} ({CAMERA_ID})")
    print(f"Status: {registration_status}")
    print(f"Local IP: {local_ip}")

    # Update CameraStateManager with the final state
    camera_state_manager.set_camera_id(CAMERA_ID, notify=False)
    camera_state_manager.set_camera_name(CAMERA_NAME)
    camera_state_manager.set_registration_status(registration_status, notify=False)
    camera_state_manager.set_local_ip(local_ip)

    return CAMERA_ID, CAMERA_NAME, registration_status, local_ip

# ============================================
# RECORDING PARAMETERS
# ============================================
MIN_HUMAN_FRAMES_TO_START = 3
NO_HUMAN_FRAMES_TO_STOP = 30  # Will be overridden by NO_HUMAN_SECONDS_TO_STOP
NO_HUMAN_SECONDS_TO_STOP = 5  # 5 seconds at 60fps = 300 frames before confirming no person
MAX_RECORDING_DURATION_MS = 90000

# Background update settings
UPDATE_INTERVAL_MS = 10000
NO_HUMAN_CONFIRM_FRAMES = 10
STEP = 8

# ============================================
# ASYNC WORKER SETTINGS
# ============================================
FLAG_SYNC_INTERVAL_MS = 1000
SAFE_AREA_SYNC_INTERVAL_MS = 5000
STATE_REPORT_INTERVAL_MS = 30000
FRAME_UPLOAD_INTERVAL_MS = 500

# ============================================
# LOCAL FILE PATHS
# ============================================
LOCAL_FLAGS_FILE = "/root/control_flags.json"
SAFE_AREA_FILE = "/root/safe_areas.json"
BACKGROUND_PATH = "/root/static/background.jpg"
LOCAL_PORT = 8080

# Garbage Collection
GC_INTERVAL_MS = 30000

# Pose Analysis
POSE_ANALYSIS_INTERVAL_MS = 50