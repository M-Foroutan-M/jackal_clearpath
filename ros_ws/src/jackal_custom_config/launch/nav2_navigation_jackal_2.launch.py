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
    default_params = os.path.join(pkg_share, "config", "nav2_jackal_2.yaml")

    common_remaps = [
        ("/tf", "/j100_0796/tf"),
        ("/tf_static", "/j100_0796/tf_static"),
        ("/cmd_vel", "/j100_0796/cmd_vel"),
    ]

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
            description="Full path to navigation params file"
        ),

        Node(
            package="nav2_controller",
            executable="controller_server",
            name="controller_server",
            namespace=namespace,
            output="screen",
            parameters=[params_file, {"use_sim_time": use_sim_time}],
            remappings=common_remaps,
        ),

        Node(
            package="nav2_planner",
            executable="planner_server",
            name="planner_server",
            namespace=namespace,
            output="screen",
            parameters=[params_file, {"use_sim_time": use_sim_time}],
            remappings=common_remaps,
        ),

        Node(
            package="nav2_behaviors",
            executable="behavior_server",
            name="behavior_server",
            namespace=namespace,
            output="screen",
            parameters=[params_file, {"use_sim_time": use_sim_time}],
            remappings=common_remaps,
        ),

        Node(
            package="nav2_bt_navigator",
            executable="bt_navigator",
            name="bt_navigator",
            namespace=namespace,
            output="screen",
            parameters=[params_file, {"use_sim_time": use_sim_time}],
            remappings=common_remaps,
        ),

        Node(
            package="nav2_waypoint_follower",
            executable="waypoint_follower",
            name="waypoint_follower",
            namespace=namespace,
            output="screen",
            parameters=[params_file, {"use_sim_time": use_sim_time}],
            remappings=common_remaps,
        ),

        Node(
            package="nav2_velocity_smoother",
            executable="velocity_smoother",
            name="velocity_smoother",
            namespace=namespace,
            output="screen",
            parameters=[params_file, {"use_sim_time": use_sim_time}],
            remappings=common_remaps + [
                ("cmd_vel", "cmd_vel_nav"),
                ("cmd_vel_smoothed", "cmd_vel"),
                ("odom", "/j100_0796/platform/odom/filtered"),
            ],
        ),

        Node(
            package="nav2_lifecycle_manager",
            executable="lifecycle_manager",
            name="lifecycle_manager_navigation",
            namespace=namespace,
            output="screen",
            parameters=[{
                "use_sim_time": use_sim_time,
                "autostart": True,
                "node_names": [
                    "controller_server",
                    "planner_server",
                    "behavior_server",
                    "bt_navigator",
                    "waypoint_follower",
                    "velocity_smoother",
                ]
            }],
        ),
    ])
