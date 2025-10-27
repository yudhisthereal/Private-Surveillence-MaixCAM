import paho.mqtt.client as mqtt
import json
import base64
import pickle
import time
import ssl
from datetime import datetime
import numpy as np
from typing import Dict, List, Any
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# HiveMQ Cloud Setup
MQTT_BROKER = "3e065ffaa6084b219bc6553c8659b067.s1.eu.hivemq.cloud"
MQTT_PORT = 8883
MQTT_USERNAME = "PatientMonitor"
MQTT_PASSWORD = "Patientmonitor1"
MQTT_TOPIC_SKELETAL = "patient_monitor/skeletal_data"
MQTT_TOPIC_DIAGNOSIS = "patient_monitor/diagnosis"
MQTT_TOPIC_CONTROL = "patient_monitor/control"

class PatientAnalytics:
    def __init__(self):
        self.mqtt_client = None
        self.patient_data = {}  # Store recent patient data
        self.diagnosis_models = {}  # Loaded AI models for diagnosis
        self.caregiver_connections = {}  # Active caregiver connections
        
        # Diagnosis history
        self.diagnosis_history = []
        
        # Configuration
        self.analysis_config = {
            "fall_detection_threshold": 0.7,
            "activity_classification": True,
            "context_analysis": True,
            "risk_assessment": True
        }
        
        self.setup_analytics_models()
    
    def setup_analytics_models(self):
        """Setup AI models for advanced analytics"""
        # Placeholder for loading advanced AI models
        logger.info("Setting up analytics models...")
        
        self.diagnosis_models = {
            "fall_detector": self.load_fall_detection_model(),
            "activity_classifier": self.load_activity_classification_model(),
            "risk_assessor": self.load_risk_assessment_model()
        }
    
    def load_fall_detection_model(self):
        """Load advanced fall detection model"""
        logger.info("Loading fall detection model...")
        return {"name": "advanced_fall_detector", "version": "1.0"}
    
    def load_activity_classification_model(self):
        """Load activity classification model"""
        logger.info("Loading activity classification model...")
        return {"name": "activity_classifier", "version": "1.0"}
    
    def load_risk_assessment_model(self):
        """Load risk assessment model"""
        logger.info("Loading risk assessment model...")
        return {"name": "risk_assessor", "version": "1.0"}
    
    def setup_mqtt(self):
        """Setup MQTT client for communication with MaixCAM devices"""
        try:
            self.mqtt_client = mqtt.Client()
            
            # Set username and password for authentication
            self.mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
            self.mqtt_client.tls_set(cert_reqs=ssl.CERT_NONE)
            
            # Set callbacks
            self.mqtt_client.on_connect = self.on_mqtt_connect
            self.mqtt_client.on_message = self.on_mqtt_message
            self.mqtt_client.on_disconnect = self.on_mqtt_disconnect
            
            # Connect to the broker
            self.mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
            self.mqtt_client.loop_start()
            logger.info("Analytics MQTT client configured for HiveMQ Cloud")
            
        except Exception as e:
            logger.error(f"MQTT connection failed: {e}")
    
    def on_mqtt_connect(self, client, userdata, flags, rc):
        """Callback for when MQTT client connects"""
        if rc == 0:
            logger.info("Successfully connected to HiveMQ Cloud")
            # Subscribe to relevant topics
            client.subscribe(MQTT_TOPIC_SKELETAL)
            client.subscribe(MQTT_TOPIC_CONTROL)
            logger.info(f"Subscribed to topics: {MQTT_TOPIC_SKELETAL}, {MQTT_TOPIC_CONTROL}")
        else:
            logger.error(f"Failed to connect to MQTT broker, return code: {rc}")
            if rc == 5:
                logger.error("Authentication failed - check username and password")
    
    def on_mqtt_disconnect(self, client, userdata, rc):
        """Callback for when MQTT client disconnects"""
        logger.warning(f"Disconnected from MQTT broker, return code: {rc}")
    
    def on_mqtt_message(self, client, userdata, msg):
        """Callback for when MQTT message is received"""
        try:
            logger.debug(f"Received message on topic: {msg.topic}")
            payload = json.loads(msg.payload.decode())
            
            if msg.topic == MQTT_TOPIC_SKELETAL:
                self.process_skeletal_data(payload)
            elif msg.topic == MQTT_TOPIC_CONTROL:
                self.process_control_message(payload)
            else:
                logger.warning(f"Received message on unknown topic: {msg.topic}")
                
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode JSON message: {e}")
        except Exception as e:
            logger.error(f"Error processing MQTT message: {e}")
    
    def decrypt_skeletal_data(self, encrypted_data):
        """Decrypt skeletal data received from MaixCAM"""
        try:
            decoded_data = base64.b64decode(encrypted_data["encrypted_data"])
            return pickle.loads(decoded_data)
        except Exception as e:
            logger.error(f"Error decrypting skeletal data: {e}")
            return None
    
    def process_skeletal_data(self, encrypted_payload):
        """Process skeletal data received from MaixCAM"""
        # Decrypt the data
        skeletal_data = self.decrypt_skeletal_data(encrypted_payload)
        if not skeletal_data:
            return
        
        device_id = skeletal_data.get("device_id")
        track_id = skeletal_data.get("track_id")
        timestamp = skeletal_data.get("timestamp")
        keypoints = skeletal_data.get("keypoints")
        bbox = skeletal_data.get("bbox")
        
        logger.info(f"Processing skeletal data from device: {device_id}, track: {track_id}")
        
        # Store data for analysis
        if device_id not in self.patient_data:
            self.patient_data[device_id] = {}
        
        if track_id not in self.patient_data[device_id]:
            self.patient_data[device_id][track_id] = []
        
        # Keep only recent data (e.g., last 5 minutes)
        current_time = time.time()
        self.patient_data[device_id][track_id].append({
            "timestamp": timestamp,
            "keypoints": keypoints,
            "bbox": bbox,
            "processing_time": current_time
        })
        
        # Remove old data
        self.patient_data[device_id][track_id] = [
            data for data in self.patient_data[device_id][track_id]
            if current_time - data["processing_time"] < 300  # 5 minutes
        ]
        
        # Perform advanced analysis
        diagnosis = self.perform_advanced_analysis(device_id, track_id, skeletal_data)
        
        # Send diagnosis back to MaixCAM if needed
        if diagnosis and diagnosis.get("alert_level") != "normal":
            self.send_diagnosis_to_device(device_id, diagnosis)
        
        # Notify caregivers if high-risk situation detected
        if diagnosis and diagnosis.get("alert_level") in ["high", "critical"]:
            self.notify_caregivers(device_id, diagnosis)
    
    def perform_advanced_analysis(self, device_id, track_id, skeletal_data):
        """Perform advanced analysis on skeletal data"""
        try:
            keypoints = skeletal_data.get("keypoints")
            bbox = skeletal_data.get("bbox")
            timestamp = skeletal_data.get("timestamp")
            
            # Advanced fall detection
            fall_risk = self.advanced_fall_detection(keypoints, bbox)
            
            # Activity classification
            activity = self.classify_activity(keypoints, bbox)
            
            # Context analysis (time of day, historical patterns, etc.)
            context_risk = self.analyze_context(device_id, track_id, timestamp)
            
            # Risk assessment
            overall_risk = self.assess_overall_risk(fall_risk, activity, context_risk)
            
            # Generate diagnosis
            diagnosis = {
                "device_id": device_id,
                "track_id": track_id,
                "timestamp": timestamp,
                "analysis_time": datetime.now().isoformat(),
                "fall_risk": fall_risk,
                "detected_activity": activity,
                "context_risk": context_risk,
                "overall_risk": overall_risk,
                "alert_level": self.determine_alert_level(overall_risk),
                "recommendations": self.generate_recommendations(overall_risk, activity),
                "confidence": 0.85,
                "signature": self.sign_diagnosis(device_id, timestamp)
            }
            
            # Store in history
            self.diagnosis_history.append(diagnosis)
            
            logger.info(f"Generated diagnosis for {device_id}-{track_id}: {diagnosis['alert_level']}")
            return diagnosis
            
        except Exception as e:
            logger.error(f"Error in advanced analysis: {e}")
            return None
    
    def advanced_fall_detection(self, keypoints, bbox):
        """Advanced fall detection using multiple factors"""
        if len(keypoints) % 2 == 0:
            kp_array = np.array(keypoints).reshape(-1, 2)
            fall_probability = 0.0
            
            # Add sophisticated analysis here
            if len(kp_array) >= 5:
                pass
            
            return min(fall_probability, 1.0)
        
        return 0.0
    
    def classify_activity(self, keypoints, bbox):
        """Classify patient activity"""
        activities = [
            "standing", "sitting", "walking", "lying", 
            "bending", "reaching", "transitioning"
        ]
        return "unknown"
    
    def analyze_context(self, device_id, track_id, timestamp):
        """Analyze contextual factors"""
        context_risk = 0.0
        return context_risk
    
    def assess_overall_risk(self, fall_risk, activity, context_risk):
        """Assess overall risk based on multiple factors"""
        overall_risk = (
            fall_risk * 0.6 + 
            self.activity_risk(activity) * 0.3 + 
            context_risk * 0.1
        )
        return min(overall_risk, 1.0)
    
    def activity_risk(self, activity):
        """Map activity to risk level"""
        risk_map = {
            "lying": 0.8,
            "transitioning": 0.7,
            "bending": 0.5,
            "standing": 0.3,
            "sitting": 0.2,
            "walking": 0.4,
            "unknown": 0.5
        }
        return risk_map.get(activity, 0.5)
    
    def determine_alert_level(self, overall_risk):
        """Determine alert level based on risk score"""
        if overall_risk >= 0.8:
            return "critical"
        elif overall_risk >= 0.6:
            return "high"
        elif overall_risk >= 0.4:
            return "medium"
        elif overall_risk >= 0.2:
            return "low"
        else:
            return "normal"
    
    def generate_recommendations(self, overall_risk, activity):
        """Generate recommendations based on risk and activity"""
        recommendations = []
        
        if overall_risk >= 0.8:
            recommendations.extend([
                "Immediate caregiver attention required",
                "Check patient position and vital signs",
                "Consider emergency protocols"
            ])
        elif overall_risk >= 0.6:
            recommendations.extend([
                "Increased monitoring recommended",
                "Check patient environment for hazards",
                "Review recent activity patterns"
            ])
        
        if activity == "lying":
            recommendations.append("Monitor for prolonged immobility")
        elif activity == "transitioning":
            recommendations.append("Assist with position changes if needed")
        
        return recommendations
    
    def sign_diagnosis(self, device_id, timestamp):
        """Create a signature for the diagnosis to prevent tampering"""
        import hashlib
        signature_data = f"{device_id}_{timestamp}_{datetime.now().isoformat()}"
        return hashlib.sha256(signature_data.encode()).hexdigest()
    
    def send_diagnosis_to_device(self, device_id, diagnosis):
        """Send diagnosis back to MaixCAM device"""
        try:
            if self.mqtt_client and self.mqtt_client.is_connected():
                encrypted_diagnosis = self.encrypt_diagnosis(diagnosis)
                result = self.mqtt_client.publish(MQTT_TOPIC_DIAGNOSIS, json.dumps(encrypted_diagnosis))
                
                if result.rc == mqtt.MQTT_ERR_SUCCESS:
                    logger.info(f"Sent diagnosis to device {device_id}")
                else:
                    logger.error(f"Failed to send diagnosis to device {device_id}, error code: {result.rc}")
            else:
                logger.warning("MQTT client not connected, cannot send diagnosis")
                
        except Exception as e:
            logger.error(f"Error sending diagnosis to device: {e}")
    
    def encrypt_diagnosis(self, diagnosis):
        """Encrypt diagnosis for secure transmission"""
        return {
            "encrypted_diagnosis": base64.b64encode(pickle.dumps(diagnosis)).decode(),
            "encryption_method": "placeholder",
            "timestamp": time.time()
        }
    
    def notify_caregivers(self, device_id, diagnosis):
        """Notify registered caregivers of high-risk situations"""
        if device_id in self.caregiver_connections:
            for caregiver_id, connection_info in self.caregiver_connections[device_id].items():
                logger.info(f"Notifying caregiver {caregiver_id} about alert for device {device_id}")
                self.send_caregiver_notification(caregiver_id, device_id, diagnosis)
        else:
            logger.warning(f"No caregivers registered for device {device_id}")
    
    def send_caregiver_notification(self, caregiver_id, device_id, diagnosis):
        """Send notification to specific caregiver"""
        notification = {
            "caregiver_id": caregiver_id,
            "device_id": device_id,
            "alert_level": diagnosis["alert_level"],
            "timestamp": diagnosis["timestamp"],
            "message": f"Patient alert: {diagnosis['alert_level']} risk detected",
            "recommendations": diagnosis["recommendations"],
            "verification_token": diagnosis["signature"]
        }
        logger.info(f"Notification for {caregiver_id}: {notification['message']}")
    
    def process_control_message(self, payload):
        """Process control messages from caregivers or MaixCAM devices"""
        command = payload.get("command")
        source = payload.get("source", "unknown")
        
        logger.info(f"Processing control message: {command} from {source}")
        
        if command == "remote_access_request":
            self.handle_remote_access_request(payload)
        elif command == "diagnosis_request":
            self.handle_diagnosis_request(payload)
        elif command == "mode_switch_request":
            self.handle_mode_switch_request(payload)
        elif command == "caregiver_registration":
            self.handle_caregiver_registration(payload)
        else:
            logger.warning(f"Unknown command received: {command}")
    
    def handle_remote_access_request(self, payload):
        """Handle remote access requests from caregivers"""
        device_id = payload.get("device_id")
        caregiver_id = payload.get("caregiver_id")
        access_type = payload.get("access_type", "video")
        
        if self.verify_caregiver_permissions(caregiver_id, device_id):
            control_message = {
                "command": "remote_access",
                "enable": True,
                "access_type": access_type,
                "caregiver_id": caregiver_id
            }
            
            result = self.mqtt_client.publish(MQTT_TOPIC_CONTROL, json.dumps(control_message))
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                logger.info(f"Enabled remote access for caregiver {caregiver_id} on device {device_id}")
            else:
                logger.error(f"Failed to send remote access command, error code: {result.rc}")
        else:
            logger.warning(f"Unauthorized remote access request from {caregiver_id} for device {device_id}")
    
    def handle_diagnosis_request(self, payload):
        """Handle diagnosis requests from caregivers"""
        device_id = payload.get("device_id")
        caregiver_id = payload.get("caregiver_id")
        timeframe = payload.get("timeframe", "recent")
        
        diagnoses = self.get_diagnosis_history(device_id, timeframe)
        self.send_diagnosis_to_caregiver(caregiver_id, device_id, diagnoses)
    
    def handle_mode_switch_request(self, payload):
        """Handle requests to switch operation modes"""
        device_id = payload.get("device_id")
        requested_mode = payload.get("mode")
        caregiver_id = payload.get("caregiver_id")
        
        if self.verify_caregiver_permissions(caregiver_id, device_id):
            control_message = {
                "command": "switch_mode",
                "mode": requested_mode
            }
            
            result = self.mqtt_client.publish(MQTT_TOPIC_CONTROL, json.dumps(control_message))
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                logger.info(f"Switched device {device_id} to {requested_mode} mode")
            else:
                logger.error(f"Failed to send mode switch command, error code: {result.rc}")
        else:
            logger.warning(f"Unauthorized mode switch request from {caregiver_id} for device {device_id}")
    
    def handle_caregiver_registration(self, payload):
        """Handle caregiver registration for devices"""
        caregiver_id = payload.get("caregiver_id")
        device_id = payload.get("device_id")
        contact_info = payload.get("contact_info", {})
        
        if device_id not in self.caregiver_connections:
            self.caregiver_connections[device_id] = {}
        
        self.caregiver_connections[device_id][caregiver_id] = {
            "registration_time": datetime.now().isoformat(),
            "contact_info": contact_info,
            "permissions": payload.get("permissions", ["view", "receive_alerts"])
        }
        
        logger.info(f"Registered caregiver {caregiver_id} for device {device_id}")
    
    def verify_caregiver_permissions(self, caregiver_id, device_id):
        """Verify that caregiver has permissions for the device"""
        return (device_id in self.caregiver_connections and 
                caregiver_id in self.caregiver_connections[device_id])
    
    def get_diagnosis_history(self, device_id, timeframe):
        """Get diagnosis history for a device"""
        current_time = datetime.now()
        
        filtered_diagnoses = []
        for diagnosis in self.diagnosis_history:
            if diagnosis.get("device_id") == device_id:
                diagnosis_time = datetime.fromisoformat(diagnosis.get("analysis_time", ""))
                
                if timeframe == "recent" and (current_time - diagnosis_time).total_seconds() < 3600:
                    filtered_diagnoses.append(diagnosis)
                elif timeframe == "today" and diagnosis_time.date() == current_time.date():
                    filtered_diagnoses.append(diagnosis)
                elif timeframe == "all":
                    filtered_diagnoses.append(diagnosis)
        
        return filtered_diagnoses
    
    def send_diagnosis_to_caregiver(self, caregiver_id, device_id, diagnoses):
        """Send diagnosis information to caregiver"""
        logger.info(f"Sent {len(diagnoses)} diagnoses to caregiver {caregiver_id} for device {device_id}")
    
    def start(self):
        """Start the analytics service"""
        logger.info("Starting Patient Analytics Service...")
        self.setup_mqtt()
        self.start_web_interface()
        logger.info("Patient Analytics Service is running")
    
    def start_web_interface(self):
        """Start web interface for monitoring and configuration"""
        logger.info("Web interface placeholder - would start dashboard service")
    
    def stop(self):
        """Stop the analytics service"""
        if self.mqtt_client:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
        logger.info("Patient Analytics Service stopped")

def main():
    """Main function to start the analytics service"""
    analytics = PatientAnalytics()
    
    try:
        analytics.start()
        logger.info("Analytics service running. Press Ctrl+C to stop.")
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("Shutting down analytics service...")
    except Exception as e:
        logger.error(f"Analytics service error: {e}")
    finally:
        analytics.stop()

if __name__ == "__main__":
    main()