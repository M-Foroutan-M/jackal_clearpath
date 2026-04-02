from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    camera_1 = Node(
        package='usb_cam',
        executable='usb_cam_node_exe',
        namespace='logitech_c920_1',
        name='logitech_c920_1_node',
        output='screen',
        parameters=[
            '/home/administrator/jackal_clearpath_ws/ros_ws/src/jackal_custom_config/config/active/logitech_c920_1.yaml'
        ]
    )

    camera_2 = Node(
        package='usb_cam',
        executable='usb_cam_node_exe',
        namespace='logitech_c920_2',
        name='logitech_c920_2_node',
        output='screen',
        parameters=[
            '/home/administrator/jackal_clearpath_ws/ros_ws/src/jackal_custom_config/config/active/logitech_c920_2.yaml'
        ]
    )

    return LaunchDescription([
        camera_1,
        camera_2
    ])
