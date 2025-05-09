"""
KTG Gimbal Control Library
Based on the KTG GT Command Note_20241014.pdf specification

This library provides a Python interface for controlling KTG gimbal systems through 
their TCP control interface.
"""

import socket
import struct
import time
from enum import Enum
from typing import Tuple, Optional, List, Dict, Union


class ControlUnit(Enum):
    """Control unit identifiers"""
    EO = 0x01  # Visible light
    IR = 0x02  # Thermal imaging
    TCP_CONTROL = 0x21  # Modify TCP control IP


class EOCommand(Enum):
    """EO (Visible light) commands"""
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


class IRCommand(Enum):
    """IR (Thermal imaging) commands"""
    TOGGLE_HUD = 0x01
    CHANGE_PALETTE = 0x02
    AUTO_SENSITIVITY = 0x03
    MANUAL_SENSITIVITY = 0x04
    ZOOM = 0x05


class PaletteType(Enum):
    """Thermal camera palette types"""
    WH = 0x00  # White Hot
    BH = 0x01  # Black Hot
    RB = 0x02  # Rainbow
    RH = 0x03  # Red Hot
    IB = 0x04  # Iron Bow (Default)
    LV = 0x05
    AT = 0x06
    GB = 0x07
    GF = 0x08
    HT = 0x09


class KTGGimbalController:
    """
    Controller for KTG gimbal systems.
    Provides methods for connecting to and controlling KTG gimbals.
    """

    def __init__(self, ip: str = "192.168.144.200", port: int = 2000, timeout: float = 1.0):
        """
        Initialize the gimbal controller.
        
        Args:
            ip: IP address of the gimbal control server (default: 192.168.144.200)
            port: Port of the gimbal control server (default: 2000)
            timeout: Socket timeout in seconds (default: 1.0)
        """
        self.ip = ip
        self.port = port
        self.timeout = timeout
        self.socket = None
        self.connected = False
        
    def connect(self) -> bool:
        """
        Connect to the gimbal control server.
        
        Returns:
            bool: True if connection successful, False otherwise
        """
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(self.timeout)
            self.socket.connect((self.ip, self.port))
            self.connected = True
            return True
        except Exception as e:
            print(f"Connection error: {e}")
            self.connected = False
            return False
            
    def disconnect(self) -> None:
        """Disconnect from the gimbal control server."""
        if self.socket:
            self.socket.close()
        self.connected = False
    
    def _calculate_checksum(self, buffer: bytes) -> int:
        """
        Calculate checksum for the command packet.
        
        Args:
            buffer: Bytes to calculate checksum for
            
        Returns:
            int: Calculated checksum
        """
        return sum(buffer) & 0xFF
    
    def _build_command(self, 
                      control_unit: ControlUnit, 
                      command: Union[EOCommand, IRCommand], 
                      data: List[int] = None) -> bytes:
        """
        Build a command packet according to the specification.
        
        Args:
            control_unit: Control unit (EO, IR, or TCP_CONTROL)
            command: Command to send
            data: Command parameters (up to 7 bytes)
            
        Returns:
            bytes: Complete command packet
        """
        if data is None:
            data = [0, 0, 0, 0, 0, 0, 0]
        else:
            # Ensure data is 7 bytes long
            data = data + [0] * (7 - len(data))
            data = data[:7]  # Truncate if longer than 7
            
        # Build packet
        packet = bytearray([
            0x4B, 0x4B,                 # Header
            *[0x00] * 10,               # Reserved
            0x40, 0x88,                 # TCP control
            control_unit.value,         # Control unit
            command.value,              # Command
            *data                       # Data
        ])
        
        # Calculate and append checksum
        checksum = self._calculate_checksum(packet)
        packet.append(checksum)
        
        return bytes(packet)
    
    def send_command(self, 
                     control_unit: ControlUnit, 
                     command: Union[EOCommand, IRCommand], 
                     data: List[int] = None) -> dict:
        """
        Send a command to the gimbal and receive response.
        
        Args:
            control_unit: Control unit (EO, IR, or TCP_CONTROL)
            command: Command to send
            data: Command parameters (up to 7 bytes)
            
        Returns:
            dict: Parsed response from the gimbal
        """
        if not self.connected:
            return {"success": False, "error": "Not connected"}
            
        packet = self._build_command(control_unit, command, data)
        
        try:
            self.socket.send(packet)
            response = self.socket.recv(32)  # Adjust buffer size as needed
            
            # Parse response
            if len(response) >= 13 and response[0:2] == b'KK':
                if response[2] == 0x01:  # Command success response
                    return {
                        "success": True,
                        "control_unit": response[3],
                        "command": response[4],
                        "data": list(response[5:12]),
                    }
                elif response[2] == 0xFF:  # Version response
                    return {
                        "success": True,
                        "type": "version",
                        "version": f"V{response[3]}.{response[4]}.{response[5]}",
                        "build_date": f"20{response[6]:02d}-{response[7]:02d}-{response[8]:02d}"
                    }
                elif response[2] == 0x02:  # Gimbal info response
                    return self._parse_gimbal_info(response)
            
            return {"success": False, "error": "Invalid response", "raw": response.hex()}
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _parse_gimbal_info(self, response: bytes) -> dict:
        """
        Parse gimbal information response packet.
        
        Args:
            response: Raw response bytes
            
        Returns:
            dict: Parsed gimbal information
        """
        if len(response) < 32:
            return {"success": False, "error": "Response too short"}
            
        try:
            # Extract values according to section 4.3 of the documentation
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
            
    def listen_gimbal_info(self, callback=None, max_attempts=10) -> dict:
        """
        Listen for gimbal information packets.
        
        Args:
            callback: Optional callback function to call with each info packet
            max_attempts: Maximum number of attempts to read data
            
        Returns:
            dict: Last received gimbal information or error
        """
        if not self.connected:
            return {"success": False, "error": "Not connected"}
            
        last_info = {"success": False, "error": "No data received"}
        attempts = 0
        
        try:
            self.socket.settimeout(0.2)  # Short timeout for polling
            
            while attempts < max_attempts:
                try:
                    response = self.socket.recv(32)
                    
                    if len(response) >= 2 and response[0:2] == b'KK' and response[2] == 0x02:
                        info = self._parse_gimbal_info(response)
                        last_info = info
                        
                        if callback:
                            callback(info)
                    
                except socket.timeout:
                    attempts += 1
                    continue
                
            return last_info
            
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            self.socket.settimeout(self.timeout)  # Restore original timeout
    
    # ====== EO Camera Control Methods ======
    
    def eo_point_zoom(self, x_offset: int, y_offset: int) -> dict:
        """
        Point zoom function.
        
        Args:
            x_offset: Horizontal offset from center (-10000 to 10000)
            y_offset: Vertical offset from center (-10000 to 10000)
            
        Returns:
            dict: Command response
        """
        x_offset = max(-10000, min(10000, x_offset))
        y_offset = max(-10000, min(10000, y_offset))
        
        # Convert to little-endian bytes and then to list of integers
        x_bytes = list(struct.pack('<h', x_offset))
        y_bytes = list(struct.pack('<h', y_offset))
        
        data = x_bytes + y_bytes
        return self.send_command(ControlUnit.EO, EOCommand.POINT_ZOOM, data)
    
    def eo_follow_heading(self) -> dict:
        """
        Enable follow heading mode.
        
        Returns:
            dict: Command response
        """
        return self.send_command(ControlUnit.EO, EOCommand.FOLLOW_HEADING)
    
    def eo_center(self) -> dict:
        """
        Center the gimbal.
        
        Returns:
            dict: Command response
        """
        return self.send_command(ControlUnit.EO, EOCommand.CENTER)
    
    def eo_control_gimbal(self, yaw_speed: float, pitch_speed: float) -> dict:
        """
        Control gimbal movement speed.
        
        Args:
            yaw_speed: Yaw movement speed in deg/s (-100 to 100)
            pitch_speed: Pitch movement speed in deg/s (-100 to 100)
            
        Returns:
            dict: Command response
        """
        # Convert to 0.01 deg/s and constrain to valid range
        yaw_speed_int = max(-10000, min(10000, int(yaw_speed * 100)))
        pitch_speed_int = max(-10000, min(10000, int(pitch_speed * 100)))
        
        # Convert to little-endian bytes and then to list of integers
        yaw_bytes = list(struct.pack('<h', yaw_speed_int))
        pitch_bytes = list(struct.pack('<h', pitch_speed_int))
        
        data = yaw_bytes + pitch_bytes
        return self.send_command(ControlUnit.EO, EOCommand.CONTROL_GIMBAL, data)
    
    def eo_start_tracking(self, x: int, y: int, width: int, height: int) -> dict:
        """
        Start tracking an object.
        
        Args:
            x: X coordinate of target center (0-8191)
            y: Y coordinate of target center (0-8191)
            width: Width of tracking window
            height: Height of tracking window
            
        Returns:
            dict: Command response
        """
        x = max(0, min(8191, x))
        y = max(0, min(8191, y))
        
        # Convert to little-endian bytes and then to list of integers
        x_bytes = list(struct.pack('<h', x))
        y_bytes = list(struct.pack('<h', y))
        
        # Convert width and height to required format (divided by 16)
        width_byte = min(255, width // 16)
        height_byte = min(255, height // 16)
        
        data = x_bytes + y_bytes + [width_byte, height_byte]
        return self.send_command(ControlUnit.EO, EOCommand.START_TRACKING, data)
    
    def eo_stop_tracking(self) -> dict:
        """
        Stop tracking.
        
        Returns:
            dict: Command response
        """
        return self.send_command(ControlUnit.EO, EOCommand.STOP_TRACKING)
    
    def eo_vertical_view(self) -> dict:
        """
        Set gimbal to vertical view position.
        
        Returns:
            dict: Command response
        """
        return self.send_command(ControlUnit.EO, EOCommand.VERTICAL_VIEW)
    
    def eo_rotate_to_angle(self, 
                           mode: int, 
                           angle: float, 
                           reference: int = 0x01) -> dict:
        """
        Rotate gimbal to specified angle.
        
        Args:
            mode: 1=yaw, 2=pitch, 3=zoom
            angle: Angle in degrees (yaw: -180 to 180, pitch: -100 to 60) 
                   or zoom level (zoom: 10 to 30/35) depending on mode
            reference: 1=compass reference, 2=follow heading
            
        Returns:
            dict: Command response
        """
        if mode not in [1, 2, 3]:
            return {"success": False, "error": "Invalid mode"}
            
        # Convert angle to required format and constrain to valid range
        if mode == 1:  # Yaw
            angle_int = max(-1800, min(1800, int(angle * 10)))
        elif mode == 2:  # Pitch
            angle_int = max(-1000, min(600, int(angle * 10)))
        else:  # Zoom
            angle_int = max(1000, min(3500, int(angle * 100)))
            
        # Convert to little-endian bytes and then to list of integers
        angle_bytes = list(struct.pack('<h', angle_int))
        
        data = [mode] + angle_bytes + [reference]
        return self.send_command(ControlUnit.EO, EOCommand.ROTATE_TO_ANGLE, data)
    
    def eo_take_photo(self, mode: int, param: int = 0) -> dict:
        """
        Take photo.
        
        Args:
            mode: 1=single, 2=burst, 3=delayed, 4=timed, 5=stop
            param: For mode 2: count, mode 3: delay in seconds, mode 4: interval in seconds
            
        Returns:
            dict: Command response
        """
        if mode not in [1, 2, 3, 4, 5]:
            return {"success": False, "error": "Invalid photo mode"}
            
        data = [mode, param]
        return self.send_command(ControlUnit.EO, EOCommand.TAKE_PHOTO, data)
    
    def eo_record_video(self, start: bool) -> dict:
        """
        Start or stop video recording.
        
        Args:
            start: True to start recording, False to stop
            
        Returns:
            dict: Command response
        """
        mode = 0x01 if start else 0x02
        return self.send_command(ControlUnit.EO, EOCommand.RECORD_VIDEO, [mode])
    
    def eo_zoom(self, mode: int) -> dict:
        """
        Control zoom.
        
        Args:
            mode: 1=zoom in, 2=zoom out, 3=stop zoom, 4=reset to 1x, 
                  5=zoom in 2x, 6=zoom out 2x
            
        Returns:
            dict: Command response
        """
        if mode not in [1, 2, 3, 4, 5, 6]:
            return {"success": False, "error": "Invalid zoom mode"}
            
        return self.send_command(ControlUnit.EO, EOCommand.ZOOM, [mode])
    
    def eo_focus(self, mode: int) -> dict:
        """
        Control focus.
        
        Args:
            mode: 1=focus+, 2=focus-, 3=stop focus, 4=auto focus
            
        Returns:
            dict: Command response
        """
        if mode not in [1, 2, 3, 4]:
            return {"success": False, "error": "Invalid focus mode"}
            
        return self.send_command(ControlUnit.EO, EOCommand.FOCUS, [mode])
    
    def eo_point_focus(self, x: int, y: int, width: int, height: int) -> dict:
        """
        Focus on a specific point.
        
        Args:
            x: X coordinate of focus point (0-8191)
            y: Y coordinate of focus point (0-8191)
            width: Width of focus area
            height: Height of focus area
            
        Returns:
            dict: Command response
        """
        x = max(0, min(8191, x))
        y = max(0, min(8191, y))
        
        # Convert to little-endian bytes and then to list of integers
        x_bytes = list(struct.pack('<h', x))
        y_bytes = list(struct.pack('<h', y))
        
        # Convert width and height to required format (divided by 16)
        width_byte = min(255, width // 16)
        height_byte = min(255, height // 16)
        
        data = x_bytes + y_bytes + [width_byte, height_byte]
        return self.send_command(ControlUnit.EO, EOCommand.POINT_FOCUS, data)
    
    def eo_range_finding(self, enable: bool) -> dict:
        """
        Enable or disable range finding (TX series only).
        
        Args:
            enable: True to enable, False to disable
            
        Returns:
            dict: Command response
        """
        mode = 0x01 if enable else 0x00
        return self.send_command(ControlUnit.EO, EOCommand.RANGE_FINDING, [mode])
    
    def eo_target_follow(self, 
                       enable: bool, 
                       x_ratio: int = 0, 
                       y_ratio: int = 0) -> dict:
        """
        Control target following.
        
        Args:
            enable: True to enable, False to disable
            x_ratio: Target center horizontal offset ratio (-100 to 100)
            y_ratio: Target center vertical offset ratio (-100 to 100)
            
        Returns:
            dict: Command response
        """
        mode = 0x01 if enable else 0x00
        x_ratio = max(-100, min(100, x_ratio))
        y_ratio = max(-100, min(100, y_ratio))
        
        data = [mode, x_ratio, y_ratio]
        return self.send_command(ControlUnit.EO, EOCommand.TARGET_FOLLOW, data)
    
    def eo_format_sd(self) -> dict:
        """
        Format SD card.
        
        Returns:
            dict: Command response
        """
        return self.send_command(ControlUnit.EO, EOCommand.FORMAT_SD)
    
    def query_version(self) -> dict:
        """
        Query controller version.
        
        Returns:
            dict: Version information response
        """
        return self.send_command(ControlUnit.EO, EOCommand.QUERY_VERSION)
    
    # ====== IR Camera Control Methods ======
    
    def ir_toggle_hud(self, enable: bool) -> dict:
        """
        Toggle HUD display on thermal camera.
        
        Args:
            enable: True to enable, False to disable
            
        Returns:
            dict: Command response
        """
        mode = 0x01 if enable else 0x00
        return self.send_command(ControlUnit.IR, IRCommand.TOGGLE_HUD, [mode])
    
    def ir_change_palette(self, palette: PaletteType) -> dict:
        """
        Change thermal camera color palette.
        
        Args:
            palette: Palette type
            
        Returns:
            dict: Command response
        """
        return self.send_command(ControlUnit.IR, IRCommand.CHANGE_PALETTE, [palette.value])
    
    def ir_auto_sensitivity(self, enable: bool) -> dict:
        """
        Toggle auto sensitivity adjustment.
        
        Args:
            enable: True to enable, False to disable
            
        Returns:
            dict: Command response
        """
        mode = 0x01 if enable else 0x00
        return self.send_command(ControlUnit.IR, IRCommand.AUTO_SENSITIVITY, [mode])
    
    def ir_manual_sensitivity(self, level: int) -> dict:
        """
        Set manual sensitivity level.
        
        Args:
            level: Sensitivity level (1-5)
            
        Returns:
            dict: Command response
        """
        level = max(1, min(5, level))
        return self.send_command(ControlUnit.IR, IRCommand.MANUAL_SENSITIVITY, [level])
    
    def ir_zoom(self, zoom_in: bool) -> dict:
        """
        Control IR camera zoom.
        
        Args:
            zoom_in: True to zoom in, False to zoom out
            
        Returns:
            dict: Command response
        """
        mode = 0x01 if zoom_in else 0x02
        return self.send_command(ControlUnit.IR, IRCommand.ZOOM, [mode])
    
    # ====== TCP Control Methods ======
    
    def modify_ip_gateway(self, 
                         new_ip: List[int], 
                         new_gateway: List[int]) -> dict:
        """
        Modify the IP address and gateway.
        
        Args:
            new_ip: List of 4 integers for new IP address
            new_gateway: List of 4 integers for new gateway
            
        Returns:
            dict: Command response
        """
        if len(new_ip) != 4 or len(new_gateway) != 4:
            return {"success": False, "error": "IP and gateway must be 4 bytes each"}
            
        data = new_ip + new_gateway
        
        # For IP modification, we use a special format
        packet = bytearray([
            0x4B, 0x4B,                 # Header
            *[0x00] * 10,               # Reserved
            0x40, 0x88,                 # TCP control
            ControlUnit.TCP_CONTROL.value,  # Control unit
            *data[:4],                  # IP
            *data[4:],                  # Gateway
        ])
        
        # Calculate and append checksum
        checksum = self._calculate_checksum(packet)
        packet.append(checksum)
        
        try:
            self.socket.send(bytes(packet))
            response = self.socket.recv(32)
            
            # Parse response
            if len(response) >= 13 and response[0:2] == b'KK' and response[2] == 0x01:
                return {
                    "success": True,
                    "message": "IP and gateway modified successfully"
                }
            
            return {"success": False, "error": "Failed to modify IP and gateway"}
            
        except Exception as e:
            return {"success": False, "error": str(e)}


# Example usage
if __name__ == "__main__":
    # Create controller
    controller = KTGGimbalController()
    
    # Connect to gimbal
    if controller.connect():
        print("Connected to gimbal")
        
        # Get version
        version_info = controller.query_version()
        print(f"Gimbal version: {version_info}")
        
        # Center the gimbal
        controller.eo_center()
        
        # Listen for gimbal info
        def print_info(info):
            print(f"Gimbal position: Yaw={info['yaw']}, Pitch={info['pitch']}")
        
        controller.listen_gimbal_info(callback=print_info, max_attempts=5)
        
        # Disconnect
        controller.disconnect()
    else:
        print("Failed to connect to gimbal")
