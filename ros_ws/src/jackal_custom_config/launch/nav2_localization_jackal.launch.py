from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    namespace = LaunchConfiguration('namespace')
    map_yaml = LaunchConfiguration('map')
    params_file = LaunchConfiguration('params_file')
    use_sim_time = LaunchConfiguration('use_sim_time')

    return LaunchDescription([
        DeclareLaunchArgument(
            'namespace',
            default_value='j100_0796'
        ),
        DeclareLaunchArgument(
            'map',
            default_value='/home/administrator/maps/jackal_map.yaml'
        ),
        DeclareLaunchArgument(
            'params_file',
            default_value='/home/administrator/jackal_clearpath_ws/ros_ws/src/jackal_custom_config/config/nav2_jackal.yaml'
        ),
        DeclareLaunchArgument(
            'use_sim_time',
            default_value='false'
        ),

        Node(
            package='nav2_map_server',
            executable='map_server',
            name='map_server',
            namespace=namespace,
            output='screen',
            parameters=[
                params_file,
                {
                    'use_sim_time': use_sim_time,
                    'yaml_filename': map_yaml
                }
            ]
        ),

        Node(
            package='nav2_amcl',
            executable='amcl',
            name='amcl',
            namespace=namespace,
            output='screen',
            parameters=[
                params_file,
                {
                    'use_sim_time': use_sim_time
                }
            ]
        ),

        Node(
            package='nav2_lifecycle_manager',
            executable='lifecycle_manager',
            name='lifecycle_manager_localization',
            namespace=namespace,
            output='screen',
            parameters=[{
                'use_sim_time': use_sim_time,
                'autostart': True,
                'node_names': ['map_server', 'amcl']
            }]
        ),
    ])
