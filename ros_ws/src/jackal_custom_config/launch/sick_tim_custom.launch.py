import tempfile
import yaml

from launch import LaunchDescription
from launch_ros.actions import Node

ROBOT_NS = "j100_0796"


def generate_launch_description():
    # Params apply to any node (avoids name/namespace mismatches)
    sick_params = {
        "/**": {
            "ros__parameters": {
                "scanner_type": "sick_tim_5xx",
                "hostname": "192.168.131.20",

                # Some builds declare port as string; avoid setting "port" to int.
                # Keep ONLY port_number as int (this avoids the type-conflict warning).
                "port_number": 2112,

                "use_binary_protocol": True,
                "frame_id": "lidar2d_0_laser",
                "laserscan_topic": "scan",

                # --- TF: do not publish into global TF tree ---
                "tf_publish_rate": 0.0,
                "tf_parent_frame_id": "base_link",
                "tf_base_frame_id": "base_link",
                "nav_tf_parent_frame_id": "base_link",

                # --- TIMESTAMPING: avoid PLL/tick time causing bad stamps ---
                # Use ROS time (arrival time) instead of lidar "generation" stamps.
                "use_generation_timestamp": False,
                # Keep tick mapping disabled
                "tick_to_timestamp_mode": 0,
                # Do NOT gate publishing on PLL lock
                "sw_pll_only_publish": False,
            }
        }
    }

    tmp = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".yaml")
    yaml.safe_dump(sick_params, tmp, default_flow_style=False)
    tmp.flush()
    tmp.close()
    sick_param_file = tmp.name

    return LaunchDescription([
        Node(
            package="sick_scan_xd",
            executable="sick_generic_caller",
            name="sick_scan_xd",
            namespace=f"{ROBOT_NS}/sensors/lidar2d_0",
            output="screen",
            parameters=[sick_param_file],
            # IMPORTANT: remap TF away so it cannot pollute the robot TF tree
            remappings=[
                ("/tf", f"/{ROBOT_NS}/sensors/lidar2d_0/tf_bad"),
                ("/tf_static", f"/{ROBOT_NS}/sensors/lidar2d_0/tf_static_bad"),
            ],
        ),

        # Relay scan to /j100_0796/scan for consumers (slam_toolbox, nav2, etc.)
        Node(
            package="topic_tools",
            executable="relay",
            namespace=ROBOT_NS,
            arguments=[
                f"/{ROBOT_NS}/sensors/lidar2d_0/scan",
                f"/{ROBOT_NS}/scan",
            ],
            output="screen",
        ),
    ])
