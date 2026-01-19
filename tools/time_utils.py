import time
from collections import defaultdict
from typing import Dict, List, Optional

def get_timestamp_str():
    """
    Returns a timestamp string in the format: YYYY-MM-DD_HH-MM-SS
    """
    tm = time.localtime()
    return f"{tm[0]:04d}-{tm[1]:02d}-{tm[2]:02d}_{tm[3]:02d}-{tm[4]:02d}-{tm[5]:02d}"

def time_ms():
    return round(time.time() * 1000)


class FrameProfiler:
    """
    Frame timing profiler for measuring synchronous task durations.
    Logs individual task times and total frame time to the terminal.
    """
    
    def __init__(self, print_interval: int = 30, enabled: bool = True):
        """
        Initialize the frame profiler.
        
        Args:
            print_interval: Number of frames between printing profiling summary
            enabled: Whether profiling is enabled
        """
        self.enabled = enabled
        self.print_interval = print_interval
        self.frame_count = 0
        
        # Timing storage for current frame
        self.current_frame_tasks: Dict[str, float] = {}
        self.current_task_start: Optional[str] = None
        self.frame_start_time: float = 0.0
        self.total_frame_time: float = 0.0
        
        # Historical data for averaging
        self.task_history: Dict[str, List[float]] = defaultdict(list)
        self.frame_times: List[float] = []
        
        # Task names for consistent ordering
        self.task_order = [
            "async_updates",
            "commands",
            "camera_read",
            "background_check",
            "human detect",
            "display_prep",
            "pose_extraction",
            "tracking",
            "pose_label_handling",
            "recording",
            "frame_upload"
        ]
        
        # Task display names
        self.task_names = {
            "async_updates": "Async Updates",
            "commands": "Command Processing",
            "camera_read": "Camera Read",
            "background_check": "Background Check",
            "human detect": "Human Detect",
            "display_prep": "Display Prep",
            "pose_extraction": "Pose Extraction",
            "tracking": "Tracking",
            "pose_label_handling": "Pose Label Handling",
            "recording": "Recording",
            "frame_upload": "Frame Upload"
        }
    
    def start_frame(self):
        """Start timing a new frame."""
        if not self.enabled:
            return
        
        self.frame_count += 1
        self.current_frame_tasks = {}
        self.frame_start_time = time_ms()
    
    def start_task(self, task_name: str):
        """
        Start timing a specific task.
        
        Args:
            task_name: Name of the task to time
        """
        if not self.enabled:
            return
        
        self.current_task_start = task_name
        self.current_frame_tasks[task_name] = time_ms()
    
    def end_task(self, task_name: str):
        """
        End timing a specific task.
        
        Args:
            task_name: Name of the task to stop timing
        """
        if not self.enabled:
            return
        
        end_time = time_ms()
        if task_name in self.current_frame_tasks:
            duration = end_time - self.current_frame_tasks[task_name]
            self.current_frame_tasks[task_name] = duration
    
    def end_frame(self) -> float:
        """
        End timing the frame and calculate total time.
        
        Returns:
            Total frame processing time in milliseconds
        """
        if not self.enabled:
            return 0.0
        
        self.total_frame_time = time_ms() - self.frame_start_time
        self.current_frame_tasks["total"] = self.total_frame_time
        
        # Store history for averaging
        for task, duration in self.current_frame_tasks.items():
            if task != "total":
                self.task_history[task].append(duration)
        self.frame_times.append(self.total_frame_time)
        
        # Print summary if interval reached
        if self.frame_count % self.print_interval == 0:
            self.print_summary()
        
        return self.total_frame_time
    
    def print_summary(self):
        """Print profiling summary to terminal."""
        if not self.enabled or len(self.frame_times) == 0:
            return
        
        # Calculate averages
        avg_frame_time = sum(self.frame_times) / len(self.frame_times)
        avg_fps = 1000.0 / avg_frame_time if avg_frame_time > 0 else 0
        
        print("\n" + "=" * 60)
        print(f"FRAME PROFILING SUMMARY (Frames {self.frame_count - self.print_interval + 1} to {self.frame_count})")
        print("=" * 60)
        print(f"{'Task':<25} {'Avg (ms)':<12} {'Min (ms)':<12} {'Max (ms)':<12} {'% of Frame':<12}")
        print("-" * 60)
        
        for task in self.task_order:
            if task in self.task_history:
                times = self.task_history[task]
                avg_time = sum(times) / len(times)
                min_time = min(times)
                max_time = max(times)
                percent = (avg_time / avg_frame_time * 100) if avg_frame_time > 0 else 0
                name = self.task_names.get(task, task)
                print(f"{name:<25} {avg_time:<12.2f} {min_time:<12.2f} {max_time:<12.2f} {percent:<12.1f}%")
        
        print("-" * 60)
        print(f"{'TOTAL FRAME TIME':<25} {avg_frame_time:<12.2f} {min(self.frame_times):<12.2f} {max(self.frame_times):<12.2f}")
        print(f"{'CALCULATED FPS':<25} {avg_fps:<12.2f}")
        print("=" * 60 + "\n")
        
        # Clear history after printing
        self.task_history.clear()
        self.frame_times.clear()
    
    def get_last_frame_times(self) -> Dict[str, float]:
        """Get timing results from the last frame."""
        return self.current_frame_tasks.copy()
    
    def set_enabled(self, enabled: bool):
        """Enable or disable profiling."""
        self.enabled = enabled
    
    def set_print_interval(self, interval: int):
        """Set how often to print profiling summary."""
        self.print_interval = interval
