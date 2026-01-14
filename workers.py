# workers.py - Async worker classes for streaming server communication

import queue
import time
import requests
import socket
import select
import json
import threading
from config import (
    STREAMING_HTTP_URL, FLAG_SYNC_INTERVAL_MS, 
    SAFE_AREA_SYNC_INTERVAL_MS, STATE_REPORT_INTERVAL_MS,
    LOCAL_PORT
)
from control_manager import (
    get_camera_state_from_server, get_safe_areas_from_server, report_state,
    camera_state_manager
)
from streaming import send_frame_to_server
from tools.time_utils import time_ms, FrameProfiler

class FlagAndSafeAreaSyncWorker(threading.Thread):
    """Background thread for syncing flags AND safe areas from streaming server"""
    
    def __init__(self, flags_queue, safe_areas_queue, streaming_url, camera_id, 
                 sync_interval_ms=FLAG_SYNC_INTERVAL_MS):
        super().__init__(daemon=True)
        self.flags_queue = flags_queue
        self.safe_areas_queue = safe_areas_queue
        self.streaming_url = streaming_url
        self.camera_id = camera_id
        self.sync_interval = sync_interval_ms / 1000.0  # Convert to seconds
        self.running = True
        self.connection_errors = 0
        self.max_errors = 10
        self.last_successful_sync = 0
        self.last_safe_area_sync = 0
        
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
                
                # 2. Get safe areas from streaming server (less frequent)
                current_time = time.time()
                if current_time - self.last_safe_area_sync > (SAFE_AREA_SYNC_INTERVAL_MS / 1000.0):
                    safe_areas = get_safe_areas_from_server()
                    
                    if safe_areas is not None and isinstance(safe_areas, list):
                        try:
                            self.safe_areas_queue.put_nowait(("safe_areas_update", safe_areas))
                            # print(f"[SafeAreaSync] Safe areas synced ({len(safe_areas)} polygons)")
                            self.last_safe_area_sync = current_time
                        except queue.Full:
                            pass
                    else:
                        print(f"[SafeAreaSync] Failed to get safe areas from server")
                
            except requests.exceptions.Timeout:
                self.connection_errors += 1
                if self.connection_errors % 5 == 0:
                    print(f"[FlagSync] Timeout connecting to server ({self.connection_errors} errors)")
            
            except Exception as e:
                self.connection_errors += 1
                if self.connection_errors % 10 == 0:
                    print(f"[FlagSync] Error ({self.connection_errors}): {e}")
            
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
                    # Report state using the helper function (RTMP always False now)
                    success = report_state(
                        rtmp_connected=False,  # OBSOLETE: RTMP removed
                        is_recording=get_is_recording()
                    )
                    
                    if success:
                        print(f"[StateReporter] State reported successfully to /api/stream/report-state")
                    else:
                        print(f"[StateReporter] Failed to report state")
                    
                    self.last_report_time = current_time
                
                # Sleep for report interval
                time.sleep(self.report_interval)
                    
            except Exception as e:
                print(f"[StateReporter] Error: {e}")
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
    
    This achieves UDP-like behavior where slow uploads don't pile up - old frames
    are dropped and only the latest frame is uploaded.
    """
    
    def __init__(self, streaming_url, camera_id):
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
        
        # Upload statistics for debugging
        self.upload_count = 0
        self.skip_count = 0
        
        # Profiler for upload FPS calculation
        self.upload_profiler = FrameProfiler(print_interval=30, enabled=True)
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
            
    def run(self):
        """Main worker loop - continuously try to upload latest frame"""
        while self.running:
            try:
                # Get the current frame
                current_frame = self.get_frame()
                
                if current_frame is None:
                    # No frame available yet, sleep briefly
                    time.sleep(0.01)  # 10ms
                    continue
                
                # Check if we're already uploading
                if self.uploading:
                    # UDP-like behavior: skip this frame, will upload latest on next iteration
                    self.skip_count += 1
                    time.sleep(0.005)  # Brief sleep before checking again
                    continue
                
                # Start upload with profiling
                self.uploading = True
                self.upload_profiler.start_frame()
                self.upload_profiler.start_task("frame_upload")
                
                # Upload the frame
                upload_start = time_ms()
                success = send_frame_to_server(current_frame, self.camera_id)
                upload_duration = time_ms() - upload_start
                
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
                        print(f"[FrameUpload] Uploaded {self.upload_count} frames, skipped {self.skip_count}, avg upload: {avg_upload_time:.2f}ms, FPS: {upload_fps:.2f}")
                else:
                    print(f"[FrameUpload] Failed to upload frame")
                    # Keep the frame for retry on next iteration
                
                # Upload complete, check for new frame
                self.uploading = False
                
                # Small delay between uploads to avoid overwhelming server
                time.sleep(0.005)  # 5ms
                
            except Exception as e:
                print(f"[FrameUpload] Error: {e}")
                self.uploading = False
                time.sleep(0.1)  # Longer sleep on error
    
    def stop(self):
        """Stop the worker"""
        self.running = False

class PingWorker(threading.Thread):
    """Background thread for sending periodic pings to streaming server
    
    This worker pings the streaming server every 250ms to notify the server
    that this camera is connected and alive. Uses fire-and-forget pattern.
    
    IMPORTANT: Pings are only sent when the camera is registered (not pending/unregistered).
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
                    time.sleep(0.1)
                    
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
                                response += json.dumps({"status": "success", "camera_id": camera_state_manager.get_camera_id()})
                                conn.send(response.encode())
                                print(f"Received command from {addr[0]}: {data.get('command')} = {data.get('value')}")
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

def get_received_commands():
    """Get and clear received commands"""
    global received_commands
    with commands_lock:
        commands = received_commands.copy()
        received_commands = []
    return commands

def handle_command(command, value, camera_id, registration_status, save_camera_info_func):
    """Handle command from streaming server"""
    print(f"Processing command: {command} = {value}")
    
    if command == "set_background":
        if value:
            print("Background update requested")
            # Will be handled in main loop
    elif command == "update_safe_areas":
        if isinstance(value, list):
            from control_manager import update_safety_checker_polygons
            update_safety_checker_polygons(value)
    elif command in get_default_control_flags():
        from control_manager import update_control_flag
        update_control_flag(command, value)
    elif command == "set_fall_algorithm":
        algorithm = int(value) if isinstance(value, (int, float)) else 3
        if algorithm in [1, 2, 3]:
            from control_manager import update_control_flag
            update_control_flag("fall_algorithm", algorithm)
            print(f"Fall algorithm set to {algorithm}")
    elif command == "forget_camera":
        if value == camera_id:
            print("⚠️ Camera forget command received!")
            print("This camera will be removed from the registry.")
            save_camera_info_func("camera_000", "Unnamed Camera", "unregistered", "")
    elif command == "approve_camera":
        print(f"✅ Camera approval received!")
        if registration_status == "pending":
            registration_status = "registered"
            save_camera_info_func(camera_id, camera_state_manager.get_camera_name(), "registered", "")
            print(f"Camera status updated to: registered")

def get_default_control_flags():
    """Get default control flags"""
    return {
        "record": False,
        "show_raw": False,
        "set_background": False,
        "auto_update_bg": False,
        "show_safe_area": False,
        "use_safety_check": True,
        "analytics_mode": True,
        "fall_algorithm": 3,
        "hme": False
    }

# OBSOLETE: RTMP-related functions kept as reference only (commented out):
# def update_rtmp_connected(value):
#     """OBSOLETE: RTMP has been removed"""
#     global rtmp_connected
#     rtmp_connected = False  # Always False now

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
        print(f"[SYNC] is_recording updated: {old_state} -> {recording}")

def get_is_recording():
    """Get current recording state
    
    Returns:
        Boolean indicating if recording is currently active
    """
    return _recording_state["is_recording"]

