# pc_camera_manager.py - PC adaptation for Camera and display initialization
# Wraps Ultralytics YOLO and OpenCV VideoCapture to mimic MaixCAM interfaces

import cv2
import numpy as np
import os
import time

try:
    from ultralytics import YOLO
except ImportError:
    YOLO = None

import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

from debug_config import DebugLogger

# Module-level debug logger instance
logger = DebugLogger(tag="CAM_MGR", instance_enable=False)

# Global references
cam = None
disp = None
pose_extractor = None
detector = None

class Object:
    """Mimic MaixPy object structure"""
    def __init__(self, x, y, w, h, class_id, score, points=None):
        self.x = int(x)
        self.y = int(y)
        self.w = int(w)
        self.h = int(h)
        self.class_id = int(class_id)
        self.score = float(score)
        self.points = points or [] # Flattened keypoints [x1, y1, x2, y2, ...]

class MediaPipePose:
    """Wrapper for MediaPipe Pose Landmarker (Multi-person support)"""
    def __init__(self, model_path="pose_landmarker_heavy.task", num_poses=3):
        self.model_path = model_path
        self.num_poses = num_poses
        self._input_width = 320
        self._input_height = 224
        
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"MediaPipe model not found: {model_path}")

        # Initialize MediaPipe Pose Landmarker
        base_options = python.BaseOptions(model_asset_path=model_path)
        options = vision.PoseLandmarkerOptions(
            base_options=base_options,
            output_segmentation_masks=False,
            num_poses=num_poses,
            min_pose_detection_confidence=0.5,
            min_pose_presence_confidence=0.5,
            min_tracking_confidence=0.5
        )
        self.landmarker = vision.PoseLandmarker.create_from_options(options)
        logger.print("CAM_MGR", f"MediaPipe Pose initialized (max_poses={num_poses})")

    def input_width(self):
        return 320

    def input_height(self):
        return 224

    def input_format(self):
        return "RGB"

    def detect(self, img, conf_th=0.5, iou_th=0.45, keypoint_th=0.5):
        """
        Detect poses in the image.
        Args:
            img: Input image (BGR from OpenCV)
        Returns:
            list of Object (mimicking MaixPy format)
        """
        # Convert BGR to RGB
        rgb_image = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_image)
        
        # Run inference
        results = self.landmarker.detect(mp_image)
        
        objs = []
        if results.pose_landmarks:
            for i, landmarks in enumerate(results.pose_landmarks):
                # Calculate bounding box from landmarks
                x_min, y_min = 1.0, 1.0
                x_max, y_max = 0.0, 0.0
                
                points_flat = []
                h, w = img.shape[:2]
                
                # MediaPipe landmarks are normalized [0,1]
                # We need to map them to COCO format if possible, or just use 33 MP landmarks?
                # The rest of the system expects COCO 17 keypoints.
                # MediaPipe has 33 landmarks. We need to map them.
                # COCO 17 keypoints:
                # 0: nose, 1: left_eye, 2: right_eye, 3: left_ear, 4: right_ear
                # 5: left_shoulder, 6: right_shoulder, 7: left_elbow, 8: right_elbow
                # 9: left_wrist, 10: right_wrist, 11: left_hip, 12: right_hip
                # 13: left_knee, 14: right_knee, 15: left_ankle, 16: right_ankle
                
                # MediaPipe Mapping (approximate):
                # 0: nose
                # 2: left_eye (MP has inner/outer, 2 is reasonable avg or use 5) -> MP 2 is left eye
                # 5: right_eye -> MP 5 is right eye
                # 7: left_ear -> MP 7
                # 8: right_ear -> MP 8
                # 11: left_shoulder -> MP 11
                # 12: right_shoulder -> MP 12
                # 13: left_elbow -> MP 13
                # 14: right_elbow -> MP 14
                # 15: left_wrist -> MP 15
                # 16: right_wrist -> MP 16
                # 23: left_hip -> MP 23
                # 24: right_hip -> MP 24
                # 25: left_knee -> MP 25
                # 26: right_knee -> MP 26
                # 27: left_ankle -> MP 27
                # 28: right_ankle -> MP 28
                
                mp_to_coco = [0, 2, 5, 7, 8, 11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28]
                
                # Extract COCO keypoints
                coco_kpts = []
                for mp_idx in mp_to_coco:
                    try:
                        lm = landmarks[mp_idx]
                        px, py = int(lm.x * w), int(lm.y * h)
                        points_flat.extend([px, py])
                        coco_kpts.append((px, py))
                        
                        # Update Bbox
                        x_min = min(x_min, lm.x)
                        y_min = min(y_min, lm.y)
                        x_max = max(x_max, lm.x)
                        y_max = max(y_max, lm.y)
                    except IndexError:
                        points_flat.extend([0, 0])

                # Bounding Box
                bx = int(x_min * w)
                by = int(y_min * h)
                bw = int((x_max - x_min) * w)
                bh = int((y_max - y_min) * h)
                
                # Ensure valid bbox
                bx = max(0, bx)
                by = max(0, by)
                
                score = 0.9 # Placeholder, MP doesn't give single score per pose
                
                objs.append(Object(bx, by, bw, bh, 0, score, points_flat))
        
        logger.print("CAM_MGR", f"MediaPipe detected {len(objs)} objects")
        if len(objs) > 0:
             logger.print("CAM_MGR", f"First Obj BBox: {objs[0].x}, {objs[0].y}, {objs[0].w}, {objs[0].h}")
        return objs

class YOLO11_Pose:
    """Wrapper for Ultralytics YOLO pose model"""
    def __init__(self, model_path="yolo11n-pose.pt"):
        if YOLO is None:
             raise ImportError("Ultralytics not installed")
        self.model = YOLO(model_path)
        self._input_width = 320
        self._input_height = 224

    def input_width(self):
        return 320

    def input_height(self):
        return 224

    def input_format(self):
        return "RGB"

    def detect(self, img, conf_th=0.5, iou_th=0.45, keypoint_th=0.5):
        # Ultralytics expects RGB usually, but works with BGR from cv2
        results = self.model(img, conf=conf_th, iou=iou_th, verbose=False)
        objs = []
        if len(results) > 0:
            result = results[0]
            boxes = result.boxes
            keypoints = result.keypoints
            
            if boxes is not None:
                for i, box in enumerate(boxes):
                    # xywh format
                    x, y, w, h = box.xywh[0].tolist()
                    score = box.conf[0].item()
                    cls = box.cls[0].item()
                    
                    # Keypoints
                    points_flat = []
                    if keypoints is not None and len(keypoints) > i:
                        # shape (17, 2) or (17, 3)
                        kpts = keypoints[i].xy[0].tolist()
                        # Flatten: [x1, y1, x2, y2, ...]
                        for kp in kpts:
                            points_flat.extend([kp[0], kp[1]])
                            
                    objs.append(Object(x, y, w, h, cls, score, points_flat))
        return objs

class YOLO11_Detect:
    """Wrapper for Ultralytics YOLO detection model"""
    def __init__(self, model_path="yolo11n.pt"):
        if YOLO is None:
             raise ImportError("Ultralytics not installed")
        self.model = YOLO(model_path)
        self.labels = self.model.names

    def detect(self, img, conf_th=0.5, iou_th=0.45):
        results = self.model(img, conf=conf_th, iou=iou_th, verbose=False)
        objs = []
        if len(results) > 0:
            for box in results[0].boxes:
                x, y, w, h = box.xywh[0].tolist()
                score = box.conf[0].item()
                cls = box.cls[0].item()
                objs.append(Object(x, y, w, h, cls, score))
        return objs

class Camera:
    """Wrapper for OpenCV VideoCapture"""
    def __init__(self, width=640, height=480, fps=30):
        self.cap = cv2.VideoCapture(0) # Default webcam
        if not self.cap.isOpened():
            logger.print("CAM_MGR", "Error: Could not open webcam.")
        
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, fps)
        
        self._fps = fps
        self._width = 320  # Force report 320
        self._height = 224 # Force report 224

    def read(self):
        ret, frame = self.cap.read()
        if not ret:
            # Return blank image if read fails to prevent crash
            return np.zeros((self._height, self._width, 3), dtype=np.uint8)
        
        # Resize to have height = 224, preserving aspect ratio
        h, w = frame.shape[:2]
        target_h = 224
        scale = target_h / h
        new_w = int(w * scale)
        resized = cv2.resize(frame, (new_w, target_h))
        
        # Center crop to width = 320
        target_w = 320
        if new_w > target_w:
            start_x = (new_w - target_w) // 2
            cropped = resized[:, start_x:start_x+target_w]
        else:
            # Pad to 320 if smaller
            pad_w = target_w - new_w
            left = pad_w // 2
            right = pad_w - left
            cropped = cv2.copyMakeBorder(resized, 0, 0, left, right, cv2.BORDER_CONSTANT, value=[0,0,0])
            
        return cropped

    def fps(self):
        return self._fps
    
    def width(self):
        return self._width
    
    def height(self):
        return self._height

class Display:
    """Wrapper for OpenCV imshow"""
    def __init__(self, title="Private CCTV"):
        self.title = title

    def show(self, img):
        if img is not None:
            cv2.imshow(self.title, img)

def initialize_cameras():
    """Initialize camera, display, and detectors for PC
    
    Returns:
        cam: Camera instance for capturing frames
        disp: Display instance for showing output
        pose_extractor: Pose detector (MediaPipe or YOLO)
        detector: Person detector (YOLO)
    """
    global cam, disp, pose_extractor, detector
    
    # Initialize wrapper classes
    # Switch to MediaPipePose as requested
    pose_extractor = MediaPipePose(model_path="pose_landmarker_heavy.task", num_poses=3)
    
    # Still use YOLO for general object/person detection if needed (or we could use MP? But code expects separate detector)
    detector = YOLO11_Detect("yolo11n.pt")
    
    cam = Camera(width=pose_extractor.input_width(), height=pose_extractor.input_height(), fps=30)
    disp = Display()

    logger.print("CAM_MGR", "PC Camera initialized with MediaPipe Pose")
    
    return cam, disp, pose_extractor, detector

def load_fonts():
    """Mock load_fonts"""
    pass

def get_camera():
    return cam

def get_display():
    return disp

def get_pose_extractor():
    return pose_extractor

def get_detector():
    return detector
