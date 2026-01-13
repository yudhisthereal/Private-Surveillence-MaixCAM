# camera_manager.py - Camera and display initialization, RTMP streaming setup

from maix import camera, display, rtmp, nn, image

# Global references
cam = None
disp = None
detector = None
segmentor = None
rtmp_streamer = None

def initialize_cameras():
    """Initialize camera, display, and YOLO detectors"""
    global cam, disp, detector, segmentor
    
    # Initialize detectors
    # detector = nn.YOLO11(model="/root/models/yolo11n_pose.mud", dual_buff=True)
    detector = nn.YOLOv8(model="/root/models/tiny_pose.mud", dual_buff=True)
    segmentor = nn.YOLO11(model="/root/models/yolo11n_seg.mud", dual_buff=True)

    # Initialize camera
    cam = camera.Camera(detector.input_width(), detector.input_height(), detector.input_format(), fps=60)
    disp = display.Display()

    print(f"Camera initialized: {detector.input_width()}x{detector.input_height()} @ {cam.fps()} fps")
    
    return cam, disp, detector, segmentor

def setup_rtmp_stream(display_obj, camera_id):
    """Setup RTMP streaming to streaming server"""
    global rtmp_streamer
    
    from config import RTMP_SERVER_URL, STREAMING_SERVER_IP
    
    rtmp_url = f"{RTMP_SERVER_URL}/live/{camera_id}"
    
    try:
        print(f"Setting up RTMP stream to: {rtmp_url}")
        rtmp_streamer = rtmp.Rtmp(STREAMING_SERVER_IP, 1935, 'live', camera_id, 1000000)
        rtmp_streamer.bind_display(display_obj)
        rtmp_streamer.start()
        print("✅ RTMP streaming started")
        return True
    except Exception as e:
        print(f"❌ RTMP setup error: {e}")
        return False

def load_fonts():
    """Load default fonts for image rendering"""
    image.load_font("sourcehansans", "/maixapp/share/font/SourceHanSansCN-Regular.otf", size=32)
    image.set_default_font("sourcehansans")

def stop_rtmp_stream():
    """Stop RTMP streaming"""
    global rtmp_streamer
    if rtmp_streamer:
        rtmp_streamer.stop()
        rtmp_streamer = None
        print("RTMP streaming stopped")

def get_camera():
    """Get camera instance"""
    return cam

def get_display():
    """Get display instance"""
    return disp

def get_detector():
    """Get pose detector"""
    return detector

def get_segmentor():
    """Get segmentation detector"""
    return segmentor

