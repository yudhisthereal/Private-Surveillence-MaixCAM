# camera_manager.py - Camera and display initialization

from maix import camera, display, nn, image
from debug_config import DebugLogger

# Module-level debug logger instance
logger = DebugLogger(tag="CAM_MGR", instance_enable=False)

# Global references
cam = None
disp = None
pose_extractor = None
detector = None  # Person detection using YOLOv8


def initialize_cameras():
    """Initialize camera, display, and YOLO detectors
    
    Returns:
        cam: Camera instance for capturing frames
        disp: Display instance for showing output
        pose_extractor: YOLO11 pose detector for keypoint extraction
        detector: YOLOv8 person detector for human presence detection
    """
    global cam, disp, pose_extractor, detector
    
    # Initialize pose extractor (YOLO11 pose model for keypoint detection)
    pose_extractor = nn.YOLO11(model="/root/models/yolo11n_pose.mud", dual_buff=True)
    
    # Initialize person detector (YOLOv8 for detecting person existence)
    detector = nn.YOLO11(model="/root/models/yolo11n.mud", dual_buff=True)

    # Initialize camera (single camera, no RTMP)
    cam = camera.Camera(pose_extractor.input_width(), pose_extractor.input_height(), pose_extractor.input_format(), fps=60)
    disp = display.Display()

    logger.print("CAM_MGR", "Camera initialized: %sx%s @ %d fps", pose_extractor.input_width(), pose_extractor.input_height(), cam.fps())
    logger.print("CAM_MGR", "Pose extractor: /root/models/yolo11n_pose.mud")
    logger.print("CAM_MGR", "Person detector: /root/models/yolo11n.mud")
    
    return cam, disp, pose_extractor, detector


def load_fonts():
    """Load default fonts for image rendering"""
    image.load_font("sourcehansans", "/maixapp/share/font/SourceHanSansCN-Regular.otf", size=32)
    image.set_default_font("sourcehansans")

def get_camera():
    """Get camera instance"""
    return cam

def get_display():
    """Get display instance"""
    return disp

def get_pose_extractor():
    """Get pose extractor (YOLO11 pose for keypoint extraction)"""
    return pose_extractor

def get_detector():
    """Get person detector (YOLOv8 for human presence detection)"""
    return detector

