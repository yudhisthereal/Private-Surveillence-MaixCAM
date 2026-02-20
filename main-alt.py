# main-alt.py - PC adaptation of main.py
# Runs on standard Laptop with Webcam using OpenCV and Ultralytics

import os
import sys
import queue
import gc
import time as py_time
import numpy as np
import cv2

# ============================================
# MOCK MAIX MODULE (To prevent hardware access)
# ============================================
from unittest.mock import MagicMock
import types

# Create a module-like object for maix.tracker
tracker_module = types.ModuleType("maix.tracker")

class PCTrackerObject:
    def __init__(self, x, y, w, h, class_id, score):
        self.x = int(x)
        self.y = int(y)
        self.w = int(w)
        self.h = int(h)
        self.class_id = int(class_id)
        self.score = float(score)

class PCTrack:
    def __init__(self, track_id, obj):
        self.id = track_id
        self.lost = False
        self.history = [obj]

class PCByteTracker:
    """Simple pass-through tracker for PC testing"""
    def __init__(self, *args, **kwargs):
        self.tracks = []
        self.next_id = 1
        
    def update(self, objs):
        # Simple ID assignment (no real tracking for now to ensure robustness)
        # Just map input objects to tracks 1:1
        res = []
        for i, obj in enumerate(objs):
            # In a real tracker we would match IDs. 
            # Here we just treat index as ID for this frame (unstable IDs across frames but shows keypoints)
            t = PCTrack(i + 1, obj)
            res.append(t)
        return res

tracker_module.ByteTracker = PCByteTracker
tracker_module.Object = PCTrackerObject

sys.modules["maix"] = MagicMock()
sys.modules["maix.app"] = MagicMock()
sys.modules["maix.app"].need_exit.return_value = False
sys.modules["maix.time"] = MagicMock()
sys.modules["maix.image"] = MagicMock()
sys.modules["maix.display"] = MagicMock()
sys.modules["maix.camera"] = MagicMock()
sys.modules["maix.nn"] = MagicMock()
sys.modules["maix"].tracker = tracker_module

# ============================================
# PATH PATCHING (Must be before importing modules that use these constants)
# ============================================
import config
import control_manager

# Redirect paths from /root/ (MaixCAM) to local directory
config.CAMERA_INFO_FILE = os.path.abspath("./camera_info.json")
config.BACKGROUND_PATH = os.path.abspath("./static/background.jpg")

control_manager.LOCAL_FLAGS_FILE = os.path.abspath("./control_flags.json")
control_manager.BED_AREA_FILE = os.path.abspath("./bed_areas.json")
control_manager.FLOOR_AREA_FILE = os.path.abspath("./floor_areas.json")

# Create necessary directories
os.makedirs("./static", exist_ok=True)
os.makedirs("./recordings", exist_ok=True)
os.makedirs("./extracted-skeleton-2d", exist_ok=True)

# ============================================
# IMPORTS
# ============================================

# Use PC adaptations
import pc_camera_manager as camera_manager
from pc_video_record import VideoRecorder

from debug_config import DebugLogger
# tools.wifi_connect skipped on PC
from tools.skeleton_saver import SkeletonSaver2D
from tools.bed_area_checker import BedAreaChecker
from tools.floor_area_checker import FloorAreaChecker
from tools.polygon_checker import CheckMethod
from tools.chair_area_checker import ChairAreaChecker
from tools.couch_area_checker import CouchAreaChecker
from tools.bench_area_checker import BenchAreaChecker
from tools.safety_judgment import SafetyJudgment

from config import (
    initialize_camera, save_camera_info, STREAMING_HTTP_URL,
    BACKGROUND_PATH, MIN_HUMAN_FRAMES_TO_START, NO_HUMAN_FRAMES_TO_STOP,
    MAX_RECORDING_DURATION_MS, UPDATE_INTERVAL_MS, NO_HUMAN_CONFIRM_FRAMES,
    GC_INTERVAL_MS, NO_HUMAN_SECONDS_TO_STOP
)



# Fix import: camera_manager above is the module pc_camera_manager
# But main.py did: from camera_manager import initialize_cameras...
# Since we imported pc_camera_manager as camera_manager, we can use it.
from pc_camera_manager import initialize_cameras, load_fonts

from control_manager import (
    load_initial_flags, get_control_flags, send_background_updated, update_control_flags_from_server,
    update_control_flag, get_flag, register_status_change_callback,
    initialize_bed_area_checker, update_bed_area_polygons, load_bed_areas,
    initialize_floor_area_checker, update_floor_area_polygons, load_floor_areas,
    initialize_chair_area_checker, update_chair_area_polygons, load_chair_areas,
    initialize_couch_area_checker, update_couch_area_polygons, load_couch_areas,
    initialize_bench_area_checker, update_bench_area_polygons, load_bench_areas,
    CameraStateManager
)
from workers import (
    CameraStateSyncWorker, StateReporterWorker, FrameUploadWorker,
    CommandReceiver, PingWorker, get_received_commands, handle_command,
    update_is_recording, AnalyticsWorker,
    send_track_to_analytics, is_analytics_server_available, set_analytics_queue, TracksSenderWorker,
    set_tracks_worker, update_latest_tracks, mark_tracks_as_ready
)
from tracking import (
    update_tracks, process_track, set_fps
)
from tools.time_utils import time_ms, TaskProfiler, get_timestamp_str

logger = DebugLogger("MAIN", instance_enable=False)

# ============================================
# HELPER FUNCTIONS
# ============================================

def merge_background_with_mask(old_background, new_frame, processed_tracks, padding=20):
    """Merge old background with new frame, masking out areas where humans are present.

    Uses NumPy for efficient array operations (OpenCV Mat is NumPy array).
    Uses keypoints for precise masking: body bbox and head area (ear-nose-ear).

    Args:
        old_background: Existing background image (NumPy array, BGR format)
        new_frame: New raw frame (NumPy array, BGR format)
        processed_tracks: List of track dicts with 'bbox' and 'keypoints'
        padding: Extra padding around body bboxes and head base

    Returns:
        Tuple of (merged_image, mask_visualization)
        - merged_image: Result with old background in human areas, new frame elsewhere
        - mask_visualization: Color-coded mask for display overlay (white=body, red=head)
    """
    import math

    # Start with a copy of the new frame
    merged = new_frame.copy()
    height, width = merged.shape[:2]

    # Create color mask for visualization:
    # Black (0,0,0) = unmasked, White (255,255,255) = body, Red (255,0,0) = head
    mask_vis = np.zeros((height, width, 3), dtype=np.uint8)

    # Create binary mask for merging
    mask_binary = np.zeros((height, width), dtype=np.uint8)

    # For each track, mask body bbox and head area
    for track in processed_tracks:
        bbox = track.get("bbox")
        keypoints = track.get("keypoints")

        if not bbox:
            continue

        x, y, w, h = bbox

        # 1. Mask body bbox with normal padding
        x1 = max(0, int(x - padding))
        y1 = max(0, int(y - padding))
        x2 = min(width, int(x + w + padding))
        y2 = min(height, int(y + h + padding))

        mask_binary[y1:y2, x1:x2] = 255
        # Mark in visualization (white = body)
        mask_vis[y1:y2, x1:x2] = [255, 255, 255]

        # 2. Mask head area (ear-nose-ear) with proportional padding
        if keypoints and len(keypoints) >= 10:
            # Extract nose and ear coordinates
            nose_x, nose_y = keypoints[0], keypoints[1]
            left_ear_x, left_ear_y = keypoints[6], keypoints[7]
            right_ear_x, right_ear_y = keypoints[8], keypoints[9]

            # Check if keypoints are valid (not 0,0 or near it)
            if nose_x > 0 and nose_y > 0:
                # Calculate distances from nose to ears
                nose_to_left_ear_dist = 0
                nose_to_right_ear_dist = 0

                if left_ear_x > 0 and left_ear_y > 0:
                    nose_to_left_ear_dist = math.sqrt((nose_x - left_ear_x)**2 + (nose_y - left_ear_y)**2)

                if right_ear_x > 0 and right_ear_y > 0:
                    nose_to_right_ear_dist = math.sqrt((nose_x - right_ear_x)**2 + (nose_y - right_ear_y)**2)

                # Use the longer distance, proportionally scale padding
                max_ear_nose_dist = max(nose_to_left_ear_dist, nose_to_right_ear_dist)

                # Calculate proportional head padding: base (padding) + (1.5 * longest ear-nose distance)
                if max_ear_nose_dist > 0:
                    head_padding = int(padding + (max_ear_nose_dist * 1.5))
                else:
                    head_padding = padding

                # Calculate head bounding box from ear-nose-ear
                if left_ear_x > 0 and right_ear_x > 0:
                    # Both ears visible, use them for width
                    head_x1 = int(min(left_ear_x, right_ear_x) - head_padding)
                    head_x2 = int(max(left_ear_x, right_ear_x) + head_padding)
                else:
                    # One or both ears not visible, use nose as center
                    head_x1 = int(nose_x - head_padding)
                    head_x2 = int(nose_x + head_padding)

                head_y1 = int(min(nose_y, left_ear_y if left_ear_y > 0 else nose_y, right_ear_y if right_ear_y > 0 else nose_y) - head_padding)
                head_y2 = int(max(nose_y, left_ear_y if left_ear_y > 0 else nose_y, right_ear_y if right_ear_y > 0 else nose_y) + head_padding)

                # Clamp to image bounds
                head_x1 = max(0, head_x1)
                head_y1 = max(0, head_y1)
                head_x2 = min(width, head_x2)
                head_y2 = min(height, head_y2)

                # Set head region in mask
                mask_binary[head_y1:head_y2, head_x1:head_x2] = 255
                # Mark in visualization (red = head)
                mask_vis[head_y1:head_y2, head_x1:head_x2] = [255, 0, 0]

    # Where mask is white (human areas), use old_background
    # Where mask is black (no humans), use new_frame (already set as merged)
    mask_3ch = cv2.cvtColor(mask_binary, cv2.COLOR_GRAY2BGR)
    merged = np.where(mask_3ch == 255, old_background, merged)

    return merged, mask_vis

# ============================================
# MAIN INITIALIZATION
# ============================================


# Global camera state manager instance
camera_state_manager = CameraStateManager()

def main():
    """Main entry point"""
    # 1. Initialize camera identity
    CAMERA_ID, CAMERA_NAME, registration_status, local_ip = initialize_camera()

    # Debug rendering toggles
    debug_render_flags = {
        "show_bg_mask": False,
        "show_skeleton": False,
        "show_skeleton": False,
        "show_bed_areas": False,
        "show_floor_areas": False,
        "show_labels": False,
        "show_bbox": False
    }
    
    # 2. Connect to WiFi (Skipped on PC)
    server_ip = local_ip
    logger.print("MAIN", "PC IP: %s", server_ip)
    
    # 3. Initialize cameras and detectors
    cam, disp, pose_extractor, detector = initialize_cameras()
    load_fonts()
    
    # 4. Initialize tools
    recorder = VideoRecorder()
    skeleton_saver_2d = SkeletonSaver2D()
    skeleton_saver_2d.log_dir = os.path.abspath("./extracted-skeleton-2d") # Patch path
    
    # 5. Initialize area checkers
    # 5. Initialize area checkers
    bed_area_checker = BedAreaChecker(too_long_threshold_ms=10000)
    initialize_bed_area_checker(bed_area_checker)

    floor_area_checker = FloorAreaChecker()
    initialize_floor_area_checker(floor_area_checker)

    chair_area_checker = ChairAreaChecker()
    initialize_chair_area_checker(chair_area_checker)

    couch_area_checker = CouchAreaChecker()
    initialize_couch_area_checker(couch_area_checker)

    bench_area_checker = BenchAreaChecker()
    initialize_bench_area_checker(bench_area_checker)

    # 6. Load initial configuration
    logger.print("MAIN", "=== Loading Configuration ===")
    load_initial_flags()
    control_flags = get_control_flags()  # Get loaded flags including check_method
    
    # Initialize SafetyJudgment with all area checkers
    # Get check_method from control_flags (default: 3 = TORSO_HEAD)
    check_method_value = control_flags.get("check_method", 3)

    # Map integer value to CheckMethod enum
    check_method_map = {
        1: CheckMethod.HIP,
        2: CheckMethod.TORSO,
        3: CheckMethod.TORSO_HEAD,
        4: CheckMethod.TORSO_HEAD_KNEES,
        5: CheckMethod.FULL_BODY
    }
    check_method = check_method_map.get(check_method_value, CheckMethod.TORSO_HEAD)

    safety_judgment = SafetyJudgment(
        bed_area_checker=bed_area_checker,
        floor_area_checker=floor_area_checker,
        chair_area_checker=chair_area_checker,
        couch_area_checker=couch_area_checker,
        bench_area_checker=bench_area_checker,
        check_method=check_method
    )



    initial_bed_areas = load_bed_areas()
    update_bed_area_polygons(initial_bed_areas)

    initial_floor_areas = load_floor_areas()
    update_floor_area_polygons(initial_floor_areas)

    initial_chair_areas = load_chair_areas()
    update_chair_area_polygons(initial_chair_areas)

    initial_couch_areas = load_couch_areas()
    update_couch_area_polygons(initial_couch_areas)

    initial_bench_areas = load_bench_areas()
    update_bench_area_polygons(initial_bench_areas)
    
    # 7. Load or create background image
    background_img = None
    if os.path.exists(BACKGROUND_PATH):
        background_img = cv2.imread(BACKGROUND_PATH)
        logger.print("MAIN", "Loaded background image from file")
    else:
        # Initial read to set background
        background_img = cam.read()
        cv2.imwrite(BACKGROUND_PATH, background_img)
        logger.print("MAIN", "Created new background image")
    
    # ============================================
    # ASYNC WORKERS SETUP
    # ============================================
    flags_queue = queue.Queue(maxsize=10)
    bed_areas_queue = queue.Queue(maxsize=5)
    floor_areas_queue = queue.Queue(maxsize=5)
    chair_areas_queue = queue.Queue(maxsize=5)
    couch_areas_queue = queue.Queue(maxsize=5)
    bench_areas_queue = queue.Queue(maxsize=5)
    analytics_queue = queue.Queue(maxsize=20)  # Queue for analytics worker
    tracks_queue = queue.Queue(maxsize=30)  # Queue for tracks sender

    # Start command server (might conflict on port 8080 if running on PC, but we'll try)
    command_receiver = CommandReceiver()
    command_receiver.start()

    # Start async workers
    logger.print("MAIN", "Starting async workers...")
    flag_sync_worker = CameraStateSyncWorker(
        flags_queue, STREAMING_HTTP_URL, CAMERA_ID,
        bed_areas_queue=bed_areas_queue, floor_areas_queue=floor_areas_queue,
        chair_areas_queue=chair_areas_queue, couch_areas_queue=couch_areas_queue,
        bench_areas_queue=bench_areas_queue
    )
    state_reporter_worker = StateReporterWorker(STREAMING_HTTP_URL, CAMERA_ID)
    frame_upload_worker = FrameUploadWorker(STREAMING_HTTP_URL, CAMERA_ID, profiler_enabled=True)
    ping_worker = PingWorker(STREAMING_HTTP_URL, CAMERA_ID)
    
    flag_sync_worker.start()
    state_reporter_worker.start()
    frame_upload_worker.start()
    ping_worker.start()
    
    # Start Analytics Worker (only if analytics_mode is True)
    analytics_worker = None
    if get_flag("analytics_mode", False):
        # Register the analytics queue with the workers module
        set_analytics_queue(analytics_queue)
        analytics_worker = AnalyticsWorker(analytics_queue, CAMERA_ID)
        analytics_worker.start()
        logger.print("MAIN", "[Analytics] Worker started (analytics_mode=True)")
    else:
        logger.print("MAIN", "[Analytics] Worker NOT started (analytics_mode=False)")
    
    # Start Tracks Sender Worker
    tracks_sender = TracksSenderWorker(CAMERA_ID, profiler_enabled=False)
    set_tracks_worker(tracks_sender)
    tracks_sender.start()
    logger.print("MAIN", "[TracksSender] Worker started")
    
    logger.print("MAIN", "Async workers started successfully")
    
    # Register callback for registration status changes
    def on_registration_status_changed(new_status):
        """Callback to handle registration status changes"""
        nonlocal registration_status
        old_status = registration_status
        registration_status = new_status
        logger.print("MAIN", "[StatusChange] Registration status changed: %s -> %s", old_status, new_status)
        
        # If camera was approved, save the updated info
        if new_status == "registered" and old_status == "pending":
            save_camera_info(CAMERA_ID, CAMERA_NAME, "registered", local_ip)
    
    register_status_change_callback(on_registration_status_changed)
    
    # ============================================
    # STATE VARIABLES
    # ============================================
    frame_id = 0
    human_presence_history = []
    recording_start_time = 0
    is_recording = False
    background_update_in_progress = False
    background_update_needed = False  # Flag for deferred background update with masking
    mask_vis = None  # Store mask visualization for display
    prev_human_present = False
    no_human_counter = 0
    last_update_ms = time_ms()
    last_gc_time = time_ms()
    streaming_server_available = True
    frame_profiler = TaskProfiler(task_name="Main", enabled=True)
    frame_profiler.register_subtasks([
        "async_updates",
        "commands",
        "background_check",
        "human detect",
        "display_prep",
        "pose_extraction",
        "tracking",
        "recording",
        "display",
        "frame_upload"
    ])
    frame_start_time = time_ms()
    
    # caches last known track, will only be cleared after `cached_tracks_timeout` of no tracks detected.
    # is used for selective background updates
    cached_tracks = None
    cached_tracks_timeout = 3000 # 3s
    cached_tracks_last_updated = 0
    
    # ============================================
    # MAIN LOOP
    # ============================================
    frame_counter = 0
    
    logger.print("MAIN", "=== Camera Stream Started (PC) ===")
    logger.print("MAIN", "Press 'q' or 'ESC' in the display window to exit.")
    
    control_flags = get_control_flags()

    # NOTE: This sets show_raw locally but the server sync may override it.
    # If you want to force show_raw=True on PC, you need to also set it on the streaming server.
    control_flags['show_raw'] = True
    update_control_flag('show_raw', True)

    logger.print("MAIN", "Final Configuration:")
    logger.print("MAIN", "  Camera: %s (%s)", CAMERA_NAME, CAMERA_ID)
    logger.print("MAIN", "  Status: %s", registration_status)
    logger.print("MAIN", "  Analytics Mode: %s", 'ENABLED' if control_flags.get('analytics_mode') else 'DISABLED')
    logger.print("MAIN", "  Recording: %s", 'ENABLED' if control_flags.get('record') else 'DISABLED')
    
    # Upload background image to server at startup
    if streaming_server_available and background_img is not None:
        try:
            # OpenCV JPEG encoding
            success, jpeg_bytes = cv2.imencode('.jpg', background_img, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
            if success:
                frame_upload_worker.update_background(jpeg_bytes.tobytes())
                logger.print("MAIN", "[BACKGROUND] Background queued for upload at startup")
        except Exception as e:
            logger.print("MAIN", "[BACKGROUND] Failed to queue background for upload at startup: %s", e)
    
    while True:
        # Start frame profiling
        frame_profiler.start_frame()
        
        # 1. Process async updates from workers
        frame_profiler.start_task("async_updates")
        try:
            while not flags_queue.empty():
                msg_type, data = flags_queue.get_nowait()
                if msg_type == "flags_update":
                    flags_updated = update_control_flags_from_server(data)
                    if flags_updated:
                        streaming_server_available = True
        except queue.Empty:
            pass



        try:
            while not bed_areas_queue.empty():
                msg_type, data = bed_areas_queue.get_nowait()
                if msg_type == "bed_areas_update":
                    if isinstance(data, list):
                        update_bed_area_polygons(data)
        except queue.Empty:
            pass

        try:
            while not floor_areas_queue.empty():
                msg_type, data = floor_areas_queue.get_nowait()
                if msg_type == "floor_areas_update":
                    if isinstance(data, list):
                        update_floor_area_polygons(data)
        except queue.Empty:
            pass

        try:
            while not chair_areas_queue.empty():
                msg_type, data = chair_areas_queue.get_nowait()
                if msg_type == "chair_areas_update":
                    if isinstance(data, list):
                        update_chair_area_polygons(data)
        except queue.Empty:
            pass

        try:
            while not couch_areas_queue.empty():
                msg_type, data = couch_areas_queue.get_nowait()
                if msg_type == "couch_areas_update":
                    if isinstance(data, list):
                        update_couch_area_polygons(data)
        except queue.Empty:
            pass

        try:
            while not bench_areas_queue.empty():
                msg_type, data = bench_areas_queue.get_nowait()
                if msg_type == "bench_areas_update":
                    if isinstance(data, list):
                        update_bench_area_polygons(data)
        except queue.Empty:
            pass
        frame_profiler.end_task("async_updates")
        
        # 2. Process received commands
        frame_profiler.start_task("commands")
        commands = get_received_commands()
        for cmd_data in commands:
            handle_command(
                cmd_data.get("command"), 
                cmd_data.get("value"),
                CAMERA_ID,
                registration_status,
                lambda cid, cname, status, ip: save_camera_info(cid, cname, status, ip)
            )
            if cmd_data.get("command") == "approve_camera":
                camera_state_manager.set_registration_status("registered")
        frame_profiler.end_task("commands")
        
        # 3. Camera Read
        raw_img = cam.read()
        if raw_img is None:
            break
            
        # 4. Check for background update request
        frame_profiler.start_task("background_check")
        if get_flag("set_background", False) and not background_update_in_progress and not get_flag("_background_update_pending", False):
            logger.print("MAIN", "[BACKGROUND] Starting background update...")
            background_img = raw_img.copy()
            cv2.imwrite(BACKGROUND_PATH, background_img)
            background_update_in_progress = True
            update_control_flag("set_background", False)
            # Upload background image to server via FrameUploadWorker
            if streaming_server_available:
                try:
                    success, jpeg_bytes = cv2.imencode('.jpg', background_img, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                    if success:
                        frame_upload_worker.update_background(jpeg_bytes.tobytes())
                        logger.print("MAIN", "[BACKGROUND] Background queued for upload to server")
                except Exception as e:
                    logger.print("MAIN", "[BACKGROUND] Failed to queue background for upload: %s", e)
            send_background_updated(py_time.time())
            background_update_in_progress = False
            logger.print("MAIN", "[BACKGROUND] Background updated")
        frame_profiler.end_task("background_check")
        
        # 5. Pose extraction (Moved up to replace distinct human detection)
        frame_profiler.start_task("pose_extraction")
        objs = pose_extractor.detect(raw_img, conf_th=0.5, iou_th=0.45, keypoint_th=0.5)
        pose_human_present = len(objs) > 0
        
        current_human_present = pose_human_present
        frame_profiler.end_task("pose_extraction")

        # 5b. Auto background update logic (using pose detection results)
        if get_flag("auto_update_bg", False):
            if prev_human_present and not current_human_present:
                no_human_counter += 1
                if no_human_counter >= NO_HUMAN_CONFIRM_FRAMES:
                    # No humans present, can update background immediately
                    background_img = raw_img.copy()
                    cv2.imwrite(BACKGROUND_PATH, background_img)
                    # Upload background image to server via FrameUploadWorker
                    if streaming_server_available:
                        try:
                            success, jpeg_bytes = cv2.imencode('.jpg', background_img, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                            if success:
                                frame_upload_worker.update_background(jpeg_bytes.tobytes())
                                logger.print("MAIN", "[BACKGROUND] Auto-update background queued for upload (no humans)")
                        except Exception as e:
                            logger.print("MAIN", "[BACKGROUND] Auto-update failed to queue background: %s", e)
                    last_update_ms = time_ms()
                    no_human_counter = 0
            else:
                no_human_counter = 0
                if time_ms() - last_update_ms > UPDATE_INTERVAL_MS:
                    # Periodic update - defer to after tracking so we can mask out humans
                    background_update_needed = True
                    logger.print("MAIN", "[BACKGROUND] Deferred background update scheduled (will mask human areas)")
            prev_human_present = current_human_present
        
        # 6. Prepare display image (no UI rendering)
        frame_profiler.start_task("display_prep")
        if get_flag("show_raw", False):
            img = raw_img.copy()
        else:
            img = background_img.copy() if background_img is not None else raw_img.copy()
        frame_profiler.end_task("display_prep")
        
        # 7. Pose extraction and tracking (Pose already done above)
        # frame_profiler.start_task("pose_extraction")
        # objs = pose_extractor.detect(raw_img, conf_th=0.5, iou_th=0.45, keypoint_th=0.5)
        # pose_human_present = len(objs) > 0
        human_present = current_human_present # or pose_human_present (same thing now)
        
        human_presence_history.append(human_present)
        if len(human_presence_history) > NO_HUMAN_FRAMES_TO_STOP + 1:
            human_presence_history.pop(0)
        
        # Recording logic
        record_flag = get_flag("record", False)
        now = time_ms()
        
        if record_flag and not is_recording:
            if (len(human_presence_history) >= MIN_HUMAN_FRAMES_TO_START and 
                all(human_presence_history[-MIN_HUMAN_FRAMES_TO_START:])):
                timestamp = get_timestamp_str()
                # Use local recordings path
                video_path = os.path.abspath(f"./recordings/{timestamp}.mp4")
                recorder.start(video_path, pose_extractor.input_width(), pose_extractor.input_height())
                skeleton_saver_2d.start_new_log(timestamp)
                frame_id = 0
                recording_start_time = now
                is_recording = True
                update_is_recording(True)
                logger.print("MAIN", "Started recording: %s", timestamp)
        
        if is_recording:
            no_human_count = 0
            for presence in reversed(human_presence_history):
                if not presence:
                    no_human_count += 1
                else:
                    break
            
            no_human_frames_to_stop = NO_HUMAN_SECONDS_TO_STOP * 60
            
            if (no_human_count >= no_human_frames_to_stop or 
                now - recording_start_time >= MAX_RECORDING_DURATION_MS or
                not record_flag):
                recorder.end()
                skeleton_saver_2d.save_to_csv()
                is_recording = False
                update_is_recording(False)
                logger.print("MAIN", "Stopped recording")
        
        # 8. Process tracking and collect all tracks
        frame_profiler.start_task("tracking")
        tracks = update_tracks(objs)

        if is_recording:
            frame_id += 1

        # Calculate FPS
        frame_end_time = time_ms()
        frame_duration = frame_end_time - frame_start_time
        current_fps = 1000.0 / frame_duration if frame_duration > 0 else 30.0
        set_fps(current_fps)
        frame_start_time = frame_end_time
        
        processed_tracks = []
        
        for track in tracks:
            track_result = process_track(
                track, objs, CAMERA_ID,
                is_recording=is_recording,
                skeleton_saver=skeleton_saver_2d if is_recording else None,
                frame_id=frame_id,
                fps=current_fps,
                analytics_mode=get_flag("analytics_mode", False),
                safety_judgment=safety_judgment
            )
            
            if not track_result:
                continue
            
            # Draw tracks on img for display visualization (PC only feature)
            # Draw skeleton
            # COCO Keypoints: 0:nose, 1:left_eye, 2:right_eye, 3:left_ear, 4:right_ear, 5:left_shoulder, 
            # 6:right_shoulder, 7:left_elbow, 8:right_elbow, 9:left_wrist, 10:right_wrist, 
            # 11:left_hip, 12:right_hip, 13:left_knee, 14:right_knee, 15:left_ankle, 16:right_ankle
            connections = [
                (0,1), (0,2), (1,3), (2,4), (5,6), (5,7), (7,9), (6,8), (8,10), 
                (5,11), (6,12), (11,12), (11,13), (13,15), (12,14), (14,16)
            ]
            
            # Keypoints are flattened [x1, y1, x2, y2, ...]
            pts = []
            if track_result.get("keypoints"):
                kp_flat = track_result.get("keypoints")
                for i in range(0, len(kp_flat), 2):
                    pts.append((int(kp_flat[i]), int(kp_flat[i+1])))
                
                # Draw connections and points if skeleton is enabled
                if debug_render_flags["show_skeleton"]:
                    # Draw connections
                    for p1, p2 in connections:
                        if p1 < len(pts) and p2 < len(pts):
                            pt1 = pts[p1]
                            pt2 = pts[p2]
                            if pt1 != (0,0) and pt2 != (0,0):
                                cv2.line(img, pt1, pt2, (0, 255, 0), 2)
                    
                    # Prepare polygon contours for point testing
                    h, w = img.shape[:2]
                    
                    def to_contours(polygons):
                        contours = []
                        if polygons:
                            for poly in polygons:
                                if len(poly) >= 3:
                                    cnt = np.array([[int(p[0] * w), int(p[1] * h)] for p in poly], np.int32)
                                    contours.append(cnt)
                        return contours

                    bed_contours = to_contours(bed_area_checker.bed_polygons)
                    floor_contours = to_contours(floor_area_checker.floor_polygons)

                    # Draw points with color coding
                    for pt in pts:
                        if pt != (0,0):
                            color = (0, 0, 255) # Default Red
                            
                            # Priority: Floor > Bed
                            
                            # Check Floor
                            is_floor = False
                            for cnt in floor_contours:
                                if cv2.pointPolygonTest(cnt, pt, False) >= 0:
                                    is_floor = True
                                    break
                            
                            if is_floor:
                                color = (0, 0, 139) # Dark Red for Floor
                            else:
                                # Check Bed
                                is_bed = False
                                for cnt in bed_contours:
                                    if cv2.pointPolygonTest(cnt, pt, False) >= 0:
                                        is_bed = True
                                        break
                                
                                if is_bed:
                                    color = (139, 0, 0) # Dark Blue

                            
                            cv2.circle(img, pt, 4, color, -1)

            # Draw bounding box and label
            safety_reason = track_result.get("safety_reason", "normal")
            if track_result.get("bbox") and debug_render_flags["show_bbox"]:
                nx, ny, nw, nh = track_result.get("bbox")
                
                if debug_render_flags["show_labels"]:
                    cv2.putText(img, f"ID: {track_result.get('track_id')} {track_result.get('pose_label')} [{track_result.get('status')}]", 
                            (int(nx), int(ny)-10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
                    
                    # Render safety reason if unsafe
                    if safety_reason != "normal":
                        cv2.putText(img, f"Reason: {safety_reason}", 
                            (int(nx), int(ny)-30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
                    
                    # Render bbox top y coord
                    cv2.putText(img, f"y_top: {int(ny)}", 
                        (int(nx), int(ny)-50), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 2)
            
            logger.print("MAIN", "pose_label <- track_result['pose_label']: %s", track_result.get('pose_label', 'unknown'))
            track_id = track_result.get("track_id")
            keypoints = track_result.get("keypoints")
            bbox = track_result.get("bbox")
            pose_label = track_result.get("pose_label", "unknown")
            status = track_result.get("status", "tracking")
            safety_status = "normal" if status == "tracking" else status
            # safety_reason already extracted above
            encrypted_features = track_result.get("encrypted_features")

            processed_track = {
                "track_id": track_id,
                "keypoints": keypoints,
                "bbox": bbox,
                "pose_label": pose_label,
                "safety_status": safety_status,
                "safety_reason": safety_reason,
                "encrypted_features": encrypted_features
            }
            processed_tracks.append(processed_track)

        # Send tracks to queue and mark them as ready for sending
        update_latest_tracks(processed_tracks)
        mark_tracks_as_ready()
        
        # Cleanup cached_tracks if timeout is hit after the last cache update
        if cached_tracks:
            if time_ms() - cached_tracks_last_updated > cached_tracks_timeout:
                cached_tracks = None    

        # Deferred selective background update with masking (after we have track bboxes)
        if background_update_needed and background_img is not None:
            try:
                # Extract bounding boxes from processed tracks
                # Get valid tracks (have bbox and keypoints)
                valid_tracks = [t for t in processed_tracks if t.get("bbox") and t.get("keypoints")]
                
                if valid_tracks:
                    # Merge background with new frame, masking out human areas
                    logger.print("MAIN", "[BACKGROUND] Updating background with %d masked track(s)", len(valid_tracks))
                    new_background, mask_vis = merge_background_with_mask(background_img, raw_img, valid_tracks, padding=20)
                    
                    # Update cached_tracks (and reset `timer`) if valid_track exist
                    cached_tracks_last_updated = time_ms()
                    cached_tracks = valid_tracks
                elif cached_tracks:
                    # No valid tracks for the current frame, but the tracks cache is still available
                    # Workaround for "person(/people) exist in the frame but isn't being detected by the pose extractor"
                    logger.print("MAIN", "[BACKGROUND] Updating background with %d cached track(s)", len(cached_tracks))
                    new_background, mask_vis = merge_background_with_mask(background_img, raw_img, cached_tracks, padding=20)
                else:
                    # No tracks, just use the raw frame
                    logger.print("MAIN", "[BACKGROUND] No tracks to mask, using raw frame")
                    new_background = raw_img.copy()
                    mask_vis = None

                # Save and upload the new background
                background_img = new_background
                cv2.imwrite(BACKGROUND_PATH, background_img)

                if streaming_server_available:
                    try:
                        success, jpeg_bytes = cv2.imencode('.jpg', background_img, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                        if success:
                            frame_upload_worker.update_background(jpeg_bytes.tobytes())
                            logger.print("MAIN", "[BACKGROUND] Auto-update background queued for upload")
                    except Exception as e:
                        logger.print("MAIN", "[BACKGROUND] Auto-update failed to queue background: %s", e)

                last_update_ms = time_ms()
            except Exception as e:
                logger.print("MAIN", "[BACKGROUND] Error during masked background update: %s", e)
                mask_vis = None
            finally:
                background_update_needed = False

        frame_profiler.end_task("tracking")
        
        # 10. Recording
        frame_profiler.start_task("recording")
        if is_recording:
            recorder.add_frame(img)
        frame_profiler.end_task("recording")
        
        # 11. Display
        frame_profiler.start_task("display")

        # Overlay mask visualization if available (blend with 30% opacity)
        display_img = img.copy()
        if mask_vis is not None and debug_render_flags["show_bg_mask"]:
            # Resize mask to match display size if needed
            if mask_vis.shape[:2] != display_img.shape[:2]:
                mask_vis = cv2.resize(mask_vis, (display_img.shape[1], display_img.shape[0]))
            # Blend mask with 30% opacity
            display_img = cv2.addWeighted(display_img, 0.7, mask_vis, 0.3, 0)

        # Draw Areas (Safe, Bed, Floor) overlay
        h_disp, w_disp = display_img.shape[:2]
        overlay_areas = display_img.copy()
        
        areas_to_draw = []
        if debug_render_flags["show_bed_areas"]:
            areas_to_draw.append((bed_area_checker.bed_polygons, (255, 0, 0), "Bed"))
        if debug_render_flags["show_floor_areas"]:
            areas_to_draw.append((floor_area_checker.floor_polygons, (0, 0, 255), "Floor"))
        
        for polygons, color, label in areas_to_draw:
            if polygons:
                for poly in polygons:
                    if len(poly) >= 3:
                        pts = np.array([[int(p[0] * w_disp), int(p[1] * h_disp)] for p in poly], np.int32)
                        pts = pts.reshape((-1, 1, 2))
                        cv2.fillPoly(overlay_areas, [pts], color)
                        cv2.polylines(display_img, [pts], True, color, 2)
                        # Label
                        M = cv2.moments(pts)
                        if M["m00"] != 0:
                            cX = int(M["m10"] / M["m00"])
                            cY = int(M["m01"] / M["m00"])
                            cv2.putText(display_img, label, (cX - 20, cY), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

        # Blend overlays (20% opacity)
        cv2.addWeighted(overlay_areas, 0.2, display_img, 0.8, 0, display_img)

        # Upscale for display (make it bigger for the user)
        # Original is 320x224, scale by 3x -> 960x672
        display_scale = 3
        display_img_large = cv2.resize(display_img, (0, 0), fx=display_scale, fy=display_scale, interpolation=cv2.INTER_NEAREST)
        disp.show(display_img_large)
        
        # Check for user exit - Ensure waitKey is called here if disp.show didn't do it enough
        # Increase wait time to 10ms to process UI events better and cap FPS reasonably
        key = cv2.waitKey(10) & 0xFF
        if key in [ord('q'), 27]: # q or ESC
            logger.print("MAIN", "Exit requested by user")
            sys.modules["maix.app"].need_exit.return_value = True # Signal exit
            break
        frame_profiler.end_task("display")
        
        # 12. Update FrameUploadWorker with latest RAW frame (no overlays)
        # Always send raw frame regardless of show_raw flag
        frame_profiler.start_task("frame_upload")
        try:
            success, jpeg_bytes = cv2.imencode('.jpg', raw_img, [int(cv2.IMWRITE_JPEG_QUALITY), 60])
            if success:
                frame_upload_worker.update_frame(jpeg_bytes.tobytes())
        except Exception:
            pass
        frame_profiler.end_task("frame_upload")
        
        # End frame profiling
        frame_profiler.end_frame()
        
        # 13. Periodic GC
        current_time = time_ms()
        if current_time - last_gc_time > GC_INTERVAL_MS:
            gc.collect()
            last_gc_time = current_time
        
        frame_counter += 1
        if frame_counter % 30 == 0:
            logger.print("MAIN", "Frame %d processed (FPS: %.1f)", frame_counter, current_fps)
            if frame_counter % 60 == 0:
                control_flags = get_control_flags()
                logger.print("MAIN", "[STATUS] Flags: record=%s, analytics_mode=%s, fall_algorithm=%s",
                          control_flags.get('record'),
                          control_flags.get('analytics_mode'),
                          control_flags.get('fall_algorithm'))

    # ============================================
    # CLEANUP
    # ============================================
    command_receiver.stop()
    flag_sync_worker.stop()
    state_reporter_worker.stop()
    frame_upload_worker.stop()
    ping_worker.stop()
    tracks_sender.stop()
    
    if is_recording:
        recorder.end()
        skeleton_saver_2d.save_to_csv()
    
    from control_manager import save_control_flags
    save_control_flags()
    
    # Cleanup OpenCV
    cam.cap.release()
    cv2.destroyAllWindows()
    
    logger.print("MAIN", "=== Camera Stream Stopped ===")

if __name__ == "__main__":
    main()
