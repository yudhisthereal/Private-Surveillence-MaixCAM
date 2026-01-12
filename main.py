# main.py - Modularized main entry point for Private CCTV System

from maix import camera, display, app, nn, image, time
from tools.wifi_connect import connect_wifi
from tools.video_record import VideoRecorder
from tools.skeleton_saver import SkeletonSaver2D
from tools.safe_area import BodySafetyChecker

# Import modular components
from config import (
    initialize_camera, STREAMING_HTTP_URL, STREAMING_SERVER_IP,
    BACKGROUND_PATH, MIN_HUMAN_FRAMES_TO_START, NO_HUMAN_FRAMES_TO_STOP,
    MAX_RECORDING_DURATION_MS, UPDATE_INTERVAL_MS, NO_HUMAN_CONFIRM_FRAMES,
    FLAG_SYNC_INTERVAL_MS, GC_INTERVAL_MS, POSE_ANALYSIS_INTERVAL_MS
)
from camera_manager import (
    initialize_cameras, setup_rtmp_stream, load_fonts,
    get_camera, get_display, get_detector, get_segmentor, stop_rtmp_stream
)
from control_manager import (
    load_initial_flags, get_control_flags, update_control_flags_from_server,
    update_control_flag, get_flag, initialize_safety_checker,
    update_safety_checker_polygons, load_safe_areas, get_safety_checker
)
from workers import (
    FlagAndSafeAreaSyncWorker, StateReporterWorker, FrameUploadWorker,
    CommandReceiver, get_received_commands, handle_command,
    update_is_recording, update_rtmp_connected
)
from tracking import (
    update_tracks, process_track, clear_track_history, get_online_targets
)
from streaming import send_background_updated
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
    
    # 3. Initialize cameras and detectors
    cam, disp, detector, segmentor = initialize_cameras()
    load_fonts()
    
    # 4. Setup RTMP streaming
    rtmp_connected = setup_rtmp_stream(disp, CAMERA_ID)
    update_rtmp_connected(rtmp_connected)
    
    # 5. Initialize tools
    recorder = VideoRecorder()
    skeleton_saver_2d = SkeletonSaver2D()
    
    # 6. Initialize safety checker
    safety_checker = BodySafetyChecker()
    initialize_safety_checker(safety_checker)
    
    # 7. Load initial configuration
    print("\n=== Loading Configuration ===")
    load_initial_flags()
    initial_safe_areas = load_safe_areas()
    update_safety_checker_polygons(initial_safe_areas)
    
    # 8. Load or create background image
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
    frame_queue = queue.Queue(maxsize=2)  # Small queue for frames
    
    # Start command server
    command_receiver = CommandReceiver()
    command_receiver.start()
    
    # Start async workers
    print("Starting async workers...")
    flag_sync_worker = FlagAndSafeAreaSyncWorker(
        flags_queue, safe_areas_queue, STREAMING_HTTP_URL, CAMERA_ID
    )
    state_reporter_worker = StateReporterWorker(STREAMING_HTTP_URL, CAMERA_ID)
    frame_upload_worker = FrameUploadWorker(frame_queue, STREAMING_HTTP_URL, CAMERA_ID)
    
    flag_sync_worker.start()
    state_reporter_worker.start()
    frame_upload_worker.start()
    print("✅ Async workers started successfully")
    
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
    last_flag_sync_time = time_ms()
    last_frame_upload = 0
    
    # Server connection status
    streaming_server_available = True
    connection_error_count = 0
    
    # Initialize frame profiler
    frame_profiler = FrameProfiler()
    
    # ============================================
    # MAIN LOOP
    # ============================================
    frame_counter = 0
    last_pose_analysis_time = 0
    
    print("\n=== Starting Camera Stream ===")
    print(f"Camera: {CAMERA_NAME} ({CAMERA_ID})")
    print(f"Status: {registration_status}")
    print(f"RTMP Streaming: {'ENABLED' if rtmp_connected else 'DISABLED'}")
    print(f"Streaming Server: {STREAMING_HTTP_URL}")
    print("Frame profiling ENABLED - timing summaries print every 30 frames")
    
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
                lambda cid, cname, status, ip: None  # Placeholder - save_camera_info imported separately if needed
            )
        frame_profiler.end_task("commands")
        
        # 3. Camera Read
        raw_img = cam.read()
        
        # 4. Upload frame to streaming server periodically
        current_time = time_ms()
        if current_time - last_frame_upload > 500:  # 500ms = 2 FPS
            try:
                jpeg_bytes = raw_img.tobytes()
                frame_queue.put_nowait(jpeg_bytes)
                last_frame_upload = current_time
            except:
                pass
        
        # 5. Check for background update request
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
        
        # 6. Segmentation
        frame_profiler.start_task("segmentation")
        objs_seg = segmentor.detect(raw_img, conf_th=0.5, iou_th=0.45)
        current_human_present = any(segmentor.labels[obj.class_id] in ["person", "human"] for obj in objs_seg)
        
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
        frame_profiler.end_task("segmentation")
        
        # 7. Prepare display image
        frame_profiler.start_task("display_prep")
        if get_flag("show_raw", False):
            img = raw_img.copy()
        else:
            img = background_img.copy() if background_img is not None else raw_img.copy()
        frame_profiler.end_task("display_prep")
        
        # 8. Pose detection and tracking
        frame_profiler.start_task("pose_detection")
        objs = detector.detect(raw_img, conf_th=0.5, iou_th=0.45, keypoint_th=0.5)
        pose_human_present = len(objs) > 0
        human_present = current_human_present or pose_human_present
        
        human_presence_history.append(human_present)
        if len(human_presence_history) > NO_HUMAN_FRAMES_TO_STOP + 1:
            human_presence_history.pop(0)
        
        # Recording logic
        if get_flag("record", False):
            now = time_ms()
            
            if not is_recording:
                if (len(human_presence_history) >= MIN_HUMAN_FRAMES_TO_START and 
                    all(human_presence_history[-MIN_HUMAN_FRAMES_TO_START:])):
                    timestamp = time.strftime("%Y%m%d_%H%M%S")
                    video_path = f"/root/recordings/{timestamp}.mp4"
                    recorder.start(video_path, detector.input_width(), detector.input_height())
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
                
                if (no_human_count >= NO_HUMAN_FRAMES_TO_STOP or 
                    now - recording_start_time >= MAX_RECORDING_DURATION_MS):
                    recorder.end()
                    skeleton_saver_2d.save_to_csv()
                    is_recording = False
                    update_is_recording(False)
                    print("Stopped recording")
        else:
            if is_recording:
                recorder.end()
                skeleton_saver_2d.save_to_csv()
                is_recording = False
                update_is_recording(False)
        frame_profiler.end_task("pose_detection")
        
        # 9. Process tracking
        frame_profiler.start_task("tracking")
        tracks = update_tracks(objs)
        
        if is_recording:
            frame_id += 1
        
        current_fall_counter = 0
        current_fall_detected = False
        current_fall_algorithm = get_flag("fall_algorithm", 3)
        current_torso_angle = None
        current_thigh_uprightness = None
        
        for track in tracks:
            track_result = process_track(
                track, objs, detector, img,
                is_recording=is_recording,
                skeleton_saver=skeleton_saver_2d if is_recording else None,
                frame_id=frame_id,
                safety_checker=safety_checker
            )
        frame_profiler.end_task("tracking")
        
        # 10. Draw UI elements
        frame_profiler.start_task("ui_drawing")
        y_position = 30
        font_scale = 0.4
        
        algorithm_names = {1: "BBox Only", 2: "Flexible", 3: "Conservative"}
        algorithm_name = algorithm_names.get(current_fall_algorithm, "Conservative")
        fall_text = f"Fall ({algorithm_name}): {current_fall_counter}/2"
        text_color = image.COLOR_RED if current_fall_detected else image.COLOR_WHITE
        
        img.draw_string(10, y_position, fall_text, color=text_color, scale=font_scale)
        y_position += 15
        
        if current_torso_angle is not None:
            torso_text = f"Torso: {current_torso_angle:.1f}°"
            img.draw_string(10, y_position, torso_text, color=image.COLOR_BLUE, scale=font_scale)
            y_position += 15
        
        if current_thigh_uprightness is not None:
            thigh_text = f"Thigh: {current_thigh_uprightness:.1f}°"
            img.draw_string(10, y_position, thigh_text, color=image.COLOR_BLUE, scale=font_scale)
        
        # Draw camera status
        status_text = f"{CAMERA_NAME} ({registration_status})"
        img.draw_string(img.width() - 200, 10, status_text, color=image.COLOR_GREEN, scale=0.5)
        
        # Draw connection status
        connection_text = "✓ Online" if streaming_server_available else "✗ Offline"
        connection_color = image.COLOR_GREEN if streaming_server_available else image.COLOR_RED
        img.draw_string(img.width() - 150, 30, connection_text, color=connection_color, scale=0.5)
        
        # Draw safe areas
        if get_flag("show_safe_area", False):
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
        
        # Draw recording status
        if is_recording:
            recording_time = (time_ms() - recording_start_time) // 1000
            status_text = f"REC {recording_time}s"
            text_x = img.width() - 100
            text_y = 50
            img.draw_string(int(text_x), int(text_y), status_text, color=image.COLOR_RED, scale=0.5)
        
        # Draw operation mode
        if get_flag("analytics_mode", True):
            mode_text = f"Mode: Analytics (Alg:{current_fall_algorithm})"
            mode_color = image.Color.from_rgb(0, 255, 255)
        else:
            mode_text = f"Mode: Local (Alg:{current_fall_algorithm})"
            mode_color = image.Color.from_rgb(255, 165, 0)
        
        img.draw_string(10, 15, mode_text, color=mode_color, scale=0.5)
        frame_profiler.end_task("ui_drawing")
        
        # 11. Recording and Display
        frame_profiler.start_task("recording")
        if is_recording:
            recorder.add_frame(img)
        frame_profiler.end_task("recording")
        
        frame_profiler.start_task("display")
        disp.show(img)
        frame_profiler.end_task("display")
        
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
    
    if is_recording:
        recorder.end()
        skeleton_saver_2d.save_to_csv()
    
    stop_rtmp_stream()
    
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
    print(f"  RTMP Streaming: {'ENABLED' if rtmp_connected else 'DISABLED'}")
    print(f"  Fall Algorithm: {control_flags.get('fall_algorithm')}")

if __name__ == "__main__":
    main()

