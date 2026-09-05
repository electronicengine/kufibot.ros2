from glob import glob
from setuptools import setup

package_name = 'kufibot_bringup'

setup(
    name=package_name,
    version='0.1.0',
    packages=[],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', glob('launch/*.launch.py')),
        ('share/' + package_name + '/config', glob('config/*.yaml')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='kufi',
    maintainer_email='y.bulbul@hotmail.com',
    description='System launch files and deployment configuration for Kufibot.',
    license='BSD-2-Clause',
)
