CC = gcc

# Compilation flags - Add security and debug options
CFLAGS = `pkg-config --cflags gstreamer-1.0 gstreamer-rtsp-server-1.0 json-c` \
         -Wall -Wextra -O2 -g \
         -D_FORTIFY_SOURCE=2 \
         -fstack-protector-strong

# Linking flags
LIBS = `pkg-config --libs gstreamer-1.0 gstreamer-rtsp-server-1.0 json-c` \
       -pthread -lnvbuf_utils

# Object files
OBJS = rtsp_server.o config_parser.o

# Target executable
TARGET = rtsp_server

# Configuration file
CONFIG_FILE = rtsp_server.conf

# Installation directories
PREFIX ?= /usr/local
BINDIR = $(PREFIX)/bin
CONFIGDIR = $(PREFIX)/etc

# Default target
all: $(TARGET)

# Compile executable
$(TARGET): $(OBJS)
	$(CC) -o $(TARGET) $(OBJS) $(LIBS)

# Compile object files
%.o: %.c %.h
	$(CC) $(CFLAGS) -c $<

# Special handling for main file (no corresponding .h file)
rtsp_server.o: rtsp_server.c config_parser.h
	$(CC) $(CFLAGS) -c rtsp_server.c

# Dependencies
config_parser.o: config_parser.c config_parser.h

# Install target
install: $(TARGET)
	install -d $(BINDIR)
	install -d $(CONFIGDIR)
	install -m 755 $(TARGET) $(BINDIR)
	install -m 644 $(CONFIG_FILE) $(CONFIGDIR)

# Uninstall target
uninstall:
	rm -f $(BINDIR)/$(TARGET)
	rm -f $(CONFIGDIR)/$(CONFIG_FILE)

# Clean target
clean:
	rm -f $(TARGET) *.o

# Deep clean
distclean: clean
	rm -f *~ *.bak

# Check dependencies
check-deps:
	@echo "Checking dependencies..."
	@pkg-config --exists gstreamer-1.0 || (echo "ERROR: gstreamer-1.0 not found" && exit 1)
	@pkg-config --exists gstreamer-rtsp-server-1.0 || (echo "ERROR: gstreamer-rtsp-server-1.0 not found" && exit 1)
	@pkg-config --exists json-c || (echo "ERROR: json-c not found" && exit 1)
	@echo "Checking for nvbuf_utils library..."
	@if ! ldconfig -p | grep -q nvbuf_utils; then \
		echo "WARNING: nvbuf_utils library not found. This is required for NVIDIA hardware acceleration."; \
		echo "Please ensure you're running on NVIDIA Jetson platform with proper drivers."; \
	fi
	@echo "All dependencies checked"

# Debug version
debug: CFLAGS += -DDEBUG -O0
debug: $(TARGET)

# Release version
release: CFLAGS += -DNDEBUG -O3
release: clean $(TARGET)

# Run target
run: $(TARGET)
	./$(TARGET)

# Help information
help:
	@echo "Available targets:"
	@echo "  all         - Build the project (default)"
	@echo "  clean       - Remove object files and executable"
	@echo "  install     - Install to system directories"
	@echo "  uninstall   - Remove from system directories"
	@echo "  check-deps  - Check for required dependencies"
	@echo "  debug       - Build debug version"
	@echo "  release     - Build optimized release version"
	@echo "  run         - Build and run the program"
	@echo "  help        - Show this help message"

# Declare phony targets
.PHONY: all clean install uninstall check-deps debug release run help distclean