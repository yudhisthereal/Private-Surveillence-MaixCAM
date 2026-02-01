import socket
import threading
import os
import time
import json
from debug_config import debug_print

# For MJPEG stream
from io import BytesIO
from maix import image

class WebServer:
    def __init__(self):
        # === Shared State ===
        self.latest_jpeg = None  # updated externally by main.py
        self.img_snapshot = None # will be used by main.py to update static bg
        self.clients = set()     # active MJPEG streaming clients
        self.control_flags = {
            "record": False,
            "show_raw": False,
            "set_background": False,
            "auto_update_bg": False,
            "show_safe_areas": False,
            "show_bed_areas": False,
            "show_floor_areas": False,
            "use_safety_check": True,
            "analytics_mode": True,
            "fall_algorithm": 1,
            "hme": False
        }
        
        # === Config ===
        self.STATIC_DIR = os.path.join(os.path.dirname(__file__), "../static")
        self.STREAM_JPG_PATH = "/tmp/stream_frame.jpg"
        self.SAFE_AREA_FILE = "/root/safe_areas.json"
        self.HTTP_PORT = 80
        self.WS_PORT = 8081
        
        # Safe areas storage
        self.safe_areas = []
        self.safe_areas_updated = False
        self.safe_areas_callback = None  # Callback to notify main.py of updates
        
        # Load safe areas from file
        self.load_safe_areas()
    
    def set_safe_areas_callback(self, callback):
        """Set callback function to be called when safe areas are updated"""
        self.safe_areas_callback = callback
    
    def load_safe_areas(self):
        """Load safe areas from JSON file"""
        try:
            if os.path.exists(self.SAFE_AREA_FILE):
                with open(self.SAFE_AREA_FILE, 'r') as f:
                    self.safe_areas = json.load(f)
                debug_print("WEB_SERVER", "Loaded %d safe area(s) from file", len(self.safe_areas))
                self.safe_areas_updated = True
        except Exception as e:
            debug_print("WEB_SERVER", "Error loading safe areas: %s", e)
    
    def save_safe_areas(self):
        """Save safe areas to file"""
        try:
            with open(self.SAFE_AREA_FILE, 'w') as f:
                json.dump(self.safe_areas, f)
            debug_print("WEB_SERVER", "Saved %d safe area(s) to file", len(self.safe_areas))
            self.safe_areas_updated = True
            # Notify main.py that safe areas have been updated
            if self.safe_areas_callback:
                self.safe_areas_callback(self.safe_areas)
            return True
        except Exception as e:
            debug_print("WEB_SERVER", "Error saving safe areas: %s", e)
            return False
    
    def get_safe_areas(self):
        """Get current safe areas"""
        return self.safe_areas
    
    def safe_areas_have_updates(self):
        """Check if safe areas have been updated and reset the flag"""
        if self.safe_areas_updated:
            self.safe_areas_updated = False
            return True
        return False

    # === HTTP Server Thread ===
    def handle_http(self, conn, addr):
        try:
            request = conn.recv(1024).decode("utf-8")
            if not request:
                conn.close()
                return
            path = request.split(" ")[1]

            if path == "/" or path == "/index.html":
                file_path = os.path.join(self.STATIC_DIR, "index.html")
                mime = "text/html"
            elif path.endswith(".js"):
                file_path = os.path.join(self.STATIC_DIR, os.path.basename(path))
                mime = "application/javascript"
            elif path.endswith(".css"):
                file_path = os.path.join(self.STATIC_DIR, os.path.basename(path))
                mime = "text/css"
            elif path == "/stream.mjpg":
                self.stream_mjpeg(conn)
                return
            elif path.startswith("/snapshot.jpg"):
                if self.latest_jpeg:
                    self.img_snapshot = image.load(self.STREAM_JPG_PATH, format=image.Format.FMT_RGBA8888)
                    conn.send(b"HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\n\r\n")
                    conn.send(self.latest_jpeg)
                else:
                    conn.send(b"HTTP/1.1 503 Service Unavailable\r\n\r\n")
                conn.close()
                return
            elif path == "/command":
                try:
                    header, body = request.split("\r\n\r\n", 1)
                    debug_print("WEB_SERVER", "[Command] Body: %s", body)
                    msg = json.loads(body)
                    self.handle_command(msg)
                    conn.send(b"HTTP/1.1 200 OK\r\n\r\n")
                except Exception as e:
                    debug_print("WEB_SERVER", "Command error: %s", e)
                    conn.send(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                conn.close()
                return
            elif path == "/get_safe_areas":
                # Return current safe areas
                conn.send(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n")
                conn.send(json.dumps(self.safe_areas).encode())
                conn.close()
                return
            elif path == "/set_safe_areas":
                # Set new safe areas
                try:
                    header, body = request.split("\r\n\r\n", 1)
                    new_safe_areas = json.loads(body)
                    self.safe_areas.clear()
                    self.safe_areas.extend(new_safe_areas)
                    success = self.save_safe_areas()
                    if success:
                        conn.send(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n")
                        conn.send(json.dumps({"status": "success"}).encode())
                    else:
                        conn.send(b"HTTP/1.1 500 Internal Server Error\r\n\r\n")
                except Exception as e:
                    debug_print("WEB_SERVER", "Set safe areas error: %s", e)
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
            debug_print("WEB_SERVER", "HTTP error: %s", e)
        finally:
            conn.close()

    # === MJPEG Streaming ===
    def stream_mjpeg(self, conn):
        try:
            conn.send(b"HTTP/1.1 200 OK\r\nContent-Type: multipart/x-mixed-replace; boundary=frame\r\n\r\n")
            self.clients.add(conn)
            while True:
                if self.latest_jpeg:
                    conn.send(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n")
                    conn.send(self.latest_jpeg)
                    conn.send(b"\r\n")
                time.sleep(0.05)
        except:
            pass
        finally:
            self.clients.discard(conn)
            conn.close()

    def confirm_background(self, path):
        self.img_snapshot.save(path)
        self.img_snapshot = None

    def handle_command(self, msg):
        cmd = msg.get("command")
        val = msg.get("value")
        if cmd == "toggle_record":
            self.control_flags["record"] = bool(val)
        elif cmd == "toggle_raw":
            self.control_flags["show_raw"] = bool(val)
        elif cmd == "auto_update_bg":
            self.control_flags["auto_update_bg"] = bool(val)
        elif cmd == "set_background":
            self.control_flags["set_background"] = True
        elif cmd == "toggle_safe_area_display":
            self.control_flags["show_safe_areas"] = bool(val)
        elif cmd == "toggle_bed_area_display":
            self.control_flags["show_bed_areas"] = bool(val)
        elif cmd == "toggle_floor_area_display":
            self.control_flags["show_floor_areas"] = bool(val)
        elif cmd == "toggle_safety_check":
            self.control_flags["use_safety_check"] = bool(val)

    # === External API ===
    def send_frame(self, img):
        try:
            img.save(self.STREAM_JPG_PATH, quality=80)
            with open(self.STREAM_JPG_PATH, "rb") as f:
                self.latest_jpeg = f.read()
        except Exception as e:
            debug_print("WEB_SERVER", "Error saving JPEG for stream: %s", e)

    def get_control_flags(self):
        return self.control_flags

    def reset_set_background_flag(self):
        self.control_flags["set_background"] = False

    # === Main Server Loops ===
    def start_servers(self):
        # HTTP Server
        def http_loop():
            sk = socket.socket()
            sk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sk.bind(("0.0.0.0", self.HTTP_PORT))
            sk.listen(5)
            debug_print("WEB_SERVER", "[HTTP] Listening on port %d", self.HTTP_PORT)
            while True:
                conn, addr = sk.accept()
                threading.Thread(target=self.handle_http, args=(conn, addr), daemon=True).start()

        threading.Thread(target=http_loop, daemon=True).start()

# Create global instance for backward compatibility
web_server = WebServer()

