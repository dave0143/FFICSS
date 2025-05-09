import paho.mqtt.client as mqtt
import json
import threading
import time
from typing import Callable, Dict, Any

class MQTTClient:
    """MQTT Client for gimbal control"""
    def __init__(self, config: Dict[str, Any], on_message_callback: Callable = None):
        """
        Initialize MQTT client
        :param config: Configuration dictionary
        :param on_message_callback: Callback function for received messages
        """
        self.config = config.get("mqtt", {})
        self.enabled = self.config.get("enabled", False)
        self.on_message_callback = on_message_callback
        
        if not self.enabled:
            return
            
        # Initialize MQTT client
        self.client = mqtt.Client(
            client_id=self.config.get("client_id", "gimbal_control"),
            clean_session=True
        )
        
        # Set credentials if provided
        username = self.config.get("username")
        password = self.config.get("password")
        if username and password:
            self.client.username_pw_set(username, password)
        
        # Set callbacks
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        
        # Set connection parameters
        self.broker = self.config.get("broker", "localhost")
        self.port = self.config.get("port", 1883)
        self.keepalive = self.config.get("keepalive", 60)
        self.qos = self.config.get("qos", 1)
        
        # Get topics
        self.topics = self.config.get("topics", {})
        self.control_topic = self.topics.get("control", "fire/XY_target")
        self.status_topic = self.topics.get("status", "gimbal/status")
        
        # Connection status
        self.connected = False
        self.reconnect_thread = None
        self.running = False
    
    def _on_connect(self, client, userdata, flags, rc):
        """Callback for when the client connects to the broker"""
        if rc == 0:
            print("MQTT: Connected to broker")
            self.connected = True
            # Subscribe to control topic
            self.client.subscribe(self.control_topic, qos=self.qos)
            print(f"MQTT: Subscribed to {self.control_topic}")
        else:
            print(f"MQTT: Connection failed with code {rc}")
    
    def _on_disconnect(self, client, userdata, rc):
        """Callback for when the client disconnects from the broker"""
        print("MQTT: Disconnected from broker")
        self.connected = False
        if rc != 0:
            print(f"MQTT: Unexpected disconnection (rc={rc})")
            self._start_reconnect()
    
    def _on_message(self, client, userdata, msg):
        """Callback for when a message is received"""
        try:
            payload = json.loads(msg.payload.decode())
            print(f"MQTT: Received message on {msg.topic}: {payload}")
            
            if self.on_message_callback:
                self.on_message_callback(msg.topic, payload)
                
        except Exception as e:
            print(f"MQTT: Error processing message: {e}")
    
    def _start_reconnect(self):
        """Start reconnection thread"""
        if self.reconnect_thread and self.reconnect_thread.is_alive():
            return
            
        self.reconnect_thread = threading.Thread(target=self._reconnect_loop)
        self.reconnect_thread.daemon = True
        self.reconnect_thread.start()
    
    def _reconnect_loop(self):
        """Reconnection loop"""
        while not self.connected and self.running:
            try:
                print("MQTT: Attempting to reconnect...")
                self.connect()
                time.sleep(5)  # Wait before next attempt
            except Exception as e:
                print(f"MQTT: Reconnection failed: {e}")
                time.sleep(5)
    
    def connect(self):
        """Connect to MQTT broker"""
        if not self.enabled:
            return False
            
        try:
            self.running = True
            self.client.connect(self.broker, self.port, self.keepalive)
            self.client.loop_start()
            return True
        except Exception as e:
            print(f"MQTT: Connection error: {e}")
            return False
    
    def disconnect(self):
        """Disconnect from MQTT broker"""
        if not self.enabled:
            return
            
        self.running = False
        if self.reconnect_thread and self.reconnect_thread.is_alive():
            self.reconnect_thread.join(timeout=1.0)
            
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
    
    def publish_status(self, status: Dict[str, Any]):
        """
        Publish status message
        :param status: Status data to publish
        """
        if not self.enabled or not self.connected:
            return
            
        try:
            payload = json.dumps(status)
            self.client.publish(self.status_topic, payload, qos=self.qos)
        except Exception as e:
            print(f"MQTT: Error publishing status: {e}")
    
    def is_connected(self) -> bool:
        """Check if client is connected to broker"""
        return self.connected 
