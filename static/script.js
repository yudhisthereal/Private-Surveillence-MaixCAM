// script.js - Analytics Dashboard with auto-refreshing stream

// DOM Elements
const streamImg = document.getElementById('stream');
const toggleRecord = document.getElementById('toggleRecord');
const toggleRaw = document.getElementById('toggleRaw');
const autoUpdateBg = document.getElementById('autoUpdateBg');
const showSafeArea = document.getElementById('showSafeArea');
const useSafetyCheck = document.getElementById('useSafetyCheck');
const setBackgroundBtn = document.getElementById('setBackgroundBtn');
const editSafeAreaBtn = document.getElementById('editSafeAreaBtn');
const refreshCamerasBtn = document.getElementById('refreshCamerasBtn');

// Popup elements
const popup = document.getElementById('popup');
const preview = document.getElementById('preview');
const safeAreaPopup = document.getElementById('safeAreaPopup');

// Safe Area Editor Elements
const safeAreaCanvas = document.getElementById('safeAreaCanvas');
const newPolygonBtn = document.getElementById('newPolygonBtn');
const clearAllBtn = document.getElementById('clearAllBtn');
const saveSafeAreasBtn = document.getElementById('saveSafeAreasBtn');
const saveStatus = document.getElementById('saveStatus');

// Safe Area Editor State
let safeAreas = [];
let currentPolygon = [];
let isEditing = false;
let canvasContext = null;
let backgroundImage = null;
let originalImageWidth = 0;
let originalImageHeight = 0;
let canvasScale = 1;

// Camera selection
let currentCameraId = "maixcam_001";

// Analytics server URL (current server)
let ANALYTICS_HTTP_URL = window.location.origin;

// Stream state
let streamRefreshInterval = null;
const REFRESH_INTERVAL_MS = 200; // 5 FPS
let errorCount = 0;
const MAX_ERRORS = 10;

// Connection state
let isConnected = false;
let lastUpdateTime = null;
let cameraStateTimer = null;
let cameraListTimer = null;

// ============================================
// STREAM FUNCTIONS - SIMPLE AUTO-REFRESH
// ============================================

function createStatusIndicator() {
    const status = document.createElement('div');
    status.id = 'stream-status';
    status.style.cssText = `
        position: fixed;
        top: 10px;
        right: 10px;
        padding: 5px 10px;
        border-radius: 3px;
        font-size: 12px;
        z-index: 1000;
        background: #4CAF50;
        color: white;
        font-weight: bold;
    `;
    status.textContent = `Stream: ${currentCameraId}`;
    document.body.appendChild(status);
    return status;
}

const statusIndicator = createStatusIndicator();

function startStream() {
    stopStream(); // Clear any existing refresh
    
    if (streamImg) {
        console.log(`Starting auto-refresh stream for ${currentCameraId} at ${REFRESH_INTERVAL_MS}ms interval`);
        
        // Initial load
        refreshStreamImage();
        
        // Set up auto-refresh
        streamRefreshInterval = setInterval(refreshStreamImage, REFRESH_INTERVAL_MS);
        
        updateStatus(`Active (${currentCameraId})`, 'green');
    }
}

function stopStream() {
    if (streamRefreshInterval) {
        clearInterval(streamRefreshInterval);
        streamRefreshInterval = null;
    }
    if (streamImg) {
        streamImg.src = '';
    }
}

function refreshStreamImage() {
    if (!streamImg) return;
    
    // Add timestamp to prevent caching
    const timestamp = Date.now();
    const streamUrl = `${ANALYTICS_HTTP_URL}/stream.jpg?camera_id=${currentCameraId}&t=${timestamp}`;
    
    // Update last update time
    lastUpdateTime = new Date();
    updateLastUpdateDisplay();
    
    // Set image source
    streamImg.src = streamUrl;
    
    // Update status on successful load
    streamImg.onload = function() {
        errorCount = 0;
        isConnected = true;
        updateStatus(`Active (${currentCameraId})`, 'green');
    };
    
    // Handle errors
    streamImg.onerror = function() {
        errorCount++;
        console.error(`Stream error ${errorCount}/${MAX_ERRORS} for ${currentCameraId}`);
        updateStatus(`Error ${errorCount}/${MAX_ERRORS} (${currentCameraId})`, 'red');
        
        if (errorCount >= MAX_ERRORS) {
            console.error('Too many stream errors, trying to recover...');
            errorCount = 0;
            // Try to reload camera list and reconnect
            loadCameraList();
        }
    };
}

function updateStatus(text, color) {
    const colors = {
        'green': '#4CAF50',
        'yellow': '#ff9800',
        'red': '#ff4444',
        'gray': '#777'
    };
    if (statusIndicator) {
        statusIndicator.textContent = `Stream: ${text}`;
        statusIndicator.style.background = colors[color] || colors.gray;
    }
}

function updateLastUpdateDisplay() {
    const lastUpdateElement = document.getElementById('lastUpdate');
    if (lastUpdateElement && lastUpdateTime) {
        const now = new Date();
        const diff = Math.floor((now - lastUpdateTime) / 1000);
        
        if (diff < 60) {
            lastUpdateElement.textContent = `${diff} seconds ago`;
            lastUpdateElement.style.color = diff < 5 ? '#4CAF50' : '#ff9800';
        } else {
            lastUpdateElement.textContent = lastUpdateTime.toLocaleTimeString();
            lastUpdateElement.style.color = '#777';
        }
    }
}

// ============================================
// CAMERA MANAGEMENT
// ============================================

async function loadCameraList() {
    try {
        const infoSpan = document.getElementById('camera-info');
        if (infoSpan) infoSpan.textContent = 'Loading cameras...';
        
        const response = await fetch(`${ANALYTICS_HTTP_URL}/camera_list`, {
            method: 'GET',
            headers: {
                'Accept': 'application/json'
            }
        });
        
        if (response.ok) {
            const data = await response.json();
            updateCameraSelect(data.cameras);
            
            if (infoSpan) {
                infoSpan.textContent = `${data.count} camera(s) online`;
                infoSpan.style.color = data.count > 0 ? '#4CAF50' : '#ff4444';
            }
            
            // Update camera status
            const cameraStatus = document.getElementById('cameraStatus');
            if (cameraStatus) {
                cameraStatus.textContent = data.count > 0 ? 'Connected' : 'No cameras';
                cameraStatus.style.color = data.count > 0 ? '#4CAF50' : '#ff4444';
            }
            
        } else {
            throw new Error(`HTTP ${response.status}`);
        }
    } catch (error) {
        console.error('Failed to load camera list:', error);
        const infoSpan = document.getElementById('camera-info');
        if (infoSpan) {
            infoSpan.textContent = 'Connection error';
            infoSpan.style.color = '#ff4444';
        }
        
        const cameraStatus = document.getElementById('cameraStatus');
        if (cameraStatus) {
            cameraStatus.textContent = 'Disconnected';
            cameraStatus.style.color = '#ff4444';
        }
    }
}

function updateCameraSelect(cameras) {
    const select = document.getElementById('cameraSelect');
    if (!select) return;
    
    const currentValue = select.value;
    
    // Clear and add placeholder
    select.innerHTML = '<option value="" disabled>Select a camera</option>';
    
    if (!cameras || cameras.length === 0) {
        const option = document.createElement('option');
        option.value = "maixcam_001";
        option.textContent = "Camera 1 (offline)";
        select.appendChild(option);
        select.value = "maixcam_001";
        return;
    }
    
    // Add active cameras
    cameras.forEach(cam => {
        const option = document.createElement('option');
        option.value = cam.camera_id;
        
        const timeAgo = Math.round((Date.now()/1000 - cam.last_seen));
        const status = cam.online ? '✓' : '✗';
        option.textContent = `${cam.camera_id} ${status} (${timeAgo}s ago)`;
        
        select.appendChild(option);
    });
    
    // Keep current selection if possible
    if (currentValue && cameras.some(cam => cam.camera_id === currentValue)) {
        select.value = currentValue;
    } else if (cameras.length > 0) {
        select.value = cameras[0].camera_id;
        currentCameraId = cameras[0].camera_id;
        updateStatus(`Active (${currentCameraId})`, 'green');
    }
}

// ============================================
// CAMERA STATE & COMMANDS
// ============================================

async function fetchCameraState(cameraId) {
    try {
        const response = await fetch(`${ANALYTICS_HTTP_URL}/camera_state?camera_id=${cameraId}`, {
            method: 'GET',
            headers: {
                'Accept': 'application/json'
            }
        });
        
        if (response.ok) {
            const flags = await response.json();
            updateUIControls(flags);
            return flags;
        }
    } catch (error) {
        console.error(`Failed to fetch state for ${cameraId}:`, error);
    }
    return null;
}

function updateUIControls(flags) {
    if (!flags) return;
    
    // Update checkboxes only for actual control flags
    if (typeof flags.record === 'boolean') {
        toggleRecord.checked = flags.record;
    }
    if (typeof flags.show_raw === 'boolean') {
        toggleRaw.checked = flags.show_raw;
    }
    if (typeof flags.auto_update_bg === 'boolean') {
        autoUpdateBg.checked = flags.auto_update_bg;
    }
    if (typeof flags.show_safe_area === 'boolean') {
        showSafeArea.checked = flags.show_safe_area;
    }
    if (typeof flags.use_safety_check === 'boolean') {
        useSafetyCheck.checked = flags.use_safety_check;
    }
}

function sendCommand(command, value = null) {
    console.log(`Sending command to ${currentCameraId}: ${command}=${value}`);
    
    fetch(`${ANALYTICS_HTTP_URL}/command`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            camera_id: currentCameraId,
            command: command,
            value: value
        })
    })
    .then(response => {
        if (response.ok) {
            console.log(`Command sent successfully`);
            // Update UI after a short delay
            setTimeout(() => fetchCameraState(currentCameraId), 300);
        } else {
            console.error(`Command failed: HTTP ${response.status}`);
            updateStatus(`Command failed (${currentCameraId})`, 'yellow');
            setTimeout(() => updateStatus(`Active (${currentCameraId})`, 'green'), 2000);
        }
    })
    .catch(error => {
        console.error('Command error:', error);
        updateStatus(`Network error (${currentCameraId})`, 'red');
        setTimeout(() => updateStatus(`Active (${currentCameraId})`, 'green'), 2000);
    });
}

// ============================================
// EVENT HANDLERS
// ============================================

// Control button handlers
if (toggleRecord) {
    toggleRecord.onchange = () => {
        sendCommand("toggle_record", toggleRecord.checked);
    };
}

if (toggleRaw) {
    toggleRaw.onchange = () => {
        sendCommand("toggle_raw", toggleRaw.checked);
    };
}

if (autoUpdateBg) {
    autoUpdateBg.onchange = () => {
        sendCommand("auto_update_bg", autoUpdateBg.checked);
    };
}

if (showSafeArea) {
    showSafeArea.onchange = () => {
        sendCommand("toggle_safe_area_display", showSafeArea.checked);
    };
}

if (useSafetyCheck) {
    useSafetyCheck.onchange = () => {
        sendCommand("toggle_safety_check", useSafetyCheck.checked);
    };
}

if (setBackgroundBtn) {
    setBackgroundBtn.onclick = () => {
        if (preview && popup) {
            preview.src = `${ANALYTICS_HTTP_URL}/snapshot.jpg?camera_id=${currentCameraId}&t=${Date.now()}`;
            popup.style.display = "block";
        }
    };
}

if (editSafeAreaBtn) {
    editSafeAreaBtn.onclick = () => {
        showSafeAreaEditor();
    };
}

if (refreshCamerasBtn) {
    refreshCamerasBtn.onclick = loadCameraList;
}

const cameraSelect = document.getElementById('cameraSelect');
if (cameraSelect) {
    cameraSelect.onchange = () => {
        currentCameraId = cameraSelect.value;
        console.log(`Switched to camera: ${currentCameraId}`);
        
        // Restart stream with new camera
        startStream();
        
        // Load new camera's state
        fetchCameraState(currentCameraId);
        
        // Update safe areas for new camera
        loadSafeAreasForCamera(currentCameraId);
    };
}

// ============================================
// SAFE AREA EDITOR
// ============================================

async function loadSafeAreasForCamera(cameraId) {
    try {
        const response = await fetch(`${ANALYTICS_HTTP_URL}/get_safe_areas?camera_id=${cameraId}`, {
            method: 'GET',
            headers: {
                'Accept': 'application/json'
            }
        });
        if (response.ok) {
            safeAreas = await response.json();
            console.log(`Loaded ${safeAreas.length} safe areas for ${cameraId}`);
        }
    } catch (error) {
        console.error(`Failed to load safe areas for ${cameraId}:`, error);
        safeAreas = [];
    }
}

async function showSafeAreaEditor() {
    try {
        // Load current safe areas
        await loadSafeAreasForCamera(currentCameraId);
        
        // Load background image
        backgroundImage = new Image();
        backgroundImage.onload = function() {
            initializeCanvas();
            safeAreaPopup.style.display = "block";
            isEditing = true;
            drawSafeAreas();
        };
        backgroundImage.onerror = function() {
            alert('Failed to load background image');
        };
        backgroundImage.src = `${ANALYTICS_HTTP_URL}/snapshot.jpg?camera_id=${currentCameraId}&t=${Date.now()}`;
        
    } catch (error) {
        console.error('Error showing safe area editor:', error);
        alert('Failed to open safe area editor');
    }
}

function initializeCanvas() {
    if (!backgroundImage) return;
    
    originalImageWidth = backgroundImage.width;
    originalImageHeight = backgroundImage.height;
    
    // Set canvas size
    safeAreaCanvas.width = originalImageWidth;
    safeAreaCanvas.height = originalImageHeight;
    
    // Calculate display scale
    const maxWidth = 800;
    const maxHeight = 600;
    const scaleX = maxWidth / originalImageWidth;
    const scaleY = maxHeight / originalImageHeight;
    canvasScale = Math.min(scaleX, scaleY);
    
    safeAreaCanvas.style.width = (originalImageWidth * canvasScale) + 'px';
    safeAreaCanvas.style.height = (originalImageHeight * canvasScale) + 'px';
    
    canvasContext = safeAreaCanvas.getContext('2d');
    
    // Add event listeners
    safeAreaCanvas.addEventListener('click', handleCanvasClick);
    safeAreaCanvas.addEventListener('mousemove', handleCanvasMouseMove);
    safeAreaCanvas.addEventListener('contextmenu', handleCanvasRightClick);
    
    // Toolbar listeners
    if (newPolygonBtn) newPolygonBtn.onclick = startNewPolygon;
    if (clearAllBtn) clearAllBtn.onclick = clearAllPolygons;
    if (saveSafeAreasBtn) saveSafeAreasBtn.onclick = saveSafeAreas;
}

function getCanvasCoordinates(event) {
    const rect = safeAreaCanvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    
    // Convert to original image coordinates
    return {
        x: Math.floor(x / canvasScale),
        y: Math.floor(y / canvasScale)
    };
}

function handleCanvasClick(event) {
    if (!isEditing) return;
    
    const { x, y } = getCanvasCoordinates(event);
    const normalizedX = x / originalImageWidth;
    const normalizedY = y / originalImageHeight;
    
    // Check if closing polygon
    if (currentPolygon.length >= 3) {
        const firstPoint = currentPolygon[0];
        const distance = Math.sqrt(
            Math.pow(normalizedX - firstPoint[0], 2) + 
            Math.pow(normalizedY - firstPoint[1], 2)
        );
        
        if (distance < 0.05) {
            finishCurrentPolygon();
            return;
        }
    }
    
    // Add new point
    currentPolygon.push([normalizedX, normalizedY]);
    drawSafeAreas();
}

function handleCanvasMouseMove(event) {
    if (!isEditing || currentPolygon.length === 0) return;
    
    const { x, y } = getCanvasCoordinates(event);
    const normalizedX = x / originalImageWidth;
    const normalizedY = y / originalImageHeight;
    
    drawSafeAreas([...currentPolygon, [normalizedX, normalizedY]]);
}

function handleCanvasRightClick(event) {
    event.preventDefault();
    if (!isEditing || currentPolygon.length === 0) return;
    
    currentPolygon.pop();
    drawSafeAreas();
}

function startNewPolygon() {
    if (currentPolygon.length >= 3) {
        finishCurrentPolygon();
    }
    currentPolygon = [];
    drawSafeAreas();
}

function finishCurrentPolygon() {
    if (currentPolygon.length >= 3) {
        safeAreas.push([...currentPolygon]);
        currentPolygon = [];
        drawSafeAreas();
    }
}

function clearAllPolygons() {
    if (confirm("Clear all safe areas?")) {
        safeAreas = [];
        currentPolygon = [];
        drawSafeAreas();
    }
}

function drawSafeAreas(tempPolygon = null) {
    if (!canvasContext || !backgroundImage) return;
    
    // Clear canvas
    canvasContext.clearRect(0, 0, originalImageWidth, originalImageHeight);
    
    // Draw background
    canvasContext.drawImage(backgroundImage, 0, 0, originalImageWidth, originalImageHeight);
    
    // Draw existing polygons
    safeAreas.forEach((polygon, index) => {
        drawPolygon(polygon, `hsl(${index * 60}, 70%, 50%)`, true);
    });
    
    // Draw current polygon
    const polygonToDraw = tempPolygon || currentPolygon;
    if (polygonToDraw.length > 0) {
        drawPolygon(polygonToDraw, 'cyan', false);
    }
}

function drawPolygon(polygon, color, isComplete) {
    if (polygon.length === 0) return;
    
    canvasContext.strokeStyle = color;
    canvasContext.fillStyle = color + '40';
    canvasContext.lineWidth = 2;
    canvasContext.setLineDash(isComplete ? [] : [5, 5]);
    
    // Convert normalized to pixel coordinates
    const points = polygon.map(p => [
        p[0] * originalImageWidth,
        p[1] * originalImageHeight
    ]);
    
    // Draw polygon
    canvasContext.beginPath();
    canvasContext.moveTo(points[0][0], points[0][1]);
    for (let i = 1; i < points.length; i++) {
        canvasContext.lineTo(points[i][0], points[i][1]);
    }
    
    if (isComplete && points.length >= 3) {
        canvasContext.closePath();
        canvasContext.fill();
    }
    
    canvasContext.stroke();
    canvasContext.setLineDash([]);
    
    // Draw points
    points.forEach((point, index) => {
        canvasContext.fillStyle = color;
        canvasContext.beginPath();
        canvasContext.arc(point[0], point[1], 4, 0, Math.PI * 2);
        canvasContext.fill();
        
        // Highlight first point
        if (index === 0 && !isComplete && polygon.length >= 3) {
            canvasContext.strokeStyle = 'yellow';
            canvasContext.lineWidth = 2;
            canvasContext.beginPath();
            canvasContext.arc(point[0], point[1], 8, 0, Math.PI * 2);
            canvasContext.stroke();
        }
    });
}

async function saveSafeAreas() {
    // Finish current polygon
    if (currentPolygon.length >= 3) {
        safeAreas.push([...currentPolygon]);
        currentPolygon = [];
    }
    
    if (saveStatus) {
        saveStatus.textContent = "Saving...";
        saveStatus.className = "status saving";
    }
    
    try {
        const response = await fetch(`${ANALYTICS_HTTP_URL}/set_safe_areas`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                camera_id: currentCameraId,
                safe_areas: safeAreas
            })
        });
        
        if (response.ok) {
            if (saveStatus) {
                saveStatus.textContent = "Saved successfully!";
                saveStatus.className = "status success";
            }
            
            // Also send command to camera to update safe areas
            sendCommand("update_safe_areas", safeAreas);
            
            setTimeout(() => {
                hideSafeAreaPopup();
            }, 1000);
        } else {
            throw new Error(`HTTP ${response.status}`);
        }
    } catch (error) {
        console.error('Save error:', error);
        if (saveStatus) {
            saveStatus.textContent = "Save failed";
            saveStatus.className = "status error";
        }
    }
}

function hideSafeAreaPopup() {
    safeAreaPopup.style.display = "none";
    isEditing = false;
    
    // Clean up
    if (canvasContext) {
        safeAreaCanvas.removeEventListener('click', handleCanvasClick);
        safeAreaCanvas.removeEventListener('mousemove', handleCanvasMouseMove);
        safeAreaCanvas.removeEventListener('contextmenu', handleCanvasRightClick);
    }
}

// ============================================
// POPUP FUNCTIONS
// ============================================

function confirmBackground() {
    sendCommand("set_background", true);
    hidePopup();
}

function hidePopup() {
    if (popup) popup.style.display = "none";
}

// ============================================
// INITIALIZATION
// ============================================

document.addEventListener('DOMContentLoaded', function() {
    ANALYTICS_HTTP_URL = window.location.origin;
    console.log(`Connected to analytics server: ${ANALYTICS_HTTP_URL}`);
    
    // Update server status
    const serverStatusElement = document.getElementById('serverStatus');
    if (serverStatusElement) {
        serverStatusElement.textContent = `Connected to ${ANALYTICS_HTTP_URL}`;
        serverStatusElement.style.color = '#4CAF50';
    }
    
    // Initialize stream
    startStream();
    
    // Load initial data
    loadCameraList();
    fetchCameraState(currentCameraId);
    loadSafeAreasForCamera(currentCameraId);
    
    // Set up periodic updates
    cameraListTimer = setInterval(loadCameraList, 30000); // Update camera list every 30 seconds
    cameraStateTimer = setInterval(() => fetchCameraState(currentCameraId), 10000); // Update state every 10 seconds
    
    // Update time display every second
    setInterval(updateLastUpdateDisplay, 1000);
    
    // Stop stream when page closes
    window.addEventListener('beforeunload', stopStream);
    
    // Handle window resize for editor
    window.addEventListener('resize', function() {
        if (isEditing && backgroundImage) {
            initializeCanvas();
            drawSafeAreas();
        }
    });
});

// ============================================
// GLOBAL EXPORTS
// ============================================

window.confirmBackground = confirmBackground;
window.hidePopup = hidePopup;
window.hideSafeAreaPopup = hideSafeAreaPopup;
window.sendCommand = sendCommand;
window.loadCameraList = loadCameraList;
window.fetchCameraState = fetchCameraState;