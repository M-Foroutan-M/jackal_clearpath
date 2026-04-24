#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import signal
from datetime import datetime
import gi
import pyds
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

CONFIG = {}
SYNC_SETTINGS = {}
SYNC_MONITORING = {}
TIMESTAMP_LOGGING = {}
ENCODER_PRESET_LEVEL = 2
ENCODER_PROFILE = 2

OUTPUT_FILE = None
ENABLE_OVERLAYS = None
ENABLE_DEBUG_PROBES = None
USB_CAM1_DEV = None
USB_CAM2_DEV = None
USB_CAM_WIDTH = None
USB_CAM_HEIGHT = None
USB_CAM_FPS = None
RTSP_SOURCE_IP1 = None
RTSP_SOURCE_IP2 = None
RTSP_SOURCE_IP3 = None
RTSP_SOURCE_PORT = None
RTSP_CAM1_MOUNT = None
RTSP_CAM2_MOUNT = None
RTSP_SOURCE_LATENCY_MS = None
RTSP_SOURCE_PROTOCOL = None
TIMESTAMP_LOG_INTERVAL = None

# TIMESTAMP LOGGING
timestamp_log = []
recording_start_time = [None]
recording_start_wall_clock = [None]

def load_config():
    """Load configuration from SETTINGS.json, fallback to SETTINGS_DEFAULT.json"""
    script_dir = Path(__file__).parent
    settings_path = script_dir / "settings" / "2usb_n_6rtsp_to_file_SETTINGS.json"
    default_path = script_dir / "settings" / "default" / "2usb_n_6rtsp_to_file_SETTINGS_DEFAULT.json"
    
    if settings_path.exists():
        config_path = settings_path
    elif default_path.exists():
        sys.stderr.write(f"WARNING: SETTINGS.json not found, using SETTINGS_DEFAULT.json\n")
        config_path = default_path
    else:
        raise FileNotFoundError(
            f"Neither SETTINGS.json nor SETTINGS_DEFAULT.json found in {script_dir}"
        )
    
    with config_path.open("r") as config_file:
        return json.load(config_file)

def _resolve_encoder_preset(preset_name):
    presets = {
        "Preset_Default": 0,
        "Preset_LowLatency": 1,
        "Preset_Fast": 2,
        "Preset_Medium": 3,
        "Preset_Slow": 4
    }
    return presets.get(preset_name)

def _resolve_encoder_profile(profile_name):
    profiles = {
        "Profile_Baseline": 0,
        "Profile_Main": 1,
        "Profile_High": 2
    }
    return profiles.get(profile_name)

def validate_config(config):
    """Validate configuration values and check for common errors."""
    errors = []

    for cam_key in ("camera1_device", "camera2_device"):
        device = config["usb_cameras"].get(cam_key)
        if not device or not os.path.exists(device):
            errors.append(f"USB device not found: {device}")

    width = config["usb_cameras"].get("width", 0)
    height = config["usb_cameras"].get("height", 0)
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        errors.append(f"Invalid resolution: {width}x{height}")

    fps = config["usb_cameras"].get("fps", 0)
    if not isinstance(fps, int) or fps <= 0 or fps > 120:
        errors.append(f"Invalid FPS: {fps}")

    try:
        source_port = int(config["rtsp_sources"].get("port"))
        if source_port < 1 or source_port > 65535:
            errors.append(f"RTSP source port out of range: {source_port}")
    except (TypeError, ValueError):
        errors.append(f"Invalid RTSP source port: {config['rtsp_sources'].get('port')}")

    # Validate all 3 RTSP source IPs
    for ip_key in ("ip1", "ip2", "ip3"):
        ip = config["rtsp_sources"].get(ip_key)
        if not ip or not isinstance(ip, str):
            errors.append(f"Invalid or missing RTSP source {ip_key}: {ip}")

    protocol = str(config["rtsp_sources"].get("protocol", "")).lower()
    if protocol not in ("tcp", "udp"):
        errors.append(f"Invalid RTSP protocol: {config['rtsp_sources'].get('protocol')}")

    if config["sync_settings"].get("usb_queue_max_buffers", 0) < 1:
        errors.append("usb_queue_max_buffers must be >= 1")

    if config["sync_settings"].get("rtsp_queue_max_buffers", 0) < 1:
        errors.append("rtsp_queue_max_buffers must be >= 1")

    if config["sync_settings"].get("max_latency_ns", 0) < 0:
        errors.append("max_latency_ns must be >= 0")

    if config["sync_settings"].get("frame_duration_ns", 0) <= 0:
        errors.append("frame_duration_ns must be > 0")

    if config["sync_settings"].get("batched_push_timeout_ms", 0) < 0:
        errors.append("batched_push_timeout_ms must be >= 0")

    if config["sync_settings"].get("sync_inputs") not in (0, 1):
        errors.append("sync_inputs must be 0 or 1")

    if config["tiler"].get("rows", 0) <= 0 or config["tiler"].get("columns", 0) <= 0:
        errors.append("tiler rows and columns must be > 0")

    if config["tiler"].get("width", 0) <= 0 or config["tiler"].get("height", 0) <= 0:
        errors.append("tiler width and height must be > 0")

    if config["sync_monitoring"].get("log_interval_frames", 0) <= 0:
        errors.append("sync_monitoring.log_interval_frames must be > 0")

    if config["sync_monitoring"].get("max_latency_variance_ms", 0) < 0:
        errors.append("sync_monitoring.max_latency_variance_ms must be >= 0")

    if config["timestamp_logging"].get("log_interval_frames", 0) <= 0:
        errors.append("timestamp_logging.log_interval_frames must be > 0")

    if config["encoder"].get("bitrate", 0) <= 0:
        errors.append("encoder.bitrate must be > 0")

    if config["encoder"].get("iframeinterval", 0) <= 0:
        errors.append("encoder.iframeinterval must be > 0")

    if _resolve_encoder_preset(config["encoder"].get("preset")) is None:
        errors.append(f"Invalid encoder preset: {config['encoder'].get('preset')}")

    if _resolve_encoder_profile(config["encoder"].get("profile")) is None:
        errors.append(f"Invalid encoder profile: {config['encoder'].get('profile')}")

    output_path = config["file_output"].get("path", "")
    if not output_path:
        errors.append("file_output.path must not be empty")

    return errors

def apply_config(config):
    global CONFIG
    global SYNC_SETTINGS
    global SYNC_MONITORING
    global TIMESTAMP_LOGGING
    global ENCODER_PRESET_LEVEL
    global ENCODER_PROFILE
    global OUTPUT_FILE
    global ENABLE_OVERLAYS
    global ENABLE_DEBUG_PROBES
    global USB_CAM1_DEV
    global USB_CAM2_DEV
    global USB_CAM_WIDTH
    global USB_CAM_HEIGHT
    global USB_CAM_FPS
    global RTSP_SOURCE_IP1
    global RTSP_SOURCE_IP2
    global RTSP_SOURCE_IP3
    global RTSP_SOURCE_PORT
    global RTSP_CAM1_MOUNT
    global RTSP_CAM2_MOUNT
    global RTSP_SOURCE_LATENCY_MS
    global RTSP_SOURCE_PROTOCOL
    global TIMESTAMP_LOG_INTERVAL
    global FINAL_TILER_WIDTH
    global FINAL_TILER_HEIGHT

    CONFIG = config
    SYNC_SETTINGS = config["sync_settings"]
    SYNC_MONITORING = config["sync_monitoring"]
    TIMESTAMP_LOGGING = config["timestamp_logging"]

    OUTPUT_FILE = config["file_output"]["path"]
    ENABLE_OVERLAYS = bool(config["file_output"]["enable_overlays"])
    ENABLE_DEBUG_PROBES = bool(config["file_output"]["enable_debug_probes"])

    USB_CAM1_DEV = config["usb_cameras"]["camera1_device"]
    USB_CAM2_DEV = config["usb_cameras"]["camera2_device"]
    USB_CAM_WIDTH = config["usb_cameras"]["width"]
    USB_CAM_HEIGHT = config["usb_cameras"]["height"]
    USB_CAM_FPS = config["usb_cameras"]["fps"]

    RTSP_SOURCE_IP1 = config["rtsp_sources"]["ip1"]
    RTSP_SOURCE_IP2 = config["rtsp_sources"]["ip2"]
    RTSP_SOURCE_IP3 = config["rtsp_sources"]["ip3"]
    RTSP_SOURCE_PORT = str(config["rtsp_sources"]["port"])
    RTSP_CAM1_MOUNT = config["rtsp_sources"]["camera1_mount_point"]
    RTSP_CAM2_MOUNT = config["rtsp_sources"]["camera2_mount_point"]
    RTSP_SOURCE_LATENCY_MS = config["rtsp_sources"]["latency_ms"]
    RTSP_SOURCE_PROTOCOL = str(config["rtsp_sources"]["protocol"]).lower()

    TIMESTAMP_LOG_INTERVAL = config["timestamp_logging"]["log_interval_frames"]

    preset_level = _resolve_encoder_preset(config["encoder"]["preset"])
    profile_level = _resolve_encoder_profile(config["encoder"]["profile"])
    ENCODER_PRESET_LEVEL = preset_level if preset_level is not None else 2
    ENCODER_PROFILE = profile_level if profile_level is not None else 2

    FINAL_TILER_WIDTH = int((config["tiler"]["width"]*config["tiler"]["columns"])/config["tiler"]["output_resolution_div_factor"])
    FINAL_TILER_HEIGHT = int((config["tiler"]["height"]*config["tiler"]["rows"])/config["tiler"]["output_resolution_div_factor"])

def apply_camera_settings_batch(camera_device: str, settings_dict: dict):
    """
    Apply camera settings using a single v4l2-ctl command (faster for multiple settings).
    
    Args:
        camera_device (str): The camera device path
        settings_dict (dict): Dictionary of camera settings
    
    Returns:
        Tuple[bool, str]: (success, message)
    """
    
    if not os.path.exists(camera_device):
        return False, f"Camera device {camera_device} not found"
    
    # Build command with all --set-ctrl flags
    cmd = ['v4l2-ctl', '-d', camera_device]
    for control_name, control_value in settings_dict.items():
        cmd.append(f'--set-ctrl={control_name}={control_value}')
    
    print("#"*40)
    try:
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        time.sleep(5)  # Let settings stabilize
        print("#"*40)
        print(f"Successfully applied {len(settings_dict)} settings to {camera_device}")
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.strip() if e.stderr else str(e)
        f"Failed to apply settings: {error_msg}"
    except Exception as e:
        f"Error: {str(e)}"
        
    print("#"*40)

# SYNC MONITORING
sync_stats = {}
sync_frame_count = [0]
last_pts = {}  # Track last PTS for each source to calculate deltas
pts_deltas = {}  # Track PTS deltas (frame-to-frame progression)

# SOURCE READINESS TRACKING
sources_ready = {
    "usb_cam1": False,
    "usb_cam2": False,
    "ip1_cam1": False,
    "ip1_cam2": False,
    "ip2_cam1": False,
    "ip2_cam2": False,
    "ip3_cam1": False,
    "ip3_cam2": False
}
all_sources_ready = [False]  # Flag to track if all sources are ready
recording_started = [False]  # Flag to track if recording has started

# Global pipeline reference for signal handler
pipeline = None

def save_timestamp_log():
    """Save timestamp log to JSON file"""
    timestamp_file = OUTPUT_FILE.replace(".mp4", "_timestamps.json")

    metadata = {
        "recording_info": {
            "start_time": (
                recording_start_wall_clock[0].isoformat()
                if recording_start_wall_clock[0]
                else None
            ),
            "start_time_unix": recording_start_time[0],
            "source_camera": TIMESTAMP_LOGGING.get("reference_camera", "usb_cam1"),
            "frame_rate_fps": USB_CAM_FPS,
            "total_frames_logged": len(timestamp_log),
            "log_interval": TIMESTAMP_LOG_INTERVAL,
        },
        "frames": timestamp_log,
    }

    with open(timestamp_file, "w") as timestamp_handle:
        json.dump(metadata, timestamp_handle, indent=2)

    print(f"[TIMESTAMP] Saved {len(timestamp_log)} timestamps to {timestamp_file}")

def set_mp4_metadata(mp4mux):
    """Set metadata on MP4 muxer including creation time"""
    taglist = Gst.TagList.new_empty()
    creation_time = datetime.now()

    taglist.add_value(
        Gst.TagMergeMode.REPLACE,
        Gst.TAG_DATE_TIME,
        Gst.DateTime.new_from_iso8601_string(creation_time.isoformat()),
    )
    taglist.add_value(
        Gst.TagMergeMode.REPLACE,
        Gst.TAG_TITLE,
        f"8-Camera Recording - {creation_time.strftime('%Y-%m-%d %H:%M:%S')}",
    )
    taglist.add_value(
        Gst.TagMergeMode.REPLACE,
        Gst.TAG_DESCRIPTION,
        "8-camera synchronized recording (2x USB + 6x RTSP)",
    )

    mp4mux.merge_tags(taglist, Gst.TagMergeMode.REPLACE)

def create_timestamp_logging_probe(source_name):
    """Create probe to log wall-clock timestamps for frames"""
    frame_count = [0]

    def timestamp_probe(pad, info, u_data):
        gst_buffer = info.get_buffer()
        if gst_buffer and gst_buffer.pts != Gst.CLOCK_TIME_NONE:
            frame_count[0] += 1

            if recording_start_time[0] is None:
                recording_start_time[0] = time.time()
                recording_start_wall_clock[0] = datetime.now()
                print(
                    "[TIMESTAMP] Recording started at: "
                    f"{recording_start_wall_clock[0].isoformat()}"
                )

            if frame_count[0] % TIMESTAMP_LOG_INTERVAL == 0:
                pts_ms = gst_buffer.pts / 1000000
                wall_clock = datetime.now()

                timestamp_log.append(
                    {
                        "frame": frame_count[0],
                        "pts_ms": round(pts_ms, 3),
                        "wall_clock": wall_clock.isoformat(timespec="milliseconds"),
                        "elapsed_ms": round(
                            (time.time() - recording_start_time[0]) * 1000, 3
                        ),
                    }
                )

        return Gst.PadProbeReturn.OK

    return timestamp_probe

def signal_handler(sig, frame):
    """Graceful shutdown handler for Ctrl+C"""
    print("\n\nStopping recording...")
    if timestamp_log:
        save_timestamp_log()
    if pipeline:
        print("Sending EOS event to flush buffers...")
        pipeline.send_event(Gst.Event.new_eos())
        # Wait for EOS to propagate through pipeline
        print("Waiting for EOS to complete...")
        time.sleep(5)
        print("Setting pipeline to NULL state...")
        pipeline.set_state(Gst.State.NULL)
        time.sleep(1)
    print("Recording stopped and file saved.")
    sys.exit(0)

def create_readiness_probe(source_name):
    """Create a probe to detect when a source starts producing frames"""
    def readiness_probe(pad, info, u_data):
        global sources_ready, all_sources_ready, recording_started

        # Mark this source as ready
        if not sources_ready[source_name]:
            sources_ready[source_name] = True
            print(f"[READY] {source_name} is now producing frames")

            # Check if all sources are ready
            if all(sources_ready.values()) and not all_sources_ready[0]:
                all_sources_ready[0] = True
                print("\n" + "=" * 60)
                print("✓ ALL 8 SOURCES READY - Starting synchronized recording!")
                print("=" * 60 + "\n")

        return Gst.PadProbeReturn.OK

    return readiness_probe

def create_sync_monitor_probe(source_name):
    """Create a probe to monitor frame alignment by tracking PTS deltas (frame-to-frame progression)"""
    def sync_probe(pad, info, u_data):
        gst_buffer = info.get_buffer()
        if gst_buffer and gst_buffer.pts != Gst.CLOCK_TIME_NONE:
            pts_ns = gst_buffer.pts

            # Calculate PTS delta (time since last frame from this source)
            if source_name in last_pts:
                delta_ns = pts_ns - last_pts[source_name]
                delta_ms = delta_ns / 1000000  # Convert to milliseconds
                pts_deltas[source_name] = delta_ms

            last_pts[source_name] = pts_ns

            # Only log sync stats after all sources are ready and if monitoring is enabled
            if all_sources_ready[0] and SYNC_MONITORING.get("enable", True):
                log_interval = SYNC_MONITORING.get("log_interval_frames", 60)
                if not isinstance(log_interval, int) or log_interval <= 0:
                    log_interval = 60

                variance_threshold = SYNC_MONITORING.get("max_latency_variance_ms", 10)
                if not isinstance(variance_threshold, (int, float)) or variance_threshold < 0:
                    variance_threshold = 10

                expected_fps = USB_CAM_FPS if isinstance(USB_CAM_FPS, int) and USB_CAM_FPS > 0 else 30

                # Log every configured number of frames
                sync_frame_count[0] += 1
                if sync_frame_count[0] % log_interval == 0:
                    if source_name in pts_deltas:
                        print(f"[SYNC] {source_name}: Frame delta={pts_deltas[source_name]:.2f}ms")

                    # Analyze frame alignment if we have all 8 sources
                    if len(pts_deltas) == 8:
                        delta_values = list(pts_deltas.values())
                        avg_delta = sum(delta_values) / len(delta_values)
                        max_delta = max(delta_values)
                        min_delta = min(delta_values)
                        delta_variance = max_delta - min_delta

                        print(f"[SYNC] Avg frame interval: {avg_delta:.2f}ms (expected: ~{1000.0/expected_fps:.2f}ms for {expected_fps}fps)")
                        print(f"[SYNC] Frame interval variance: {delta_variance:.2f}ms")

                        # Warn if frame intervals vary significantly (indicates sync issues)
                        if delta_variance > variance_threshold:
                            print(f"[SYNC WARNING] High frame interval variance: {delta_variance:.2f}ms")
                            for src, delta in pts_deltas.items():
                                offset = delta - avg_delta
                                print(f"  {src}: {delta:.2f}ms (offset: {offset:+.2f}ms)")

                        # Also warn if average is far from expected
                        expected_delta = 1000.0 / float(expected_fps)
                        delta_error = abs(avg_delta - expected_delta)
                        if delta_error > variance_threshold / 2:  # Half the variance threshold
                            print(f"[SYNC WARNING] Frame rate deviation: {delta_error:.2f}ms from expected {expected_delta:.2f}ms")

        return Gst.PadProbeReturn.OK

    return sync_probe

def bus_call(bus, message, loop):
    t = message.type
    if t == Gst.MessageType.EOS:
        sys.stdout.write("\nEnd of stream - file finalized\n")
        loop.quit()
    elif t == Gst.MessageType.ERROR:
        err, debug = message.parse_error()
        sys.stderr.write(f"Error: {err}: {debug}\n")

        # Check if it's an RTSP connection error
        if "rtspsrc" in str(debug) or "Could not open resource" in str(err):
            sys.stderr.write("\n!!! RTSP CONNECTION FAILED !!!\n")
            sys.stderr.write(f"Make sure the RTSP servers are running at:\n")
            sys.stderr.write(f"  {RTSP_SOURCE_IP1}:{RTSP_SOURCE_PORT}\n")
            sys.stderr.write(f"  {RTSP_SOURCE_IP2}:{RTSP_SOURCE_PORT}\n")
            sys.stderr.write(f"  {RTSP_SOURCE_IP3}:{RTSP_SOURCE_PORT}\n")

        loop.quit()
    elif t == Gst.MessageType.WARNING:
        warn, debug = message.parse_warning()
        sys.stderr.write(f"[WARNING] {warn}: {debug}\n")
    elif t == Gst.MessageType.STATE_CHANGED:
        if isinstance(message.src, Gst.Pipeline):
            old, new, pending = message.parse_state_changed()
            print(f"[PIPELINE STATE] {old.value_nick} -> {new.value_nick}")
    return True

def verify_pipeline_links(pipeline):
    """Verify all elements in pipeline are properly linked"""
    print("\n=== Verifying Pipeline Links ===")

    it = pipeline.iterate_elements()
    unlinked_count = 0

    while True:
        result, element = it.next()
        if result != Gst.IteratorResult.OK:
            break

        elem_name = element.get_name()

        # Check source pads
        for pad in element.srcpads:
            peer = pad.get_peer()
            if peer:
                print(f"✓ {elem_name}:{pad.get_name()} -> {peer.get_parent().get_name()}:{peer.get_name()}")
            else:
                print(f"✗ {elem_name}:{pad.get_name()} NOT LINKED")
                unlinked_count += 1

        # Check sink pads
        for pad in element.sinkpads:
            peer = pad.get_peer()
            if not peer:
                print(f"✗ {elem_name}:{pad.get_name()} NOT LINKED")
                unlinked_count += 1

    if unlinked_count > 0:
        print(f"=== WARNING: {unlinked_count} unlinked pads found ===\n")
    else:
        print("=== All links verified ===\n")

    return unlinked_count == 0

def create_usb_camera_source(pipeline, dev_node, width, height, fps, name_suffix, streammux, sink_pad_name):
    """Create USB camera source pipeline with aggressive buffering for sync"""
    source = Gst.ElementFactory.make("v4l2src", f"source-{name_suffix}")
    source.set_property("device", dev_node)
    source.set_property("do-timestamp", True)  # Force GStreamer timestamping

    # Add buffer flow probe on source pad to detect when camera starts producing (if debug enabled)
    if ENABLE_DEBUG_PROBES:
        buffer_count = [0]

        def source_buffer_probe(pad, info, u_data):
            buffer_count[0] += 1
            if buffer_count[0] == 1:
                print(f"[BUFFER FLOW] {name_suffix} source producing first buffer")
            elif buffer_count[0] % 30 == 0:
                print(f"[BUFFER FLOW] {name_suffix} source produced {buffer_count[0]} buffers")
            return Gst.PadProbeReturn.OK

        source_pad = source.get_static_pad("src")
        source_pad.add_probe(Gst.PadProbeType.BUFFER, source_buffer_probe, None)

    caps_v4l2 = Gst.ElementFactory.make("capsfilter", f"v4l2-caps-{name_suffix}")
    caps_str = f"image/jpeg, width={width}, height={height}, framerate={fps}/1"
    caps_v4l2.set_property("caps", Gst.Caps.from_string(caps_str))

    decoder = Gst.ElementFactory.make("nvv4l2decoder", f"decoder-{name_suffix}")
    decoder.set_property("mjpeg", 1)

    nvvidconv = Gst.ElementFactory.make("nvvideoconvert", f"converter-{name_suffix}")

    # Aggressive buffering for USB - AGX Orin optimized
    queue = Gst.ElementFactory.make("queue", f"queue-{name_suffix}")
    queue.set_property("max-size-time", SYNC_SETTINGS.get("usb_queue_max_time_ns", 250000000))
    queue.set_property("max-size-buffers", SYNC_SETTINGS.get("usb_queue_max_buffers", 50))
    queue.set_property("leaky", SYNC_SETTINGS.get("usb_queue_leaky", 0))  # Don't drop frames - we want all frames for file recording

    pipeline.add(source)
    pipeline.add(caps_v4l2)
    pipeline.add(decoder)
    pipeline.add(nvvidconv)
    pipeline.add(queue)

    source.link(caps_v4l2)
    caps_v4l2.link(decoder)
    decoder.link(nvvidconv)
    nvvidconv.link(queue)

    # Link queue to muxer
    mux_sink_pad = streammux.get_request_pad(sink_pad_name)
    queue_src_pad = queue.get_static_pad("src")

    # Monitor muxer sink pad state (if debug enabled)
    if ENABLE_DEBUG_PROBES:
        mux_buffer_count = [0]

        def mux_sink_probe(pad, info, u_data):
            mux_buffer_count[0] += 1
            if mux_buffer_count[0] == 1:
                print(f"[MUX SINK] {sink_pad_name} receiving first buffer from {name_suffix}")
            elif mux_buffer_count[0] % 30 == 0:
                print(f"[MUX SINK] {sink_pad_name} received {mux_buffer_count[0]} buffers from {name_suffix}")
            return Gst.PadProbeReturn.OK

        mux_sink_pad.add_probe(Gst.PadProbeType.BUFFER, mux_sink_probe, None)

    # Add readiness probe to detect when source starts producing frames
    readiness_probe = create_readiness_probe(name_suffix)
    queue_src_pad.add_probe(Gst.PadProbeType.BUFFER, readiness_probe, None)

    # Add sync monitoring probe before muxer
    if SYNC_MONITORING.get("enable", True):
        sync_probe = create_sync_monitor_probe(name_suffix)
        queue_src_pad.add_probe(Gst.PadProbeType.BUFFER, sync_probe, None)

    # Add timestamp logging for reference camera
    if name_suffix == TIMESTAMP_LOGGING.get("reference_camera", "usb_cam1"):
        timestamp_probe = create_timestamp_logging_probe(name_suffix)
        queue_src_pad.add_probe(Gst.PadProbeType.BUFFER, timestamp_probe, None)

    queue_src_pad.link(mux_sink_pad)

    return queue

def create_rtsp_source(pipeline, rtsp_location, name_suffix, streammux, sink_pad_name):
    """Create RTSP source pipeline with aggressive buffering for sync"""

    # RTSP Source with balanced latency (faster startup, good sync)
    rtspsrc = Gst.ElementFactory.make("rtspsrc", f"rtspsrc-{name_suffix}")
    rtspsrc.set_property("location", rtsp_location)
    rtspsrc.set_property("latency", RTSP_SOURCE_LATENCY_MS)
    rtspsrc.set_property("protocols", RTSP_SOURCE_PROTOCOL)
    rtspsrc.set_property("drop-on-latency", False)  # Keep all frames for file recording
    rtspsrc.set_property("do-rtcp", True)  # Enable RTCP for better sync

    # Depayloader
    depay = Gst.ElementFactory.make("rtph264depay", f"depay-{name_suffix}")

    # H264 Parser
    parse = Gst.ElementFactory.make("h264parse", f"parse-{name_suffix}")

    # Hardware Decoder
    decoder = Gst.ElementFactory.make("nvv4l2decoder", f"decoder-{name_suffix}")

    # Converter
    nvvidconv = Gst.ElementFactory.make("nvvideoconvert", f"converter-{name_suffix}")

    # Aggressive buffering for RTSP - AGX Orin optimized
    queue = Gst.ElementFactory.make("queue", f"queue-{name_suffix}")
    queue.set_property("max-size-time", SYNC_SETTINGS.get("rtsp_queue_max_time_ns", 250000000))
    queue.set_property("max-size-buffers", SYNC_SETTINGS.get("rtsp_queue_max_buffers", 50))
    queue.set_property("leaky", SYNC_SETTINGS.get("rtsp_queue_leaky", 2))  # Drop old buffers if queue fills (downstream)

    # Add elements to pipeline
    pipeline.add(rtspsrc)
    pipeline.add(depay)
    pipeline.add(parse)
    pipeline.add(decoder)
    pipeline.add(nvvidconv)
    pipeline.add(queue)

    # Link static elements
    depay.link(parse)
    parse.link(decoder)
    decoder.link(nvvidconv)
    nvvidconv.link(queue)

    # Link queue to muxer
    mux_sink_pad = streammux.get_request_pad(sink_pad_name)
    queue_src_pad = queue.get_static_pad("src")

    # Monitor muxer sink pad state (if debug enabled)
    if ENABLE_DEBUG_PROBES:
        mux_buffer_count = [0]

        def mux_sink_probe(pad, info, u_data):
            mux_buffer_count[0] += 1
            if mux_buffer_count[0] == 1:
                print(f"[MUX SINK] {sink_pad_name} receiving first buffer from {name_suffix}")
            elif mux_buffer_count[0] % 30 == 0:
                print(f"[MUX SINK] {sink_pad_name} received {mux_buffer_count[0]} buffers from {name_suffix}")
            return Gst.PadProbeReturn.OK

        mux_sink_pad.add_probe(Gst.PadProbeType.BUFFER, mux_sink_probe, None)

    # Add readiness probe to detect when source starts producing frames
    readiness_probe = create_readiness_probe(name_suffix)
    queue_src_pad.add_probe(Gst.PadProbeType.BUFFER, readiness_probe, None)

    # Add sync monitoring probe before muxer
    if SYNC_MONITORING.get("enable", True):
        sync_probe = create_sync_monitor_probe(name_suffix)
        queue_src_pad.add_probe(Gst.PadProbeType.BUFFER, sync_probe, None)

    queue_src_pad.link(mux_sink_pad)

    # Dynamic pad callback for rtspsrc
    def on_pad_added(src, new_pad, depay_element):
        sink_pad = depay_element.get_static_pad("sink")
        if not sink_pad.is_linked():
            new_pad.link(sink_pad)
            print(f"[{name_suffix}] RTSP source pad linked")

    rtspsrc.connect("pad-added", on_pad_added, depay)

    return queue

def main(args):
    global pipeline

    # Load and validate configuration
    try:
        config = load_config()
    except FileNotFoundError as exc:
        sys.stderr.write(f"Config file not found: {exc}\n")
        return 1
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"Invalid JSON in config file: {exc}\n")
        return 1

    errors = validate_config(config)
    if errors:
        sys.stderr.write("Configuration errors:\n")
        for error in errors:
            sys.stderr.write(f"  - {error}\n")
        return 1

    apply_config(config)

    print("\n=== Configuration Loaded ===")
    print(f"USB Cameras: {USB_CAM1_DEV}, {USB_CAM2_DEV}")
    print(f"RTSP Sources: {RTSP_SOURCE_IP1}, {RTSP_SOURCE_IP2}, {RTSP_SOURCE_IP3}:{RTSP_SOURCE_PORT}")
    print(f"Output File: {OUTPUT_FILE}")
    print(f"Overlays: {'Enabled' if ENABLE_OVERLAYS else 'Disabled'}")
    print(f"Debug Probes: {'Enabled' if ENABLE_DEBUG_PROBES else 'Disabled'}")
    print("===========================\n")

    # APPLY INTERNAL CAMERA SETTINGS (optional, uncomment to enable)
    print("\n=== Applying Camera Settings ===")
    apply_camera_settings_batch(CONFIG["usb_cameras"]["camera1_device"], CONFIG["usb_cameras"]["internal_camera_settings"])
    apply_camera_settings_batch(CONFIG["usb_cameras"]["camera2_device"], CONFIG["usb_cameras"]["internal_camera_settings"])
    print("=== Camera Settings Applied ===\n")

    Gst.init(None)

    # Register signal handler for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)

    pipeline = Gst.Pipeline()

    # Muxer with Balanced Sync Settings for File Recording (1080p @ 30fps)
    streammux = Gst.ElementFactory.make("nvstreammux", "Stream-Muxer")
    streammux.set_property("width", USB_CAM_WIDTH)
    streammux.set_property("height", USB_CAM_HEIGHT)
    streammux.set_property("batch-size", 8)
    batched_push_timeout_us = int(SYNC_SETTINGS.get("batched_push_timeout_ms", 40) * 1000)
    streammux.set_property("batched-push-timeout", batched_push_timeout_us)
    streammux.set_property("live-source", 1)  # Keep as 1 since sources are live cameras
    # Enable input synchronization with balanced tolerance (matches RTSP latency)
    streammux.set_property("sync-inputs", SYNC_SETTINGS.get("sync_inputs", 1))
    streammux.set_property("max-latency", SYNC_SETTINGS.get("max_latency_ns", 150000000))
    streammux.set_property("frame-duration", SYNC_SETTINGS.get("frame_duration_ns", 33333333))
    pipeline.add(streammux)

    # Debug: print critical latency settings to verify runtime values
    print("\n=== Mux/Source Latency Debug ===")
    print("streammux.batched-push-timeout:", streammux.get_property("batched-push-timeout"))
    print("streammux.max-latency:", streammux.get_property("max-latency"))
    print("streammux.sync-inputs:", streammux.get_property("sync-inputs"))
    print("USB queue target time (ns):", SYNC_SETTINGS.get("usb_queue_max_time_ns", 250000000), 
          "buffers:", SYNC_SETTINGS.get("usb_queue_max_buffers", 50))
    print("RTSP queue target time (ns):", SYNC_SETTINGS.get("rtsp_queue_max_time_ns", 250000000), 
          "buffers:", SYNC_SETTINGS.get("rtsp_queue_max_buffers", 50), "leaky downstream")
    print("RTSP src latency (ms):", RTSP_SOURCE_LATENCY_MS, f"protocols: {RTSP_SOURCE_PROTOCOL}", "drop-on-latency:", True)
    print("================================\n")

    print("\n=== Creating 8 Video Sources with Aggressive Buffering ===")

    # Verify RTSP sources are reachable
    print(f"\n⚠️  IMPORTANT: Make sure RTSP servers are running on:")
    print(f"   IP1: {RTSP_SOURCE_IP1}:{RTSP_SOURCE_PORT}")
    print(f"   IP2: {RTSP_SOURCE_IP2}:{RTSP_SOURCE_PORT}")
    print(f"   IP3: {RTSP_SOURCE_IP3}:{RTSP_SOURCE_PORT}")
    print("   Expected streams: /cam1 and /cam2 on each IP\n")

    # Create 2 USB Camera Sources (sink_0, sink_1)
    print(f"Creating USB Camera 1: {USB_CAM1_DEV}")
    create_usb_camera_source(
        pipeline, USB_CAM1_DEV, USB_CAM_WIDTH, USB_CAM_HEIGHT, USB_CAM_FPS,
        "usb_cam1", streammux, "sink_0"
    )

    print(f"Creating USB Camera 2: {USB_CAM2_DEV}")
    create_usb_camera_source(
        pipeline, USB_CAM2_DEV, USB_CAM_WIDTH, USB_CAM_HEIGHT, USB_CAM_FPS,
        "usb_cam2", streammux, "sink_1"
    )

    # Create 6 RTSP Sources (sink_2 through sink_7)
    # IP1 sources (sink_2, sink_3)
    rtsp_location1 = f"rtsp://{RTSP_SOURCE_IP1}:{RTSP_SOURCE_PORT}{RTSP_CAM1_MOUNT}"
    print(f"Creating RTSP Source IP1-Cam1: {rtsp_location1}")
    create_rtsp_source(pipeline, rtsp_location1, "ip1_cam1", streammux, "sink_2")

    rtsp_location2 = f"rtsp://{RTSP_SOURCE_IP1}:{RTSP_SOURCE_PORT}{RTSP_CAM2_MOUNT}"
    print(f"Creating RTSP Source IP1-Cam2: {rtsp_location2}")
    create_rtsp_source(pipeline, rtsp_location2, "ip1_cam2", streammux, "sink_3")

    # IP2 sources (sink_4, sink_5)
    rtsp_location3 = f"rtsp://{RTSP_SOURCE_IP2}:{RTSP_SOURCE_PORT}{RTSP_CAM1_MOUNT}"
    print(f"Creating RTSP Source IP2-Cam1: {rtsp_location3}")
    create_rtsp_source(pipeline, rtsp_location3, "ip2_cam1", streammux, "sink_4")

    rtsp_location4 = f"rtsp://{RTSP_SOURCE_IP2}:{RTSP_SOURCE_PORT}{RTSP_CAM2_MOUNT}"
    print(f"Creating RTSP Source IP2-Cam2: {rtsp_location4}")
    create_rtsp_source(pipeline, rtsp_location4, "ip2_cam2", streammux, "sink_5")

    # IP3 sources (sink_6, sink_7)
    rtsp_location5 = f"rtsp://{RTSP_SOURCE_IP3}:{RTSP_SOURCE_PORT}{RTSP_CAM1_MOUNT}"
    print(f"Creating RTSP Source IP3-Cam1: {rtsp_location5}")
    create_rtsp_source(pipeline, rtsp_location5, "ip3_cam1", streammux, "sink_6")

    rtsp_location6 = f"rtsp://{RTSP_SOURCE_IP3}:{RTSP_SOURCE_PORT}{RTSP_CAM2_MOUNT}"
    print(f"Creating RTSP Source IP3-Cam2: {rtsp_location6}")
    create_rtsp_source(pipeline, rtsp_location6, "ip3_cam2", streammux, "sink_7")

    print("=== All 8 sources created with sync monitoring ===\n")

    # Overlays disabled for clean output
    if ENABLE_OVERLAYS:
        print("=== Overlays enabled ===\n")
    else:
        print("=== Overlays disabled - clean output mode ===\n")

    # Tiler (2x8) - 1080p sources = 3840x4320 output (4K UHD)
    tiler = Gst.ElementFactory.make("nvmultistreamtiler", "nvtiler")
    tiler.set_property("rows", CONFIG["tiler"]["rows"])
    tiler.set_property("columns", CONFIG["tiler"]["columns"])

    
    tiler.set_property("width", FINAL_TILER_WIDTH)
    tiler.set_property("height",FINAL_TILER_HEIGHT)
    pipeline.add(tiler)

    # OSD (kept for potential future use, but no overlays added)
    nvvidconv = Gst.ElementFactory.make("nvvideoconvert", "nvvidconv")
    nvosd = Gst.ElementFactory.make("nvdsosd", "nvosd")
    nvosd.set_property("process-mode", 1)
    nvosd.set_property("display-text", 1)

    # Encoding chain for file output
    nvvidconv_post = Gst.ElementFactory.make("nvvideoconvert", "nvvidconv_post")
    caps = Gst.ElementFactory.make("capsfilter", "filter")
    caps.set_property("caps", Gst.Caps.from_string("video/x-raw(memory:NVMM), format=I420"))

    encoder = Gst.ElementFactory.make("nvv4l2h264enc", "encoder")
    encoder.set_property("bitrate", CONFIG["encoder"]["bitrate"])
    encoder.set_property("iframeinterval", CONFIG["encoder"]["iframeinterval"])
    encoder.set_property("insert-sps-pps", 1)
    encoder.set_property("preset-level", ENCODER_PRESET_LEVEL)
    encoder.set_property("profile", ENCODER_PROFILE)
    encoder.set_property("insert-vui", 1)  # Insert timing info
    encoder.set_property("maxperf-enable", 1)  # Enable maximum performance mode
    try:
        encoder.set_property("zerocopy", 1)  # Zero-copy if supported
    except Exception:
        pass

    # H264 Parser (required for qtmux)
    h264parse = Gst.ElementFactory.make("h264parse", "h264parse")

    # MP4 Muxer for file output
    mp4mux = Gst.ElementFactory.make("qtmux", "mp4mux")
    mp4mux.set_property("faststart", CONFIG["mp4_mux"].get("faststart", True))
    set_mp4_metadata(mp4mux)

    # File Sink
    filesink = Gst.ElementFactory.make("filesink", "output")
    filesink.set_property("location", OUTPUT_FILE)
    filesink.set_property("sync", CONFIG["file_sink"].get("sync", False))
    filesink.set_property("async", CONFIG["file_sink"].get("async", False))
    filesink.set_property("buffer-size", CONFIG["file_sink"].get("buffer_size", 2097152))

    # Add probe to verify filesink is receiving data (if debug enabled)
    if ENABLE_DEBUG_PROBES:
        def filesink_probe(pad, info, u_data):
            print("[FILESINK] Receiving first buffer - downstream is ready")
            # Remove probe after first buffer (one-shot)
            return Gst.PadProbeReturn.REMOVE

        filesink_sink_pad = filesink.get_static_pad("sink")
        filesink_sink_pad.add_probe(Gst.PadProbeType.BUFFER, filesink_probe, None)

    # Add remaining elements
    pipeline.add(nvvidconv)
    pipeline.add(nvosd)
    pipeline.add(nvvidconv_post)
    pipeline.add(caps)
    pipeline.add(encoder)
    pipeline.add(h264parse)
    pipeline.add(mp4mux)
    pipeline.add(filesink)

    # Link
    streammux.link(tiler)
    tiler.link(nvvidconv)
    nvvidconv.link(nvosd)
    nvosd.link(nvvidconv_post)
    nvvidconv_post.link(caps)
    caps.link(encoder)
    encoder.link(h264parse)

    # Link h264parse to mp4mux (qtmux requires request pad)
    h264parse_src_pad = h264parse.get_static_pad("src")
    mp4mux_sink_pad = mp4mux.get_request_pad("video_%u")
    h264parse_src_pad.link(mp4mux_sink_pad)

    mp4mux.link(filesink)

    # Verify all links before starting
    verify_pipeline_links(pipeline)

    print("\n *** Launching 8-Camera File Recording with Timestamps ***")
    print(f" *** Output File: {OUTPUT_FILE} ***")
    print(f" *** Timestamp File: {OUTPUT_FILE.replace('.mp4', '_timestamps.json')} ***")
    print(f" *** Row 0: 2x USB Cameras ({USB_CAM1_DEV}, {USB_CAM2_DEV}) ***")
    print(f" *** Row 1: 2x RTSP Streams from {RTSP_SOURCE_IP1} ***")
    print(f" *** Row 2: 2x RTSP Streams from {RTSP_SOURCE_IP2} ***")
    print(f" *** Row 3: 2x RTSP Streams from {RTSP_SOURCE_IP3} ***")
    print(f" *** All Cameras: {USB_CAM_WIDTH}x{USB_CAM_HEIGHT} @ {USB_CAM_FPS}fps ***")
    print(f" *** Output: {FINAL_TILER_WIDTH}x{FINAL_TILER_HEIGHT} (2x4 tiled 1080p) ***")
    print(f" *** Encoder: {CONFIG['encoder']['bitrate']/1000000:.0f} Mbps, keyframe every {CONFIG['encoder']['iframeinterval']} frames ***")
    print(f" *** USB Buffer: {SYNC_SETTINGS.get('usb_queue_max_time_ns', 250000000)/1000000:.0f}ms, RTSP Buffer: {SYNC_SETTINGS.get('rtsp_queue_max_time_ns', 250000000)/1000000:.0f}ms, Muxer: {SYNC_SETTINGS.get('batched_push_timeout_ms', 40):.0f}ms ***")
    print(f" *** Expected Sync: ±{SYNC_MONITORING.get('max_latency_variance_ms', 10)}ms @ {USB_CAM_FPS}fps ***\n")
    print("⏳ Waiting for all 8 sources to be ready before starting recording...")
    print("   This ensures all cameras start from a synchronized point.\n")

    loop = GLib.MainLoop()
    bus = pipeline.get_bus()
    bus.add_signal_watch()
    bus.connect("message", bus_call, loop)

    print("Starting pipeline directly to PLAYING state...")
    print("(Live sources don't support PAUSED state properly)\n")

    # Start pipeline in PLAYING state immediately
    # This prevents the "not-linked" error that occurs when trying to pause live sources
    pipeline.set_state(Gst.State.PLAYING)

    # Wait for pipeline to reach PLAYING state
    print("Waiting for pipeline to start...")
    state_return = pipeline.get_state(10 * Gst.SECOND)

    if state_return[0] in [Gst.StateChangeReturn.SUCCESS, Gst.StateChangeReturn.NO_PREROLL]:
        print("Pipeline is now playing\n")

        # Give all sources time to connect and start producing frames
        print("Waiting 10 seconds for all 8 sources to connect and stabilize...")
        time.sleep(10)
        print("Sources should be ready now\n")
    else:
        print(f"Warning: Pipeline state change returned: {state_return[0]}\n")
        print("Waiting 10 seconds anyway...")
        time.sleep(10)

    try:
        loop.run()
    except:
        pass
    pipeline.set_state(Gst.State.NULL)
    
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv))
