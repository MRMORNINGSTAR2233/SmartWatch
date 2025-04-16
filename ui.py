# streamlit_app.py - Streamlit UI for Smartwatch Data Collection

import streamlit as st
import requests
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import time
import json
import asyncio
import threading
import nest_asyncio
from websocket import create_connection
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# API Configuration
API_BASE_URL = "http://localhost:8000"
WS_URL = "ws://localhost:8000/ws"

# Initialize session state variables
if 'connected' not in st.session_state:
    st.session_state.connected = False
if 'device_address' not in st.session_state:
    st.session_state.device_address = None
if 'data_history' not in st.session_state:
    st.session_state.data_history = pd.DataFrame(columns=['timestamp', 'heart_rate', 'steps'])
if 'ws_connection' not in st.session_state:
    st.session_state.ws_connection = None

# Apply nest_asyncio to make async work in Streamlit
nest_asyncio.apply()

# Function to scan for devices
def scan_devices():
    try:
        response = requests.get(f"{API_BASE_URL}/devices")
        if response.status_code == 200:
            devices = response.json()
            return devices
        else:
            st.error(f"Error scanning devices: {response.text}")
            return []
    except Exception as e:
        st.error(f"Connection error: {e}")
        return []

# Function to connect to a device
def connect_to_device(device_address):
    try:
        response = requests.post(f"{API_BASE_URL}/connect/{device_address}")
        if response.status_code == 200:
            st.session_state.connected = True
            st.session_state.device_address = device_address
            
            # Start WebSocket connection
            setup_websocket_connection()
            
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
        if st.session_state.ws_connection:
            st.session_state.ws_connection.close()
            st.session_state.ws_connection = None
        
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

# Function to get the latest data
def get_latest_data():
    try:
        response = requests.get(f"{API_BASE_URL}/data")
        if response.status_code == 200:
            return response.json()
        else:
            st.error(f"Error getting data: {response.text}")
            return None
    except Exception as e:
        st.error(f"Data retrieval error: {e}")
        return None

# Function to update data in a separate thread
def websocket_thread():
    try:
        while st.session_state.connected and st.session_state.ws_connection:
            try:
                message = st.session_state.ws_connection.recv()
                if message:
                    data = json.loads(message)
                    process_new_data(data)
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                time.sleep(1)  # Prevent tight loop on error
    except Exception as e:
        logger.error(f"WebSocket thread error: {e}")

# Setup WebSocket connection
def setup_websocket_connection():
    try:
        # Close existing connection if any
        if st.session_state.ws_connection:
            st.session_state.ws_connection.close()
        
        # Create new connection
        ws = create_connection(WS_URL)
        st.session_state.ws_connection = ws
        
        # Start listener thread
        thread = threading.Thread(target=websocket_thread, daemon=True)
        thread.start()
        
        return True
    except Exception as e:
        st.error(f"WebSocket connection error: {e}")
        return False

# Process new data received from WebSocket
def process_new_data(data):
    if not data:
        return
    
    # Convert timestamp to datetime for better display
    import datetime
    dt = datetime.datetime.fromtimestamp(data['timestamp'])
    
    # Create new row for DataFrame
    new_data = pd.DataFrame([{
        'timestamp': dt,
        'heart_rate': data['heart_rate'],
        'steps': data['steps']
    }])
    
    # Append to history (use lock if needed for thread safety)
    st.session_state.data_history = pd.concat([st.session_state.data_history, new_data])
    
    # Keep only the last 100 records to avoid memory issues
    if len(st.session_state.data_history) > 100:
        st.session_state.data_history = st.session_state.data_history.iloc[-100:]

# App UI
st.title("Smartwatch Data Collection")
st.write("Connect to your smartwatch and monitor heart rate and steps in real-time.")

# Device connection section
st.header("Device Connection")

col1, col2 = st.columns(2)

with col1:
    if st.button("Scan for Devices", use_container_width=True):
        with st.spinner("Scanning..."):
            devices = scan_devices()
            if devices:
                st.session_state.devices = devices
                st.success(f"Found {len(devices)} devices")
            else:
                st.warning("No devices found")

with col2:
    if st.session_state.connected:
        if st.button("Disconnect", use_container_width=True):
            with st.spinner("Disconnecting..."):
                if disconnect_device():
                    st.success("Device disconnected")
    else:
        st.button("Connect", disabled=True, use_container_width=True)

# Display available devices if any
if not st.session_state.connected and 'devices' in st.session_state and st.session_state.devices:
    st.subheader("Available Devices")
    
    for device in st.session_state.devices:
        col1, col2 = st.columns([3, 1])
        with col1:
            device_name = device.get('name', 'Unknown')
            device_address = device.get('address', '')
            st.write(f"**{device_name}** ({device_address})")
        with col2:
            if st.button("Connect", key=f"connect_{device_address}"):
                with st.spinner(f"Connecting to {device_name}..."):
                    if connect_to_device(device_address):
                        st.success(f"Connected to {device_name}")
                        st.experimental_rerun()

# Display connected device info if connected
if st.session_state.connected:
    st.success(f"Connected to device: {st.session_state.device_address}")
    
    # Data display section
    st.header("Real-time Data")
    
    # Create placeholder for real-time metrics
    col1, col2 = st.columns(2)
    
    with col1:
        heart_rate_container = st.container()
    
    with col2:
        steps_container = st.container()
    
    # Create placeholders for charts
    heart_rate_chart = st.empty()
    steps_chart = st.empty()
    
    # Function to update the UI (called by a separate thread)
    def update_ui():
        while st.session_state.connected:
            try:
                if not st.session_state.data_history.empty:
                    # Get the most recent data
                    latest_data = st.session_state.data_history.iloc[-1]
                    
                    # Update metrics
                    with heart_rate_container:
                        st.metric("Heart Rate", f"{latest_data['heart_rate']} BPM" if pd.notna(latest_data['heart_rate']) else "N/A")
                    
                    with steps_container:
                        st.metric("Steps", f"{latest_data['steps']}" if pd.notna(latest_data['steps']) else "N/A")
                    
                    # Update charts if we have enough data
                    if len(st.session_state.data_history) > 1:
                        # Heart Rate Chart
                        fig_hr = px.line(
                            st.session_state.data_history,
                            x='timestamp',
                            y='heart_rate',
                            title='Heart Rate Over Time'
                        )
                        fig_hr.update_layout(height=300)
                        heart_rate_chart.plotly_chart(fig_hr, use_container_width=True)
                        
                        # Steps Chart
                        fig_steps = px.line(
                            st.session_state.data_history,
                            x='timestamp',
                            y='steps',
                            title='Steps Over Time'
                        )
                        fig_steps.update_layout(height=300)
                        steps_chart.plotly_chart(fig_steps, use_container_width=True)
                
                # Update every second
                time.sleep(1)
            
            except Exception as e:
                logger.error(f"UI update error: {e}")
                time.sleep(1)
    
    # Start UI update thread if not already running
    if 'ui_thread' not in st.session_state or not st.session_state.ui_thread.is_alive():
        st.session_state.ui_thread = threading.Thread(target=update_ui, daemon=True)
        st.session_state.ui_thread.start()
    
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

# Display instructions
with st.expander("How to use this application"):
    st.markdown("""
    ### Instructions
    
    1. Click **Scan for Devices** to find nearby Bluetooth devices
    2. Select your smartwatch from the list and click **Connect**
    3. Once connected, you'll see real-time heart rate and step count data
    4. The data will be plotted on graphs automatically
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