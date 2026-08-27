import os
from launch import LaunchDescription
from launch_ros.actions import Node

def generate_launch_description():
    # 사용 중인 ROS 2 패키지 이름으로 반드시 변경해 주세요!
    # 예: 'cobot_interface', 'doosan_robot', 등
    package_name = 'cobot1' 

    return LaunchDescription([
                
        # 2. Grab Paper 노드
        Node(
            package=package_name,
            executable='final_grab',
            name='grab_paper_node',
            output='screen'
        ),

        # 3. Press Dot 노드
        Node(
            package=package_name,
            executable='final_press_dot',
            name='press_dot_node',
            output='screen'
        ),

        # 4. Finish 노드
        Node(
            package=package_name,
            executable='final_finish',
            name='finish_node',
            output='screen'
        ),

        Node(
            package='cobot1',
            executable='final_stamp',
            name='stamp_node',
            output='screen'
        ),

        Node(
            package='cobot1',
            executable='final_end',
            name='end_node',
            output='screen'
        ),
        Node(
            package='cobot1',
            executable='emergency',
            name='emergency',
            output='screen'
        ),

        Node(
            package=package_name,
            executable='final_controller',  # setup.py에 등록된 실행 파일 이름
            name='controller_node',
            output='screen'
        ),
    ])