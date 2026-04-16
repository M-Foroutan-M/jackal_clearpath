#!/usr/bin/env python3

import subprocess
import rclpy
from rclpy.node import Node


class SimpleRecordTrigger(Node):

    def __init__(self):
        super().__init__('simple_record_trigger')

        # SSH CONFIG
        self.ssh_user = 'arp'
        self.ssh_host = '192.168.5.1'

        # TEST COMMANDS (CHANGE LATER)
        self.start_cmd = 'bash /home/arp/start_recording.sh'
        self.stop_cmd =  'bash /home/arp/stop_recording_and_transfer.sh'

        # 👉 Replace later with:
        # self.start_cmd = 'bash /home/nvidia/start_recording.sh'
        # self.stop_cmd  = 'bash /home/nvidia/stop_recording.sh'

        self.get_logger().info('Sending START command')
        self.send_ssh(self.start_cmd, 'START')

        # 2 minutes timer (120 sec)
        self.timer = self.create_timer(200.0, self.stop_once)

        self.sent_stop = False

    # -------------------------
    def stop_once(self):
        if self.sent_stop:
            return

        self.sent_stop = True

        self.get_logger().info('Sending STOP command')
        self.send_ssh(self.stop_cmd, 'STOP')

        self.get_logger().info('Done — shutting down node')
        rclpy.shutdown()

    # -------------------------
    def send_ssh(self, command, label):
        ssh_cmd = [
            'ssh',
            '-o', 'BatchMode=yes',
            '-o', 'StrictHostKeyChecking=no',
            f'{self.ssh_user}@{self.ssh_host}',
            command,
        ]

        try:
            result = subprocess.run(
                ssh_cmd,
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0:
                self.get_logger().info(f'{label} command success')
            else:
                self.get_logger().error(
                    f'{label} failed: {result.stderr.strip()}'
                )

        except Exception as e:
            self.get_logger().error(f'{label} SSH error: {e}')


# -------------------------
def main(args=None):
    rclpy.init(args=args)
    node = SimpleRecordTrigger()
    rclpy.spin(node)


if __name__ == '__main__':
    main()
