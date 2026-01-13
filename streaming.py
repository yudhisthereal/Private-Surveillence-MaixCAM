# streaming.py - Streaming server communication (frame upload and generic streaming server helpers)

import requests
from config import STREAMING_HTTP_URL

def send_to_streaming_server(endpoint, data):
    """Send data to streaming server"""
    try:
        url = f"{STREAMING_HTTP_URL}{endpoint}"
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
        # Fire-and-forget: timeout is short and we don't wait for response
        requests.post(url, params=params, timeout=0.5)
    except Exception:
        # Silently ignore ping failures - this is a fire-and-forget operation
        pass

