#!/usr/bin/env python3

import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    namespace = LaunchConfiguration("namespace")
    params_file = LaunchConfiguration("params_file")
    use_sim_time = LaunchConfiguration("use_sim_time")

    pkg_share = get_package_share_directory("jackal_custom_config")
    default_params = os.path.join(
        pkg_share, "config", "active", "nav2_localization.yaml"
    )

    return LaunchDescription([
        DeclareLaunchArgument(
            "namespace",
            default_value="j100_0796",
            description="Robot namespace"
        ),

        DeclareLaunchArgument(
            "use_sim_time",
            default_value="false",
            description="Use simulation time"
        ),

        DeclareLaunchArgument(
            "params_file",
            default_value=default_params,
            description="Full path to localization params file"
        ),

        Node(
            package="nav2_map_server",
            executable="map_server",
            name="map_server",
            namespace=namespace,
            output="screen",
            parameters=[params_file, {"use_sim_time": use_sim_time}],
            remappings=[
                ("/tf", "/j100_0796/tf"),
                ("/tf_static", "/j100_0796/tf_static"),
            ],
        ),

        Node(
            package="nav2_amcl",
            executable="amcl",
            name="amcl",
            namespace=namespace,
            output="screen",
            parameters=[params_file, {"use_sim_time": use_sim_time}],
            remappings=[
                ("/tf", "/j100_0796/tf"),
                ("/tf_static", "/j100_0796/tf_static"),
            ],
        ),

        Node(
            package="nav2_lifecycle_manager",
            executable="lifecycle_manager",
            name="lifecycle_manager_localization",
            namespace=namespace,
            output="screen",
            parameters=[{
                "use_sim_time": use_sim_time,
                "autostart": True,
                "node_names": ["map_server", "amcl"]
            }],
        ),
    ])
