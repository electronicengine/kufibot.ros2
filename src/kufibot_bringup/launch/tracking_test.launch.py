#!/usr/bin/env python3
"""Launch the complete camera-to-servo tracking test pipeline."""

from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, LogInfo
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    """Start camera, MediaPipe, arbitration, and optional servo hardware."""
    default_config = str(Path(get_package_share_directory(
        'kufibot_bringup')) / 'config' / 'interactive_robot.yaml')
    config = LaunchConfiguration('config')
    camera_device = LaunchConfiguration('camera_device')
    enable_servo = LaunchConfiguration('enable_servo')
    debug_image = LaunchConfiguration('debug_image')

    return LaunchDescription([
        DeclareLaunchArgument('config', default_value=default_config),
        DeclareLaunchArgument(
            'camera_device', default_value='auto',
            description='V4L2 path or auto for hot-plug discovery'),
        DeclareLaunchArgument(
            'enable_servo', default_value='true',
            description='Start PCA9685 servo hardware output'),
        DeclareLaunchArgument(
            'debug_image', default_value='false',
            description='Publish /perception/debug_image'),
        LogInfo(msg=[
            'Starting tracking pipeline; servo hardware enabled=',
            enable_servo,
        ]),
        Node(
            package='kufibot_perception',
            executable='usb_camera_node',
            name='usb_camera_node',
            parameters=[config, {'device': camera_device}],
            output='screen',
        ),
        Node(
            package='kufibot_perception',
            executable='mediapipe_node',
            name='mediapipe_node',
            parameters=[config, {'publish_debug_image': debug_image}],
            output='screen',
        ),
        Node(
            package='kufibot_interaction',
            executable='servo_arbiter',
            name='servo_arbiter',
            parameters=[config, {'default_control_mode': 'ai'}],
            output='screen',
        ),
        Node(
            package='kufibot_actuators',
            executable='servo_node',
            name='servo_node',
            parameters=[config],
            condition=IfCondition(enable_servo),
            output='screen',
        ),
    ])
