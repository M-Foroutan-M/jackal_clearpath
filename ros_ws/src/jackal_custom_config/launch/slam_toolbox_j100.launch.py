from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

def generate_launch_description():
    use_sim_time = LaunchConfiguration('use_sim_time')
    scan_topic    = LaunchConfiguration('scan_topic')
    base_frame    = LaunchConfiguration('base_frame')
    odom_frame    = LaunchConfiguration('odom_frame')
    map_frame     = LaunchConfiguration('map_frame')

    return LaunchDescription([
        DeclareLaunchArgument('use_sim_time', default_value='false'),
        DeclareLaunchArgument('scan_topic', default_value='/j100_0796/sensors/lidar2d_0/scan'),
        DeclareLaunchArgument('base_frame', default_value='base_link'),
        DeclareLaunchArgument('odom_frame', default_value='odom'),
        DeclareLaunchArgument('map_frame',  default_value='map'),

        Node(
            package='slam_toolbox',
            executable='async_slam_toolbox_node',
            name='slam_toolbox',
            output='screen',
            parameters=[{
                'use_sim_time': use_sim_time,
                'base_frame': base_frame,
                'odom_frame': odom_frame,
                'map_frame': map_frame,
                # optional but helps:
                'scan_queue_size': 50,
            }],
            remappings=[
                ('/scan', scan_topic),
                ('/tf', '/j100_0796/tf'),
                ('/tf_static', '/j100_0796/tf_static'),
            ]
        )
    ])
