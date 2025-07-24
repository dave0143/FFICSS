// Enhanced configuration validation and secure string handling with AI Integration
#include "config_parser.h"
#include <stdio.h>
#include <string.h>
#include <json-c/json.h>
#include <sys/stat.h>

// Safe string copy function
static void safe_strcpy(char *dest, const char *src, size_t dest_size) {
    if (!dest || !src || dest_size == 0) return;
    
    strncpy(dest, src, dest_size - 1);
    dest[dest_size - 1] = '\0';
}

// Validate URI format
static int validate_uri(const char *uri) {
    if (!uri || strlen(uri) == 0) {
        return 0;
    }
    
    // Check supported protocols
    if (strncmp(uri, "rtsp://", 7) == 0) {
        return 1; // Valid RTSP URI
    }
    
    // Also support other possible protocols
    if (strncmp(uri, "rtspt://", 8) == 0) {
        return 1; // RTSP over TCP
    }
    
    if (strncmp(uri, "rtspu://", 8) == 0) {
        return 1; // RTSP over UDP
    }
    
    // Allow local files for testing environment
    if (strncmp(uri, "file://", 7) == 0) {
        printf("Info: Using local file source: %s\n", uri);
        return 1;
    }
    
    fprintf(stderr, "Warning: URI '%s' may not be supported. Expected rtsp:// protocol.\n", uri);
    return 0; // Return 0 for unexpected protocols but give warning
}

// Validate mount point format
static int validate_mount_point(const char *mount) {
    if (!mount || strlen(mount) == 0) {
        return 0;
    }
    
    // Must start with '/'
    if (mount[0] != '/') {
        return 0;
    }
    
    // Check for illegal characters
    for (const char *p = mount; *p; p++) {
        if (*p == ' ' || *p == '\t' || *p == '\n') {
            fprintf(stderr, "Warning: Mount point '%s' contains whitespace\n", mount);
            return 0;
        }
    }
    
    return 1;
}

int parse_config(const char *filename, ServerConfig *config) {
    if (!filename || !config) {
        fprintf(stderr, "Error: Invalid parameters\n");
        return -1;
    }
    
    // Check if configuration file exists
    struct stat st;
    if (stat(filename, &st) != 0) {
        fprintf(stderr, "Error: Configuration file '%s' not found\n", filename);
        return -1;
    }
    
    // Initialize configuration with default values - using safe string copy
    config->server_port = 8554;
    config->gpu_memory_percent = 60;
    config->zero_copy = 1;
    safe_strcpy(config->performance_profile, "balanced", sizeof(config->performance_profile));
    config->stream_count = 0;
    
    // Parse JSON configuration file
    json_object *root = json_object_from_file(filename);
    if (!root) {
        fprintf(stderr, "Error: Failed to parse JSON in file: %s\n", filename);
        return -1;
    }
    
    // Parse server port
    json_object *j_port;
    if (json_object_object_get_ex(root, "server_port", &j_port)) {
        if (json_object_get_type(j_port) != json_type_int) {
            fprintf(stderr, "Warning: server_port must be an integer\n");
        } else {
            config->server_port = json_object_get_int(j_port);
            if (config->server_port < 1 || config->server_port > 65535) {
                fprintf(stderr, "Warning: Invalid port number %d. Using default port 8554.\n", 
                       config->server_port);
                config->server_port = 8554;
            }
        }
    }
    
    // Parse GPU memory percentage
    json_object *j_gpu_mem;
    if (json_object_object_get_ex(root, "gpu_memory_percent", &j_gpu_mem)) {
        if (json_object_get_type(j_gpu_mem) != json_type_int) {
            fprintf(stderr, "Warning: gpu_memory_percent must be an integer\n");
        } else {
            config->gpu_memory_percent = json_object_get_int(j_gpu_mem);
            if (config->gpu_memory_percent < 10 || config->gpu_memory_percent > 90) {
                fprintf(stderr, "Warning: GPU memory percent %d out of range (10-90). Using default 60%%.\n", 
                       config->gpu_memory_percent);
                config->gpu_memory_percent = 60;
            }
        }
    }
    
    // Parse zero-copy setting
    json_object *j_zero_copy;
    if (json_object_object_get_ex(root, "zero_copy", &j_zero_copy)) {
        if (json_object_get_type(j_zero_copy) != json_type_boolean) {
            fprintf(stderr, "Warning: zero_copy must be a boolean\n");
        } else {
            config->zero_copy = json_object_get_boolean(j_zero_copy);
        }
    }
    
    // Parse performance profile
    json_object *j_perf_profile;
    if (json_object_object_get_ex(root, "performance_profile", &j_perf_profile)) {
        if (json_object_get_type(j_perf_profile) != json_type_string) {
            fprintf(stderr, "Warning: performance_profile must be a string\n");
        } else {
            const char *profile = json_object_get_string(j_perf_profile);
            if (strcmp(profile, "low_latency") == 0 || 
                strcmp(profile, "high_throughput") == 0 ||
                strcmp(profile, "balanced") == 0) {
                safe_strcpy(config->performance_profile, profile, sizeof(config->performance_profile));
            } else {
                fprintf(stderr, "Warning: Invalid performance profile '%s'. Using 'balanced'.\n", profile);
                safe_strcpy(config->performance_profile, "balanced", sizeof(config->performance_profile));
            }
        }
    }
    
    // Parse stream configurations
    json_object *j_streams;
    if (json_object_object_get_ex(root, "streams", &j_streams)) {
        if (json_object_get_type(j_streams) != json_type_array) {
            fprintf(stderr, "Error: 'streams' must be an array\n");
            json_object_put(root);
            return -1;
        }
        
        int array_len = json_object_array_length(j_streams);
        
        if (array_len > MAX_STREAMS) {
            fprintf(stderr, "Warning: Configuration contains %d streams, but only %d are supported.\n", 
                   array_len, MAX_STREAMS);
            config->stream_count = MAX_STREAMS;
        } else {
            config->stream_count = array_len;
        }
        
        int valid_streams = 0;
        for (int i = 0; i < config->stream_count; i++) {
            json_object *stream_obj = json_object_array_get_idx(j_streams, i);
            
            if (json_object_get_type(stream_obj) != json_type_object) {
                fprintf(stderr, "Error: Stream %d is not a valid object. Skipping.\n", i+1);
                continue;
            }
            
            // Initialize stream configuration with default values
            memset(&config->streams[valid_streams], 0, sizeof(StreamConfig));
            config->streams[valid_streams].latency = 100;
            config->streams[valid_streams].thread_affinity = -1;
            
            // Initialize AI integration config
            config->streams[valid_streams].ai_config.enable_ai_forward = 0;
            config->streams[valid_streams].ai_config.enable_tee = 0;
            memset(config->streams[valid_streams].ai_config.ai_target_uri, 0, MAX_URI_LEN);
            
            json_object *j_mount, *j_uri;
            if (!json_object_object_get_ex(stream_obj, "mount_point", &j_mount)) {
                fprintf(stderr, "Error: Stream %d missing 'mount_point'. Skipping.\n", i+1);
                continue;
            }
            
            if (!json_object_object_get_ex(stream_obj, "source_uri", &j_uri)) {
                fprintf(stderr, "Error: Stream %d missing 'source_uri'. Skipping.\n", i+1);
                continue;
            }
            
            if (json_object_get_type(j_mount) != json_type_string ||
                json_object_get_type(j_uri) != json_type_string) {
                fprintf(stderr, "Error: Stream %d mount_point and source_uri must be strings. Skipping.\n", i+1);
                continue;
            }
            
            const char *mount_str = json_object_get_string(j_mount);
            const char *uri_str = json_object_get_string(j_uri);
            
            // Validate URI and mount point
            if (!validate_uri(uri_str)) {
                fprintf(stderr, "Error: Stream %d has invalid source URI. Skipping.\n", i+1);
                continue;
            }
            
            // Handle mount point format
            if (validate_mount_point(mount_str)) {
                safe_strcpy(config->streams[valid_streams].mount_point, 
                           mount_str, sizeof(config->streams[valid_streams].mount_point));
            } else if (mount_str[0] != '/') {
                snprintf(config->streams[valid_streams].mount_point, 
                        sizeof(config->streams[valid_streams].mount_point), 
                        "/%s", mount_str);
                fprintf(stderr, "Info: Auto-corrected mount point to '%s'\n", 
                       config->streams[valid_streams].mount_point);
            } else {
                fprintf(stderr, "Error: Stream %d has invalid mount point. Skipping.\n", i+1);
                continue;
            }
            
            safe_strcpy(config->streams[valid_streams].source_uri, 
                       uri_str, sizeof(config->streams[valid_streams].source_uri));
            
            // Parse optimization parameters
            json_object *j_optimization;
            if (json_object_object_get_ex(stream_obj, "optimization", &j_optimization) &&
                json_object_get_type(j_optimization) == json_type_object) {
                
                json_object *j_decoder, *j_encoder, *j_latency, *j_affinity;
                
                if (json_object_object_get_ex(j_optimization, "decoder", &j_decoder) &&
                    json_object_get_type(j_decoder) == json_type_string) {
                    const char *dec_str = json_object_get_string(j_decoder);
                    safe_strcpy(config->streams[valid_streams].decoder_params, 
                               dec_str, sizeof(config->streams[valid_streams].decoder_params));
                }
                
                if (json_object_object_get_ex(j_optimization, "encoder", &j_encoder) &&
                    json_object_get_type(j_encoder) == json_type_string) {
                    const char *enc_str = json_object_get_string(j_encoder);
                    safe_strcpy(config->streams[valid_streams].encoder_params, 
                               enc_str, sizeof(config->streams[valid_streams].encoder_params));
                }
                
                if (json_object_object_get_ex(j_optimization, "latency", &j_latency) &&
                    json_object_get_type(j_latency) == json_type_int) {
                    int latency = json_object_get_int(j_latency);
                    if (latency >= 10 && latency <= 500) {
                        config->streams[valid_streams].latency = latency;
                    } else {
                        fprintf(stderr, "Warning: Invalid latency %d for stream %d. Using default 100ms.\n",
                               latency, valid_streams + 1);
                    }
                }
                
                if (json_object_object_get_ex(j_optimization, "thread_affinity", &j_affinity) &&
                    json_object_get_type(j_affinity) == json_type_int) {
                    config->streams[valid_streams].thread_affinity = json_object_get_int(j_affinity);
                }
            }
            
            // Parse AI integration parameters
            json_object *j_ai_integration;
            if (json_object_object_get_ex(stream_obj, "ai_integration", &j_ai_integration) &&
                json_object_get_type(j_ai_integration) == json_type_object) {
                
                json_object *j_enable_ai, *j_ai_target, *j_enable_tee;
                
                if (json_object_object_get_ex(j_ai_integration, "enable_ai_forward", &j_enable_ai) &&
                    json_object_get_type(j_enable_ai) == json_type_boolean) {
                    config->streams[valid_streams].ai_config.enable_ai_forward = 
                        json_object_get_boolean(j_enable_ai);
                }
                
                if (json_object_object_get_ex(j_ai_integration, "ai_target_uri", &j_ai_target) &&
                    json_object_get_type(j_ai_target) == json_type_string) {
                    const char *ai_uri = json_object_get_string(j_ai_target);
                    if (validate_uri(ai_uri)) {
                        safe_strcpy(config->streams[valid_streams].ai_config.ai_target_uri,
                                   ai_uri, sizeof(config->streams[valid_streams].ai_config.ai_target_uri));
                    } else {
                        fprintf(stderr, "Warning: Invalid AI target URI for stream %d\n", valid_streams + 1);
                    }
                }
                
                if (json_object_object_get_ex(j_ai_integration, "enable_tee", &j_enable_tee) &&
                    json_object_get_type(j_enable_tee) == json_type_boolean) {
                    config->streams[valid_streams].ai_config.enable_tee = 
                        json_object_get_boolean(j_enable_tee);
                }
                
                if (config->streams[valid_streams].ai_config.enable_ai_forward) {
                    g_print("AI Integration enabled for stream %s\n", 
                           config->streams[valid_streams].mount_point);
                    g_print("  Target URI: %s\n", 
                           config->streams[valid_streams].ai_config.ai_target_uri);
                    g_print("  Tee enabled: %s\n", 
                           config->streams[valid_streams].ai_config.enable_tee ? "Yes" : "No");
                }
            }
            
            fprintf(stderr, "Configured stream %d: %s => %s\n", 
                   valid_streams + 1, config->streams[valid_streams].mount_point, 
                   config->streams[valid_streams].source_uri);
            
            valid_streams++;
        }
        
        config->stream_count = valid_streams;
    }
    
    // Clean up JSON object
    json_object_put(root);
    
    if (config->stream_count > 0) {
        fprintf(stderr, "Configuration loaded successfully: %d valid streams on port %d\n", 
               config->stream_count, config->server_port);
    } else {
        fprintf(stderr, "Warning: No valid streams configured\n");
    }
    
    return 0;
}

void print_config_summary(const ServerConfig *config) {
    if (!config) {
        fprintf(stderr, "Error: Invalid config pointer\n");
        return;
    }
    
    printf("\n==================== Configuration Summary ====================\n");
    printf("Server Port: %d\n", config->server_port);
    printf("GPU Memory: %d%%\n", config->gpu_memory_percent);
    printf("Zero Copy: %s\n", config->zero_copy ? "Enabled" : "Disabled");
    printf("Performance Profile: %s\n", config->performance_profile);
    printf("Stream Count: %d\n", config->stream_count);
    printf("===============================================================\n");
    
    for (int i = 0; i < config->stream_count; i++) {
        printf("\nStream %d:\n", i + 1);
        printf("  Mount Point: %s\n", config->streams[i].mount_point);
        printf("  Source URI: %s\n", config->streams[i].source_uri);
        printf("  Decoder Params: %s\n", 
               config->streams[i].decoder_params[0] ? config->streams[i].decoder_params : "(default)");
        printf("  Encoder Params: %s\n", 
               config->streams[i].encoder_params[0] ? config->streams[i].encoder_params : "(default)");
        printf("  Latency: %dms\n", config->streams[i].latency);
        
        // Fixed thread affinity display logic
        if (config->streams[i].thread_affinity >= 0) {
            printf("  Thread Affinity: Core %d\n", config->streams[i].thread_affinity);
        } else {
            printf("  Thread Affinity: No affinity\n");
        }
        
        // AI Integration information
        if (config->streams[i].ai_config.enable_ai_forward) {
            printf("  AI Integration: Enabled\n");
            printf("    Target URI: %s\n", config->streams[i].ai_config.ai_target_uri);
            printf("    Tee Output: %s\n", config->streams[i].ai_config.enable_tee ? "Enabled" : "Disabled");
        } else {
            printf("  AI Integration: Disabled\n");
        }
    }
    printf("===============================================================\n\n");
}

// Function to validate configuration integrity
int validate_config(const ServerConfig *config) {
    if (!config) {
        fprintf(stderr, "Error: NULL configuration\n");
        return -1;
    }
    
    // Check port range
    if (config->server_port < 1 || config->server_port > 65535) {
        fprintf(stderr, "Error: Invalid server port %d\n", config->server_port);
        return -1;
    }
    
    // Check GPU memory percentage
    if (config->gpu_memory_percent < MIN_GPU_MEMORY_PERCENT || 
        config->gpu_memory_percent > MAX_GPU_MEMORY_PERCENT) {
        fprintf(stderr, "Error: Invalid GPU memory percentage %d\n", config->gpu_memory_percent);
        return -1;
    }
    
    // Check performance profile
    if (strcmp(config->performance_profile, "low_latency") != 0 &&
        strcmp(config->performance_profile, "high_throughput") != 0 &&
        strcmp(config->performance_profile, "balanced") != 0) {
        fprintf(stderr, "Error: Invalid performance profile '%s'\n", config->performance_profile);
        return -1;
    }
    
    // Check stream configuration
    if (config->stream_count < 0 || config->stream_count > MAX_STREAMS) {
        fprintf(stderr, "Error: Invalid stream count %d\n", config->stream_count);
        return -1;
    }
    
    for (int i = 0; i < config->stream_count; i++) {
        const StreamConfig *stream = &config->streams[i];
        
        // Check mount point
        if (strlen(stream->mount_point) == 0 || stream->mount_point[0] != '/') {
            fprintf(stderr, "Error: Invalid mount point for stream %d\n", i + 1);
            return -1;
        }
        
        // Check source URI
        if (strlen(stream->source_uri) == 0) {
            fprintf(stderr, "Error: Empty source URI for stream %d\n", i + 1);
            return -1;
        }
        
        // Check latency setting
        if (stream->latency < MIN_LATENCY_MS || stream->latency > MAX_LATENCY_MS) {
            fprintf(stderr, "Error: Invalid latency %d for stream %d\n", stream->latency, i + 1);
            return -1;
        }
        
        // Check AI integration settings
        if (stream->ai_config.enable_ai_forward) {
            if (strlen(stream->ai_config.ai_target_uri) == 0) {
                fprintf(stderr, "Error: AI forward enabled but no target URI for stream %d\n", i + 1);
                return -1;
            }
            if (!validate_uri(stream->ai_config.ai_target_uri)) {
                fprintf(stderr, "Error: Invalid AI target URI for stream %d\n", i + 1);
                return -1;
            }
        }
    }
    
    return 0;
}