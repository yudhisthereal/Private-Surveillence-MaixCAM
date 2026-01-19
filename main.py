# main.py - Modularized main entry point for Private CCTV System

from maix import app, image, time
from tools.wifi_connect import connect_wifi
from tools.video_record import VideoRecorder
from tools.skeleton_saver import SkeletonSaver2D
from tools.safe_area import BodySafetyChecker

# Import modular components
from config import (
    initialize_camera, save_camera_info, STREAMING_HTTP_URL,
    BACKGROUND_PATH, MIN_HUMAN_FRAMES_TO_START, NO_HUMAN_FRAMES_TO_STOP,
    MAX_RECORDING_DURATION_MS, UPDATE_INTERVAL_MS, NO_HUMAN_CONFIRM_FRAMES,
    FLAG_SYNC_INTERVAL_MS, GC_INTERVAL_MS, POSE_ANALYSIS_INTERVAL_MS,
    NO_HUMAN_SECONDS_TO_STOP
)
from camera_manager import (
    initialize_cameras, load_fonts,
)
from control_manager import (
    load_initial_flags, get_control_flags, update_control_flags_from_server,
    update_control_flag, get_flag, initialize_safety_checker,
    update_safety_checker_polygons, load_safe_areas,
    send_background_updated, camera_state_manager, register_status_change_callback
)
from workers import (
    FlagAndSafeAreaSyncWorker, StateReporterWorker, FrameUploadWorker,
    CommandReceiver, PingWorker, get_received_commands, handle_command,
    update_is_recording, AnalyticsWorker,
    send_track_to_analytics, get_analytics_pose_data, get_analytics_fall_data,
    is_analytics_server_available, set_analytics_queue, KeypointsSenderWorker,
    set_keypoints_queue, send_keypoints_to_queue
)
from tracking import (
    update_tracks, process_track, set_fps
)
from streaming import send_frame_to_server, send_pose_label_to_streaming_server
from tools.time_utils import time_ms, FrameProfiler

import os
import queue
import gc

# ============================================
# MAIN INITIALIZATION
# ============================================

def main():
    """Main entry point"""
    # 1. Initialize camera identity
    CAMERA_ID, CAMERA_NAME, registration_status, local_ip = initialize_camera()
    
    # 2. Connect to WiFi
    server_ip = connect_wifi("MaixCAM-Wifi", "maixcamwifi")
    print(f"Camera IP: {server_ip}")
    
    # 3. Initialize cameras and detectors (RTMP removed, now returns 4 values)
    cam, disp, pose_extractor, detector = initialize_cameras()
    load_fonts()
    
    # 4. Initialize tools
    recorder = VideoRecorder()
    skeleton_saver_2d = SkeletonSaver2D()
    
    # 5. Initialize safety checker
    safety_checker = BodySafetyChecker()
    initialize_safety_checker(safety_checker)
    
    # 6. Load initial configuration
    print("\n=== Loading Configuration ===")
    load_initial_flags()
    initial_safe_areas = load_safe_areas()
    update_safety_checker_polygons(initial_safe_areas)
    
    # 7. Load or create background image
    if os.path.exists(BACKGROUND_PATH):
        from maix import image as img_module
        background_img = image.load(BACKGROUND_PATH, format=image.Format.FMT_RGB888)
        print("Loaded background image from file")
    else:
        background_img = cam.read().copy()
        background_img.save(BACKGROUND_PATH)
        print("Created new background image")
    
    # ============================================
    # ASYNC WORKERS SETUP
    # ============================================
    flags_queue = queue.Queue(maxsize=10)
    safe_areas_queue = queue.Queue(maxsize=5)
    analytics_queue = queue.Queue(maxsize=20)  # Queue for analytics worker
    keypoints_queue = queue.Queue(maxsize=30)  # Queue for keypoints sender
    
    # Start command server
    command_receiver = CommandReceiver()
    command_receiver.start()
    
    # Start async workers
    print("Starting async workers...")
    flag_sync_worker = FlagAndSafeAreaSyncWorker(
        flags_queue, safe_areas_queue, STREAMING_HTTP_URL, CAMERA_ID
    )
    state_reporter_worker = StateReporterWorker(STREAMING_HTTP_URL, CAMERA_ID)
    frame_upload_worker = FrameUploadWorker(STREAMING_HTTP_URL, CAMERA_ID)
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
        print("[Analytics] Worker started (analytics_mode=True)")
    else:
        print("[Analytics] Worker NOT started (analytics_mode=False)")
    
    # Start Keypoints Sender Worker (always runs to send plain keypoints to Streaming Server)
    set_keypoints_queue(keypoints_queue)
    keypoints_sender = KeypointsSenderWorker(keypoints_queue, CAMERA_ID)
    keypoints_sender.start()
    print("[KeypointsSender] Worker started")
    
    print("Async workers started successfully")
    
    # Register callback for registration status changes
    def on_registration_status_changed(new_status):
        """Callback to handle registration status changes"""
        nonlocal registration_status
        old_status = registration_status
        registration_status = new_status
        print(f"[StatusChange] Registration status changed: {old_status} -> {new_status}")
        
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
    frame_profiler = FrameProfiler()
    frame_start_time = time_ms()
    
    # ============================================
    # MAIN LOOP
    # ============================================
    frame_counter = 0
    
    print("\n=== Camera Stream Started ===")
    control_flags = get_control_flags()
    print(f"Final Configuration:")
    print(f"  Camera: {CAMERA_NAME} ({CAMERA_ID})")
    print(f"  Status: {registration_status}")
    print(f"  Analytics Mode: {'ENABLED' if control_flags.get('analytics_mode') else 'DISABLED'}")
    print(f"  Recording: {'ENABLED' if control_flags.get('record') else 'DISABLED'}")
    print(f"  UI Rendering: DISABLED (no text/skeleton)")
    print(f"  Display: DISABLED")
    print(f"  Fall Algorithm: {control_flags.get('fall_algorithm')}")
    
    # Upload background image to server at startup
    if streaming_server_available and background_img is not None:
        try:
            jpeg_bytes = background_img.to_jpeg(quality=70).to_bytes(copy=False)
            frame_upload_worker.update_background(jpeg_bytes)
            print("[BACKGROUND] Background queued for upload at startup")
        except Exception as e:
            print(f"[BACKGROUND] Failed to queue background for upload at startup: {e}")
    
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
        if get_flag("set_background", False) and not background_update_in_progress:
            print("[BACKGROUND] Starting background update...")
            background_img = raw_img.copy()
            background_img.save(BACKGROUND_PATH)
            background_update_in_progress = True
            update_control_flag("set_background", False)
            # Upload background image to server via FrameUploadWorker
            if streaming_server_available:
                try:
                    jpeg_bytes = background_img.to_jpeg(quality=70).to_bytes(copy=False)
                    frame_upload_worker.update_background(jpeg_bytes)
                    print("[BACKGROUND] Background queued for upload to server")
                except Exception as e:
                    print(f"[BACKGROUND] Failed to queue background for upload: {e}")
            background_update_in_progress = False
            print("[BACKGROUND] Background updated")
        frame_profiler.end_task("background_check")
        
        # 5. Person Detection
        frame_profiler.start_task("human detect")
        objs_det = detector.detect(raw_img, conf_th=0.5, iou_th=0.45)
        current_human_present = any(detector.labels[obj.class_id] == "person" for obj in objs_det)
        
        # Background update logic
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
                            print("[BACKGROUND] Auto-update background queued for upload")
                        except Exception as e:
                            print(f"[BACKGROUND] Auto-update failed to queue background: {e}")
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
                            print("[BACKGROUND] Auto-update background queued for upload")
                        except Exception as e:
                            print(f"[BACKGROUND] Auto-update failed to queue background: {e}")
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
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                video_path = f"/root/recordings/{timestamp}.mp4"
                recorder.start(video_path, pose_extractor.input_width(), pose_extractor.input_height())
                skeleton_saver_2d.start_new_log(timestamp)
                frame_id = 0
                recording_start_time = now
                is_recording = True
                update_is_recording(True)
                print(f"Started recording: {timestamp}")
        
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
                print("Stopped recording")
        frame_profiler.end_task("pose_extraction")
        
        # 8. Process tracking and handle data based on analytics server availability
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
        elapsed_ms = frame_duration if frame_duration > 0 else 33.33
        
        # Check analytics server availability
        analytics_available = is_analytics_server_available()
        
        # Track previous bboxes for analytics server (per track_id)
        previous_bboxes = {}
        
        for track in tracks:
            track_result = process_track(
                track, objs, pose_extractor, img,
                is_recording=is_recording,
                skeleton_saver=skeleton_saver_2d if is_recording else None,
                frame_id=frame_id,
                safety_checker=safety_checker,
                fps=current_fps,
                analytics_mode=get_flag("analytics_mode", False)
            )
            
            if not track_result:
                continue
            
            # print(f"process_track() -> main(): {track_result}")
            print(f"pose_label <- track_result['pose_label]: {track_result.get('pose_label', 'unknown')}")
            track_id = track_result.get("track_id")
            keypoints = track_result.get("keypoints")
            bbox = track_result.get("bbox")
            pose_label = track_result.get("pose_label", "unknown")
            safety_status = track_result.get("status", "normal")
            encrypted_features = track_result.get("encrypted_features")
            use_hme = track_result.get("use_hme", False)
            
            # Decide where to send data based on use_hme and analytics availability
            # use_hme is automatically False when analytics server is unavailable
            # (this is set in process_track based on is_analytics_server_available())
            
            if use_hme and analytics_available:
                # HME mode with available analytics server: send encrypted features to Analytics Server
                # Plain keypoints are NEVER sent to Analytics Server
                if encrypted_features:
                    send_track_to_analytics(
                        track_id=track_id,
                        bbox=bbox,
                        previous_bbox=previous_bboxes.get(track_id),
                        elapsed_ms=elapsed_ms,
                        use_hme=True,
                        encrypted_features=encrypted_features
                    )
                    previous_bboxes[track_id] = bbox
                else:
                    # No encrypted features available, fall back to local processing
                    print(f"[Main] Warning: use_hme=True but no encrypted_features for track_id={track_id}")
                    send_pose_label_to_streaming_server(
                        camera_id=CAMERA_ID,
                        track_id=track_id,
                        pose_label=pose_label,
                        safety_status=safety_status
                    )
            else:
                # Either HME disabled OR analytics server unavailable
                # NEVER send plain keypoints to Analytics Server
                # Always do local fall detection and send results to Streaming Server
                send_pose_label_to_streaming_server(
                    camera_id=CAMERA_ID,
                    track_id=track_id,
                    pose_label=pose_label,
                    safety_status=safety_status
                )
                # Always send plain keypoints to Streaming Server (not Analytics)
                send_keypoints_to_queue(
                    track_id=track_id,
                    keypoints=keypoints,
                    bbox=bbox,
                    pose_label=pose_label,
                    safety_status=safety_status
                )
                previous_bboxes[track_id] = bbox
        
        frame_profiler.end_task("tracking")
        
        # 9. Pose label handling (direct sending)
        frame_profiler.start_task("pose_label_handling")
        # Pose labels are sent directly to streaming server (no queue)
        frame_profiler.end_task("pose_label_handling")
        
        # 10. Recording (no display, no UI rendering)
        frame_profiler.start_task("recording")
        if is_recording:
            recorder.add_frame(img)
        frame_profiler.end_task("recording")
        
        # 11. Display to MaixVision
        frame_profiler.start_task("display")
        disp.show(img)
        frame_profiler.end_task("display")
        
        # 12. Update FrameUploadWorker with latest rendered frame (only if show_raw is True)
        frame_profiler.start_task("frame_upload")
        if get_flag("show_raw", False):
            try:
                jpeg_bytes = img.to_jpeg(quality=60).to_bytes(copy=False)
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
            print(f"Frame {frame_counter} processed")
            if frame_counter % 60 == 0:
                control_flags = get_control_flags()
                print(f"[STATUS] Flags: record={control_flags.get('record')}, "
                      f"analytics_mode={control_flags.get('analytics_mode')}, "
                      f"fall_algorithm={control_flags.get('fall_algorithm')}")

    # ============================================
    # CLEANUP
    # ============================================
    command_receiver.stop()
    flag_sync_worker.stop()
    state_reporter_worker.stop()
    frame_upload_worker.stop()
    ping_worker.stop()
    keypoints_sender.stop()
    
    if is_recording:
        recorder.end()
        skeleton_saver_2d.save_to_csv()
    
    from control_manager import save_control_flags
    save_control_flags()
    
    print("\n=== Camera Stream Stopped ===")
    control_flags = get_control_flags()
    print(f"Final Configuration:")
    print(f"  Camera: {CAMERA_NAME} ({CAMERA_ID})")
    print(f"  Status: {registration_status}")
    print(f"  Analytics Mode: {'ENABLED' if control_flags.get('analytics_mode') else 'DISABLED'}")
    print(f"  Recording: {'ENABLED' if control_flags.get('record') else 'DISABLED'}")
    print(f"  Fall Algorithm: {control_flags.get('fall_algorithm')}")

if __name__ == "__main__":
    main()

