# main.py

from maix import camera, display, app, nn, image, time, tracker
from pose.pose_estimation import PoseEstimation
from tools.wifi_connect import connect_wifi
from tools.video_record import VideoRecorder
from tools.time_utils import get_timestamp_str, time_ms
from tools.skeleton_saver import SkeletonSaver2D
from pose.judge_fall import get_fall_info, FALL_COUNT_THRES
from tools.safe_area import BodySafetyChecker, CheckMethod
from debug_config import debug_print, log_pose_data, log_fall_detection
import queue
import numpy as np
import os
import json
import requests
import gc
import socket
import threading
import select
import time

# Async Worker Classes for Non-Blocking Operations
class FlagSyncWorker(threading.Thread):
    """Background thread for syncing flags from analytics server"""
    
    def __init__(self, flags_queue, analytics_url, camera_id, sync_interval_ms=500):
        super().__init__(daemon=True)
        self.flags_queue = flags_queue
        self.analytics_url = analytics_url
        self.camera_id = camera_id
        self.sync_interval = sync_interval_ms / 1000.0  # Convert to seconds
        self.running = True
        self.connection_errors = 0
        self.max_errors = 10
        
    def run(self):
        while self.running:
            try:
                # Check server connection and get flags
                response = requests.get(
                    f"{self.analytics_url}/camera_state?camera_id={self.camera_id}",
                    timeout=1.0
                )
                
                if response.status_code == 200:
                    flags = response.json()
                    # Put flags in queue for main thread to consume
                    self.flags_queue.put(("flags_update", flags))
                    self.connection_errors = 0  # Reset error count on success
                else:
                    self.connection_errors += 1
                    
            except Exception as e:
                self.connection_errors += 1
                if self.connection_errors % 5 == 0:  # Log every 5th error
                    print(f"[FlagSync] Connection error ({self.connection_errors}): {e}")
            
            # Sleep for sync interval
            time.sleep(self.sync_interval)
    
    def stop(self):
        self.running = False


class FrameSenderWorker(threading.Thread):
    """Background thread for sending frames to analytics server"""
    
    def __init__(self, frame_queue, analytics_url, camera_id, send_interval_ms=200):
        super().__init__(daemon=True)
        self.frame_queue = frame_queue
        self.analytics_url = analytics_url
        self.camera_id = camera_id
        self.send_interval = send_interval_ms / 1000.0  # Convert to seconds
        self.running = True
        self.connection_errors = 0
        self.max_errors = 10
        
    def run(self):
        last_send_time = 0
        
        while self.running:
            try:
                current_time = time.time()
                
                # Only send if enough time has passed
                if current_time - last_send_time >= self.send_interval:
                    # Get frame from queue (non-blocking with timeout)
                    try:
                        img = self.frame_queue.get(timeout=0.1)
                        
                        # Convert image to JPEG and send
                        jpeg_img = img.to_format(image.Format.FMT_JPEG)
                        jpeg_bytes = jpeg_img.to_bytes()
                        
                        response = requests.post(
                            f"{self.analytics_url}/upload_frame",
                            data=jpeg_bytes,
                            headers={
                                'Content-Type': 'image/jpeg', 
                                'X-Camera-ID': self.camera_id
                            },
                            timeout=1.0
                        )
                        
                        if response.status_code == 200:
                            self.connection_errors = 0  # Reset error count on success
                            last_send_time = current_time
                        else:
                            self.connection_errors += 1
                            
                        # Mark task as done
                        self.frame_queue.task_done()
                        
                    except queue.Empty:
                        # No frame available, continue
                        pass
                    except Exception as e:
                        self.connection_errors += 1
                        if self.connection_errors % 5 == 0:
                            print(f"[FrameSender] Send error ({self.connection_errors}): {e}")
                else:
                    # Sleep briefly to avoid busy waiting
                    time.sleep(0.01)
                    
            except Exception as e:
                print(f"[FrameSender] Unexpected error: {e}")
                time.sleep(0.1)
    
    def stop(self):
        self.running = False


class StateReporterWorker(threading.Thread):
    """Background thread for reporting camera state to analytics server"""
    
    def __init__(self, state_queue, analytics_url, camera_id, report_interval_ms=30000):
        super().__init__(daemon=True)
        self.state_queue = state_queue
        self.analytics_url = analytics_url
        self.camera_id = camera_id
        self.report_interval = report_interval_ms / 1000.0  # Convert to seconds
        self.running = True
        
    def run(self):
        while self.running:
            try:
                # Get state data from queue
                try:
                    state_data = self.state_queue.get(timeout=1.0)
                    
                    response = requests.post(
                        f"{self.analytics_url}/camera_state",
                        json=state_data,
                        timeout=2.0
                    )
                    
                    if response.status_code == 200:
                        print(f"[StateReporter] State reported successfully")
                    else:
                        print(f"[StateReporter] Failed: HTTP {response.status_code}")
                        
                    self.state_queue.task_done()
                    
                except queue.Empty:
                    # No state data to report, continue
                    pass
                    
            except Exception as e:
                print(f"[StateReporter] Error: {e}")
            
            # Sleep for report interval
            time.sleep(self.report_interval)
    
    def stop(self):
        self.running = False


# Image Paths
BACKGROUND_PATH = "/root/static/background.jpg"
SAFE_AREA_FILE = "/root/safe_areas.json"
STATE_FILE = "/root/camera_state.json"
LOCAL_FLAGS_FILE = "/root/control_flags.json"
CAMERA_INFO_FILE = "/root/camera_info.json"

# Camera Identity
def get_mac_address():
    """Get MAC address of the device"""
    try:
        import ubinascii
        import network
        wlan = network.WLAN()
        mac = wlan.config('mac')
        return ubinascii.hexlify(mac).decode()
    except:
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
                
                if camera_id and camera_name:
                    print(f"Loaded camera info: {camera_name} ({camera_id}) - Status: {registration_status}")
                    return camera_id, camera_name, registration_status
    except Exception as e:
        print(f"Error loading camera info: {e}")
    
    return None, None, "unregistered"

def save_camera_info(camera_id, camera_name, registration_status):
    # if registration failed (indicated by default IDs)
    if camera_id in ["camera_000", "maixcam_000"]:
        print("Registration failed, not saving camera info.")
        return False

    """Save camera ID and name to local file"""
    try:
        data = {
            "camera_id": camera_id,
            "camera_name": camera_name,
            "status": registration_status,
            "saved_at": time_ms(),
            "saved_locally": True
        }
        
        with open(CAMERA_INFO_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"Camera info saved: {camera_name} ({camera_id}) - Status: {registration_status}")
        return True
    except Exception as e:
        print(f"Error saving camera info: {e}")
        return False

def register_with_analytics(server_ip, existing_camera_id=None, server_port=8000):
    """Register camera with analytics server and get ID - IMPROVED with waiting"""
    try:
        # Request registration with existing camera_id if we have one
        url = f"http://{server_ip}:{server_port}/register_camera"
        
        # Always send camera_id if we have one (even if it's a default one)
        if existing_camera_id:
            url += f"?camera_id={existing_camera_id}"
        
        print(f"Registering with analytics server: {url}")
        response = requests.get(url, timeout=5.0)
        
        if response.status_code == 200:
            result = response.json()
            print(f"Registration response: {result}")
            status = result.get("status")
            
            if status == "registered" or status == "already_registered":
                # Camera already registered
                camera_id = result.get("camera_id")
                camera_name = result.get("camera_name", f"Camera {camera_id.split('_')[-1]}")
                print(f"✅ Camera already registered: {camera_name} ({camera_id})")
                return camera_id, camera_name, "registered"
            
            elif status == "pending" or status == "pending_approval":
                # Registration pending user approval
                camera_id = result.get("camera_id")
                print(f"⏳ Registration pending approval. Camera ID: {camera_id}")
                print("Please visit the analytics dashboard to name this camera.")
                
                # Wait for approval with timeout
                start_time = time_ms()
                timeout_ms = 300000  # 5 minutes
                poll_interval_ms = 10000  # Check every 10 seconds
                
                while time_ms() - start_time < timeout_ms:
                    print(f"Waiting for approval... ({((time_ms() - start_time) // 1000)}s elapsed)")
                    time.sleep_ms(poll_interval_ms)
                    
                    # Try to get registration status
                    try:
                        # Check camera registry to see if we've been approved
                        registry_url = f"http://{server_ip}:{server_port}/camera_registry"
                        registry_response = requests.get(registry_url, timeout=2.0)
                        
                        if registry_response.status_code == 200:
                            registry_data = registry_response.json()
                            cameras = registry_data.get("cameras", {})
                            
                            # Check if our camera_id is now registered
                            if camera_id in cameras:
                                camera_data = cameras[camera_id]
                                approved_camera_name = camera_data.get("name", f"Camera {camera_id.split('_')[-1]}")
                                print(f"✅ Camera approved: {approved_camera_name} ({camera_id})")
                                return camera_id, approved_camera_name, "registered"
                            
                            # Also check pending registrations
                            pending_url = f"http://{server_ip}:{server_port}/pending_registrations"
                            pending_response = requests.get(pending_url, timeout=2.0)
                            
                            if pending_response.status_code == 200:
                                pending_data = pending_response.json()
                                pending_list = pending_data.get("pending", [])
                                
                                # If we're no longer in pending, we might have been approved
                                still_pending = any(reg.get("camera_id") == camera_id for reg in pending_list)
                                
                                if not still_pending:
                                    # Check registry again to be sure
                                    registry_response2 = requests.get(registry_url, timeout=2.0)
                                    if registry_response2.status_code == 200:
                                        registry_data2 = registry_response2.json()
                                        cameras2 = registry_data2.get("cameras", {})
                                        
                                        if camera_id in cameras2:
                                            camera_data = cameras2[camera_id]
                                            approved_camera_name = camera_data.get("name", f"Camera {camera_id.split('_')[-1]}")
                                            print(f"✅ Camera approved: {approved_camera_name} ({camera_id})")
                                            
                                            # Save camera info immediately
                                            save_camera_info(CAMERA_ID, CAMERA_NAME, registration_status)
                                            
                                            # Force immediate frame upload to appear connected
                                            print("Camera approved - starting immediate frame upload...")
                                            
                                            return camera_id, approved_camera_name, "registered"
                                        else:
                                            # We're not pending but not registered either - something went wrong
                                            print(f"⚠️ Camera {camera_id} no longer pending but not registered")
                                            return camera_id, "Pending Camera", "pending"
                    
                    except Exception as poll_error:
                        print(f"Polling error: {poll_error}")
                        # Continue waiting despite polling errors
                
                # Timeout reached
                print(f"⏰ Registration timeout after {timeout_ms // 1000} seconds")
                print(f"Using pending camera ID: {camera_id}")
                return camera_id, "Pending Camera", "pending"
            
            else:
                print(f"⚠️ Unexpected registration status: {status}")
                camera_id = result.get("camera_id", "camera_000")
                return camera_id, "Pending Camera", status
        else:
            print(f"❌ Registration failed: HTTP {response.status_code}")
            return "camera_000", "Unnamed Camera", "unregistered"
            
    except Exception as e:
        print(f"❌ Registration error: {e}")
        import traceback
        traceback.print_exc()
        return "camera_000", "Unnamed Camera", "unregistered"


# Analytics Server Setup
ANALYTICS_SERVER_IP = "103.150.93.198"
CAMERA_ID, CAMERA_NAME, registration_status = load_camera_info()
ANALYTICS_HTTP_URL = f"http://{ANALYTICS_SERVER_IP}:8000"

print(f"Current camera info: ID={CAMERA_ID}, Name={CAMERA_NAME}, Status={registration_status}")
print(f"Current camera info: ID={CAMERA_ID}, Name={CAMERA_NAME}, Status={registration_status}")
print(f"Current camera info: ID={CAMERA_ID}, Name={CAMERA_NAME}, Status={registration_status}")
print(f"Current camera info: ID={CAMERA_ID}, Name={CAMERA_NAME}, Status={registration_status}")
print(f"Current camera info: ID={CAMERA_ID}, Name={CAMERA_NAME}, Status={registration_status}")

# Use existing camera_id if we have one, otherwise use None
if registration_status != "registered":
    CAMERA_ID, CAMERA_NAME, registration_status = register_with_analytics(
        ANALYTICS_SERVER_IP, 
        existing_camera_id=CAMERA_ID if CAMERA_ID and CAMERA_ID not in ["camera_000", "maixcam_000"] else None
    )

# Local server for receiving commands (runs on MaixCAM)
LOCAL_PORT = 8080

# Connect to WiFi
server_ip = connect_wifi("MaixCAM-Wifi", "maixcamwifi")
print(f"Camera IP: {server_ip}")
print(f"Analytics server: {ANALYTICS_HTTP_URL}")

# Ensure directories exist
os.makedirs("/root/static", exist_ok=True)
os.makedirs("/root/recordings", exist_ok=True)

# Initialize detectors
detector = nn.YOLO11(model="/root/models/yolo11n_pose.mud", dual_buff=True)
segmentor = nn.YOLO11(model="/root/models/yolo11n_seg.mud", dual_buff=True)

cam = camera.Camera(detector.input_width(), detector.input_height(), detector.input_format(), fps=60)
disp = display.Display()

pose_estimator = PoseEstimation()  # Only used in local mode

image.load_font("sourcehansans", "/maixapp/share/font/SourceHanSansCN-Regular.otf", size=32)
image.set_default_font("sourcehansans")

# Skeleton Saver and Recorder setup
recorder = VideoRecorder()
skeleton_saver_2d = SkeletonSaver2D()
frame_id = 0

# Recording parameters
MIN_HUMAN_FRAMES_TO_START = 3
NO_HUMAN_FRAMES_TO_STOP = 30
MAX_RECORDING_DURATION_MS = 90000

# Recording state variables
human_presence_history = []
recording_start_time = 0
is_recording = False

# Background update settings
UPDATE_INTERVAL_MS = 10000
NO_HUMAN_CONFIRM_FRAMES = 10
STEP = 8

# Background state variables
background_img = None
prev_human_present = False
no_human_counter = 0
last_update_ms = time_ms()

# Initialize Body Safety Checker
safety_checker = BodySafetyChecker()
SAFETY_CHECK_METHOD = CheckMethod.TORSO_HEAD

# Camera control flags
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

# Frame sending
FRAME_SEND_INTERVAL_MS = 200
last_frame_sent_time = time_ms()

# State reporting (informational only, doesn't update flags) - now handled by async worker
STATE_REPORT_INTERVAL_MS = 30000
last_state_report = time_ms()

# Flag syncing from analytics (read-only, every 500ms) - now handled by async worker
FLAG_SYNC_INTERVAL_MS = 500
flags_initialized = False

# Connection management
connection_error_count = 0
MAX_CONNECTION_ERRORS = 10

# Analytics server connection status
analytics_server_available = False
CONNECTION_RETRY_INTERVAL_MS = 10000
last_connection_check = 0

# Async queues for non-blocking operations
flags_queue = queue.Queue(maxsize=10)
frame_queue = queue.Queue(maxsize=5)
state_queue = queue.Queue(maxsize=3)

# Worker threads
flag_sync_worker = None
frame_sender_worker = None
state_reporter_worker = None

# Command server for receiving commands from analytics
command_server_running = True
received_commands = []
commands_lock = threading.Lock()

def save_control_flags():
    """Save control flags to local storage"""
    try:
        flags_data = {
            "control_flags": control_flags,
            "timestamp": time_ms(),
            "camera_id": CAMERA_ID,
            "saved_locally": True
        }
        
        with open(LOCAL_FLAGS_FILE, 'w') as f:
            json.dump(flags_data, f, indent=2)
        print(f"Control flags saved locally to {LOCAL_FLAGS_FILE}")
        
    except Exception as e:
        print(f"Error saving control flags: {e}")

def load_initial_flags():
    """Load initial flags from local storage only if server is unavailable"""
    try:
        if os.path.exists(LOCAL_FLAGS_FILE):
            with open(LOCAL_FLAGS_FILE, 'r') as f:
                data = json.load(f)
                if "control_flags" in data:
                    for key in control_flags.keys():
                        if key in data["control_flags"]:
                            control_flags[key] = data["control_flags"][key]
                    print(f"Loaded initial flags from local storage")
                    return True
    except Exception as e:
        print(f"Error loading initial flags from local file: {e}")
    
    return False

def check_server_connection():
    """Check if analytics server is reachable"""
    global analytics_server_available
    
    try:
        response = requests.get(f"{ANALYTICS_HTTP_URL}/", timeout=2)
        if response.status_code == 200:
            analytics_server_available = True
            connection_error_count = 0
            print(f"Analytics server connection successful: {response.status_code}")
            return True
        else:
            analytics_server_available = False
            print(f"Analytics server responded with non-200: {response.status_code}")
            return False
    except Exception as e:
        analytics_server_available = False
        print(f"Analytics server connection failed: {e}")
        return False

def send_frame_simple(img):
    """Simple frame upload to analytics server"""
    global connection_error_count
    
    try:
        jpeg_img = img.to_format(image.Format.FMT_JPEG)
        jpeg_bytes = jpeg_img.to_bytes()
        
        response = requests.post(
            f"{ANALYTICS_HTTP_URL}/upload_frame",
            data=jpeg_bytes,
            headers={
                'Content-Type': 'image/jpeg', 
                'X-Camera-ID': CAMERA_ID
            },
            timeout=1.0
        )
        
        if response.status_code == 200:
            connection_error_count = 0
            return True
        else:
            print(f"Frame upload failed: HTTP {response.status_code}")
            connection_error_count += 1
            return False
        
    except Exception as e:
        connection_error_count += 1
        if connection_error_count % 10 == 0:
            print(f"Frame upload error ({connection_error_count}): {e}")
        return False

def report_camera_state():
    """Report state to analytics server (informational only, does NOT update control_flags)"""
    if not analytics_server_available:
        return False
    
    # Report state but note that control_flags are informational only
    # Analytics server will preserve flags from web UI, not update from camera
    state = {
        "camera_id": CAMERA_ID,
        "camera_name": CAMERA_NAME,
        "control_flags": control_flags,  # Informational only, server won't use this to update flags
        "safe_areas": safety_checker.safe_polygons,
        "ip_address": server_ip,
        "timestamp": time_ms()
    }
    
    try:
        response = requests.post(
            f"{ANALYTICS_HTTP_URL}/camera_state",
            json=state,
            timeout=2.0
        )
        if response.status_code == 200:
            return True
        else:
            print(f"State report failed: HTTP {response.status_code}")
            return False
    except Exception as e:
        print(f"Error reporting state: {e}")
        return False

def send_frame_to_analytics(img):
    """Send frame to analytics server if connected"""
    global last_frame_sent_time
    
    if not analytics_server_available or not control_flags.get("analytics_mode", True):
        return False
    
    current_time = time_ms()
    time_since_last = current_time - last_frame_sent_time

    if time_since_last >= FRAME_SEND_INTERVAL_MS:
        success = send_frame_simple(img)
        last_frame_sent_time = current_time
        return success
    
    return True

def get_pose_analysis_from_analytics():
    """Get pose analysis from analytics server"""
    if not analytics_server_available or not control_flags.get("analytics_mode", True):
        return None
    
    try:
        response = requests.get(
            f"{ANALYTICS_HTTP_URL}/pose_analysis?camera_id={CAMERA_ID}",
            timeout=2.0
        )
        
        if response.status_code == 200:
            analysis_data = response.json()
            
            # Check if we have valid data
            if analysis_data.get("status") == "no_data" or analysis_data.get("status") == "no_pose_data":
                return None
                
            return analysis_data
        else:
            if response.status_code != 200:
                try:
                    error_body = response.text
                except:
                    pass
            return None
            
    except Exception as e:
        return None

def send_data_via_http(data_type, data):
    """Send JSON data to analytics server if connected"""
    if not analytics_server_available or not control_flags.get("analytics_mode", True):
        return False
    
    try:
        url = f"{ANALYTICS_HTTP_URL}/upload_data"
        payload = {
            "type": data_type,
            "camera_id": CAMERA_ID,
            "timestamp": time_ms(),
            "data": data
        }
        
        response = requests.post(
            url,
            json=payload,
            headers={'Content-Type': 'application/json'},
            timeout=2.0
        )
        
        if response.status_code == 200:
            return True
        else:
            print(f"Data upload failed: HTTP {response.status_code}")
            return False
            
    except Exception as e:
        print(f"HTTP data upload error: {e}")
        return False

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

def update_safety_checker_polygons(safe_areas=None):
    """Update the safety checker with safe areas"""
    try:
        if safe_areas is None:
            safe_areas = load_safe_areas()
        
        safety_checker.clear_safe_polygons()
        for polygon in safe_areas:
            safety_checker.add_safe_polygon(polygon)
        print(f"Updated safety checker with {len(safe_areas)} polygon(s)")
        
        with open(SAFE_AREA_FILE, 'w') as f:
            json.dump(safe_areas, f)
            
        if analytics_server_available and control_flags.get("analytics_mode", True):
            report_camera_state()
            
    except Exception as e:
        print(f"Error updating safety checker: {e}")

def handle_command(command, value):
    """Handle command from analytics server or local input"""
    print(f"Processing command: {command} = {value}")
    
    # Only handle non-flag commands locally
    if command == "set_background":
        if value:
            print("Background update requested")
            # This will be handled in the main loop
    elif command == "update_safe_areas":
        if isinstance(value, list):
            update_safety_checker_polygons(value)
    # Flag commands are ignored here - camera will poll for them from analytics

def start_new_recording():
    global frame_id, recording_start_time, is_recording
    
    timestamp = get_timestamp_str()
    video_path = os.path.join("/root/recordings", f"{timestamp}.mp4")
    recorder.start(video_path, detector.input_width(), detector.input_height())
    skeleton_saver_2d.start_new_log(timestamp)
    frame_id = 0
    recording_start_time = time_ms()
    is_recording = True
    
    if analytics_server_available and control_flags.get("analytics_mode", True):
        send_data_via_http("recording_started", {"timestamp": timestamp})
    
    print(f"Started recording: {timestamp}")
    return recording_start_time

def stop_recording():
    global is_recording
    
    if recorder.is_active:
        recorder.end()
        skeleton_saver_2d.save_to_csv()
        is_recording = False
        
        if analytics_server_available and control_flags.get("analytics_mode", True):
            send_data_via_http("recording_stopped", {})
        
        print("Stopped recording")

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

# Background update functions
def rects_overlap(x1, y1, w1, h1, x2, y2, w2, h2):
    return not (x1 + w1 <= x2 or x2 + w2 <= x1 or y1 + h1 <= y2 or y2 + h2 <= y1)

def update_background(bg, current, objs):
    width, height = current.width(), current.height()
    human_boxes = []
    
    for obj in objs:
        if segmentor.labels[obj.class_id] in ["person", "human"]:
            human_boxes.append((obj.x, obj.y, obj.w, obj.h))

    for y in range(0, height, STEP):
        for x in range(0, width, STEP):
            overlaps = False
            for bx, by, bw, bh in human_boxes:
                if rects_overlap(x, y, STEP, STEP, bx, by, bw, bh):
                    overlaps = True
                    break

            if not overlaps:
                w = min(STEP, width - x)
                h = min(STEP, height - y)
                region = current.crop(x, y, w, h)
                bg.draw_image(x, y, region)

    return bg

class CommandServer(threading.Thread):
    """Simple HTTP server to receive commands from analytics"""
    
    def __init__(self):
        super().__init__(daemon=True)
        self.running = True
        self.sock = None
        
    def run(self):
        """Run command server"""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        try:
            self.sock.bind(('0.0.0.0', LOCAL_PORT))
            self.sock.listen(5)
            self.sock.settimeout(0.5)
            print(f"Command server listening on port {LOCAL_PORT}")
            
            while self.running:
                try:
                    readable, _, _ = select.select([self.sock], [], [], 0.5)
                    if readable:
                        conn, addr = self.sock.accept()
                        conn.settimeout(1.0)
                        
                        client_thread = threading.Thread(
                            target=self.handle_client, 
                            args=(conn, addr),
                            daemon=True
                        )
                        client_thread.start()
                        
                except socket.timeout:
                    continue
                except Exception as e:
                    print(f"Command server accept error: {e}")
                    time.sleep_ms(100)
                    
        except Exception as e:
            print(f"Failed to start command server: {e}")
        finally:
            if self.sock:
                self.sock.close()
    
    def handle_client(self, conn, addr):
        """Handle client connection"""
        try:
            request = b""
            try:
                while True:
                    chunk = conn.recv(1024)
                    if not chunk:
                        break
                    request += chunk
                    if b"\r\n\r\n" in request:
                        break
            except socket.timeout:
                pass
            
            if request:
                try:
                    request_str = request.decode('utf-8', errors='ignore')
                    
                    if "POST /command" in request_str:
                        body_start = request_str.find("\r\n\r\n")
                        if body_start != -1:
                            body = request_str[body_start + 4:]
                            if body:
                                data = json.loads(body.strip())
                                with commands_lock:
                                    received_commands.append(data)
                                
                                response = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n"
                                response += json.dumps({"status": "success"})
                                conn.send(response.encode())
                                print(f"Received command from {addr[0]}: {data.get('command')}")
                    else:
                        response = "HTTP/1.1 404 Not Found\r\n\r\n"
                        conn.send(response.encode())
                        
                except Exception as e:
                    print(f"Command parsing error: {e}")
                    response = "HTTP/1.1 400 Bad Request\r\n\r\nError"
                    conn.send(response.encode())
            
            conn.close()
            
        except Exception as e:
            print(f"Client handling error: {e}")
            try:
                conn.close()
            except:
                pass
    
    def stop(self):
        """Stop the server"""
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except:
                pass

# Tracker and fall detection setup
fallParam = {
    "v_bbox_y": 0.43,
    "angle": 70
}
fps = cam.fps()
queue_size = 5

online_targets = {
    "id": [],
    "bbox": [],
    "points": []
}

fall_ids = set()
unsafe_ids = set()

# Tracking params
max_lost_buff_time = 30
track_thresh = 0.4
high_thresh = 0.6
match_thresh = 0.8
max_history_num = 5
valid_class_id = [0]
tracker0 = tracker.ByteTracker(max_lost_buff_time, track_thresh, high_thresh, match_thresh, max_history_num)

def yolo_objs_to_tracker_objs(objs, valid_class_id=[0]):
    out = []
    for obj in objs:
        if obj.class_id in valid_class_id:
            out.append(tracker.Object(obj.x, obj.y, obj.w, obj.h, obj.class_id, obj.score))
    return out

def check_and_fallback_to_local():
    """Check analytics server connection and fall back to local mode if needed"""
    global analytics_server_available, last_connection_check
    
    current_time = time_ms()
    
    if current_time - last_connection_check > CONNECTION_RETRY_INTERVAL_MS:
        was_available = analytics_server_available
        check_server_connection()
        last_connection_check = current_time
        
        if was_available and not analytics_server_available:
            print("Analytics server connection lost, falling back to local analysis mode")
            control_flags["analytics_mode"] = False
        
        elif not was_available and analytics_server_available:
            print("Analytics server connection restored, switching back to analytics mode")
            control_flags["analytics_mode"] = True
    
    return analytics_server_available

def sync_flags_from_server():
    """Get current flags from analytics server (read-only, camera never modifies flags)"""
    global analytics_server_available, flags_initialized
    
    if check_server_connection():
        try:
            response = requests.get(
                f"{ANALYTICS_HTTP_URL}/camera_state?camera_id={CAMERA_ID}",
                timeout=1.0
            )
            
            if response.status_code == 200:
                flags = response.json()
                
                # Update only the control flags that exist in our local dict
                # Camera is read-only - it never modifies flags, only retrieves them
                for key in control_flags.keys():
                    if key in flags:
                        control_flags[key] = flags[key]
                
                flags_initialized = True
                analytics_server_available = True
                return True
            else:
                analytics_server_available = False
                
        except Exception as e:
            analytics_server_available = False
    else:
        analytics_server_available = False
    
    return False



# Initialize background
if os.path.exists(BACKGROUND_PATH):
    background_img = image.load(BACKGROUND_PATH, format=image.Format.FMT_RGB888)
    print("Loaded background image from file")
else:
    background_img = cam.read().copy()
    background_img.save(BACKGROUND_PATH)
    print("Created new background image")

# Load initial control flags and safe areas
print("\n=== Loading Configuration ===")
load_initial_flags()
initial_safe_areas = load_safe_areas()
update_safety_checker_polygons(initial_safe_areas)

# Start command server
command_server = CommandServer()
command_server.start()

# Test connection to analytics server and sync flags
print(f"\n=== Testing Analytics Server Connection ===")
print(f"Analytics server: {ANALYTICS_HTTP_URL}")
print(f"\n=== Starting Camera: {CAMERA_NAME} ===")
print(f"Camera ID: {CAMERA_ID}")
print(f"Camera IP: {server_ip}")

# Initial flag sync and start async workers
if not sync_flags_from_server():
    print("Warning: Cannot connect to analytics server")
    print("Using locally stored configuration")
    control_flags["analytics_mode"] = False
    save_control_flags()
else:
    print("Analytics server connection successful")
    control_flags["analytics_mode"] = True
    
    # Start async workers for non-blocking operations
    print("Starting async workers...")
    flag_sync_worker = FlagSyncWorker(flags_queue, ANALYTICS_HTTP_URL, CAMERA_ID)
    frame_sender_worker = FrameSenderWorker(frame_queue, ANALYTICS_HTTP_URL, CAMERA_ID)
    state_reporter_worker = StateReporterWorker(state_queue, ANALYTICS_HTTP_URL, CAMERA_ID)
    
    flag_sync_worker.start()
    frame_sender_worker.start()
    state_reporter_worker.start()
    
    print("✅ Async workers started successfully")
    
    # Initial sync done, flags will be synced every 500ms in background thread

print(f"\n=== Starting with Configuration ===")
print(f"Analytics Mode: {'ENABLED' if control_flags['analytics_mode'] else 'DISABLED'}")
print(f"Recording: {'ENABLED' if control_flags['record'] else 'DISABLED'}")

print("\nStarting camera stream...")

# === Main loop ===
frame_counter = 0
last_gc_time = time_ms()
GC_INTERVAL_MS = 10000
last_pose_analysis_time = 0
POSE_ANALYSIS_INTERVAL_MS = 500  # Get pose analysis every 500ms

# Store latest pose analysis from server
server_pose_analysis = {}

while not app.need_exit():
    # Performance timing start
    frame_start_time = time_ms()
    
    # 1. Camera Read
    camera_start = time_ms()
    raw_img = cam.read()
    camera_time = time_ms() - camera_start
    
    # Check analytics server connection and handle fallback
    check_and_fallback_to_local()
    
    # Process flag updates from async worker (non-blocking)
    try:
        while not flags_queue.empty():
            msg_type, data = flags_queue.get_nowait()
            if msg_type == "flags_update":
                # Update control flags with server data
                for key in control_flags.keys():
                    if key in data:
                        control_flags[key] = data[key]
                print(f"[ASYNC] Flags synced from server")
    except queue.Empty:
        pass
    
    # Process received commands
    with commands_lock:
        while received_commands:
            cmd_data = received_commands.pop(0)
            handle_command(cmd_data.get("command"), cmd_data.get("value"))
            # Commands are processed via async flag sync worker, no need for manual sync
    
    # Periodically get pose analysis from analytics server
    if control_flags["analytics_mode"]:
        current_time = time_ms()
        if current_time - last_pose_analysis_time > POSE_ANALYSIS_INTERVAL_MS:
            analysis_data = get_pose_analysis_from_analytics()
            if analysis_data:
                server_pose_analysis = analysis_data
            else:
                server_pose_analysis = {}
            last_pose_analysis_time = current_time
    
    # Check for background update request
    if control_flags.get("set_background", False):
        background_img = raw_img.copy()
        background_img.save(BACKGROUND_PATH)
        control_flags["set_background"] = False
        save_control_flags()
        print("Background updated from request")
    
    # 2. Segmentation (background update and human detection)
    seg_start = time_ms()
    objs_seg = segmentor.detect(raw_img, conf_th=0.5, iou_th=0.45)
    current_human_present = any(segmentor.labels[obj.class_id] in ["person", "human"] for obj in objs_seg)
    seg_time = time_ms() - seg_start
    bg_updated = False

    # Background update logic
    if control_flags.get("auto_update_bg", False):
        if prev_human_present and not current_human_present:
            no_human_counter += 1
            if no_human_counter >= NO_HUMAN_CONFIRM_FRAMES:
                background_img = raw_img.copy()
                background_img.save(BACKGROUND_PATH)
                last_update_ms = time_ms()
                no_human_counter = 0
                bg_updated = True
        else:
            no_human_counter = 0
            if time_ms() - last_update_ms > UPDATE_INTERVAL_MS:
                bg_update_start = time_ms()
                background_img = update_background(background_img, raw_img, objs_seg)
                background_img.save(BACKGROUND_PATH)
                bg_update_time = time_ms() - bg_update_start
                print(f"[PERF] Background update: {bg_update_time}ms")
                last_update_ms = time_ms()
                bg_updated = True

        prev_human_present = current_human_present

        if time_ms() - last_update_ms > UPDATE_INTERVAL_MS and not bg_updated:
            bg_update_start = time_ms()
            background_img = update_background(background_img, raw_img, objs_seg)
            background_img.save(BACKGROUND_PATH)
            bg_update_time = time_ms() - bg_update_start
            print(f"[PERF] Background update: {bg_update_time}ms")
            last_update_ms = time_ms()
    else:
        no_human_counter = 0
        prev_human_present = current_human_present

    # Prepare display image
    if control_flags.get("show_raw", False):
        img = raw_img.copy()
    else:
        if background_img is not None:
            img = background_img.copy()
        else:
            img = raw_img.copy()

    # 3. Pose detection and tracking
    pose_start = time_ms()
    objs = detector.detect(raw_img, conf_th=0.5, iou_th=0.45, keypoint_th=0.5)
    pose_time = time_ms() - pose_start
    print(f"[PERF] Pose detection: {pose_time}ms, found {len(objs)} objects")
    
    pose_human_present = len(objs) > 0
    human_present = current_human_present or pose_human_present
    
    human_presence_history.append(human_present)
    if len(human_presence_history) > NO_HUMAN_FRAMES_TO_STOP + 1:
        human_presence_history.pop(0)
    
    # Recording logic
    if control_flags.get("record", False):
        now = time_ms()
        
        if not is_recording:
            if (len(human_presence_history) >= MIN_HUMAN_FRAMES_TO_START and 
                all(human_presence_history[-MIN_HUMAN_FRAMES_TO_START:])):
                start_new_recording()
        
        if is_recording:
            no_human_count = 0
            for presence in reversed(human_presence_history):
                if not presence:
                    no_human_count += 1
                else:
                    break
            
            if (no_human_count >= NO_HUMAN_FRAMES_TO_STOP or 
                now - recording_start_time >= MAX_RECORDING_DURATION_MS):
                stop_recording()
    else:
        if is_recording:
            stop_recording()

    # Process tracking and drawing
    tracking_start = time_ms()
    out_bbox = yolo_objs_to_tracker_objs(objs)
    tracks = tracker0.update(out_bbox)
    tracking_time = time_ms() - tracking_start
    print(f"[PERF] Tracking: {tracking_time}ms, {len(tracks)} tracks")

    if is_recording:
        frame_id += 1

    # Variables to store fall detection results for display
    current_fall_counter = 0
    current_fall_detected = False
    current_fall_algorithm = control_flags.get("fall_algorithm", 3)
    current_torso_angle = None
    current_thigh_uprightness = None

    for track in tracks:
        if track.lost:
            continue
        for tracker_obj in track.history[-1:]:
            for obj in objs:
                if abs(obj.x - tracker_obj.x) < 10 and abs(obj.y - tracker_obj.y) < 10:
                    keypoints_np = to_keypoints_np(obj.points)
                    timestamp = time_ms()

                    # Get pose data based on mode
                    pose_data = None
                    pose_eval_start = time_ms()
                    
                    # Get HME flag from centralized control_flags
                    use_hme = control_flags.get("hme", False)
                    
                    if control_flags["analytics_mode"]:
                        # In analytics mode, send data based on HME setting
                        skeletal_data = {
                            "keypoints": obj.points,
                            "bbox": [tracker_obj.x, tracker_obj.y, tracker_obj.w, tracker_obj.h],
                            "track_id": track.id,
                            "timestamp": timestamp
                        }
                        
                        pose_data = pose_estimator.evaluate_pose(keypoints_np.flatten(), use_hme)
                        if use_hme:
                            # Get encrypted features from pose estimator
                            if pose_data and 'encrypted_features' in pose_data:
                                skeletal_data["encrypted_features"] = pose_data['encrypted_features']
                                skeletal_data["hme_mode"] = True
                        else:
                            # Plain mode: send raw keypoints
                            if pose_data:
                                skeletal_data['pose_data'] = pose_data
                                skeletal_data["hme_mode"] = False
                        
                        send_data_via_http("skeletal_data", skeletal_data)
                    else:
                        # In local mode, do pose estimation locally
                        pose_data = pose_estimator.evaluate_pose(keypoints_np.flatten(), use_hme)
                    
                    pose_eval_time = time_ms() - pose_eval_start
                    if pose_eval_time > 10:  # Only log if pose evaluation takes more than 10ms
                        print(f"[PERF] Pose evaluation: {pose_eval_time}ms")
                    
                    # Send alerts for fall detection (only in analytics mode)
                    if control_flags["analytics_mode"] and track.id in fall_ids:
                        alert_data = {
                            "track_id": track.id,
                            "alert_type": "fall_detected",
                            "timestamp": timestamp,
                            "pose_data": pose_data
                        }
                        send_data_via_http("pose_alert", alert_data)

                    # Assign ID
                    if track.id not in online_targets["id"]:
                        online_targets["id"].append(track.id)
                        online_targets["bbox"].append(queue.Queue(maxsize=queue_size))
                        online_targets["points"].append(queue.Queue(maxsize=queue_size))

                    idx = online_targets["id"].index(track.id)

                    # Add bbox and points to queue
                    if online_targets["bbox"][idx].qsize() >= queue_size:
                        online_targets["bbox"][idx].get()
                        online_targets["points"][idx].get()
                    online_targets["bbox"][idx].put([tracker_obj.x, tracker_obj.y, tracker_obj.w, tracker_obj.h])
                    online_targets["points"][idx].put(obj.points)


                    # Enhanced fall detection with pose data (only in local mode)
                    skip_fall_judgment = (pose_data is None or 
                                         (isinstance(pose_data, dict) and pose_data.get('label') == "None"))
                    
                    if not control_flags["analytics_mode"] and online_targets["bbox"][idx].qsize() == queue_size and not skip_fall_judgment:
                        # Get results from all three fall detection methods
                        (fall_detected_bbox_only, counter_bbox_only,
                         fall_detected_motion_pose_and, counter_motion_pose_and,
                         fall_detected_flexible, counter_flexible) = get_fall_info(
                            tracker_obj, online_targets, idx, fallParam, queue_size, fps, pose_data, use_hme
                        )
                        
                        # Store results based on selected algorithm
                        current_fall_algorithm = control_flags.get("fall_algorithm", 3)
                        
                        if current_fall_algorithm == 1:
                            current_fall_detected = fall_detected_bbox_only
                            current_fall_counter = counter_bbox_only
                        elif current_fall_algorithm == 2:
                            current_fall_detected = fall_detected_motion_pose_and
                            current_fall_counter = counter_motion_pose_and
                        else:  # Algorithm 3
                            current_fall_detected = fall_detected_flexible
                            current_fall_counter = counter_flexible
                        
                        # Update fall_ids based on selected algorithm
                        if current_fall_detected:
                            fall_ids.add(track.id)
                            print(f"LOCAL ALERT: Fall detected (Algorithm {current_fall_algorithm}) for track_id {track.id}")
                        elif track.id in fall_ids:
                            fall_ids.remove(track.id)
                    elif not control_flags["analytics_mode"] and skip_fall_judgment:
                        # Reset fall counters when skipping fall judgment
                        current_fall_counter = 0
                        current_fall_detected = False
                        if track.id in fall_ids:
                            fall_ids.remove(track.id)

                    elif control_flags["analytics_mode"]:
                        # Try to match track_id with server analysis
                        server_track_id = server_pose_analysis.get("track_id")
                        fall_detection = server_pose_analysis.get("fall_detection", {})
                        
                        # Get selected algorithm
                        current_fall_algorithm = control_flags.get("fall_algorithm", 3)
                        
                        # Get detection and counter based on selected algorithm
                        method_key = f"method{current_fall_algorithm}"
                        
                        if method_key in fall_detection:
                            method_data = fall_detection[method_key]
                            current_fall_detected = method_data.get("detected", False)
                            current_fall_counter = method_data.get("counter", 0)
                            
                            # Update fall_ids based on detection
                            if current_fall_detected:
                                fall_ids.add(track.id)
                            elif track.id in fall_ids:
                                fall_ids.remove(track.id)
                        else:
                            current_fall_detected = False
                            current_fall_counter = 0

                    # Safety area check (uses pose_data from either mode)
                    is_lying_down = False
                    status_str = "unknown"
                    
                    if pose_data:
                        status_str = pose_data.get('label', 'unknown')
                        is_lying_down = ("lying" in status_str.lower())
                        current_torso_angle = pose_data.get('torso_angle')
                        current_thigh_uprightness = pose_data.get('thigh_uprightness')
                            
                    if is_lying_down:
                        if control_flags.get("use_safety_check", True):
                            normalized_keypoints = normalize_keypoints(obj.points, img.width(), img.height())
                            is_safe = safety_checker.body_in_safe_zone(normalized_keypoints, SAFETY_CHECK_METHOD)
                            
                            if not is_safe:
                                unsafe_ids.add(track.id)
                                # Send unsafe alert to analytics if in analytics mode
                                if control_flags["analytics_mode"]:
                                    alert_data = {
                                        "track_id": track.id,
                                        "alert_type": "unsafe_position",
                                        "timestamp": timestamp,
                                        "pose_data": pose_data
                                    }
                                    send_data_via_http("pose_alert", alert_data)
                                else:
                                    print(f"LOCAL ALERT: Unsafe position for track_id {track.id} at {timestamp}")
                            elif track.id in unsafe_ids:
                                unsafe_ids.remove(track.id)
                        else:
                            if track.id in unsafe_ids:
                                unsafe_ids.remove(track.id)

                    # Draw tracking info
                    if track.id in fall_ids:
                        msg = f"[{track.id}] FALL"
                        color = image.COLOR_RED
                    elif track.id in unsafe_ids:
                        msg = f"[{track.id}] UNSAFE"
                        color = image.COLOR_ORANGE
                    else:
                        msg = f"[{track.id}] {status_str}"
                        color = image.COLOR_GREEN

                    img.draw_string(int(tracker_obj.x), int(tracker_obj.y), msg, color=color, scale=0.5)
                    detector.draw_pose(img, obj.points, 8 if detector.input_width() > 480 else 4, color=color)
                    
                    if is_recording:
                        safety_status = 1 if track.id in fall_ids else (2 if track.id in unsafe_ids else 0)
                        skeleton_saver_2d.add_keypoints(frame_id, track.id, obj.points, safety_status)
                    
                    break

    # Display fall detection results for chosen algorithm
    y_position = 30
    font_scale = 0.4
    
    # Determine algorithm name for display
    algorithm_names = {
        1: "BBox Only",
        2: "Flexible",
        3: "Conservative"
    }
    
    # Show selected algorithm and counter
    algorithm_name = algorithm_names.get(current_fall_algorithm, "Conservative")
    fall_text = f"Fall ({algorithm_name}): {current_fall_counter}/{FALL_COUNT_THRES}"
    
    # Color based on detection status
    text_color = image.COLOR_RED if current_fall_detected else image.COLOR_WHITE
    
    img.draw_string(10, y_position, fall_text, color=text_color, scale=font_scale)
    y_position += 15
    
    # Add detection status text
    if current_fall_detected:
        img.draw_string(150, 30, "DETECTED!", color=image.COLOR_RED, scale=font_scale)

    # Pose data display (if available)
    if current_torso_angle is not None:
        torso_text = f"Torso: {current_torso_angle:.1f}°"
        img.draw_string(10, y_position, torso_text, color=image.COLOR_BLUE, scale=font_scale)
        y_position += 15

    if current_thigh_uprightness is not None:
        thigh_text = f"Thigh: {current_thigh_uprightness:.1f}°"
        img.draw_string(10, y_position, thigh_text, color=image.COLOR_BLUE, scale=font_scale)
        y_position += 15

    # Draw safe area polygons
    if control_flags.get("show_safe_area", False):
        for polygon in safety_checker.safe_polygons:
            points = []
            for x_norm, y_norm in polygon:
                x_pixel = int(x_norm * img.width())
                y_pixel = int(y_norm * img.height())
                points.append((x_pixel, y_pixel))
            
            for i in range(len(points)):
                start_point = points[i]
                end_point = points[(i + 1) % len(points)]
                img.draw_line(start_point[0], start_point[1], 
                            end_point[0], end_point[1], 
                            color=image.COLOR_BLUE, thickness=2)

    if is_recording:
        recorder.add_frame(img)

    # Draw recording status
    if is_recording:
        recording_time = (time_ms() - recording_start_time) // 1000
        status_text = f"REC {recording_time}s"
        
        font_scale = 0.5
        approx_char_width = 10
        text_width = len(status_text) * approx_char_width
        right_margin = 15
        top_margin = 10
        
        text_x = img.width() - text_width - right_margin
        text_y = top_margin
        
        text_x = max(5, min(text_x, img.width() - text_width - 5))
        
        img.draw_string(int(text_x), int(text_y), status_text, color=image.COLOR_RED, scale=font_scale)

    # Draw operation mode and algorithm
    if control_flags["analytics_mode"]:
        mode_text = f"Mode: Analytics (Alg:{current_fall_algorithm})"
        mode_color = image.Color.from_rgb(0, 255, 255)  # Cyan
    else:
        mode_text = f"Mode: Local (Alg:{current_fall_algorithm})"
        mode_color = image.Color.from_rgb(255, 165, 0)  # Orange
    
    img.draw_string(10, 15, mode_text, color=mode_color, scale=0.5)
    
    # Draw connection status
    if control_flags["analytics_mode"] and not analytics_server_available:
        conn_text = "ANALYTICS OFFLINE"
        img.draw_string(img.width() - 200, 15, conn_text, color=image.COLOR_YELLOW, scale=0.4)
    elif control_flags["analytics_mode"] and connection_error_count > 5:
        conn_text = f"CONN: {connection_error_count} errors"
        img.draw_string(img.width() - 200, 15, conn_text, color=image.COLOR_YELLOW, scale=0.4)

    disp.show(img)
    
    # 7. Send frame to analytics server if in analytics mode (async)
    if control_flags["analytics_mode"] and analytics_server_available:
        # Add frame to queue for async worker to send
        try:
            frame_queue.put_nowait(img)
        except queue.Full:
            # Queue is full, skip this frame to avoid blocking
            print("[ASYNC] Frame queue full, skipping frame")
    
    # Periodically report state if in analytics mode and connected (async)
    if control_flags["analytics_mode"] and analytics_server_available:
        current_time = time_ms()
        if current_time - last_state_report > STATE_REPORT_INTERVAL_MS:
            # Prepare state data for async worker
            state_data = {
                "camera_id": CAMERA_ID,
                "camera_name": CAMERA_NAME,
                "control_flags": control_flags,  # Informational only, server won't use this to update flags
                "safe_areas": safety_checker.safe_polygons,
                "ip_address": server_ip,
                "timestamp": time_ms()
            }
            
            # Add to state queue for async worker to send
            try:
                state_queue.put_nowait(state_data)
                last_state_report = current_time
            except queue.Full:
                print("[ASYNC] State queue full, skipping state report")
    
    # 8. Display rendering
    display_start = time_ms()
    disp.show(img)
    display_time = time_ms() - display_start
    
    # 9. Frame timing summary (every 30 frames to avoid spam)
    frame_total_time = time_ms() - frame_start_time
    frame_counter += 1
    
    if frame_counter % 30 == 0:
        # Ensure all timing variables are available
        cam_time = camera_time if 'camera_time' in locals() else 0
        seg_t = seg_time if 'seg_time' in locals() else 0
        pose_t = pose_time if 'pose_time' in locals() else 0
        track_t = tracking_time if 'tracking_time' in locals() else 0
        disp_t = display_time if 'display_time' in locals() else 0
        
        print(f"[PERF] Frame {frame_counter}: Total={frame_total_time}ms, Camera={cam_time}ms, Seg={seg_t}ms, Pose={pose_t}ms, Track={track_t}ms, Display={disp_t}ms")
    
    # Periodic garbage collection
    current_time = time_ms()
    if current_time - last_gc_time > GC_INTERVAL_MS:
        gc_start = time_ms()
        gc.collect()
        gc_time = time_ms() - gc_start
        print(f"[PERF] Garbage collection: {gc_time}ms")
        last_gc_time = current_time

# Final cleanup
command_server.stop()

# Stop async workers
if 'flag_sync_worker' in locals() and flag_sync_worker:
    flag_sync_worker.stop()
if 'frame_sender_worker' in locals() and frame_sender_worker:
    frame_sender_worker.stop()
if 'state_reporter_worker' in locals() and state_reporter_worker:
    state_reporter_worker.stop()

if is_recording:
    stop_recording()

# Save final state before exit
save_control_flags()
print("\n=== Final Configuration Saved ===")
print(f"Analytics Mode: {'ENABLED' if control_flags['analytics_mode'] else 'DISABLED'}")
print(f"Recording: {'ENABLED' if control_flags['record'] else 'DISABLED'}")

print("Camera stream stopped")