from setuptools import find_packages, setup

package_name = 'kufibot_actuators'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    package_data={
        package_name: ['dc_motor_config.json', 'joint_angles.json'],
    },
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='kufi',
    maintainer_email='y.bulbul@hotmail.com',
    description='Safe servo and differential-drive control for Kufibot.',
    license='BSD-2-Clause',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'servo_node = kufibot_actuators.servo_node:main',
            'dc_motor_node = kufibot_actuators.dc_motor_node:main',
        ],
    },
)
