from setuptools import find_packages, setup

setup(
    name='kufibot_remote', version='0.1.0', packages=find_packages(),
    data_files=[('share/ament_index/resource_index/packages', ['resource/kufibot_remote']),
                ('share/kufibot_remote', ['package.xml', 'README.md'])],
    package_data={'kufibot_remote': ['web/*.html', 'web/*.css', 'web/*.js', 'web/*LICENSE.txt']},
    install_requires=['setuptools', 'aiohttp>=3.9,<4', 'aiortc==1.9.0'], zip_safe=False,
    maintainer='kufi', maintainer_email='y.bulbul@hotmail.com',
    description='LAN mobile controller bridge', license='BSD-2-Clause',
    entry_points={'console_scripts': ['remote_controller = kufibot_remote.node:main']},
)
