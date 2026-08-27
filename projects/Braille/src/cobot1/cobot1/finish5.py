import rclpy
import DR_init
import time
from rclpy.action import ActionServer
from rclpy.node import Node
from rclpy.executors import MultiThreadedExecutor

# 액션 인터페이스
from cobot_interface.action import BraillePunch 

class FinishTaskActionServer(Node):
    def __init__(self):
        super().__init__('finish_task_server', namespace="dsr01")
        
        # 액션 서버 설정
        self._action_server = ActionServer(
            self,
            BraillePunch,
            'finish_task',
            self.execute_callback
        )
        self.get_logger().info("🏁 마무리 작업(Finish Task) 서버 가동 중...")

    def execute_callback(self, goal_handle):
        self.get_logger().info("🧾 마무리 작업 요청 수신")
        
        # [추가] 센서 값을 읽기 위해 get_digital_input 임포트
        from DSR_ROBOT2 import (
            posx, movel, wait, set_tool, set_tcp, set_robot_mode,
            ROBOT_MODE_AUTONOMOUS, set_digital_output, get_digital_input
        )

        # --- [데이터 설정] ---
        P0 = [474.13, -235.05, 101.31, 26.11, -177.12, 27.74]
        P00 = [474.13, -235.05, 199.31, 26.11, -177.12, 27.74]
        P1 = [528.86, -31.04, 74.00, 86.31, 157.02, 87.29]
        P2 = [692.31, -2.01, 287.97, 3.46, 121.62, 1.72]
        P3 = [678.25, 5.2, 220.43, 2.76, 121.10, 1.71]
        P4 = [553.95, -14.29, 244.06, 1.26, 115.95, -0.67]

        # 도구 및 모드 설정
        set_tool("Tool Weight")
        set_tcp("GripperDA_v1")
        set_robot_mode(ROBOT_MODE_AUTONOMOUS)

        feedback_msg = BraillePunch.Feedback()
        result = BraillePunch.Result()

        def publish_progress(percentage):
            """피드백 전송 헬퍼 함수"""
            feedback_msg.progress = float(percentage)
            goal_handle.publish_feedback(feedback_msg)
            # self.get_logger().info(f"📊 진행률: {percentage}%")

        try:
            # 1. 그리퍼 초기화 (10%)
            publish_progress(10)
            set_digital_output(1, 0); set_digital_output(2, 1); wait(0.8)
            
            # 2. 0번 좌표 이동 및 집기 (20%)
            publish_progress(20)
            movel(posx(P0), v=100, a=100)
            
            # --- [핵심 추가] 종이 파지 및 센서 검증 ---
            self.get_logger().info("▶️ 타각 완료된 종이 파지 시도...")
            set_digital_output(1, 1); set_digital_output(2, 0)
            wait(1.5) # 센서가 인식할 수 있도록 물리적 파지 시간을 넉넉히(1.5초) 부여
            
            val_green = get_digital_input(2)
            
            if val_green == 0:
                self.get_logger().error("🚨 [예외 발생] 종이를 잃어버렸거나 파지에 실패했습니다!")
                set_digital_output(1, 0); set_digital_output(2, 1) # 그리퍼 다시 열기
                goal_handle.abort()
                result.success = False
                result.message = "GRIP_FAIL"
                return result
                
            self.get_logger().info("✅ [정상] 타각된 종이 파지 성공! 뒤집기 이송을 시작합니다.")
            # ----------------------------------------
            
            # 3. 안전 고도 이동 (30%)
            movel(posx(P00), v=30, a=30)
            publish_progress(30)

            # 4. 순차 좌표 이동 (P1 -> P4 -> P2 -> P3 -> P4)
            points = [("P1", P1), ("P4-1", P4), ("P2", P2), ("P3", P3), ("P4-2", P4)]
            for i, (name, pt) in enumerate(points):
                self.get_logger().info(f"이동 중: {name}")
                movel(posx(pt), v=60, a=60)
                
                # 구간 피드백 계산 (30% ~ 90% 구간 할당)
                current_progress = 30.0 + (float(i+1) / len(points)) * 60.0
                publish_progress(current_progress)

            # 5. 최종 내려놓기 (100%)
            movel(posx(P0), v=100, a=100)
            set_digital_output(1, 0); set_digital_output(2, 1); wait(0.8)
            
            publish_progress(100)
            goal_handle.succeed()
            result.success = True
            result.message = "마무리 좌표 이동 완료"
            return result

        except Exception as e:
            self.get_logger().error(f"작업 실패: {e}")
            result.success = False
            result.message = str(e) # 예외 발생 시 에러 메시지 리턴
            return result

def main(args=None):
    rclpy.init(args=args)
    server = FinishTaskActionServer()

    # 두산 로봇 라이브러리 노드 연결
    DR_init.__dsr__node = server
    DR_init.__dsr__id = "dsr01"
    DR_init.__dsr__model = "m0609"

    # MultiThreadedExecutor 사용 (스탬프 서버와 동일한 구조)
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