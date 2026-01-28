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


class TaskProfiler:
    """
    Task timing profiler for measuring synchronous subtask durations.
    Logs individual subtask times and total cycle time to the terminal.
    """
    
    def __init__(self, task_name: str = "Task", print_interval: int = 30, enabled: bool = True):
        """
        Initialize the task profiler.
        
        Args:
            task_name: Name of the high-level task being profiled
            print_interval: Number of cycles between printing profiling summary
            enabled: Whether profiling is enabled
        """
        self.task_name = task_name
        self.enabled = enabled
        self.print_interval = print_interval
        self.cycle_count = 0
        
        # Timing storage for current cycle
        self.current_cycle_tasks: Dict[str, float] = {}
        self.current_task_start: Optional[str] = None
        self.cycle_start_time: float = 0.0
        self.total_cycle_time: float = 0.0
        
        # Historical data for averaging
        self.task_history: Dict[str, List[float]] = defaultdict(list)
        self.cycle_times: List[float] = []
        
        # Registered Subtasks
        self.registered_subtasks: Set[str] = set()
        self.subtask_order: List[str] = [] # To maintain order
        self.subtask_display_names: Dict[str, str] = {} # Optional friendly names
    
    def register_subtasks(self, subtasks: List[str]):
        """
        Register a list of subtasks that will be profiled.
        Must be called before start_task is used for any subtask.
        
        Args:
            subtasks: List of subtask names (identifiers)
        """
        for task in subtasks:
            if task not in self.registered_subtasks:
                self.registered_subtasks.add(task)
                self.subtask_order.append(task)
                # Default display name is same as key, usually explicit mapping isn't passed here
                # We could add a way to map names if needed, but for now just use the key or title case
                self.subtask_display_names[task] = task.replace("_", " ").title()

    def start_frame(self):
        """Start timing a new cycle/frame (alias for start_cycle)."""
        self.start_cycle()

    def end_frame(self) -> float:
        """End timing the cycle/frame (alias for end_cycle)."""
        return self.end_cycle()

    def start_cycle(self):
        """Start timing a new cycle."""
        if not self.enabled:
            return
        
        self.cycle_count += 1
        self.current_cycle_tasks = {}
        self.cycle_start_time = time_ms()
    
    def start_task(self, task_name: str):
        """
        Start timing a specific subtask.
        
        Args:
            task_name: Name of the subtask to time
        
        Raises:
            ValueError: If task_name is not registered
        """
        if not self.enabled:
            return
            
        if task_name not in self.registered_subtasks:
            # We raise exception as requested to enforce registration
            raise ValueError(f"TaskProfiler '{self.task_name}': Subtask '{task_name}' is not registered. Call register_subtasks() first.")
        
        self.current_task_start = task_name
        self.current_cycle_tasks[task_name] = time_ms()
    
    def end_task(self, task_name: str):
        """
        End timing a specific subtask.
        
        Args:
            task_name: Name of the subtask to stop timing
        """
        if not self.enabled:
            return
        
        end_time = time_ms()
        if task_name in self.current_cycle_tasks:
            start_t = self.current_cycle_tasks[task_name]
            # Verify we are indeed ending the stored start time, not overwriting valid duration
            # (In this simple implementation, we just subtract)
            duration = end_time - start_t
            self.current_cycle_tasks[task_name] = duration
    
    def end_cycle(self) -> float:
        """
        End timing the cycle and calculate total time.
        
        Returns:
            Total cycle processing time in milliseconds
        """
        if not self.enabled:
            return 0.0
        
        self.total_cycle_time = time_ms() - self.cycle_start_time
        self.current_cycle_tasks["total"] = self.total_cycle_time
        
        # Store history for averaging
        for task, duration in self.current_cycle_tasks.items():
            if task != "total":
                self.task_history[task].append(duration)
        self.cycle_times.append(self.total_cycle_time)
        
        # Print summary if interval reached
        if self.cycle_count % self.print_interval == 0:
            self.print_summary()
        
        return self.total_cycle_time
    
    def print_summary(self):
        """Print profiling summary to terminal."""
        if not self.enabled or len(self.cycle_times) == 0:
            return
        
        # Calculate averages
        avg_cycle_time = sum(self.cycle_times) / len(self.cycle_times)
        avg_rate = 1000.0 / avg_cycle_time if avg_cycle_time > 0 else 0
        
        print("\n" + "=" * 60)
        print(f"PROFILING SUMMARY: {self.task_name} (Cycles {self.cycle_count - self.print_interval + 1} to {self.cycle_count})")
        print("=" * 60)
        print(f"{'Subtask':<25} {'Avg (ms)':<12} {'Min (ms)':<12} {'Max (ms)':<12} {'% of Cycle':<12}")
        print("-" * 60)
        
        # Iterate over registered order
        for task in self.subtask_order:
            if task in self.task_history:
                times = self.task_history[task]
                if not times:
                    continue
                avg_time = sum(times) / len(times)
                min_time = min(times)
                max_time = max(times)
                percent = (avg_time / avg_cycle_time * 100) if avg_cycle_time > 0 else 0
                name = self.subtask_display_names.get(task, task)
                print(f"{name:<25} {avg_time:<12.2f} {min_time:<12.2f} {max_time:<12.2f} {percent:<12.1f}%")
        
        print("-" * 60)
        print(f"{'TOTAL CYCLE TIME':<25} {avg_cycle_time:<12.2f} {min(self.cycle_times):<12.2f} {max(self.cycle_times):<12.2f}")
        print(f"{'CALCULATED RATE':<25} {avg_rate:<12.2f} Hz")
        print("=" * 60 + "\n")
        
        # Clear history after printing
        self.task_history.clear()
        self.cycle_times.clear()
    
    def get_last_cycle_times(self) -> Dict[str, float]:
        """Get timing results from the last cycle."""
        return self.current_cycle_tasks.copy()
    
    def set_enabled(self, enabled: bool):
        """Enable or disable profiling."""
        self.enabled = enabled
    
    def set_print_interval(self, interval: int):
        """Set how often to print profiling summary."""
        self.print_interval = interval
