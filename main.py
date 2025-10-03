from maix import camera, display, app, nn, image, time, tracker
from pose.pose_estimation import PoseEstimation
from tools.wifi_connect import connect_wifi
from tools.video_record import VideoRecorder
from tools.time_utils import get_timestamp_str
from tools.skeleton_saver import SkeletonSaver2D
from pose.judge_fall import get_fall_info
from tools.web_server import web_server  # Import the web_server instance
from tools.safe_area import BodySafetyChecker, CheckMethod  # Import the safe area classes
import queue
import numpy as np
import os
import json

# Image Paths
BACKGROUND_PATH = "/root/static/background.jpg"
SAFE_AREA_FILE = "/root/safe_areas.json"

# Wi-Fi Setup
SSID = "ROBOTIIK"
PASSWORD = "81895656"
server_ip = connect_wifi(SSID, PASSWORD)

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
frame_id = 0  # the frame ID of the current recording

# Recording parameters
MIN_HUMAN_FRAMES_TO_START = 3  # Start recording after 3 frames with human presence
NO_HUMAN_FRAMES_TO_STOP = 30   # Stop recording after 30 frames without human presence
MAX_RECORDING_DURATION_MS = 90000  # 90 seconds max recording duration

# Recording state variables
human_presence_history = []  # Track human presence for last few frames
recording_start_time = 0
is_recording = False

# Background update settings
UPDATE_INTERVAL_MS = 10000  # 10 seconds
NO_HUMAN_CONFIRM_FRAMES = 10
STEP = 8  # Step size for background update

# Background state variables
background_img = None
prev_human_present = False
no_human_counter = 0
last_update_ms = time.ticks_ms()

# Initialize Body Safety Checker
safety_checker = BodySafetyChecker()
SAFETY_CHECK_METHOD = CheckMethod.TORSO_HEAD  # Choose from: HIP, TORSO, TORSO_HEAD, TORSO_HEAD_KNEES, FULL_BODY
USE_SAFETY_CHECK = True  # Enable/disable safety checking

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
        # If odd number, assume last element is confidence or discard it
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
        if x > 0 and y > 0:  # Valid keypoint
            x_norm = x / img_width
            y_norm = y / img_height
            normalized.append((x_norm, y_norm, 1.0))  # Default confidence
        else:
            normalized.append((0.0, 0.0, 0.0))  # Invalid keypoint
    
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
unsafe_ids = set()  # Track persons not in safe area

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
    # Capture initial background
    background_img = cam.read().copy()
    background_img.save(BACKGROUND_PATH)

# === Main loop ===
while not app.need_exit():
    raw_img = cam.read()
    flags = web_server.get_control_flags()

    # Check for safe areas updates (only process if there are updates)
    if web_server.safe_areas_have_updates():
        # The callback will automatically update the safety checker
        pass

    # Run segmentation for background updates and human detection
    objs_seg = segmentor.detect(raw_img, conf_th=0.5, iou_th=0.45)
    current_human_present = any(segmentor.labels[obj.class_id] in ["person", "human"] for obj in objs_seg)
    bg_updated = False

    # Background update logic - only if auto_update_bg is enabled
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
        # Reset counters when auto-update is disabled
        no_human_counter = 0
        prev_human_present = current_human_present

    # Prepare display image
    if flags["show_raw"]:
        img = raw_img.copy()
    else:
        if background_img is not None:
            img = background_img.copy()
        else:
            img = raw_img.copy()  # fallback

    # Pose detection and tracking
    objs = detector.detect(raw_img, conf_th=0.5, iou_th=0.45, keypoint_th=0.5)
    
    # Check for human presence from pose detector as well
    pose_human_present = len(objs) > 0
    human_present = current_human_present or pose_human_present
    
    # Update human presence history (keep last NO_HUMAN_FRAMES_TO_STOP + 1 frames)
    human_presence_history.append(human_present)
    if len(human_presence_history) > NO_HUMAN_FRAMES_TO_STOP + 1:
        human_presence_history.pop(0)
    
    # Smart recording logic
    if flags["record"]:
        now = time.ticks_ms()
        
        # Check if we should start recording
        if not is_recording:
            # Start if we have human presence for MIN_HUMAN_FRAMES_TO_START consecutive frames
            # Or if we just ended a recording and human is still present
            if (len(human_presence_history) >= MIN_HUMAN_FRAMES_TO_START and 
                all(human_presence_history[-MIN_HUMAN_FRAMES_TO_START:])):
                start_new_recording()
        
        # Check if we should stop recording
        if is_recording:
            # Stop if no human for NO_HUMAN_FRAMES_TO_STOP consecutive frames
            no_human_count = 0
            for presence in reversed(human_presence_history):
                if not presence:
                    no_human_count += 1
                else:
                    break
            
            if (no_human_count >= NO_HUMAN_FRAMES_TO_STOP or 
                now - recording_start_time >= MAX_RECORDING_DURATION_MS):
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

                    # Call fall detection when queue is full
                    if online_targets["bbox"][idx].qsize() == queue_size:
                        if get_fall_info(tracker_obj, online_targets, idx, fallParam, queue_size, fps):
                            fall_ids.add(track.id)
                        elif track.id in fall_ids:
                            fall_ids.remove(track.id)

                    # Safety area check - ONLY when person is lying down
                    status_str = pose_estimator.evaluate_pose(keypoints_np)
                    # is_lying_down = False
                    # if status_str:
                    #     is_lying_down = "lying" in status_str.lower() or "fall" in status_str.lower()
                    
                    if USE_SAFETY_CHECK and flags.get("use_safety_check", True):
                        # Normalize keypoints for safe area checking
                        normalized_keypoints = normalize_keypoints(obj.points, img.width(), img.height())
                        
                        # Check if person is in safe area
                        is_safe = safety_checker.body_in_safe_zone(normalized_keypoints, SAFETY_CHECK_METHOD)
                        
                        if not is_safe:
                            unsafe_ids.add(track.id)
                        elif track.id in unsafe_ids:
                            unsafe_ids.remove(track.id)
                    else:
                        # If not lying down or safety check disabled, remove from unsafe_ids
                        if track.id in unsafe_ids:
                            unsafe_ids.remove(track.id)

                    # Draw
                    # Determine status and color based on fall detection and safety check
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
                        # Save safety status: 0=normal, 1=fall, 2=unsafe
                        safety_status = 1 if track.id in fall_ids else (2 if track.id in unsafe_ids else 0)
                        skeleton_saver_2d.add_keypoints(frame_id, track.id, obj.points, safety_status)
                    
                    break  # no need to check other objs

    # Draw safe area polygons for visualization (if enabled)
    if USE_SAFETY_CHECK and flags.get("show_safe_area", False):
        for polygon in safety_checker.safe_polygons:
            points = []
            for x_norm, y_norm in polygon:
                x_pixel = int(x_norm * img.width())
                y_pixel = int(y_norm * img.height())
                points.append((x_pixel, y_pixel))
            
            # Draw polygon
            for i in range(len(points)):
                start_point = points[i]
                end_point = points[(i + 1) % len(points)]
                img.draw_line(start_point[0], start_point[1], 
                            end_point[0], end_point[1], 
                            color=image.COLOR_BLUE, thickness=2)

    if is_recording:
        recorder.add_frame(img)
    
    # Draw recording status on display
    if is_recording:
        recording_time = (time.ticks_ms() - recording_start_time) // 1000
        status_text = f"REC {recording_time}s"
        img.draw_string(10, 10, status_text, color=image.COLOR_RED, scale=0.5)
    
    disp.show(img)
    
    web_server.send_frame(img)

    flags = web_server.get_control_flags()
    
    # Manual recording control from web interface
    if flags["record"] and not is_recording and not recorder.is_active:
        # Manual start - check if we should start immediately
        if human_present:
            start_new_recording()
    elif not flags["record"] and is_recording:
        # Manual stop
        stop_recording()

    if flags["set_background"]:
        web_server.confirm_background(BACKGROUND_PATH)
        web_server.reset_set_background_flag()

# Final cleanup
if is_recording:
    stop_recording()