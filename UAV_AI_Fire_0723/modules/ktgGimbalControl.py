"""
KTG 雲台控制
"""

import threading
import socket
import struct
import time
from enum import Enum
from typing import List, Optional, Union, Callable, Dict, Any

class ControlUnit(Enum):
    EO = 0x01
    IR = 0x02
    TCP_CONTROL = 0x21

class EOCommand(Enum):
    POINT_ZOOM = 0x01
    FOLLOW_HEADING = 0x02
    CENTER = 0x03
    CONTROL_GIMBAL = 0x04
    START_TRACKING = 0x05
    STOP_TRACKING = 0x06
    VERTICAL_VIEW = 0x07
    ROTATE_TO_ANGLE = 0x08
    TAKE_PHOTO = 0x10
    RECORD_VIDEO = 0x11
    ZOOM = 0x12
    FOCUS = 0x13
    POINT_FOCUS = 0x14
    RANGE_FINDING = 0x21
    TARGET_FOLLOW = 0x31
    FORMAT_SD = 0xF1
    QUERY_VERSION = 0xFF

class GimbalConnectionManager:
    def __init__(self, controller, min_reconnect_interval=5):
        self.controller = controller
        self.lock = threading.RLock()
        self.last_connect_time = 0
        self.min_reconnect_interval = min_reconnect_interval
        self._active = True
    
    def ensure_connected(self):
        with self.lock:
            if not self._active:
                return False
            
            if self._is_connection_alive():
                return True
            
            current_time = time.time()
            if current_time - self.last_connect_time < self.min_reconnect_interval:
                return False
            
            try:
                if self.controller.connect():
                    self.last_connect_time = current_time
                    self.controller.eo_center()
                    self.controller.eo_range_finding(enable=True)
                    return True
            except Exception as e:
                print(f"連線雲台失敗: {str(e)}")
            
            return False
    
    def _is_connection_alive(self):
        if not hasattr(self.controller, 'connected') or not self.controller.connected:
            return False
        
        try:
            test_cmd = self.controller._build_command(ControlUnit.EO, EOCommand.QUERY_VERSION)
            with self.lock:
                self.controller.socket.settimeout(1.0)
                self.controller.socket.send(test_cmd)
                response = self.controller.socket.recv(32)
                return len(response) >= 3 and response[0:2] == b'KK'
        except:
            return False
    
    def shutdown(self):
        with self.lock:
            self._active = False
            if self.controller.connected:
                self.controller.disconnect()

class KTGGimbalController:
    def __init__(self, ip: str = "192.168.144.200", port: int = 2000, timeout: float = 1.0):
        self.ip = ip
        self.port = port
        self.timeout = timeout
        self.socket = None
        self.connected = False
        self.latest_info: Dict[str, Any] = {}
        self.connection_manager = GimbalConnectionManager(self)
        self.listener_thread = None
        self.mqtt_service = None
        self.callback = None
    
    def set_mqtt_service(self, mqtt_service):
        self.mqtt_service = mqtt_service
    
    def maintain_connection_loop(self, reconnect_interval=5):
        def connection_loop():
            while True:
                self.connection_manager.ensure_connected()
                time.sleep(reconnect_interval)
        
        threading.Thread(target=connection_loop, daemon=True).start()
    
    def start_listening(self):
        def listen():
            while True:
                data = self.receive_data()
                if data:
                    self.latest_info = self._parse_gimbal_info(data)
                    if self.callback:
                        self.callback(self.latest_info)
                    if self.mqtt_service and self.mqtt_service.is_connected():
                        self.mqtt_service.publish("gimbal/info", self.latest_info)
                time.sleep(0.1)  # 10Hz
        
        self.listener_thread = threading.Thread(target=listen, daemon=True)
        self.listener_thread.start()
    
    def receive_data(self) -> Optional[bytes]:
        if not self.connected:
            return None
        
        try:
            data = self.socket.recv(1024)
            return data if data else None
        except socket.timeout:
            return None
        except Exception as e:
            print(f"接收數據錯誤: {e}")
            self.connected = False
            return None
    
    def connect(self) -> bool:
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(self.timeout)
            self.socket.connect((self.ip, self.port))
            self.connected = True
            return True
        except Exception as e:
            print(f"連接錯誤: {e}")
            self.connected = False
            return False
    
    def disconnect(self) -> None:
        if self.socket:
            self.socket.close()
        self.connected = False
    
    def _calculate_checksum(self, buffer: bytes) -> int:
        return sum(buffer) & 0xFF
    
    def _build_command(self, 
        control_unit: ControlUnit, 
        command: Union[EOCommand], 
        data: List[int] = None) -> bytes:
        if data is None:
            data = [0, 0, 0, 0, 0, 0, 0]
        else:
            data = data + [0] * (7 - len(data))
            data = data[:7]
        
        packet = bytearray([
            0x4B, 0x4B,
            *[0x00] * 10,
            0x40, 0x88,
            control_unit.value,
            command.value,
            *data
        ])
        
        checksum = self._calculate_checksum(packet)
        packet.append(checksum)
        
        return bytes(packet)
    
    def send_command(self, 
        control_unit: ControlUnit, 
        command: Union[EOCommand], 
        data: List[int] = None) -> dict:
        if not self.connected:
            return {"success": False, "error": "Not connected"}
        
        packet = self._build_command(control_unit, command, data)
        
        try:
            self.socket.send(packet)
            response = self.socket.recv(32)
            
            if len(response) >= 13 and response[0:2] == b'KK':
                if response[2] == 0x01:
                    return {
                        "success": True,
                        "control_unit": response[3],
                        "command": response[4],
                        "data": list(response[5:12]),
                    }
                elif response[2] == 0xFF:
                    return {
                        "success": True,
                        "type": "version",
                        "version": f"V{response[3]}.{response[4]}.{response[5]}",
                        "build_date": f"20{response[6]:02d}-{response[7]:02d}-{response[8]:02d}"
                    }
                elif response[2] == 0x02:
                    return self._parse_gimbal_info(response)
            
            return {"success": False, "error": "Invalid response", "raw": response.hex()}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _parse_gimbal_info(self, response: bytes) -> dict:
        if len(response) < 32:
            return {"success": False, "error": "Response too short"}
        
        try:
            z_axis = struct.unpack('<h', response[3:5])[0] * 0.01
            pitch = struct.unpack('<h', response[5:7])[0] * 0.01
            roll = struct.unpack('<h', response[7:9])[0] * 0.01
            yaw = struct.unpack('<h', response[9:11])[0] * 0.01
            
            range_flag = response[11]
            target_distance = struct.unpack('<H', response[12:14])[0] * 0.1
            target_height = struct.unpack('<H', response[14:16])[0] * 0.1
            
            target_longitude = struct.unpack('<i', response[16:20])[0] * 1e-7
            target_latitude = struct.unpack('<i', response[20:24])[0] * 1e-7
            
            self_test = response[24]
            eo_zoom = struct.unpack('<H', response[25:27])[0] * 0.1
            ir_zoom = struct.unpack('<H', response[27:29])[0] * 0.1
            
            return {
                "success": True,
                "type": "gimbal_info",
                "z_axis_angle": z_axis,
                "pitch": pitch,
                "roll": roll,
                "yaw": yaw,
                "range_finding": {
                    "success": range_flag == 0x01,
                    "distance": target_distance,
                    "height": target_height
                },
                "target_position": {
                    "longitude": target_longitude,
                    "latitude": target_latitude
                },
                "self_test_passed": self_test == 0x00,
                "zoom": {
                    "eo": eo_zoom,
                    "ir": ir_zoom
                }
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to parse gimbal info: {e}"}
    
    def eo_point_zoom(self, x_offset: int, y_offset: int) -> dict:
        x_offset = max(-10000, min(10000, x_offset))
        y_offset = max(-10000, min(10000, y_offset))
        
        x_bytes = list(struct.pack('<h', x_offset))
        y_bytes = list(struct.pack('<h', y_offset))
        
        data = x_bytes + y_bytes
        return self.send_command(ControlUnit.EO, EOCommand.POINT_ZOOM, data)
    
    def eo_center(self) -> dict:
        return self.send_command(ControlUnit.EO, EOCommand.CENTER)
    
    def eo_rotate_to_angle(self, 
        mode: int, 
        angle: float, 
        reference: int = 0x01) -> dict:
        if mode not in [1, 2, 3]:
            return {"success": False, "error": "Invalid mode"}
        
        if mode == 1:
            angle_int = max(-1800, min(1800, int(angle * 10)))
        elif mode == 2:
            angle_int = max(-1000, min(600, int(angle * 10)))
        else:
            angle_int = max(1000, min(3500, int(angle * 100)))
        
        angle_bytes = list(struct.pack('<h', angle_int))
        
        data = [mode] + angle_bytes + [reference]
        return self.send_command(ControlUnit.EO, EOCommand.ROTATE_TO_ANGLE, data)
    
    def eo_range_finding(self, enable: bool) -> dict:
        mode = 0x01 if enable else 0x00
        return self.send_command(ControlUnit.EO, EOCommand.RANGE_FINDING, [mode])