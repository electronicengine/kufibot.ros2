from glob import glob
import os

from setuptools import find_packages, setup

package_name = 'kufibot_sensors'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'),
            glob('launch/*.launch.py')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='kufi',
    maintainer_email='y.bulbul@hotmail.com',
    description='Battery, compass, and range sensor nodes for Kufibot.',
    license='BSD-2-Clause',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'ina219_node = kufibot_sensors.ina219_node:main',
            'hmc5883l_node = kufibot_sensors.hmc5883l_node:main',
            'tfluna_node = kufibot_sensors.tfluna_node:main',
        ],
    },
)
