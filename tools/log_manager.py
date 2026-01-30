# log_manager.py - Thread-safe logging queue with dedicated logging thread
# Ensures all log messages are printed atomically without interleaving

import queue
import threading
import time
import sys
from typing import Optional, Dict, Any
from datetime import datetime

# Log levels
LOG_DEBUG = 0
LOG_INFO = 1
LOG_WARNING = 2
LOG_ERROR = 3
LOG_CRITICAL = 4

LOG_LEVEL_NAMES = {
    LOG_DEBUG: "DEBUG",
    LOG_INFO: "INFO",
    LOG_WARNING: "WARN",
    LOG_ERROR: "ERROR",
    LOG_CRITICAL: "CRITICAL"
}


class LogManager:
    """
    Thread-safe logging manager with a dedicated logging thread.

    All log messages are put into a queue and processed by a single thread,
    ensuring that output is never interleaved. Worker threads never block on
    I/O - they only block briefly when putting messages into the queue.

    Usage:
        logger = LogManager.get_instance()
        logger.log("MAIN", "Camera started", LOG_INFO)
        logger.debug("MAIN", "Debug message")
        logger.info("MAIN", "Info message")
        logger.warning("MAIN", "Warning message")
        logger.error("MAIN", "Error message")
    """

    _instance: Optional['LogManager'] = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> 'LogManager':
        """Get the singleton LogManager instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        """Initialize the LogManager (use get_instance() instead)."""
        if LogManager._instance is not None:
            raise RuntimeError("Use LogManager.get_instance() to get the instance")

        # Thread-safe queue for log messages
        self._queue = queue.Queue(maxsize=1000)

        # Logging control
        self._running = True
        self._min_log_level = LOG_INFO  # Only log INFO and above by default
        self._tag_filter = None  # Optional tag filter (only show logs from this tag)

        # Statistics
        self._messages_processed = 0
        self._messages_dropped = 0

        # Start the dedicated logging thread
        self._thread = threading.Thread(target=self._logging_loop, daemon=True)
        self._thread.start()

    def _logging_loop(self):
        """Dedicated logging thread that processes the log queue."""
        while self._running:
            try:
                # Get message with timeout to allow checking _running periodically
                try:
                    record = self._queue.get(timeout=0.1)
                except queue.Empty:
                    continue

                # Print the message atomically
                self._print_record(record)

                self._messages_processed += 1

            except Exception as e:
                # Don't log here to avoid infinite loop
                print(f"[LogManager ERROR] {e}", file=sys.stderr)

    def _print_record(self, record: Dict[str, Any]):
        """Print a log record to stdout/stderr."""
        tag = record.get('tag', 'UNKNOWN')
        level = record.get('level', LOG_INFO)
        message = record.get('message', '')

        # Format: [TAG] message
        output = f"[{tag}] {message}"

        # Print to stderr for errors, stdout for everything else
        if level >= LOG_ERROR:
            print(output, file=sys.stderr, flush=True)
        else:
            print(output, file=sys.stdout, flush=True)

    def log(self, tag: str, message: str, level: int = LOG_INFO):
        """
        Log a message (thread-safe, non-blocking).

        Args:
            tag: Tag/identifier for the log source (e.g., "MAIN", "WORKER")
            message: Log message
            level: Log level (LOG_DEBUG, LOG_INFO, LOG_WARNING, LOG_ERROR, LOG_CRITICAL)
        """
        # Check log level filter
        if level < self._min_log_level:
            return

        # Check tag filter
        if self._tag_filter is not None and tag != self._tag_filter:
            return

        record = {
            'tag': tag,
            'message': message,
            'level': level,
            'timestamp': time.time()
        }

        try:
            self._queue.put_nowait(record)
        except queue.Full:
            # Queue is full, drop message
            self._messages_dropped += 1

    def debug(self, tag: str, message: str):
        """Log a debug message."""
        self.log(tag, message, LOG_DEBUG)

    def info(self, tag: str, message: str):
        """Log an info message."""
        self.log(tag, message, LOG_INFO)

    def warning(self, tag: str, message: str):
        """Log a warning message."""
        self.log(tag, message, LOG_WARNING)

    def error(self, tag: str, message: str):
        """Log an error message."""
        self.log(tag, message, LOG_ERROR)

    def critical(self, tag: str, message: str):
        """Log a critical message."""
        self.log(tag, message, LOG_CRITICAL)

    def print(self, tag: str, fmt: str, *args):
        """
        Print a formatted message (for backward compatibility with DebugLogger).

        Args:
            tag: Tag/identifier
            fmt: Format string with % placeholders
            *args: Arguments for the format string
        """
        if args:
            message = fmt % args
        else:
            message = fmt
        self.log(tag, message, LOG_INFO)

    def set_min_log_level(self, level: int):
        """Set the minimum log level (messages below this level are ignored)."""
        self._min_log_level = level

    def set_tag_filter(self, tag: Optional[str]):
        """Set a tag filter (only logs from this tag are shown, None = show all)."""
        self._tag_filter = tag

    def get_stats(self) -> Dict[str, int]:
        """Get logging statistics."""
        return {
            'processed': self._messages_processed,
            'dropped': self._messages_dropped,
            'queue_size': self._queue.qsize()
        }

    def stop(self):
        """Stop the logging thread and flush remaining messages."""
        self._running = False

        # Wait for thread to finish (with timeout)
        self._thread.join(timeout=2.0)

        # Flush remaining messages
        while not self._queue.empty():
            try:
                record = self._queue.get_nowait()
                self._print_record(record)
                self._messages_processed += 1
            except queue.Empty:
                break


# Convenience functions for quick access
def get_log_manager() -> LogManager:
    """Get the LogManager instance."""
    return LogManager.get_instance()


def log(tag: str, message: str, level: int = LOG_INFO):
    """Log a message."""
    LogManager.get_instance().log(tag, message, level)


def debug(tag: str, message: str):
    """Log a debug message."""
    LogManager.get_instance().debug(tag, message)


def info(tag: str, message: str):
    """Log an info message."""
    LogManager.get_instance().info(tag, message)


def warning(tag: str, message: str):
    """Log a warning message."""
    LogManager.get_instance().warning(tag, message)


def error(tag: str, message: str):
    """Log an error message."""
    LogManager.get_instance().error(tag, message)


def critical(tag: str, message: str):
    """Log a critical message."""
    LogManager.get_instance().critical(tag, message)
