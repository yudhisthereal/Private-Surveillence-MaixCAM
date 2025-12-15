# analytics.py

import traceback
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
from debug_config import debug_print, log_pose_data, log_fall_detection

# Import the same pose modules as MaixCAM
from pose.pose_estimation import PoseEstimation
from pose.judge_fall import get_fall_info, FALL_COUNT_THRES

# Configure logging
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

USE_HME = False  # Global toggle for HME mode
class WiFiConnector:
    """Handle WiFi connection for analytics server with smart connection logic"""
    
    def __init__(self, ssid="MaixCAM-Wifi", password="maixcamwifi"):
        self.ssid = ssid
        self.password = password
        self.connected = False
        self.ip_address = None
        self.current_ssid = None
        self.reconnection_attempts = 0
        self.MAX_RECONNECTION_ATTEMPTS = 3
        self.last_connection_check = 0
        self.CHECK_INTERVAL_SECONDS = 30  # Check connection every 30 seconds
    
    def connect_wifi(self):
        """Connect to WiFi network only if not already connected to the correct one"""
        system = platform.system().lower()
        
        try:
            # First, check current connection status
            self._check_current_connection(system)
            
            # If already connected to the correct WiFi, just update IP and return
            if self.current_ssid == self.ssid:
                logger.info(f"Already connected to target WiFi: {self.ssid}")
                self.connected = True
                self.ip_address = self._get_current_ip()
                self.reconnection_attempts = 0
                return self.ip_address
            
            # If connected to a different WiFi, check if we should switch
            if self.current_ssid and self.current_ssid != self.ssid:
                logger.info(f"Connected to different WiFi: {self.current_ssid}. Switching to {self.ssid}")
                # We'll proceed to connect to the target WiFi
            
            # If not connected or connected to wrong network, connect to target WiFi
            logger.info(f"Connecting to WiFi: {self.ssid}")
            
            if system == "windows":
                self._connect_wifi_windows()
            elif system == "linux":
                self._connect_wifi_linux()
            elif system == "darwin":  # macOS
                self._connect_wifi_macos()
            else:
                logger.warning(f"Unsupported OS: {system}")
                self.ip_address = self._get_current_ip()
                return self.ip_address
            
            # Verify connection after attempting to connect
            time.sleep(3)  # Wait for connection to stabilize
            self._verify_connection(system)
            
        except Exception as e:
            logger.error(f"WiFi connection failed: {e}")
            self.ip_address = self._get_current_ip()
        
        return self.ip_address
    
    def _check_current_connection(self, system):
        """Check what WiFi network we're currently connected to"""
        try:
            if system == "windows":
                self._check_windows_connection()
            elif system == "linux":
                self._check_linux_connection()
            elif system == "darwin":  # macOS
                self._check_macos_connection()
            else:
                logger.warning(f"Unsupported OS for connection check: {system}")
                self.current_ssid = None
                
        except Exception as e:
            logger.error(f"Error checking current connection: {e}")
            self.current_ssid = None
    
    def _check_windows_connection(self):
        """Check WiFi connection on Windows"""
        try:
            # Use netsh to get current connection info
            cmd = 'netsh wlan show interfaces'
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            
            if result.returncode == 0:
                output = result.stdout
                # Parse for SSID
                for line in output.split('\n'):
                    if 'SSID' in line and 'BSSID' not in line:
                        parts = line.split(':')
                        if len(parts) > 1:
                            ssid = parts[1].strip()
                            if ssid and ssid != "":
                                self.current_ssid = ssid
                                logger.debug(f"Windows: Currently connected to SSID: {self.current_ssid}")
                                return
            self.current_ssid = None
            logger.debug("Windows: Not connected to any WiFi or could not determine SSID")
        except Exception as e:
            logger.error(f"Windows connection check error: {e}")
            self.current_ssid = None
    
    def _check_linux_connection(self):
        """Check WiFi connection on Linux"""
        try:
            # Try using nmcli first (NetworkManager)
            cmd = 'nmcli -t -f active,ssid dev wifi | grep "^yes:"'
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            
            if result.returncode == 0 and result.stdout:
                # Extract SSID from output (format: yes:SSID_NAME)
                lines = result.stdout.strip().split('\n')
                for line in lines:
                    if line.startswith('yes:'):
                        self.current_ssid = line.split(':', 1)[1]
                        logger.debug(f"Linux (nmcli): Currently connected to SSID: {self.current_ssid}")
                        return
            
            # Fallback: check /proc/net/wireless or iwgetid
            cmd = 'iwgetid -r'
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            if result.returncode == 0 and result.stdout.strip():
                self.current_ssid = result.stdout.strip()
                logger.debug(f"Linux (iwgetid): Currently connected to SSID: {self.current_ssid}")
                return
            
            self.current_ssid = None
            logger.debug("Linux: Not connected to any WiFi or could not determine SSID")
        except Exception as e:
            logger.error(f"Linux connection check error: {e}")
            self.current_ssid = None
    
    def _check_macos_connection(self):
        """Check WiFi connection on macOS"""
        try:
            # Use airport command or networksetup
            cmd = '/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport -I'
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            
            if result.returncode == 0:
                output = result.stdout
                for line in output.split('\n'):
                    if ' SSID:' in line:
                        parts = line.split(':')
                        if len(parts) > 1:
                            self.current_ssid = parts[1].strip()
                            logger.debug(f"macOS: Currently connected to SSID: {self.current_ssid}")
                            return
            
            # Alternative method
            cmd = 'networksetup -getairportnetwork en0'
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            if result.returncode == 0:
                output = result.stdout
                if 'Current Wi-Fi Network:' in output:
                    parts = output.split(':')
                    if len(parts) > 1:
                        self.current_ssid = parts[1].strip()
                        logger.debug(f"macOS (networksetup): Currently connected to SSID: {self.current_ssid}")
                        return
            
            self.current_ssid = None
            logger.debug("macOS: Not connected to any WiFi or could not determine SSID")
        except Exception as e:
            logger.error(f"macOS connection check error: {e}")
            self.current_ssid = None
    
    def _verify_connection(self, system):
        """Verify we're connected to the correct WiFi after attempting connection"""
        # Check what we're actually connected to now
        self._check_current_connection(system)
        
        if self.current_ssid == self.ssid:
            self.connected = True
            self.ip_address = self._get_current_ip()
            self.reconnection_attempts = 0
            logger.info(f"Successfully connected to WiFi: {self.ssid}")
            logger.info(f"IP Address: {self.ip_address}")
        else:
            self.connected = False
            self.reconnection_attempts += 1
            logger.warning(f"Failed to connect to {self.ssid}. Currently connected to: {self.current_ssid or 'nothing'}")
            
            if self.reconnection_attempts < self.MAX_RECONNECTION_ATTEMPTS:
                logger.info(f"Will retry connection (attempt {self.reconnection_attempts + 1}/{self.MAX_RECONNECTION_ATTEMPTS})")
            else:
                logger.error(f"Max reconnection attempts reached. Staying with current connection.")
                self.ip_address = self._get_current_ip()
    
    def maintain_connection(self):
        """Periodically check and maintain WiFi connection"""
        current_time = time.time()
        
        if current_time - self.last_connection_check > self.CHECK_INTERVAL_SECONDS:
            self.last_connection_check = current_time
            
            # Check current connection
            system = platform.system().lower()
            self._check_current_connection(system)
            
            # If not connected to target WiFi, try to reconnect
            if self.current_ssid != self.ssid:
                logger.warning(f"Not connected to target WiFi. Current: {self.current_ssid}, Target: {self.ssid}")
                logger.info("Attempting to reconnect to target WiFi...")
                self.connect_wifi()
            else:
                # Just update IP address
                new_ip = self._get_current_ip()
                if new_ip != self.ip_address:
                    logger.info(f"WiFi IP address updated: {new_ip}")
                    self.ip_address = new_ip
    
    # Keep the original connection methods but update them slightly
    
    def _connect_wifi_windows(self):
        """Connect to WiFi on Windows"""
        try:
            # Check if already connected to target SSID
            if self.current_ssid == self.ssid:
                logger.info(f"Windows: Already connected to {self.ssid}")
                return
            
            # Try to connect
            connect_cmd = f'netsh wlan connect name="{self.ssid}"'
            logger.debug(f"Windows: Executing command: {connect_cmd}")
            result = subprocess.run(connect_cmd, shell=True, capture_output=True, text=True)
            
            if result.returncode == 0:
                logger.info(f"Windows: Connected to WiFi: {self.ssid}")
            else:
                logger.warning(f"Windows: Could not connect to {self.ssid}: {result.stderr}")
                # Try adding the profile first if it doesn't exist
                self._add_wifi_profile_windows()
                
        except Exception as e:
            logger.error(f"Windows WiFi connection error: {e}")
    
    def _add_wifi_profile_windows(self):
        """Add WiFi profile on Windows if it doesn't exist"""
        try:
            # Check if profile exists
            check_cmd = f'netsh wlan show profiles name="{self.ssid}"'
            result = subprocess.run(check_cmd, shell=True, capture_output=True, text=True)
            
            if "is not found on the system" in result.stdout or result.returncode != 0:
                # Create XML profile
                xml_profile = f'''<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
    <name>{self.ssid}</name>
    <SSIDConfig>
        <SSID>
            <name>{self.ssid}</name>
        </SSID>
    </SSIDConfig>
    <connectionType>ESS</connectionType>
    <connectionMode>auto</connectionMode>
    <MSM>
        <security>
            <authEncryption>
                <authentication>WPA2PSK</authentication>
                <encryption>AES</encryption>
                <useOneX>false</useOneX>
            </authEncryption>
            <sharedKey>
                <keyType>passPhrase</keyType>
                <protected>false</protected>
                <keyMaterial>{self.password}</keyMaterial>
            </sharedKey>
        </security>
    </MSM>
</WLANProfile>'''
                
                # Save to temp file
                import tempfile
                with tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False) as f:
                    f.write(xml_profile)
                    temp_file = f.name
                
                # Add profile
                add_cmd = f'netsh wlan add profile filename="{temp_file}"'
                result = subprocess.run(add_cmd, shell=True, capture_output=True, text=True)
                
                # Clean up
                import os
                os.unlink(temp_file)
                
                if result.returncode == 0:
                    logger.info(f"Windows: Added WiFi profile for {self.ssid}")
                    # Now try connecting again
                    connect_cmd = f'netsh wlan connect name="{self.ssid}"'
                    subprocess.run(connect_cmd, shell=True, capture_output=True, text=True)
        except Exception as e:
            logger.error(f"Windows profile creation error: {e}")
    
    def _connect_wifi_linux(self):
        """Connect to WiFi on Linux"""
        try:
            # Check if already connected to target SSID
            if self.current_ssid == self.ssid:
                logger.info(f"Linux: Already connected to {self.ssid}")
                return
            
            # Try using nmcli
            connect_cmd = f'nmcli device wifi connect "{self.ssid}" password "{self.password}"'
            logger.debug(f"Linux: Executing command: {connect_cmd}")
            result = subprocess.run(connect_cmd, shell=True, capture_output=True, text=True)
            
            if result.returncode == 0:
                logger.info(f"Linux: Connected to WiFi: {self.ssid}")
            else:
                logger.warning(f"Linux: Could not connect to {self.ssid}: {result.stderr}")
                
        except Exception as e:
            logger.error(f"Linux WiFi connection error: {e}")
    
    def _connect_wifi_macos(self):
        """Connect to WiFi on macOS"""
        try:
            # Check if already connected to target SSID
            if self.current_ssid == self.ssid:
                logger.info(f"macOS: Already connected to {self.ssid}")
                return
            
            # Try using networksetup
            connect_cmd = f'networksetup -setairportnetwork en0 "{self.ssid}" "{self.password}"'
            logger.debug(f"macOS: Executing command: {connect_cmd}")
            result = subprocess.run(connect_cmd, shell=True, capture_output=True, text=True)
            
            if result.returncode == 0:
                logger.info(f"macOS: Connected to WiFi: {self.ssid}")
            else:
                logger.warning(f"macOS: Could not connect to {self.ssid}: {result.stderr}")
                
        except Exception as e:
            logger.error(f"macOS WiFi connection error: {e}")
    
    def _get_current_ip(self):
        """Get current IP address with better error handling"""
        try:
            # Method 1: Get local IP using socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(1)
            try:
                # Connect to Google DNS
                s.connect(("8.8.8.8", 80))
                ip = s.getsockname()[0]
                s.close()
                return ip
            except socket.error:
                s.close()
            
            # Method 2: Get from hostname
            try:
                hostname = socket.gethostname()
                ip = socket.gethostbyname(hostname)
                if ip.startswith("127."):
                    # Loopback address, not useful
                    raise socket.error
                return ip
            except socket.error:
                pass
            
            # Method 3: Try to get from network interfaces
            if platform.system().lower() == "linux":
                try:
                    import netifaces
                    for interface in netifaces.interfaces():
                        addresses = netifaces.ifaddresses(interface)
                        if netifaces.AF_INET in addresses:
                            for addr in addresses[netifaces.AF_INET]:
                                ip = addr.get('addr')
                                if ip and not ip.startswith("127."):
                                    return ip
                except ImportError:
                    pass
            
            logger.warning("Could not determine IP address")
            return "0.0.0.0"
            
        except Exception as e:
            logger.error(f"Error getting IP address: {e}")
            return "0.0.0.0"

# Global frame storage
camera_frames = {}
frame_lock = threading.Lock()
placeholder_frames = {}  # Store placeholder per camera

# Track history for fall detection
camera_track_history = {}  # camera_id -> {track_id -> history}
track_history_lock = threading.Lock()

# Pose estimator (same as MaixCAM)
pose_estimator = PoseEstimation()

# Fall detection parameters (same as MaixCAM)
fallParam = {
    "v_bbox_y": 0.43,
    "angle": 70
}
queue_size = 5
fps = 30  # Default FPS for analytics server

def create_placeholder_frame(camera_id="default"):
    """Create a placeholder frame for when camera is not connected"""
    global placeholder_frames
    
    if camera_id not in placeholder_frames:
        img = np.ones((240, 320, 3), dtype=np.uint8) * 50
        cv2.putText(img, f"Camera {camera_id}", (50, 100), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        cv2.putText(img, "Not Connected", (60, 130), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
        cv2.putText(img, "Waiting for connection...", (30, 160), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.4, (150, 150, 150), 1)
        _, jpeg = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 70])
        placeholder_frames[camera_id] = jpeg.tobytes()
    
    return placeholder_frames[camera_id]

def get_camera_status(camera_id):
    """Check if camera is currently connected"""
    with frame_lock:
        frame_info = camera_frames.get(camera_id, {})
        if frame_info:
            last_seen = frame_info.get('timestamp', 0)
            # Camera is considered connected if seen in last 30 seconds
            if time.time() - last_seen < 30:
                return "connected"
    return "disconnected"

def to_keypoints_np(obj_points):
    """Convert flat list [x1, y1, x2, y2, ...] to numpy array (same as MaixCAM)"""
    keypoints = np.array(obj_points)
    return keypoints.reshape(-1, 2)

def analyze_pose_on_server(keypoints_flat, bbox, track_id, camera_id):
    """Perform pose analysis on the server using the same logic as MaixCAM"""
    try:
        logger.debug(f"[DEBUG] analyze_pose_on_server called for camera {camera_id}, track {track_id}")
        
        if not keypoints_flat or len(keypoints_flat) < 10:
            logger.debug(f"[DEBUG] Insufficient keypoints: {len(keypoints_flat) if keypoints_flat else 0}")
            return None
        
        # Initialize track history for this camera if needed
        with track_history_lock:
            if camera_id not in camera_track_history:
                camera_track_history[camera_id] = {}
                logger.debug(f"[DEBUG] Created track history for camera {camera_id}")
            
            if track_id not in camera_track_history[camera_id]:
                camera_track_history[camera_id][track_id] = {
                    "id": [],
                    "bbox": [],
                    "points": []
                }
                logger.debug(f"[DEBUG] Created track history for track {track_id}")
            
            track_history = camera_track_history[camera_id][track_id]
            
            # Add track to history
            if track_id not in track_history["id"]:
                track_history["id"].append(track_id)
                track_history["bbox"].append([])  # Will be populated as queue
                track_history["points"].append([])
                logger.debug(f"[DEBUG] Added track {track_id} to history")
            
            idx = track_history["id"].index(track_id)
            
            # Initialize queues if needed
            if not track_history["bbox"][idx]:
                import queue
                track_history["bbox"][idx] = queue.Queue(maxsize=queue_size)
                track_history["points"][idx] = queue.Queue(maxsize=queue_size)
                logger.debug(f"[DEBUG] Initialized queues for track {track_id}")
            
            # Add current data to queue
            if track_history["bbox"][idx].qsize() >= queue_size:
                track_history["bbox"][idx].get()
                track_history["points"][idx].get()
                logger.debug(f"[DEBUG] Removed oldest data from queues for track {track_id}")
            
            track_history["bbox"][idx].put(bbox)
            track_history["points"][idx].put(keypoints_flat)
            logger.debug(f"[DEBUG] Added data to queues for track {track_id}. Queue size: {track_history['bbox'][idx].qsize()}")
        
        # Convert to numpy for pose estimation
        keypoints_np = to_keypoints_np(keypoints_flat)
        
        # Get pose estimation data (same as MaixCAM)
        pose_data = pose_estimator.evaluate_pose(keypoints_np.flatten())
        
        if not pose_data:
            logger.debug(f"[DEBUG] No pose data returned from pose_estimator")
            return None
        
        logger.debug(f"[DEBUG] Pose data label: {pose_data.get('label')}")
        
        # Check if we have enough history for fall detection
        if track_history["bbox"][idx].qsize() == queue_size:
            logger.debug(f"[DEBUG] Queue full ({queue_size}), running fall detection for track {track_id}")
            
            # Create a tracker object similar to MaixCAM
            class MockTrackerObj:
                def __init__(self, x, y, w, h):
                    self.x = x
                    self.y = y
                    self.w = w
                    self.h = h
            
            tracker_obj = MockTrackerObj(bbox[0], bbox[1], bbox[2], bbox[3])
            
            # Get fall info using the new function that returns 6 values
            (fall_detected_method1, counter_method1,
             fall_detected_method2, counter_method2,
             fall_detected_method3, counter_method3) = get_fall_info(
                tracker_obj, track_history, idx, fallParam, queue_size, fps, pose_data
            )
            
            logger.debug(f"[DEBUG] Fall detection results for track {track_id}:")
            logger.debug(f"[DEBUG]   Method1: detected={fall_detected_method1}, counter={counter_method1}")
            logger.debug(f"[DEBUG]   Method2: detected={fall_detected_method2}, counter={counter_method2}")
            logger.debug(f"[DEBUG]   Method3: detected={fall_detected_method3}, counter={counter_method3}")
            
            # Add fall detection results to pose data with NEW naming
            pose_data["fall_detected_method1"] = fall_detected_method1
            pose_data["fall_detected_method2"] = fall_detected_method2
            pose_data["fall_detected_method3"] = fall_detected_method3
            pose_data["fall_counter_method1"] = counter_method1
            pose_data["fall_counter_method2"] = counter_method2
            pose_data["fall_counter_method3"] = counter_method3
            pose_data["fall_threshold"] = FALL_COUNT_THRES
            # Use method 3 as the primary alert (most conservative)
            pose_data["fall_alert"] = fall_detected_method3
            pose_data["server_analysis"] = True  # Mark as server-side analysis
            
            logger.info(f"[ANALYTICS] Pose analysis for camera {camera_id}, track {track_id}:")
            logger.info(f"[ANALYTICS]   Activity: {pose_data.get('label')}")
            logger.info(f"[ANALYTICS]   Fall Method1: {'DETECTED' if fall_detected_method1 else 'no'} (counter={counter_method1}/{FALL_COUNT_THRES})")
            logger.info(f"[ANALYTICS]   Fall Method2: {'DETECTED' if fall_detected_method2 else 'no'} (counter={counter_method2}/{FALL_COUNT_THRES})")
            logger.info(f"[ANALYTICS]   Fall Method3: {'DETECTED' if fall_detected_method3 else 'no'} (counter={counter_method3}/{FALL_COUNT_THRES})")
        else:
            logger.debug(f"[DEBUG] Queue not full ({track_history['bbox'][idx].qsize()}/{queue_size}), skipping fall detection")
        
        return pose_data
        
    except Exception as e:
        logger.error(f"Error in server-side pose analysis: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return None

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
            elif path == '/camera_status':
                self.get_camera_status(camera_id)
            elif path == '/stats':
                self.get_stats()
            elif path == '/server_info':
                self.get_server_info()
            elif path == '/debug':
                self.get_debug_info()
            elif path == '/pose_analysis':
                self.get_pose_analysis(camera_id)
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
                last_seen = frame_info.get('timestamp', 0)
            
            # Check if camera is connected (seen in last 30 seconds)
            camera_connected = time.time() - last_seen < 30
            
            if frame_data is None or not camera_connected:
                # Use placeholder with connection status
                frame_data = create_placeholder_frame(camera_id)
            
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
    
    def get_pose_analysis(self, camera_id):
        """Get current pose analysis for camera"""
        try:
            # Get latest skeletal data for this camera
            skeletal_data = self.analytics.get_latest_skeletal_data(camera_id)
            
            # DEBUG: Log what we're getting
            logger.debug(f"[DEBUG] get_pose_analysis for {camera_id}: skeletal_data = {skeletal_data is not None}")
            
            if not skeletal_data:
                response = {
                    "camera_id": camera_id,
                    "status": "no_data",
                    "timestamp": time.time(),
                    "message": "No skeletal data available"
                }
                self.send_json_response(200, response)
                return
            
            pose_data = skeletal_data.get("pose_data")
            server_analysis = skeletal_data.get("server_analysis")
            
            # DEBUG: Log what data we have
            logger.debug(f"[DEBUG] pose_data exists: {pose_data is not None}, server_analysis exists: {server_analysis is not None}")
            
            # Use server_analysis if available, otherwise use pose_data
            analysis_data = server_analysis if server_analysis else pose_data
            
            if not analysis_data:
                response = {
                    "camera_id": camera_id,
                    "status": "no_pose_data",
                    "timestamp": time.time(),
                    "message": "No pose analysis data available"
                }
                self.send_json_response(200, response)
                return
            
            # Extract fall detection data with proper field names
            fall_detection_data = {}
            
            # Check for new method naming (method1, method2, method3)
            if analysis_data.get("fall_detected_method1") is not None:
                # New naming scheme
                fall_detection_data = {
                    "method1": {
                        "detected": bool(analysis_data.get("fall_detected_method1", False)),
                        "counter": analysis_data.get("fall_counter_method1", 0),
                        "description": "BBox Motion only"
                    },
                    "method2": {
                        "detected": bool(analysis_data.get("fall_detected_method2", False)),
                        "counter": analysis_data.get("fall_counter_method2", 0),
                        "description": "BBox+Pose AND"
                    },
                    "method3": {
                        "detected": bool(analysis_data.get("fall_detected_method3", False)),
                        "counter": analysis_data.get("fall_counter_method3", 0),
                        "description": "Flexible Verification"
                    }
                }
            # Check for old naming scheme (for backward compatibility)
            elif analysis_data.get("fall_detected_old") is not None or analysis_data.get("fall_detected_new") is not None:
                fall_detection_data = {
                    "method1": {
                        "detected": bool(analysis_data.get("fall_detected_old", False)),
                        "counter": analysis_data.get("fall_counter_old", 0),
                        "description": "Legacy Old Method"
                    },
                    "method2": {
                        "detected": bool(analysis_data.get("fall_detected_new", False)),
                        "counter": analysis_data.get("fall_counter_new", 0),
                        "description": "Legacy New Method"
                    },
                    "method3": {
                        "detected": bool(analysis_data.get("fall_alert", False) or 
                                        analysis_data.get("fall_detected_method3", False)),
                        "counter": max(analysis_data.get("fall_counter_old", 0), 
                                    analysis_data.get("fall_counter_new", 0)),
                        "description": "Flexible (Consensus)"
                    }
                }
            else:
                # No fall detection data found
                logger.warning(f"[DEBUG] No fall detection data found in analysis_data for {camera_id}")
                logger.warning(f"[DEBUG] analysis_data keys: {list(analysis_data.keys())}")
                fall_detection_data = {
                    "method1": {"detected": False, "counter": 0, "description": "No data"},
                    "method2": {"detected": False, "counter": 0, "description": "No data"},
                    "method3": {"detected": False, "counter": 0, "description": "No data"}
                }
            
            # DEBUG: Log fall detection data
            logger.debug(f"[DEBUG] Fall detection data for {camera_id}: {fall_detection_data}")
            
            # Determine primary alert based on method3 (most conservative)
            primary_alert = fall_detection_data.get("method3", {}).get("detected", False)
            
            response = {
                "camera_id": camera_id,
                "pose_data": analysis_data,
                "fall_detection": fall_detection_data,
                "track_id": skeletal_data.get("track_id", 0),
                "timestamp": skeletal_data.get("timestamp", time.time()),
                "server_analysis_time": time.time(),
                "primary_alert": primary_alert,
                # Add metadata about available algorithms
                "algorithms_available": [1, 2, 3],
                "algorithm_descriptions": {
                    1: "BBox Motion only",
                    2: "BBox+Pose AND",
                    3: "Flexible Verification"
                },
                "status": "success"
            }
            
            self.send_json_response(200, response)
            
        except Exception as e:
            logger.error(f"Pose analysis error for {camera_id}: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
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
                    'source_addr': self.client_address[0],
                    'last_upload': time.time()
                }
            
            logger.debug(f"Received frame from {camera_id} ({len(body)} bytes)")
            
            # Send success response
            response = {
                "status": "success", 
                "message": f"Frame received ({len(body)} bytes)",
                "timestamp": time.time(),
                "camera_id": camera_id
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
                        "use_safety_check": True,
                        "analytics_mode": True,
                        "fall_algorithm": 3
                    },
                    "safe_areas": [],
                    "last_command": time.time(),
                    "connected": False
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
            elif command == "toggle_analytics_mode":
                self.analytics.camera_states[camera_id]["control_flags"]["analytics_mode"] = bool(value)
            elif command == "set_fall_algorithm":
                algorithm = int(value) if isinstance(value, (int, float)) else 3
                if algorithm in [1, 2, 3]:
                    self.analytics.camera_states[camera_id]["control_flags"]["fall_algorithm"] = algorithm
                    logger.info(f"Fall algorithm set to {algorithm} for {camera_id}")
                else:
                    logger.warning(f"Invalid fall algorithm: {value}, defaulting to 3")
                    self.analytics.camera_states[camera_id]["control_flags"]["fall_algorithm"] = 3
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
                "last_report": time.time(),
                "connected": True
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
                    # Camera is active if seen in last 30 seconds
                    if current_time - last_seen < 30:
                        status = "connected"
                        online = True
                    else:
                        status = "disconnected"
                        online = False
                    
                    active_cameras.append({
                        "camera_id": cam_id,
                        "last_seen": last_seen,
                        "ip_address": frame_info.get('source_addr', 'unknown'),
                        "online": online,
                        "status": status,
                        "age_seconds": current_time - last_seen
                    })
            
            # Also include cameras in states but not in frames
            for cam_id, state in self.analytics.camera_states.items():
                if cam_id not in [c["camera_id"] for c in active_cameras]:
                    last_seen = state.get("last_seen", 0)
                    active_cameras.append({
                        "camera_id": cam_id,
                        "last_seen": last_seen,
                        "ip_address": state.get("ip_address", "unknown"),
                        "online": False,
                        "status": "disconnected",
                        "age_seconds": current_time - last_seen
                    })
            
            response = {
                "cameras": active_cameras,
                "count": len(active_cameras),
                "connected_count": len([c for c in active_cameras if c["online"]]),
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
            
            # Add metadata and connection status
            with frame_lock:
                frame_info = camera_frames.get(camera_id, {})
                last_seen = frame_info.get('timestamp', 0)
                connected = time.time() - last_seen < 30
            
            flags["_timestamp"] = time.time()
            flags["_camera_id"] = camera_id
            flags["_connected"] = connected
            flags["_last_seen"] = last_seen
            
            self.send_json_response(200, flags)
            
        except Exception as e:
            logger.error(f"Camera state error for {camera_id}: {e}")
            self.send_error(500, "Internal Server Error")
    
    def get_camera_status(self, camera_id):
        """Return camera connection status"""
        try:
            with frame_lock:
                frame_info = camera_frames.get(camera_id, {})
                last_seen = frame_info.get('timestamp', 0)
            
            connected = time.time() - last_seen < 30
            
            response = {
                "camera_id": camera_id,
                "connected": connected,
                "last_seen": last_seen,
                "status": "connected" if connected else "disconnected"
            }
            
            self.send_json_response(200, response)
            
        except Exception as e:
            logger.error(f"Camera status error for {camera_id}: {e}")
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
            upload_data = data.get("data", {})
            
            if data_type == "skeletal_data":
                hme_mode = upload_data.get("hme_mode", False)
                
                if hme_mode and USE_HME:
                    # HME mode: process encrypted features
                    encrypted_features = upload_data.get("encrypted_features", {})
                    
                    # Analytics performs encrypted comparisons
                    comparison_results = pose_estimator.perform_hme_comparisons(encrypted_features)
                    
                    if comparison_results:
                        # Send comparison results to caregiver for decryption
                        # In real implementation, this would be sent back to the caregiver
                        # For now, we'll simulate the decryption locally
                        pose_label = pose_estimator.decrypt_comparison_results(comparison_results)
                        
                        upload_data["server_analysis"] = {
                            "label": pose_label,
                            "hme_processed": True,
                            "comparison_results": comparison_results
                        }
                        
                        print(f"[HME] Processed encrypted features from {camera_id}, pose: {pose_label}")
                else:
                    # Plain mode: original server-side analysis
                    if "keypoints" in upload_data and "bbox" in upload_data:
                        track_id = upload_data.get("track_id", 0)
                        pose_data = analyze_pose_on_server(
                            upload_data["keypoints"],
                            upload_data["bbox"],
                            track_id,
                            camera_id
                        )
                        upload_data["server_analysis"] = pose_data
                
                self.analytics.process_skeletal_data(camera_id, upload_data)
            elif data_type == "pose_alert":
                self.analytics.process_pose_alert(camera_id, upload_data)
            elif data_type == "recording_started":
                logger.info(f"Recording started on camera {camera_id}: {upload_data.get('timestamp')}")
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
                connected_cameras = sum(1 for cam_id in camera_frames 
                                      if time.time() - camera_frames[cam_id].get('timestamp', 0) < 30)
            
            stats = {
                "total_cameras": len(self.analytics.camera_states),
                "connected_cameras": connected_cameras,
                "diagnoses": len(self.analytics.diagnosis_history),
                "wifi_connected": self.analytics.wifi_connector.connected,
                "wifi_ssid": self.analytics.wifi_connector.ssid,
                "timestamp": time.time(),
                "fall_threshold": FALL_COUNT_THRES,
                "queue_size": queue_size,
                "fps": fps
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
                "uptime": getattr(self.analytics, 'start_time', time.time()),
                "cameras_registered": len(self.analytics.camera_states),
                "pose_estimator": "Available" if pose_estimator else "Not available",
                "fall_detection": "Available (using same logic as MaixCAM)",
                "fall_threshold": FALL_COUNT_THRES
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
                            'source': info.get('source_addr', 'unknown'),
                            'connected': time.time() - info.get('timestamp', 0) < 30 if info.get('timestamp') else False
                        }
                        for cam_id, info in camera_frames.items()
                    },
                    'camera_states': {
                        cam_id: {
                            'last_seen': state.get('last_seen', 0),
                            'age_seconds': time.time() - state.get('last_seen', 0),
                            'has_flags': bool(state.get('control_flags')),
                            'connected': state.get('connected', False)
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
        self.latest_skeletal_data = {}  # camera_id -> latest skeletal data
        
        # WiFi connection
        self.wifi_connector = WiFiConnector("MaixCAM-Wifi", "maixcamwifi")
        
        # HTTP server
        self.http_port = port
        self.http_server = None
        
        # Create default placeholder frame
        create_placeholder_frame("default")
        
        # Start time
        self.start_time = time.time()
        
    def get_latest_skeletal_data(self, camera_id):
        """Get latest skeletal data for a camera"""
        return self.latest_skeletal_data.get(camera_id)
    
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
            
            # Log pose estimation status
            logger.info(f"Pose Estimator: {'Available' if pose_estimator else 'Not available'}")
            logger.info(f"Fall Detection: Available (using same logic as MaixCAM)")
            logger.info(f"Fall Threshold: {FALL_COUNT_THRES}")
            
            # Start server in background thread
            server_thread = threading.Thread(target=self.http_server.serve_forever, daemon=True)
            server_thread.start()
            
            logger.info("=" * 60)
            logger.info("📊 Analytics Gateway Service Started Successfully!")
            logger.info(f"🌐 Analytics IP: {ip}")
            logger.info(f"🖥️  Dashboard: http://{ip}:{self.http_port}")
            logger.info(f"🎯 Using same pose/fall detection as MaixCAM")
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
                # Try to get IP from camera state
                state = self.camera_states.get(camera_id, {})
                camera_ip = state.get('ip_address')
            
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
            # Store latest data
            self.latest_skeletal_data[camera_id] = data
            
            # Get pose data (use server analysis if available, otherwise use camera data)
            pose_data = data.get("server_analysis") or data.get("pose_data")
            
            # Perform analytics using server-side analysis
            diagnosis = self.perform_advanced_analysis(camera_id, pose_data, data)
            
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
            pose_data = data.get("pose_data", {})
            
            # Get server analysis if available
            server_analysis = data.get("server_analysis")
            
            if alert_type == "fall_detected" and server_analysis:
                method1_detected = server_analysis.get("fall_detected_method1", False)
                method2_detected = server_analysis.get("fall_detected_method2", False)
                method3_detected = server_analysis.get("fall_detected_method3", False)
                counter1 = server_analysis.get("fall_counter_method1", 0)
                counter2 = server_analysis.get("fall_counter_method2", 0)
                counter3 = server_analysis.get("fall_counter_method3", 0)
                
                logger.warning(f"FALL DETECTION from {camera_id} (track {track_id}):")
                logger.warning(f"  Method 1 (BBox only): {'DETECTED' if method1_detected else 'no'} (counter={counter1})")
                logger.warning(f"  Method 2 (Flexible): {'DETECTED' if method2_detected else 'no'} (counter={counter2})")
                logger.warning(f"  Method 3 (Conservative): {'DETECTED' if method3_detected else 'no'} (counter={counter3})")
                logger.warning(f"  Threshold: {FALL_COUNT_THRES}")
            else:
                logger.warning(f"Pose alert from {camera_id}: {alert_type} for track {track_id}")
            
        except Exception as e:
            logger.error(f"Error processing pose alert from {camera_id}: {e}")

    def perform_advanced_analysis(self, camera_id: str, pose_data: dict, full_data: dict):
        """Perform advanced analytics on skeletal data using server-side analysis"""
        try:
            timestamp = full_data.get("timestamp", time.time())
            
            # Get fall detection info from server analysis if available
            fall_detected = False
            fall_confidence = 0.0
            
            if pose_data:
                fall_detected_old = pose_data.get("fall_detected_old", False)
                fall_detected_new = pose_data.get("fall_detected_new", False)
                fall_detected = fall_detected_old or fall_detected_new
                
                # Calculate fall confidence based on counters
                counter_old = pose_data.get("fall_counter_old", 0)
                counter_new = pose_data.get("fall_counter_new", 0)
                fall_threshold = pose_data.get("fall_threshold", FALL_COUNT_THRES)
                
                if fall_detected:
                    fall_confidence = max(counter_old, counter_new) / max(fall_threshold, 1)
                else:
                    fall_confidence = min(counter_old, counter_new) / max(fall_threshold, 1)
            
            # Activity classification from pose data
            activity = pose_data.get('label', 'unknown') if pose_data else 'unknown'
            
            # Enhanced risk assessment using server-side fall detection
            overall_risk = self.assess_overall_risk(fall_confidence, activity, fall_detected)
            
            diagnosis = {
                "camera_id": camera_id,
                "timestamp": timestamp,
                "analysis_time": datetime.now().isoformat(),
                "fall_detected": fall_detected,
                "fall_confidence": fall_confidence,
                "fall_threshold": FALL_COUNT_THRES,
                "detected_activity": activity,
                "pose_data": pose_data,
                "overall_risk": overall_risk,
                "alert_level": self.determine_alert_level(overall_risk, fall_detected),
                "recommendations": self.generate_recommendations(overall_risk, activity, fall_detected),
                "confidence": 0.9 if fall_detected else 0.7,
                "analysis_source": "server_side" if full_data.get("server_analysis") else "camera_side"
            }
            
            logger.info(f"Generated diagnosis for {camera_id}: {diagnosis['alert_level']} (Fall: {fall_detected})")
            return diagnosis
            
        except Exception as e:
            logger.error(f"Error in advanced analysis for {camera_id}: {e}")
            return None

    def assess_overall_risk(self, fall_confidence, activity, fall_detected):
        """Assess overall risk"""
        activity_risk = self.activity_risk(activity)
        
        if fall_detected:
            overall_risk = 0.8 + (fall_confidence * 0.2)  # Base 0.8 for detected fall
        else:
            overall_risk = (fall_confidence * 0.7) + (activity_risk * 0.3)
        
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

    def determine_alert_level(self, overall_risk, fall_detected):
        """Determine alert level based on risk score and fall detection"""
        if fall_detected:
            return "critical"
        elif overall_risk >= 0.8:
            return "critical"
        elif overall_risk >= 0.6:
            return "high"
        elif overall_risk >= 0.4:
            return "medium"
        elif overall_risk >= 0.2:
            return "low"
        else:
            return "normal"

    def generate_recommendations(self, overall_risk, activity, fall_detected):
        """Generate recommendations based on risk, activity, and fall detection"""
        recommendations = []
        
        if fall_detected:
            recommendations.extend([
                "FALL DETECTED - Immediate caregiver attention required!",
                "Check patient position and vital signs immediately",
                "Emergency response may be needed"
            ])
        elif overall_risk >= 0.8:
            recommendations.extend([
                "Immediate caregiver attention required",
                "High fall risk detected - monitor closely"
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
        elif activity == "transitioning":
            recommendations.append("Assist with position changes")
            
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