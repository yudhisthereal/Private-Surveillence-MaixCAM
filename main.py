# main.py - Modularized main entry point for Private CCTV System

from maix import app, image, time
from debug_config import DebugLogger
from tools.wifi_connect import connect_wifi
from tools.video_record import VideoRecorder
from tools.skeleton_saver import SkeletonSaver2D
from tools.safe_area import SafeAreaChecker, CheckMethod
from tools.bed_area_checker import BedAreaChecker
from tools.floor_area_checker import FloorAreaChecker
from tools.safety_judgment import SafetyJudgment

# Import modular components
from config import (
    initialize_camera, save_camera_info, STREAMING_HTTP_URL,
    BACKGROUND_PATH, MIN_HUMAN_FRAMES_TO_START, NO_HUMAN_FRAMES_TO_STOP,
    MAX_RECORDING_DURATION_MS, UPDATE_INTERVAL_MS, NO_HUMAN_CONFIRM_FRAMES,
    GC_INTERVAL_MS, NO_HUMAN_SECONDS_TO_STOP
)
from camera_manager import (
    initialize_cameras, load_fonts,
)
from control_manager import (
    load_initial_flags, get_control_flags, send_background_updated, update_control_flags_from_server,
    update_control_flag, get_flag, initialize_safety_checker,
    update_safety_checker_polygons, load_safe_areas, camera_state_manager, register_status_change_callback,
    initialize_bed_area_checker, update_bed_area_polygons, load_bed_areas,
    initialize_floor_area_checker, update_floor_area_polygons, load_floor_areas
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

import os
import queue
import gc

logger = DebugLogger("MAIN", instance_enable=False)

# ============================================
# MAIN INITIALIZATION
# ============================================

def main():
    """Main entry point"""
    # 1. Initialize camera identity
    CAMERA_ID, CAMERA_NAME, registration_status, local_ip = initialize_camera()
    
    # 2. Connect to WiFi
    server_ip = connect_wifi("MaixCAM-Wifi", "maixcamwifi")
    logger.print("MAIN", "Camera IP: %s", server_ip)
    
    # 3. Initialize cameras and detectors (RTMP removed, now returns 4 values)
    cam, disp, pose_extractor, detector = initialize_cameras()
    load_fonts()
    
    # 4. Initialize tools
    recorder = VideoRecorder()
    skeleton_saver_2d = SkeletonSaver2D()

    # 5. Initialize area checkers
    safe_area_checker = SafeAreaChecker()
    initialize_safety_checker(safe_area_checker)

    bed_area_checker = BedAreaChecker(too_long_threshold_sec=5.0)
    initialize_bed_area_checker(bed_area_checker)

    floor_area_checker = FloorAreaChecker()
    initialize_floor_area_checker(floor_area_checker)

    # Initialize SafetyJudgment with all three checkers
    safety_judgment = SafetyJudgment(
        bed_area_checker=bed_area_checker,
        floor_area_checker=floor_area_checker,
        safe_area_checker=safe_area_checker,
        check_method=CheckMethod.TORSO_HEAD
    )

    # 6. Load initial configuration
    logger.print("MAIN", "=== Loading Configuration ===")
    load_initial_flags()
    initial_safe_areas = load_safe_areas()
    update_safety_checker_polygons(initial_safe_areas)

    initial_bed_areas = load_bed_areas()
    update_bed_area_polygons(initial_bed_areas)

    initial_floor_areas = load_floor_areas()
    update_floor_area_polygons(initial_floor_areas)
    
    # 7. Load or create background image
    if os.path.exists(BACKGROUND_PATH):
        from maix import image as img_module
        background_img = image.load(BACKGROUND_PATH, format=image.Format.FMT_RGB888)
        logger.print("MAIN", "Loaded background image from file")
    else:
        background_img = cam.read().copy()
        background_img.save(BACKGROUND_PATH)
        logger.print("MAIN", "Created new background image")
    
    # ============================================
    # ASYNC WORKERS SETUP
    # ============================================
    flags_queue = queue.Queue(maxsize=10)
    safe_areas_queue = queue.Queue(maxsize=5)
    bed_areas_queue = queue.Queue(maxsize=5)
    floor_areas_queue = queue.Queue(maxsize=5)
    analytics_queue = queue.Queue(maxsize=20)  # Queue for analytics worker
    tracks_queue = queue.Queue(maxsize=30)  # Queue for tracks sender

    # Start command server
    command_receiver = CommandReceiver()
    command_receiver.start()

    # Start async workers
    logger.print("MAIN", "Starting async workers...")
    flag_sync_worker = CameraStateSyncWorker(
        flags_queue, safe_areas_queue, STREAMING_HTTP_URL, CAMERA_ID,
        bed_areas_queue=bed_areas_queue, floor_areas_queue=floor_areas_queue
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
    
    # Start Tracks Sender Worker (always runs to send all tracks to Streaming Server)
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
    prev_human_present = False
    no_human_counter = 0
    last_update_ms = time_ms()
    last_gc_time = time_ms()
    streaming_server_available = True
    frame_profiler = TaskProfiler(task_name="Main", enabled=False)
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
    
    # ============================================
    # MAIN LOOP
    # ============================================
    frame_counter = 0
    
    logger.print("MAIN", "=== Camera Stream Started ===")
    control_flags = get_control_flags()
    logger.print("MAIN", "Final Configuration:")
    logger.print("MAIN", "  Camera: %s (%s)", CAMERA_NAME, CAMERA_ID)
    logger.print("MAIN", "  Status: %s", registration_status)
    logger.print("MAIN", "  Analytics Mode: %s", 'ENABLED' if control_flags.get('analytics_mode') else 'DISABLED')
    logger.print("MAIN", "  Recording: %s", 'ENABLED' if control_flags.get('record') else 'DISABLED')
    logger.print("MAIN", "  UI Rendering: DISABLED (no text/skeleton)")
    logger.print("MAIN", "  Display: DISABLED")
    logger.print("MAIN", "  Fall Algorithm: %s", control_flags.get('fall_algorithm'))
    
    # Upload background image to server at startup
    if streaming_server_available and background_img is not None:
        try:
            jpeg_bytes = background_img.to_jpeg(quality=70).to_bytes(copy=False)
            frame_upload_worker.update_background(jpeg_bytes)
            logger.print("MAIN", "[BACKGROUND] Background queued for upload at startup")
        except Exception as e:
            logger.print("MAIN", "[BACKGROUND] Failed to queue background for upload at startup: %s", e)
    
    while not app.need_exit():
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
            while not safe_areas_queue.empty():
                msg_type, data = safe_areas_queue.get_nowait()
                if msg_type == "safe_areas_update":
                    if isinstance(data, list):
                        update_safety_checker_polygons(data)
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
        
        # 4. Check for background update request
        frame_profiler.start_task("background_check")
        if get_flag("set_background", False) and not background_update_in_progress and not get_flag("_background_update_pending", False):
            logger.print("MAIN", "[BACKGROUND] Starting background update...")
            background_img = raw_img.copy()
            background_img.save(BACKGROUND_PATH)
            background_update_in_progress = True
            update_control_flag("set_background", False)
            # Upload background image to server via FrameUploadWorker
            if streaming_server_available:
                try:
                    jpeg_bytes = background_img.to_jpeg(quality=70).to_bytes(copy=False)
                    frame_upload_worker.update_background(jpeg_bytes)
                    logger.print("MAIN", "[BACKGROUND] Background queued for upload to server")
                except Exception as e:
                    logger.print("MAIN", "[BACKGROUND] Failed to queue background for upload: %s", e)
            send_background_updated(time.time())
            background_update_in_progress = False
            logger.print("MAIN", "[BACKGROUND] Background updated")
        frame_profiler.end_task("background_check")
        
        # 5. Person Detection
        frame_profiler.start_task("human detect")
        objs_det = detector.detect(raw_img, conf_th=0.5, iou_th=0.45)
        current_human_present = any(detector.labels[obj.class_id] == "person" for obj in objs_det)
        
        # Auto background update logic
        if get_flag("auto_update_bg", False):
            if prev_human_present and not current_human_present:
                no_human_counter += 1
                if no_human_counter >= NO_HUMAN_CONFIRM_FRAMES:
                    background_img = raw_img.copy()
                    background_img.save(BACKGROUND_PATH)
                    # Upload background image to server via FrameUploadWorker
                    if streaming_server_available:
                        try:
                            jpeg_bytes = background_img.to_jpeg(quality=70).to_bytes(copy=False)
                            frame_upload_worker.update_background(jpeg_bytes)
                            logger.print("MAIN", "[BACKGROUND] Auto-update background queued for upload")
                        except Exception as e:
                            logger.print("MAIN", "[BACKGROUND] Auto-update failed to queue background: %s", e)
                    last_update_ms = time_ms()
                    no_human_counter = 0
            else:
                no_human_counter = 0
                if time_ms() - last_update_ms > UPDATE_INTERVAL_MS:
                    background_img = raw_img.copy()
                    background_img.save(BACKGROUND_PATH)
                    # Upload background image to server via FrameUploadWorker
                    if streaming_server_available:
                        try:
                            jpeg_bytes = background_img.to_jpeg(quality=70).to_bytes(copy=False)
                            frame_upload_worker.update_background(jpeg_bytes)
                            logger.print("MAIN", "[BACKGROUND] Auto-update background queued for upload")
                        except Exception as e:
                            logger.print("MAIN", "[BACKGROUND] Auto-update failed to queue background: %s", e)
                    last_update_ms = time_ms()
            prev_human_present = current_human_present
        frame_profiler.end_task("human detect")
        
        # 6. Prepare display image (no UI rendering)
        frame_profiler.start_task("display_prep")
        if get_flag("show_raw", False):
            img = raw_img.copy()
        else:
            img = background_img.copy() if background_img is not None else raw_img.copy()
        frame_profiler.end_task("display_prep")
        
        # 7. Pose extraction and tracking
        frame_profiler.start_task("pose_extraction")
        objs = pose_extractor.detect(raw_img, conf_th=0.5, iou_th=0.45, keypoint_th=0.5)
        pose_human_present = len(objs) > 0
        human_present = current_human_present or pose_human_present
        
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
                video_path = f"/root/recordings/{timestamp}.mp4"
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
        frame_profiler.end_task("pose_extraction")
        
        # 8. Process tracking and collect all tracks into processed_tracks list
        frame_profiler.start_task("tracking")
        tracks = update_tracks(objs)

        if is_recording:
            frame_id += 1

        # Calculate FPS and elapsed time
        frame_end_time = time_ms()
        frame_duration = frame_end_time - frame_start_time
        current_fps = 1000.0 / frame_duration if frame_duration > 0 else 30.0
        set_fps(current_fps)
        frame_start_time = frame_end_time

        # Collect all processed tracks in a single list - single source of truth
        processed_tracks = []
        
        for track in tracks:
            track_result = process_track(
                track, objs,
                is_recording=is_recording,
                skeleton_saver=skeleton_saver_2d if is_recording else None,
                frame_id=frame_id,
                fps=current_fps,
                analytics_mode=get_flag("analytics_mode", False),
                safety_judgment=safety_judgment
            )
            
            if not track_result:
                continue
            
            logger.print("MAIN", "pose_label <- track_result['pose_label']: %s", track_result.get('pose_label', 'unknown'))
            track_id = track_result.get("track_id")
            keypoints = track_result.get("keypoints")
            bbox = track_result.get("bbox")
            pose_label = track_result.get("pose_label", "unknown")
            # Map "tracking" status to "normal" for safety_status
            status = track_result.get("status", "tracking")
            safety_status = "normal" if status == "tracking" else status
            encrypted_features = track_result.get("encrypted_features")

            # Create processed track entry with all required data
            processed_track = {
                "track_id": track_id,
                "keypoints": keypoints,
                "bbox": bbox,
                "pose_label": pose_label,
                "safety_status": safety_status,
                "encrypted_features": encrypted_features  # Include for analytics server, omit when sending to streaming
            }
            processed_tracks.append(processed_track)
        
        # Send all processed tracks at once via queue (fire-and-forget)
        update_latest_tracks(processed_tracks)
        # Signal tracks ready for sending
        mark_tracks_as_ready()

        frame_profiler.end_task("tracking")
        
        # 10. Recording (no display, no UI rendering)
        frame_profiler.start_task("recording")
        if is_recording:
            recorder.add_frame(img)
        frame_profiler.end_task("recording")
        
        # 11. Display to MaixVision
        frame_profiler.start_task("display")
        disp.show(img)
        frame_profiler.end_task("display")
        
        # 12. Update FrameUploadWorker with latest RAW frame (no overlays)
        # Always send raw frame regardless of show_raw flag
        frame_profiler.start_task("frame_upload")
        try:
            jpeg_bytes = raw_img.to_jpeg(quality=60).to_bytes(copy=False)
            frame_upload_worker.update_frame(jpeg_bytes)
        except Exception:
            pass
        frame_profiler.end_task("frame_upload")
        
        # End frame profiling
        frame_profiler.end_frame()
        
        # 13. Periodic garbage collection
        current_time = time_ms()
        if current_time - last_gc_time > GC_INTERVAL_MS:
            gc.collect()
            last_gc_time = current_time
        
        frame_counter += 1
        if frame_counter % 30 == 0:
            logger.print("MAIN", "Frame %d processed", frame_counter)
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
    
    logger.print("MAIN", "=== Camera Stream Stopped ===")
    control_flags = get_control_flags()
    logger.print("MAIN", "Final Configuration:")
    logger.print("MAIN", "  Camera: %s (%s)", CAMERA_NAME, CAMERA_ID)
    logger.print("MAIN", "  Status: %s", registration_status)
    logger.print("MAIN", "  Analytics Mode: %s", 'ENABLED' if control_flags.get('analytics_mode') else 'DISABLED')
    logger.print("MAIN", "  Recording: %s", 'ENABLED' if control_flags.get('record') else 'DISABLED')
    logger.print("MAIN", "  Fall Algorithm: %s", control_flags.get('fall_algorithm'))

if __name__ == "__main__":
    main()

