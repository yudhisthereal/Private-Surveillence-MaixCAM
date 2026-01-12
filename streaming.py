# streaming.py - Streaming server communication

import requests
from config import STREAMING_HTTP_URL, CAMERA_ID

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

def send_frame_to_server(frame_data, camera_id=None):
    """Send frame to streaming server"""
    try:
        url = f"{STREAMING_HTTP_URL}/api/stream/upload-frame"
        headers = {'X-Camera-ID': camera_id or CAMERA_ID}
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

def send_command_response(command, value, status="success"):
    """Send command response to streaming server"""
    try:
        url = f"{STREAMING_HTTP_URL}/api/stream/command-response"
        data = {
            "camera_id": CAMERA_ID,
            "command": command,
            "value": value,
            "status": status
        }
        response = requests.post(
            url,
            json=data,
            headers={'Content-Type': 'application/json'},
            timeout=2.0
        )
        return response.status_code == 200
    except Exception as e:
        print(f"Command response error: {e}")
        return False

def send_camera_state(state_data):
    """Send camera state to streaming server"""
    try:
        url = f"{STREAMING_HTTP_URL}/api/stream/camera-state"
        data = {
            "camera_id": CAMERA_ID,
            **state_data
        }
        response = requests.post(
            url,
            json=data,
            headers={'Content-Type': 'application/json'},
            timeout=2.0
        )
        return response.status_code == 200
    except Exception as e:
        print(f"Camera state update error: {e}")
        return False

def send_background_updated(timestamp):
    """Notify streaming server that background was updated"""
    try:
        return send_to_streaming_server("/api/stream/command", {
            "camera_id": CAMERA_ID,
            "command": "background_updated",
            "value": {"timestamp": timestamp}
        })
    except Exception as e:
        print(f"Background update notification error: {e}")
        return False

def get_camera_state_from_server():
    """Get camera state from streaming server"""
    try:
        url = f"{STREAMING_HTTP_URL}/api/stream/camera-state?camera_id={CAMERA_ID}"
        response = requests.get(url, timeout=2.0)
        if response.status_code == 200:
            return response.json()
        return None
    except Exception as e:
        print(f"Get camera state error: {e}")
        return None

def get_safe_areas_from_server():
    """Get safe areas from streaming server"""
    try:
        url = f"{STREAMING_HTTP_URL}/api/stream/safe-areas?camera_id={CAMERA_ID}"
        response = requests.get(url, timeout=2.0)
        if response.status_code == 200:
            return response.json()
        return []
    except Exception as e:
        print(f"Get safe areas error: {e}")
        return []

def check_server_available():
    """Check if streaming server is available"""
    try:
        url = f"{STREAMING_HTTP_URL}/api/stream/ping"
        response = requests.get(url, timeout=2.0)
        return response.status_code == 200
    except Exception as e:
        print(f"Server ping error: {e}")
        return False

def report_state(is_recording=False, rtmp_connected=False):
    """Report camera state to streaming server"""
    try:
        state_report = {
            "camera_id": CAMERA_ID,
            "status": "online",
            "is_recording": is_recording,
            "rtmp_connected": rtmp_connected
        }
        url = f"{STREAMING_HTTP_URL}/api/stream/report-state"
        response = requests.post(
            url,
            json=state_report,
            headers={'Content-Type': 'application/json'},
            timeout=2.0
        )
        return response.status_code == 200
    except Exception as e:
        print(f"State report error: {e}")
        return False

