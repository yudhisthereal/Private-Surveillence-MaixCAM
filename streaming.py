# streaming.py - Streaming server communication (frame upload and generic streaming server helpers)

import requests
import time
from config import STREAMING_HTTP_URL
from debug_config import debug_print

def send_to_streaming_server(endpoint, data):
    """Send data to streaming server"""
    try:
        url = f"{STREAMING_HTTP_URL}{endpoint}"
        debug_print("API_REQUEST", "POST %s | endpoint: %s | payload: %s", "POST", endpoint, str(data)[:200])
        response = requests.post(
            url,
            json=data,
            headers={'Content-Type': 'application/json'},
            timeout=2.0
        )
        return response.status_code == 200
    except Exception as e:
        print(f"Streaming server error: {e}")
        return False

def send_frame_to_server(frame_data, camera_id):
    """Send frame to streaming server"""
    try:
        url = f"{STREAMING_HTTP_URL}/api/stream/upload-frame"
        headers = {'X-Camera-ID': camera_id}
        debug_print("API_REQUEST", "POST %s | endpoint: /api/stream/upload-frame | params: camera_id=%s | payload_size: %d bytes", "POST", camera_id, len(frame_data))
        response = requests.post(
            url,
            headers=headers,
            data=frame_data,
            timeout=2.0
        )
        return response.status_code == 200
    except Exception as e:
        print(f"Frame upload error: {e}")
        return False

def ping_streaming_server(camera_id):
    """Ping streaming server to notify camera is connected (fire-and-forget)
    
    Sends a ping to /api/stream/ping endpoint with camera_id as query parameter.
    This is a non-blocking call - we don't wait for or check the response.
    """
    try:
        url = f"{STREAMING_HTTP_URL}/api/stream/ping"
        params = {'camera_id': camera_id}
        debug_print("API_REQUEST", "POST %s | endpoint: /api/stream/ping | params: camera_id=%s", "POST", camera_id)
        # Fire-and-forget: timeout is short and we don't wait for response
        requests.post(url, params=params, timeout=0.5)
    except Exception:
        # Silently ignore ping failures - this is a fire-and-forget operation
        pass

def send_pose_label_to_streaming_server(camera_id, track_id, pose_label, safety_status="normal"):
    """Send pose label to streaming server for logging/display
    
    Args:
        camera_id: Camera identifier
        track_id: Track ID of the person
        pose_label: Pose classification label (standing, sitting, bending_down, lying_down, unknown)
        safety_status: Safety status (normal, unsafe, fall)
    
    Returns:
        bool: True if sent successfully, False otherwise
    """
    try:
        url = f"{STREAMING_HTTP_URL}/api/stream/pose-label"
        data = {
            "camera_id": camera_id,
            "track_id": track_id,
            "pose_label": pose_label,
            "safety_status": safety_status,
            "timestamp": time.time()
        }
        debug_print("API_REQUEST", "POST %s | endpoint: /api/stream/pose-label | payload: %s", "POST", str(data)[:150])
        response = requests.post(url, json=data, timeout=2.0)
        return response.status_code == 200
    except Exception as e:
        print(f"Pose label streaming error: {e}")
        return False

def send_keypoints_to_streaming_server(camera_id, track_id, keypoints, bbox=None, pose_label=None, safety_status="normal"):
    """Send keypoints to streaming server for logging/display
    
    Args:
        camera_id: Camera identifier
        track_id: Track ID of the person
        keypoints: List of 34 floats (17 keypoints × 2 coordinates)
        bbox: Optional bounding box [x, y, width, height]
        pose_label: Optional pose classification label
        safety_status: Safety status (normal, unsafe, fall)
    
    Returns:
        bool: True if sent successfully, False otherwise
    """
    try:
        url = f"{STREAMING_HTTP_URL}/api/stream/keypoints"
        data = {
            "camera_id": camera_id,
            "track_id": track_id,
            "keypoints": keypoints if keypoints else [],
            "safety_status": safety_status,
            "timestamp": time.time()
        }
        if bbox is not None:
            data["bbox"] = bbox
        if pose_label is not None:
            data["pose_label"] = pose_label
        debug_print("API_REQUEST", "%s | endpoint: /api/stream/keypoints | payload: %s", "POST", data)
        response = requests.post(url, json=data, timeout=2.0)
        return response.status_code == 200
    except Exception as e:
        print(f"Keypoints streaming error: {e}")
        return False

def send_background_to_server(background_data, camera_id):
    """Send background image to streaming server
    
    Args:
        background_data: JPEG bytes of the background image
        camera_id: Camera identifier
    
    Returns:
        bool: True if sent successfully, False otherwise
    """
    try:
        url = f"{STREAMING_HTTP_URL}/api/stream/upload-bg"
        headers = {'X-Camera-ID': camera_id}
        debug_print("API_REQUEST", "POST %s | endpoint: /api/stream/upload-bg | params: camera_id=%s | payload_size: %d bytes", "POST", camera_id, len(background_data))
        response = requests.post(
            url,
            headers=headers,
            data=background_data,
            timeout=5.0  # Longer timeout for background image upload
        )
        if response.status_code == 200:
            print(f"[BackgroundUpload] Background image uploaded successfully")
            return True
        else:
            print(f"[BackgroundUpload] Failed to upload background: HTTP {response.status_code}")
            return False
    except Exception as e:
        print(f"[BackgroundUpload] Error uploading background: {e}")
        return False

