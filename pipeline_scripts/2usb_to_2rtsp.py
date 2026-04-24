#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys
import subprocess
import time
from typing import Dict, Tuple
import gi
import pyds
gi.require_version('Gst', '1.0')
gi.require_version('GstRtspServer', '1.0')
from gi.repository import Gst, GObject, GstRtspServer

# CONFIGURATION - Global variables populated by apply_config()
CONFIG = {}
RTSP_PORT = None
CAM1_MOUNT = None
CAM2_MOUNT = None
UDP_PORT_CAM1 = None
UDP_PORT_CAM2 = None
ENABLE_OVERLAYS = None
BYPASS_OSD = None
CAM_WIDTH = None
CAM_HEIGHT = None
CAM_FPS = None
CAM1_DEV = None
CAM2_DEV = None

def load_config():
    """Load configuration from SETTINGS.json, fallback to SETTINGS_DEFAULT.json"""
    script_dir = Path(__file__).parent
    settings_path = script_dir / "settings" / "2usb_to_2rtsp_SETTINGS.json"
    default_path = script_dir / "settings" / "default" / "2usb_to_2rtsp_SETTINGS_DEFAULT.json"
    
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

def validate_config(config):
    """Validate configuration values and check for common errors."""
    errors = []
    
    # Validate USB camera devices
    for cam_key in ("camera1_device", "camera2_device"):
        device = config["usb_cameras"].get(cam_key)
        if not device or not os.path.exists(device):
            errors.append(f"USB device not found: {device}")
    
    # Validate resolution
    width = config["usb_cameras"].get("width", 0)
    height = config["usb_cameras"].get("height", 0)
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        errors.append(f"Invalid resolution: {width}x{height}")
    
    # Validate FPS
    fps = config["usb_cameras"].get("fps", 0)
    if not isinstance(fps, int) or fps <= 0 or fps > 120:
        errors.append(f"Invalid FPS: {fps}")
    
    # Validate RTSP output port
    try:
        rtsp_port = int(config["rtsp_output"].get("port"))
        if rtsp_port < 1024 or rtsp_port > 65535:
            errors.append(f"RTSP output port out of range: {rtsp_port}")
    except (TypeError, ValueError):
        errors.append(f"Invalid RTSP output port: {config['rtsp_output'].get('port')}")
    
    # Validate UDP ports
    try:
        udp_port_cam1 = int(config["rtsp_output"].get("camera1_udp_port"))
        if udp_port_cam1 < 1024 or udp_port_cam1 > 65535:
            errors.append(f"Camera 1 UDP port out of range: {udp_port_cam1}")
    except (TypeError, ValueError):
        errors.append(f"Invalid Camera 1 UDP port: {config['rtsp_output'].get('camera1_udp_port')}")
    
    try:
        udp_port_cam2 = int(config["rtsp_output"].get("camera2_udp_port"))
        if udp_port_cam2 < 1024 or udp_port_cam2 > 65535:
            errors.append(f"Camera 2 UDP port out of range: {udp_port_cam2}")
    except (TypeError, ValueError):
        errors.append(f"Invalid Camera 2 UDP port: {config['rtsp_output'].get('camera2_udp_port')}")
    
    # Validate encoder settings
    if config["encoder"].get("bitrate", 0) <= 0:
        errors.append("encoder.bitrate must be > 0")
    
    if config["encoder"].get("iframeinterval", 0) <= 0:
        errors.append("encoder.iframeinterval must be > 0")
    
    # Validate queue settings
    if config["queue_pre_encoder"].get("max_size_buffers", 0) < 1:
        errors.append("queue_pre_encoder.max_size_buffers must be >= 1")
    
    if config["queue_post_encoder"].get("max_size_buffers", 0) < 1:
        errors.append("queue_post_encoder.max_size_buffers must be >= 1")
    
    # Validate overlay settings
    if config["overlay_display"].get("font_size", 0) <= 0:
        errors.append("overlay_display.font_size must be > 0")
    
    return errors

def apply_config(config):
    """Apply configuration to global variables"""
    global CONFIG
    global RTSP_PORT
    global CAM1_MOUNT
    global CAM2_MOUNT
    global UDP_PORT_CAM1
    global UDP_PORT_CAM2
    global ENABLE_OVERLAYS
    global BYPASS_OSD
    global CAM_WIDTH
    global CAM_HEIGHT
    global CAM_FPS
    global CAM1_DEV
    global CAM2_DEV
    global source_info_cam1
    global source_info_cam2
    
    CONFIG = config
    
    # RTSP output settings
    RTSP_PORT = str(config["rtsp_output"]["port"])
    CAM1_MOUNT = config["rtsp_output"]["camera1_mount_point"]
    CAM2_MOUNT = config["rtsp_output"]["camera2_mount_point"]
    UDP_PORT_CAM1 = config["rtsp_output"]["camera1_udp_port"]
    UDP_PORT_CAM2 = config["rtsp_output"]["camera2_udp_port"]
    
    # Overlay settings
    ENABLE_OVERLAYS = bool(config["overlay_settings"]["enable_overlays"])
    BYPASS_OSD = bool(config["overlay_settings"]["bypass_osd"])
    
    # USB camera settings
    CAM1_DEV = config["usb_cameras"]["camera1_device"]
    CAM2_DEV = config["usb_cameras"]["camera2_device"]
    CAM_WIDTH = config["usb_cameras"]["width"]
    CAM_HEIGHT = config["usb_cameras"]["height"]
    CAM_FPS = config["usb_cameras"]["fps"]
    
    # Initialize source info objects with labels from config
    source_info_cam1 = SourceInfo(config["source_labels"]["camera1"])
    source_info_cam2 = SourceInfo(config["source_labels"]["camera2"])

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

# Source name tracking (for overlay labels)
class SourceInfo:
    def __init__(self, name):
        self.name = name

# Will be initialized by apply_config()
source_info_cam1 = None
source_info_cam2 = None

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

def create_buffer_probe(source_info):
    """Factory function to create a buffer probe for PTS timestamp overlay"""
    probe_call_count = [0]  # Mutable counter to track probe calls
    
    def buffer_probe(pad, info, u_data):
        if not ENABLE_OVERLAYS:
            return Gst.PadProbeReturn.OK
            
        gst_buffer = info.get_buffer()
        if not gst_buffer:
            return Gst.PadProbeReturn.OK

        try:
            # Get batch metadata
            batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(gst_buffer))
            if not batch_meta:
                if probe_call_count[0] == 0:
                    print(f"[{source_info.name}] Warning: No batch metadata available")
                probe_call_count[0] += 1
                return Gst.PadProbeReturn.OK
            
            # Check if frame_meta_list exists and has frames
            l_frame = batch_meta.frame_meta_list
            if not l_frame:
                if probe_call_count[0] == 0:
                    print(f"[{source_info.name}] Warning: No frame_meta_list")
                probe_call_count[0] += 1
                return Gst.PadProbeReturn.OK
                
            # Get the first frame in the batch
            frame_meta = pyds.NvDsFrameMeta.cast(l_frame.data)
            if not frame_meta:
                if probe_call_count[0] == 0:
                    print(f"[{source_info.name}] Warning: Could not cast frame_meta")
                probe_call_count[0] += 1
                return Gst.PadProbeReturn.OK
            
            # PTS timestamp (convert from nanoseconds to seconds)
            pts_sec = frame_meta.buf_pts / 1000000000.0
            
            # Add Display Meta (Overlay) - PTS timestamp only
            display_meta = pyds.nvds_acquire_display_meta_from_pool(batch_meta)
            display_meta.num_labels = 1
            txt_params = display_meta.text_params[0]
            txt_params.display_text = f"{source_info.name}\nPTS: {pts_sec:.3f}s"
            
            # Position (top-right corner)
            txt_params.x_offset = CONFIG['overlay_display']['x_offset']
            txt_params.y_offset = CONFIG['overlay_display']['y_offset']
            
            # Font
            txt_params.font_params.font_name = CONFIG['overlay_display']['font_name']
            txt_params.font_params.font_size = CONFIG['overlay_display']['font_size']
            font_color = CONFIG['overlay_display']['font_color_rgba']
            txt_params.font_params.font_color.set(font_color[0], font_color[1], font_color[2], font_color[3])
            
            # Background
            txt_params.set_bg_clr = 1
            bg_color = CONFIG['overlay_display']['background_rgba']
            txt_params.text_bg_clr.set(bg_color[0], bg_color[1], bg_color[2], bg_color[3])
            
            # Add display meta to frame
            pyds.nvds_add_display_meta_to_frame(frame_meta, display_meta)
            
            # Debug output once every 60 frames
            probe_call_count[0] += 1
            if probe_call_count[0] % 60 == 1:
                print(f"[{source_info.name}] PTS overlay added: {pts_sec:.3f}s")
            
        except Exception as e:
            print(f"Error in probe ({source_info.name}): {e}")
            import traceback
            traceback.print_exc()
        
        return Gst.PadProbeReturn.OK
    
    return buffer_probe

def create_fps_probe(label):
    """
    Pad probe to log per-stream frame interval/FPS every ~60 frames (averaged).
    Uses buffer PTS; averages deltas across the last 60 samples to avoid spikes.
    """
    last_pts = [None]
    count = [0]
    sum_delta = [0.0]
    def fps_probe(pad, info, u_data):
        gst_buffer = info.get_buffer()
        if not gst_buffer or gst_buffer.pts == Gst.CLOCK_TIME_NONE:
            return Gst.PadProbeReturn.OK
        pts = gst_buffer.pts
        if last_pts[0] is not None:
            delta_ms = (pts - last_pts[0]) / 1_000_000.0
            # Ignore obviously bogus negative or huge gaps (>2s) to avoid misleading output
            if 0 < delta_ms < 2000:
                sum_delta[0] += delta_ms
                count[0] += 1
                if count[0] >= 60:
                    avg_delta = sum_delta[0] / count[0]
                    fps = 1000.0 / avg_delta if avg_delta > 0 else 0
                    print(f"[FPS] {label}: avg_delta={avg_delta:.2f}ms (~{fps:.1f} fps, {count[0]} samples)")
                    sum_delta[0] = 0.0
                    count[0] = 0
        last_pts[0] = pts
        return Gst.PadProbeReturn.OK
    return fps_probe

def create_camera_pipeline(pipeline, dev_node, width, height, fps, udp_port, source_info, name_suffix):
    """
    Simplified low-latency pipeline for maximum throughput:
    v4l2src -> caps -> decoder -> (queue) -> (nvosd) -> encoder -> rtppay -> udpsink
    
    Simplifications:
    - Removed redundant nvvideoconvert elements (decoder/OSD/encoder handle format conversion)
    - Removed redundant caps filter before encoder (encoder accepts NVMM directly)
    - Minimal queues only where needed to prevent backpressure
    - OSD bypass option available for maximum performance
    """
    # 1. Source (v4l2src) - optimized for USB camera throughput
    source = Gst.ElementFactory.make("v4l2src", f"source-{name_suffix}")
    source.set_property('device', dev_node)
    source.set_property('do-timestamp', CONFIG['v4l2_source']['do_timestamp'])
    try:
        source.set_property('io-mode', CONFIG['v4l2_source']['io_mode'])  # DMABUF if supported (faster than MMAP)
    except Exception:
        pass
    try:
        # Increase USB buffer size for better throughput (default is often too small)
        source.set_property('num-buffers', CONFIG['v4l2_source']['num_buffers'])  # Unlimited buffers
    except Exception:
        pass
    try:
        # Set pixel-aspect-ratio to avoid extra processing
        pixel_aspect_ratio = CONFIG['v4l2_source']['pixel_aspect_ratio'].split('/')
        source.set_property('pixel-aspect-ratio', Gst.Fraction(int(pixel_aspect_ratio[0]), int(pixel_aspect_ratio[1])))
    except Exception:
        pass
    
    # 2. CapsFilter (Force MJPEG at configured fps)
    # Note: If source FPS is lower than expected, check:
    #   - USB topology: run 'lsusb -t' to verify cameras are on separate USB controllers
    #   - USB version: ensure cameras are on USB 3.0 ports (blue connectors) for 1080p
    #   - Camera settings: verify with 'v4l2-ctl --device {dev_node} --get-fmt-video --get-parm'
    caps_v4l2 = Gst.ElementFactory.make("capsfilter", f"v4l2-caps-{name_suffix}")
    caps_str = f"image/jpeg, width={width}, height={height}, framerate={fps}/1"
    caps_v4l2.set_property("caps", Gst.Caps.from_string(caps_str))

    # 3. Decoder (Hardware MJPEG Decoder) - outputs NVMM format
    decoder = Gst.ElementFactory.make("nvv4l2decoder", f"decoder-{name_suffix}")
    decoder.set_property("mjpeg", CONFIG['decoder']['mjpeg'])
    try:
        decoder.set_property("low-latency-mode", CONFIG['decoder']['low_latency_mode'])
    except Exception:
        pass

    # 4. Converter to ensure format compatibility (decoder -> encoder)
    # Decoder outputs NV12/NVMM, encoder needs I420/NVMM - converter handles this
    nvvidconv = Gst.ElementFactory.make("nvvideoconvert", f"converter-{name_suffix}")

    # 5. Caps filter to explicitly set I420 format for encoder
    caps_encoder = Gst.ElementFactory.make("capsfilter", f"encoder-caps-{name_suffix}")
    caps_encoder.set_property("caps", Gst.Caps.from_string("video/x-raw(memory:NVMM), format=I420"))

    # 6. Small queue before encoder to smooth timing and prevent backpressure
    # Even with OSD bypassed, this helps absorb small timing variations
    queue_pre_encoder = Gst.ElementFactory.make("queue", f"queue-pre-encoder-{name_suffix}")
    queue_pre_encoder.set_property("max-size-buffers", CONFIG['queue_pre_encoder']['max_size_buffers'])
    queue_pre_encoder.set_property("max-size-time", CONFIG['queue_pre_encoder']['max_size_time_ns'])
    queue_pre_encoder.set_property("leaky", CONFIG['queue_pre_encoder']['leaky'])

    # 7. OSD (GPU mode) - optional, currently bypassed by default
    # Note: nvdsosd requires NvDs metadata (from nvstreammux) to function properly.
    nvosd = None
    if not BYPASS_OSD:
        nvosd = Gst.ElementFactory.make("nvdsosd", f"nvosd-{name_suffix}")
        nvosd.set_property("process-mode", CONFIG['nvosd']['process_mode'])  # GPU mode
        nvosd.set_property("display-text", CONFIG['nvosd']['display_text'])
        try:
            nvosd.set_property("gpu-id", CONFIG['nvosd']['gpu_id'])
        except Exception:
            pass

    # 8. Encoder (H.264) - requires I420/NVMM format
    # Adjusted for 1080p30: higher bitrate, keyframe every 1 second (30 frames)
    encoder = Gst.ElementFactory.make("nvv4l2h264enc", f"encoder-{name_suffix}")
    encoder.set_property("bitrate", CONFIG['encoder']['bitrate'])
    encoder.set_property("iframeinterval", CONFIG['encoder']['iframeinterval'])
    encoder.set_property("insert-sps-pps", CONFIG['encoder']['insert_sps_pps'])
    encoder.set_property("preset-level", CONFIG['encoder']['preset_level'])
    encoder.set_property("profile", CONFIG['encoder']['profile'])
    encoder.set_property("insert-vui", CONFIG['encoder']['insert_vui'])
    encoder.set_property("maxperf-enable", CONFIG['encoder']['maxperf_enable'])
    encoder.set_property("control-rate", CONFIG['encoder']['control_rate'])
    try:
        encoder.set_property("zerocopy", CONFIG['encoder']['zerocopy'])
    except Exception:
        pass
    try:
        encoder.set_property("async-mode", CONFIG['encoder']['async_mode'])
    except Exception:
        pass
    
    # 9. Minimal queue before payloader
    queue2 = Gst.ElementFactory.make("queue", f"queue2-{name_suffix}")
    queue2.set_property("max-size-buffers", CONFIG['queue_post_encoder']['max_size_buffers'])
    queue2.set_property("max-size-time", CONFIG['queue_post_encoder']['max_size_time_ns'])
    queue2.set_property("leaky", CONFIG['queue_post_encoder']['leaky'])

    # 10. RTP Payloader
    rtppay = Gst.ElementFactory.make("rtph264pay", f"rtppay-{name_suffix}")
    rtppay.set_property("config-interval", CONFIG['rtp_payload']['config_interval'])
    rtppay.set_property("pt", CONFIG['rtp_payload']['payload_type'])
    
    # 11. UDP Sink
    udp_sink = Gst.ElementFactory.make("udpsink", f"udp_sink-{name_suffix}")
    udp_sink.set_property("host", CONFIG['udp_sink']['host'])
    udp_sink.set_property("port", udp_port)
    udp_sink.set_property("async", CONFIG['udp_sink']['async'])
    udp_sink.set_property("sync", CONFIG['udp_sink']['sync'])

    # Add elements to pipeline
    pipeline.add(source)
    pipeline.add(caps_v4l2)
    pipeline.add(decoder)
    pipeline.add(nvvidconv)
    pipeline.add(caps_encoder)
    pipeline.add(queue_pre_encoder)
    if nvosd:
        pipeline.add(nvosd)
    pipeline.add(encoder)
    pipeline.add(queue2)
    pipeline.add(rtppay)
    pipeline.add(udp_sink)

    # Link elements - simplified path with format conversion
    source.link(caps_v4l2)
    caps_v4l2.link(decoder)
    decoder.link(nvvidconv)
    nvvidconv.link(caps_encoder)
    caps_encoder.link(queue_pre_encoder)
    
    # Link queue_pre_encoder -> (OSD) -> encoder
    if BYPASS_OSD:
        # Direct path: queue_pre_encoder -> encoder (maximum performance)
        if not queue_pre_encoder.link(encoder):
            sys.stderr.write(f"ERROR: Failed to link queue_pre_encoder to encoder for {name_suffix}\n")
            return
    else:
        # Path with OSD: queue_pre_encoder -> OSD -> encoder
        if not queue_pre_encoder.link(nvosd):
            sys.stderr.write(f"ERROR: Failed to link queue_pre_encoder to nvosd for {name_suffix}\n")
            return
        if not nvosd.link(encoder):
            sys.stderr.write(f"ERROR: Failed to link nvosd to encoder for {name_suffix}\n")
            return
    
    if not encoder.link(queue2):
        sys.stderr.write(f"ERROR: Failed to link encoder to queue2 for {name_suffix}\n")
        return
    if not queue2.link(rtppay):
        sys.stderr.write(f"ERROR: Failed to link queue2 to rtppay for {name_suffix}\n")
        return
    if not rtppay.link(udp_sink):
        sys.stderr.write(f"ERROR: Failed to link rtppay to udpsink for {name_suffix}\n")
        return

    # FPS probes to verify cadence
    # Probe 1: Right after camera caps (source FPS)
    caps_src = caps_v4l2.get_static_pad("src")
    if caps_src:
        fps_probe_source = create_fps_probe(f"{source_info.name}-source")
        caps_src.add_probe(Gst.PadProbeType.BUFFER, fps_probe_source, None)
    
    # Probe 2: After decoder (raw GPU path)
    decoder_src = decoder.get_static_pad("src")
    if decoder_src:
        fps_probe_raw = create_fps_probe(f"{source_info.name}-raw")
        decoder_src.add_probe(Gst.PadProbeType.BUFFER, fps_probe_raw, None)

    # Probe 3: Before encoder (to check if encoder is the bottleneck)
    encoder_sink = encoder.get_static_pad("sink")
    if encoder_sink:
        fps_probe_pre_enc = create_fps_probe(f"{source_info.name}-pre-enc")
        encoder_sink.add_probe(Gst.PadProbeType.BUFFER, fps_probe_pre_enc, None)
    
    # Probe 4: After encoding (encoded output)
    rtppay_src_pad = rtppay.get_static_pad("src")
    if rtppay_src_pad:
        fps_probe_enc = create_fps_probe(f"{source_info.name}-enc")
        rtppay_src_pad.add_probe(Gst.PadProbeType.BUFFER, fps_probe_enc, None)

    # Optional overlay probe (only if OSD is enabled and overlays are enabled)
    if ENABLE_OVERLAYS and nvosd:
        nvosd_src = nvosd.get_static_pad("src")
        if nvosd_src:
            probe_func = create_buffer_probe(source_info)
            nvosd_src.add_probe(Gst.PadProbeType.BUFFER, probe_func, None)

def main(args):
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
    
    # Print configuration summary
    print("\n=== Configuration Loaded ===")
    print(f"USB Cameras: {CAM1_DEV}, {CAM2_DEV}")
    print(f"Resolution: {CAM_WIDTH}x{CAM_HEIGHT} @ {CAM_FPS}fps")
    print(f"Output RTSP Port: {RTSP_PORT}")
    print(f"Camera 1: rtsp://localhost:{RTSP_PORT}{CAM1_MOUNT}")
    print(f"Camera 2: rtsp://localhost:{RTSP_PORT}{CAM2_MOUNT}")
    print(f"Overlays: {'Enabled' if ENABLE_OVERLAYS else 'Disabled'}")
    print(f"OSD: {'BYPASSED' if BYPASS_OSD else 'ENABLED'}")
    print(f"Encoder: {CONFIG['encoder']['bitrate'] // 1000000} Mbps, keyframe every {CONFIG['encoder']['iframeinterval']} frames")
    print("===========================\n")
    

    # APPLY INTERNAL CAMERA SETTINGS (optional, uncomment to enable)
    print("\n=== Applying Camera Settings ===")
    apply_camera_settings_batch(CONFIG["usb_cameras"]["camera1_device"], CONFIG["usb_cameras"]["internal_camera_settings"])
    apply_camera_settings_batch(CONFIG["usb_cameras"]["camera2_device"], CONFIG["usb_cameras"]["internal_camera_settings"])
    print("=== Camera Settings Applied ===\n")


    Gst.init(None)

    # Create Pipeline
    pipeline = Gst.Pipeline()
    
    # Create Camera 1 Pipeline
    create_camera_pipeline(
        pipeline, 
        CAM1_DEV, 
        CAM_WIDTH, 
        CAM_HEIGHT, 
        CAM_FPS, 
        UDP_PORT_CAM1, 
        source_info_cam1,
        "cam1"
    )

    # Create Camera 2 Pipeline
    create_camera_pipeline(
        pipeline, 
        CAM2_DEV, 
        CAM_WIDTH, 
        CAM_HEIGHT, 
        CAM_FPS, 
        UDP_PORT_CAM2, 
        source_info_cam2,
        "cam2"
    )

    # Setup RTSP Server
    server = GstRtspServer.RTSPServer.new()
    server.set_property("service", RTSP_PORT)
    server.attach(None)
    
    # Create factory for Camera 1
    factory1 = GstRtspServer.RTSPMediaFactory.new()
    factory1.set_launch(
        f"( udpsrc name=pay0 port={UDP_PORT_CAM1} buffer-size={CONFIG['rtsp_media_factory']['buffer_size']} "
        f"caps=\"application/x-rtp, media=video, clock-rate={CONFIG['rtsp_media_factory']['clock_rate']}, "
        f"encoding-name=(string){CONFIG['rtsp_media_factory']['encoding_name']}, payload={CONFIG['rtp_payload']['payload_type']}\" )"
    )
    factory1.set_shared(CONFIG['rtsp_media_factory']['shared'])
    server.get_mount_points().add_factory(CAM1_MOUNT, factory1)
    
    # Create factory for Camera 2
    factory2 = GstRtspServer.RTSPMediaFactory.new()
    factory2.set_launch(
        f"( udpsrc name=pay0 port={UDP_PORT_CAM2} buffer-size={CONFIG['rtsp_media_factory']['buffer_size']} "
        f"caps=\"application/x-rtp, media=video, clock-rate={CONFIG['rtsp_media_factory']['clock_rate']}, "
        f"encoding-name=(string){CONFIG['rtsp_media_factory']['encoding_name']}, payload={CONFIG['rtp_payload']['payload_type']}\" )"
    )
    factory2.set_shared(CONFIG['rtsp_media_factory']['shared'])
    server.get_mount_points().add_factory(CAM2_MOUNT, factory2)
    
    print(f"\n *** Launched Dual Independent RTSP Streams ***")
    print(f" *** Camera 1: rtsp://localhost:{RTSP_PORT}{CAM1_MOUNT} ***")
    print(f" *** Camera 2: rtsp://localhost:{RTSP_PORT}{CAM2_MOUNT} ***")
    print(f" *** Resolution: {CAM_WIDTH}x{CAM_HEIGHT} @ {CAM_FPS}fps ***")
    print(f" *** Encoder: {CONFIG['encoder']['bitrate'] // 1000000} Mbps, keyframe every {CONFIG['encoder']['iframeinterval']} frames ***")
    print(f" *** OSD: {'BYPASSED' if BYPASS_OSD else 'ENABLED'} ***\n")

    # Run Loop
    loop = GObject.MainLoop()
    bus = pipeline.get_bus()
    bus.add_signal_watch()
    bus.connect("message", bus_call, loop)

    pipeline.set_state(Gst.State.PLAYING)
    try:
        loop.run()
    except:
        pass
    pipeline.set_state(Gst.State.NULL)

if __name__ == '__main__':
    sys.exit(main(sys.argv))

