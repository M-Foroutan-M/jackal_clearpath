from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(
            package='jackal_custom_config',
            executable='goal_mission_orchestrator.py',
            name='goal_mission_orchestrator',
            output='screen',
            parameters=[{
                # =========================
                # Topics
                # =========================
                'input_goal_topic': '/goal_pose_hijacked',
                'output_goal_topic': '/j100_0796/goal_pose',

                # =========================
                # Timing
                # =========================
                'goal_delay_sec': 10.0,

                # =========================
                # Rosbag
                # =========================
                'bag_output_root': '/home/administrator/jackal_bags',

                # =========================
                # SSH (TEST MODE)
                # =========================
                'ssh_user': 'arp',
                'ssh_host': '192.168.5.1',

                # These are TEST commands
                'ssh_start_command': 'bash /home/arp/start_recording.sh',
                'ssh_stop_command': 'bash /home/arp/stop_recording_and_transfer.sh',

                # =========================
                # NOTE
                # =========================
                # Replace the above two commands later with:
                # 'ssh_start_command': 'bash /home/nvidia/start_recording.sh',
                # 'ssh_stop_command': 'bash /home/nvidia/stop_recording.sh',
            }]
        )
    ])
