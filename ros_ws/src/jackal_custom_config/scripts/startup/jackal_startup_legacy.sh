#!/bin/bash
echo "Starting Jackal Manual Recovery..."

# 1. Fix the network bridge
sudo ip link set br0 up
sudo ip addr add 192.168.131.1/24 dev br0 2>/dev/null
echo "Network bridge br0 is UP."

# 2. Source the ROS environments
source /opt/ros/humble/setup.bash
source ~/jackal_clearpath_ws/ros_ws/install/setup.bash

# 3. Kill any zombie processes from previous failed runs
sudo pkill -9 sick_generic_caller
sudo pkill -9 robot_state_publisher

echo "System ready. Launching Body and Lidar..."
# 4. Launch the custom config
ros2 launch jackal_custom_config sick_tim_custom.launch.py
