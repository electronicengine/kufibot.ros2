from setuptools import find_packages, setup

setup(
    name='kufibot_interaction', version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
         ['resource/kufibot_interaction']),
        ('share/kufibot_interaction', ['package.xml']),
    ],
    package_data={'kufibot_interaction': ['expression_defaults/*.json', 'model/*.json', 'model/*.glb']},
    install_requires=['setuptools'], zip_safe=True,
    maintainer='kufi', maintainer_email='y.bulbul@hotmail.com',
    description='Verasist voice agent and servo arbitration for Kufibot.',
    license='BSD-2-Clause',
    entry_points={'console_scripts': [
        'voice_agent_node = kufibot_interaction.voice_agent_node:main',
        'servo_arbiter = kufibot_interaction.servo_arbiter:main',
        'expression_test_node = kufibot_interaction.expression_test_node:main',
    ]},
)
