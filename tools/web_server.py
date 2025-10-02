import socket
import threading
import os
import time
import json

# For MJPEG stream
from io import BytesIO
from maix import image

# === Shared State ===
latest_jpeg = None  # updated externally by main.py
img_snapshot = None # will be used by main.py to update static bg
clients = set()     # active MJPEG streaming clients
control_flags = {
    "record": False,
    "show_raw": False,
    "set_background": False,
    "auto_update_bg": True,
    "show_safe_area": False,
    "use_safety_check": True
}

# === Config ===
STATIC_DIR = os.path.join(os.path.dirname(__file__), "../static")
STREAM_JPG_PATH = "/tmp/stream_frame.jpg"
SAFE_AREA_FILE = "/root/safe_areas.json"
HTTP_PORT = 80
WS_PORT = 8081

# Load safe areas from file
safe_areas = []
try:
    if os.path.exists(SAFE_AREA_FILE):
        with open(SAFE_AREA_FILE, 'r') as f:
            safe_areas = json.load(f)
        print(f"Loaded {len(safe_areas)} safe area(s) from file")
except Exception as e:
    print(f"Error loading safe areas: {e}")

def save_safe_areas():
    """Save safe areas to file"""
    try:
        with open(SAFE_AREA_FILE, 'w') as f:
            json.dump(safe_areas, f)
        print(f"Saved {len(safe_areas)} safe area(s) to file")
        return True
    except Exception as e:
        print(f"Error saving safe areas: {e}")
        return False

# === HTTP Server Thread ===
def handle_http(conn, addr):
    global latest_jpeg, img_snapshot
    try:
        request = conn.recv(1024).decode("utf-8")
        if not request:
            conn.close()
            return
        path = request.split(" ")[1]

        if path == "/" or path == "/index.html":
            file_path = os.path.join(STATIC_DIR, "index.html")
            mime = "text/html"
        elif path.endswith(".js"):
            file_path = os.path.join(STATIC_DIR, os.path.basename(path))
            mime = "application/javascript"
        elif path.endswith(".css"):
            file_path = os.path.join(STATIC_DIR, os.path.basename(path))
            mime = "text/css"
        elif path == "/stream.mjpg":
            stream_mjpeg(conn)
            return
        elif path.startswith("/snapshot.jpg"):
            if latest_jpeg:
                img_snapshot = image.load(STREAM_JPG_PATH, format = image.Format.FMT_RGBA8888)
                conn.send(b"HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\n\r\n")
                conn.send(latest_jpeg)
            else:
                conn.send(b"HTTP/1.1 503 Service Unavailable\r\n\r\n")
            conn.close()
            return
        elif path == "/command":
            try:
                header, body = request.split("\r\n\r\n", 1)
                print("[Command] Body:", body)
                msg = json.loads(body)
                handle_command(msg)
                conn.send(b"HTTP/1.1 200 OK\r\n\r\n")
            except Exception as e:
                print("Command error:", e)
                conn.send(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            conn.close()
            return
        elif path == "/get_safe_areas":
            # Return current safe areas
            conn.send(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n")
            conn.send(json.dumps(safe_areas).encode())
            conn.close()
            return
        elif path == "/set_safe_areas":
            # Set new safe areas
            try:
                header, body = request.split("\r\n\r\n", 1)
                new_safe_areas = json.loads(body)
                safe_areas.clear()
                safe_areas.extend(new_safe_areas)
                success = save_safe_areas()
                if success:
                    conn.send(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n")
                    conn.send(json.dumps({"status": "success"}).encode())
                else:
                    conn.send(b"HTTP/1.1 500 Internal Server Error\r\n\r\n")
            except Exception as e:
                print("Set safe areas error:", e)
                conn.send(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            conn.close()
            return
        else:
            conn.send(b"HTTP/1.1 404 Not Found\r\n\r\n")
            conn.close()
            return

        with open(file_path, "rb") as f:
            body = f.read()
        header = f"HTTP/1.1 200 OK\r\nContent-Type: {mime}\r\nContent-Length: {len(body)}\r\n\r\n"
        conn.send(header.encode("utf-8") + body)
    except Exception as e:
        print("HTTP error:", e)
    finally:
        conn.close()


# === MJPEG Streaming ===
def stream_mjpeg(conn):
    global latest_jpeg
    try:
        conn.send(b"HTTP/1.1 200 OK\r\nContent-Type: multipart/x-mixed-replace; boundary=frame\r\n\r\n")
        clients.add(conn)
        while True:
            if latest_jpeg:
                conn.send(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n")
                conn.send(latest_jpeg)
                conn.send(b"\r\n")
            time.sleep(0.05)
    except:
        pass
    finally:
        clients.discard(conn)
        conn.close()

def confirm_background(path):
    global img_snapshot
    img_snapshot.save(path)
    img_snapshot = None


def handle_command(msg):
    cmd = msg.get("command")
    val = msg.get("value")
    if cmd == "toggle_record":
        control_flags["record"] = bool(val)
    elif cmd == "toggle_raw":
        control_flags["show_raw"] = bool(val)
    elif cmd == "auto_update_bg":
        control_flags["auto_update_bg"] = bool(val)
    elif cmd == "set_background":
        control_flags["set_background"] = True
    elif cmd == "toggle_safe_area_display":
        control_flags["show_safe_area"] = bool(val)
    elif cmd == "toggle_safety_check":
        control_flags["use_safety_check"] = bool(val)


# === External API ===
def send_frame(img):
    global latest_jpeg

    try:
        img.save(STREAM_JPG_PATH, quality=80)
        with open(STREAM_JPG_PATH, "rb") as f:
            latest_jpeg = f.read()
    except Exception as e:
        print("Error saving JPEG for stream:", e)

def get_control_flags():
    return control_flags

def get_safe_areas():
    return safe_areas

def reset_set_background_flag():
    control_flags["set_background"] = False

# === Main Server Loops ===
def start_servers():
    # HTTP Server
    def http_loop():
        sk = socket.socket()
        sk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sk.bind(("0.0.0.0", HTTP_PORT))
        sk.listen(5)
        print(f"[HTTP] Listening on port {HTTP_PORT}")
        while True:
            conn, addr = sk.accept()
            threading.Thread(target=handle_http, args=(conn, addr), daemon=True).start()

    threading.Thread(target=http_loop, daemon=True).start()