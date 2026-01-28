# pc_video_record.py - PC adaptation for Video Recording
# Replaces tools.video_record.VideoRecorder using cv2.VideoWriter

import cv2
import os

class VideoRecorder:
    def __init__(self):
        self.writer = None
        self.filename = None
        self.width = None
        self.height = None
        self.is_active = False

    def start(self, filename, width, height):
        """
        Start recording into a new file.
        """
        # Ensure directories exist
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        
        self.filename = filename
        self.width = width
        self.height = height
        
        # Define codec and create VideoWriter object
        fourcc = cv2.VideoWriter_fourcc(*'mp4v') # or 'avc1' or 'XVID'
        self.writer = cv2.VideoWriter(filename, fourcc, 30.0, (width, height))
        
        self.is_active = True

    def add_frame(self, img):
        """
        Add a frame to the recording.
        """
        if self.writer is None:
            # raise RuntimeError("VideoRecorder not started. Call start() first.") 
            # Silently ignore or print error to avoid crash in loop
            return

        if img is None:
            return

        # Resize if needed (should match init size)
        if img.shape[1] != self.width or img.shape[0] != self.height:
            img = cv2.resize(img, (self.width, self.height))

        self.writer.write(img)

    def end(self):
        """
        Finish recording and clean up.
        """
        if self.writer is not None:
            self.writer.release()
            self.writer = None
            self.filename = None
            self.is_active = False
