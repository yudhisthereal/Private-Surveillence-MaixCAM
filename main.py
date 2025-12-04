# main.py

from maix import camera, display, app, nn, image, time, tracker
from pose.pose_estimation import PoseEstimation
from tools.wifi_connect import connect_wifi
from tools.video_record import VideoRecorder
from tools.time_utils import get_timestamp_str
from tools.skeleton_saver import SkeletonSaver2D
from pose.judge_fall import get_fall_info, FALL_COUNT_THRES
from tools.safe_area import BodySafetyChecker, CheckMethod
import queue
import numpy as np
import os
import json
import requests
import gc
import socket
import threading
import select

# Image Paths
BACKGROUND_PATH = "/root/static/background.jpg"
SAFE_AREA_FILE = "/root/safe_areas.json"
STATE_FILE = "/root/camera_state.json"
LOCAL_FLAGS_FILE = "/root/control_flags.json"

# Analytics Server Setup
ANALYTICS_SERVER_IP = "10.128.10.130"
CAMERA_ID = "maixcam_001"
ANALYTICS_HTTP_URL = f"http://{ANALYTICS_SERVER_IP}:8000"

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
last_update_ms = time.ticks_ms()

# Initialize Body Safety Checker
safety_checker = BodySafetyChecker()
SAFETY_CHECK_METHOD = CheckMethod.TORSO_HEAD
USE_SAFETY_CHECK = True

# Camera control flags
control_flags = {
    "record": False,
    "show_raw": False,
    "set_background": False,
    "auto_update_bg": False,
    "show_safe_area": False,
    "use_safety_check": True,
    "analytics_mode": True,
    "fall_algorithm": 3
}

# Frame sending
FRAME_SEND_INTERVAL_MS = 200
last_frame_sent_time = time.ticks_ms()

# State reporting
STATE_REPORT_INTERVAL_MS = 30000
last_state_report = time.ticks_ms()

# Connection management
connection_error_count = 0
MAX_CONNECTION_ERRORS = 10

# Analytics server connection status
analytics_server_available = False
CONNECTION_RETRY_INTERVAL_MS = 10000
last_connection_check = 0

# Command server for receiving commands from analytics
command_server_running = True
received_commands = []
commands_lock = threading.Lock()

# Track when flags were last changed locally to prevent sync overwriting
flag_last_changed = {}  # key -> timestamp in ms
FLAG_SYNC_PROTECTION_MS = 5000  # Don't sync flags changed within last 5 seconds

def save_control_flags():
    """Save control flags to local storage"""
    try:
        flags_data = {
            "control_flags": control_flags,
            "timestamp": time.ticks_ms(),
            "camera_id": CAMERA_ID,
            "saved_locally": True
        }
        
        with open(LOCAL_FLAGS_FILE, 'w') as f:
            json.dump(flags_data, f, indent=2)
        print(f"Control flags saved locally to {LOCAL_FLAGS_FILE}")
        
    except Exception as e:
        print(f"Error saving control flags: {e}")

def load_control_flags():
    """Load control flags from local storage or analytics server"""
    local_flags_loaded = False
    
    try:
        if os.path.exists(LOCAL_FLAGS_FILE):
            with open(LOCAL_FLAGS_FILE, 'r') as f:
                data = json.load(f)
                if "control_flags" in data:
                    for key in control_flags.keys():
                        if key in data["control_flags"]:
                            control_flags[key] = data["control_flags"][key]
                    local_flags_loaded = True
                    print(f"Loaded control flags from local storage")
                    return True
    except Exception as e:
        print(f"Error loading control flags from local file: {e}")
    
    return local_flags_loaded

def sync_flags_with_server():
    """Try to sync flags with analytics server, fall back to local if unavailable"""
    global analytics_server_available
    
    if check_server_connection():
        try:
            response = requests.get(
                f"{ANALYTICS_HTTP_URL}/camera_state?camera_id={CAMERA_ID}",
                timeout=2.0
            )
            
            if response.status_code == 200:
                flags = response.json()
                current_time = time.ticks_ms()
                
                for key in control_flags.keys():
                    if key in flags:
                        # Don't overwrite flags that were changed recently
                        last_changed = flag_last_changed.get(key, 0)
                        time_since_change = current_time - last_changed
                        
                        if time_since_change > FLAG_SYNC_PROTECTION_MS:
                            # Safe to sync - flag wasn't changed recently
                            control_flags[key] = flags[key]
                        else:
                            # Flag was changed recently, skip syncing this flag
                            print(f"Skipping sync for {key} (changed {time_since_change}ms ago)")
                
                control_flags["analytics_mode"] = True
                analytics_server_available = True
                
                save_control_flags()
                
                print("Synced flags from analytics server")
                return True
            else:
                print(f"Server responded with non-200: {response.status_code}")
                analytics_server_available = False
                
        except Exception as e:
            print(f"Error fetching flags from server: {e}")
            analytics_server_available = False
    else:
        analytics_server_available = False
    
    print("Using locally stored flags (server unavailable)")
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
    """Report state to analytics server"""
    if not analytics_server_available:
        return False
    
    state = {
        "camera_id": CAMERA_ID,
        "control_flags": control_flags,
        "safe_areas": safety_checker.safe_polygons,
        "ip_address": server_ip,
        "timestamp": time.ticks_ms()
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
    
    current_time = time.ticks_ms()
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
            return response.json()
        else:
            print(f"Pose analysis request failed: HTTP {response.status_code}")
            return None
            
    except Exception as e:
        print(f"Error getting pose analysis: {e}")
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
            "timestamp": time.ticks_ms(),
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
    global control_flags
    
    print(f"Processing command: {command} = {value}")
    current_time = time.ticks_ms()
    
    if command == "toggle_record":
        control_flags["record"] = bool(value)
        flag_last_changed["record"] = current_time
    elif command == "toggle_raw":
        control_flags["show_raw"] = bool(value)
        flag_last_changed["show_raw"] = current_time
    elif command == "auto_update_bg":
        control_flags["auto_update_bg"] = bool(value)
        flag_last_changed["auto_update_bg"] = current_time
    elif command == "set_background":
        control_flags["set_background"] = bool(value)
        flag_last_changed["set_background"] = current_time
        if value:
            print("Background update requested")
    elif command == "toggle_safe_area_display":
        control_flags["show_safe_area"] = bool(value)
        flag_last_changed["show_safe_area"] = current_time
    elif command == "toggle_safety_check":
        control_flags["use_safety_check"] = bool(value)
        flag_last_changed["use_safety_check"] = current_time
    elif command == "update_safe_areas":
        if isinstance(value, list):
            update_safety_checker_polygons(value)
    elif command == "toggle_analytics_mode":
        control_flags["analytics_mode"] = bool(value)
        flag_last_changed["analytics_mode"] = current_time
        if not control_flags["analytics_mode"]:
            print("Switched to local analysis mode")
        else:
            print("Switched to analytics mode")
    elif command == "set_fall_algorithm":  # This is already correct
        algorithm = int(value) if isinstance(value, (int, float)) else 3
        if algorithm in [1, 2, 3]:
            control_flags["fall_algorithm"] = algorithm
            flag_last_changed["fall_algorithm"] = current_time
            print(f"Fall algorithm set to Method {algorithm}")
        else:
            print(f"Invalid fall algorithm: {value}, defaulting to 3")
            control_flags["fall_algorithm"] = 3
            flag_last_changed["fall_algorithm"] = current_time
    
    save_control_flags()

def start_new_recording():
    global frame_id, recording_start_time, is_recording
    
    timestamp = get_timestamp_str()
    video_path = os.path.join("/root/recordings", f"{timestamp}.mp4")
    recorder.start(video_path, detector.input_width(), detector.input_height())
    skeleton_saver_2d.start_new_log(timestamp)
    frame_id = 0
    recording_start_time = time.ticks_ms()
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
    
    current_time = time.ticks_ms()
    
    if current_time - last_connection_check > CONNECTION_RETRY_INTERVAL_MS:
        was_available = analytics_server_available
        check_server_connection()
        last_connection_check = current_time
        
        if was_available and not analytics_server_available:
            print("Analytics server connection lost, falling back to local analysis mode")
            control_flags["analytics_mode"] = False
            save_control_flags()
        
        elif not was_available and analytics_server_available:
            print("Analytics server connection restored, switching back to analytics mode")
            control_flags["analytics_mode"] = True
            save_control_flags()
            sync_flags_with_server()
    
    return analytics_server_available

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
load_control_flags()
initial_safe_areas = load_safe_areas()
update_safety_checker_polygons(initial_safe_areas)

# Start command server
command_server = CommandServer()
command_server.start()

# Test connection to analytics server and sync flags
print(f"\n=== Testing Analytics Server Connection ===")
print(f"Analytics server: {ANALYTICS_HTTP_URL}")

if not sync_flags_with_server():
    print("Warning: Cannot connect to analytics server")
    print("Using locally stored configuration")
    control_flags["analytics_mode"] = False
    save_control_flags()
else:
    print("Analytics server connection successful")
    control_flags["analytics_mode"] = True
    save_control_flags()

print(f"\n=== Starting with Configuration ===")
print(f"Analytics Mode: {'ENABLED' if control_flags['analytics_mode'] else 'DISABLED'}")
print(f"Recording: {'ENABLED' if control_flags['record'] else 'DISABLED'}")

print("\nStarting camera stream...")

# === Main loop ===
frame_counter = 0
last_gc_time = time.ticks_ms()
GC_INTERVAL_MS = 10000
last_pose_analysis_time = 0
POSE_ANALYSIS_INTERVAL_MS = 500  # Get pose analysis every 500ms

# Store latest pose analysis from server
server_pose_analysis = {}

while not app.need_exit():
    raw_img = cam.read()
    
    # Check analytics server connection and handle fallback
    check_and_fallback_to_local()
    
    # Process received commands
    with commands_lock:
        while received_commands:
            cmd_data = received_commands.pop(0)
            handle_command(cmd_data.get("command"), cmd_data.get("value"))
    
    # Periodically sync flags with analytics server if in analytics mode
    if control_flags["analytics_mode"] and frame_counter % 60 == 0:
        sync_flags_with_server()
    
    # Periodically get pose analysis from analytics server
    if control_flags["analytics_mode"]:
        current_time = time.ticks_ms()
        if current_time - last_pose_analysis_time > POSE_ANALYSIS_INTERVAL_MS:
            analysis_data = get_pose_analysis_from_analytics()
            if analysis_data and analysis_data.get("status") != "no_data":
                server_pose_analysis = analysis_data
            last_pose_analysis_time = current_time
    
    # Check for background update request
    if control_flags.get("set_background", False):
        background_img = raw_img.copy()
        background_img.save(BACKGROUND_PATH)
        control_flags["set_background"] = False
        save_control_flags()
        print("Background updated from request")
    
    # Run segmentation for background updates and human detection
    objs_seg = segmentor.detect(raw_img, conf_th=0.5, iou_th=0.45)
    current_human_present = any(segmentor.labels[obj.class_id] in ["person", "human"] for obj in objs_seg)
    bg_updated = False

    # Background update logic
    if control_flags.get("auto_update_bg", False):
        if prev_human_present and not current_human_present:
            no_human_counter += 1
            if no_human_counter >= NO_HUMAN_CONFIRM_FRAMES:
                background_img = raw_img.copy()
                background_img.save(BACKGROUND_PATH)
                last_update_ms = time.ticks_ms()
                no_human_counter = 0
                bg_updated = True
        else:
            no_human_counter = 0
            if time.ticks_ms() - last_update_ms > UPDATE_INTERVAL_MS:
                background_img = update_background(background_img, raw_img, objs_seg)
                background_img.save(BACKGROUND_PATH)
                last_update_ms = time.ticks_ms()
                bg_updated = True

        prev_human_present = current_human_present

        if time.ticks_ms() - last_update_ms > UPDATE_INTERVAL_MS and not bg_updated:
            background_img = update_background(background_img, raw_img, objs_seg)
            background_img.save(BACKGROUND_PATH)
            last_update_ms = time.ticks_ms()
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

    # Pose detection and tracking
    objs = detector.detect(raw_img, conf_th=0.5, iou_th=0.45, keypoint_th=0.5)
    
    pose_human_present = len(objs) > 0
    human_present = current_human_present or pose_human_present
    
    human_presence_history.append(human_present)
    if len(human_presence_history) > NO_HUMAN_FRAMES_TO_STOP + 1:
        human_presence_history.pop(0)
    
    # Recording logic
    if control_flags.get("record", False):
        now = time.ticks_ms()
        
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
    out_bbox = yolo_objs_to_tracker_objs(objs)
    tracks = tracker0.update(out_bbox)

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
                    timestamp = time.ticks_ms()

                    # Get pose data based on mode
                    pose_data = None
                    if control_flags["analytics_mode"]:
                        # In analytics mode, use data from server or send raw data
                        # Try to match track_id with server analysis
                        server_track_id = server_pose_analysis.get("track_id")
                        if server_track_id == track.id:
                            pose_data = server_pose_analysis.get("pose_data")
                            fall_detection = server_pose_analysis.get("fall_detection", {})
                            current_fall_detected_old = fall_detection.get("fall_detected_old", False)
                            current_fall_detected_new = fall_detection.get("fall_detected_new", False)
                            current_fall_counter_old = fall_detection.get("counter_old", 0)
                            current_fall_counter_new = fall_detection.get("counter_new", 0)
                        
                        # Always send raw skeletal data to analytics
                        skeletal_data = {
                            "keypoints": obj.points,
                            "bbox": [tracker_obj.x, tracker_obj.y, tracker_obj.w, tracker_obj.h],
                            "track_id": track.id,
                            "timestamp": timestamp
                        }
                        send_data_via_http("skeletal_data", skeletal_data)
                    else:
                        # In local mode, do pose estimation locally
                        pose_data = pose_estimator.evaluate_pose(keypoints_np.flatten())
                    
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
                            tracker_obj, online_targets, idx, fallParam, queue_size, fps, pose_data
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
                        if current_fall_algorithm == 1:
                            current_fall_detected = fall_detection.get("method1", {}).get("detected", False)
                            current_fall_counter = fall_detection.get("method1", {}).get("counter", 0)
                        elif current_fall_algorithm == 2:
                            current_fall_detected = fall_detection.get("method2", {}).get("detected", False)
                            current_fall_counter = fall_detection.get("method2", {}).get("counter", 0)
                        else:
                            current_fall_detected = fall_detection.get("method3", {}).get("detected", False)
                            current_fall_counter = fall_detection.get("method3", {}).get("counter", 0)
                        
                        print(f"[DEBUG] Analytics Mode - Algorithm {current_fall_algorithm}, Detected: {current_fall_detected}, Counter: {current_fall_counter}")

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
    
    # Add mode indicator
    mode_text = "Analytics" if control_flags["analytics_mode"] else "Local"
    mode_color = image.COLOR_BLUE if control_flags["analytics_mode"] else image.COLOR_YELLOW
    img.draw_string(10, y_position, f"Mode: {mode_text}", color=mode_color, scale=font_scale)
    y_position += 15
    
    # Show algorithm number
    img.draw_string(10, y_position, f"Algorithm: {current_fall_algorithm}", color=image.COLOR_GREEN, scale=font_scale)
    y_position += 15

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
        recording_time = (time.ticks_ms() - recording_start_time) // 1000
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
    
    # Send frame to analytics server if in analytics mode
    if control_flags["analytics_mode"] and analytics_server_available:
        send_frame_to_analytics(img)
    
    # Periodically report state if in analytics mode and connected
    if control_flags["analytics_mode"] and analytics_server_available:
        current_time = time.ticks_ms()
        if current_time - last_state_report > STATE_REPORT_INTERVAL_MS:
            report_camera_state()
            last_state_report = current_time
    
    # Periodic garbage collection
    current_time = time.ticks_ms()
    if current_time - last_gc_time > GC_INTERVAL_MS:
        gc.collect()
        last_gc_time = current_time
    
    frame_counter += 1

# Final cleanup
command_server.stop()
if is_recording:
    stop_recording()

# Save final state before exit
save_control_flags()
print("\n=== Final Configuration Saved ===")
print(f"Analytics Mode: {'ENABLED' if control_flags['analytics_mode'] else 'DISABLED'}")
print(f"Recording: {'ENABLED' if control_flags['record'] else 'DISABLED'}")

print("Camera stream stopped")