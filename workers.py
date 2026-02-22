# workers.py - Async worker classes for streaming server communication

import queue
import time
import requests
import socket
import select
import json
import threading
from debug_config import DebugLogger

# Module-level debug logger instance
logger = DebugLogger(tag="WORKERS", instance_enable=False)
from config import (
    FLAG_SYNC_INTERVAL_MS, 
    SAFE_AREA_SYNC_INTERVAL_MS, STATE_REPORT_INTERVAL_MS,
    LOCAL_PORT
)

from control_manager import (
    get_camera_state_from_server, report_state,
    get_bed_areas_from_server, get_floor_areas_from_server,
    get_chair_areas_from_server, get_couch_areas_from_server, get_bench_areas_from_server,
    camera_state_manager
)
from streaming import send_frame_to_server, send_background_to_server
from tools.time_utils import time_ms, TaskProfiler

class CameraStateSyncWorker(threading.Thread):
    """Background thread for syncing flags and editable areas from streaming server"""

    def __init__(self, flags_queue, streaming_url, camera_id,
                 sync_interval_ms=FLAG_SYNC_INTERVAL_MS, bed_areas_queue=None, floor_areas_queue=None,
                 chair_areas_queue=None, couch_areas_queue=None, bench_areas_queue=None):
        super().__init__(daemon=True)
        self.flags_queue = flags_queue
        self.bed_areas_queue = bed_areas_queue
        self.floor_areas_queue = floor_areas_queue
        self.chair_areas_queue = chair_areas_queue
        self.couch_areas_queue = couch_areas_queue
        self.bench_areas_queue = bench_areas_queue
        self.streaming_url = streaming_url
        self.camera_id = camera_id
        self.sync_interval = sync_interval_ms / 1000.0  # Convert to seconds
        self.running = True
        self.connection_errors = 0
        self.max_errors = 10
        self.last_successful_sync = 0
        self.last_bed_area_sync = 0
        self.last_floor_area_sync = 0
        self.last_chair_area_sync = 0
        self.last_couch_area_sync = 0
        self.last_bench_area_sync = 0

        # Use CameraStateManager to set the camera_id
        camera_state_manager.set_camera_id(camera_id)
        
    def run(self):
        while self.running:
            try:
                # 1. Get camera state (including control flags) from streaming server
                flags = get_camera_state_from_server()
                
                if flags is not None:
                    # Check if we received valid flags
                    if flags and isinstance(flags, dict):
                        # Put flags in queue for main thread to consume
                        try:
                            self.flags_queue.put_nowait(("flags_update", flags))
                            # print(f"[FlagSync] Flags synced from server ({len(flags)} items)")
                        except queue.Full:
                            pass
                        
                        # Reset error count on success
                        self.connection_errors = 0
                        self.last_successful_sync = time.time()
                    else:
                        # print(f"[FlagSync] Invalid flags data received")
                        self.connection_errors += 1
                else:
                    # print(f"[FlagSync] Failed to get camera state from server")
                    self.connection_errors += 1
                
                # 2. Get safe areas from streaming server (Removed)
                # Safe areas functionality is now obsolete
                
                # 3. Get bed areas from streaming server (less frequent)
                current_time = time.time()
                if current_time - self.last_bed_area_sync > (SAFE_AREA_SYNC_INTERVAL_MS / 1000.0):
                    if self.bed_areas_queue is not None:
                        bed_areas = get_bed_areas_from_server()

                        if bed_areas is not None and isinstance(bed_areas, list):
                            try:
                                self.bed_areas_queue.put_nowait(("bed_areas_update", bed_areas))
                                logger.print("FLAG_SYNC", "Bed areas synced (%d polygons)", len(bed_areas))
                                self.last_bed_area_sync = current_time
                            except queue.Full:
                                pass
                        else:
                            logger.print("FLAG_SYNC", "Failed to get bed areas from server")

                # 4. Get floor areas from streaming server (less frequent)
                if current_time - self.last_floor_area_sync > (SAFE_AREA_SYNC_INTERVAL_MS / 1000.0):
                    if self.floor_areas_queue is not None:
                        floor_areas = get_floor_areas_from_server()

                        if floor_areas is not None and isinstance(floor_areas, list):
                            try:
                                self.floor_areas_queue.put_nowait(("floor_areas_update", floor_areas))
                                logger.print("FLAG_SYNC", "Floor areas synced (%d polygons)", len(floor_areas))
                                self.last_floor_area_sync = current_time
                            except queue.Full:
                                pass
                        else:
                            logger.print("FLAG_SYNC", "Failed to get floor areas from server")

                # 5. Get chair areas from streaming server (less frequent)
                if current_time - self.last_chair_area_sync > (SAFE_AREA_SYNC_INTERVAL_MS / 1000.0):
                    if self.chair_areas_queue is not None:
                        chair_areas = get_chair_areas_from_server()

                        if chair_areas is not None and isinstance(chair_areas, list):
                            try:
                                self.chair_areas_queue.put_nowait(("chair_areas_update", chair_areas))
                                logger.print("FLAG_SYNC", "Chair areas synced (%d polygons)", len(chair_areas))
                                self.last_chair_area_sync = current_time
                            except queue.Full:
                                pass
                        else:
                            logger.print("FLAG_SYNC", "Failed to get chair areas from server")

                # 6. Get couch areas from streaming server (less frequent)
                if current_time - self.last_couch_area_sync > (SAFE_AREA_SYNC_INTERVAL_MS / 1000.0):
                    if self.couch_areas_queue is not None:
                        couch_areas = get_couch_areas_from_server()

                        if couch_areas is not None and isinstance(couch_areas, list):
                            try:
                                self.couch_areas_queue.put_nowait(("couch_areas_update", couch_areas))
                                logger.print("FLAG_SYNC", "Couch areas synced (%d polygons)", len(couch_areas))
                                self.last_couch_area_sync = current_time
                            except queue.Full:
                                pass
                        else:
                            logger.print("FLAG_SYNC", "Failed to get couch areas from server")

                # 7. Get bench areas from streaming server (less frequent)
                if current_time - self.last_bench_area_sync > (SAFE_AREA_SYNC_INTERVAL_MS / 1000.0):
                    if self.bench_areas_queue is not None:
                        bench_areas = get_bench_areas_from_server()

                        if bench_areas is not None and isinstance(bench_areas, list):
                            try:
                                self.bench_areas_queue.put_nowait(("bench_areas_update", bench_areas))
                                logger.print("FLAG_SYNC", "Bench areas synced (%d polygons)", len(bench_areas))
                                self.last_bench_area_sync = current_time
                            except queue.Full:
                                pass
                        else:
                            logger.print("FLAG_SYNC", "Failed to get bench areas from server")
                

            except requests.exceptions.Timeout:
                self.connection_errors += 1
                if self.connection_errors % 5 == 0:
                    logger.print("FLAG_SYNC", "Timeout connecting to server (%d errors)", self.connection_errors)
            
            except Exception as e:
                self.connection_errors += 1
                if self.connection_errors % 10 == 0:
                    logger.print("FLAG_SYNC", "Error (%d): %s", self.connection_errors, e)
            
            # Sleep for sync interval
            time.sleep(self.sync_interval)
            
            # If too many errors, increase interval
            if self.connection_errors > 20:
                # print(f"[FlagSync] High error count, increasing sync interval")
                time.sleep(5.0)  # Longer sleep on many errors
    
    def stop(self):
        self.running = False

class StateReporterWorker(threading.Thread):
    """Background thread for reporting camera state to streaming server
    
    This worker sends periodic heartbeats and state reports to the streaming server.
    It reports to the /api/stream/report-state endpoint (NOT the command endpoint).
    The camera only reports state - it cannot modify control flags.
    """
    
    def __init__(self, streaming_url, camera_id, report_interval_ms=STATE_REPORT_INTERVAL_MS):
        super().__init__(daemon=True)
        self.streaming_url = streaming_url
        self.camera_id = camera_id
        self.report_interval = report_interval_ms / 1000.0  # Convert to seconds
        self.running = True
        self.last_report_time = 0
        
    def run(self):
        while self.running:
            try:
                current_time = time.time()
                
                # Check if it's time to send a heartbeat/report
                if current_time - self.last_report_time > self.report_interval:
                    # Report state using the helper function
                    success = report_state(
                        rtmp_connected=False,
                        is_recording=get_is_recording()
                    )
                    
                    if success:
                        logger.print("STATE_REPORT", "State reported successfully")
                    else:
                        logger.print("STATE_REPORT", "Failed to report state")
                    
                    self.last_report_time = current_time
                
                # Sleep for report interval
                time.sleep(self.report_interval)
                    
            except Exception as e:
                logger.print("STATE_REPORT", "Error: %s", e)
                time.sleep(5.0)  # Sleep on error
    
    def stop(self):
        self.running = False

class FrameUploadWorker(threading.Thread):
    """Background thread for uploading frames to streaming server (UDP-like behavior)

    This worker works asynchronously from the main loop. It shares a reference to
    the latest frame with the main thread. When uploading:
    - If no upload is in progress: upload the current latest frame
    - If upload is already in progress: skip and wait for next frame
    - When upload finishes: upload the current latest frame immediately
    - Rate limited to 100ms intervals (10 FPS max) to avoid overwhelming server

    This achieves UDP-like behavior where slow uploads don't pile up - old frames
    are dropped and only the latest frame is uploaded.

    Always sends RAW frames (no overlays) to streaming server.

    Background uploads have higher priority than regular frame uploads.
    """

    def __init__(self, streaming_url, camera_id, profiler_enabled=False):
        super().__init__(daemon=True)
        self.streaming_url = streaming_url
        self.camera_id = camera_id
        self.running = True
        self.uploading = False

        # Shared frame reference - main thread writes, worker reads
        # Using a lock for thread-safe access
        self._frame_lock = threading.Lock()
        self._current_frame = None
        self._frame_timestamp = 0

        # Shared background reference - main thread writes, worker reads
        # Using a separate lock for background-specific operations
        self._background_lock = threading.Lock()
        self._current_background = None
        self._background_timestamp = 0

        # Upload statistics for debugging
        self.upload_count = 0
        self.skip_count = 0
        self.background_upload_count = 0

        # Rate limiting: max 10 FPS (100ms intervals)
        self._min_upload_interval_ms = 100  # 100ms = 10 FPS max
        self._last_upload_time = 0

        # Profiler for upload FPS calculation
        self._profiler_enabled = profiler_enabled
        self.upload_profiler = TaskProfiler(task_name="Frame Upload", print_interval=30, enabled=self._profiler_enabled)
        self.upload_profiler.register_subtasks(["frame_upload"])
        self.upload_profiler.start_frame()
        self.upload_profiler.start_task("frame_upload")
        
    def update_frame(self, frame_data):
        """Update the shared frame reference (called from main thread after disp.show)
        
        Args:
            frame_data: JPEG bytes of the latest rendered frame
        """
        with self._frame_lock:
            self._current_frame = frame_data
            self._frame_timestamp = time.time()
    
    def get_frame(self):
        """Get the current frame for upload (thread-safe)
        
        Returns:
            The latest frame data, or None if no frame available
        """
        with self._frame_lock:
            return self._current_frame
            
    def clear_frame(self):
        """Clear the current frame after successful upload"""
        with self._frame_lock:
            self._current_frame = None
            
    def update_background(self, background_data):
        """Update the shared background reference (called when background is updated)
        
        Args:
            background_data: JPEG bytes of the background image
        """
        with self._background_lock:
            self._current_background = background_data
            self._background_timestamp = time.time()
        logger.print("FRAME_UPLOAD", "Background update queued for upload")
    
    def get_background(self):
        """Get the current background for upload (thread-safe)
        
        Returns:
            The latest background data, or None if no background available
        """
        with self._background_lock:
            return self._current_background
            
    def clear_background(self):
        """Clear the current background after successful upload"""
        with self._background_lock:
            self._current_background = None
            
    def run(self):
        """Main worker loop - continuously try to upload latest frame
        
        Background uploads have higher priority than regular frame uploads.
        """
        while self.running:
            try:
                # PRIORITY 1: Check for background upload (higher priority)
                current_background = self.get_background()
                
                if current_background is not None:
                    # Check if we're already uploading
                    if self.uploading:
                        # Skip regular frame upload if background is pending
                        time.sleep(0.005)
                        continue
                    
                    # Start background upload
                    self.uploading = True
                    logger.print("FRAME_UPLOAD", "Uploading background image...")
                    
                    # Upload the background
                    bg_upload_start = time_ms()
                    success = send_background_to_server(current_background, self.camera_id)
                    bg_upload_duration = time_ms() - bg_upload_start
                    
                    if success:
                        self.background_upload_count += 1
                        self.clear_background()
                        logger.print("FRAME_UPLOAD", "Background uploaded successfully (%d total)", self.background_upload_count)
                    else:
                        logger.print("FRAME_UPLOAD", "Failed to upload background (will retry)")
                        # Keep the background for retry on next iteration
                    
                    # Background upload complete
                    self.uploading = False
                    time.sleep(0.01)  # Brief pause after background upload
                    continue
                
                # PRIORITY 2: Regular frame upload (ALWAYS send raw frames)
                # Get the current frame
                current_frame = self.get_frame()

                if current_frame is None:
                    # No frame available yet, sleep briefly
                    time.sleep(0.01)  # 10ms
                    continue

                # Check rate limiting: enforce 100ms min interval between uploads
                current_time = time_ms()
                time_since_last_upload = current_time - self._last_upload_time
                if time_since_last_upload < self._min_upload_interval_ms:
                    # Too soon to upload again, sleep and continue
                    time.sleep(0.01)  # 10ms
                    continue

                # Check if we're already uploading
                if self.uploading:
                    # UDP-like behavior: skip this frame, will upload latest on next iteration
                    self.skip_count += 1
                    time.sleep(0.01)  # Brief sleep before checking again
                    continue

                # Start upload with profiling
                self.uploading = True
                self.upload_profiler.start_frame()
                self.upload_profiler.start_task("frame_upload")

                # Upload the frame
                upload_start = time_ms()
                success = send_frame_to_server(current_frame, self.camera_id)
                upload_duration = time_ms() - upload_start
                self._last_upload_time = time_ms()  # Update last upload time

                # End profiling for this upload
                self.upload_profiler.end_task("frame_upload")
                self.upload_profiler.end_frame()

                if success:
                    self.upload_count += 1
                    # Clear frame after successful upload
                    self.clear_frame()

                    # Log periodically with upload FPS
                    if self.upload_count % 30 == 0:
                        avg_upload_time = upload_duration
                        upload_fps = 1000.0 / avg_upload_time if avg_upload_time > 0 else 0
                        logger.print("FRAME_UPLOAD", "Uploaded %d frames, skipped %d, avg upload: %.2fms, FPS: %.2f (capped at 10 FPS)",
                                    self.upload_count, self.skip_count, avg_upload_time, upload_fps)
                else:
                    logger.print("FRAME_UPLOAD", "Failed to upload frame")
                    # Keep the frame for retry on next iteration

                # Upload complete, check for new frame
                self.uploading = False

                # Rate limit: sleep to maintain ~100ms intervals
                time.sleep(0.05)  # 50ms to give some buffer
                
            except Exception as e:
                logger.print("FRAME_UPLOAD", "Error: %s", e)
                self.uploading = False
                time.sleep(0.1)  # Longer sleep on error
    
    def stop(self):
        """Stop the worker"""
        self.running = False

class PingWorker(threading.Thread):
    """Background thread for sending periodic pings to streaming server

    This worker pings the streaming server every 250ms to notify the server
    that this camera is connected and alive. Uses fire-and-forget pattern.

    Pings are only sent when the camera is registered (not pending/unregistered).
    The registration status is checked dynamically using CameraStateManager.
    """
    
    def __init__(self, streaming_url, camera_id, ping_interval_ms=250):
        super().__init__(daemon=True)
        self.streaming_url = streaming_url
        self.camera_id = camera_id
        self.ping_interval = ping_interval_ms / 1000.0  # Convert to seconds
        self.running = True
        
        # Use CameraStateManager for registration status checks
        camera_state_manager.set_camera_id(camera_id)
        
    def run(self):
        from streaming import ping_streaming_server
        
        while self.running:
            try:
                # Only ping if camera is registered
                # Check registration status dynamically via CameraStateManager
                status = camera_state_manager.get_registration_status()
                
                # Get camera_id from CameraStateManager (in case it was updated)
                camera_id = camera_state_manager.get_camera_id()
                
                if status == "registered" and camera_id and camera_id != "camera_000":
                    # Fire-and-forget ping - don't wait for response
                    ping_streaming_server(camera_id)
            except Exception:
                # Silently ignore ping errors
                pass
            
            # Sleep for ping interval
            time.sleep(self.ping_interval)
    
    def stop(self):
        self.running = False

# ============================================
# COMMAND SERVER (for receiving commands from streaming server)
# ============================================
command_server_running = True
received_commands = []
commands_lock = threading.Lock()

class CommandReceiver(threading.Thread):
    """HTTP server to receive commands from streaming server"""
    
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
            logger.print("CMD_SERVER", "Command server listening on port %d", LOCAL_PORT)
            
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
                    logger.print("CMD_SERVER", "Accept error: %s", e)
                    time.sleep(0.1)
                    
        except Exception as e:
            logger.print("CMD_SERVER", "Failed to start: %s", e)
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
                                response += json.dumps({"status": "success", "camera_id": camera_state_manager.get_camera_id()})
                                conn.send(response.encode())
                                logger.print("CMD_SERVER", "Received command from %s: %s = %s", addr[0], data.get('command'), data.get('value'))
                    else:
                        response = "HTTP/1.1 404 Not Found\r\n\r\n"
                        conn.send(response.encode())
                        
                except Exception as e:
                    logger.print("CMD_SERVER", "Command parsing error: %s", e)
                    response = "HTTP/1.1 400 Bad Request\r\n\r\nError"
                    conn.send(response.encode())
            
            conn.close()
            
        except Exception as e:
            logger.print("CMD_SERVER", "Client handling error: %s", e)
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

def get_received_commands():
    """Get and clear received commands"""
    global received_commands
    with commands_lock:
        commands = received_commands.copy()
        received_commands = []
    return commands

def handle_command(command, value, camera_id, registration_status, save_camera_info_func):
    """Handle command from streaming server"""
    logger.print("CMD", "Processing command: %s = %s", command, value)
    
    if command == "set_background":
        if value:
            logger.print("CMD", "Background update requested")
            # Will be handled in main loop
    elif command == "update_safe_areas":
        if isinstance(value, list):
            pass # Safe areas valid, but functionality removed
            # from control_manager import update_safety_checker_polygons
            # update_safety_checker_polygons(value)
    elif command in get_default_control_flags():
        from control_manager import camera_state_manager, get_control_flags, update_control_flag, get_camera_id
        from tools.polygon_checker import CheckMethod
        update_control_flag(command, value)
    elif command == "set_fall_algorithm":
        algorithm = int(value) if isinstance(value, (int, float)) else 3
        if algorithm in [1, 2, 3]:
            from control_manager import update_control_flag
            update_control_flag("fall_algorithm", algorithm)
            logger.print("CMD", "Fall algorithm set to %d", algorithm)
    elif command == "forget_camera":
        if value == camera_id:
            logger.print("CMD", "Camera forget command received!")
            logger.print("CMD", "This camera will be removed from the registry.")
            save_camera_info_func("camera_000", "Unnamed Camera", "unregistered", "")
    elif command == "approve_camera":
        logger.print("CMD", "Camera approval received!")
        if registration_status == "pending":
            registration_status = "registered"
            save_camera_info_func(camera_id, camera_state_manager.get_camera_name(), "registered", "")
            logger.print("CMD", "Camera status updated to: registered")

def get_default_control_flags():
    """Get default control flags"""
    return {
        "record": False,
        "show_raw": False,
        "set_background": False,
        "auto_update_bg": False,
        "show_safe_areas": False,
        "show_bed_areas": False,
        "show_floor_areas": False,
        "use_safety_check": True,
        "fall_algorithm": 1,
        "check_method": 3,  # CheckMethod.TORSO_HEAD
        "hme": False
    }

# Recording state synchronization
_recording_state = {
    "is_recording": False
}

def update_is_recording(recording):
    """Update and synchronize is_recording state across modules
    
    This function should be called whenever recording state changes.
    It ensures all modules have access to the current recording state.
    
    Args:
        recording: Boolean indicating if recording is active
    """
    global _recording_state
    old_state = _recording_state["is_recording"]
    _recording_state["is_recording"] = recording
    if old_state != recording:
        logger.print("SYNC", "is_recording updated: %s -> %s", old_state, recording)

def get_is_recording():
    """Get current recording state
    
    Returns:
        Boolean indicating if recording is currently active
    """
    return _recording_state["is_recording"]


# ============================================

# Global reference to the tracks sender worker
_tracks_worker = None

def set_tracks_worker(worker):
    """Set the global tracks sender worker reference.
    
    Args:
        worker: TracksSenderWorker instance
    """
    global _tracks_worker
    _tracks_worker = worker

def update_latest_tracks(processed_tracks):
    """Update latest tracks for async sending to streaming servers.

    This function is called from the main thread to update the latest tracks
    for the TracksSenderWorker to send asynchronously. Always sends the latest
    tracks, dropping old ones (UDP-like behavior).

    IMPORTANT: After calling this function, you must call mark_tracks_as_ready()
    to signal the worker that tracks are ready to send.

    Args:
        processed_tracks: List of track dictionaries, each containing:
            - track_id: int
            - keypoints: list of 34 floats (17 keypoints × 2 coordinates)
            - bbox: list [x, y, w, h]
            - pose_label: str
            - safety_status: str (normal, unsafe, fall)

    Returns:
        bool: True if data was updated successfully, False otherwise
    """
    # Check if worker is available
    if _tracks_worker is None:
        return False

    try:
        # Update the latest tracks (always overwrites previous)
        _tracks_worker.update_tracks(processed_tracks)
        return True
    except Exception as e:
        logger.print("TRACKS_SENDER", "Failed to update tracks data: %s", e)
        return False


def mark_tracks_as_ready():
    """Mark tracks as ready to send (called from main thread after processing).

    This signals the TracksSenderWorker that all tracks have been processed
    and are ready to be sent to the servers.

    The worker monitors this flag and will send the tracks when it's set to True,
    then reset it to False after sending.

    Returns:
        bool: True if flag was set successfully, False otherwise
    """
    # Check if worker is available
    if _tracks_worker is None:
        return False

    try:
        _tracks_worker.mark_tracks_ready()
        return True
    except Exception as e:
        logger.print("TRACKS_SENDER", "Failed to mark tracks as ready: %s", e)
        return False


class TracksSenderWorker(threading.Thread):
    """Background worker for sending all processed tracks to streaming server.

    This worker waits for the main thread to finish processing tracks, then sends them
    to the streaming server in the background, preventing blocking of the main loop.

    Uses a flag-based synchronization mechanism:
    - Main thread processes tracks and sets _tracks_ready flag to True
    - Worker monitors the flag and sends tracks when True
    - Worker resets flag to False after sending

    UDP-like behavior: Always sends the latest tracks, dropping old ones if sending is in progress.
    """

    def __init__(self, camera_id, profiler_enabled=False):
        super().__init__(daemon=True)
        self.camera_id = camera_id
        self.running = True

        # Shared tracks reference - main thread writes, worker reads
        # Using a lock for thread-safe access
        self._tracks_lock = threading.Lock()
        self._current_tracks = None
        self._tracks_timestamp = 0
        self._sending = False

        # Flag to track if tracks have been processed and ready to send
        self._tracks_ready_lock = threading.Lock()
        self._tracks_ready = False

        # Statistics for monitoring
        self.sent_count = 0
        self.error_count = 0
        self.skip_count = 0



        # Rate limiting: max 10 FPS (100ms intervals)
        self._min_send_interval_ms = 100  # 100ms = 10 FPS max
        self._last_send_time = 0

        # Profiler for tracks sending
        self._profiler_enabled = profiler_enabled
        self.profiler = TaskProfiler(task_name="Tracks Sender", print_interval=30, enabled=self._profiler_enabled)
        self.profiler.register_subtasks(["tracks_send", "streaming_send"])
        self.profiler.start_frame()

    def update_tracks(self, processed_tracks):
        """Update the latest tracks to send (called from main thread).

        Args:
            processed_tracks: List of track dictionaries
        """
        with self._tracks_lock:
            self._current_tracks = processed_tracks
            self._tracks_timestamp = time_ms()

    def mark_tracks_ready(self):
        """Mark tracks as ready to send (called from main thread after processing).

        This signals the worker that all tracks have been processed and are ready
        to be sent to the servers.
        """
        with self._tracks_ready_lock:
            self._tracks_ready = True

    def _get_current_tracks(self):
        """Get current tracks (thread-safe).

        Returns:
            tuple: (tracks, timestamp) or (None, 0) if no tracks
        """
        with self._tracks_lock:
            if self._current_tracks is not None:
                return (self._current_tracks.copy(), self._tracks_timestamp)
            return (None, 0)

    def _is_tracks_ready(self):
        """Check if tracks are ready to send (thread-safe).

        Returns:
            bool: True if tracks are ready to send
        """
        with self._tracks_ready_lock:
            return self._tracks_ready

    def _reset_tracks_ready(self):
        """Reset the tracks ready flag (thread-safe)."""
        with self._tracks_ready_lock:
            self._tracks_ready = False
        
    def run(self):
        """Main worker loop - wait for tracks to be processed, then send.

        Synchronization mechanism:
        - Main thread processes all tracks and calls mark_tracks_ready()
        - This worker monitors the _tracks_ready flag
        - When flag is True, worker sends all tracks and resets flag to False
        - UDP-like behavior: If sending is in progress, skip and wait for next frame

        Always sends the latest tracks, dropping old ones.
        """
        logger.print("TRACKS_SENDER", "Starting worker for camera: %s", self.camera_id)

        while self.running:
            try:
                # Check if tracks are ready to send (flag set by main thread)
                if not self._is_tracks_ready():
                    # Tracks not ready yet, sleep briefly and check again
                    time.sleep(0.001)  # 1ms
                    continue

                # Get current tracks
                current_tracks, _ = self._get_current_tracks()

                if current_tracks is None:
                    # No tracks available yet, reset flag and sleep
                    self._reset_tracks_ready()
                    time.sleep(0.001)  # 1ms
                    continue

                # Check rate limiting: enforce 100ms min interval between sends
                current_time = time_ms()
                time_since_last_send = current_time - self._last_send_time
                if time_since_last_send < self._min_send_interval_ms:
                    # Too soon to send again, reset flag and sleep
                    self._reset_tracks_ready()
                    time.sleep(0.01)  # 10ms
                    continue

                # Check if we're already sending
                if self._sending:
                    # UDP-like behavior: skip this frame, will send latest on next iteration
                    self.skip_count += 1
                    self._reset_tracks_ready()
                    time.sleep(0.01)  # 10ms
                    continue

                # Start sending with profiling
                self._sending = True
                self.profiler.start_frame()
                self.profiler.start_task("tracks_send")

                # Send the tracks
                send_start = time_ms()
                self._send_tracks(current_tracks)
                send_duration = time_ms() - send_start
                self._last_send_time = time_ms()  # Update last send time

                # End profiling for this send
                self.profiler.end_task("tracks_send")
                self.profiler.end_frame()

                self.sent_count += 1

                # Log periodically with send stats
                if self.sent_count % 30 == 0:
                    avg_send_time = send_duration
                    send_fps = 1000.0 / avg_send_time if avg_send_time > 0 else 0
                    logger.print("TRACKS_SENDER", "Sent %d batches, skipped %d, avg send: %.2fms, FPS: %.2f (capped at 10 FPS)",
                                self.sent_count, self.skip_count, avg_send_time, send_fps)

                # Send complete, reset the ready flag for next frame
                self._sending = False
                self._reset_tracks_ready()

            except Exception as e:
                logger.print("TRACKS_SENDER", "Error in main loop: %s", e)
                self.error_count += 1
                self._sending = False
                self._reset_tracks_ready()  # Reset flag on error
                time.sleep(0.1)  # Sleep on error

        logger.print("TRACKS_SENDER", "Stopped (sent=%d, errors=%d, skipped=%d)", self.sent_count, self.error_count, self.skip_count)
    
    def _send_tracks(self, processed_tracks):
        """Send all processed tracks to streaming server asynchronously.
        
        Args:
            processed_tracks: List of track dictionaries, each containing:
                - track_id: int
                - keypoints: list of 34 floats (17 keypoints × 2 coordinates)
                - bbox: list [x, y, w, h]
                - pose_label: str
                - safety_status: str (normal, unsafe, fall)
                - safety_reason: str (e.g., "lying_on_floor", "unsafe_sleep_too_long", "normal")
        """
        try:
            from control_manager import get_flag
            
            # Prepare tracks for streaming server
            tracks_for_streaming = []
            for track in processed_tracks:
                track_for_streaming = {
                    "track_id": track.get("track_id"),
                    "keypoints": track.get("keypoints", []),
                    "bbox": track.get("bbox"),
                    "pose_label": track.get("pose_label", "unknown"),
                    "safety_status": track.get("status", "tracking"),
                    "safety_reason": track.get("safety_reason", "normal"),
                    "int_features": track.get("int_features")
                }
                tracks_for_streaming.append(track_for_streaming)
            
            # Send to streaming server asynchronously (fire-and-forget)
            # Always send tracks, even if empty list, to ensure server knows current state
            self.profiler.start_task("streaming_send")
            self._send_to_streaming_async(self.camera_id, tracks_for_streaming)
            self.profiler.end_task("streaming_send")
                
        except Exception as e:
            logger.print("TRACKS_SENDER", "Error sending tracks: %s", e)
            self.error_count += 1
    
    def _send_to_streaming_async(self, camera_id, tracks):
        """Send tracks to streaming server asynchronously using a thread."""
        def _send():
            try:
                from streaming import send_tracks_to_streaming_server
                send_tracks_to_streaming_server(camera_id, tracks)
            except Exception as e:
                logger.print("TRACKS_SENDER", "Error in async streaming send: %s", e)
        
        thread = threading.Thread(target=_send, daemon=True)
        thread.start()
    
    def stop(self):
        """Stop the worker."""
        self.running = False
    
    def get_stats(self):
        """Get worker statistics.
        
        Returns:
            dict: Statistics about the worker's activity
        """
        return {
            "sent": self.sent_count,
            "errors": self.error_count
        }

