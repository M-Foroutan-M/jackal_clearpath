#!/usr/bin/env python3

import os
import signal
import subprocess
import sys
import termios
import tty
import uuid
import select
from datetime import datetime
from pathlib import Path

import rclpy
from rclpy.node import Node


class KeyboardRecordOrchestrator(Node):
    def __init__(self):
        super().__init__('keyboard_record_orchestrator')

        self.bag_output_root = '/home/administrator/jackal_bags'

        self.ssh_user = 'arp'
        self.ssh_host = '192.168.5.1'

        self.ssh_start_command = 'bash /home/arp/start_recording.sh'
        self.ssh_stop_command = 'bash /home/arp/stop_recording_and_transfer.sh'

        self.start_key = 's'
        self.stop_key = 'x'
        self.quit_key = 'q'

        self.mission_active = False
        self.bag_process = None
        self.current_bag_name = None
        self.camera_started = False

        self.stdin_is_tty = sys.stdin.isatty()
        self.settings = None

        self.get_logger().info('Keyboard record orchestrator is ready')
        self.get_logger().info(
            f'Press "{self.start_key}" to START, "{self.stop_key}" to STOP, "{self.quit_key}" to quit'
        )

        if not self.stdin_is_tty:
            self.get_logger().error(
                'stdin is not a TTY. Keyboard input will not work. '
                'Run this node directly in an interactive terminal.'
            )
        else:
            self.settings = termios.tcgetattr(sys.stdin.fileno())

        self.key_timer = self.create_timer(0.05, self.poll_keyboard)

    # ---------------------------------------------------------
    # Keyboard polling
    # ---------------------------------------------------------
    def poll_keyboard(self):
        if not self.stdin_is_tty:
            return

        key = self.get_key_nonblocking()
        if key is None:
            return

        self.get_logger().info(f'Key received: {repr(key)}')

        if key == self.start_key:
            if not self.mission_active:
                self.start_mission()
            else:
                self.get_logger().warn('Mission already active')

        elif key == self.stop_key:
            if self.mission_active:
                self.stop_mission()
            else:
                self.get_logger().warn('No active mission to stop')

        elif key == self.quit_key:
            self.get_logger().info('Quit requested')
            self.cleanup()
            rclpy.shutdown()

    def get_key_nonblocking(self):
        fd = sys.stdin.fileno()
        tty.setraw(fd)
        try:
            rlist, _, _ = select.select([sys.stdin], [], [], 0)
            if rlist:
                return sys.stdin.read(1)
            return None
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, self.settings)

    # ---------------------------------------------------------
    # Mission control
    # ---------------------------------------------------------
    def start_mission(self):
        self.get_logger().info('START key pressed -> starting recording mission')

        self.send_ssh(self.ssh_start_command, 'START')
        self.camera_started = True

        self.start_bag()

        self.mission_active = True
        self.get_logger().info('Mission active')

    def stop_mission(self):
        self.get_logger().info('STOP key pressed -> stopping recording mission')

        self.stop_bag()

        if self.camera_started:
            self.send_ssh(self.ssh_stop_command, 'STOP')
            self.camera_started = False

        self.mission_active = False
        self.get_logger().info('Mission complete, ready for next run')

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
        self.current_bag_name = f'keyboard_{timestamp}_{unique_suffix}'
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
        except Exception as exc:
            self.get_logger().error(f'Failed to start rosbag recording: {exc}')
            self.bag_process = None

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

    # ---------------------------------------------------------
    # SSH
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
    # Shutdown
    # ---------------------------------------------------------
    def cleanup(self):
        if self.mission_active:
            self.stop_bag()

            if self.camera_started:
                self.send_ssh(self.ssh_stop_command, 'STOP on shutdown')
                self.camera_started = False

        self.mission_active = False

        if self.stdin_is_tty and self.settings is not None:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self.settings)


def main(args=None):
    rclpy.init(args=args)
    node = KeyboardRecordOrchestrator()

    try:
        rclpy.spin(node)
    finally:
        node.cleanup()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
