from maix import camera, display, app, nn, image, time, tracker
from pose.pose_estimation import PoseEstimation
from tools.wifi_connect import connect_wifi
from tools.video_record import VideoRecorder
from tools.time_utils import get_timestamp_str
from tools.skeleton_saver import SkeletonSaver2D
from pose.judge_fall import get_fall_info
from tools.web_server import web_server
from tools.safe_area import BodySafetyChecker, CheckMethod
import queue
import numpy as np
import os
import json
import paho.mqtt.client as mqtt
import base64
import pickle
import ssl

# Image Paths
BACKGROUND_PATH = "/root/static/background.jpg"
SAFE_AREA_FILE = "/root/safe_areas.json"

# Wi-Fi Setup
SSID = "GEREJA AL-IKHLAS (UMI MARIA)"
PASSWORD = "susugedhe"
server_ip = connect_wifi(SSID, PASSWORD)

# HiveMQ MQTT Setup
MQTT_BROKER = "3e065ffaa6084b219bc6553c8659b067.s1.eu.hivemq.cloud"
MQTT_PORT = 8883
MQTT_USERNAME = "PatientMonitor"
MQTT_PASSWORD = "Patientmonitor1"
MQTT_TOPIC_SKELETAL = "patient_monitor/skeletal_data"
MQTT_TOPIC_DIAGNOSIS = "patient_monitor/diagnosis"
MQTT_TOPIC_CONTROL = "patient_monitor/control"

# Web Server Setup
web_server.start_servers()
print("\nServer started. Connect to MaixCAM in your browser:")
print(f"   → http://{server_ip}:{web_server.HTTP_PORT}/")

# Ensure static dir exist
os.makedirs("/root/static", exist_ok=True)

# Initialize detectors
detector = nn.YOLO11(model="/root/models/yolo11n_pose.mud", dual_buff=True)
segmentor = nn.YOLO11(model="/root/models/yolo11n_seg.mud", dual_buff=True)

cam = camera.Camera(detector.input_width(), detector.input_height(), detector.input_format(), fps=60)
disp = display.Display()

pose_estimator = PoseEstimation()

image.load_font("sourcehansans", "/maixapp/share/font/SourceHanSansCN-Regular.otf", size=32)
image.set_default_font("sourcehansans")

# Skeleton Saver and Recorder setup
video_dir = "/root/recordings"
os.makedirs(video_dir, exist_ok=True)

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

# MQTT Client setup
mqtt_client = None
OPERATION_MODE = "encrypted_analytics"  # "local_analysis" or "encrypted_analytics"
REMOTE_ACCESS_MODE = False

def setup_mqtt():
    """Setup MQTT client for communication with analytics server"""
    global mqtt_client
    try:
        mqtt_client = mqtt.Client()
        
        # Set username and password for authentication
        mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
        mqtt_client.tls_set(cert_reqs=ssl.CERT_NONE)
        
        mqtt_client.on_connect = on_mqtt_connect
        mqtt_client.on_message = on_mqtt_message
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        mqtt_client.loop_start()
        print("MQTT client configured for HiveMQ Cloud")
    except Exception as e:
        print(f"MQTT connection failed: {e}")

def on_mqtt_connect(client, userdata, flags, rc):
    """Callback for when MQTT client connects"""
    if rc == 0:
        print("Connected to MQTT broker")
        client.subscribe(MQTT_TOPIC_DIAGNOSIS)
        client.subscribe(MQTT_TOPIC_CONTROL)
    else:
        print(f"Failed to connect to MQTT broker, return code {rc}")
        if rc == 5:
            print("Authentication failed - check username and password")

def on_mqtt_message(client, userdata, msg):
    """Callback for when MQTT message is received"""
    global OPERATION_MODE, REMOTE_ACCESS_MODE
    
    try:
        payload = json.loads(msg.payload.decode())
        
        if msg.topic == MQTT_TOPIC_DIAGNOSIS:
            handle_analytics_diagnosis(payload)
        elif msg.topic == MQTT_TOPIC_CONTROL:
            handle_control_message(payload)
            
    except Exception as e:
        print(f"Error processing MQTT message: {e}")

def handle_analytics_diagnosis(payload):
    """Handle diagnosis received from analytics server"""
    # Placeholder for handling encrypted diagnosis from analytics
    diagnosis = payload.get("diagnosis", {})
    timestamp = payload.get("timestamp")
    signature = payload.get("signature")  # For verification
    
    print(f"Received diagnosis from analytics: {diagnosis}")
    
    # Verify signature and process diagnosis
    if verify_diagnosis_signature(payload):
        # Merge with local diagnosis if needed
        fused_diagnosis = fuse_diagnoses(local_diagnosis, diagnosis)
        trigger_appropriate_actions(fused_diagnosis)

def handle_control_message(payload):
    """Handle control messages from analytics server or caregiver"""
    global OPERATION_MODE, REMOTE_ACCESS_MODE
    
    command = payload.get("command")
    
    if command == "switch_mode":
        new_mode = payload.get("mode")
        if new_mode in ["local_analysis", "encrypted_analytics"]:
            OPERATION_MODE = new_mode
            print(f"Switched to {OPERATION_MODE} mode")
    
    elif command == "remote_access":
        REMOTE_ACCESS_MODE = payload.get("enable", False)
        if REMOTE_ACCESS_MODE:
            print("Remote access enabled")
            # Start streaming reduced video/skeletal data
            start_remote_streaming()
        else:
            print("Remote access disabled")
            stop_remote_streaming()
    
    elif command == "trigger_recording":
        # Trigger recording based on analytics decision
        start_new_recording()

def verify_diagnosis_signature(payload):
    """Verify the signature of the diagnosis from analytics"""
    # Placeholder for signature verification
    # This ensures the diagnosis hasn't been tampered with
    return True  # Implement proper verification

def fuse_diagnoses(local_diagnosis, analytics_diagnosis):
    """Fuse local and analytics diagnoses for better accuracy"""
    # Placeholder for diagnosis fusion logic
    return analytics_diagnosis  # For now, trust analytics

def trigger_appropriate_actions(diagnosis):
    """Trigger appropriate actions based on diagnosis"""
    # Placeholder for action triggering
    if diagnosis.get("alert_level") == "high":
        # Trigger emergency protocols
        pass

def start_remote_streaming():
    """Start streaming reduced video/skeletal data for remote access"""
    # Placeholder for remote streaming implementation
    print("Starting remote streaming...")

def stop_remote_streaming():
    """Stop remote streaming"""
    # Placeholder for stopping remote streaming
    print("Stopping remote streaming...")

def send_skeletal_data_to_analytics(keypoints, bbox, track_id, timestamp):
    """Send skeletal data to analytics server via MQTT"""
    if mqtt_client and OPERATION_MODE == "encrypted_analytics":
        try:
            # Encrypt skeletal data before sending
            encrypted_data = encrypt_skeletal_data({
                "keypoints": keypoints,
                "bbox": bbox,
                "track_id": track_id,
                "timestamp": timestamp,
                "device_id": "maixcam_001"  # Unique device identifier
            })
            
            mqtt_client.publish(MQTT_TOPIC_SKELETAL, json.dumps(encrypted_data))
        except Exception as e:
            print(f"Error sending skeletal data to analytics: {e}")

def encrypt_skeletal_data(data):
    """Encrypt skeletal data for secure transmission"""
    # Placeholder for encryption implementation
    # Use proper encryption like AES or RSA
    return {
        "encrypted_data": base64.b64encode(pickle.dumps(data)).decode(),
        "encryption_method": "placeholder",
        "timestamp": time.time()
    }

# Load safe areas from file or use default
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
            safe_areas = web_server.get_safe_areas()
        safety_checker.clear_safe_polygons()
        for polygon in safe_areas:
            safety_checker.add_safe_polygon(polygon)
        print(f"Updated safety checker with {len(safe_areas)} polygon(s)")
    except Exception as e:
        print(f"Error updating safety checker: {e}")

# Set callback for safe areas updates
web_server.set_safe_areas_callback(update_safety_checker_polygons)

# Load initial safe areas
initial_safe_areas = load_safe_areas()
update_safety_checker_polygons(initial_safe_areas)

def start_new_recording():
    global frame_id, recording_start_time, is_recording
    
    timestamp = get_timestamp_str()
    video_path = os.path.join(video_dir, f"{timestamp}.mp4")
    recorder.start(video_path, detector.input_width(), detector.input_height())
    skeleton_saver_2d.start_new_log(timestamp)
    frame_id = 0
    recording_start_time = time.ticks_ms()
    is_recording = True
    
    print(f"Started recording: {timestamp}")
    return recording_start_time

def stop_recording():
    global is_recording
    
    if recorder.is_active:
        recorder.end()
        skeleton_saver_2d.save_to_csv()
        is_recording = False
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

fall_down = False
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
else:
    background_img = cam.read().copy()
    background_img.save(BACKGROUND_PATH)

# Setup MQTT
setup_mqtt()

# === Main loop ===
while not app.need_exit():
    raw_img = cam.read()
    flags = web_server.get_control_flags()

    # Check for safe areas updates
    if web_server.safe_areas_have_updates():
        pass

    # Run segmentation for background updates and human detection
    objs_seg = segmentor.detect(raw_img, conf_th=0.5, iou_th=0.45)
    current_human_present = any(segmentor.labels[obj.class_id] in ["person", "human"] for obj in objs_seg)
    bg_updated = False

    # Background update logic
    if flags["auto_update_bg"]:
        if prev_human_present and not current_human_present:
            no_human_counter += 1
            if no_human_counter >= NO_HUMAN_CONFIRM_FRAMES:
                print("Confirmed human absence for 5 frames — updating background.")
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
    if flags["show_raw"]:
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
    if flags["record"]:
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
    
    for track in tracks:
        if track.lost:
            continue
        for tracker_obj in track.history[-1:]:
            for obj in objs:
                if abs(obj.x - tracker_obj.x) < 10 and abs(obj.y - tracker_obj.y) < 10:
                    keypoints_np = to_keypoints_np(obj.points)
                    timestamp = time.ticks_ms()

                    # Send skeletal data to analytics if in encrypted mode
                    if OPERATION_MODE == "encrypted_analytics":
                        send_skeletal_data_to_analytics(
                            obj.points, 
                            [tracker_obj.x, tracker_obj.y, tracker_obj.w, tracker_obj.h],
                            track.id,
                            timestamp
                        )

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

                    # Local fall detection
                    if online_targets["bbox"][idx].qsize() == queue_size:
                        if get_fall_info(tracker_obj, online_targets, idx, fallParam, queue_size, fps):
                            fall_ids.add(track.id)
                        elif track.id in fall_ids:
                            fall_ids.remove(track.id)

                    # Safety area check
                    status_str = pose_estimator.evaluate_pose(keypoints_np)
                    is_lying_down = False
                    if status_str:
                        if status_str is not str:
                            status_str = str(status_str)
                        is_lying_down = "lying" in status_str.lower() or "fall" in status_str.lower()
                            
                    if is_lying_down:
                        if USE_SAFETY_CHECK and flags.get("use_safety_check", True):
                            normalized_keypoints = normalize_keypoints(obj.points, img.width(), img.height())
                            is_safe = safety_checker.body_in_safe_zone(normalized_keypoints, SAFETY_CHECK_METHOD)
                            
                            if not is_safe:
                                unsafe_ids.add(track.id)
                            elif track.id in unsafe_ids:
                                unsafe_ids.remove(track.id)
                        else:
                            if track.id in unsafe_ids:
                                unsafe_ids.remove(track.id)

                    # Draw
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

    # Draw safe area polygons
    if USE_SAFETY_CHECK and flags.get("show_safe_area", False):
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
        img.draw_string(10, 10, status_text, color=image.COLOR_RED, scale=0.5)
    
    # Draw operation mode
    mode_text = f"Mode: {OPERATION_MODE}"
    img.draw_string(10, 20, mode_text, color=image.Color.from_rgb(0, 255, 255), scale=0.5)
    
    if REMOTE_ACCESS_MODE:
        access_text = "REMOTE ACCESS"
        img.draw_string(10, 70, access_text, color=image.COLOR_YELLOW, scale=0.5)
    
    disp.show(img)
    
    web_server.send_frame(img)

    if flags["set_background"]:
        web_server.confirm_background(BACKGROUND_PATH)
        web_server.reset_set_background_flag()

# Final cleanup
if is_recording:
    stop_recording()
if mqtt_client:
    mqtt_client.loop_stop()
    mqtt_client.disconnect()