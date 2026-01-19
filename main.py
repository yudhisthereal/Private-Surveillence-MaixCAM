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
    is_analytics_server_available, set_analytics_queue,
)
from tracking import (
    update_tracks, process_track, set_fps
)
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
    
    # OBSOLETE: RTMP streaming has been removed
    # Old code:
    # rtmp_connected = setup_rtmp_stream(cam_rtmp, CAMERA_ID)
    
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
    # OBSOLETE: FrameUploadWorker no longer uses queue - uses shared frame reference
    # Old code:
    # frame_queue = queue.Queue(maxsize=2)
    
    # Start command server
    command_receiver = CommandReceiver()
    command_receiver.start()
    
    # Start async workers
    print("Starting async workers...")
    flag_sync_worker = FlagAndSafeAreaSyncWorker(
        flags_queue, safe_areas_queue, STREAMING_HTTP_URL, CAMERA_ID
    )
    state_reporter_worker = StateReporterWorker(STREAMING_HTTP_URL, CAMERA_ID)
    # OBSOLETE: New FrameUploadWorker uses shared frame reference instead of queue
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
    
    print("Async workers started successfully")
    
    # Register callback for registration status changes
    # This ensures that when the camera is approved, the local status is updated
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
    
    # Background update state
    last_gc_time = time_ms()
    
    # Server connection status
    streaming_server_available = True
    
    # Initialize frame profiler
    frame_profiler = FrameProfiler()
    
    # FPS tracking
    frame_start_time = time_ms()
    
    # ============================================
    # MAIN LOOP
    # ============================================
    frame_counter = 0
    last_pose_analysis_time = 0
    
    print("\n=== Camera Stream Stopped ===")
    control_flags = get_control_flags()
    print(f"Final Configuration:")
    print(f"  Camera: {CAMERA_NAME} ({CAMERA_ID})")
    print(f"  Status: {registration_status}")
    print(f"  Analytics Mode: {'ENABLED' if control_flags.get('analytics_mode') else 'DISABLED'}")
    print(f"  Recording: {'ENABLED' if control_flags.get('record') else 'DISABLED'}")
    print(f"  OBSOLETE: RTMP Streaming DISABLED (JPEG upload via HTTP)")
    print(f"  Fall Algorithm: {control_flags.get('fall_algorithm')}")
    
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
                        last_flag_sync_time = time_ms()
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
        control_flags = get_control_flags()
        for cmd_data in commands:
            handle_command(
                cmd_data.get("command"), 
                cmd_data.get("value"),
                CAMERA_ID,
                registration_status,
                lambda cid, cname, status, ip: save_camera_info(cid, cname, status, ip)
            )
            
            # If command was "approve_camera", update the CameraStateManager
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
            
            # Send update to streaming server
            if streaming_server_available:
                send_background_updated(time_ms())
            
            background_update_in_progress = False
            print("[BACKGROUND] Background updated")
        frame_profiler.end_task("background_check")
        
        # 5. Person Detection (was: Segmentation)
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
                    last_update_ms = time_ms()
                    no_human_counter = 0
            else:
                no_human_counter = 0
                if time_ms() - last_update_ms > UPDATE_INTERVAL_MS:
                    background_img = raw_img.copy()
                    background_img.save(BACKGROUND_PATH)
                    last_update_ms = time_ms()
            
            prev_human_present = current_human_present
        frame_profiler.end_task("human detect")
        
        # 6. Prepare display image
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
        
        # Recording logic - clean state machine approach
        record_flag = get_flag("record", False)
        now = time_ms()
        
        if record_flag and not is_recording:
            # Start recording if: record flag is True AND not already recording
            # AND we have confirmed human presence for minimum frames
            if (len(human_presence_history) >= MIN_HUMAN_FRAMES_TO_START and 
                all(human_presence_history[-MIN_HUMAN_FRAMES_TO_START:])):
                timestamp = time.strptime("%Y%m%d_%H%M%S")
                video_path = f"/root/recordings/{timestamp}.mp4"
                recorder.start(video_path, pose_extractor.input_width(), pose_extractor.input_height())
                skeleton_saver_2d.start_new_log(timestamp)
                frame_id = 0
                recording_start_time = now
                is_recording = True
                update_is_recording(True)
                print(f"Started recording: {timestamp}")
        
        if is_recording:
            # Check if we should stop recording
            no_human_count = 0
            for presence in reversed(human_presence_history):
                if not presence:
                    no_human_count += 1
                else:
                    break
            
            # Calculate frames threshold based on 5 seconds
            # Assuming ~60fps, 5 seconds = 300 frames
            no_human_frames_to_stop = NO_HUMAN_SECONDS_TO_STOP * 60
            
            # Stop if: no human detected for 5 seconds threshold OR max duration reached
            # OR if record flag was turned off (external control)
            if (no_human_count >= no_human_frames_to_stop or 
                now - recording_start_time >= MAX_RECORDING_DURATION_MS or
                not record_flag):
                recorder.end()
                skeleton_saver_2d.save_to_csv()
                is_recording = False
                update_is_recording(False)
                print("Stopped recording")
        frame_profiler.end_task("pose_extraction")
        
        # 8. Process tracking
        frame_profiler.start_task("tracking")
        tracks = update_tracks(objs)
        
        if is_recording:
            frame_id += 1
        
        # Calculate FPS for fall detection (using time since last frame)
        frame_end_time = time_ms()
        frame_duration = frame_end_time - frame_start_time
        current_fps = 1000.0 / frame_duration if frame_duration > 0 else 30.0
        set_fps(current_fps)
        frame_start_time = frame_end_time
        
        # Calculate elapsed_ms for analytics server (time since last frame)
        elapsed_ms = frame_duration if frame_duration > 0 else 33.33
        
        current_fall_counter = 0
        current_fall_detected = False
        current_fall_algorithm = get_flag("fall_algorithm", 3)
        
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
            
            # Send track data to analytics server if analytics_mode is enabled
            if track_result and get_flag("analytics_mode", False):
                track_id = track_result.get("track_id")
                keypoints = track_result.get("keypoints")
                bbox = track_result.get("bbox")
                
                if track_id and keypoints and bbox:
                    # Get previous bbox for this track
                    prev_bbox = previous_bboxes.get(track_id)
                    
                    # Send to analytics queue
                    send_track_to_analytics(
                        track_id=track_id,
                        keypoints=keypoints,
                        bbox=bbox,
                        previous_bbox=prev_bbox,
                        elapsed_ms=elapsed_ms,
                        use_hme=get_flag("hme", False)
                    )
                    
                    # Store current bbox for next frame
                    previous_bboxes[track_id] = bbox
            
            # Track fall detection results
            if track_result:
                track_id = track_result.get("track_id")
                
                # Check if analytics server has fall detection result
                if get_flag("analytics_mode", False) and is_analytics_server_available():
                    analytics_fall = get_analytics_fall_data(track_id)
                    if analytics_fall and analytics_fall.get("primary_alert"):
                        current_fall_detected = True
                        current_fall_counter += 1
                        print(f"[Analytics] Fall detected from server: track_id={track_id}")
                    elif analytics_fall and (analytics_fall.get("counter_method3", 0) > 0 or 
                                             analytics_fall.get("counter_method2", 0) > 0 or
                                             analytics_fall.get("counter_method1", 0) > 0):
                        # Track as unsafe if any counter is active
                        pass
                else:
                    # Fall back to local fall detection result
                    if track_result.get("status") == "fall":
                        current_fall_detected = True
                        current_fall_counter += 1
        frame_profiler.end_task("tracking")
        
        # 9. Draw UI elements
        frame_profiler.start_task("ui_drawing")
        y_position = 30
        font_scale = 0.4
        
        algorithm_names = {1: "BBox Only", 2: "Flexible", 3: "Conservative"}
        algorithm_name = algorithm_names.get(current_fall_algorithm, "Conservative")
        fall_text = f"Fall ({algorithm_name}): {current_fall_counter}/2"
        text_color = image.COLOR_RED if current_fall_detected else image.COLOR_WHITE
        
        img.draw_string(10, y_position, fall_text, color=text_color, scale=font_scale)
        y_position += 15
        frame_profiler.end_task("ui_drawing")
        
        # 10. Recording and Display
        frame_profiler.start_task("recording")
        if is_recording:
            recorder.add_frame(img)
        frame_profiler.end_task("recording")
        
        frame_profiler.start_task("display")
        disp.show(img)
        frame_profiler.end_task("display")
        
        # 11. Update FrameUploadWorker with latest rendered frame
        # This is done AFTER disp.show(img) to ensure we upload the final rendered image
        frame_profiler.start_task("frame_upload")
        try:
            # Convert the final rendered image to JPEG bytes and update worker
            # Using quality=60 for good balance between quality and bandwidth
            jpeg_bytes = img.to_jpeg(quality=60).to_bytes(copy=False)
            frame_upload_worker.update_frame(jpeg_bytes)
        except Exception as e:
            # Silently ignore upload errors - don't disrupt main loop
            pass
        frame_profiler.end_task("frame_upload")
        
        # End frame profiling
        frame_profiler.end_frame()
        
        # 12. Periodic garbage collection
        current_time = time_ms()
        if current_time - last_gc_time > GC_INTERVAL_MS:
            gc.collect()
            last_gc_time = current_time
        
        frame_counter += 1
        if frame_counter % 30 == 0:
            print(f"Frame {frame_counter} processed")
            
            # Log current flag status
            if frame_counter % 60 == 0:
                control_flags = get_control_flags()
                print(f"[STATUS] Flags: record={control_flags.get('record')}, "
                      f"show_raw={control_flags.get('show_raw')}, "
                      f"fall_algorithm={control_flags.get('fall_algorithm')}")

    # ============================================
    # CLEANUP
    # ============================================
    command_receiver.stop()
    
    # Stop async workers
    flag_sync_worker.stop()
    state_reporter_worker.stop()
    frame_upload_worker.stop()
    ping_worker.stop()
    
    if is_recording:
        recorder.end()
        skeleton_saver_2d.save_to_csv()
    
    # OBSOLETE: RTMP cleanup removed
    # Old code:
    # stop_rtmp_stream()
    
    # Save final state
    from control_manager import save_control_flags
    save_control_flags()
    
    print("\n=== Camera Stream Stopped ===")
    control_flags = get_control_flags()
    print(f"Final Configuration:")
    print(f"  Camera: {CAMERA_NAME} ({CAMERA_ID})")
    print(f"  Status: {registration_status}")
    print(f"  Analytics Mode: {'ENABLED' if control_flags.get('analytics_mode') else 'DISABLED'}")
    print(f"  Recording: {'ENABLED' if control_flags.get('record') else 'DISABLED'}")
    print(f"  OBSOLETE: RTMP Streaming DISABLED (JPEG upload via HTTP)")
    print(f"  Fall Algorithm: {control_flags.get('fall_algorithm')}")

if __name__ == "__main__":
    main()

