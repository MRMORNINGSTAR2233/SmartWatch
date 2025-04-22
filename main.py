import os
import sys
import time
import logging
import asyncio
import sqlite3
import json
from datetime import datetime
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from bleak import BleakScanner, BleakClient
from bleak.exc import BleakError
import tensorflow as tf
import numpy as np
import threading
import uvicorn
from tensorflow.keras.models import load_model

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("smartwatch_app.log")]
)
logger = logging.getLogger("smartwatch.main")

# Configuration
DB_PATH = "smartwatch_data.db"
MODEL_PATH = "fall_detection_model.h5"
FALL_THRESHOLD = 0.75  # Confidence threshold for fall detection

# Bluetooth UUIDs
HEART_RATE_SERVICE_UUID = "0000180d-0000-1000-8000-00805f9b34fb"
HEART_RATE_CHARACTERISTIC_UUID = "00002a37-0000-1000-8000-00805f9b34fb"
STEP_COUNT_SERVICE_UUID = "0000181c-0000-1000-8000-00805f9b34fb"
STEP_COUNT_CHARACTERISTIC_UUID = "00002a56-0000-1000-8000-00805f9b34fb"
ACCELEROMETER_SERVICE_UUID = "0000183e-0000-1000-8000-00805f9b34fb"  # Example UUID, may vary by device
ACCELEROMETER_CHARACTERISTIC_UUID = "00002a6d-0000-1000-8000-00805f9b34fb"  # Example UUID, may vary by device

# Load the trained model
try:
    model = load_model(MODEL_PATH)
    logger.info(f"Successfully loaded fall detection model from {MODEL_PATH}")
except Exception as e:
    logger.error(f"Failed to load model: {e}")
    model = None

# Initialize FastAPI
app = FastAPI(title="Smartwatch Fall Detection System")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic models
class DeviceInfo(BaseModel):
    address: str
    name: Optional[str] = None
    rssi: Optional[int] = None

class SmartWatchData(BaseModel):
    heart_rate: Optional[int] = None
    steps: Optional[int] = None
    accelerometer_x: Optional[float] = None
    accelerometer_y: Optional[float] = None
    accelerometer_z: Optional[float] = None
    timestamp: float = 0.0
    device_id: Optional[str] = None

class FallAlertConfig(BaseModel):
    enabled: bool = True
    threshold: float = FALL_THRESHOLD
    contacts: List[str] = []

class HistoricalDataRequest(BaseModel):
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    limit: int = 100

# Globals
connected_device: Optional[BleakClient] = None
device_data = SmartWatchData()
data_lock = threading.Lock()
active_connections: List[WebSocket] = []
alert_config = FallAlertConfig()
recent_data_buffer = []  # Buffer for accumulating time-series data for prediction

# Database initialization
def init_database():
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Create tables if they don't exist
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS device_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT,
            heart_rate INTEGER,
            steps INTEGER,
            accelerometer_x REAL,
            accelerometer_y REAL,
            accelerometer_z REAL,
            timestamp REAL,
            created_at TEXT
        )
        ''')
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS fall_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT,
            confidence REAL,
            timestamp REAL,
            acknowledged BOOLEAN DEFAULT 0,
            created_at TEXT
        )
        ''')
        
        conn.commit()
        conn.close()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Database initialization error: {e}")

# Data storage function
def store_device_data(data: SmartWatchData):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
        INSERT INTO device_data 
        (device_id, heart_rate, steps, accelerometer_x, accelerometer_y, accelerometer_z, timestamp, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            data.device_id,
            data.heart_rate,
            data.steps,
            data.accelerometer_x,
            data.accelerometer_y,
            data.accelerometer_z,
            data.timestamp,
            datetime.now().isoformat()
        ))
        
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Database storage error: {e}")

def store_fall_alert(device_id: str, confidence: float, timestamp: float):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
        INSERT INTO fall_alerts 
        (device_id, confidence, timestamp, created_at)
        VALUES (?, ?, ?, ?)
        ''', (
            device_id,
            confidence,
            timestamp,
            datetime.now().isoformat()
        ))
        
        conn.commit()
        conn.close()
        logger.info(f"Fall alert stored for device {device_id} with confidence {confidence}")
    except Exception as e:
        logger.error(f"Fall alert storage error: {e}")

# Handlers
async def heart_rate_notification_handler(sender: int, data: bytearray):
    try:
        flags = data[0]
        heart_rate = data[1] if (flags & 0x01) == 0 else int.from_bytes(data[1:3], byteorder='little')
        
        with data_lock:
            device_data.heart_rate = heart_rate
            device_data.timestamp = time.time()
            
            # Store data in the database
            if connected_device:
                device_data.device_id = connected_device.address
                
            # Make a copy to avoid race conditions
            current_data = SmartWatchData(**device_data.dict())
        
        # Store in database (run outside of lock)
        store_device_data(current_data)
        
        logger.info(f"Heart Rate: {heart_rate} BPM")
        
        # Buffer for time-series analysis
        recent_data_buffer.append(current_data.dict())
        if len(recent_data_buffer) > 10:  # Keep last 10 readings
            recent_data_buffer.pop(0)
            
        # Predict fall
        await predict_fall_and_alert()
        
        # Broadcast to websocket clients
        await broadcast_data(current_data)
        
    except Exception as e:
        logger.error(f"Heart rate handler error: {e}")

async def step_count_notification_handler(sender: int, data: bytearray):
    try:
        steps = int.from_bytes(data, byteorder='little')
        
        with data_lock:
            device_data.steps = steps
            device_data.timestamp = time.time()
            
            # Store data in the database
            if connected_device:
                device_data.device_id = connected_device.address
                
            # Make a copy to avoid race conditions
            current_data = SmartWatchData(**device_data.dict())
        
        # Store in database (run outside of lock)
        store_device_data(current_data)
        
        logger.info(f"Step Count: {steps}")
        
        # Buffer for time-series analysis
        recent_data_buffer.append(current_data.dict())
        if len(recent_data_buffer) > 10:  # Keep last 10 readings
            recent_data_buffer.pop(0)
            
        # Predict fall
        await predict_fall_and_alert()
        
        # Broadcast to websocket clients
        await broadcast_data(current_data)
        
    except Exception as e:
        logger.error(f"Step count handler error: {e}")

async def accelerometer_notification_handler(sender: int, data: bytearray):
    try:
        # Parse accelerometer data (format may vary by device)
        # This is a simplified example assuming x, y, z values as signed 16-bit integers
        x = int.from_bytes(data[0:2], byteorder='little', signed=True) / 1000.0  # Convert to g
        y = int.from_bytes(data[2:4], byteorder='little', signed=True) / 1000.0
        z = int.from_bytes(data[4:6], byteorder='little', signed=True) / 1000.0
        
        with data_lock:
            device_data.accelerometer_x = x
            device_data.accelerometer_y = y
            device_data.accelerometer_z = z
            device_data.timestamp = time.time()
            
            # Store data in the database
            if connected_device:
                device_data.device_id = connected_device.address
                
            # Make a copy to avoid race conditions
            current_data = SmartWatchData(**device_data.dict())
        
        # Store in database (run outside of lock)
        store_device_data(current_data)
        
        logger.info(f"Accelerometer: X={x:.2f}g, Y={y:.2f}g, Z={z:.2f}g")
        
        # Buffer for time-series analysis
        recent_data_buffer.append(current_data.dict())
        if len(recent_data_buffer) > 10:  # Keep last 10 readings
            recent_data_buffer.pop(0)
            
        # Predict fall
        await predict_fall_and_alert()
        
        # Broadcast to websocket clients
        await broadcast_data(current_data)
        
    except Exception as e:
        logger.error(f"Accelerometer handler error: {e}")

async def broadcast_data(data: SmartWatchData):
    try:
        message = data.dict()
        message["message_type"] = "data_update"
        
        with data_lock:
            for connection in active_connections[:]:
                try:
                    await connection.send_json(message)
                except Exception as e:
                    logger.error(f"WebSocket send error: {e}")
                    if connection in active_connections:
                        active_connections.remove(connection)
    except Exception as e:
        logger.error(f"Data broadcast error: {e}")

async def predict_fall_and_alert():
    if not model or not alert_config.enabled:
        return
        
    try:
        # Get current data for prediction
        with data_lock:
            hr = device_data.heart_rate
            steps = device_data.steps
            acc_x = device_data.accelerometer_x
            acc_y = device_data.accelerometer_y
            acc_z = device_data.accelerometer_z
            timestamp = device_data.timestamp
            device_id = device_data.device_id if connected_device else "unknown"

        # Check if we have sufficient data
        if hr is None or steps is None or acc_x is None or acc_y is None or acc_z is None:
            missing = []
            if hr is None: missing.append("heart_rate")
            if steps is None: missing.append("steps")
            if acc_x is None or acc_y is None or acc_z is None: missing.append("accelerometer")
            
            if missing:
                logger.debug(f"Missing data for prediction. Skipping fall detection. Missing fields: {missing}")
            return

        # Calculate additional features
        acc_magnitude = np.sqrt(acc_x**2 + acc_y**2 + acc_z**2)
        
        # Time-series features from buffer
        if len(recent_data_buffer) >= 5:
            recent_hr = [entry.get('heart_rate', hr) for entry in recent_data_buffer[-5:] if entry.get('heart_rate')]
            hr_variance = np.var(recent_hr) if recent_hr else 0
            
            recent_acc = [(entry.get('accelerometer_x', 0), entry.get('accelerometer_y', 0), entry.get('accelerometer_z', 0)) 
                          for entry in recent_data_buffer[-5:] if entry.get('accelerometer_x') is not None]
            
            # Calculate jerk (derivative of acceleration)
            acc_jerk = 0
            if len(recent_acc) >= 2:
                # Simple first-order difference
                jerk_values = []
                for i in range(1, len(recent_acc)):
                    dt = recent_data_buffer[-i]['timestamp'] - recent_data_buffer[-(i+1)]['timestamp']
                    if dt > 0:  # Avoid division by zero
                        dx = (recent_acc[-i][0] - recent_acc[-(i+1)][0]) / dt
                        dy = (recent_acc[-i][1] - recent_acc[-(i+1)][1]) / dt
                        dz = (recent_acc[-i][2] - recent_acc[-(i+1)][2]) / dt
                        jerk = np.sqrt(dx**2 + dy**2 + dz**2)
                        jerk_values.append(jerk)
                
                acc_jerk = np.mean(jerk_values) if jerk_values else 0
        else:
            hr_variance = 0
            acc_jerk = 0

        # Input features for model
        input_data = np.array([[
            hr, 
            steps, 
            acc_x, 
            acc_y, 
            acc_z, 
            acc_magnitude,
            hr_variance,
            acc_jerk
        ]])
        
        # Make prediction
        prediction = model.predict(input_data, verbose=0)
        fall_confidence = float(prediction[0][1])  # Assuming binary classification (not fall, fall)
        
        logger.debug(f"Fall prediction confidence: {fall_confidence:.4f} (threshold: {alert_config.threshold})")
        
        # Check if fall is detected
        if fall_confidence >= alert_config.threshold:
            logger.warning(f"Fall detected with confidence {fall_confidence:.4f}! Triggering alert...")
            
            # Store in database
            store_fall_alert(device_id, fall_confidence, timestamp)
            
            # Send alert to connected clients
            await trigger_alert(fall_confidence)

    except Exception as e:
        logger.error(f"Fall prediction error: {e}")
        logger.exception("Detailed traceback:")

async def trigger_alert(confidence: float):
    alert_message = {
        "message_type": "fall_alert",
        "fall_detected": True, 
        "confidence": confidence,
        "timestamp": time.time(),
        "message": "ALERT: Fall detected!",
        "device_id": device_data.device_id if connected_device else "unknown"
    }
    
    # Send to all websocket clients
    with data_lock:
        for connection in active_connections[:]:
            try:
                await connection.send_json(alert_message)
            except Exception as e:
                logger.error(f"WebSocket alert send error: {e}")
                if connection in active_connections:
                    active_connections.remove(connection)
    
    # Here you could implement additional alert mechanisms like SMS or email
    # For example, integrate with Twilio for SMS or use SMTP for email
    if alert_config.contacts:
        logger.info(f"Would send alerts to contacts: {alert_config.contacts}")
        # Implement notification sending logic here

# Endpoints
@app.get("/")
async def root():
    return {"message": "Smartwatch Fall Detection System", "status": "running"}

@app.get("/devices", response_model=List[DeviceInfo])
async def scan_devices():
    try:
        logger.info("Scanning for Bluetooth devices...")
        devices = await BleakScanner.discover()
        return [DeviceInfo(address=d.address, name=d.name, rssi=d.rssi) for d in devices if d.name]
    except Exception as e:
        logger.error(f"Scan error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/connect/{device_address}")
async def connect_to_device(device_address: str):
    global connected_device

    if connected_device and connected_device.is_connected:
        await connected_device.disconnect()
        logger.info(f"Disconnected from previous device")

    try:
        logger.info(f"Connecting to device: {device_address}")
        client = BleakClient(device_address)
        await client.connect()
        logger.info(f"Connected to device: {device_address}")

        # Reset data
        with data_lock:
            device_data.heart_rate = None
            device_data.steps = None
            device_data.accelerometer_x = None
            device_data.accelerometer_y = None
            device_data.accelerometer_z = None
            device_data.timestamp = time.time()
            device_data.device_id = device_address

        services = await client.get_services()
        service_found = False
        
        for service in services:
            logger.debug(f"Service: {service.uuid}")
            for char in service.characteristics:
                logger.debug(f"Characteristic: {char.uuid}")
                
                if char.uuid.lower() == HEART_RATE_CHARACTERISTIC_UUID.lower():
                    await client.start_notify(char.uuid, heart_rate_notification_handler)
                    logger.info("Heart rate notifications started.")
                    service_found = True
                
                elif char.uuid.lower() == STEP_COUNT_CHARACTERISTIC_UUID.lower():
                    await client.start_notify(char.uuid, step_count_notification_handler)
                    logger.info("Step count notifications started.")
                    service_found = True
                
                elif char.uuid.lower() == ACCELEROMETER_CHARACTERISTIC_UUID.lower():
                    await client.start_notify(char.uuid, accelerometer_notification_handler)
                    logger.info("Accelerometer notifications started.")
                    service_found = True

        if not service_found:
            logger.warning("No compatible services found on device.")
            await client.disconnect()
            raise HTTPException(status_code=400, detail="No compatible services found on device.")

        connected_device = client
        logger.info("Device connected successfully.")
        return {"status": "connected", "device_address": device_address}

    except BleakError as e:
        logger.error(f"Bluetooth error: {e}")
        raise HTTPException(status_code=500, detail=f"Bluetooth error: {str(e)}")
    except Exception as e:
        logger.error(f"Connection error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/disconnect")
async def disconnect_device():
    global connected_device
    
    if not connected_device:
        return {"status": "not_connected"}
        
    try:
        if connected_device.is_connected:
            await connected_device.disconnect()
            logger.info("Device disconnected successfully.")
        
        connected_device = None
        return {"status": "disconnected"}
    except Exception as e:
        logger.error(f"Disconnect error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/status")
async def get_status():
    global connected_device
    
    return {
        "device_connected": connected_device is not None and connected_device.is_connected,
        "device_address": connected_device.address if connected_device else None,
        "active_connections": len(active_connections),
        "fall_detection_enabled": alert_config.enabled,
        "last_data_timestamp": device_data.timestamp
    }

@app.get("/current-data")
async def get_current_data():
    with data_lock:
        return device_data

@app.post("/history")
async def get_historical_data(request: HistoricalDataRequest):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        query = "SELECT * FROM device_data WHERE 1=1"
        params = []
        
        if request.start_time:
            query += " AND timestamp >= ?"
            params.append(request.start_time)
            
        if request.end_time:
            query += " AND timestamp <= ?"
            params.append(request.end_time)
            
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(request.limit)
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        # Convert to list of dicts
        result = [dict(row) for row in rows]
        
        conn.close()
        return {"data": result, "count": len(result)}
    except Exception as e:
        logger.error(f"Historical data query error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/alerts")
async def get_alerts(limit: int = 50, include_acknowledged: bool = False):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        query = "SELECT * FROM fall_alerts"
        params = []
        
        if not include_acknowledged:
            query += " WHERE acknowledged = 0"
            
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        # Convert to list of dicts
        result = [dict(row) for row in rows]
        
        conn.close()
        return {"alerts": result, "count": len(result)}
    except Exception as e:
        logger.error(f"Alerts query error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/acknowledge-alert/{alert_id}")
async def acknowledge_alert(alert_id: int):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("UPDATE fall_alerts SET acknowledged = 1 WHERE id = ?", (alert_id,))
        
        if cursor.rowcount == 0:
            conn.close()
            raise HTTPException(status_code=404, detail="Alert not found")
            
        conn.commit()
        conn.close()
        return {"status": "acknowledged", "alert_id": alert_id}
    except sqlite3.Error as e:
        logger.error(f"Database error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Acknowledge alert error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/configure-alerts")
async def configure_alerts(config: FallAlertConfig):
    global alert_config
    
    alert_config = config
    logger.info(f"Alert configuration updated: {config}")
    return {"status": "updated", "config": alert_config}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    global alert_config  # ✅ Move this to the top

    await websocket.accept()
    active_connections.append(websocket)
    logger.info(f"WebSocket client connected. Total connections: {len(active_connections)}")
    
    # Send initial status
    status_message = {
        "message_type": "status_update",
        "device_connected": connected_device is not None and connected_device.is_connected,
        "device_address": connected_device.address if connected_device else None,
        "fall_detection_enabled": alert_config.enabled
    }
    await websocket.send_json(status_message)
    
    try:
        while True:
            message = await websocket.receive_text()
            try:
                # Handle commands from frontend
                data = json.loads(message)
                command = data.get("command")
                
                if command == "get_status":
                    await websocket.send_json({
                        "message_type": "status_response",
                        "device_connected": connected_device is not None and connected_device.is_connected,
                        "device_address": connected_device.address if connected_device else None,
                        "fall_detection_enabled": alert_config.enabled
                    })
                    
                elif command == "set_alert_config":
                    config_data = data.get("data", {})
                    alert_config.enabled = config_data.get("enabled", alert_config.enabled)
                    alert_config.threshold = config_data.get("threshold", alert_config.threshold)
                    alert_config.contacts = config_data.get("contacts", alert_config.contacts)
                    
                    await websocket.send_json({
                        "message_type": "config_updated",
                        "config": alert_config.dict()
                    })
                    
            except json.JSONDecodeError:
                logger.warning(f"Received invalid JSON: {message}")
            except Exception as e:
                logger.error(f"WebSocket command error: {e}")
                
    except WebSocketDisconnect:
        if websocket in active_connections:
            active_connections.remove(websocket)
        logger.info(f"WebSocket client disconnected. Remaining: {len(active_connections)}")

@app.on_event("startup")
async def startup_event():
    # Initialize database
    init_database()
    logger.info("API started successfully.")

@app.on_event("shutdown")
async def shutdown_event():
    global connected_device
    if connected_device and connected_device.is_connected:
        logger.info("Disconnecting device...")
        await connected_device.disconnect()
    logger.info("API shutdown complete.")

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)