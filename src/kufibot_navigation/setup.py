from setuptools import find_packages, setup

setup(name='kufibot_navigation', version='0.1.0', packages=find_packages(),
      data_files=[('share/ament_index/resource_index/packages', ['resource/kufibot_navigation']),
                  ('share/kufibot_navigation', ['package.xml'])],
      install_requires=['setuptools'], zip_safe=True,
      maintainer='kufi', maintainer_email='y.bulbul@hotmail.com', license='BSD-2-Clause',
      description='Bounded sensor-driven navigation and drive arbitration.',
      entry_points={'console_scripts': ['navigation_node = kufibot_navigation.node:main']})
