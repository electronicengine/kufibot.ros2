from setuptools import find_packages, setup

setup(
    name='kufibot_perception', version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
         ['resource/kufibot_perception']),
        ('share/kufibot_perception', ['package.xml']),
    ],
    install_requires=['setuptools'], zip_safe=True,
    maintainer='kufi', maintainer_email='y.bulbul@hotmail.com',
    description='USB camera and MediaPipe perception for Kufibot.',
    license='BSD-2-Clause',
    entry_points={'console_scripts': [
        'usb_camera_node = kufibot_perception.camera_node:main',
        'mediapipe_node = kufibot_perception.mediapipe_node:main',
    ]},
)
