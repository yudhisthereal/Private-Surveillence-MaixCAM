// script.js

const streamImg = document.getElementById('stream');
const popup = document.getElementById('popup');
const preview = document.getElementById('preview');
const toggleRecord = document.getElementById('toggleRecord');
const toggleRaw = document.getElementById('toggleRaw');
const autoUpdateBg = document.getElementById('autoUpdateBg');
const showSafeArea = document.getElementById('showSafeArea');
const useSafetyCheck = document.getElementById('useSafetyCheck');
const setBackgroundBtn = document.getElementById('setBackgroundBtn');
const editSafeAreaBtn = document.getElementById('editSafeAreaBtn');

// Safe Area Editor Elements
const safeAreaPopup = document.getElementById('safeAreaPopup');
const safeAreaCanvas = document.getElementById('safeAreaCanvas');
const newPolygonBtn = document.getElementById('newPolygonBtn');
const clearAllBtn = document.getElementById('clearAllBtn');
const saveSafeAreasBtn = document.getElementById('saveSafeAreasBtn');
const saveStatus = document.getElementById('saveStatus');

// Safe Area Editor State
let safeAreas = []; // Array of polygons, each polygon is array of points
let currentPolygon = []; // Points for the currently being drawn polygon
let isEditing = false;
let canvasContext = null;
let backgroundImage = null;
let originalImageWidth = 0;
let originalImageHeight = 0;
let canvasScale = 1;
let canvasOffsetX = 0;
let canvasOffsetY = 0;

// === 🔁 Send command using HTTP POST instead of WebSocket
function sendCommand(command, value = null) {
    fetch("/command", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command, value }),
    }).catch((err) => {
        console.error("Command failed:", err);
    });
}

// === ⬛ Event handlers
toggleRecord.onchange = () => {
    sendCommand("toggle_record", toggleRecord.checked);
};

toggleRaw.onchange = () => {
    sendCommand("toggle_raw", toggleRaw.checked);
};

autoUpdateBg.onchange = () => {
    sendCommand("auto_update_bg", autoUpdateBg.checked);
};

showSafeArea.onchange = () => {
    sendCommand("toggle_safe_area_display", showSafeArea.checked);
};

useSafetyCheck.onchange = () => {
    sendCommand("toggle_safety_check", useSafetyCheck.checked);
};

setBackgroundBtn.onclick = () => {
    preview.src = "/snapshot.jpg?_=" + Date.now();
    popup.style.display = "block";
};

editSafeAreaBtn.onclick = () => {
    showSafeAreaEditor();
};

// === Safe Area Editor Functions ===
function showSafeAreaEditor() {
    // Load current safe areas
    fetch("/get_safe_areas")
        .then(response => response.json())
        .then(areas => {
            safeAreas = areas;
            loadBackgroundImage();
        })
        .catch(err => {
            console.error("Error loading safe areas:", err);
            loadBackgroundImage();
        });
}

function loadBackgroundImage() {
    backgroundImage = new Image();
    backgroundImage.onload = function() {
        // Store original dimensions
        originalImageWidth = backgroundImage.width;
        originalImageHeight = backgroundImage.height;
        console.log(`Original image dimensions: ${originalImageWidth}x${originalImageHeight}`);
        initializeCanvas();
        safeAreaPopup.style.display = "block";
        isEditing = true;
        drawSafeAreas();
    };
    backgroundImage.src = "/snapshot.jpg?_=" + Date.now();
}

function initializeCanvas() {
    const container = safeAreaCanvas.parentElement;
    const maxWidth = Math.min(800, window.innerWidth - 100);
    const maxHeight = Math.min(600, window.innerHeight - 200);
    
    // Calculate scale to fit image in container while maintaining aspect ratio
    const scaleX = maxWidth / originalImageWidth;
    const scaleY = maxHeight / originalImageHeight;
    canvasScale = Math.min(scaleX, scaleY);
    
    // Set canvas display size (scaled)
    const displayWidth = Math.floor(originalImageWidth * canvasScale);
    const displayHeight = Math.floor(originalImageHeight * canvasScale);
    
    safeAreaCanvas.style.width = displayWidth + 'px';
    safeAreaCanvas.style.height = displayHeight + 'px';
    
    // Set canvas internal resolution to match original image
    safeAreaCanvas.width = originalImageWidth;
    safeAreaCanvas.height = originalImageHeight;
    
    // Calculate offset for centering
    canvasOffsetX = (displayWidth - (originalImageWidth * canvasScale)) / 2;
    canvasOffsetY = (displayHeight - (originalImageHeight * canvasScale)) / 2;
    
    canvasContext = safeAreaCanvas.getContext('2d');
    
    // Add event listeners with proper coordinate conversion
    safeAreaCanvas.addEventListener('click', handleCanvasClick);
    safeAreaCanvas.addEventListener('mousemove', handleCanvasMouseMove);
    safeAreaCanvas.addEventListener('contextmenu', handleCanvasRightClick);
    
    // Toolbar event listeners
    newPolygonBtn.onclick = startNewPolygon;
    clearAllBtn.onclick = clearAllPolygons;
    saveSafeAreasBtn.onclick = saveSafeAreas;
    
    console.log(`Canvas initialized: ${originalImageWidth}x${originalImageHeight} (scale: ${canvasScale.toFixed(2)})`);
}

function getCanvasCoordinates(clientX, clientY) {
    const rect = safeAreaCanvas.getBoundingClientRect();
    
    // Convert screen coordinates to canvas display coordinates
    const displayX = clientX - rect.left;
    const displayY = clientY - rect.top;
    
    // Convert display coordinates to original image coordinates
    const originalX = Math.floor(displayX / canvasScale);
    const originalY = Math.floor(displayY / canvasScale);
    
    // Clamp coordinates to canvas bounds
    const clampedX = Math.max(0, Math.min(originalX, originalImageWidth - 1));
    const clampedY = Math.max(0, Math.min(originalY, originalImageHeight - 1));
    
    return { x: clampedX, y: clampedY };
}

function handleCanvasClick(event) {
    if (!isEditing) return;
    
    const { x, y } = getCanvasCoordinates(event.clientX, event.clientY);
    
    // Normalize coordinates (0-1 range)
    const normalizedX = x / originalImageWidth;
    const normalizedY = y / originalImageHeight;
    
    // Check if clicking near first point to close polygon
    if (currentPolygon.length >= 3) {
        const firstPoint = currentPolygon[0];
        const distance = Math.sqrt(
            Math.pow(normalizedX - firstPoint[0], 2) + 
            Math.pow(normalizedY - firstPoint[1], 2)
        );
        
        // If close to first point, close the polygon
        if (distance < 0.05) { // 5% threshold
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
    
    const { x, y } = getCanvasCoordinates(event.clientX, event.clientY);
    
    // Normalize coordinates
    const normalizedX = x / originalImageWidth;
    const normalizedY = y / originalImageHeight;
    
    drawSafeAreas([...currentPolygon, [normalizedX, normalizedY]]);
}

function handleCanvasRightClick(event) {
    event.preventDefault();
    if (!isEditing || currentPolygon.length === 0) return;
    
    // Remove last point
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
    if (confirm("Are you sure you want to clear all safe areas?")) {
        safeAreas = [];
        currentPolygon = [];
        drawSafeAreas();
    }
}

function drawSafeAreas(tempPolygon = null) {
    // Clear canvas
    canvasContext.clearRect(0, 0, originalImageWidth, originalImageHeight);
    
    // Draw background image (scaled to fit original canvas dimensions)
    canvasContext.drawImage(backgroundImage, 0, 0, originalImageWidth, originalImageHeight);
    
    // Draw existing polygons
    safeAreas.forEach((polygon, polyIndex) => {
        drawPolygon(polygon, `hsl(${polyIndex * 60}, 70%, 50%)`, true);
    });
    
    // Draw current polygon (in progress)
    if (tempPolygon && tempPolygon.length > 0) {
        drawPolygon(tempPolygon, 'cyan', false);
    } else if (currentPolygon.length > 0) {
        drawPolygon(currentPolygon, 'cyan', false);
    }
}

function drawPolygon(polygon, color, isComplete) {
    if (polygon.length === 0) return;
    
    canvasContext.strokeStyle = color;
    canvasContext.fillStyle = color + '40'; // Add transparency
    canvasContext.lineWidth = 3;
    canvasContext.setLineDash(isComplete ? [] : [5, 5]);
    
    // Convert normalized coordinates to canvas coordinates
    const canvasPoints = polygon.map(point => [
        point[0] * originalImageWidth,
        point[1] * originalImageHeight
    ]);
    
    // Draw polygon
    canvasContext.beginPath();
    canvasContext.moveTo(canvasPoints[0][0], canvasPoints[0][1]);
    
    for (let i = 1; i < canvasPoints.length; i++) {
        canvasContext.lineTo(canvasPoints[i][0], canvasPoints[i][1]);
    }
    
    // Close the polygon if complete
    if (isComplete && canvasPoints.length >= 3) {
        canvasContext.closePath();
        canvasContext.fill();
    }
    
    canvasContext.stroke();
    canvasContext.setLineDash([]);
    
    // Draw points
    canvasPoints.forEach((point, index) => {
        // Point circle
        canvasContext.fillStyle = color;
        canvasContext.beginPath();
        canvasContext.arc(point[0], point[1], 5, 0, 2 * Math.PI);
        canvasContext.fill();
        
        // Point border
        canvasContext.strokeStyle = 'white';
        canvasContext.lineWidth = 2;
        canvasContext.beginPath();
        canvasContext.arc(point[0], point[1], 5, 0, 2 * Math.PI);
        canvasContext.stroke();
        
        // Highlight first point for closing
        if (index === 0 && !isComplete && polygon.length >= 3) {
            canvasContext.strokeStyle = 'yellow';
            canvasContext.lineWidth = 3;
            canvasContext.beginPath();
            canvasContext.arc(point[0], point[1], 10, 0, 2 * Math.PI);
            canvasContext.stroke();
        }
    });
}

function saveSafeAreas() {
    // Finish current polygon if it has enough points
    if (currentPolygon.length >= 3) {
        safeAreas.push([...currentPolygon]);
        currentPolygon = [];
    }
    
    saveStatus.textContent = "Saving...";
    saveStatus.className = "status saving";
    
    fetch("/set_safe_areas", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(safeAreas),
    })
    .then(response => response.json())
    .then(result => {
        saveStatus.textContent = "Saved successfully!";
        saveStatus.className = "status success";
        setTimeout(() => {
            hideSafeAreaPopup();
        }, 1000);
    })
    .catch(err => {
        console.error("Error saving safe areas:", err);
        saveStatus.textContent = "Error saving safe areas";
        saveStatus.className = "status error";
    });
}

function hideSafeAreaPopup() {
    safeAreaPopup.style.display = "none";
    isEditing = false;
    // Clean up event listeners
    safeAreaCanvas.removeEventListener('click', handleCanvasClick);
    safeAreaCanvas.removeEventListener('mousemove', handleCanvasMouseMove);
    safeAreaCanvas.removeEventListener('contextmenu', handleCanvasRightClick);
}

// === Background Popup Functions ===
function confirmBackground() {
    sendCommand("set_background");
    hidePopup();
}

function hidePopup() {
    popup.style.display = "none";
}

// Handle window resize
window.addEventListener('resize', function() {
    if (isEditing && backgroundImage) {
        initializeCanvas();
        drawSafeAreas();
    }
});

// Expose for inline onclick
window.confirmBackground = confirmBackground;
window.hidePopup = hidePopup;
window.hideSafeAreaPopup = hideSafeAreaPopup;