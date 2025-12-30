# Private Human Pose Monitoring System for MaixCAM
![maixcam](https://github.com/user-attachments/assets/25653e7d-aa29-4702-9b5c-b6a4c986b9ea)

## 📋 Overview

A comprehensive human pose monitoring system built for the MaixCAM platform that provides real-time pose estimation, fall detection, privacy protection, and remote monitoring capabilities. The system uses advanced computer vision and machine learning to track human activities while maintaining privacy through intelligent background management.

> **⚠️ Important Requirement**: You must have a **MaixCAM device** to run this application. This software is specifically designed for the MaixCAM hardware platform and will not work on other devices.

## ✨ Features

### 🎯 Core Functionality
- **Real-time Pose Estimation**: Accurate human pose detection using YOLO11 pose model
- **Multi-person Tracking**: Track multiple individuals with unique IDs using ByteTracker
- **Fall Detection**: Intelligent fall detection algorithm with visual alerts
- **Privacy Protection**: Automatic background updating that excludes human subjects
- **Remote Monitoring**: Web interface for remote viewing and control

### 🎥 Recording System
- **Smart Recording**: Automatically starts/stops based on human presence
  - Starts after 3 consecutive frames with human detection
  - Stops after 30 frames without humans or 90-second timeout
- **Dual Output**: Saves both video recordings and skeleton CSV data
- **Timestamped Files**: Organized storage with proper timestamps

### 🌐 Web Interface
- **Live Streaming**: View real-time camera feed via MJPEG streaming
- **Remote Control**: Toggle features without physical access to device
- **Configuration**: Adjust settings through intuitive web controls

### 🔧 Advanced Features
- **Background Management**: 
  - Manual background setting option
  - Automatic background updates every 10 seconds
  - Privacy-focused: only updates non-human areas
- **Pose Analysis**: Posture evaluation and status reporting
- **Multi-model Integration**: Uses both segmentation and pose detection models

## 🛠️ Installation & Setup

### Prerequisites
- **MaixCAM device** (required - will not work on other hardware)
- MicroSD card with sufficient storage
- Wi-Fi network access

### File Structure
```
/root/
├── models/
│   ├── yolo11n_pose.mud      # Pose estimation model
│   └── yolo11n_seg.mud       # Segmentation model
├── static/
│   ├── background.jpg        # Background image
│   ├── index.html           # Web interface
│   ├── script.js            # JavaScript for controls
│   └── style.css            # Stylesheet
├── recordings/              # Auto-created for video storage
├── pose/                   # Pose estimation modules
├── tools/                  # Utility functions
└── main.py                 # Main application
```

### Setup Steps

1. **Load Models**: Ensure both YOLO models are placed in `/root/models/`
2. **Configure Wi-Fi**: Edit the SSID and password in `main.py`:
   ```python
   SSID = "YOUR_WIFI_NAME"
   PASSWORD = "YOUR_WIFI_PASSWORD"
   ```
3. **Initial Background**: The system will create an initial background automatically

## 🚀 Usage

### Starting the Application
1. Power on your MaixCAM device
2. The system will automatically:
   - Connect to Wi-Fi
   - Start web servers
   - Initialize camera and models
   - Begin monitoring

### Accessing Web Interface
1. Check the console output for the device IP address
2. Open a web browser and navigate to: `http://[DEVICE_IP]:80/`
3. Use the web interface to:
   - View live feed (MJPEG stream)
   - Toggle recording
   - Adjust settings
   - Set manual background

### Control Flags
The web interface provides these control options:
- `show_raw`: Toggle between raw feed and privacy-protected view
- `record`: Manual recording control
- `auto_update_bg`: Enable/disable automatic background updates
- `set_background`: Capture and set current frame as background

## ⚙️ Configuration

### Recording Parameters
```python
MIN_HUMAN_FRAMES_TO_START = 3    # Start after 3 human frames
NO_HUMAN_FRAMES_TO_STOP = 30     # Stop after 30 no-human frames  
MAX_RECORDING_DURATION_MS = 90000  # 90-second maximum recording
```

### Background Settings
```python
UPDATE_INTERVAL_MS = 10000       # Update background every 10 seconds
NO_HUMAN_CONFIRM_FRAMES = 5      # Confirm human absence with 5 frames
STEP = 8                         # Pixel step for background updates
```

### Fall Detection Parameters
```python
fallParam = {
    "v_bbox_y": 0.43,           # Vertical threshold for fall detection
    "angle": 70                 # Angle threshold for fall detection
}
```

## 📊 Output Files

### Video Recordings
- Location: `/root/recordings/`
- Format: MP4 with timestamped filenames
- Example: `20240315_143022.mp4`

### Skeleton Data
- CSV files with detailed pose keypoints
- Includes tracking IDs and fall detection status
- Synchronized with video recordings

## 🔧 Technical Details

### Models Used
1. **YOLO11 Pose**: `yolo11n_pose.mud` - Human pose estimation
2. **YOLO11 Segmentation**: `yolo11n_seg.mud` - Human detection for privacy

### Tracking System
- **ByteTracker** algorithm for robust multi-object tracking
- Queue-based history for smooth trajectory analysis
- Fall detection using temporal analysis

### Privacy Protection
- Dynamic background updating excludes human regions
- Manual override available via web interface
- No human data stored in background images

## 🐛 Troubleshooting

### Common Issues

1. **Wi-Fi Connection Failure**
   - Verify SSID and password in code
   - Check Wi-Fi signal strength

2. **Model Loading Errors**
   - Ensure models are in `/root/models/`
   - Verify model compatibility with MaixCAM

3. **Web Interface Not Accessible**
   - Check IP address in console output
   - Verify device is connected to network

4. **Recording Not Starting**
   - Check human detection is working
   - Verify storage space on SD card

### Performance Tips
- Ensure adequate lighting for better detection
- Position camera at appropriate height for optimal coverage
- Regularly check available storage space

## 📝 License & Acknowledgments

You are free to modify and distribute this source code, even if it's commercialized, but it's encouraged that you give us some form of credit.

This project is built specifically for the MaixCAM platform using:
- MaixPy framework
- YOLO11 models optimized for MaixCAM
- Open-source computer vision techniques

> **Note**: This software is designed exclusively for MaixCAM devices and leverages hardware-specific optimizations that are not available on other platforms.

## 🔮 Potential Enhancements

Potential improvements include
- Cloud integration for remote storage
- Advanced analytics and reporting
- Multi-camera support
- Custom alert configurations
- Export functionality for analysis

---

**📞 Support**: For issues specific to MaixCAM hardware, refer to the official MaixCAM documentation and support channels.

**⚠️ Disclaimer**: Requires MaixCAM device - this software will not function on conventional computers or other embedded systems.
