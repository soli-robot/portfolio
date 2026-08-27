from setuptools import find_packages, setup
import os
from glob import glob

package_name = 'cobot1'

setup(
    name=package_name,
    version='0.0.0',
    # packages=find_packages(exclude=['test']), 
    # 아래와 같이 패키지 이름을 명시적으로 포함하는 것이 더 안전합니다.
    packages=['cobot1'],
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), glob(os.path.join('launch', '*launch.[pxy][yma]*'))),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='soli',
    maintainer_email='abcdsjj7378@gamil.com',
    description='ROS 2 Braille Robot Package',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            # 기존 스크립트들 유지
            'final_controller = cobot1.controller17:main',
            'final_press_dot = cobot1.press_dot16:main',
            'final_grab = cobot1.grab_paper7:main',
            'final_finish = cobot1.finish5:main',
            'final_end = cobot1.end_real4:main',
            'final_stamp = cobot1.robot_stamp4:main',
            'emergency = cobot1.exeption3:main',
        ],
    },
)