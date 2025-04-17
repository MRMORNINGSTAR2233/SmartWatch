# streamlit_app.py - Thread-Safe Streamlit UI for Smartwatch Data Collection

import streamlit as st
import requests
import pandas as pd
import plotly.express as px
import time
import json
import threading
import queue
import websocket
import logging
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# API Configuration
API_BASE_URL = "http://localhost:8000"
WS_URL = "ws://localhost:8000/ws"

# Create a global queue for thread-safe communication between WebSocket and main thread
ws_data_queue = queue.Queue()

# Initialize session state variables
if 'data_queue' not in st.session_state:
    st.session_state.data_queue = queue.Queue()
if 'connected' not in st.session_state:
    st.session_state.connected = False
if 'device_address' not in st.session_state:
    st.session_state.device_address = None
if 'data_history' not in st.session_state:
    st.session_state.data_history = pd.DataFrame(columns=['timestamp', 'heart_rate', 'steps'])
if 'devices' not in st.session_state:
    st.session_state.devices = []
if 'latest_heart_rate' not in st.session_state:
    st.session_state.latest_heart_rate = "N/A"
if 'latest_steps' not in st.session_state:
    st.session_state.latest_steps = "N/A"
if 'ws_active' not in st.session_state:
    st.session_state.ws_active = False

# Global WebSocket object (outside of session state)
ws_app = None
ws_thread = None

# Function to scan for devices
def scan_devices():
    try:
        response = requests.get(f"{API_BASE_URL}/devices")
        if response.status_code == 200:
            st.session_state.devices = response.json()
            return True
        else:
            st.error(f"Error scanning devices: {response.text}")
            return False
    except Exception as e:
        st.error(f"Connection error: {e}")
        return False

# Function to connect to a device
def connect_to_device(device_address):
    try:
        response = requests.post(f"{API_BASE_URL}/connect/{device_address}")
        if response.status_code == 200:
            st.session_state.connected = True
            st.session_state.device_address = device_address
            
            # Start WebSocket connection in a thread-safe way
            start_websocket_connection()
            
            return True
        else:
            st.error(f"Error connecting to device: {response.text}")
            return False
    except Exception as e:
        st.error(f"Connection error: {e}")
        return False

# Function to disconnect from device
def disconnect_device():
    try:
        # Stop WebSocket first
        stop_websocket_connection()
        
        response = requests.get(f"{API_BASE_URL}/disconnect")
        if response.status_code == 200:
            st.session_state.connected = False
            st.session_state.device_address = None
            return True
        else:
            st.error(f"Error disconnecting device: {response.text}")
            return False
    except Exception as e:
        st.error(f"Disconnection error: {e}")
        return False

# WebSocket message handler - puts data in queue instead of accessing session state
def on_message(ws, message):
    try:
        data = json.loads(message)
        logger.info(f"Received data: {data}")
        
        # Format the data
        new_data = {
            'timestamp': datetime.fromtimestamp(data.get('timestamp', time.time())),
            'heart_rate': data.get('heart_rate'),
            'steps': data.get('steps')
        }
        
        # Instead of accessing session state directly, use a global variable
        # or pass data through a thread-safe mechanism
        global ws_data_queue
        if not hasattr(globals(), 'ws_data_queue'):
            ws_data_queue = queue.Queue()
        
        ws_data_queue.put(new_data)
        
    except Exception as e:
        logger.error(f"Error processing WebSocket message: {e}")

def on_error(ws, error):
    logger.error(f"WebSocket error: {error}")

def on_close(ws, close_status_code, close_msg):
    logger.info(f"WebSocket connection closed: {close_msg}")
    st.session_state.ws_active = False

def on_open(ws):
    logger.info("WebSocket connection established")
    st.session_state.ws_active = True

def start_websocket_connection():
    """Start WebSocket connection in a separate thread"""
    global ws_app, ws_thread
    
    # Make sure any existing connection is closed
    stop_websocket_connection()
    
    try:
        # Create new connection
        ws_app = websocket.WebSocketApp(
            WS_URL,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close
        )
        
        # Start in a new thread
        ws_thread = threading.Thread(target=ws_app.run_forever)
        ws_thread.daemon = True
        ws_thread.start()
        
        st.session_state.ws_active = True
        logger.info("WebSocket thread started")
        
    except Exception as e:
        logger.error(f"Failed to start WebSocket: {e}")

def stop_websocket_connection():
    """Safely stop WebSocket connection"""
    global ws_app, ws_thread
    
    try:
        if ws_app:
            ws_app.close()
        
        # Wait for thread to finish if it exists
        if ws_thread and ws_thread.is_alive():
            ws_thread.join(timeout=1.0)
            
        ws_app = None
        ws_thread = None
        st.session_state.ws_active = False
        
    except Exception as e:
        logger.error(f"Error stopping WebSocket: {e}")

# Process any data that arrived via the queue
def process_queued_data():
    """Process any data that arrived in the queue"""
    global ws_data_queue
    queue_size = ws_data_queue.qsize()
    update_happened = False
    
    # Log the current queue size
    if queue_size > 0:
        logger.info(f"Processing {queue_size} queued data items")
    
    for _ in range(queue_size):
        try:
            # Get data from the queue (non-blocking)
            new_data = ws_data_queue.get_nowait()
            
            # Update the latest values
            if 'heart_rate' in new_data and new_data['heart_rate'] is not None:
                st.session_state.latest_heart_rate = f"{new_data['heart_rate']} BPM"
                logger.info(f"Updated heart rate: {st.session_state.latest_heart_rate}")
            
            if 'steps' in new_data and new_data['steps'] is not None:
                st.session_state.latest_steps = f"{new_data['steps']}"
                logger.info(f"Updated steps: {st.session_state.latest_steps}")
            
            # Add to the history dataframe
            new_row = pd.DataFrame([new_data])
            st.session_state.data_history = pd.concat([st.session_state.data_history, new_row], ignore_index=True)
            
            # Keep last 100 records to avoid memory issues
            if len(st.session_state.data_history) > 100:
                st.session_state.data_history = st.session_state.data_history.iloc[-100:]
                
            # Mark that we had an update
            update_happened = True
            ws_data_queue.task_done()
            
        except queue.Empty:
            break
    
    return update_happened

# Main Streamlit UI
st.title("Smartwatch Data Collection")
st.write("Connect to your smartwatch and monitor heart rate and steps in real-time.")

# Check for new data at each rerun
if st.session_state.connected:
    process_queued_data()

# Device connection section
st.header("Device Connection")

col1, col2 = st.columns(2)

with col1:
    if st.button("Scan for Devices", use_container_width=True):
        with st.spinner("Scanning..."):
            if scan_devices():
                st.success(f"Found {len(st.session_state.devices)} devices")
            else:
                st.warning("No devices found or error occurred")

with col2:
    if st.session_state.connected:
        if st.button("Disconnect", use_container_width=True):
            with st.spinner("Disconnecting..."):
                if disconnect_device():
                    st.success("Device disconnected")
                    st.rerun()
    else:
        st.button("Connect", disabled=True, use_container_width=True)

# Display available devices if any
if not st.session_state.connected and st.session_state.devices:
    st.subheader("Available Devices")
    
    for i, device in enumerate(st.session_state.devices):
        col1, col2 = st.columns([3, 1])
        with col1:
            device_name = device.get('name', 'Unknown')
            device_address = device.get('address', '')
            st.write(f"**{device_name}** ({device_address})")
        with col2:
            if st.button("Connect", key=f"connect_{i}"):
                with st.spinner(f"Connecting to {device_name}..."):
                    if connect_to_device(device_address):
                        st.success(f"Connected to {device_name}")
                        st.rerun()

# Display connection status
if st.session_state.connected:
    st.success(f"Connected to device: {st.session_state.device_address}")
    
    # WebSocket status indicator
    if st.session_state.ws_active:
        st.info("Live data stream active")
    else:
        st.warning("Live data stream inactive")
    
    # Data display section
    st.header("Real-time Data")
    
    # Display metrics
    col1, col2 = st.columns(2)
    with col1:
        st.metric("Heart Rate", st.session_state.latest_heart_rate)
    with col2:
        st.metric("Steps", st.session_state.latest_steps)
    
    # Auto-refresh button
    if st.button("Refresh Data"):
        st.rerun()
    
    # Add automatic refresh using an empty placeholder and time
    refresh_placeholder = st.empty()
    with refresh_placeholder.container():
        if st.session_state.ws_active:
            st.caption(f"Next automatic refresh in: {5} seconds")
    
    # Charts
    if not st.session_state.data_history.empty:
        # Heart Rate Chart
        st.subheader("Heart Rate Over Time")
        fig_hr = px.line(
            st.session_state.data_history,
            x='timestamp',
            y='heart_rate',
            title='Heart Rate (BPM)'
        )
        fig_hr.update_layout(height=300)
        st.plotly_chart(fig_hr, use_container_width=True)
        
        # Steps Chart
        st.subheader("Steps Over Time")
        fig_steps = px.line(
            st.session_state.data_history,
            x='timestamp',
            y='steps',
            title='Step Count'
        )
        fig_steps.update_layout(height=300)
        st.plotly_chart(fig_steps, use_container_width=True)
    else:
        st.info("No data recorded yet. The charts will appear once data is received.")
    
    # Data export section
    st.header("Data Export")
    
    if not st.session_state.data_history.empty:
        # Download button for CSV
        csv = st.session_state.data_history.to_csv(index=False)
        st.download_button(
            label="Download data as CSV",
            data=csv,
            file_name="smartwatch_data.csv",
            mime="text/csv",
        )
        
        # Display raw data table
        with st.expander("View Raw Data"):
            st.dataframe(st.session_state.data_history)
    else:
        st.info("No data collected yet")

    # Set up automatic refresh if connected
    if st.session_state.ws_active:
        time.sleep(3)  # Wait a few seconds
        st.rerun()

# Display instructions
with st.expander("How to use this application"):
    st.markdown("""
    ### Instructions
    
    1. Click **Scan for Devices** to find nearby Bluetooth devices
    2. Select your smartwatch from the list and click **Connect**
    3. Once connected, you'll see real-time heart rate and step count data
    4. The data will automatically refresh and plot on graphs
    5. You can download the collected data as a CSV file
    6. Click **Disconnect** when you're done
    
    ### Troubleshooting
    
    - Make sure your smartwatch is in pairing mode
    - Ensure Bluetooth is enabled on your computer
    - If you're having connection issues, try restarting your smartwatch
    - Some smartwatches may require pairing in your operating system's Bluetooth settings first
    """)

# Footer
st.markdown("---")
st.caption("Smartwatch Data Collection App | Built with FastAPI and Streamlit")