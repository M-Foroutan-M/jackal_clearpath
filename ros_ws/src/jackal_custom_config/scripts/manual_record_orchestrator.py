#!/usr/bin/env python3

import os
import signal
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path

import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node


class AutoRecordOrchestrator(Node):
    def __init__(self):
        super().__init__('auto_record_orchestrator')

        # ---------------------------------------------------------
        # Configuration
        # ---------------------------------------------------------
        self.bag_output_root = '/home/administrator/jackal_bags'

        self.ssh_user = 'arp'
        self.ssh_host = '192.168.5.1'

        self.ssh_start_command = 'bash /home/arp/start_recording.sh'
        self.ssh_stop_command = 'bash /home/arp/stop_recording_and_transfer.sh'

        self.odom_topic = '/j100_0796/platform/odom'

        # Camera warm-up before rosbag starts
        self.camera_warmup_sec = 10

        # Stop if stationary this long, but ONLY after rosbag starts
        self.stationary_timeout_sec = 2.0

        # Motion thresholds
        self.linear_stop_threshold = 0.03   # m/s
        self.angular_stop_threshold = 0.05  # rad/s

        # ---------------------------------------------------------
        # Runtime state
        # ---------------------------------------------------------
        self.mission_active = False
        self.camera_started = False
        self.bag_started = False
        self.stop_logic_enabled = False

        self.bag_process = None
        self.current_bag_name = None

        self.stationary_start_time = None

        # ---------------------------------------------------------
        # ROS interfaces
        # ---------------------------------------------------------
        self.odom_sub = self.create_subscription(
            Odometry,
            self.odom_topic,
            self.odom_callback,
            20
        )

        self.get_logger().info('Auto record orchestrator started')
        self.start_mission()

    # ---------------------------------------------------------
    # Mission control
    # ---------------------------------------------------------
    def start_mission(self):
        if self.mission_active:
            self.get_logger().warn('Mission already active')
            return

        self.get_logger().info('Sending START command to AGX...')
        ok = self.send_ssh(self.ssh_start_command, 'START')
        if not ok:
            self.get_logger().error('Failed to start AGX recording. Exiting.')
            return

        self.camera_started = True
        self.mission_active = True

        self.get_logger().info(
            f'AGX recording started. Waiting {self.camera_warmup_sec} seconds for camera warm-up...'
        )

        for remaining in range(self.camera_warmup_sec, 0, -1):
            self.get_logger().info(f'Rosbag starts in {remaining}...')
            time.sleep(1)

        self.start_bag()

        if not self.bag_started:
            self.get_logger().error('Rosbag failed to start. Stopping AGX recording.')
            if self.camera_started:
                self.send_ssh(self.ssh_stop_command, 'STOP_AFTER_BAG_FAIL')
                self.camera_started = False
            self.mission_active = False
            return

        # Only now enable stop logic
        self.stop_logic_enabled = True
        self.stationary_start_time = None

        self.get_logger().info(
            'Rosbag started. Stationary-stop logic is now ENABLED.'
        )
        self.get_logger().info(
            f'Mission will stop if robot stays stationary for more than {self.stationary_timeout_sec:.1f} seconds.'
        )

    def stop_mission(self, reason='Unknown reason'):
        if not self.mission_active:
            self.get_logger().warn('Mission is not active')
            return

        self.get_logger().info(f'Stopping mission. Reason: {reason}')

        self.stop_logic_enabled = False
        self.stationary_start_time = None

        self.stop_bag()

        if self.camera_started:
            self.send_ssh(self.ssh_stop_command, 'STOP')
            self.camera_started = False

        self.mission_active = False
        self.bag_started = False

        self.get_logger().info('Mission complete')
        rclpy.shutdown()

    # ---------------------------------------------------------
    # Odom monitoring
    # ---------------------------------------------------------
    def odom_callback(self, msg: Odometry):
        if not self.mission_active:
            return

        if not self.bag_started:
            return

        if not self.stop_logic_enabled:
            return

        vx = msg.twist.twist.linear.x
        vy = msg.twist.twist.linear.y
        vz = msg.twist.twist.linear.z

        wx = msg.twist.twist.angular.x
        wy = msg.twist.twist.angular.y
        wz = msg.twist.twist.angular.z

        linear_speed = (vx**2 + vy**2 + vz**2) ** 0.5
        angular_speed = (wx**2 + wy**2 + wz**2) ** 0.5

        moving = (
            linear_speed >= self.linear_stop_threshold or
            angular_speed >= self.angular_stop_threshold
        )

        now_sec = self.get_clock().now().nanoseconds / 1e9

        if moving:
            if self.stationary_start_time is not None:
                self.get_logger().info(
                    f'Motion detected again '
                    f'(linear={linear_speed:.3f} m/s, angular={angular_speed:.3f} rad/s). '
                    f'Resetting stationary timer.'
                )
            self.stationary_start_time = None
        else:
            if self.stationary_start_time is None:
                self.stationary_start_time = now_sec
                self.get_logger().info(
                    f'Robot appears stationary '
                    f'(linear={linear_speed:.3f} m/s, angular={angular_speed:.3f} rad/s). '
                    f'Starting stationary timer...'
                )
            else:
                stationary_duration = now_sec - self.stationary_start_time
                if stationary_duration >= self.stationary_timeout_sec:
                    self.get_logger().info(
                        f'Robot stationary for {stationary_duration:.2f} seconds. '
                        f'Threshold reached.'
                    )
                    self.stop_mission(reason='Robot stationary for more than 2 seconds')

    # ---------------------------------------------------------
    # Rosbag
    # ---------------------------------------------------------
    def start_bag(self):
        if self.bag_process is not None and self.bag_process.poll() is None:
            self.get_logger().warn('rosbag already running')
            self.bag_started = True
            return

        Path(self.bag_output_root).mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_suffix = uuid.uuid4().hex[:8]
        self.current_bag_name = f'auto_{timestamp}_{unique_suffix}'
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
            self.bag_started = True
            self.get_logger().info('rosbag started successfully')
        except Exception as exc:
            self.get_logger().error(f'Failed to start rosbag recording: {exc}')
            self.bag_process = None
            self.bag_started = False

    def stop_bag(self):
        if self.bag_process is None:
            self.get_logger().warn('No rosbag process to stop')
            return

        if self.bag_process.poll() is not None:
            self.get_logger().info('rosbag process already exited')
            self.bag_process = None
            self.bag_started = False
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
            self.bag_started = False

    # ---------------------------------------------------------
    # SSH
    # ---------------------------------------------------------
    def send_ssh(self, command: str, label: str) -> bool:
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
                timeout=20
            )
            if result.returncode == 0:
                self.get_logger().info(f'{label} command success')
                return True
            else:
                self.get_logger().error(
                    f'{label} command failed. rc={result.returncode}, '
                    f'stdout="{result.stdout.strip()}", stderr="{result.stderr.strip()}"'
                )
                return False
        except Exception as exc:
            self.get_logger().error(f'{label} SSH exception: {exc}')
            return False

    # ---------------------------------------------------------
    # Shutdown
    # ---------------------------------------------------------
    def cleanup(self):
        self.get_logger().info('Cleanup called')

        self.stop_logic_enabled = False
        self.stationary_start_time = None

        if self.bag_started:
            self.stop_bag()

        if self.camera_started:
            self.send_ssh(self.ssh_stop_command, 'STOP_ON_SHUTDOWN')
            self.camera_started = False

        self.mission_active = False


def main(args=None):
    rclpy.init(args=args)
    node = AutoRecordOrchestrator()

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        node.get_logger().info('KeyboardInterrupt received')
    finally:
        node.cleanup()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
