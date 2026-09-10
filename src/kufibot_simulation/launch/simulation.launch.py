#!/usr/bin/env python3
"""Launch the navigation stack against the virtual home instead of hardware."""

import os
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue

from kufibot_simulation.world_node import DEFAULT_FLOORPLAN


def generate_launch_description():
    share = Path(get_package_share_directory('kufibot_simulation'))
    default_config = str(share / 'config' / 'simulation.yaml')
    config = LaunchConfiguration('config')
    floorplan = LaunchConfiguration('floorplan')
    remote = LaunchConfiguration('remote')
    remote_port = LaunchConfiguration('remote_port')
    voice = LaunchConfiguration('voice')
    wheel_geometry = {'wheel_separation_m': ParameterValue(
        LaunchConfiguration('wheel_separation_m'), value_type=float)}
    return LaunchDescription([
        DeclareLaunchArgument('config', default_value=default_config),
        DeclareLaunchArgument('floorplan', default_value=DEFAULT_FLOORPLAN),
        DeclareLaunchArgument('wheel_separation_m', default_value='0.2'),
        DeclareLaunchArgument(
            'remote', default_value='true',
            description='Start the optional web/mobile controller (requires aiortc).'),
        DeclareLaunchArgument(
            'remote_port', default_value='8080',
            description='HTTP/WebSocket port for the simulation controller.'),
        DeclareLaunchArgument(
            'voice', default_value='true',
            description='Start the voice agent; AI mode opens the microphone and session.'),
        DeclareLaunchArgument(
            'audio_device',
            default_value='pulse:default' if os.environ.get('PULSE_SERVER') else 'default',
            description='Audio device: pulse:default for WSLg, or an ALSA device.'),
        Node(package='kufibot_navigation', executable='navigation_node',
             name='navigation_node', parameters=[config], output='screen'),
        Node(package='kufibot_remote', executable='remote_controller',
             name='remote_controller', parameters=[config, {'port': ParameterValue(
                 remote_port, value_type=int),
                 'gesture_config_file': str(share / 'config/expressions/gesture_config.json'),
                 'motion_config_file': str(share / 'config/expressions/motion_definitions.json'),
                 'joint_angles_file': str(share / 'config/expressions/joint_angles.json')}], output='screen',
             condition=IfCondition(remote)),
        Node(package='kufibot_interaction', executable='servo_arbiter',
             name='servo_arbiter', parameters=[config], output='screen'),
        Node(package='kufibot_interaction', executable='voice_agent_node',
             name='voice_agent_node', parameters=[config, {
                 'gesture_config_file': str(share / 'config/expressions/gesture_config.json'),
                 'motion_config_file': str(share / 'config/expressions/motion_definitions.json'),
                 'joint_angles_file': str(share / 'config/expressions/joint_angles.json'),
                 'mic_device': LaunchConfiguration('audio_device'),
                 'speaker_device': LaunchConfiguration('audio_device')}], output='screen',
             condition=IfCondition(voice)),
        Node(package='kufibot_simulation', executable='sim_servo_node',
             name='servo_node', parameters=[config], output='screen'),
        Node(package='kufibot_simulation', executable='sim_dc_motor_node',
             name='dc_motor_node', parameters=[config, wheel_geometry], output='screen'),
        Node(package='kufibot_simulation', executable='world_node',
             name='world_node', parameters=[config, wheel_geometry, {'floorplan_file': floorplan}],
             output='screen'),
    ])
