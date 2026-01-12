# workers.py - Async worker classes for streaming server communication

import queue
import time
import requests
import socket
import select
import json
import threading
from config import (
    STREAMING_HTTP_URL, CAMERA_ID, FLAG_SYNC_INTERVAL_MS, 
    SAFE_AREA_SYNC_INTERVAL_MS, STATE_REPORT_INTERVAL_MS,
    FRAME_UPLOAD_INTERVAL_MS, LOCAL_PORT
)

# Global flags for state reporter (will be updated by main)
is_recording = False
rtmp_connected = False

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
        
    def run(self):
        while self.running:
            try:
                # 1. Get camera state (including control flags) from streaming server
                state_response = requests.get(
                    f"{self.streaming_url}/api/stream/camera-state?camera_id={self.camera_id}",
                    timeout=2.0
                )
                
                if state_response.status_code == 200:
                    flags = state_response.json()
                    
                    # Check if we received valid flags
                    if flags and isinstance(flags, dict):
                        # Put flags in queue for main thread to consume
                        try:
                            self.flags_queue.put_nowait(("flags_update", flags))
                            print(f"[FlagSync] Flags synced from server ({len(flags)} items)")
                        except queue.Full:
                            pass
                        
                        # Reset error count on success
                        self.connection_errors = 0
                        self.last_successful_sync = time.time()
                    else:
                        print(f"[FlagSync] Invalid flags data received")
                        self.connection_errors += 1
                else:
                    print(f"[FlagSync] Failed to get flags: HTTP {state_response.status_code}")
                    self.connection_errors += 1
                
                # 2. Get safe areas from streaming server (less frequent)
                current_time = time.time()
                if current_time - self.last_safe_area_sync > (SAFE_AREA_SYNC_INTERVAL_MS / 1000.0):
                    safe_areas_response = requests.get(
                        f"{self.streaming_url}/api/stream/safe-areas?camera_id={self.camera_id}",
                        timeout=2.0
                    )
                    
                    if safe_areas_response.status_code == 200:
                        safe_areas = safe_areas_response.json()
                        if isinstance(safe_areas, list):
                            try:
                                self.safe_areas_queue.put_nowait(("safe_areas_update", safe_areas))
                                print(f"[SafeAreaSync] Safe areas synced ({len(safe_areas)} polygons)")
                                self.last_safe_area_sync = current_time
                            except queue.Full:
                                pass
                    else:
                        print(f"[SafeAreaSync] Failed to get safe areas: HTTP {safe_areas_response.status_code}")
                
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
                print(f"[FlagSync] High error count, increasing sync interval")
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
                    # Build state report with current camera status
                    # Note: We report state only - camera does NOT send control_flags
                    # as it cannot modify them (server is the authority)
                    state_report = {
                        "camera_id": self.camera_id,
                        "timestamp": int(time.time() * 1000),
                        "status": "online",
                        "is_recording": is_recording,
                        "rtmp_connected": rtmp_connected
                    }
                    
                    try:
                        # Send to the proper state report endpoint (NOT command endpoint)
                        response = requests.post(
                            f"{self.streaming_url}/api/stream/report-state",
                            json=state_report,
                            headers={'Content-Type': 'application/json'},
                            timeout=2.0
                        )
                        
                        if response.status_code == 200:
                            print(f"[StateReporter] State reported successfully to /api/stream/report-state")
                        else:
                            print(f"[StateReporter] Failed: HTTP {response.status_code}")
                            
                    except Exception as e:
                        print(f"[StateReporter] State report error: {e}")
                    
                    self.last_report_time = current_time
                
                # Sleep for report interval
                time.sleep(self.report_interval)
                    
            except Exception as e:
                print(f"[StateReporter] Error: {e}")
                time.sleep(5.0)  # Sleep on error
    
    def stop(self):
        self.running = False

class FrameUploadWorker(threading.Thread):
    """Background thread for uploading frames to streaming server"""
    
    def __init__(self, frame_queue, streaming_url, camera_id, upload_interval_ms=FRAME_UPLOAD_INTERVAL_MS):
        super().__init__(daemon=True)
        self.frame_queue = frame_queue
        self.streaming_url = streaming_url
        self.camera_id = camera_id
        self.running = True
        self.last_upload_time = 0
        self.upload_interval = upload_interval_ms / 1000.0
        
    def run(self):
        while self.running:
            try:
                # Get frame from queue with timeout
                frame_data = self.frame_queue.get(timeout=1.0)
                
                current_time = time.time() * 1000  # Convert to ms
                # Limit upload rate to avoid overwhelming server
                if current_time - self.last_upload_time > self.upload_interval * 1000:
                    try:
                        response = requests.post(
                            f"{self.streaming_url}/api/stream/upload-frame",
                            headers={'X-Camera-ID': self.camera_id},
                            data=frame_data,
                            timeout=2.0
                        )
                        
                        if response.status_code == 200:
                            self.last_upload_time = current_time
                        else:
                            print(f"[FrameUpload] Failed: HTTP {response.status_code}")
                            
                    except Exception as e:
                        print(f"[FrameUpload] Error: {e}")
                
                self.frame_queue.task_done()
                
            except queue.Empty:
                # No frames to upload
                pass
            except Exception as e:
                print(f"[FrameUpload] Queue error: {e}")
    
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
                                response += json.dumps({"status": "success", "camera_id": CAMERA_ID})
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
            from safe_area_manager import update_safety_checker_polygons
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
            save_camera_info_func(camera_id, CAMERA_NAME, "registered", "")
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

def update_is_recording(value):
    """Update global is_recording flag"""
    global is_recording
    is_recording = value

def update_rtmp_connected(value):
    """Update global rtmp_connected flag"""
    global rtmp_connected
    rtmp_connected = value

