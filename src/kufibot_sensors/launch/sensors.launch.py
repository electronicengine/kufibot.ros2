#!/usr/bin/env python3
"""Launch all Kufibot sensor nodes."""

from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    """Start the battery monitor, compass and distance sensor nodes."""
    return LaunchDescription([
        Node(
            package='kufibot_sensors',
            executable='ina219_node',
            name='ina219_node',
            output='screen',
        ),
        Node(
            package='kufibot_sensors',
            executable='hmc5883l_node',
            name='hmc5883l_node',
            output='screen',
        ),
        Node(
            package='kufibot_sensors',
            executable='tfluna_node',
            name='tfluna_node',
            output='screen',
        ),
    ])
