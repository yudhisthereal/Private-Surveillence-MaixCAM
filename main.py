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

# Analytics Server Setup
ANALYTICS_SERVER_IP = "10.128.10.130"  # Your analytics server IP
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

pose_estimator = PoseEstimation()

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

# Camera control flags (fetched from analytics)
control_flags = {
    "record": False,
    "show_raw": False,
    "set_background": False,
    "auto_update_bg": False,
    "show_safe_area": False,
    "use_safety_check": True
}

# Frame sending
FRAME_SEND_INTERVAL_MS = 200  # 5 FPS
last_frame_sent_time = time.ticks_ms()

# State reporting
STATE_REPORT_INTERVAL_MS = 30000
last_state_report = time.ticks_ms()

# Connection management
connection_error_count = 0
MAX_CONNECTION_ERRORS = 10

# Command server for receiving commands from analytics
command_server_running = True
received_commands = []
commands_lock = threading.Lock()

def save_control_flags():
    """Save control flags to file"""
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump({
                "control_flags": control_flags,
                "timestamp": time.ticks_ms()
            }, f)
    except Exception as e:
        print(f"Error saving control flags: {e}")

def load_control_flags():
    """Load control flags from file"""
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                data = json.load(f)
                if "control_flags" in data:
                    control_flags.update(data["control_flags"])
                    print(f"Loaded control flags from file")
    except Exception as e:
        print(f"Error loading control flags: {e}")

def check_server_connection():
    """Check if analytics server is reachable"""
    try:
        response = requests.get(f"{ANALYTICS_HTTP_URL}/", timeout=2)
        print(f"Analytics server connection successful: {response.status_code}")
        return True
    except Exception as e:
        print(f"Analytics server connection failed: {e}")
        return False

def send_frame_simple(img):
    """Simple frame upload to analytics server"""
    global connection_error_count
    
    try:
        # Convert to JPEG with lower quality for faster transmission
        jpeg_img = img.to_format(image.Format.FMT_JPEG)
        jpeg_bytes = jpeg_img.to_bytes()
        
        # Upload to analytics server
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
        if connection_error_count % 10 == 0:  # Print every 10 errors
            print(f"Frame upload error ({connection_error_count}): {e}")
        return False

def fetch_control_flags():
    """Fetch control flags from analytics server"""
    try:
        response = requests.get(
            f"{ANALYTICS_HTTP_URL}/camera_state?camera_id={CAMERA_ID}",
            timeout=2.0
        )
        
        if response.status_code == 200:
            flags = response.json()
            # Update control flags (ignore metadata)
            for key in control_flags.keys():
                if key in flags:
                    control_flags[key] = flags[key]
            return True
        else:
            print(f"Failed to fetch flags: HTTP {response.status_code}")
            return False
            
    except Exception as e:
        print(f"Error fetching control flags: {e}")
        return False

def report_camera_state():
    """Report state to analytics server"""
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
    """Send frame to analytics server"""
    global last_frame_sent_time
    
    current_time = time.ticks_ms()
    time_since_last = current_time - last_frame_sent_time

    if time_since_last >= FRAME_SEND_INTERVAL_MS:
        success = send_frame_simple(img)
        last_frame_sent_time = current_time
        return success
    
    return True  # Don't send this frame, rate limited

def send_data_via_http(data_type, data):
    """Send JSON data to analytics server"""
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
        
        # Save to file
        with open(SAFE_AREA_FILE, 'w') as f:
            json.dump(safe_areas, f)
            
        # Report updated state
        report_camera_state()
            
    except Exception as e:
        print(f"Error updating safety checker: {e}")

def handle_command(command, value):
    """Handle command from analytics server"""
    global control_flags
    
    print(f"Processing command: {command} = {value}")
    
    if command == "toggle_record":
        control_flags["record"] = bool(value)
    elif command == "toggle_raw":
        control_flags["show_raw"] = bool(value)
    elif command == "auto_update_bg":
        control_flags["auto_update_bg"] = bool(value)
    elif command == "set_background":
        control_flags["set_background"] = bool(value)
        if value:
            print("Background update requested")
    elif command == "toggle_safe_area_display":
        control_flags["show_safe_area"] = bool(value)
    elif command == "toggle_safety_check":
        control_flags["use_safety_check"] = bool(value)
    elif command == "update_safe_areas":
        if isinstance(value, list):
            update_safety_checker_polygons(value)
    
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
    
    # Notify analytics
    send_data_via_http("recording_started", {"timestamp": timestamp})
    
    print(f"Started recording: {timestamp}")
    return recording_start_time

def stop_recording():
    global is_recording
    
    if recorder.is_active:
        recorder.end()
        skeleton_saver_2d.save_to_csv()
        is_recording = False
        
        # Notify analytics
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
            self.sock.settimeout(0.5)  # Non-blocking with timeout
            print(f"Command server listening on port {LOCAL_PORT}")
            
            while self.running:
                try:
                    readable, _, _ = select.select([self.sock], [], [], 0.5)
                    if readable:
                        conn, addr = self.sock.accept()
                        conn.settimeout(1.0)
                        
                        # Handle connection in separate thread
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
            # Read request
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
                    # Parse request
                    request_str = request.decode('utf-8', errors='ignore')
                    
                    # Check if it's a POST command
                    if "POST /command" in request_str:
                        # Find body
                        body_start = request_str.find("\r\n\r\n")
                        if body_start != -1:
                            body = request_str[body_start + 4:]
                            if body:
                                data = json.loads(body.strip())
                                with commands_lock:
                                    received_commands.append(data)
                                
                                # Send response
                                response = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n"
                                response += json.dumps({"status": "success"})
                                conn.send(response.encode())
                                print(f"Received command from {addr[0]}: {data.get('command')}")
                    else:
                        # Send 404 for other paths
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

# Initialize background
if os.path.exists(BACKGROUND_PATH):
    background_img = image.load(BACKGROUND_PATH, format=image.Format.FMT_RGB888)
    print("Loaded background image from file")
else:
    background_img = cam.read().copy()
    background_img.save(BACKGROUND_PATH)
    print("Created new background image")

# Load initial control flags and safe areas
load_control_flags()
initial_safe_areas = load_safe_areas()
update_safety_checker_polygons(initial_safe_areas)

# Start command server
command_server = CommandServer()
command_server.start()

# Test connection to analytics server
print(f"Analytics server: {ANALYTICS_HTTP_URL}")

if not check_server_connection():
    print("Warning: Cannot connect to analytics server")
    print("Will continue with local operation and try to reconnect periodically")
else:
    print("Analytics server connection successful")
    # Initial state report
    report_camera_state()
    # Fetch initial flags
    fetch_control_flags()

print("Starting camera stream...")

# === Main loop ===
frame_counter = 0
last_gc_time = time.ticks_ms()
GC_INTERVAL_MS = 10000

while not app.need_exit():
    raw_img = cam.read()
    
    # Process received commands
    with commands_lock:
        while received_commands:
            cmd_data = received_commands.pop(0)
            handle_command(cmd_data.get("command"), cmd_data.get("value"))
    
    # Periodically fetch control flags from analytics
    if frame_counter % 30 == 0:  # Every ~30 frames
        fetch_control_flags()
    
    # Check for background update request
    if control_flags.get("set_background", False):
        background_img = raw_img.copy()
        background_img.save(BACKGROUND_PATH)
        control_flags["set_background"] = False
        save_control_flags()
        print("Background updated from analytics request")
    
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

    # Variables to store current fall counters and pose data for display
    current_fall_counter_old = 0
    current_fall_counter_new = 0
    current_fall_detected_old = False
    current_fall_detected_new = False
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

                    # Get pose estimation data
                    pose_data = pose_estimator.evaluate_pose(keypoints_np.flatten())

                    # Send skeletal data to analytics
                    skeletal_data = {
                        "keypoints": obj.points,
                        "bbox": [tracker_obj.x, tracker_obj.y, tracker_obj.w, tracker_obj.h],
                        "track_id": track.id,
                        "pose_data": pose_data,
                        "timestamp": timestamp
                    }
                    send_data_via_http("skeletal_data", skeletal_data)

                    # Send alerts for fall detection
                    if track.id in fall_ids:
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

                    # Enhanced fall detection with pose data
                    skip_fall_judgment = (pose_data is None or 
                                         (isinstance(pose_data, dict) and pose_data.get('label') == "None"))
                    
                    if online_targets["bbox"][idx].qsize() == queue_size and not skip_fall_judgment:
                        fall_detected_old, counter_old, fall_detected_new, counter_new = get_fall_info(
                            tracker_obj, online_targets, idx, fallParam, queue_size, fps, pose_data
                        )
                        
                        # Store current counter values for display
                        current_fall_counter_old = counter_old
                        current_fall_counter_new = counter_new
                        current_fall_detected_old = fall_detected_old
                        current_fall_detected_new = fall_detected_new
                        
                        if fall_detected_old or fall_detected_new:
                            fall_ids.add(track.id)
                        elif track.id in fall_ids:
                            fall_ids.remove(track.id)
                    elif skip_fall_judgment:
                        # Reset fall counters when skipping fall judgment
                        current_fall_counter_old = 0
                        current_fall_counter_new = 0
                        current_fall_detected_old = False
                        current_fall_detected_new = False
                        if track.id in fall_ids:
                            fall_ids.remove(track.id)

                    # Safety area check
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
                                # Send unsafe alert to analytics
                                alert_data = {
                                    "track_id": track.id,
                                    "alert_type": "unsafe_position",
                                    "timestamp": timestamp,
                                    "pose_data": pose_data
                                }
                                send_data_via_http("pose_alert", alert_data)
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

    # Display fall counters and pose data with smaller font
    y_position = 30
    font_scale = 0.4

    # Fall counters display with detection status
    fall_old_text = f"Fall Old: {current_fall_counter_old}/{FALL_COUNT_THRES}"
    fall_new_text = f"Fall New: {current_fall_counter_new}/{FALL_COUNT_THRES}"
    
    old_color = image.COLOR_RED if current_fall_detected_old else image.COLOR_WHITE
    new_color = image.COLOR_RED if current_fall_detected_new else image.COLOR_WHITE

    img.draw_string(10, y_position, fall_old_text, color=old_color, scale=font_scale)
    y_position += 15
    img.draw_string(10, y_position, fall_new_text, color=new_color, scale=font_scale)
    y_position += 15

    # Add detection status text
    if current_fall_detected_old:
        img.draw_string(150, 30, "DETECTED!", color=image.COLOR_RED, scale=font_scale)
    if current_fall_detected_new:
        img.draw_string(150, 45, "DETECTED!", color=image.COLOR_RED, scale=font_scale)

    # Pose data display
    if current_torso_angle is not None:
        torso_text = f"Torso: {current_torso_angle:.1f}°"
        img.draw_string(10, y_position, torso_text, color=image.COLOR_CYAN, scale=font_scale)
        y_position += 15

    if current_thigh_uprightness is not None:
        thigh_text = f"Thigh: {current_thigh_uprightness:.1f}°"
        img.draw_string(10, y_position, thigh_text, color=image.COLOR_CYAN, scale=font_scale)
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

    # Draw operation mode
    mode_text = "Mode: Analytics"
    img.draw_string(10, 15, mode_text, color=image.Color.from_rgb(0, 255, 255), scale=0.5)
    
    # Draw connection status
    if connection_error_count > 5:
        conn_text = f"CONN: {connection_error_count} errors"
        img.draw_string(img.width() - 200, 15, conn_text, color=image.COLOR_YELLOW, scale=0.4)

    disp.show(img)
    
    # Send frame to analytics server
    send_frame_to_analytics(img)
    
    # Periodically report state
    current_time = time.ticks_ms()
    if current_time - last_state_report > STATE_REPORT_INTERVAL_MS:
        report_camera_state()
        last_state_report = current_time
    
    # Periodic garbage collection
    if current_time - last_gc_time > GC_INTERVAL_MS:
        gc.collect()
        last_gc_time = current_time
    
    frame_counter += 1

# Final cleanup
command_server.stop()
if is_recording:
    stop_recording()
print("Camera stream stopped")