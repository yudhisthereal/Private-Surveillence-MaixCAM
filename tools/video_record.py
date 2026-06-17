from maix import video, time, image
import os
import json

class VideoRecorder:
    def __init__(self):
        self.encoder = None
        self.filename = None
        self.width = None
        self.height = None
        self.is_active = False

    def start(self, filename, width, height):
        """
        Start recording into a new file.
        Creates the directory if it doesn't exist.
        """
        # Create directory if it doesn't exist
        directory = os.path.dirname(filename)
        if directory and not os.path.exists(directory):
            os.makedirs(directory)
            print(f"Created directory: {directory}")
        
        self.filename = filename
        self.width = width
        self.height = height
        self.encoder = video.Encoder(filename, width, height)
        self.is_active = True

    def add_frame(self, img):
        """
        Add a frame to the recording. Converts to YVU420SP if needed.
        """
        if self.encoder is None:
            raise RuntimeError("VideoRecorder not started. Call start() first.")

        # Ensure format is YVU420SP
        if img.format() != image.Format.FMT_YVU420SP:
            img = img.to_format(format=image.Format.FMT_YVU420SP)

        self.encoder.encode(img)

    def end(self):
        """
        Finish recording and clean up.
        """
        if self.encoder is not None:
            del self.encoder
            self.encoder = None
            self.filename = None
            self.width = None
            self.height = None
            self.is_active = False


class VideoManager:
    def __init__(self, json_path="video_durations.json", max_total_duration=72*3600):  # 72 hours in seconds
        """
        Initialize the video manager.
        
        Args:
            json_path: Path to the JSON file storing video durations
            max_total_duration: Maximum total duration in seconds (default 72 hours)
        """
        self.json_path = json_path
        self.max_total_duration = max_total_duration
        self.pending_duration = 0  # Cumulative duration since last JSON update
        self.update_threshold = 30 * 60  # 30 minutes in seconds (CHANGED from 5 min)
        self.video_durations = self._load_durations()
        self.recorder = VideoRecorder()
        
    def _load_durations(self):
        """Load video durations from JSON file."""
        if os.path.exists(self.json_path):
            try:
                with open(self.json_path, 'r') as f:
                    return json.load(f)
            except:
                return {}
        return {}
    
    def _save_durations(self):
        """Save video durations to JSON file."""
        try:
            with open(self.json_path, 'w') as f:
                json.dump(self.video_durations, f)
        except Exception as e:
            print(f"Error saving durations: {e}")
    
    def _get_total_duration(self):
        """Calculate total duration of all stored videos."""
        return sum(self.video_durations.values())
    
    def _delete_oldest_video(self):
        """Delete the oldest video file and remove from tracking."""
        if not self.video_durations:
            return False
        
        # Find the oldest video (by creation time or filename)
        # Using filename sort as a proxy for age (assuming filenames are chronological)
        oldest_file = sorted(self.video_durations.keys())[0]
        
        try:
            if os.path.exists(oldest_file):
                os.remove(oldest_file)
                print(f"Deleted oldest video: {oldest_file} (duration: {self.video_durations[oldest_file]:.1f}s)")
            
            del self.video_durations[oldest_file]
            return True
        except Exception as e:
            print(f"Error deleting {oldest_file}: {e}")
            return False
    
    def _ensure_storage_limit(self):
        """Delete oldest videos until total duration is within limit."""
        while self._get_total_duration() > self.max_total_duration:
            if not self._delete_oldest_video():
                break  # Couldn't delete, something went wrong
    
    def start_recording(self, filename, width, height):
        """Start recording a new video."""
        self.recorder.start(filename, width, height)
    
    def add_frame(self, img):
        """Add a frame to the current recording."""
        self.recorder.add_frame(img)
    
    def stop_recording(self, duration_seconds):
        """
        Stop recording and update tracking.
        
        Args:
            duration_seconds: Duration of the recorded video in seconds
        """
        self.recorder.end()
        
        if self.recorder.filename:
            # Add the new video duration
            self.video_durations[self.recorder.filename] = duration_seconds
            
            # Add to pending duration
            self.pending_duration += duration_seconds
            
            # Check if we need to save to JSON (pending > 30 minutes)
            if self.pending_duration > self.update_threshold:
                self._save_durations()
                self.pending_duration = 0
                print(f"Saved durations to JSON (pending: {self.pending_duration:.1f}s)")
            
            # Check if we need to delete old videos
            self._ensure_storage_limit()
    
    def force_save(self):
        """Force save pending durations to JSON."""
        if self.pending_duration > 0:
            self._save_durations()
            self.pending_duration = 0
            print(f"Forced save to JSON")
    
    def get_total_duration(self):
        """Get total duration of all videos in seconds."""
        return self._get_total_duration()
    
    def get_video_count(self):
        """Get the number of video files tracked."""
        return len(self.video_durations)
    
    def get_storage_info(self):
        """Get storage information."""
        total = self._get_total_duration()
        hours = total / 3600
        percentage = (total / self.max_total_duration) * 100 if self.max_total_duration > 0 else 0
        return {
            'total_seconds': total,
            'total_hours': hours,
            'percentage': percentage,
            'count': len(self.video_durations),
            'pending_duration': self.pending_duration
        }