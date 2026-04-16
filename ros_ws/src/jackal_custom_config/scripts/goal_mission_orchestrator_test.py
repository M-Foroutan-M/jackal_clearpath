#!/usr/bin/env python3

import math
import os
import signal
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import rclpy
from rclpy.node import Node

from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped, Twist


class GoalMissionOrchestratorTest(Node):
    def __init__(self):
        super().__init__('goal_mission_orchestrator_test')

        # Topics
        self.input_goal_topic = '/goal_pose_hijacked'
        self.output_goal_topic = '/j100_0796/goal_pose'
        self.pose_topic = '/j100_0796/amcl_pose'
        self.cmd_vel_topic = '/j100_0796/cmd_vel'

        # Timing
        self.goal_delay_sec = 10.0
        self.stop_after_goal_sec = 3.0

        # Goal detection
        self.goal_position_tolerance_m = 0.25
        self.goal_yaw_tolerance_rad = 0.35
        self.stopped_linear_threshold = 0.03
        self.stopped_angular_threshold = 0.05
        self.goal_hold_time_sec = 1.0

        # Rosbag
        self.bag_output_root = '/home/administrator/jackal_bags'
        self.bag_topics = [
            '/j100_0796/sensors/lidar2d_0/scan',
            '/j100_0796/odom',
            '/j100_0796/amcl_pose',
            '/j100_0796/cmd_vel',
            '/j100_0796/tf',
            '/j100_0796/tf_static',
            '/j100_0796/goal_pose',
            '/goal_pose_hijacked',
        ]

        # SSH TEST CONFIG
        self.ssh_host = '192.168.5.1'
        self.ssh_user = 'arp'

        # Instead of scripts → create files
        self.start_cmd = 'touch ~/start.txt'
        self.stop_cmd = 'touch ~/stop.txt'

        # State
        self.pending_goal: Optional[PoseStamped] = None
        self.active_goal: Optional[PoseStamped] = None

        self.latest_pose = None
        self.latest_cmd_vel = None

        self.goal_delay_timer = None
        self.stop_timer = None
        self.goal_hold_start = None

        self.bag_process = None
        self.mission_active = False

        # ROS interfaces
        self.goal_sub = self.create_subscription(
            PoseStamped,
            self.input_goal_topic,
            self.goal_callback,
            10
        )

        self.pose_sub = self.create_subscription(
            PoseWithCovarianceStamped,
            self.pose_topic,
            self.pose_callback,
            10
        )

        self.cmd_sub = self.create_subscription(
            Twist,
            self.cmd_vel_topic,
            self.cmd_callback,
            10
        )

        self.goal_pub = self.create_publisher(
            PoseStamped,
            self.output_goal_topic,
            10
        )

        self.monitor_timer = self.create_timer(0.1, self.monitor_goal)

        self.get_logger().info('TEST orchestrator ready')

    # =====================
    def goal_callback(self, msg):
        if self.mission_active:
            self.get_logger().warn('Mission already active')
            return

        self.mission_active = True
        self.pending_goal = msg

        self.get_logger().info('Goal received → sending START test')

        self.send_ssh(self.start_cmd)

        self.goal_delay_timer = self.create_timer(
            self.goal_delay_sec,
            self.release_goal
        )

    # =====================
    def release_goal(self):
        self.goal_delay_timer.cancel()

        self.start_bag()

        self.active_goal = self.pending_goal
        self.goal_pub.publish(self.active_goal)

        self.get_logger().info('Goal published to Nav2')

    # =====================
    def pose_callback(self, msg):
        self.latest_pose = msg.pose.pose

    def cmd_callback(self, msg):
        self.latest_cmd_vel = msg

    # =====================
    def monitor_goal(self):
        if not self.mission_active:
            return

        if self.active_goal is None:
            return

        if self.latest_pose is None or self.latest_cmd_vel is None:
            return

        gx = self.active_goal.pose.position.x
        gy = self.active_goal.pose.position.y

        cx = self.latest_pose.position.x
        cy = self.latest_pose.position.y

        dist = math.hypot(gx - cx, gy - cy)

        linear = abs(self.latest_cmd_vel.linear.x)
        angular = abs(self.latest_cmd_vel.angular.z)

        if dist < self.goal_position_tolerance_m and linear < 0.03 and angular < 0.05:
            now = self.get_clock().now().nanoseconds / 1e9

            if self.goal_hold_start is None:
                self.goal_hold_start = now
                return

            if now - self.goal_hold_start > 1.0:
                if self.stop_timer is None:
                    self.get_logger().info('Goal reached → stopping soon')
                    self.stop_timer = self.create_timer(3.0, self.finish)

        else:
            self.goal_hold_start = None

    # =====================
    def finish(self):
        self.stop_timer.cancel()

        self.get_logger().info('Stopping rosbag + sending STOP test')

        self.stop_bag()
        self.send_ssh(self.stop_cmd)

        self.reset()

    # =====================
    def start_bag(self):
        Path(self.bag_output_root).mkdir(parents=True, exist_ok=True)

        name = f"test_{datetime.now().strftime('%H%M%S')}_{uuid.uuid4().hex[:4]}"
        path = os.path.join(self.bag_output_root, name)

        cmd = ['ros2', 'bag', 'record', '--output', path] + self.bag_topics

        self.bag_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid
        )

        self.get_logger().info(f'Bag started: {name}')

    def stop_bag(self):
        if self.bag_process:
            os.killpg(os.getpgid(self.bag_process.pid), signal.SIGINT)
            self.bag_process = None
            self.get_logger().info('Bag stopped')

    # =====================
    def send_ssh(self, command):
        subprocess.run(['ssh', f'{self.ssh_user}@{self.ssh_host}', command])

    def reset(self):
        self.pending_goal = None
        self.active_goal = None
        self.goal_hold_start = None
        self.mission_active = False


def main(args=None):
    rclpy.init(args=args)
    node = GoalMissionOrchestratorTest()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
