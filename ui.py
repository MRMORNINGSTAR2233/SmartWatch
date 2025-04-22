# # streamlit_app.py - Thread-Safe Streamlit UI for Smartwatch Data Collection

# import streamlit as st
# import requests
# import pandas as pd
# import plotly.express as px
# import time
# import json
# import threading
# import queue
# import websocket
# import logging
# from datetime import datetime

# # Configure logging
# logging.basicConfig(level=logging.INFO)
# logger = logging.getLogger(__name__)

# # API Configuration
# API_BASE_URL = "http://localhost:8000"
# WS_URL = "ws://localhost:8000/ws"

# # Create a global queue for thread-safe communication between WebSocket and main thread
# ws_data_queue = queue.Queue()

# # Initialize session state variables
# if 'data_queue' not in st.session_state:
#     st.session_state.data_queue = queue.Queue()
# if 'connected' not in st.session_state:
#     st.session_state.connected = False
# if 'device_address' not in st.session_state:
#     st.session_state.device_address = None
# if 'data_history' not in st.session_state:
#     st.session_state.data_history = pd.DataFrame(columns=['timestamp', 'heart_rate', 'steps'])
# if 'devices' not in st.session_state:
#     st.session_state.devices = []
# if 'latest_heart_rate' not in st.session_state:
#     st.session_state.latest_heart_rate = "N/A"
# if 'latest_steps' not in st.session_state:
#     st.session_state.latest_steps = "N/A"
# if 'ws_active' not in st.session_state:
#     st.session_state.ws_active = False

# # Global WebSocket object (outside of session state)
# ws_app = None
# ws_thread = None

# # Function to scan for devices
# def scan_devices():
#     try:
#         response = requests.get(f"{API_BASE_URL}/devices")
#         if response.status_code == 200:
#             st.session_state.devices = response.json()
#             return True
#         else:
#             st.error(f"Error scanning devices: {response.text}")
#             return False
#     except Exception as e:
#         st.error(f"Connection error: {e}")
#         return False

# # Function to connect to a device
# def connect_to_device(device_address):
#     try:
#         response = requests.post(f"{API_BASE_URL}/connect/{device_address}")
#         if response.status_code == 200:
#             st.session_state.connected = True
#             st.session_state.device_address = device_address
            
#             # Start WebSocket connection in a thread-safe way
#             start_websocket_connection()
            
#             return True
#         else:
#             st.error(f"Error connecting to device: {response.text}")
#             return False
#     except Exception as e:
#         st.error(f"Connection error: {e}")
#         return False

# # Function to disconnect from device
# def disconnect_device():
#     try:
#         # Stop WebSocket first
#         stop_websocket_connection()
        
#         response = requests.get(f"{API_BASE_URL}/disconnect")
#         if response.status_code == 200:
#             st.session_state.connected = False
#             st.session_state.device_address = None
#             return True
#         else:
#             st.error(f"Error disconnecting device: {response.text}")
#             return False
#     except Exception as e:
#         st.error(f"Disconnection error: {e}")
#         return False

# # WebSocket message handler - puts data in queue instead of accessing session state
# def on_message(ws, message):
#     try:
#         data = json.loads(message)
#         logger.info(f"Received data: {data}")
        
#         # Format the data
#         new_data = {
#             'timestamp': datetime.fromtimestamp(data.get('timestamp', time.time())),
#             'heart_rate': data.get('heart_rate'),
#             'steps': data.get('steps')
#         }
        
#         # Instead of accessing session state directly, use a global variable
#         # or pass data through a thread-safe mechanism
#         global ws_data_queue
#         if not hasattr(globals(), 'ws_data_queue'):
#             ws_data_queue = queue.Queue()
        
#         ws_data_queue.put(new_data)
        
#     except Exception as e:
#         logger.error(f"Error processing WebSocket message: {e}")

# def on_error(ws, error):
#     logger.error(f"WebSocket error: {error}")

# def on_close(ws, close_status_code, close_msg):
#     logger.info(f"WebSocket connection closed: {close_msg}")
#     st.session_state.ws_active = False

# def on_open(ws):
#     logger.info("WebSocket connection established")
#     st.session_state.ws_active = True

# def start_websocket_connection():
#     """Start WebSocket connection in a separate thread"""
#     global ws_app, ws_thread
    
#     # Make sure any existing connection is closed
#     stop_websocket_connection()
    
#     try:
#         # Create new connection
#         ws_app = websocket.WebSocketApp(
#             WS_URL,
#             on_open=on_open,
#             on_message=on_message,
#             on_error=on_error,
#             on_close=on_close
#         )
        
#         # Start in a new thread
#         ws_thread = threading.Thread(target=ws_app.run_forever)
#         ws_thread.daemon = True
#         ws_thread.start()
        
#         st.session_state.ws_active = True
#         logger.info("WebSocket thread started")
        
#     except Exception as e:
#         logger.error(f"Failed to start WebSocket: {e}")

# def stop_websocket_connection():
#     """Safely stop WebSocket connection"""
#     global ws_app, ws_thread
    
#     try:
#         if ws_app:
#             ws_app.close()
        
#         # Wait for thread to finish if it exists
#         if ws_thread and ws_thread.is_alive():
#             ws_thread.join(timeout=1.0)
            
#         ws_app = None
#         ws_thread = None
#         st.session_state.ws_active = False
        
#     except Exception as e:
#         logger.error(f"Error stopping WebSocket: {e}")

# # Process any data that arrived via the queue
# def process_queued_data():
#     """Process any data that arrived in the queue"""
#     global ws_data_queue
#     queue_size = ws_data_queue.qsize()
#     update_happened = False
    
#     # Log the current queue size
#     if queue_size > 0:
#         logger.info(f"Processing {queue_size} queued data items")
    
#     for _ in range(queue_size):
#         try:
#             # Get data from the queue (non-blocking)
#             new_data = ws_data_queue.get_nowait()
            
#             # Update the latest values
#             if 'heart_rate' in new_data and new_data['heart_rate'] is not None:
#                 st.session_state.latest_heart_rate = f"{new_data['heart_rate']} BPM"
#                 logger.info(f"Updated heart rate: {st.session_state.latest_heart_rate}")
            
#             if 'steps' in new_data and new_data['steps'] is not None:
#                 st.session_state.latest_steps = f"{new_data['steps']}"
#                 logger.info(f"Updated steps: {st.session_state.latest_steps}")
            
#             # Add to the history dataframe
#             new_row = pd.DataFrame([new_data])
#             st.session_state.data_history = pd.concat([st.session_state.data_history, new_row], ignore_index=True)
            
#             # Keep last 100 records to avoid memory issues
#             if len(st.session_state.data_history) > 100:
#                 st.session_state.data_history = st.session_state.data_history.iloc[-100:]
                
#             # Mark that we had an update
#             update_happened = True
#             ws_data_queue.task_done()
            
#         except queue.Empty:
#             break
    
#     return update_happened

# # Main Streamlit UI
# st.title("Smartwatch Data Collection")
# st.write("Connect to your smartwatch and monitor heart rate and steps in real-time.")

# # Check for new data at each rerun
# if st.session_state.connected:
#     process_queued_data()

# # Device connection section
# st.header("Device Connection")

# col1, col2 = st.columns(2)

# with col1:
#     if st.button("Scan for Devices", use_container_width=True):
#         with st.spinner("Scanning..."):
#             if scan_devices():
#                 st.success(f"Found {len(st.session_state.devices)} devices")
#             else:
#                 st.warning("No devices found or error occurred")

# with col2:
#     if st.session_state.connected:
#         if st.button("Disconnect", use_container_width=True):
#             with st.spinner("Disconnecting..."):
#                 if disconnect_device():
#                     st.success("Device disconnected")
#                     st.rerun()
#     else:
#         st.button("Connect", disabled=True, use_container_width=True)

# # Display available devices if any
# if not st.session_state.connected and st.session_state.devices:
#     st.subheader("Available Devices")
    
#     for i, device in enumerate(st.session_state.devices):
#         col1, col2 = st.columns([3, 1])
#         with col1:
#             device_name = device.get('name', 'Unknown')
#             device_address = device.get('address', '')
#             st.write(f"**{device_name}** ({device_address})")
#         with col2:
#             if st.button("Connect", key=f"connect_{i}"):
#                 with st.spinner(f"Connecting to {device_name}..."):
#                     if connect_to_device(device_address):
#                         st.success(f"Connected to {device_name}")
#                         st.rerun()

# # Display connection status
# if st.session_state.connected:
#     st.success(f"Connected to device: {st.session_state.device_address}")
    
#     # WebSocket status indicator
#     if st.session_state.ws_active:
#         st.info("Live data stream active")
#     else:
#         st.warning("Live data stream inactive")
    
#     # Data display section
#     st.header("Real-time Data")
    
#     # Display metrics
#     col1, col2 = st.columns(2)
#     with col1:
#         st.metric("Heart Rate", st.session_state.latest_heart_rate)
#     with col2:
#         st.metric("Steps", st.session_state.latest_steps)
    
#     # Auto-refresh button
#     if st.button("Refresh Data"):
#         st.rerun()
    
#     # Add automatic refresh using an empty placeholder and time
#     refresh_placeholder = st.empty()
#     with refresh_placeholder.container():
#         if st.session_state.ws_active:
#             st.caption(f"Next automatic refresh in: {5} seconds")
    
#     # Charts
#     if not st.session_state.data_history.empty:
#         # Heart Rate Chart
#         st.subheader("Heart Rate Over Time")
#         fig_hr = px.line(
#             st.session_state.data_history,
#             x='timestamp',
#             y='heart_rate',
#             title='Heart Rate (BPM)'
#         )
#         fig_hr.update_layout(height=300)
#         st.plotly_chart(fig_hr, use_container_width=True)
        
#         # Steps Chart
#         st.subheader("Steps Over Time")
#         fig_steps = px.line(
#             st.session_state.data_history,
#             x='timestamp',
#             y='steps',
#             title='Step Count'
#         )
#         fig_steps.update_layout(height=300)
#         st.plotly_chart(fig_steps, use_container_width=True)
#     else:
#         st.info("No data recorded yet. The charts will appear once data is received.")
    
#     # Data export section
#     st.header("Data Export")
    
#     if not st.session_state.data_history.empty:
#         # Download button for CSV
#         csv = st.session_state.data_history.to_csv(index=False)
#         st.download_button(
#             label="Download data as CSV",
#             data=csv,
#             file_name="smartwatch_data.csv",
#             mime="text/csv",
#         )
        
#         # Display raw data table
#         with st.expander("View Raw Data"):
#             st.dataframe(st.session_state.data_history)
#     else:
#         st.info("No data collected yet")

#     # Set up automatic refresh if connected
#     if st.session_state.ws_active:
#         time.sleep(3)  # Wait a few seconds
#         st.rerun()

# # Display instructions
# with st.expander("How to use this application"):
#     st.markdown("""
#     ### Instructions
    
#     1. Click **Scan for Devices** to find nearby Bluetooth devices
#     2. Select your smartwatch from the list and click **Connect**
#     3. Once connected, you'll see real-time heart rate and step count data
#     4. The data will automatically refresh and plot on graphs
#     5. You can download the collected data as a CSV file
#     6. Click **Disconnect** when you're done
    
#     ### Troubleshooting
    
#     - Make sure your smartwatch is in pairing mode
#     - Ensure Bluetooth is enabled on your computer
#     - If you're having connection issues, try restarting your smartwatch
#     - Some smartwatches may require pairing in your operating system's Bluetooth settings first
#     """)

# # Footer
# st.markdown("---")
# st.caption("Smartwatch Data Collection App | Built with FastAPI and Streamlit")



import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import json
import time
import websocket
import threading
import logging
from datetime import datetime, timedelta
import sqlite3
from typing import Dict, List, Optional
import os

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("smartwatch.frontend")

# Configuration
API_URL = os.getenv("API_URL", "http://localhost:8000")
WS_URL = os.getenv("WS_URL", "ws://localhost:8000/ws")
DB_PATH = os.getenv("DB_PATH", "smartwatch_data.db")

# Initialize session state
if 'connected' not in st.session_state:
    st.session_state.connected = False
if 'device_address' not in st.session_state:
    st.session_state.device_address = None
if 'data' not in st.session_state:
    st.session_state.data = {
        'heart_rate': [],
        'steps': [],
        'accelerometer_x': [],
        'accelerometer_y': [],
        'accelerometer_z': [],
        'timestamp': []
    }
if 'websocket' not in st.session_state:
    st.session_state.websocket = None
if 'alerts' not in st.session_state:
    st.session_state.alerts = []
if 'alert_config' not in st.session_state:
    st.session_state.alert_config = {
        'enabled': True,
        'threshold': 0.75,
        'contacts': []
    }
if 'last_alert_time' not in st.session_state:
    st.session_state.last_alert_time = None
if 'ws_connected' not in st.session_state:
    st.session_state.ws_connected = False

# WebSocket handlers
def on_ws_message(ws, message):
    data = json.loads(message)
    msg_type = data.get('message_type', '')
    if msg_type == 'data_update':
        # Sensor data update
        ts = data.get('timestamp', time.time())
        # Heart rate
        hr = data.get('heart_rate')
        if hr is not None:
            st.session_state.data['heart_rate'].append(hr)
            st.session_state.data['timestamp'].append(ts)
            if len(st.session_state.data['heart_rate']) > 100:
                st.session_state.data['heart_rate'] = st.session_state.data['heart_rate'][-100:]
                st.session_state.data['timestamp'] = st.session_state.data['timestamp'][-100:]
        # Steps
        steps = data.get('steps')
        if steps is not None:
            st.session_state.data['steps'].append(steps)
            if len(st.session_state.data['steps']) > 100:
                st.session_state.data['steps'] = st.session_state.data['steps'][-100:]
        # Accelerometer
        if all(data.get(k) is not None for k in ['accelerometer_x','accelerometer_y','accelerometer_z']):
            for axis in ['accelerometer_x','accelerometer_y','accelerometer_z']:
                st.session_state.data[axis].append(data[axis])
                if len(st.session_state.data[axis]) > 100:
                    st.session_state.data[axis] = st.session_state.data[axis][-100:]
    elif msg_type == 'fall_alert':
        alert = {
            'timestamp': data.get('timestamp', time.time()),
            'confidence': data.get('confidence', 0),
            'device_id': data.get('device_id', 'unknown'),
            'acknowledged': False,
            'id': len(st.session_state.alerts) + 1
        }
        st.session_state.alerts.append(alert)
        st.session_state.last_alert_time = datetime.now()
        logger.warning(f"FALL DETECTED! Confidence: {alert['confidence']:.2f}")
    elif msg_type in ('status_update','status_response'):
        st.session_state.connected = data.get('device_connected', False)
        st.session_state.device_address = data.get('device_address')
        st.session_state.alert_config['enabled'] = data.get('fall_detection_enabled', True)
    elif msg_type == 'config_updated':
        if 'config' in data:
            st.session_state.alert_config = data['config']


def on_ws_error(ws, error):
    logger.error(f"WebSocket error: {error}")
    st.session_state.ws_connected = False


def on_ws_close(ws, code, msg):
    logger.info(f"WebSocket closed: {code} - {msg}")
    st.session_state.ws_connected = False


def on_ws_open(ws):
    logger.info("WebSocket connection established")
    st.session_state.ws_connected = True
    # Request initial status
    ws.send(json.dumps({"command": "get_status"}))


def connect_websocket():
    if st.session_state.websocket:
        try:
            st.session_state.websocket.close()
        except:
            pass
    ws = websocket.WebSocketApp(
        WS_URL,
        on_open=on_ws_open,
        on_message=on_ws_message,
        on_error=on_ws_error,
        on_close=on_ws_close
    )
    st.session_state.websocket = ws
    thread = threading.Thread(target=ws.run_forever, daemon=True)
    thread.start()

# Database queries
def fetch_historical_data(start_time=None, end_time=None, limit=1000):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        query = "SELECT * FROM device_data WHERE 1=1"
        params = []
        if start_time:
            query += " AND timestamp >= ?"
            params.append(start_time)
        if end_time:
            query += " AND timestamp <= ?"
            params.append(end_time)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        df = pd.DataFrame([dict(row) for row in rows])
        if not df.empty:
            df['datetime'] = pd.to_datetime(df['timestamp'], unit='s')
            df = df.sort_values('timestamp')
        return df
    except Exception as e:
        logger.error(f"DB query error: {e}")
        return pd.DataFrame()


def fetch_alerts(include_acknowledged=True, limit=100):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        query = "SELECT * FROM fall_alerts WHERE 1=1"
        params = []
        if not include_acknowledged:
            query += " AND acknowledged=0"
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Alerts query error: {e}")
        return []


def acknowledge_alert(alert_id):
    try:
        resp = requests.post(f"{API_URL}/acknowledge-alert/{alert_id}")
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Ack alert error: {e}")
        return False

# Visualization helpers
def create_heart_rate_chart(df):
    if df.empty: return go.Figure()
    fig = px.line(df, x='datetime', y='heart_rate', title='Heart Rate')
    fig.update_layout(height=300, margin=dict(l=20,r=20,t=30,b=20))
    return fig


def create_step_count_chart(df):
    if df.empty: return go.Figure()
    fig = px.line(df, x='datetime', y='steps', title='Step Count')
    fig.update_layout(height=300, margin=dict(l=20,r=20,t=30,b=20))
    return fig


def create_accelerometer_chart(df):
    if df.empty: return go.Figure()
    df['magnitude'] = np.sqrt(df['accelerometer_x']**2 + df['accelerometer_y']**2 + df['accelerometer_z']**2)
    fig = make_subplots(rows=1, cols=1)
    for axis,color in zip(['accelerometer_x','accelerometer_y','accelerometer_z','magnitude'], ['red','green','blue','purple']):
        fig.add_trace(go.Scatter(x=df['datetime'], y=df[axis], name=axis, line=dict(color=color)), row=1, col=1)
    fig.update_layout(height=300, margin=dict(l=20,r=20,t=30,b=20))
    return fig


def format_ts(ts):
    return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')

# Main app

def main():
    st.title("Smartwatch Fall Detection System")
    # Connect WS
    if not st.session_state.ws_connected:
        connect_websocket()
    # Sidebar
    with st.sidebar:
        st.header("Device Connection")
        if st.session_state.connected:
            st.success(f"Connected: {st.session_state.device_address}")
            if st.button("Disconnect"):
                disconnect_device()
                st.rerun()
        else:
            if st.button("Scan for Devices"):
                devices = requests.get(f"{API_URL}/devices").json()
                st.session_state.devices = devices
            if 'devices' in st.session_state:
                opts = {f"{d['name']}({d['address']})":d['address'] for d in st.session_state.devices}
                sel = st.selectbox("Select Device", list(opts.keys()))
                if st.button("Connect"):
                    connect_device(opts[sel])
                    st.rerun()
        st.header("Fall Settings")
        en = st.checkbox("Enable", value=st.session_state.alert_config['enabled'])
        th = st.slider("Threshold",0.1,1.0,value=st.session_state.alert_config['threshold'],step=0.05)
        contacts = st.text_input("Contacts", ", ".join(st.session_state.alert_config['contacts']))
        if st.button("Update"): update_alert_config({'enabled':en,'threshold':th,'contacts':[c.strip() for c in contacts.split(',')]})
    # Tabs
    tab1,tab2,tab3 = st.tabs(["Live","Alerts","History"])
    with tab1:
        st.header("Live Data")
        if not st.session_state.connected:
            st.warning("Connect device in sidebar")
        else:
            col1,col2,col3 = st.columns(3)
            col1.metric("HR", f"{st.session_state.data['heart_rate'][-1] if st.session_state.data['heart_rate'] else 'N/A'} BPM")
            col2.metric("Steps", st.session_state.data['steps'][-1] if st.session_state.data['steps'] else 'N/A')
            if all(st.session_state.data[k] for k in ['accelerometer_x','accelerometer_y','accelerometer_z']):
                mag = np.sqrt(st.session_state.data['accelerometer_x'][-1]**2 + st.session_state.data['accelerometer_y'][-1]**2 + st.session_state.data['accelerometer_z'][-1]**2)
                col3.metric("Motion", f"{mag:.2f} g")
            else:
                col3.metric("Motion", "N/A")
            df_live = pd.DataFrame({
                'datetime':[datetime.fromtimestamp(ts) for ts in st.session_state.data['timestamp']],
                'heart_rate':st.session_state.data['heart_rate'],
                'steps':st.session_state.data['steps'],
                'accelerometer_x':st.session_state.data['accelerometer_x'],
                'accelerometer_y':st.session_state.data['accelerometer_y'],
                'accelerometer_z':st.session_state.data['accelerometer_z']
            })
            st.plotly_chart(create_heart_rate_chart(df_live),use_container_width=True)
            st.plotly_chart(create_step_count_chart(df_live),use_container_width=True)
            st.plotly_chart(create_accelerometer_chart(df_live),use_container_width=True)
    with tab2:
        st.header("Fall Alerts")
        if st.session_state.alerts:
            for alert in st.session_state.alerts:
                st.warning(f"Fall @ {format_ts(alert['timestamp'])} (Conf: {alert['confidence']:.2f})")
            if st.button("Acknowledge All"):
                for a in st.session_state.alerts:
                    acknowledge_alert(a['id'])
                st.session_state.alerts.clear()
                st.rerun()
        else:
            st.info("No alerts")
        hist_alerts = pd.DataFrame(fetch_alerts())
        if not hist_alerts.empty:
            hist_alerts['datetime'] = pd.to_datetime(hist_alerts['timestamp'],unit='s')
            st.dataframe(hist_alerts.rename(columns={'id':'ID','confidence':'Conf','device_id':'Device','acknowledged':'Ack','datetime':'Time'}))
    with tab3:
        st.header("Historical Data")
        start = st.date_input("Start", value=datetime.now()-timedelta(days=7))
        end = st.date_input("End", value=datetime.now())
        df_hist = fetch_historical_data(datetime.combine(start,datetime.min.time()).timestamp(),
                                        datetime.combine(end,datetime.max.time()).timestamp())
        if df_hist.empty:
            st.info("No data in range")
        else:
            df_hist['datetime'] = pd.to_datetime(df_hist['timestamp'],unit='s')
            st.plotly_chart(create_heart_rate_chart(df_hist),use_container_width=True)
            st.plotly_chart(create_step_count_chart(df_hist),use_container_width=True)
            st.plotly_chart(create_accelerometer_chart(df_hist),use_container_width=True)
            st.download_button("Download CSV",data=df_hist.to_csv(index=False),file_name="history.csv")

if __name__ == "__main__":
    main()
