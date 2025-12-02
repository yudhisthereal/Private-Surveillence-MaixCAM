import asyncio
import json
import base64
import pickle
import time
from datetime import datetime
import numpy as np
import logging
from typing import Dict, Set, List
import cv2
import threading
import subprocess
import platform
import socket
from urllib.parse import urlparse, parse_qs
import os
import errno
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
import mimetypes

# Configure logging
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class WiFiConnector:
    """Handle WiFi connection for analytics server"""
    
    def __init__(self, ssid="MaixCAM-Wifi", password="maixcamwifi"):
        self.ssid = ssid
        self.password = password
        self.connected = False
        self.ip_address = None
    
    def connect_wifi(self):
        """Connect to WiFi network"""
        system = platform.system().lower()
        
        try:
            if system == "windows":
                self._connect_wifi_windows()
            elif system == "linux":
                self._connect_wifi_linux()
            elif system == "darwin":  # macOS
                self._connect_wifi_macos()
            else:
                logger.warning(f"Unsupported OS: {system}")
                return self._get_current_ip()
                
        except Exception as e:
            logger.error(f"WiFi connection failed: {e}")
            return self._get_current_ip()
    
    def _connect_wifi_windows(self):
        """Connect to WiFi on Windows"""
        try:
            # Use netsh to connect to WiFi
            connect_cmd = f'netsh wlan connect name="{self.ssid}"'
            result = subprocess.run(connect_cmd, shell=True, capture_output=True, text=True)
            
            if result.returncode == 0:
                logger.info(f"Connected to WiFi: {self.ssid}")
                self.connected = True
                time.sleep(5)  # Wait for connection to stabilize
                self.ip_address = self._get_current_ip()
            else:
                logger.warning(f"Could not connect to {self.ssid}. Using current network.")
                self.ip_address = self._get_current_ip()
                
        except Exception as e:
            logger.error(f"Windows WiFi connection error: {e}")
            self.ip_address = self._get_current_ip()
    
    def _connect_wifi_linux(self):
        """Connect to WiFi on Linux"""
        try:
            # Try using nmcli (NetworkManager)
            connect_cmd = f'nmcli device wifi connect "{self.ssid}" password "{self.password}"'
            result = subprocess.run(connect_cmd, shell=True, capture_output=True, text=True)
            
            if result.returncode == 0:
                logger.info(f"Connected to WiFi: {self.ssid}")
                self.connected = True
                time.sleep(5)
                self.ip_address = self._get_current_ip()
            else:
                logger.warning(f"Could not connect to {self.ssid}. Using current network.")
                self.ip_address = self._get_current_ip()
                
        except Exception as e:
            logger.error(f"Linux WiFi connection error: {e}")
            self.ip_address = self._get_current_ip()
    
    def _connect_wifi_macos(self):
        """Connect to WiFi on macOS"""
        try:
            # Try using networksetup
            connect_cmd = f'networksetup -setairportnetwork en0 "{self.ssid}" "{self.password}"'
            result = subprocess.run(connect_cmd, shell=True, capture_output=True, text=True)
            
            if result.returncode == 0:
                logger.info(f"Connected to WiFi: {self.ssid}")
                self.connected = True
                time.sleep(5)
                self.ip_address = self._get_current_ip()
            else:
                logger.warning(f"Could not connect to {self.ssid}. Using current network.")
                self.ip_address = self._get_current_ip()
                
        except Exception as e:
            logger.error(f"macOS WiFi connection error: {e}")
            self.ip_address = self._get_current_ip()
    
    def _get_current_ip(self):
        """Get current IP address"""
        try:
            # Method 1: Get local IP
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            logger.info(f"Current IP address: {ip}")
            return ip
        except:
            try:
                # Method 2: Get hostname IP
                hostname = socket.gethostname()
                ip = socket.gethostbyname(hostname)
                logger.info(f"Hostname IP: {ip}")
                return ip
            except:
                logger.warning("Could not determine IP address")
                return "0.0.0.0"

# Global frame storage
camera_frames = {}
frame_lock = threading.Lock()
placeholder_frame = None

def create_placeholder_frame():
    """Create a placeholder frame for when no camera is connected"""
    global placeholder_frame
    img = np.ones((240, 320, 3), dtype=np.uint8) * 50
    cv2.putText(img, "No Camera Connected", (40, 120), 
               cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
    cv2.putText(img, "Waiting for MaixCAM...", (30, 150), 
               cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
    _, jpeg = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 70])
    placeholder_frame = jpeg.tobytes()
    return placeholder_frame

class AnalyticsHTTPHandler(BaseHTTPRequestHandler):
    """HTTP request handler for analytics server"""
    
    def __init__(self, *args, **kwargs):
        self.analytics = kwargs.pop('analytics')
        super().__init__(*args, **kwargs)
    
    def do_GET(self):
        """Handle GET requests"""
        try:
            # Parse the path and query parameters
            parsed_path = urlparse(self.path)
            path = parsed_path.path
            query_params = parse_qs(parsed_path.query)
            
            # Default camera ID
            camera_id = query_params.get('camera_id', ['maixcam_001'])[0]
            
            # Log the request for debugging
            logger.debug(f"GET {path} - Camera: {camera_id}")
            
            # Route based on path
            if path == '/' or path == '/index.html':
                self.serve_static_file('index.html', 'text/html')
            elif path.endswith('.css'):
                self.serve_static_file(path[1:], 'text/css')
            elif path.endswith('.js'):
                self.serve_static_file(path[1:], 'application/javascript')
            elif path == '/stream.jpg' or path == '/frame.jpg':
                self.serve_frame(camera_id)
            elif path == '/snapshot.jpg':
                self.serve_frame(camera_id)
            elif path == '/get_safe_areas':
                self.get_safe_areas(camera_id)
            elif path == '/camera_list':
                self.get_camera_list()
            elif path == '/camera_state':
                self.get_camera_state(camera_id)
            elif path == '/stats':
                self.get_stats()
            elif path == '/server_info':
                self.get_server_info()
            elif path == '/debug':
                self.get_debug_info()
            else:
                # Try to serve static file
                if os.path.exists(os.path.join('static', path[1:])):
                    self.serve_static_file(path[1:])
                else:
                    logger.warning(f"404 Not Found: {path}")
                    self.send_error(404, "Not Found")
                    
        except Exception as e:
            logger.error(f"GET request error: {e}")
            self.send_error(500, "Internal Server Error")
    
    def do_POST(self):
        """Handle POST requests"""
        try:
            # Parse the path
            parsed_path = urlparse(self.path)
            path = parsed_path.path
            
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length) if content_length > 0 else b''
            
            logger.debug(f"POST {path} - Body size: {len(body)} bytes")
            
            if path == '/upload_frame':
                self.handle_frame_upload(body)
            elif path == '/upload_data':
                self.handle_data_upload(body)
            elif path == '/set_safe_areas':
                self.handle_set_safe_areas(body)
            elif path == '/command':
                self.handle_command(body)
            elif path == '/camera_state':
                self.handle_camera_state_update(body)
            else:
                logger.warning(f"404 Not Found: {path}")
                self.send_error(404, "Not Found")
                
        except Exception as e:
            logger.error(f"POST request error: {e}")
            self.send_error(500, "Internal Server Error")
    
    def serve_static_file(self, filename, content_type=None):
        """Serve static file from static directory"""
        try:
            filepath = os.path.join('static', filename)
            
            if not os.path.exists(filepath):
                self.send_error(404, "File not found")
                return
            
            if content_type is None:
                # Guess content type
                content_type, _ = mimetypes.guess_type(filepath)
                if content_type is None:
                    content_type = 'application/octet-stream'
            
            with open(filepath, 'rb') as f:
                content = f.read()
            
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
            self.end_headers()
            self.wfile.write(content)
            
        except Exception as e:
            logger.error(f"Error serving static file {filename}: {e}")
            self.send_error(500, "Internal Server Error")
    
    def serve_frame(self, camera_id):
        """Serve single JPEG frame"""
        try:
            with frame_lock:
                frame_info = camera_frames.get(camera_id, {})
                frame_data = frame_info.get('frame')
            
            if frame_data is None:
                # Use placeholder
                if placeholder_frame is None:
                    create_placeholder_frame()
                frame_data = placeholder_frame
            
            self.send_response(200)
            self.send_header('Content-Type', 'image/jpeg')
            self.send_header('Content-Length', str(len(frame_data)))
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
            self.end_headers()
            self.wfile.write(frame_data)
            
        except (ConnectionError, BrokenPipeError):
            logger.debug("Client disconnected during frame serve")
        except Exception as e:
            logger.error(f"Error serving frame for {camera_id}: {e}")
            self.send_error(500, "Internal Server Error")
    
    def handle_frame_upload(self, body):
        """Handle frame upload from MaixCAM"""
        try:
            camera_id = self.headers.get('X-Camera-ID', 'maixcam_001')
            
            # Validate frame size
            if len(body) > 10 * 1024 * 1024:  # 10MB max
                logger.warning(f"Frame too large from {camera_id}: {len(body)} bytes")
                self.send_error(413, "Payload Too Large")
                return
            
            # Store frame
            with frame_lock:
                camera_frames[camera_id] = {
                    'frame': body,
                    'timestamp': time.time(),
                    'size': len(body),
                    'source_addr': self.client_address[0]
                }
            
            logger.debug(f"Received frame from {camera_id} ({len(body)} bytes)")
            
            # Send success response
            response = {
                "status": "success", 
                "message": f"Frame received ({len(body)} bytes)",
                "timestamp": time.time()
            }
            
            self.send_json_response(200, response)
            
        except Exception as e:
            logger.error(f"Frame upload error: {e}")
            self.send_error(500, "Internal Server Error")
    
    def handle_command(self, body):
        """Handle control commands from web UI"""
        try:
            if not body:
                self.send_error(400, "Bad Request")
                return
            
            command_data = json.loads(body.decode())
            command = command_data.get("command")
            value = command_data.get("value")
            camera_id = command_data.get("camera_id", "maixcam_001")
            
            logger.info(f"Command received: {command}={value} for {camera_id}")
            
            # Update camera state
            if camera_id not in self.analytics.camera_states:
                self.analytics.camera_states[camera_id] = {
                    "control_flags": {
                        "record": False,
                        "show_raw": False,
                        "set_background": False,
                        "auto_update_bg": False,
                        "show_safe_area": False,
                        "use_safety_check": True
                    },
                    "safe_areas": [],
                    "last_command": time.time()
                }
            
            # Update control flag
            if command == "toggle_record":
                self.analytics.camera_states[camera_id]["control_flags"]["record"] = bool(value)
            elif command == "toggle_raw":
                self.analytics.camera_states[camera_id]["control_flags"]["show_raw"] = bool(value)
            elif command == "auto_update_bg":
                self.analytics.camera_states[camera_id]["control_flags"]["auto_update_bg"] = bool(value)
            elif command == "set_background":
                self.analytics.camera_states[camera_id]["control_flags"]["set_background"] = bool(value)
            elif command == "toggle_safe_area_display":
                self.analytics.camera_states[camera_id]["control_flags"]["show_safe_area"] = bool(value)
            elif command == "toggle_safety_check":
                self.analytics.camera_states[camera_id]["control_flags"]["use_safety_check"] = bool(value)
            elif command == "update_safe_areas":
                self.analytics.camera_states[camera_id]["safe_areas"] = value
            
            # Try to forward to camera
            forwarded = self.analytics.forward_to_camera(camera_id, command, value)
            
            response = {
                "status": "success",
                "command": command,
                "value": value,
                "camera_id": camera_id,
                "forwarded": forwarded
            }
            
            self.send_json_response(200, response)
            
        except Exception as e:
            logger.error(f"Command error: {e}")
            self.send_error(500, "Internal Server Error")
    
    def handle_camera_state_update(self, body):
        """Handle camera state updates from MaixCAM"""
        try:
            state = json.loads(body.decode())
            camera_id = state.get("camera_id")
            
            if not camera_id:
                self.send_error(400, "Bad Request")
                return
            
            # Initialize if not exists
            if camera_id not in self.analytics.camera_states:
                self.analytics.camera_states[camera_id] = {}
            
            # Update state
            self.analytics.camera_states[camera_id].update({
                "control_flags": state.get("control_flags", {}),
                "safe_areas": state.get("safe_areas", []),
                "ip_address": state.get("ip_address"),
                "last_seen": time.time(),
                "last_report": time.time()
            })
            
            logger.debug(f"Updated state for camera {camera_id}")
            self.send_response(200)
            self.end_headers()
            
        except Exception as e:
            logger.error(f"Camera state error: {e}")
            self.send_error(500, "Internal Server Error")
    
    def get_camera_list(self):
        """Return list of active cameras"""
        try:
            active_cameras = []
            current_time = time.time()
            
            with frame_lock:
                for cam_id, frame_info in camera_frames.items():
                    last_seen = frame_info.get('timestamp', 0)
                    # Camera is active if seen in last 2 minutes
                    if current_time - last_seen < 120:
                        active_cameras.append({
                            "camera_id": cam_id,
                            "last_seen": last_seen,
                            "ip_address": frame_info.get('source_addr', 'unknown'),
                            "online": True
                        })
            
            response = {
                "cameras": active_cameras,
                "count": len(active_cameras),
                "timestamp": current_time
            }
            
            self.send_json_response(200, response)
            
        except Exception as e:
            logger.error(f"Camera list error: {e}")
            self.send_error(500, "Internal Server Error")
    
    def get_camera_state(self, camera_id):
        """Return camera's control flags"""
        try:
            state = self.analytics.camera_states.get(camera_id, {})
            flags = state.get("control_flags", {})
            
            # Add metadata
            flags["_timestamp"] = time.time()
            flags["_camera_id"] = camera_id
            
            self.send_json_response(200, flags)
            
        except Exception as e:
            logger.error(f"Camera state error for {camera_id}: {e}")
            self.send_error(500, "Internal Server Error")
    
    def get_safe_areas(self, camera_id):
        """Return safe areas for camera"""
        try:
            state = self.analytics.camera_states.get(camera_id, {})
            safe_areas = state.get("safe_areas", [])
            self.send_json_response(200, safe_areas)
            
        except Exception as e:
            logger.error(f"Safe areas error for {camera_id}: {e}")
            self.send_error(500, "Internal Server Error")
    
    def handle_set_safe_areas(self, body):
        """Set safe areas for camera"""
        try:
            data = json.loads(body.decode())
            camera_id = data.get("camera_id", "maixcam_001")
            safe_areas = data.get("safe_areas", [])
            
            if camera_id not in self.analytics.camera_states:
                self.analytics.camera_states[camera_id] = {}
            
            self.analytics.camera_states[camera_id]["safe_areas"] = safe_areas
            
            # Forward to camera
            self.analytics.forward_to_camera(camera_id, "update_safe_areas", safe_areas)
            
            response = {
                "status": "success", 
                "message": f"Saved {len(safe_areas)} safe areas"
            }
            
            self.send_json_response(200, response)
            
        except Exception as e:
            logger.error(f"Set safe areas error: {e}")
            self.send_error(500, "Internal Server Error")
    
    def handle_data_upload(self, body):
        """Handle data upload from MaixCAM"""
        try:
            data = json.loads(body.decode())
            camera_id = data.get("camera_id", "unknown_camera")
            data_type = data.get("type")
            
            if data_type == "skeletal_data":
                self.analytics.process_skeletal_data(camera_id, data.get("data", {}))
            elif data_type == "pose_alert":
                self.analytics.process_pose_alert(camera_id, data.get("data", {}))
            elif data_type == "recording_started":
                logger.info(f"Recording started on camera {camera_id}")
            elif data_type == "recording_stopped":
                logger.info(f"Recording stopped on camera {camera_id}")
            
            response = {"status": "success", "message": "Data received"}
            self.send_json_response(200, response)
            
        except Exception as e:
            logger.error(f"Data upload error: {e}")
            self.send_error(500, "Internal Server Error")
    
    def get_stats(self):
        """Return server statistics"""
        try:
            with frame_lock:
                frame_count = len(camera_frames)
            
            stats = {
                "cameras": frame_count,
                "diagnoses": len(self.analytics.diagnosis_history),
                "active_cameras": len([c for c in self.analytics.camera_states.values() 
                                      if time.time() - c.get("last_seen", 0) < 120]),
                "wifi_connected": self.analytics.wifi_connector.connected,
                "wifi_ssid": self.analytics.wifi_connector.ssid,
                "timestamp": time.time()
            }
            
            self.send_json_response(200, stats)
            
        except Exception as e:
            logger.error(f"Stats error: {e}")
            self.send_error(500, "Internal Server Error")
    
    def get_server_info(self):
        """Return server information"""
        try:
            hostname = socket.gethostname()
            info = {
                "ip": self.analytics.wifi_connector.ip_address,
                "hostname": hostname,
                "wifi_ssid": self.analytics.wifi_connector.ssid,
                "wifi_connected": self.analytics.wifi_connector.connected,
                "port": self.analytics.http_port,
                "uptime": getattr(self.analytics, 'start_time', time.time())
            }
            
            self.send_json_response(200, info)
            
        except Exception as e:
            logger.error(f"Server info error: {e}")
            self.send_error(500, "Internal Server Error")
    
    def get_debug_info(self):
        """Return debug information"""
        try:
            with frame_lock:
                debug_info = {
                    'camera_frames': {
                        cam_id: {
                            'has_frame': info.get('frame') is not None,
                            'size': info.get('size', 0),
                            'timestamp': info.get('timestamp', 0),
                            'age_seconds': time.time() - info.get('timestamp', 0) if info.get('timestamp') else None,
                            'source': info.get('source_addr', 'unknown')
                        }
                        for cam_id, info in camera_frames.items()
                    },
                    'camera_states': {
                        cam_id: {
                            'last_seen': state.get('last_seen', 0),
                            'age_seconds': time.time() - state.get('last_seen', 0),
                            'has_flags': bool(state.get('control_flags'))
                        }
                        for cam_id, state in self.analytics.camera_states.items()
                    }
                }
            
            self.send_json_response(200, debug_info)
            
        except Exception as e:
            logger.error(f"Debug info error: {e}")
            self.send_error(500, "Internal Server Error")
    
    def send_json_response(self, code, data):
        """Send JSON response"""
        try:
            json_data = json.dumps(data)
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(json_data)))
            self.end_headers()
            self.wfile.write(json_data.encode())
        except (ConnectionError, BrokenPipeError):
            logger.debug("Client disconnected during JSON response")
    
    def log_message(self, format, *args):
        """Override to use our logger"""
        logger.debug(f"{self.address_string()} - {format % args}")

class PatientAnalytics:
    def __init__(self, port=8000):
        self.connected_cameras: Dict = {}
        self.diagnosis_history = []
        self.camera_states = {}  # camera_id -> control_flags, safe_areas, etc.
        
        # WiFi connection
        self.wifi_connector = WiFiConnector("MaixCAM-Wifi", "maixcamwifi")
        
        # HTTP server
        self.http_port = port
        self.http_server = None
        
        # Create placeholder frame
        create_placeholder_frame()
        
        # Start time
        self.start_time = time.time()
        
    def start_http_server(self):
        """Start HTTP server"""
        try:
            # Connect to WiFi first
            logger.info("Connecting to WiFi network...")
            self.wifi_connector.connect_wifi()
            
            # Start HTTP server
            server_address = ('0.0.0.0', self.http_port)
            handler_class = lambda *args, **kwargs: AnalyticsHTTPHandler(*args, analytics=self, **kwargs)
            self.http_server = HTTPServer(server_address, handler_class)
            
            # Get the actual IP
            ip = self.wifi_connector.ip_address or "0.0.0.0"
            
            logger.info(f"HTTP server starting on port {self.http_port}")
            logger.info(f"Dashboard available at: http://{ip}:{self.http_port}")
            
            # Check static directory
            if not os.path.exists('static'):
                logger.warning("Static directory not found. Creating...")
                os.makedirs('static', exist_ok=True)
                logger.info("Please place index.html, style.css, and script.js in the static/ directory")
            
            # Start server in background thread
            server_thread = threading.Thread(target=self.http_server.serve_forever, daemon=True)
            server_thread.start()
            
            logger.info("=" * 60)
            logger.info("📊 Analytics Gateway Service Started Successfully!")
            logger.info(f"🌐 Analytics IP: {ip}")
            logger.info(f"🖥️  Dashboard: http://{ip}:{self.http_port}")
            logger.info("=" * 60)
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to start HTTP server: {e}")
            return False
    
    def forward_to_camera(self, camera_id, command, value):
        """Forward command to MaixCAM"""
        try:
            with frame_lock:
                frame_info = camera_frames.get(camera_id, {})
                camera_ip = frame_info.get('source_addr')
            
            if not camera_ip:
                logger.debug(f"No IP known for camera {camera_id}")
                return False
            
            # Send command to MaixCAM (assuming MaixCAM is listening on port 8080)
            url = f"http://{camera_ip}:8080/command"
            payload = {
                "command": command,
                "value": value,
                "camera_id": camera_id
            }
            
            try:
                response = requests.post(
                    url,
                    json=payload,
                    timeout=1.0
                )
                
                success = response.status_code == 200
                if success:
                    logger.info(f"Command forwarded to {camera_id} at {camera_ip}")
                else:
                    logger.warning(f"Command forwarding failed: HTTP {response.status_code}")
                
                return success
                
            except requests.exceptions.RequestException as e:
                logger.debug(f"Command forwarding error: {e}")
                return False
                
        except Exception as e:
            logger.error(f"Forward error: {e}")
            return False
    
    def process_skeletal_data(self, camera_id: str, data: dict):
        """Process skeletal data from camera via HTTP"""
        try:
            # Perform analytics
            diagnosis = self.perform_advanced_analysis(camera_id, data)
            
            # Store diagnosis
            if diagnosis:
                self.diagnosis_history.append(diagnosis)
            
            logger.info(f"Processed skeletal data from {camera_id}, alert: {diagnosis.get('alert_level', 'normal') if diagnosis else 'N/A'}")
                
        except Exception as e:
            logger.error(f"Error processing skeletal data from {camera_id}: {e}")

    def process_pose_alert(self, camera_id: str, data: dict):
        """Process pose alerts from camera via HTTP"""
        try:
            alert_type = data.get("alert_type")
            track_id = data.get("track_id")
            
            logger.warning(f"Pose alert from {camera_id}: {alert_type} for track {track_id}")
            
        except Exception as e:
            logger.error(f"Error processing pose alert from {camera_id}: {e}")

    def perform_advanced_analysis(self, camera_id: str, skeletal_data: dict):
        """Perform advanced analytics on skeletal data"""
        try:
            keypoints = skeletal_data.get("keypoints")
            pose_data = skeletal_data.get("pose_data", {})
            timestamp = skeletal_data.get("timestamp")
            
            # Enhanced fall detection
            fall_risk = self.enhanced_fall_detection(keypoints, pose_data)
            
            # Activity classification
            activity = pose_data.get('label', 'unknown') if pose_data else 'unknown'
            
            # Risk assessment
            overall_risk = self.assess_overall_risk(fall_risk, activity)
            
            diagnosis = {
                "camera_id": camera_id,
                "timestamp": timestamp,
                "analysis_time": datetime.now().isoformat(),
                "fall_risk": fall_risk,
                "detected_activity": activity,
                "overall_risk": overall_risk,
                "alert_level": self.determine_alert_level(overall_risk),
                "recommendations": self.generate_recommendations(overall_risk, activity),
                "confidence": 0.85
            }
            
            logger.info(f"Generated diagnosis for {camera_id}: {diagnosis['alert_level']}")
            return diagnosis
            
        except Exception as e:
            logger.error(f"Error in advanced analysis for {camera_id}: {e}")
            return None

    def enhanced_fall_detection(self, keypoints, pose_data):
        """Enhanced fall detection using pose data"""
        fall_probability = 0.0
        
        if pose_data:
            torso_angle = pose_data.get('torso_angle')
            thigh_uprightness = pose_data.get('thigh_uprightness')
            
            if torso_angle is not None and thigh_uprightness is not None:
                if torso_angle > 80 and thigh_uprightness > 60:
                    fall_probability = 0.9
                elif torso_angle > 70 and thigh_uprightness > 50:
                    fall_probability = 0.7
                elif torso_angle > 60:
                    fall_probability = 0.5
        
        return min(fall_probability, 1.0)

    def assess_overall_risk(self, fall_risk, activity):
        """Assess overall risk"""
        activity_risk = self.activity_risk(activity)
        overall_risk = (fall_risk * 0.7) + (activity_risk * 0.3)
        return min(overall_risk, 1.0)

    def activity_risk(self, activity):
        """Map activity to risk level"""
        risk_map = {
            "lying": 0.8,
            "falling": 0.9,
            "transitioning": 0.7,
            "bending": 0.5,
            "standing": 0.3,
            "sitting": 0.2,
            "walking": 0.4,
            "unknown": 0.5
        }
        return risk_map.get(activity, 0.5)

    def determine_alert_level(self, overall_risk):
        """Determine alert level based on risk score"""
        if overall_risk >= 0.8:
            return "critical"
        elif overall_risk >= 0.6:
            return "high"
        elif overall_risk >= 0.4:
            return "medium"
        elif overall_risk >= 0.2:
            return "low"
        else:
            return "normal"

    def generate_recommendations(self, overall_risk, activity):
        """Generate recommendations based on risk and activity"""
        recommendations = []
        
        if overall_risk >= 0.8:
            recommendations.extend([
                "Immediate caregiver attention required",
                "Check patient position and vital signs"
            ])
        elif overall_risk >= 0.6:
            recommendations.extend([
                "Increased monitoring recommended",
                "Check patient environment for hazards"
            ])
        
        if activity == "lying":
            recommendations.append("Monitor for prolonged immobility")
        elif activity == "falling":
            recommendations.append("Emergency response needed")
            
        return recommendations

    def stop_servers(self):
        """Stop all servers"""
        if self.http_server:
            self.http_server.shutdown()
            self.http_server.server_close()
        logger.info("All servers stopped")

def main():
    """Main function to start the analytics service"""
    analytics = PatientAnalytics(port=8000)
    
    try:
        # Start HTTP server
        if not analytics.start_http_server():
            logger.error("Failed to start HTTP server")
            return
        
        logger.info("Press Ctrl+C to stop the service.")
        
        # Keep the server running
        while True:
            time.sleep(1)
        
    except KeyboardInterrupt:
        logger.info("Shutting down analytics service...")
    except Exception as e:
        logger.error(f"Analytics service error: {e}")
    finally:
        analytics.stop_servers()

if __name__ == "__main__":
    main()