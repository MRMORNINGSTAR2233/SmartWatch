# main.py - FastAPI Backend for Smartwatch Data Collection

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import asyncio
import json
import logging
import time
from typing import Dict, List, Optional, Any
import threading
from bleak import BleakScanner, BleakClient
from bleak.exc import BleakError
import uvicorn

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Constants for Bluetooth services and characteristics
# These UUIDs are standard for heart rate monitoring and may need adjustment for specific devices
HEART_RATE_SERVICE_UUID = "0000180d-0000-1000-8000-00805f9b34fb"
HEART_RATE_CHARACTERISTIC_UUID = "00002a37-0000-1000-8000-00805f9b34fb"
STEP_COUNT_SERVICE_UUID = "0000181c-0000-1000-8000-00805f9b34fb"
STEP_COUNT_CHARACTERISTIC_UUID = "00002a56-0000-1000-8000-00805f9b34fb"

# Some watches might use custom UUIDs for step counting

app = FastAPI(title="Smartwatch Data Collection API")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic models for data validation
class DeviceInfo(BaseModel):
    address: str
    name: Optional[str] = None

class SmartWatchData(BaseModel):
    heart_rate: Optional[int] = None
    steps: Optional[int] = None
    timestamp: float = 0.0

# Global variables
connected_device: Optional[BleakClient] = None
device_data = SmartWatchData()
data_lock = threading.Lock()
active_connections: List[WebSocket] = []

async def heart_rate_notification_handler(sender: int, data: bytearray):
    """Handle incoming heart rate data from the smartwatch."""
    try:
        # Heart Rate Measurement characteristic data format (simplified):
        # First byte is flags, second byte is the heart rate value (if format is uint8)
        # Reference: https://www.bluetooth.com/specifications/specs/gatt-specification-supplement-3/
        
        flags = data[0]
        heart_rate_format = (flags & 0x01) == 0  # 0 = UINT8, 1 = UINT16
        
        if heart_rate_format:
            # UINT8 format
            heart_rate = data[1]
        else:
            # UINT16 format
            heart_rate = int.from_bytes(data[1:3], byteorder='little')
        
        logger.info(f"Received heart rate: {heart_rate} BPM")
        
        with data_lock:
            device_data.heart_rate = heart_rate
            device_data.timestamp = time.time()
        
        # Send to all connected WebSocket clients
        if active_connections:
            await broadcast_data()
    
    except Exception as e:
        logger.error(f"Error processing heart rate data: {e}")

async def step_count_notification_handler(sender: int, data: bytearray):
    """Handle incoming step count data from the smartwatch."""
    try:
        # Step count format varies by device, this is a simplified approach
        steps = int.from_bytes(data, byteorder='little')
        
        logger.info(f"Received step count: {steps} steps")
        
        with data_lock:
            device_data.steps = steps
            device_data.timestamp = time.time()
        
        # Send to all connected WebSocket clients
        if active_connections:
            await broadcast_data()
    
    except Exception as e:
        logger.error(f"Error processing step count data: {e}")

async def broadcast_data():
    """Send data to all connected WebSocket clients."""
    with data_lock:
        data_to_send = device_data.dict()
    
    for connection in active_connections:
        try:
            await connection.send_json(data_to_send)
        except Exception as e:
            logger.error(f"Error sending data to WebSocket: {e}")

@app.get("/")
async def root():
    """API root endpoint."""
    return {"message": "Smartwatch Data Collection API"}

@app.get("/devices", response_model=List[DeviceInfo])
async def scan_devices():
    """Scan for nearby Bluetooth devices."""
    try:
        logger.info("Scanning for Bluetooth devices...")
        devices = await BleakScanner.discover()
        
        result = []
        for device in devices:
            if device.name:  # Filter out unnamed devices
                result.append(DeviceInfo(address=device.address, name=device.name))
        
        logger.info(f"Found {len(result)} devices")
        return result
    
    except BleakError as e:
        logger.error(f"Bluetooth error during scan: {e}")
        raise HTTPException(status_code=500, detail=f"Bluetooth error: {str(e)}")
    
    except Exception as e:
        logger.error(f"Error scanning for devices: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/connect/{device_address}")
async def connect_to_device(device_address: str):
    """Connect to a specific Bluetooth device."""
    global connected_device
    
    # Disconnect from any existing device
    if connected_device and connected_device.is_connected:
        await connected_device.disconnect()
    
    try:
        logger.info(f"Connecting to device: {device_address}")
        client = BleakClient(device_address)
        await client.connect()
        
        # Check for service availability
        services = await client.get_services()
        
        has_heart_rate = False
        has_step_count = False
        
        for service in services:
            if service.uuid.lower() == HEART_RATE_SERVICE_UUID:
                for char in service.characteristics:
                    if char.uuid.lower() == HEART_RATE_CHARACTERISTIC_UUID:
                        has_heart_rate = True
                        logger.info("Heart rate service found")
                        # Set up notification for heart rate
                        await client.start_notify(HEART_RATE_CHARACTERISTIC_UUID, heart_rate_notification_handler)
            
            if service.uuid.lower() == STEP_COUNT_SERVICE_UUID:
                for char in service.characteristics:
                    if char.uuid.lower() == STEP_COUNT_CHARACTERISTIC_UUID:
                        has_step_count = True
                        logger.info("Step count service found")
                        # Set up notification for step count
                        await client.start_notify(STEP_COUNT_CHARACTERISTIC_UUID, step_count_notification_handler)
        
        # If we couldn't find the expected services, try a more generic approach
        if not (has_heart_rate and has_step_count):
            logger.warning("Standard services not found, searching for similar characteristics")
            
            # Look for characteristics that might contain heart rate or step data
            for service in services:
                for char in service.characteristics:
                    properties = char.properties
                    if "notify" in properties or "indicate" in properties:
                        # Try to identify the characteristic by descriptors or names
                        desc_values = []
                        try:
                            for descriptor in char.descriptors:
                                value = await client.read_gatt_descriptor(descriptor.handle)
                                desc_values.append(value)
                                
                                # Look for keywords in descriptors
                                desc_str = str(value)
                                if "heart" in desc_str.lower():
                                    logger.info(f"Found potential heart rate characteristic: {char.uuid}")
                                    await client.start_notify(char.uuid, heart_rate_notification_handler)
                                elif "step" in desc_str.lower():
                                    logger.info(f"Found potential step count characteristic: {char.uuid}")
                                    await client.start_notify(char.uuid, step_count_notification_handler)
                        except:
                            pass
                            
        connected_device = client
        logger.info("Device connected successfully")
        
        return {"status": "connected", "device_address": device_address}
    
    except BleakError as e:
        logger.error(f"Bluetooth error: {e}")
        raise HTTPException(status_code=500, detail=f"Bluetooth error: {str(e)}")
    
    except Exception as e:
        logger.error(f"Error connecting to device: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/disconnect")
async def disconnect_device():
    """Disconnect from the connected Bluetooth device."""
    global connected_device
    
    if not connected_device:
        return {"status": "not_connected"}
    
    try:
        await connected_device.disconnect()
        connected_device = None
        logger.info("Device disconnected")
        return {"status": "disconnected"}
    
    except Exception as e:
        logger.error(f"Error disconnecting device: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/data", response_model=SmartWatchData)
async def get_data():
    """Get the latest data from the smartwatch."""
    if not connected_device or not connected_device.is_connected:
        raise HTTPException(status_code=400, detail="No device connected")
    
    with data_lock:
        return device_data

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time data updates."""
    await websocket.accept()
    active_connections.append(websocket)
    logger.info(f"WebSocket client connected. Total connections: {len(active_connections)}")
    
    try:
        while True:
            # Keep the connection alive, data will be sent via broadcast_data
            await websocket.receive_text()
    
    except WebSocketDisconnect:
        active_connections.remove(websocket)
        logger.info(f"WebSocket client disconnected. Remaining connections: {len(active_connections)}")

@app.on_event("startup")
async def startup_event():
    """Initialize the application."""
    logger.info("Starting Smartwatch Data Collection API")

@app.on_event("shutdown")
async def shutdown_event():
    """Clean up resources on shutdown."""
    global connected_device
    
    if connected_device and connected_device.is_connected:
        logger.info("Disconnecting device on shutdown")
        await connected_device.disconnect()
    
    logger.info("Shutting down Smartwatch Data Collection API")

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)