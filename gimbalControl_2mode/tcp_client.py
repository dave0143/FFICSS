import socket
import threading
import time
from display import TargetDataDisplay
from video import RTSPStream
import numpy as np

class TCPClient:
    def __init__(self, config):
        """
        Initialize TCP client
        :param config: Configuration dictionary with host, port, etc.
        """
        self.host = config.get("tcp_host", "192.168.144.200")
        self.port = config.get("tcp_port", 2000)
        self.client_socket = None
        self.running = False
        self.receiver_thread = None
        self.target_display = TargetDataDisplay()
        self.rtsp_stream = None
        self._closing = False
        
        # Initialize RTSP stream
        auto_rtsp = config.get("auto_rtsp")
        if auto_rtsp:
            self.rtsp_stream = RTSPStream(auto_rtsp)
            self.target_display.set_rtsp_stream(self.rtsp_stream)
        
        # Set ESC callback function
        self.target_display.set_esc_callback(self.close)
    
    def connect(self):
        """Connect to server"""
        try:
            self.client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.client_socket.settimeout(10)  # Only for connection phase
            
            print(f"Connecting to {self.host}:{self.port}...")
            self.client_socket.connect((self.host, self.port))
            # Set socket to non-blocking after connection
            self.client_socket.settimeout(None)
            print(f"Successfully connected to {self.host}:{self.port}")
            return True
        
        except ConnectionRefusedError:
            print(f"Connection refused, please verify if server {self.host}:{self.port} is running")
        except socket.timeout:
            print("Connection timeout")
        except Exception as e:
            print(f"Connection error: {e}")
        
        return False
    
    def start_receiver(self):
        """Start the receiver thread"""
        self.running = True
        self.receiver_thread = threading.Thread(target=self._receive_loop)
        self.receiver_thread.daemon = True
        self.receiver_thread.start()
        print("Data receiver thread started, waiting for server messages...")
        
        # Start target data display
        self.target_display.start_display()
        
        # Start RTSP stream
        if self.rtsp_stream:
            self.rtsp_stream.start_stream()
    
    def parse_target_data(self, data):
        """
        Parse target data from received packet
        :param data: Raw packet data
        """
        try:
            # Ensure data length is sufficient
            if len(data) < 24:
                print(f"Packet length insufficient for parsing target data (length: {len(data)})")
                return
            
            # Parse angle data
            # 3-4 bytes: Z-axis motor angle
            z_angle = int.from_bytes(data[3:5], byteorder='little', signed=True)
            
            # 5-6 bytes: Pitch angle
            pitch_angle = int.from_bytes(data[5:7], byteorder='little', signed=True)
            
            # 7-8 bytes: Roll angle
            roll_angle = int.from_bytes(data[7:9], byteorder='little', signed=True)
            
            # 9-10 bytes: Yaw angle
            yaw_angle = int.from_bytes(data[9:11], byteorder='little', signed=True)
            
            # 11th byte: Range enabled
            range_enabled = bool(data[11])
            
            # 12-13 bytes: Target distance
            distance = int.from_bytes(data[12:14], byteorder='little', signed=True)
            
            # 14-15 bytes: Target height
            height = int.from_bytes(data[14:16], byteorder='little', signed=True)
            
            # 16-19 bytes: Target longitude
            longitude_bytes = data[16:20]
            longitude = np.frombuffer(longitude_bytes, dtype=np.float32)[0]
            
            # 20-23 bytes: Target latitude
            latitude_bytes = data[20:24]
            latitude = np.frombuffer(latitude_bytes, dtype=np.float32)[0]
            
            # Update display data
            self.target_display.update_data(z_angle, pitch_angle, roll_angle, yaw_angle, range_enabled, 
                                           distance, height, longitude, latitude)
            
        except Exception as e:
            print(f"Error parsing target data: {e}")
    
    def _receive_loop(self):
        """Receiver loop"""
        while self.running and self.client_socket:
            try:
                # Attempt to receive data
                data = self.client_socket.recv(4096)
                
                if not data:
                    print("Server closed the connection")
                    self.running = False
                    break
                
                # Parse target data
                self.parse_target_data(data)
                
            except ConnectionResetError:
                print("\nConnection reset by server")
                self.running = False
                break
            except ConnectionAbortedError:
                print("\nConnection aborted")
                self.running = False
                break
            except Exception as e:
                if self.running:  # Only show error when not actively closing
                    print(f"\nError receiving data: {e}")
                    self.running = False
                break
    
    def close(self):
        """Close connection"""
        if self._closing:  # Prevent multiple closing
            return
        
        self._closing = True
        self.running = False
        
        # Close target data display
        if self.target_display:
            self.target_display.stop_display()
        
        # Close RTSP stream
        if self.rtsp_stream:
            self.rtsp_stream.stop_stream()
        
        if self.client_socket:
            try:
                self.client_socket.close()
            except:
                pass
            self.client_socket = None
        
        if self.receiver_thread and self.receiver_thread.is_alive():
            self.receiver_thread.join(timeout=1.0)
        
        print("All connections closed")

    def send(self, data):
        """
        Send data to the server
        :param data: Data to send (bytes)
        :return: True if successful, False otherwise
        """
        try:
            if self.client_socket:
                self.client_socket.send(data)
                return True
            else:
                print("Not connected to server")
                return False
        except Exception as e:
            print(f"Error sending data: {e}")
            return False
