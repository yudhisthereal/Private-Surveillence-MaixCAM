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
    LOCAL_PORT, ANALYTICS_API_URL, ANALYTICS_TIMEOUT,
    ANALYTICS_RETRY_COUNT, ANALYTICS_RETRY_BACKOFF
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


# ============================================
# ANALYTICS CLIENT AND WORKER
# ============================================

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class AnalyticsClient:
    """Client for interacting with the Analytics Server (http://103.127.136.213:5000)
    
    Provides pose estimation and fall detection capabilities through REST API endpoints.
    """
    
    def __init__(self, base_url=None, timeout=10, max_retries=3, backoff_factor=1.0):
        self.base_url = base_url or ANALYTICS_API_URL
        self.timeout = timeout
        
        # Setup session with retry strategy
        self.session = requests.Session()
        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=backoff_factor,
            status_forcelist=[429, 500, 502, 503, 504]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
    
    def health_check(self):
        """Check if the analytics server is healthy.
        
        Returns:
            bool: True if server is healthy, False otherwise
        """
        try:
            response = self.session.get(
                f"{self.base_url}/api/analytics/health",
                timeout=self.timeout
            )
            if response.status_code == 200:
                data = response.json()
                return data.get("status") == "healthy"
            return False
        except requests.exceptions.RequestException as e:
            print(f"[AnalyticsClient] Health check failed: {e}")
            return False
    
    def analyze_pose(self, keypoints, bbox=None, track_id=0, camera_id="", use_hme=False):
        """Analyze pose from 17 keypoints (COCO format).
        
        Args:
            keypoints: List of 34 floats (17 keypoints × 2 coordinates)
            bbox: Optional bounding box [x, y, width, height]
            track_id: Optional track ID for multi-object tracking
            camera_id: Optional camera identifier
            use_hme: Whether to use Homomorphic Encryption (default: False)
        
        Returns:
            dict: Pose analysis result or None on failure
        """
        payload = {
            "keypoints": keypoints,
            "track_id": track_id,
            "camera_id": camera_id,
            "use_hme": use_hme
        }
        if bbox is not None:
            payload["bbox"] = bbox
        
        try:
            response = self.session.post(
                f"{self.base_url}/api/analytics/analyze-pose",
                json=payload,
                timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"[AnalyticsClient] Pose analysis failed: {e}")
            return None
    
    def detect_fall(self, camera_id, track_id, pose_data, current_bbox, 
                    previous_bbox=None, elapsed_ms=33.33, use_hme=False):
        """Detect falls using pose data and bounding box motion analysis.
        
        Args:
            camera_id: Camera identifier (required)
            track_id: Track ID (required)
            pose_data: Pose data from analyze-pose (optional)
            current_bbox: Current bounding box [x, y, width, height] (required)
            previous_bbox: Previous frame's bounding box (optional)
            elapsed_ms: Time since last frame in milliseconds (default: 33.33)
            use_hme: Whether to use HME mode (default: False)
        
        Returns:
            dict: Fall detection result or None on failure
        """
        payload = {
            "camera_id": camera_id,
            "track_id": track_id,
            "pose_data": pose_data,
            "current_bbox": current_bbox,
            "elapsed_ms": elapsed_ms,
            "use_hme": use_hme
        }
        if previous_bbox is not None:
            payload["previous_bbox"] = previous_bbox
        
        try:
            response = self.session.post(
                f"{self.base_url}/api/analytics/detect-fall",
                json=payload,
                timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"[AnalyticsClient] Fall detection failed: {e}")
            return None


# Shared state for analytics results (thread-safe access)
_analytics_state = {
    "pose_data": {},          # track_id -> pose_data mapping
    "fall_data": {},          # track_id -> fall_data mapping
    "server_available": False,  # Whether analytics server is reachable
    "last_health_check": 0     # Timestamp of last successful health check
}
_analytics_lock = threading.Lock()


def get_analytics_pose_data(track_id):
    """Get the latest pose data for a track from analytics server.
    
    Args:
        track_id: The track ID to query
    
    Returns:
        dict or None: Pose data for the track, or None if not available
    """
    with _analytics_lock:
        return _analytics_state["pose_data"].get(track_id)


def get_analytics_fall_data(track_id):
    """Get the latest fall detection data for a track from analytics server.
    
    Args:
        track_id: The track ID to query
    
    Returns:
        dict or None: Fall data for the track, or None if not available
    """
    with _analytics_lock:
        return _analytics_state["fall_data"].get(track_id)


def is_analytics_server_available():
    """Check if the analytics server is available.
    
    Returns:
        bool: True if server is available, False otherwise
    """
    with _analytics_lock:
        return _analytics_state["server_available"]


class AnalyticsWorker(threading.Thread):
    """Background worker for fetching pose and fall detection from Analytics Server.
    
    This worker is only started if control_flags["analytics_mode"] is True.
    It receives track data (keypoints, bbox, track_id) from the main thread via a queue,
    sends it to the Analytics Server for analysis, and updates shared variables with results.
    
    The worker runs asynchronously and doesn't block the main processing loop.
    """
    
    def __init__(self, track_queue, camera_id, check_interval_ms=50):
        super().__init__(daemon=True)
        self.camera_id = camera_id
        self.check_interval = check_interval_ms / 1000.0  # Convert to seconds
        self.running = True
        self.track_queue = track_queue
        
        # Analytics client
        self.client = AnalyticsClient()
        
        # Statistics for monitoring
        self.pose_requests = 0
        self.fall_requests = 0
        self.errors = 0
        
        # Health check interval (every 30 seconds)
        self.health_check_interval = 30.0
        self.last_health_check = 0
        
    def run(self):
        """Main worker loop - process track data from queue."""
        print(f"[AnalyticsWorker] Starting worker for camera: {self.camera_id}")
        
        # Initial health check
        self._check_health()
        
        while self.running:
            try:
                # Check if analytics mode is still enabled
                from control_manager import get_flag
                if not get_flag("analytics_mode", False):
                    # Analytics mode disabled, sleep and continue checking
                    time.sleep(1.0)
                    continue
                
                # Periodic health check
                current_time = time.time()
                if current_time - self.last_health_check > self.health_check_interval:
                    self._check_health()
                
                # Check if server is available
                if not is_analytics_server_available():
                    # Server not available, sleep and retry later
                    time.sleep(1.0)
                    continue
                
                # Get next track data from queue (non-blocking)
                try:
                    track_data = self.track_queue.get_nowait()
                    self._process_track(track_data)
                except queue.Empty:
                    # No track data available, sleep briefly
                    time.sleep(0.01)  # 10ms
                    
            except Exception as e:
                print(f"[AnalyticsWorker] Error in main loop: {e}")
                self.errors += 1
                time.sleep(0.1)  # Sleep on error
        
        print(f"[AnalyticsWorker] Stopped (pose_requests={self.pose_requests}, fall_requests={self.fall_requests}, errors={self.errors})")
    
    def _check_health(self):
        """Check if analytics server is healthy and update control_flags accordingly."""
        try:
            is_healthy = self.client.health_check()
            with _analytics_lock:
                _analytics_state["server_available"] = is_healthy
                if is_healthy:
                    _analytics_state["last_health_check"] = time.time()
            
            # Update control_flags["analytics_mode"] based on server health
            from control_manager import get_flag, update_control_flag
            current_mode = get_flag("analytics_mode", False)
            
            if is_healthy:
                if not current_mode:
                    # Server is healthy, enable analytics mode
                    update_control_flag("analytics_mode", True)
                    print(f"[AnalyticsWorker] Server is healthy: {ANALYTICS_API_URL} - analytics_mode enabled")
                else:
                    print(f"[AnalyticsWorker] Server is healthy: {ANALYTICS_API_URL}")
            else:
                if current_mode:
                    # Server is unhealthy, disable analytics mode
                    update_control_flag("analytics_mode", False)
                    print(f"[AnalyticsWorker] Server health check failed - analytics_mode disabled")
                else:
                    print(f"[AnalyticsWorker] Server health check failed")
        except Exception as e:
            print(f"[AnalyticsWorker] Health check error: {e}")
            with _analytics_lock:
                _analytics_state["server_available"] = False
            # Disable analytics mode on error
            try:
                from control_manager import get_flag, update_control_flag
                if get_flag("analytics_mode", False):
                    update_control_flag("analytics_mode", False)
            except:
                pass
    
    def _process_track(self, track_data):
        """Process a single track data item.
        
        Args:
            track_data: Dictionary containing:
                - track_id: int
                - keypoints: list of 34 floats (17 keypoints × 2)
                - bbox: list of [x, y, width, height]
                - previous_bbox: optional previous bounding box
                - elapsed_ms: time since last frame in ms
        """
        try:
            track_id = track_data.get("track_id")
            keypoints = track_data.get("keypoints")
            bbox = track_data.get("bbox")
            previous_bbox = track_data.get("previous_bbox")
            elapsed_ms = track_data.get("elapsed_ms", 33.33)
            use_hme = track_data.get("use_hme", False)
            
            if not keypoints or not bbox:
                return
            
            # Step 1: Analyze pose
            pose_result = self.client.analyze_pose(
                keypoints=keypoints,
                bbox=bbox,
                track_id=track_id,
                camera_id=self.camera_id,
                use_hme=use_hme
            )
            
            self.pose_requests += 1
            
            if pose_result and pose_result.get("status") == "success":
                pose_data = pose_result.get("pose_data")
                
                # Update shared state with pose data
                with _analytics_lock:
                    _analytics_state["pose_data"][track_id] = {
                        "label": pose_data.get("label"),
                        "torso_angle": pose_data.get("torso_angle"),
                        "thigh_uprightness": pose_data.get("thigh_uprightness"),
                        "thigh_calf_ratio": pose_data.get("thigh_calf_ratio"),
                        "torso_leg_ratio": pose_data.get("torso_leg_ratio"),
                        "thigh_angle": pose_data.get("thigh_angle"),
                        "thigh_length": pose_data.get("thigh_length"),
                        "calf_length": pose_data.get("calf_length"),
                        "torso_height": pose_data.get("torso_height"),
                        "leg_length": pose_data.get("leg_length"),
                        "server_analysis": pose_data.get("server_analysis", True),
                        "timestamp": time.time()
                    }
                
                # Step 2: Detect fall (only if we have previous bbox for motion analysis)
                if previous_bbox is not None:
                    fall_result = self.client.detect_fall(
                        camera_id=self.camera_id,
                        track_id=track_id,
                        pose_data=pose_data,
                        current_bbox=bbox,
                        previous_bbox=previous_bbox,
                        elapsed_ms=elapsed_ms,
                        use_hme=use_hme
                    )
                    
                    self.fall_requests += 1
                    
                    if fall_result and fall_result.get("status") == "success":
                        fall_detection = fall_result.get("fall_detection", {})
                        
                        # Update shared state with fall data
                        with _analytics_lock:
                            _analytics_state["fall_data"][track_id] = {
                                "fall_detected_method1": fall_detection.get("fall_detected_method1", False),
                                "fall_detected_method2": fall_detection.get("fall_detected_method2", False),
                                "fall_detected_method3": fall_detection.get("fall_detected_method3", False),
                                "counter_method1": fall_detection.get("counter_method1", 0),
                                "counter_method2": fall_detection.get("counter_method2", 0),
                                "counter_method3": fall_detection.get("counter_method3", 0),
                                "primary_alert": fall_detection.get("primary_alert", False),
                                "timestamp": time.time()
                            }
                        
                        # Log fall detection result
                        if fall_detection.get("primary_alert"):
                            print(f"[AnalyticsWorker] FALL DETECTED: track_id={track_id}, method3={fall_detection.get('fall_detected_method3')}")
            else:
                self.errors += 1
                
        except Exception as e:
            print(f"[AnalyticsWorker] Error processing track: {e}")
            self.errors += 1
    
    def stop(self):
        """Stop the worker."""
        self.running = False
    
    def get_stats(self):
        """Get worker statistics.
        
        Returns:
            dict: Statistics about the worker's activity
        """
        return {
            "pose_requests": self.pose_requests,
            "fall_requests": self.fall_requests,
            "errors": self.errors,
            "server_available": is_analytics_server_available()
        }


# Global analytics queue reference (set by main.py)
_analytics_queue = None
_pose_label_queue = None  # Queue for pose label sender worker

def set_analytics_queue(q):
    """Set the analytics queue reference (called from main.py)"""
    global _analytics_queue
    _analytics_queue = q

def set_pose_label_queue(q):
    """Set the pose label queue reference (called from main.py)"""
    global _pose_label_queue
    _pose_label_queue = q

def send_track_to_analytics(track_id, keypoints, bbox, previous_bbox=None, elapsed_ms=33.33, use_hme=False):
    """Send track data to analytics queue for processing.
    
    This function is called from the main thread to queue track data
    for the AnalyticsWorker to process.
    
    Args:
        track_id: The track ID
        keypoints: List of 34 floats (17 keypoints × 2 coordinates)
        bbox: Current bounding box [x, y, width, height]
        previous_bbox: Previous frame's bounding box (optional)
        elapsed_ms: Time since last frame in milliseconds
        use_hme: Whether to use HME mode
    
    Returns:
        bool: True if data was queued successfully, False otherwise
    """
    from control_manager import get_flag
    
    # Only queue if analytics mode is enabled
    if not get_flag("analytics_mode", False):
        return False
    
    # Check if queue is available
    if _analytics_queue is None:
        return False
    
    try:
        track_data = {
            "track_id": track_id,
            "keypoints": keypoints,
            "bbox": bbox,
            "previous_bbox": previous_bbox,
            "elapsed_ms": elapsed_ms,
            "use_hme": use_hme
        }
        # Put data in the analytics queue for the worker to process
        try:
            _analytics_queue.put_nowait(track_data)
            return True
        except queue.Full:
            # Queue is full, skip this frame
            return False
    except Exception as e:
        print(f"[Analytics] Failed to prepare track data: {e}")
        return False


class PoseLabelSenderWorker(threading.Thread):
    """Background worker for sending pose labels to streaming server asynchronously.
    
    This worker is used when the analytics server is NOT available.
    It receives pose label data from the main thread via a queue and sends it
    to the streaming server in the background, preventing blocking of the main loop.
    """
    
    def __init__(self, pose_label_queue, camera_id):
        super().__init__(daemon=True)
        self.camera_id = camera_id
        self.running = True
        self.pose_label_queue = pose_label_queue
        
        # Statistics for monitoring
        self.sent_count = 0
        self.error_count = 0
        
    def run(self):
        """Main worker loop - process pose label data from queue."""
        from streaming import send_pose_label_to_streaming_server
        
        print(f"[PoseLabelSender] Starting worker for camera: {self.camera_id}")
        
        while self.running:
            try:
                # Get next pose label data from queue (non-blocking)
                try:
                    pose_data = self.pose_label_queue.get_nowait()
                    self._send_pose_label(pose_data, send_pose_label_to_streaming_server)
                except queue.Empty:
                    # No data available, sleep briefly
                    time.sleep(0.01)  # 10ms
                    
            except Exception as e:
                print(f"[PoseLabelSender] Error in main loop: {e}")
                self.error_count += 1
                time.sleep(0.1)  # Sleep on error
        
        print(f"[PoseLabelSender] Stopped (sent={self.sent_count}, errors={self.error_count})")
    
    def _send_pose_label(self, pose_data, send_func):
        """Send a single pose label to streaming server.
        
        Args:
            pose_data: Dictionary containing:
                - track_id: int
                - pose_label: str (standing, sitting, bending_down, lying_down, unknown)
                - safety_status: str (normal, unsafe, fall)
            send_func: The send function to use
        """
        try:
            track_id = pose_data.get("track_id")
            pose_label = pose_data.get("pose_label", "unknown")
            safety_status = pose_data.get("safety_status", "normal")
            
            success = send_func(
                camera_id=self.camera_id,
                track_id=track_id,
                pose_label=pose_label,
                safety_status=safety_status
            )
            
            if success:
                self.sent_count += 1
                if self.sent_count % 30 == 0:
                    print(f"[PoseLabelSender] Sent {self.sent_count} pose labels")
            else:
                self.error_count += 1
                
        except Exception as e:
            print(f"[PoseLabelSender] Error sending pose label: {e}")
            self.error_count += 1
    
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


def send_pose_label_to_queue(track_id, pose_label, safety_status="normal"):
    """Queue pose label data for async sending to streaming server.
    
    This function is called from the main thread to queue pose label data
    for the PoseLabelSenderWorker to send asynchronously.
    
    Args:
        track_id: The track ID
        pose_label: Pose classification label (standing, sitting, bending_down, lying_down, unknown)
        safety_status: Safety status (normal, unsafe, fall)
    
    Returns:
        bool: True if data was queued successfully, False otherwise
    """
    # Check if queue is available
    if _pose_label_queue is None:
        return False
    
    try:
        pose_data = {
            "track_id": track_id,
            "pose_label": pose_label,
            "safety_status": safety_status
        }
        # Put data in the queue for the worker to send
        try:
            _pose_label_queue.put_nowait(pose_data)
            return True
        except queue.Full:
            # Queue is full, skip this frame
            return False
    except Exception as e:
        print(f"[PoseLabelSender] Failed to prepare pose data: {e}")
        return False

