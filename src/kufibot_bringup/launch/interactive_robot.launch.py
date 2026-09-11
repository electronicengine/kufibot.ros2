#!/usr/bin/env python3
"""Launch the complete interactive Kufibot stack."""

from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    default_config = str(Path(get_package_share_directory(
        'kufibot_bringup')) / 'config' / 'interactive_robot.yaml')
    config = LaunchConfiguration('config')
    return LaunchDescription([
        DeclareLaunchArgument('config', default_value=default_config),
        # The normal launcher is a remote-control boot: the robot is safe to
        # drive as soon as the mobile or web client connects.  Passing either
        # argument explicitly still supports hardware-free diagnostics.
        DeclareLaunchArgument('remote', default_value='true'),
        DeclareLaunchArgument('motors', default_value='true'),
        Node(package='kufibot_navigation', executable='navigation_node',
             name='navigation_node', parameters=[config], output='screen'),
        Node(package='kufibot_remote', executable='remote_controller',
             name='remote_controller', parameters=[config], output='screen',
             condition=IfCondition(LaunchConfiguration('remote'))),
        Node(package='kufibot_actuators', executable='dc_motor_node',
             name='dc_motor_node', parameters=[config], output='screen',
             condition=IfCondition(LaunchConfiguration('motors'))),
        Node(package='kufibot_sensors', executable='ina219_node',
             name='ina219_node', output='screen'),
        Node(package='kufibot_sensors', executable='hmc5883l_node',
             name='hmc5883l_node', output='screen'),
        Node(package='kufibot_sensors', executable='tfluna_node',
             name='tfluna_node', output='screen'),
        Node(package='kufibot_actuators', executable='servo_node',
             name='servo_node', parameters=[config], output='screen'),
        Node(package='kufibot_interaction', executable='servo_arbiter',
             name='servo_arbiter', parameters=[config], output='screen'),
        Node(package='kufibot_perception', executable='usb_camera_node',
             name='usb_camera_node', parameters=[config], output='screen'),
        Node(package='kufibot_perception', executable='mediapipe_node',
             name='mediapipe_node', parameters=[config], output='screen'),
        Node(package='kufibot_perception', executable='obstacle_detector_node',
             name='obstacle_detector_node', parameters=[config], output='screen'),
        Node(package='kufibot_interaction', executable='voice_agent_node',
             name='voice_agent_node', parameters=[config], output='screen'),
    ])
