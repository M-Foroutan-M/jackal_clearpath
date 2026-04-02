from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(
            package='usb_cam',
            executable='usb_cam_node_exe',
            namespace='logitech_c920',
            name='logitech_c920_node',
            output='screen',
            parameters=[
                '/home/administrator/jackal_clearpath_ws/ros_ws/src/jackal_custom_config/config/active/logitech_c920.yaml'
            ]
        )
    ])

