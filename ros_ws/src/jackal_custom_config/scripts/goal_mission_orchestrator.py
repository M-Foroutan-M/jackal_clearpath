#!/usr/bin/env python3

import os
import signal
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node

from action_msgs.msg import GoalStatusArray
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import NavigateToPose


class GoalMissionOrchestrator(Node):
    def __init__(self):
        super().__init__('goal_mission_orchestrator')

        # Topics
        self.input_goal_topic = '/goal_pose_hijacked'
        self.output_goal_topic = '/j100_0796/goal_pose'
        self.goal_status_topic = '/j100_0796/navigate_to_pose/_action/status'

        # Timing
        self.goal_delay_sec = 10.0

        # Rosbag
        self.bag_output_root = '/home/administrator/jackal_bags'

        # SSH target
        self.ssh_host = '192.168.5.1'
        self.ssh_user = 'arp'

        # TEST COMMANDS FOR NOW
        self.start_cmd = 'bash /home/arp/start_recording.sh'
        self.stop_cmd = 'bash /home/arp/stop_recording_and_transfer.sh'

        # Replace the two commands above later with your real recording commands, for example:
        # self.start_cmd = 'bash /home/nvidia/start_recording.sh'
        # self.stop_cmd  = 'bash /home/nvidia/stop_recording.sh'

        # State
        self.pending_goal: Optional[PoseStamped] = None
        self.active_goal: Optional[PoseStamped] = None

        self.goal_delay_timer = None
        self.bag_process: Optional[subprocess.Popen] = None
        self.current_bag_name: Optional[str] = None

        self.mission_active = False
        self.nav_goal_sent = False
        self.nav_done = False
        self.recording_started = False
        self.camera_started = False

        self.current_goal_handle = None
        self.current_goal_uuid = None

        # ROS interfaces
        self.goal_sub = self.create_subscription(
            PoseStamped,
            self.input_goal_topic,
            self.goal_callback,
            10
        )

        self.status_sub = self.create_subscription(
            GoalStatusArray,
            self.goal_status_topic,
            self.status_callback,
            10
        )

        self.goal_pub = self.create_publisher(
            PoseStamped,
            self.output_goal_topic,
            10
        )

        self.nav_action_client = ActionClient(
            self,
            NavigateToPose,
            '/j100_0796/navigate_to_pose'
        )

        self.get_logger().info('Goal mission orchestrator is ready')

    # ---------------------------------------------------------
    # Main sequence
    # ---------------------------------------------------------
    def goal_callback(self, msg: PoseStamped):
        if self.mission_active:
            self.get_logger().warn('Mission already active, ignoring new hijacked goal')
            return

        self.mission_active = True
        self.nav_goal_sent = False
        self.nav_done = False
        self.recording_started = False
        self.camera_started = False
        self.current_goal_handle = None
        self.current_goal_uuid = None

        self.pending_goal = msg
        self.active_goal = None

        self.get_logger().info(
            f'Hijacked goal received: x={msg.pose.position.x:.3f}, y={msg.pose.position.y:.3f}'
        )

        # Start remote recording trigger immediately
        self.send_ssh(self.start_cmd, 'START')
        self.camera_started = True

        # Delay mission release
        self.goal_delay_timer = self.create_timer(
            self.goal_delay_sec,
            self.release_goal_once
        )

    def release_goal_once(self):
        if self.goal_delay_timer is not None:
            self.goal_delay_timer.cancel()
            self.goal_delay_timer = None

        if self.pending_goal is None:
            self.get_logger().error('No pending goal found, aborting mission')
            self.reset_state()
            return

        # Start bag first
        self.start_bag()

        # Publish actual goal topic for Nav2 / RViz path compatibility
        self.active_goal = self.pending_goal
        self.goal_pub.publish(self.active_goal)

        self.get_logger().info(
            f'Published goal on {self.output_goal_topic}: '
            f'x={self.active_goal.pose.position.x:.3f}, '
            f'y={self.active_goal.pose.position.y:.3f}'
        )

        # Also send the same goal through the Nav2 action client so we can reliably
        # know when the navigation mission is alive / finished.
        self.send_nav2_goal(self.active_goal)

        self.pending_goal = None

    # ---------------------------------------------------------
    # Nav2 action handling
    # ---------------------------------------------------------
    def send_nav2_goal(self, pose_msg: PoseStamped):
        if not self.nav_action_client.wait_for_server(timeout_sec=5.0):
            self.get_logger().error('NavigateToPose action server not available')
            self.finish_mission()
            return

        goal_msg = NavigateToPose.Goal()
        goal_msg.pose = pose_msg

        self.get_logger().info('Sending goal to NavigateToPose action server')

        send_future = self.nav_action_client.send_goal_async(goal_msg)
        send_future.add_done_callback(self.goal_response_callback)

    def goal_response_callback(self, future):
        try:
            goal_handle = future.result()
        except Exception as exc:
            self.get_logger().error(f'Failed to send goal to Nav2: {exc}')
            self.finish_mission()
            return

        if not goal_handle.accepted:
            self.get_logger().warn('Nav2 goal was rejected')
            self.finish_mission()
            return

        self.current_goal_handle = goal_handle
        self.current_goal_uuid = bytes(goal_handle.goal_id.uuid)
        self.nav_goal_sent = True

        self.get_logger().info('Nav2 goal accepted')

        result_future = goal_handle.get_result_async()
        result_future.add_done_callback(self.nav_result_callback)

    def nav_result_callback(self, future):
        try:
            result = future.result()
        except Exception as exc:
            self.get_logger().error(f'Error while waiting for Nav2 result: {exc}')
            self.finish_mission()
            return

        status = result.status
        self.get_logger().info(f'Nav2 mission finished with status code: {status}')
        self.nav_done = True
        self.finish_mission()

    def status_callback(self, msg: GoalStatusArray):
        # Optional debug visibility for the active action goal
        if not self.nav_goal_sent or self.current_goal_uuid is None:
            return

        for status in msg.status_list:
            if bytes(status.goal_info.goal_id.uuid) == self.current_goal_uuid:
                self.get_logger().debug(f'Active Nav2 status: {status.status}')
                break

    # ---------------------------------------------------------
    # Rosbag
    # ---------------------------------------------------------
    def start_bag(self):
        if self.bag_process is not None and self.bag_process.poll() is None:
            self.get_logger().warn('rosbag already running')
            return

        Path(self.bag_output_root).mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_suffix = uuid.uuid4().hex[:8]
        self.current_bag_name = f'mission_{timestamp}_{unique_suffix}'
        bag_full_path = os.path.join(self.bag_output_root, self.current_bag_name)

        cmd = [
            'ros2', 'bag', 'record',
            '-a',
            '--output', bag_full_path,
        ]

        self.get_logger().info(f'Starting rosbag recording: {bag_full_path}')

        try:
            self.bag_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                preexec_fn=os.setsid
            )
            self.recording_started = True
        except Exception as exc:
            self.get_logger().error(f'Failed to start rosbag recording: {exc}')
            self.bag_process = None
            self.recording_started = False

    def stop_bag(self):
        if self.bag_process is None:
            self.get_logger().warn('No rosbag process to stop')
            return

        if self.bag_process.poll() is not None:
            self.get_logger().info('rosbag process already exited')
            self.bag_process = None
            return

        try:
            os.killpg(os.getpgid(self.bag_process.pid), signal.SIGINT)
            self.bag_process.wait(timeout=20)
            self.get_logger().info(f'rosbag saved: {self.current_bag_name}')
        except subprocess.TimeoutExpired:
            self.get_logger().warn('rosbag did not stop in time, sending SIGTERM')
            try:
                os.killpg(os.getpgid(self.bag_process.pid), signal.SIGTERM)
                self.bag_process.wait(timeout=5)
            except Exception as exc:
                self.get_logger().error(f'Failed to terminate rosbag: {exc}')
        except Exception as exc:
            self.get_logger().error(f'Error stopping rosbag: {exc}')
        finally:
            self.bag_process = None
            self.current_bag_name = None
            self.recording_started = False

    # ---------------------------------------------------------
    # SSH helpers
    # ---------------------------------------------------------
    def send_ssh(self, command: str, label: str):
        ssh_cmd = [
            'ssh',
            '-o', 'BatchMode=yes',
            '-o', 'StrictHostKeyChecking=no',
            f'{self.ssh_user}@{self.ssh_host}',
            command,
        ]

        self.get_logger().info(f'Sending SSH command: {label}')

        try:
            result = subprocess.run(
                ssh_cmd,
                capture_output=True,
                text=True,
                timeout=15
            )
            if result.returncode == 0:
                self.get_logger().info(f'{label} command success')
            else:
                self.get_logger().error(
                    f'{label} command failed. rc={result.returncode}, '
                    f'stdout="{result.stdout.strip()}", stderr="{result.stderr.strip()}"'
                )
        except Exception as exc:
            self.get_logger().error(f'{label} SSH exception: {exc}')

    # ---------------------------------------------------------
    # Finish / reset
    # ---------------------------------------------------------
    def finish_mission(self):
        if not self.mission_active:
            return

        self.get_logger().info('Finishing mission')

        if self.goal_delay_timer is not None:
            self.goal_delay_timer.cancel()
            self.goal_delay_timer = None

        self.stop_bag()

        if self.camera_started:
            self.send_ssh(self.stop_cmd, 'STOP')

        self.reset_state()
        self.get_logger().info('Mission complete, ready for next goal')

    def reset_state(self):
        self.pending_goal = None
        self.active_goal = None

        self.mission_active = False
        self.nav_goal_sent = False
        self.nav_done = False
        self.recording_started = False
        self.camera_started = False

        self.current_goal_handle = None
        self.current_goal_uuid = None

    # ---------------------------------------------------------
    # Shutdown
    # ---------------------------------------------------------
    def cleanup(self):
        if self.goal_delay_timer is not None:
            self.goal_delay_timer.cancel()
            self.goal_delay_timer = None

        self.stop_bag()

        if self.camera_started:
            self.send_ssh(self.stop_cmd, 'STOP on shutdown')

        self.reset_state()


def main(args=None):
    rclpy.init(args=args)
    node = GoalMissionOrchestrator()

    try:
        rclpy.spin(node)
    finally:
        node.cleanup()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
