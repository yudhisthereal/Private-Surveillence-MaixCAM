# streaming.py - Streaming server communication (frame upload and generic streaming server helpers)

import requests
import time
import threading
from config import STREAMING_HTTP_URL
from debug_config import DebugLogger

# Module-level debug logger instance
logger = DebugLogger(tag="STREAMING", instance_enable=True)


def _fire_and_forget_post(endpoint, json_data=None, data=None, params=None, headers=None, timeout=2.0, tag="API_REQUEST", log_success=False):
    """Generic helper to send POST requests in a background thread."""
    def _send():
        try:
            url = f"{STREAMING_HTTP_URL}{endpoint}"
            
            req_headers = dict(headers) if headers else {}
            
            if json_data is not None:
                # Set content type only if not already set
                if 'Content-Type' not in req_headers:
                    req_headers['Content-Type'] = 'application/json'

                payload_str = str(json_data)
                # Truncate payload string for logging if it's too long and not tracks endpoint
                if len(payload_str) > 200 and endpoint != "/api/stream/tracks":
                    payload_info = f"payload: {payload_str[:200]}..."
                else:
                    payload_info = f"payload: {payload_str}"
            elif data is not None:
                payload_info = f"payload_size: {len(data)} bytes"
            else:
                payload_info = "empty"
                
            log_msg = f"POST | endpoint: {endpoint}"
            if params: log_msg += f" | params: {params}"
            log_msg += f" | {payload_info}"
            
            logger.print(tag, log_msg)
            
            response = requests.post(
                url,
                json=json_data,
                data=data,
                params=params,
                headers=req_headers if req_headers else None,
                timeout=timeout
            )
            
            if log_success:
                if response.status_code == 200:
                    logger.print(tag, "%s successful", endpoint)
                else:
                    logger.print(tag, "%s failed: HTTP %d", endpoint, response.status_code)
                    
        except Exception as e:
            # For very short timeouts (< 0.5s), we expect ReadTimeout often, so ignore it silently
            if timeout >= 0.5:
                logger.print("STREAMING", "%s error: %s", tag, e)
                
    thread = threading.Thread(target=_send, daemon=True)
    thread.start()
    return True


def send_to_streaming_server(endpoint, data):
    """Send data to streaming server (async)
    
    Args:
        endpoint: API endpoint path
        data: Data to send as JSON
    
    Returns:
        bool: True (always returns True - fire-and-forget)
    """
    return _fire_and_forget_post(endpoint, json_data=data)


def send_frame_to_server(frame_data, camera_id):
    """Send frame to streaming server (async)
    
    Note: This is called from FrameUploadWorker which already runs in a separate thread,
    but we make the request itself async to avoid blocking the worker thread.
    """
    # Note: This function is called from FrameUploadWorker which already handles async behavior.
    # The request itself is blocking but that's acceptable since it's in a worker thread.
    try:
        url = f"{STREAMING_HTTP_URL}/api/stream/upload-frame"
        headers = {'X-Camera-ID': camera_id}
        logger.print("API_REQUEST", "POST | endpoint: /api/stream/upload-frame | params: camera_id=%s | payload_size: %d bytes", camera_id, len(frame_data))
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
    _fire_and_forget_post(
        "/api/stream/ping",
        params={'camera_id': camera_id},
        timeout=0.5,
        log_success=True
    )


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
    data = {
        "camera_id": camera_id,
        "track_id": track_id,
        "pose_label": pose_label,
        "safety_status": safety_status,
        "timestamp": time.time()
    }
    return _fire_and_forget_post("/api/stream/pose-label", json_data=data)


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
        
    return _fire_and_forget_post("/api/stream/keypoints", json_data=data, timeout=0.1)


def send_background_to_server(background_data, camera_id):
    """Send background image to streaming server (async)

    Args:
        background_data: JPEG bytes of the background image
        camera_id: Camera identifier

    Returns:
        bool: True (always returns True - fire-and-forget)
    """
    return _fire_and_forget_post(
        "/api/stream/upload-bg",
        data=background_data,
        headers={'X-Camera-ID': camera_id},
        timeout=5.0,
        tag="BACKGROUND",
        log_success=True
    )


def send_tracks_to_streaming_server(camera_id, tracks):
    """Send all tracks to streaming server (fire-and-forget).

    FIRE-AND-FORGET: This function sends data asynchronously with a short timeout.
    It does NOT wait for or check the response - this achieves near 0ms interval.

    Args:
        camera_id: Camera identifier
        tracks: List of track dictionaries
            
    Returns:
        bool: True (always returns True - fire-and-forget)
    """
    data = {
        "camera_id": camera_id,
        "tracks": tracks,
        "timestamp": time.time()
    }
    return _fire_and_forget_post("/api/stream/tracks", json_data=data, timeout=0.1, tag="POST TRACKS")

