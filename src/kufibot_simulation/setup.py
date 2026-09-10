from glob import glob
from setuptools import find_packages, setup

package_name = 'kufibot_simulation'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    package_data={
        package_name: ['floorplans/*.json'],
    },
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', ['launch/simulation.launch.py']),
        ('share/' + package_name + '/config', ['config/simulation.yaml']),
        ('share/' + package_name + '/config/expressions', glob('config/expressions/*.json')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='kufi',
    maintainer_email='y.bulbul@hotmail.com',
    description='Virtual home, physics and sensor substitutes for hardware-free navigation testing.',
    license='BSD-2-Clause',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'world_node = kufibot_simulation.world_node:main',
            'sim_dc_motor_node = kufibot_simulation.sim_dc_motor_node:main',
            'sim_servo_node = kufibot_simulation.sim_servo_node:main',
            'sim_viewer = kufibot_simulation.viewer:main',
            'sim_plan_editor = kufibot_simulation.plan_editor:main',
        ],
    },
)
