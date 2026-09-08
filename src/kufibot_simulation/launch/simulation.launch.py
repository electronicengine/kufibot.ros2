#!/usr/bin/env python3
"""Launch the navigation stack against the virtual home instead of hardware."""

from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue

from kufibot_simulation.world_node import DEFAULT_FLOORPLAN


def generate_launch_description():
    share = Path(get_package_share_directory('kufibot_simulation'))
    default_config = str(share / 'config' / 'simulation.yaml')
    config = LaunchConfiguration('config')
    floorplan = LaunchConfiguration('floorplan')
    wheel_geometry = {'wheel_separation_m': ParameterValue(
        LaunchConfiguration('wheel_separation_m'), value_type=float)}
    return LaunchDescription([
        DeclareLaunchArgument('config', default_value=default_config),
        DeclareLaunchArgument('floorplan', default_value=DEFAULT_FLOORPLAN),
        DeclareLaunchArgument('wheel_separation_m', default_value='0.2'),
        Node(package='kufibot_navigation', executable='navigation_node',
             name='navigation_node', parameters=[config], output='screen'),
        Node(package='kufibot_remote', executable='remote_controller',
             name='remote_controller', parameters=[config], output='screen'),
        Node(package='kufibot_interaction', executable='servo_arbiter',
             name='servo_arbiter', parameters=[config], output='screen'),
        Node(package='kufibot_interaction', executable='voice_agent_node',
             name='voice_agent_node', parameters=[config], output='screen'),
        Node(package='kufibot_simulation', executable='sim_servo_node',
             name='servo_node', parameters=[config], output='screen'),
        Node(package='kufibot_simulation', executable='sim_dc_motor_node',
             name='dc_motor_node', parameters=[config, wheel_geometry], output='screen'),
        Node(package='kufibot_simulation', executable='world_node',
             name='world_node', parameters=[config, wheel_geometry, {'floorplan_file': floorplan}],
             output='screen'),
    ])
