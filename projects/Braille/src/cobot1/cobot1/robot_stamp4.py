import rclpy
import DR_init
import time
from rclpy.action import ActionServer
from rclpy.node import Node
from dataclasses import dataclass
from rclpy.executors import MultiThreadedExecutor

# 액션 인터페이스
from cobot_interface.action import BraillePunch 

@dataclass
class RobotConfig:
    # 기존 설정 유지
    base_x: float = 0.0
    base_y: float = 0.0
    base_rx: float = 0.0
    base_ry: float = 0.0
    base_rz: float = 0.0
    z_hover: float = 350.0
    z_draw: float = 320.0
    vel_move: int = 100
    vel_draw: int = 40
    acc_move: int = 60
    acc_draw: int = 40

class StampActionServer(Node):
    def __init__(self):
        super().__init__('stamp_action_server', namespace='dsr01')
        
        # 액션 서버 설정
        self._action_server = ActionServer(
            self,
            BraillePunch,
            'stamp',
            self.execute_callback
        )
        self.get_logger().info("🚀 스탬프 액션 서버가 시작되었습니다.")

    async def execute_callback(self, goal_handle):
        self.get_logger().info("🧾 스탬프 작업 요청 수신")
        
        # [수정] goal_handle을 전달하여 함수 내부에서 피드백을 보낼 수 있게 함
        self.execute_stamp(goal_handle)

        goal_handle.succeed()
        result = BraillePunch.Result()
        result.success = True
        return result

    def execute_stamp(self, goal_handle):
        from DSR_ROBOT2 import (
            posx, movel, wait, set_tool, set_tcp, set_robot_mode,
            ROBOT_MODE_AUTONOMOUS, set_digital_output, movej, posj
        )

        # 피드백 메시지 객체 생성
        feedback_msg = BraillePunch.Feedback()

        def publish_progress(percentage):
            """피드백 전송 보조 함수"""
            feedback_msg.progress = float(percentage)
            goal_handle.publish_feedback(feedback_msg)
            self.get_logger().info(f"📊 작업 진행률: {percentage}%")

        set_tool("Tool Weight")
        set_tcp("GripperDA_v1")
        set_robot_mode(ROBOT_MODE_AUTONOMOUS)
        
        # [좌표 데이터]
        STAMP_HOME = posx(-190.36, 84.61, -102, 90, -160.66, 90)
        STAMP_PAD  = posx(-128.8, 83.84, -92.94, 90, -160.64, 90)
        CARD_HOLDER = posx(221.36, 51.49, 121.78, 141.37, -163.6, 141.1)
        CARD_HOLDER_UP = posx(221.97, 115.45, 230.84, 162.47, -133.42, 156.15)
        CARD_PLACE_UP = posx(65.14, -0.75, 35.73, 103.57, -168.58, 101.35)
        CARD_PLACE = posx(11.22, -23.3, -18.57, 141.09, -148.13, 144.37)
        CARD_POS = posx(14.22, -39.38, -25.81, 142.88, -143.17, 145.32)
        CARD_POSE_UP = posx(2.35, -21.5, -21.07, 105.07, -174.04, 108.79)

        PRESS_Z = -2
        PRESS_TIME = 0.3
        
        # [동작 시퀀스 및 피드백 전송]
        
        # 1. 초기 위치 및 그리퍼 준비 (10%)
        publish_progress(10)
        movej(posj(0, 0, 90, 0, 90, 0), vel=60, acc=60)
        set_digital_output(1, 0)
        set_digital_output(2, 1)

        # 2. 명함 집기 (30%)
        publish_progress(30)
        movel(CARD_HOLDER, vel=100, acc=60, ref=101)
        wait(0.3)
        set_digital_output(1, 1)
        set_digital_output(2, 0)
        wait(0.3)
        movel(CARD_HOLDER_UP, vel=100, acc=60, ref=101)

        # 3. 명함 배치 (50%)
        publish_progress(50)
        movel(CARD_PLACE_UP, vel=100, acc=60, ref=101)
        wait(0.3)
        movel(CARD_PLACE, vel=50, acc=40, ref=101)
        wait(0.3)
        movel(CARD_POS, vel=50, acc=40, ref=101)
        wait(0.3)
        set_digital_output(1, 0)
        set_digital_output(2, 1)

        # 4. 스탬프 찍기 준비 (70%)
        publish_progress(70)
        temp_card_place = posx(CARD_PLACE[0]-10, CARD_PLACE[1], CARD_PLACE[2] + 80, *CARD_PLACE[3:])
        movel(temp_card_place, vel=50, acc=40, ref=101)
        movel(STAMP_HOME, vel=100, acc=60, ref=101)
        wait(0.5)
        set_digital_output(1, 1)
        set_digital_output(2, 0)
        wait(0.5)

        # 인크 패드 누르기
        lift = posx(STAMP_HOME[0], STAMP_HOME[1], STAMP_HOME[2] + 80, *STAMP_HOME[3:])
        movel(lift, vel=100, acc=60, ref=101)
        movel(STAMP_PAD, vel=100, acc=60, ref=101)
        press = posx(STAMP_PAD[0], STAMP_PAD[1], STAMP_PAD[2] + PRESS_Z, *STAMP_PAD[3:])
        movel(press, vel=30, acc=30, ref=101)
        wait(PRESS_TIME)

        # 5. 실제 스탬핑 동작 (90%)
        publish_progress(90)
        movel(STAMP_PAD, vel=100, acc=60, ref=101)
        pad_up = posx(STAMP_PAD[0], STAMP_PAD[1], STAMP_PAD[2] + 100, *STAMP_PAD[3:])
        movel(pad_up, vel=100, acc=60, ref=101)

        card_up = posx(CARD_POSE_UP[0], CARD_POSE_UP[1], CARD_POSE_UP[2] + 100, *CARD_POSE_UP[3:])
        movel(card_up, vel=100, acc=60, ref=101)
        movel(CARD_POSE_UP, vel=100, acc=60, ref=101)

        press2 = posx(CARD_POSE_UP[0], CARD_POSE_UP[1], CARD_POSE_UP[2] + PRESS_Z, *CARD_POSE_UP[3:])
        movel(press2, vel=30, acc=30, ref=101)
        wait(PRESS_TIME)

        # 6. 복귀 및 완료 (100%)
        movel(CARD_POSE_UP, vel=100, acc=60, ref=101)
        movel(card_up, vel=100, acc=60, ref=101)
        movel(lift, vel=100, acc=60, ref=101)
        movel(STAMP_HOME, vel=100, acc=60, ref=101)

        set_digital_output(1, 0)
        set_digital_output(2, 1)
        wait(0.3)
        movej(posj(0, 0, 90, 0, 90, 0), vel=60, acc=60)
        
        publish_progress(100)
        self.get_logger().info("🏁 스탬프 작업 완료")

def main(args=None):
    rclpy.init(args=args)
    server = StampActionServer()

    DR_init.__dsr__node = server
    DR_init.__dsr__id = "dsr01"
    DR_init.__dsr__model = "m0609"

    executor = MultiThreadedExecutor()
    executor.add_node(server)

    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        server.destroy_node()
        rclpy.shutdown()

if __name__ == "__main__":
    main()