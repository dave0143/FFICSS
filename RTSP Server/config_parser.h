#ifndef CONFIG_PARSER_H
#define CONFIG_PARSER_H

#include <json-c/json.h>

#define MAX_STREAMS 10
#define MAX_OPT_PARAM_LEN 256
#define MAX_URI_LEN 512
#define MAX_MOUNT_POINT_LEN 64
#define DEFAULT_LATENCY_MS 100
#define MIN_LATENCY_MS 10
#define MAX_LATENCY_MS 500
#define MIN_GPU_MEMORY_PERCENT 10
#define MAX_GPU_MEMORY_PERCENT 90

// AI Integration configuration
typedef struct {
    int enable_ai_forward;                    // Enable forwarding to AI module
    char ai_target_uri[MAX_URI_LEN];         // AI module input URI
    int enable_tee;                          // Enable tee (split output)
} AIIntegrationConfig;

typedef struct {
    char mount_point[MAX_MOUNT_POINT_LEN];    // RTSP endpoint path (e.g., "/camera1")
    char source_uri[MAX_URI_LEN];             // Source RTSP URL
    char decoder_params[MAX_OPT_PARAM_LEN];   // Decoder optimization parameters
    char encoder_params[MAX_OPT_PARAM_LEN];   // Encoder optimization parameters
    int latency;                              // Target latency in milliseconds
    int thread_affinity;                      // CPU core affinity (-1 for no affinity)
    AIIntegrationConfig ai_config;            // AI integration settings
} StreamConfig;

typedef struct {
    int server_port;                          // RTSP server port
    int gpu_memory_percent;                   // GPU memory allocation percentage
    int zero_copy;                            // Enable zero-copy memory
    char performance_profile[32];             // Performance profile
    int stream_count;                         // Number of configured streams
    StreamConfig streams[MAX_STREAMS];        // Stream configurations
} ServerConfig;

// Function declarations
int parse_config(const char *filename, ServerConfig *config);
void print_config_summary(const ServerConfig *config);
int validate_config(const ServerConfig *config);

#endif /* CONFIG_PARSER_H */