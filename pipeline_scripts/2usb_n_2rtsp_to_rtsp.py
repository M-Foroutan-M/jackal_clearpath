#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Dict, Tuple
import gi
import pyds
gi.require_version('Gst', '1.0')
gi.require_version('GstRtspServer', '1.0')
from gi.repository import Gst, GObject, GstRtspServer

CONFIG = {}
OVERLAY_CONFIG = {}
SYNC_SETTINGS = {}
SYNC_MONITORING = {}
ENCODER_PRESET_LEVEL = 1
ENCODER_PROFILE = 0

RTSP_PORT = None
RTSP_MOUNT_POINT_MAIN = None
ENABLE_OVERLAYS = None
USB_CAM1_DEV = None
USB_CAM2_DEV = None
USB_CAM_WIDTH = None
USB_CAM_HEIGHT = None
USB_CAM_FPS = None
RTSP_SOURCE_IP = None
RTSP_SOURCE_PORT = None
RTSP_CAM1_MOUNT = None
RTSP_CAM2_MOUNT = None
RTSP_SOURCE_LATENCY_MS = None
RTSP_SOURCE_PROTOCOL = None

def load_config():
    """Load configuration from SETTINGS.json, fallback to SETTINGS_DEFAULT.json"""
    script_dir = Path(__file__).parent
    settings_path = script_dir / "settings" / "2usb_n_2rtsp_to_rtsp_SETTINGS.json"
    default_path = script_dir / "settings" / "default" / "2usb_n_2rtsp_to_rtsp_SETTINGS_DEFAULT.json"
    
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

def _parse_color(color_name, default_rgba):
    if not color_name:
        return default_rgba
    if isinstance(color_name, str) and color_name.startswith("#") and len(color_name) in (7, 9):
        try:
            r = int(color_name[1:3], 16) / 255.0
            g = int(color_name[3:5], 16) / 255.0
            b = int(color_name[5:7], 16) / 255.0
            a = 1.0
            if len(color_name) == 9:
                a = int(color_name[7:9], 16) / 255.0
            return (r, g, b, a)
        except ValueError:
            return default_rgba
    name = str(color_name).strip().lower()
    named = {
        "white": (1.0, 1.0, 1.0, 1.0),
        "black": (0.0, 0.0, 0.0, 1.0),
        "yellow": (1.0, 1.0, 0.0, 1.0),
        "red": (1.0, 0.0, 0.0, 1.0),
        "green": (0.0, 1.0, 0.0, 1.0),
        "blue": (0.0, 0.0, 1.0, 1.0)
    }
    return named.get(name, default_rgba)

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
        rtsp_port = int(config["rtsp_output"].get("port"))
        if rtsp_port < 1024 or rtsp_port > 65535:
            errors.append(f"RTSP output port out of range: {rtsp_port}")
    except (TypeError, ValueError):
        errors.append(f"Invalid RTSP output port: {config['rtsp_output'].get('port')}")

    try:
        udp_port = int(config["rtsp_output"].get("udp_port"))
        if udp_port < 1024 or udp_port > 65535:
            errors.append(f"RTSP UDP port out of range: {udp_port}")
    except (TypeError, ValueError):
        errors.append(f"Invalid RTSP UDP port: {config['rtsp_output'].get('udp_port')}")

    try:
        source_port = int(config["rtsp_sources"].get("port"))
        if source_port < 1 or source_port > 65535:
            errors.append(f"RTSP source port out of range: {source_port}")
    except (TypeError, ValueError):
        errors.append(f"Invalid RTSP source port: {config['rtsp_sources'].get('port')}")

    protocol = str(config["rtsp_sources"].get("protocol", "")).lower()
    if protocol not in ("tcp", "udp"):
        errors.append(f"Invalid RTSP protocol: {config['rtsp_sources'].get('protocol')}")

    labels = config["overlays"].get("source_labels", [])
    if not isinstance(labels, list) or len(labels) != 4:
        errors.append("Must have exactly 4 source labels")

    if config["sync_settings"].get("queue_max_buffers", 0) < 1:
        errors.append("queue_max_buffers must be >= 1")

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

    if config["overlays"].get("font_size", 0) <= 0:
        errors.append("overlays.font_size must be > 0")

    if config["encoder"].get("bitrate", 0) <= 0:
        errors.append("encoder.bitrate must be > 0")

    if config["encoder"].get("iframeinterval", 0) <= 0:
        errors.append("encoder.iframeinterval must be > 0")

    if _resolve_encoder_preset(config["encoder"].get("preset")) is None:
        errors.append(f"Invalid encoder preset: {config['encoder'].get('preset')}")

    if _resolve_encoder_profile(config["encoder"].get("profile")) is None:
        errors.append(f"Invalid encoder profile: {config['encoder'].get('profile')}")

    return errors

def apply_config(config):
    global CONFIG
    global OVERLAY_CONFIG
    global SYNC_SETTINGS
    global SYNC_MONITORING
    global ENCODER_PRESET_LEVEL
    global ENCODER_PROFILE
    global RTSP_PORT
    global RTSP_MOUNT_POINT_MAIN
    global ENABLE_OVERLAYS
    global USB_CAM1_DEV
    global USB_CAM2_DEV
    global USB_CAM_WIDTH
    global USB_CAM_HEIGHT
    global USB_CAM_FPS
    global RTSP_SOURCE_IP
    global RTSP_SOURCE_PORT
    global RTSP_CAM1_MOUNT
    global RTSP_CAM2_MOUNT
    global RTSP_SOURCE_LATENCY_MS
    global RTSP_SOURCE_PROTOCOL

    CONFIG = config
    OVERLAY_CONFIG = config["overlays"]
    SYNC_SETTINGS = config["sync_settings"]
    SYNC_MONITORING = config["sync_monitoring"]

    RTSP_PORT = str(config["rtsp_output"]["port"])
    RTSP_MOUNT_POINT_MAIN = config["rtsp_output"]["mount_point"]
    ENABLE_OVERLAYS = bool(config["overlays"]["enable"])

    USB_CAM1_DEV = config["usb_cameras"]["camera1_device"]
    USB_CAM2_DEV = config["usb_cameras"]["camera2_device"]
    USB_CAM_WIDTH = config["usb_cameras"]["width"]
    USB_CAM_HEIGHT = config["usb_cameras"]["height"]
    USB_CAM_FPS = config["usb_cameras"]["fps"]

    RTSP_SOURCE_IP = config["rtsp_sources"]["ip"]
    RTSP_SOURCE_PORT = str(config["rtsp_sources"]["port"])
    RTSP_CAM1_MOUNT = config["rtsp_sources"]["camera1_mount_point"]
    RTSP_CAM2_MOUNT = config["rtsp_sources"]["camera2_mount_point"]
    RTSP_SOURCE_LATENCY_MS = config["rtsp_sources"]["latency_ms"]
    RTSP_SOURCE_PROTOCOL = str(config["rtsp_sources"]["protocol"]).lower()

    preset_level = _resolve_encoder_preset(config["encoder"]["preset"])
    profile_level = _resolve_encoder_profile(config["encoder"]["profile"])
    ENCODER_PRESET_LEVEL = preset_level if preset_level is not None else 1
    ENCODER_PROFILE = profile_level if profile_level is not None else 0

def apply_camera_settings_batch(camera_device: str, settings_dict: Dict[str, int]):
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
                
                # Analyze frame alignment if we have all 4 sources
                if len(pts_deltas) == 4:
                    delta_values = list(pts_deltas.values())
                    avg_delta = sum(delta_values) / len(delta_values)
                    max_delta = max(delta_values)
                    min_delta = min(delta_values)
                    delta_variance = max_delta - min_delta
                    
                    
                    # Warn if frame intervals vary significantly (indicates sync issues)
                    if delta_variance > variance_threshold:
                        print(f"[SYNC WARNING] High frame interval variance: {delta_variance:.2f}ms")
                        for src, delta in pts_deltas.items():
                            offset = delta - avg_delta
                            print(f"  {src}: {delta:.2f}ms (offset: {offset:+.2f}ms)")
                    
                    # Also warn if average is far from expected
                    expected_delta = 1000.0 / float(expected_fps)
                    delta_error = abs(avg_delta - expected_delta)
                    if delta_error > variance_threshold:
                        print(f"[SYNC WARNING] Frame rate deviation: {delta_error:.2f}ms from expected {expected_delta:.2f}ms")
                  
                    print(f"[SYNC] Avg frame interval: {avg_delta:.2f}ms (expected: ~{expected_delta:.2f}ms for {expected_fps}fps)")
                    print(f"[SYNC] Frame interval variance: {delta_variance:.2f}ms")
        
        return Gst.PadProbeReturn.OK
    return sync_probe

def bus_call(bus, message, loop):
    t = message.type
    if t == Gst.MessageType.EOS:
        sys.stdout.write("End of stream\n")
        loop.quit()
    elif t == Gst.MessageType.ERROR:
        err, debug = message.parse_error()
        sys.stderr.write("Error: %s: %s\n" % (err, debug))
        loop.quit()
    return True

def tiler_src_pad_buffer_probe(pad, info, u_data):
    """Buffer probe to add per-source PTS timestamps (only if ENABLE_OVERLAYS is True)"""
    if not ENABLE_OVERLAYS:
        return Gst.PadProbeReturn.OK
    
    gst_buffer = info.get_buffer()
    if not gst_buffer:
        return Gst.PadProbeReturn.OK

    try:
        # Get batch metadata
        batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(gst_buffer))
        if not batch_meta:
            return Gst.PadProbeReturn.OK
        
        # Check if frame_meta_list exists and has frames
        l_frame = batch_meta.frame_meta_list
        if not l_frame:
            return Gst.PadProbeReturn.OK
        
        # Iterate through all frames in the batch to add per-source timestamps
        l_frame = batch_meta.frame_meta_list
        while l_frame is not None:
            try:
                frame_meta = pyds.NvDsFrameMeta.cast(l_frame.data)
                if not frame_meta:
                    l_frame = l_frame.next
                    continue
                
                # Get source index (0-3)
                source_id = frame_meta.source_id
                # Position in top-right corner of each individual frame (before tiling)
                # Each frame is still at its original resolution (1920x1080)
                x_offset = OVERLAY_CONFIG.get("label_x_offset", 1520) # px from the left edge
                y_offset = OVERLAY_CONFIG.get("label_y_offset", 20) # px from top edge
                
                # PTS timestamp (convert from nanoseconds to seconds)
                pts_sec = frame_meta.buf_pts / 1000000000.0
                
                # Source name labels
                source_names = OVERLAY_CONFIG.get("source_labels", [])
                source_name = source_names[source_id] if source_id < len(source_names) else f"SRC{source_id}"
                
                # Display text with only PTS
                timestamp_text = f"{source_name}\nPTS: {pts_sec:.3f}s"
                
                # Create display meta for timestamp
                display_meta = pyds.nvds_acquire_display_meta_from_pool(batch_meta)
                display_meta.num_labels = 1
                txt_params = display_meta.text_params[0]
                txt_params.display_text = timestamp_text
                txt_params.x_offset = x_offset
                txt_params.y_offset = y_offset
                txt_params.font_params.font_name = OVERLAY_CONFIG.get("font", "Arial")
                txt_params.font_params.font_size = OVERLAY_CONFIG.get("font_size", 28)
                font_color = _parse_color(OVERLAY_CONFIG.get("font_color"), (1.0, 1.0, 0.0, 1.0))
                txt_params.font_params.font_color.set(*font_color)
                txt_params.set_bg_clr = 1
                bg_color = _parse_color(OVERLAY_CONFIG.get("background_color"), (0.0, 0.0, 0.0, 1.0))
                txt_params.text_bg_clr.set(bg_color[0], bg_color[1], bg_color[2], 0.8)
                pyds.nvds_add_display_meta_to_frame(frame_meta, display_meta)
                
            except Exception as e:
                print(f"Error processing frame {source_id}: {e}")
            
            # Move to next frame
            try:
                l_frame = l_frame.next
            except StopIteration:
                break
        
    except Exception as e:
        print(f"Error in probe: {e}")
        pass
    
    return Gst.PadProbeReturn.OK

def create_usb_camera_source(pipeline, dev_node, width, height, fps, name_suffix, streammux, sink_pad_name):
    """Create USB camera source pipeline with queue and sync monitoring"""
    source = Gst.ElementFactory.make("v4l2src", f"source-{name_suffix}")
    source.set_property('device', dev_node)
    source.set_property('do-timestamp', True)  # Force GStreamer timestamping
    
    caps_v4l2 = Gst.ElementFactory.make("capsfilter", f"v4l2-caps-{name_suffix}")
    caps_str = f"image/jpeg, width={width}, height={height}, framerate={fps}/1"
    caps_v4l2.set_property("caps", Gst.Caps.from_string(caps_str))

    decoder = Gst.ElementFactory.make("nvv4l2decoder", f"decoder-{name_suffix}")
    decoder.set_property("mjpeg", 1)

    nvvidconv = Gst.ElementFactory.make("nvvideoconvert", f"converter-{name_suffix}")
    
    # Add queue for buffering and sync
    queue = Gst.ElementFactory.make("queue", f"queue-{name_suffix}")
    queue.set_property("max-size-time", SYNC_SETTINGS.get("queue_max_time_ns", 50000000))
    queue.set_property("max-size-buffers", SYNC_SETTINGS.get("queue_max_buffers", 3))
    queue.set_property("leaky", SYNC_SETTINGS.get("leaky", 2))  # Drop old buffers if full

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
    
    # Add sync monitoring probe before muxer
    if SYNC_MONITORING.get("enable", True):
        sync_probe = create_sync_monitor_probe(name_suffix)
        queue_src_pad.add_probe(Gst.PadProbeType.BUFFER, sync_probe, None)
    
    queue_src_pad.link(mux_sink_pad)

    return queue

def create_rtsp_source(pipeline, rtsp_location, name_suffix, streammux, sink_pad_name):
    """Create RTSP source pipeline with dynamic pad linking, sync features, and queue"""
    
    # RTSP Source
    rtspsrc = Gst.ElementFactory.make("rtspsrc", f"rtspsrc-{name_suffix}")
    rtspsrc.set_property("location", rtsp_location)
    rtspsrc.set_property("latency", RTSP_SOURCE_LATENCY_MS)
    rtspsrc.set_property("protocols", RTSP_SOURCE_PROTOCOL)
    rtspsrc.set_property("drop-on-latency", True)  # Drop late frames instead of accumulating
    # Note: Removed buffer-mode, ntp-sync, and ntp-time-source properties
    # as they can cause connection issues with some RTSP servers
    
    # Depayloader
    depay = Gst.ElementFactory.make("rtph264depay", f"depay-{name_suffix}")
    
    # H264 Parser
    parse = Gst.ElementFactory.make("h264parse", f"parse-{name_suffix}")
    
    # Hardware Decoder
    decoder = Gst.ElementFactory.make("nvv4l2decoder", f"decoder-{name_suffix}")
    
    # Converter
    nvvidconv = Gst.ElementFactory.make("nvvideoconvert", f"converter-{name_suffix}")
    
    # Add queue for buffering and sync
    queue = Gst.ElementFactory.make("queue", f"queue-{name_suffix}")
    queue.set_property("max-size-time", SYNC_SETTINGS.get("queue_max_time_ns", 50000000))
    queue.set_property("max-size-buffers", SYNC_SETTINGS.get("queue_max_buffers", 3))
    queue.set_property("leaky",  SYNC_SETTINGS.get("leaky", 2))  # Drop old buffers if full
    
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

def main():
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
    print(f"RTSP Sources: {RTSP_SOURCE_IP}:{RTSP_SOURCE_PORT}")
    print(f"Output: rtsp://localhost:{RTSP_PORT}{RTSP_MOUNT_POINT_MAIN}")
    print(f"Overlays: {'Enabled' if ENABLE_OVERLAYS else 'Disabled'}")
    print("===========================\n")

    # APPLY INTERNAL CAMERA SETTINGS
    print("\n=== Applying Camera Settings ===")
    apply_camera_settings_batch(CONFIG["usb_cameras"]["camera1_device"], CONFIG["usb_cameras"]["internal_camera_settings"])
    apply_camera_settings_batch(CONFIG["usb_cameras"]["camera2_device"], CONFIG["usb_cameras"]["internal_camera_settings"])
    print("=== Camera Settings Applied ===\n")


    Gst.init(None)

    pipeline = Gst.Pipeline()
    
    # Muxer with Sync Enabled
    streammux = Gst.ElementFactory.make("nvstreammux", "Stream-Muxer")
    streammux.set_property('width', USB_CAM_WIDTH)
    streammux.set_property('height', USB_CAM_HEIGHT)
    streammux.set_property('batch-size', len(OVERLAY_CONFIG.get("source_labels", [])) or 4)
    batched_push_timeout_us = int(SYNC_SETTINGS.get("batched_push_timeout_ms", 40000) * 1000) # 4 seconds for better sync
    streammux.set_property('batched-push-timeout', batched_push_timeout_us)
    streammux.set_property('live-source', 1)
    # Enable input synchronization
    streammux.set_property('sync-inputs', SYNC_SETTINGS.get("sync_inputs", 1))
    streammux.set_property('max-latency', SYNC_SETTINGS.get("max_latency_ns", 50000000))
    streammux.set_property('frame-duration', SYNC_SETTINGS.get("frame_duration_ns", 33333333))
    pipeline.add(streammux)

    print("\n=== Creating Video Sources with Sync Features ===")
    
    # Create 2 USB Camera Sources (Top Row: sink_0, sink_1)
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

    # Create 2 RTSP Sources (Bottom Row: sink_2, sink_3)
    rtsp_location1 = f"rtsp://{RTSP_SOURCE_IP}:{RTSP_SOURCE_PORT}{RTSP_CAM1_MOUNT}"
    print(f"Creating RTSP Source 1: {rtsp_location1}")
    create_rtsp_source(pipeline, rtsp_location1, "rtsp_cam1", streammux, "sink_2")

    rtsp_location2 = f"rtsp://{RTSP_SOURCE_IP}:{RTSP_SOURCE_PORT}{RTSP_CAM2_MOUNT}"
    print(f"Creating RTSP Source 2: {rtsp_location2}")
    create_rtsp_source(pipeline, rtsp_location2, "rtsp_cam2", streammux, "sink_3")
    
    print("=== All sources created with sync monitoring ===\n")

    # Add Probe to Muxer (before tiling) for per-source timestamps (only if enabled)
    if ENABLE_OVERLAYS:
        mux_src_pad = streammux.get_static_pad("src")
        if not mux_src_pad:
            sys.stderr.write(" Unable to get src pad of muxer \n")
        else:
            mux_src_pad.add_probe(Gst.PadProbeType.BUFFER, tiler_src_pad_buffer_probe, None)
            print("=== Added per-source timestamp probe to muxer ===\n")
    else:
        print("=== Overlays disabled - skipping timestamp probe ===\n")

    # Tiler (2x2)
    tiler = Gst.ElementFactory.make("nvmultistreamtiler", "nvtiler")
    tiler.set_property("rows", config["tiler"]["rows"])
    tiler.set_property("columns", config["tiler"]["columns"])
    tiler.set_property("width", config["tiler"]["width"])
    tiler.set_property("height", config["tiler"]["height"])
    pipeline.add(tiler)

    # OSD
    nvvidconv = Gst.ElementFactory.make("nvvideoconvert", "nvvidconv")
    nvosd = Gst.ElementFactory.make("nvdsosd", "nvosd")
    nvosd.set_property("process-mode", 1)
    nvosd.set_property("display-text", 1)

    # Encoding chain for RTSP
    nvvidconv_post = Gst.ElementFactory.make("nvvideoconvert", "nvvidconv_post")
    caps = Gst.ElementFactory.make("capsfilter", "filter")
    caps.set_property("caps", Gst.Caps.from_string("video/x-raw(memory:NVMM), format=I420"))
    
    encoder = Gst.ElementFactory.make("nvv4l2h264enc", "encoder")
    encoder.set_property("bitrate", config["encoder"]["bitrate"])
    encoder.set_property("iframeinterval", config["encoder"]["iframeinterval"])
    encoder.set_property("insert-sps-pps", 1)
    encoder.set_property("preset-level", ENCODER_PRESET_LEVEL)
    encoder.set_property("profile", ENCODER_PROFILE)
    encoder.set_property("insert-vui", 1)  # Insert timing info
    
    rtppay = Gst.ElementFactory.make("rtph264pay", "rtppay")
    rtppay.set_property("config-interval", 1)
    rtppay.set_property("pt", 96)
    rtppay.set_property("mtu", config["rtp_payload"].get("mtu", 1400))
    
    udp_sink = Gst.ElementFactory.make("udpsink", "udp_sink")
    udp_sink.set_property("host", "127.0.0.1")
    udp_sink.set_property("port", config["rtsp_output"]["udp_port"])
    udp_sink.set_property("async", False)
    udp_sink.set_property("sync", 0)

    # Add remaining elements
    pipeline.add(nvvidconv)
    pipeline.add(nvosd)
    pipeline.add(nvvidconv_post)
    pipeline.add(caps)
    pipeline.add(encoder)
    pipeline.add(rtppay)
    pipeline.add(udp_sink)

    # Link
    streammux.link(tiler)
    tiler.link(nvvidconv)
    nvvidconv.link(nvosd)
    nvosd.link(nvvidconv_post)
    nvvidconv_post.link(caps)
    caps.link(encoder)
    encoder.link(rtppay)
    rtppay.link(udp_sink)

    # RTSP Server
    server = GstRtspServer.RTSPServer.new()
    server.set_property("service", RTSP_PORT)
    server.attach(None)
    
    factory = GstRtspServer.RTSPMediaFactory.new()
    udp_port = config["rtsp_output"]["udp_port"]
    factory.set_launch( f"( udpsrc name=pay0 port={udp_port} buffer-size=1048576 caps=\"application/x-rtp, media=video, clock-rate=90000, encoding-name=(string)H264, payload=96 \" )")
    factory.set_shared(True)
    server.get_mount_points().add_factory(RTSP_MOUNT_POINT_MAIN, factory)
    
    print(f"\n *** Launched Hybrid QUAD Camera RTSP Streaming ***")
    print(f" *** Output: rtsp://localhost:{RTSP_PORT}{RTSP_MOUNT_POINT_MAIN} ***")
    print(f" *** Top Row: 2x USB Cameras ({USB_CAM1_DEV}, {USB_CAM2_DEV}) ***")
    print(f" *** Bottom Row: 2x RTSP Streams from {RTSP_SOURCE_IP} ***")
    print(f" *** Resolution: {config['tiler']['width']*2}x{config['tiler']['height']*2} @ {USB_CAM_FPS}fps ***\n")

    loop = GObject.MainLoop()
    bus = pipeline.get_bus()
    bus.add_signal_watch()
    bus.connect ("message", bus_call, loop)

    pipeline.set_state(Gst.State.PLAYING)
    try:
        loop.run()
    except:
        pass
    pipeline.set_state(Gst.State.NULL)

if __name__ == '__main__':
    sys.exit(main())

