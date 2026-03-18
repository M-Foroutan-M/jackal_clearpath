#!/usr/bin/env python3

import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    slam_params_file = LaunchConfiguration("slam_params_file")

    pkg_share = get_package_share_directory("jackal_custom_config")
    default_yaml = os.path.join(pkg_share, "config", "jackal_slam_2.yaml")

    overrides = {
        "use_sim_time": False,

        # Frames
        "map_frame": "map",
        "odom_frame": "odom",
        "base_frame": "base_link",

        # Correct real lidar topic
        "scan_topic": "/j100_0796/sensors/lidar2d_0/scan",

        # Range / timing
        "min_laser_range": 0.10,
        "max_laser_range": 20.0,
        "scan_queue_size": 200,
        "transform_timeout": 0.3,
        "tf_buffer_duration": 60.0,

        # Publish map->odom
        "transform_publish_period": 0.02,
    }

    return LaunchDescription([
        DeclareLaunchArgument(
            "slam_params_file",
            default_value=default_yaml,
            description="Full path to slam_toolbox params yaml",
        ),

        Node(
            package="slam_toolbox",
            executable="async_slam_toolbox_node",
            name="slam_toolbox",
            output="screen",
            parameters=[slam_params_file, overrides],
            remappings=[
                ("/tf", "/j100_0796/tf"),
                ("/tf_static", "/j100_0796/tf_static"),
            ],
        ),
    ])
