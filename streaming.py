# streaming.py - Streaming server communication (frame upload and generic streaming server helpers)

import requests
import time
import threading
from config import STREAMING_HTTP_URL
from debug_config import DebugLogger

# Module-level debug logger instance
logger = DebugLogger(tag="STREAMING", instance_enable=False)

def send_to_streaming_server(endpoint, data):
    """Send data to streaming server (async)
    
    Args:
        endpoint: API endpoint path
        data: Data to send as JSON
    
    Returns:
        bool: True (always returns True - fire-and-forget)
    """
    def _send():
        try:
            url = f"{STREAMING_HTTP_URL}{endpoint}"
            logger.print("API_REQUEST", "%s | endpoint: %s | payload: %s", "POST", endpoint, str(data)[:200])
            requests.post(
                url,
                json=data,
                headers={'Content-Type': 'application/json'},
                timeout=2.0
            )
        except Exception as e:
            logger.print("STREAMING", "Server error: %s", e)
    
    # Run in background thread for true async behavior
    thread = threading.Thread(target=_send, daemon=True)
    thread.start()
    return True

def send_frame_to_server(frame_data, camera_id):
    """Send frame to streaming server (async)
    
    Note: This is called from FrameUploadWorker which already runs in a separate thread,
    but we make the request itself async to avoid blocking the worker thread.
    """
    # Note: This function is called from FrameUploadWorker which already handles async behavior.
    # The request itself is blocking but that's acceptable since it's in a worker thread.
    # For true async, we could wrap this in a thread, but FrameUploadWorker already manages
    # the async behavior by skipping frames if upload is in progress.
    try:
        url = f"{STREAMING_HTTP_URL}/api/stream/upload-frame"
        headers = {'X-Camera-ID': camera_id}
        logger.print("API_REQUEST", "%s | endpoint: /api/stream/upload-frame | params: camera_id=%s | payload_size: %d bytes", "POST", camera_id, len(frame_data))
        response = requests.post(
            url,
            headers=headers,
            data=frame_data,
            timeout=2.0
        )
        return response.status_code == 200
    except Exception as e:
        logger.print("STREAMING", "Frame upload error: %s", e)
        return False

def ping_streaming_server(camera_id):
    """Ping streaming server to notify camera is connected (fire-and-forget)
    
    Sends a ping to /api/stream/ping endpoint with camera_id as query parameter.
    This is a non-blocking call - we don't wait for or check the response.
    """
    def _ping():
        try:
            url = f"{STREAMING_HTTP_URL}/api/stream/ping"
            params = {'camera_id': camera_id}
            logger.print("API_REQUEST", "%s | endpoint: /api/stream/ping | params: camera_id=%s", "POST", camera_id)
            # Fire-and-forget: timeout is short and we don't wait for response
            requests.post(url, params=params, timeout=0.5)
        except Exception:
            # Silently ignore ping failures - this is a fire-and-forget operation
            pass
    
    # Run in background thread for true async behavior
    thread = threading.Thread(target=_ping, daemon=True)
    thread.start()

def send_pose_label_to_streaming_server(camera_id, track_id, pose_label, safety_status="normal"):
    """Send pose label to streaming server for logging/display (async)
    
    Args:
        camera_id: Camera identifier
        track_id: Track ID of the person
        pose_label: Pose classification label (standing, sitting, bending_down, lying_down, unknown)
        safety_status: Safety status (normal, unsafe, fall)
    
    Returns:
        bool: True (always returns True - fire-and-forget)
    """
    def _send():
        try:
            url = f"{STREAMING_HTTP_URL}/api/stream/pose-label"
            data = {
                "camera_id": camera_id,
                "track_id": track_id,
                "pose_label": pose_label,
                "safety_status": safety_status,
                "timestamp": time.time()
            }
            logger.print("API_REQUEST", "%s | endpoint: /api/stream/pose-label | payload: %s", "POST", str(data)[:150])
            requests.post(url, json=data, timeout=2.0)
        except Exception as e:
            logger.print("STREAMING", "Pose label error: %s", e)
    
    # Run in background thread for true async behavior
    thread = threading.Thread(target=_send, daemon=True)
    thread.start()
    return True

def send_keypoints_to_streaming_server(camera_id, track_id, keypoints, bbox=None, pose_label=None, safety_status="normal"):
    """Send keypoints to streaming server for logging/display
    
    FIRE-AND-FORGET: This function sends data asynchronously with a short timeout.
    It does NOT wait for or check the response - this achieves near 0ms interval.
    
    Args:
        camera_id: Camera identifier
        track_id: Track ID of the person
        keypoints: List of 34 floats (17 keypoints × 2 coordinates)
        bbox: Optional bounding box [x, y, width, height]
        pose_label: Optional pose classification label
        safety_status: Safety status (normal, unsafe, fall)
    
    Returns:
        bool: True (always returns True - fire-and-forget)
    """
    def _send():
        try:
            url = f"{STREAMING_HTTP_URL}/api/stream/keypoints"
            data = {
                "camera_id": camera_id,
                "track_id": track_id,
                "keypoints": keypoints if keypoints else [],
                "safety_status": safety_status,
                "timestamp": time.time()
            }
            logger.print("KEYPOINTS", "Track ID: %s", track_id)
            if bbox is not None:
                data["bbox"] = bbox
            if pose_label is not None:
                data["pose_label"] = pose_label
            logger.print("API_REQUEST", "%s | endpoint: /api/stream/keypoints | payload: %s", "POST", data)
            # Fire-and-forget: short timeout, don't wait for or check response
            requests.post(url, json=data, timeout=0.1)
        except Exception:
            # Silently ignore errors - fire-and-forget operation
            pass
    
    # Run in background thread for true async behavior
    thread = threading.Thread(target=_send, daemon=True)
    thread.start()
    return True

def send_background_to_server(background_data, camera_id):
    """Send background image to streaming server (async)

    Args:
        background_data: JPEG bytes of the background image
        camera_id: Camera identifier

    Returns:
        bool: True (always returns True - fire-and-forget)
    """
    def _send():
        try:
            url = f"{STREAMING_HTTP_URL}/api/stream/upload-bg"
            headers = {'X-Camera-ID': camera_id}
            logger.print("API_REQUEST", "%s | endpoint: /api/stream/upload-bg | params: camera_id=%s | payload_size: %d bytes", "POST", camera_id, len(background_data))
            response = requests.post(
                url,
                headers=headers,
                data=background_data,
                timeout=5.0  # Longer timeout for background image upload
            )
            logger.print("BACKGROUND", "Upload response: %s", response.json())
            if response.status_code == 200:
                logger.print("BACKGROUND", "Background image uploaded successfully")
            else:
                logger.print("BACKGROUND", "Failed to upload background: HTTP %d", response.status_code)
        except Exception as e:
            logger.print("BACKGROUND", "Error uploading background: %s", e)
    
    # Run in background thread for true async behavior
    thread = threading.Thread(target=_send, daemon=True)
    thread.start()
    return True

def send_tracks_to_streaming_server(camera_id, tracks):
    """Send all tracks to streaming server (fire-and-forget).

    FIRE-AND-FORGET: This function sends data asynchronously with a short timeout.
    It does NOT wait for or check the response - this achieves near 0ms interval.

    Args:
        camera_id: Camera identifier
        tracks: List of track dictionaries, each containing:
            - track_id: int
            - keypoints: list of 34 floats (17 keypoints × 2 coordinates)
            - bbox: list [x, y, w, h]
            - pose_label: str
            - safety_status: str (normal, unsafe, fall)
            - encrypted_features: dict (omitted from sending to streaming server)

    Returns:
        bool: True (always returns True - fire-and-forget)
    """
    def _send():
        try:
            url = f"{STREAMING_HTTP_URL}/api/stream/tracks"
            data = {
                "camera_id": camera_id,
                "tracks": tracks,
                "timestamp": time.time()
            }
            logger.print("POST TRACKS", "%s | endpoint: /api/stream/tracks | payload: %s", "POST", data)
            
            # Fire-and-forget: short timeout, don't wait for or check response
            requests.post(url, json=data, timeout=0.1)
        except Exception:
            # Silently ignore errors - fire-and-forget operation
            pass
    
    # Run in background thread for true async behavior
    thread = threading.Thread(target=_send, daemon=True)
    thread.start()
    return True

