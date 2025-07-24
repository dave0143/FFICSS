#include <gst/gst.h>
#include <gst/rtsp-server/rtsp-server.h>
#include "config_parser.h"
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <pthread.h>
#include <sched.h>
#include <signal.h>
#include <nvbuf_utils.h>

#define DEFAULT_CONFIG "rtsp_server.conf"
#define MAX_PIPELINE_LEN 4096  // Increased for tee pipelines

// Global variables
static volatile int running = 1;
static pthread_t threads[MAX_STREAMS];
static int active_threads = 0;
static GMainLoop *loop = NULL;
static GstRTSPServer *server = NULL;

// Thread argument structure
typedef struct {
    StreamConfig *stream;
    GstRTSPServer *server;
} ThreadArg;

// AI forwarder thread arguments
typedef struct {
    char source_pipeline[MAX_PIPELINE_LEN];
    char ai_target_uri[MAX_URI_LEN];
    char mount_point[MAX_MOUNT_POINT_LEN];
} AIForwarderArg;

// Signal handler
void signal_handler(int sig) {
    g_print("Received signal %d, shutting down...\n", sig);
    running = 0;
    if (loop) {
        g_main_loop_quit(loop);
    }
}

// Build standard pipeline string
static int build_standard_pipeline(StreamConfig *stream, char *pipeline_str, size_t max_len) {
    if (!stream || !pipeline_str || max_len == 0) {
        return -1;
    }
    
    // Validate parameter length to prevent buffer overflow
    if (strlen(stream->source_uri) > 500 ||
        strlen(stream->decoder_params) > MAX_OPT_PARAM_LEN ||
        strlen(stream->encoder_params) > MAX_OPT_PARAM_LEN) {
        g_printerr("Error: Pipeline parameters too long for stream %s\n", stream->mount_point);
        return -1;
    }
    
    // Build standard pipeline string
    int ret = snprintf(pipeline_str, max_len,
        "( rtspsrc location=%s latency=0 drop-on-latency=true ! "
        "rtph264depay ! h264parse ! "
        "nvv4l2decoder %s ! "
        "nvvideoconvert ! "
        "nvv4l2h264enc %s ! "
        "h264parse ! rtph264pay name=pay0 pt=96 config-interval=-1 )",
        stream->source_uri,
        stream->decoder_params[0] ? stream->decoder_params : "enable-max-performance=1",
        stream->encoder_params[0] ? stream->encoder_params : "bitrate=4000");
    
    if (ret >= (int)max_len) {
        g_printerr("Error: Pipeline string too long for stream %s\n", stream->mount_point);
        return -1;
    }
    
    return 0;
}

// Build tee pipeline string for AI integration
static int build_tee_pipeline(StreamConfig *stream, char *pipeline_str, size_t max_len) {
    if (!stream || !pipeline_str || max_len == 0) {
        return -1;
    }
    
    // Validate parameter length
    if (strlen(stream->source_uri) > 500 ||
        strlen(stream->decoder_params) > MAX_OPT_PARAM_LEN ||
        strlen(stream->encoder_params) > MAX_OPT_PARAM_LEN) {
        g_printerr("Error: Pipeline parameters too long for stream %s\n", stream->mount_point);
        return -1;
    }
    
    // Build tee pipeline - decode once, encode twice (one for RTSP clients, one for AI)
    int ret = snprintf(pipeline_str, max_len,
        "( rtspsrc location=%s latency=0 drop-on-latency=true ! "
        "rtph264depay ! h264parse ! "
        "nvv4l2decoder %s ! "
        "nvvideoconvert ! "
        "tee name=t ! queue max-size-buffers=2 leaky=downstream ! "
        "nvv4l2h264enc %s ! "
        "h264parse ! rtph264pay name=pay0 pt=96 config-interval=-1 )",
        stream->source_uri,
        stream->decoder_params[0] ? stream->decoder_params : "enable-max-performance=1",
        stream->encoder_params[0] ? stream->encoder_params : "bitrate=4000");
    
    if (ret >= (int)max_len) {
        g_printerr("Error: Tee pipeline string too long for stream %s\n", stream->mount_point);
        return -1;
    }
    
    return 0;
}

// AI forwarder thread function
void* ai_forwarder_thread(void* arg) {
    AIForwarderArg *ai_arg = (AIForwarderArg*)arg;
    
    if (!ai_arg) {
        g_printerr("Error: Invalid AI forwarder arguments\n");
        return NULL;
    }
    
    g_print("Starting AI forwarder for %s -> %s\n", ai_arg->mount_point, ai_arg->ai_target_uri);
    
    // Create a separate GStreamer pipeline for AI forwarding
    char ai_pipeline[MAX_PIPELINE_LEN];
    int ret = snprintf(ai_pipeline, sizeof(ai_pipeline),
        "rtspsrc location=rtsp://127.0.0.1:8554%s latency=0 drop-on-latency=true ! "
        "rtph264depay ! h264parse ! "
        "nvv4l2decoder enable-max-performance=1 ! "
        "nvvideoconvert ! "
        "nvv4l2h264enc bitrate=4000 preset-level=2 ! "
        "h264parse ! "
        "rtspclientsink location=%s protocols=tcp",
        ai_arg->mount_point,
        ai_arg->ai_target_uri);
    
    if (ret >= (int)sizeof(ai_pipeline)) {
        g_printerr("Error: AI pipeline string too long\n");
        return NULL;
    }
    
    GstElement *ai_pipeline_element = gst_parse_launch(ai_pipeline, NULL);
    if (!ai_pipeline_element) {
        g_printerr("Error: Failed to create AI forwarding pipeline for %s\n", ai_arg->mount_point);
        return NULL;
    }
    
    // Start the AI forwarding pipeline
    GstStateChangeReturn state_ret = gst_element_set_state(ai_pipeline_element, GST_STATE_PLAYING);
    if (state_ret == GST_STATE_CHANGE_FAILURE) {
        g_printerr("Error: Failed to start AI forwarding pipeline for %s\n", ai_arg->mount_point);
        gst_object_unref(ai_pipeline_element);
        return NULL;
    }
    
    g_print("AI forwarding pipeline started successfully for %s\n", ai_arg->mount_point);
    
    // Wait for pipeline to run
    while (running) {
        sleep(1);
        
        // Check pipeline state
        GstState state;
        GstStateChangeReturn ret = gst_element_get_state(ai_pipeline_element, &state, NULL, 0);
        if (ret == GST_STATE_CHANGE_FAILURE || state == GST_STATE_NULL) {
            g_printerr("Warning: AI forwarding pipeline failed for %s\n", ai_arg->mount_point);
            break;
        }
    }
    
    // Cleanup
    gst_element_set_state(ai_pipeline_element, GST_STATE_NULL);
    gst_object_unref(ai_pipeline_element);
    
    g_print("AI forwarder thread ended for %s\n", ai_arg->mount_point);
    return NULL;
}

// Enhanced stream thread function
void* stream_thread(void* arg) {
    ThreadArg *targ = (ThreadArg*)arg;
    StreamConfig *stream = targ->stream;
    GstRTSPServer *server = targ->server;
    
    if (!stream || !server) {
        g_printerr("Error: Invalid thread arguments\n");
        return NULL;
    }
    
    g_print("Initializing stream thread for %s\n", stream->mount_point);
    
    // Set thread affinity
    if (stream->thread_affinity >= 0) {
        cpu_set_t cpuset;
        CPU_ZERO(&cpuset);
        CPU_SET(stream->thread_affinity, &cpuset);
        
        if (pthread_setaffinity_np(pthread_self(), sizeof(cpu_set_t), &cpuset) != 0) {
            g_printerr("Warning: Failed to set thread affinity for %s to core %d\n",
                      stream->mount_point, stream->thread_affinity);
        } else {
            g_print("Stream %s bound to CPU core %d\n", stream->mount_point, stream->thread_affinity);
        }
    }
    
    // Get mount points
    GstRTSPMountPoints *mounts = gst_rtsp_server_get_mount_points(server);
    if (!mounts) {
        g_printerr("Error: Failed to get mount points for stream %s\n", stream->mount_point);
        return NULL;
    }
    
    // Create media factory
    GstRTSPMediaFactory *factory = gst_rtsp_media_factory_new();
    if (!factory) {
        g_printerr("Error: Failed to create media factory for stream %s\n", stream->mount_point);
        g_object_unref(mounts);
        return NULL;
    }
    
    // Build pipeline string
    char pipeline_str[MAX_PIPELINE_LEN];
    
    if (stream->ai_config.enable_tee) {
        // Use tee pipeline for AI integration
        if (build_tee_pipeline(stream, pipeline_str, sizeof(pipeline_str)) != 0) {
            g_printerr("Error: Failed to build tee pipeline for stream %s\n", stream->mount_point);
            g_object_unref(factory);
            g_object_unref(mounts);
            return NULL;
        }
        g_print("Using tee pipeline for %s (AI integration enabled)\n", stream->mount_point);
    } else {
        // Use standard pipeline
        if (build_standard_pipeline(stream, pipeline_str, sizeof(pipeline_str)) != 0) {
            g_printerr("Error: Failed to build pipeline for stream %s\n", stream->mount_point);
            g_object_unref(factory);
            g_object_unref(mounts);
            return NULL;
        }
    }
    
    // Set pipeline
    gst_rtsp_media_factory_set_launch(factory, pipeline_str);
    
    // Set media factory properties
    gst_rtsp_media_factory_set_shared(factory, TRUE);
    gst_rtsp_media_factory_set_latency(factory, stream->latency);
    
    // Add to mount points
    gst_rtsp_mount_points_add_factory(mounts, stream->mount_point, factory);
    g_object_unref(mounts);
    
    g_print("Stream %s initialized successfully\n", stream->mount_point);
    g_print("  Pipeline: %s\n", pipeline_str);
    g_print("  Target latency: %dms\n", stream->latency);
    g_print("  AI Integration: %s\n", stream->ai_config.enable_ai_forward ? "Enabled" : "Disabled");
    
    // Start AI forwarder if enabled
    if (stream->ai_config.enable_ai_forward && strlen(stream->ai_config.ai_target_uri) > 0) {
        // Wait a bit for the main stream to be ready
        sleep(2);
        
        // Create AI forwarder thread
        pthread_t ai_thread;
        AIForwarderArg *ai_arg = malloc(sizeof(AIForwarderArg));
        if (ai_arg) {
            strcpy(ai_arg->source_pipeline, pipeline_str);
            strcpy(ai_arg->ai_target_uri, stream->ai_config.ai_target_uri);
            strcpy(ai_arg->mount_point, stream->mount_point);
            
            if (pthread_create(&ai_thread, NULL, ai_forwarder_thread, ai_arg) != 0) {
                g_printerr("Error: Failed to create AI forwarder thread for %s\n", stream->mount_point);
                free(ai_arg);
            } else {
                g_print("AI forwarder thread created for %s\n", stream->mount_point);
                // Note: We don't join this thread in this simple implementation
                // In production, you'd want to track and properly cleanup these threads
            }
        }
    }
    
    return NULL;
}

// Apply performance configuration
static void apply_performance_profile(const char *profile) {
    if (strcmp(profile, "low_latency") == 0) {
        g_print("Applying low latency profile settings...\n");
        if (system("sudo jetson_clocks") != 0) {
            g_printerr("Warning: Failed to execute jetson_clocks\n");
        }
        if (system("sudo echo performance > /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor") != 0) {
            g_printerr("Warning: Failed to set CPU governor\n");
        }
    } 
    else if (strcmp(profile, "high_throughput") == 0) {
        g_print("Applying high throughput profile settings...\n");
        if (system("sudo sysctl -w net.core.rmem_max=16777216") != 0) {
            g_printerr("Warning: Failed to set rmem_max\n");
        }
        if (system("sudo sysctl -w net.core.wmem_max=16777216") != 0) {
            g_printerr("Warning: Failed to set wmem_max\n");
        }
    }
    else {
        g_print("Using balanced performance profile\n");
    }
}

// Cleanup function
static void cleanup_resources(void) {
    g_print("Cleaning up resources...\n");
    
    // Wait for all threads to complete
    for (int i = 0; i < active_threads; i++) {
        pthread_join(threads[i], NULL);
    }
    
    // Cleanup GStreamer resources
    if (server) {
        g_object_unref(server);
        server = NULL;
    }
    
    if (loop) {
        g_main_loop_unref(loop);
        loop = NULL;
    }
}

int main(int argc, char *argv[]) {
    ServerConfig config;
    char port_str[16];

    // Register signal handlers
    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);

    // Initialize GStreamer
    gst_init(&argc, &argv);

    // Parse configuration file
    if (parse_config(DEFAULT_CONFIG, &config) < 0) {
        g_printerr("Error: Failed to load configuration file. Using defaults.\n");
        config.server_port = 8554;
        config.stream_count = 0;
        return -1;
    }

    g_print("Configuration file loaded successfully!\n");

    // Validate configuration integrity
    if (validate_config(&config) != 0) {
        g_printerr("Error: Configuration validation failed\n");
        return -1;
    }

    // Print configuration summary
    print_config_summary(&config);

    // Set GPU memory allocation
    if (config.gpu_memory_percent > 0) {
        if (NvBufSurfaceSetGlobalAllocParams(
                config.zero_copy ? NVBUF_MEM_CUDA_UNIFIED : NVBUF_MEM_CUDA_DEVICE,
                config.gpu_memory_percent, 
                0) != 0) {
            g_printerr("Warning: Failed to set GPU memory allocation\n");
        } else {
            g_print("GPU memory allocation set to %d%% (%s)\n", 
                   config.gpu_memory_percent,
                   config.zero_copy ? "Unified Memory" : "Device Memory");
        }
    }

    // Create RTSP server
    server = gst_rtsp_server_new();
    if (!server) {
        g_printerr("Error: Failed to create RTSP server\n");
        return -1;
    }
    
    snprintf(port_str, sizeof(port_str), "%d", config.server_port);
    gst_rtsp_server_set_service(server, port_str);
    
    g_print("\n===========================================\n");
    g_print(" Starting Enhanced NVIDIA Orin RTSP Server\n");
    g_print(" Server Port: %d\n", config.server_port);
    g_print(" Performance Profile: %s\n", config.performance_profile);
    g_print(" Number of Stream Channels: %d\n", config.stream_count);
    g_print(" AI Integration: Enabled\n");
    g_print("===========================================\n\n");

    // Create threads
    ThreadArg thread_args[MAX_STREAMS];
    
    for (int i = 0; i < config.stream_count; i++) {
        thread_args[i].stream = &config.streams[i];
        thread_args[i].server = server;
        
        if (pthread_create(&threads[active_threads], NULL, stream_thread, &thread_args[i]) != 0) {
            g_printerr("Error: Failed to create thread for stream %s\n", 
                      config.streams[i].mount_point);
            continue;
        }
        active_threads++;
    }

    if (active_threads == 0) {
        g_printerr("Error: No streams could be initialized\n");
        cleanup_resources();
        return -1;
    }

    // Wait for thread initialization
    sleep(2);
    
    // Attach server to main context
    if (gst_rtsp_server_attach(server, NULL) == 0) {
        g_printerr("Error: Failed to bind to port %d! Port may be in use.\n", config.server_port);
        cleanup_resources();
        return -1;
    }

    g_print("\nEnhanced RTSP Server started successfully!\n");
    g_print("Active streams: %d\n", active_threads);
    g_print("Server Address: rtsp://<Orin_IP>:%s/<mount_point>\n", port_str);
    
    // Show AI integration info
    for (int i = 0; i < config.stream_count; i++) {
        if (config.streams[i].ai_config.enable_ai_forward) {
            g_print("AI Forward: %s -> %s\n", 
                   config.streams[i].mount_point, 
                   config.streams[i].ai_config.ai_target_uri);
        }
    }
    
    g_print("Press Ctrl+C to stop the server\n\n");

    // Apply performance configuration
    apply_performance_profile(config.performance_profile);

    // Run main loop
    loop = g_main_loop_new(NULL, FALSE);
    g_main_loop_run(loop);

    // Cleanup resources
    cleanup_resources();

    return 0;
}