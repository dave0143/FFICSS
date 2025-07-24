# NVIDIA Orin RTSP Streaming Server

A high-performance, multi-stream RTSP server designed for NVIDIA Jetson Orin platforms with hardware acceleration support.

## Features

- **Multi-Stream Support**: Handle up to 10 concurrent RTSP streams
- **NVIDIA Hardware Acceleration**: Leverages nvv4l2decoder and nvv4l2h264enc
- **Thread Affinity Control**: Optimized CPU core binding for each stream
- **Performance Profiles**: Low-latency, high-throughput, and balanced modes
- **Zero-Copy Memory**: Efficient memory management with CUDA unified memory
- **Secure Configuration**: Safe string handling and comprehensive validation
- **JSON Configuration**: Easy-to-modify stream settings

## Requirements

### System Requirements
- NVIDIA Jetson Orin platform
- JetPack SDK with proper drivers
- Ubuntu 18.04+ or equivalent

### Dependencies
- GStreamer 1.0+ with RTSP server support
- json-c library
- NVIDIA Buffer Utils (nvbuf_utils)
- pthread library

## Installation

### 1. Check Dependencies
```bash
make check-deps
```

### 2. Build the Project
```bash
# Standard build
make

# Debug build
make debug

# Optimized release build
make release
```

### 3. Install System-wide (Optional)
```bash
sudo make install
```

## Configuration

Edit `rtsp_server.conf` to configure your streams:

```json
{
  "server_port": 8554,
  "gpu_memory_percent": 70,
  "zero_copy": true,
  "performance_profile": "balanced",
  "streams": [
    {
      "mount_point": "/camera1",
      "source_uri": "rtsp://admin:password@192.168.1.100:554/stream",
      "optimization": {
        "decoder": "enable-max-performance=1",
        "encoder": "bitrate=4000 preset-level=1",
        "latency": 50,
        "thread_affinity": 2
      }
    }
  ]
}
```

### Configuration Parameters

#### Server Settings
- `server_port`: RTSP server port (1-65535)
- `gpu_memory_percent`: GPU memory allocation (10-90%)
- `zero_copy`: Enable CUDA unified memory (true/false)
- `performance_profile`: "low_latency", "high_throughput", or "balanced"

#### Stream Settings
- `mount_point`: RTSP endpoint path (e.g., "/camera1")
- `source_uri`: Source RTSP URL
- `decoder`: NVIDIA decoder parameters
- `encoder`: NVIDIA encoder parameters
- `latency`: Target latency in milliseconds (10-500ms)
- `thread_affinity`: CPU core assignment (-1 for no affinity)

### Performance Profiles

#### Low Latency
- Enables jetson_clocks
- Sets CPU governor to performance
- Optimized for minimal delay

#### High Throughput
- Increases network buffer sizes
- Optimized for maximum bandwidth

#### Balanced
- Default settings
- Good balance of performance and power consumption

## Usage

### Basic Usage
```bash
./rtsp_server
```

### Access Streams
Connect to your streams using any RTSP client:
```
rtsp://<Orin_IP>:8554/<mount_point>
```

Example:
```
rtsp://192.168.1.10:8554/camera1
```

### Control Server
- **Start**: `./rtsp_server`
- **Stop**: Press `Ctrl+C`
- **Check Status**: 
  ```bash
  ps aux | grep rtsp_server
  netstat -tulpn | grep 8554
  ```

## Debugging

### Enable Debug Mode
```bash
make debug
./rtsp_server
```

### Check Logs
The server outputs detailed information including:
- Configuration validation results
- Stream initialization status
- Pipeline construction details
- Performance profile applications

### Common Issues

#### Port Already in Use
```
Error: Failed to bind to port 8554! Port may be in use.
```
**Solution**: Change port in configuration or kill existing process

#### NVIDIA Libraries Not Found
```
WARNING: nvbuf_utils library not found
```
**Solution**: Ensure JetPack is properly installed

#### Stream Initialization Failed
Check:
- Source URI accessibility
- Network connectivity
- Decoder/encoder parameters

## Advanced Usage

### Custom Pipeline Parameters

#### Decoder Options
- `enable-max-performance=1`: Maximum performance mode
- `cudadec-memtype=2`: CUDA memory type
- `drop-frame-interval=0`: Frame dropping control

#### Encoder Options
- `bitrate=4000`: Target bitrate in kbps
- `preset-level=1`: Encoding preset (1-4)
- `insert-sps-pps=true`: Insert SPS/PPS headers
- `profile=0`: H.264 profile (0=baseline, 1=main, 2=high)

### Thread Affinity Guidelines
- Core 0-1: System processes
- Core 2-3: Decoder threads
- Core 4-5: Encoder threads
- Core 6-7: Network I/O

### Memory Optimization
- Use zero-copy for high-resolution streams
- Adjust GPU memory percentage based on workload
- Monitor memory usage with `nvidia-smi`

## Development

### Project Structure
```
├── config_parser.c     # Configuration parsing and validation
├── config_parser.h     # Configuration structures and constants
├── rtsp_server.c       # Main server implementation
├── rtsp_server.conf    # Default configuration file
├── Makefile           # Build system
└── README.md          # This file
```

### Building from Source
```bash
git clone <repository>
cd rtsp-server
make check-deps
make
```

### Contributing
1. Follow existing code style
2. Add appropriate error handling
3. Update documentation
4. Test on target hardware

## Support

For issues and questions:
1. Check the debugging section
2. Verify hardware compatibility
3. Review configuration syntax
4. Check GStreamer installation

## Performance Tips

1. **CPU Affinity**: Assign decoder/encoder to separate cores
2. **Memory**: Use zero-copy for high-bandwidth streams
3. **Network**: Ensure sufficient bandwidth for all streams
4. **Power**: Use appropriate performance profile for your use case
5. **Monitoring**: Watch CPU, GPU, and memory usage during operation