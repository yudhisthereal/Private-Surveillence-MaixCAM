# Debug and Performance Configuration
# Set DEBUG_ENABLED to False to disable all debug prints for production
DEBUG_ENABLED = True

# Set PERF_ENABLED to True to enable performance measurements
PERF_ENABLED = False


class DebugLogger:
    """Universal debug logger class with class-level and instance-level enable control.
    
    Class-level `enable` controls all instances - when False, all debug prints are disabled.
    Instance-level `enable` provides per-module control - when False, only that instance is disabled.
    
    Usage:
        logger = DebugLogger(tag="MODULE_NAME")
        logger.print("message")  # prints "[DEBUG MODULE_NAME] message"
        
        # With sub-tag:
        logger.print("SUB_TAG", "message")  # prints "[DEBUG MODULE_NAME:SUB_TAG] message"
    """
    
    # Class-level enable - when False, ALL instances are disabled
    _class_enable = True
    
    def __init__(self, tag="DEBUG", instance_enable=None):
        """Initialize the debug logger.
        
        Args:
            tag: The main tag to use in debug messages (e.g., "WORKERS", "CAM_MGR")
            instance_enable: Optional instance-level enable flag (overrides per-instance control)
        """
        self.tag = tag
        self._instance_enable = instance_enable
    
    @property
    def enable(self):
        """Get instance enable flag. If None, uses class-level enable."""
        if self._instance_enable is None:
            return DebugLogger._class_enable
        return self._instance_enable
    
    @enable.setter
    def enable(self, value):
        """Set instance enable flag. Use None to revert to class-level control."""
        self._instance_enable = value
    
    @classmethod
    def class_enable(cls, value=None):
        """Get or set class-level enable flag.
        
        Args:
            value: If provided, sets the class-level enable flag
            
        Returns:
            Current class-level enable flag
        """
        if value is not None:
            cls._class_enable = value
        return cls._class_enable
    
    def print(self, tag_or_message, message_or_args=None, *args):
        """Print debug messages if debugging is enabled.

        Can be called in two ways:
        - logger.print("message")  - Uses default tag
        - logger.print("SUB_TAG", "message")  - Appends sub-tag

        Args:
            tag_or_message: Either a sub-tag (when message_or_args provided) or full message
            message_or_args: Either the message format string or None
            *args: Arguments for message formatting
        """
        if not (DEBUG_ENABLED and self.enable):
            return

        # Import LogManager here to avoid circular imports
        from tools.log_manager import get_log_manager
        log_manager = get_log_manager()

        # Determine if we're using a sub-tag or just a message
        if message_or_args is not None:
            # Called with sub-tag: logger.print("SUB_TAG", "message", *args)
            sub_tag = tag_or_message
            message = message_or_args
            formatted_message = message % args if args else message
            log_manager.log(f"DEBUG {self.tag}:{sub_tag}", formatted_message)
        else:
            # Called without sub-tag: logger.print("message")
            message = tag_or_message
            formatted_message = message % args if args else message
            log_manager.log(f"DEBUG {self.tag}", formatted_message)
    
    def log_pose_data(self, pose_data, source="unknown"):
        """Log pose data for debugging.

        Args:
            pose_data: Pose data dictionary
            source: Source identifier for the log
        """
        if DEBUG_ENABLED and self.enable and pose_data:
            from tools.log_manager import get_log_manager
            log_manager = get_log_manager()

            log_manager.log(f"DEBUG POSE {source}", f"Label: {pose_data.get('raw_features', {}).get('label', 'N/A')}")
            log_manager.log(f"DEBUG POSE {source}", f"Torso angle: {pose_data.get('raw_features', {}).get('torso_angle', 'N/A')}")
            log_manager.log(f"DEBUG POSE {source}", f"Thigh uprightness: {pose_data.get('raw_features', {}).get('thigh_uprightness', 'N/A')}")

            # Check for fall detection data
            for method in ['method1', 'method2', 'method3', 'fall_detected_old', 'fall_detected_new']:
                if method in pose_data:
                    log_manager.log(f"DEBUG POSE {source}", f"{method}: {pose_data[method]}")
    
    def log_fall_detection(self, fall_detection, algorithm=3):
        """Log fall detection data for debugging.

        Args:
            fall_detection: Fall detection data dictionary
            algorithm: Algorithm number to log
        """
        if DEBUG_ENABLED and self.enable:
            from tools.log_manager import get_log_manager
            log_manager = get_log_manager()

            method_key = f"method{algorithm}"
            if fall_detection and method_key in fall_detection:
                data = fall_detection[method_key]
                log_manager.log(f"DEBUG FALL Algorithm {algorithm}", f"Detected: {data.get('detected')}, Counter: {data.get('counter')}")
    
    def perf_measure(self, func_name, duration_ms):
        """Log performance measurements if enabled.

        Args:
            func_name: Function name
            duration_ms: Duration in milliseconds
        """
        if PERF_ENABLED and self.enable and duration_ms > 5:
            from tools.log_manager import get_log_manager
            log_manager = get_log_manager()
            log_manager.log("PERF", f"{func_name}: {duration_ms:.1f}ms")

    def perf_summary(self, frame_num, total_time, breakdown):
        """Log frame performance summary.

        Args:
            frame_num: Frame number
            total_time: Total time in milliseconds
            breakdown: Dictionary of timing breakdown
        """
        if DEBUG_ENABLED and self.enable:
            from tools.log_manager import get_log_manager
            log_manager = get_log_manager()
            breakdown_str = ", ".join([f"{k}={v:.1f}ms" for k, v in breakdown.items()])
            log_manager.log("PERF", f"Frame {frame_num}: Total={total_time:.1f}ms, {breakdown_str}")


# ============================================
# Backward compatibility functions
# ============================================

def debug_print(tag, message, *args):
    """Print debug messages if debugging is enabled (backward compatibility function).

    Note: Consider using DebugLogger class for better control.
    """
    if DEBUG_ENABLED:
        from tools.log_manager import get_log_manager
        log_manager = get_log_manager()
        formatted_message = message % args if args else message
        log_manager.log(f"DEBUG {tag}", formatted_message)

def log_pose_data(pose_data, source="unknown"):
    """Log pose data for debugging (backward compatibility function)."""
    if DEBUG_ENABLED and pose_data:
        from tools.log_manager import get_log_manager
        log_manager = get_log_manager()
        log_manager.log(f"DEBUG POSE {source}", f"Label: {pose_data.get('raw_features', {}).get('label', 'N/A')}")
        log_manager.log(f"DEBUG POSE {source}", f"Torso angle: {pose_data.get('raw_features', {}).get('torso_angle', 'N/A')}")
        log_manager.log(f"DEBUG POSE {source}", f"Thigh uprightness: {pose_data.get('raw_features', {}).get('thigh_uprightness', 'N/A')}")

        # Check for fall detection data
        for method in ['method1', 'method2', 'method3', 'fall_detected_old', 'fall_detected_new']:
            if method in pose_data:
                log_manager.log(f"DEBUG POSE {source}", f"{method}: {pose_data[method]}")

def log_fall_detection(fall_detection, algorithm=3):
    """Log fall detection data for debugging (backward compatibility function)."""
    if DEBUG_ENABLED:
        from tools.log_manager import get_log_manager
        log_manager = get_log_manager()
        method_key = f"method{algorithm}"
        if fall_detection and method_key in fall_detection:
            data = fall_detection[method_key]
            log_manager.log(f"DEBUG FALL Algorithm {algorithm}", f"Detected: {data.get('detected')}, Counter: {data.get('counter')}")

def perf_measure(func_name, duration_ms):
    """Log performance measurements if enabled (backward compatibility function)."""
    if PERF_ENABLED and duration_ms > 5:
        from tools.log_manager import get_log_manager
        log_manager = get_log_manager()
        log_manager.log("PERF", f"{func_name}: {duration_ms:.1f}ms")

def perf_summary(frame_num, total_time, breakdown):
    """Log frame performance summary (backward compatibility function)."""
    if PERF_ENABLED:
        from tools.log_manager import get_log_manager
        log_manager = get_log_manager()
        breakdown_str = ", ".join([f"{k}={v:.1f}ms" for k, v in breakdown.items()])
        log_manager.log("PERF", f"Frame {frame_num}: Total={total_time:.1f}ms, {breakdown_str}")

