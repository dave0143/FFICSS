import os
import json
import time

def check_dependencies():
    """Check if necessary dependencies are installed"""
    try:
        import cv2
        print("OpenCV is installed, version:", cv2.__version__)
        return True
    except ImportError:
        print("Error: OpenCV library not found")
        print("Please install OpenCV with the following command:")
        print("pip install opencv-python")
        return False

def create_default_config():
    """Create default configuration file"""
    default_config = {
        "tcp_host": "127.0.0.1",
        "tcp_port": 9999,
        "auto_rtsp": "rtsp://admin:admin@192.168.1.1:554/stream1", 
        "auto_connect": True,
        "buffer_size": 4096,
        "reconnect_attempts": 3,
        "reconnect_delay": 5
    }
    
    with open("config.json", "w", encoding="utf-8") as f:
        json.dump(default_config, f, indent=4, ensure_ascii=False)
    
    print("Default configuration file created: config.json")
    return default_config

def load_config(config_path="config.json"):
    """
    Load configuration file
    :param config_path: Path to configuration file
    :return: Configuration dictionary
    """
    if not os.path.exists(config_path):
        print(f"Configuration file {config_path} does not exist, creating default configuration")
        return create_default_config()
    
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        print(f"Successfully loaded configuration file: {config_path}")
        return config
    except Exception as e:
        print(f"Error loading configuration file: {e}")
        print("Using default configuration")
        return create_default_config()

def parse_packet(data, verbose=True):
    """
    Parse TCP packet content (example function, can be extended based on actual protocol)
    :param data: Binary data
    :param verbose: Whether to display detailed information
    :return: Parsing result
    """
    result = {}
    
    # This is just a simple example
    # In actual applications, you need to customize based on your protocol format
    if len(data) < 4:
        return {"error": "Data too short for parsing"}
    
    # Assuming first 4 bytes are some kind of header
    header = int.from_bytes(data[:4], byteorder='big')
    result["header"] = header
    
    # Assuming the following data is message content
    payload = data[4:]
    result["payload_hex"] = payload.hex()
    
    # Print detailed parsing results
    if verbose:
        print("\nData packet parsing results:")
        print(f"- Packet length: {len(data)} bytes")
        print(f"- Header value: 0x{header:08X}")
        print(f"- Payload (HEX): {result['payload_hex']}")
    
    return result
