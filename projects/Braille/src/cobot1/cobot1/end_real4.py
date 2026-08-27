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
        # 네임스페이스와 노드 이름 설정
        super().__init__('finish_task_server', namespace="dsr01")
        
        # [송근님의 의견 반영!] 명시적인 I/O 포트 파라미터 선언
        self.DO_BLUE = 1
        self.DO_PURPLE = 2
        self.DI_GREEN = 2 # 파지 확인용 초록 핀
        
        self._action_server = ActionServer(
            self,
            BraillePunch,
            'end_task',  # 컨트롤러 시퀀스의 마지막 단계 이름
            self.execute_callback
        )
        self.get_logger().info("🏁 최종 종료 작업(End Task) 서버 가동 중...")

    def execute_callback(self, goal_handle):
        # [수정] 센서 값을 읽기 위해 get_digital_input 추가 임포트
        from DSR_ROBOT2 import (
            posx, movel, wait, set_tool, set_tcp, set_robot_mode,
            ROBOT_MODE_AUTONOMOUS, set_digital_output, movej, posj, amovel,
            get_digital_input
        )

        self.get_logger().info("📥 최종 종료 작업 시퀀스 시작")

        # --- [데이터 설정] ---
        P0 = [474.13, -235.05, 101.31, 26.11, -177.12, 27.74]
        P00 = [474.13, -235.05, 199.31, 26.11, -177.12, 27.74]
        P1 = [468.86, -31.04, 74.00, 86.31, 157.02, 87.29]
        P2 = [468.86, -31.04, 274.00, 86.31, 157.02, 87.29]
        P3 = [507.88, 236.64, 94.44, 32.05, -176.44, 28.94]
        P4 = [507.88, 236.64, 194.44, 32.05, -176.44, 28.94]

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
            self.get_logger().info(f"📊 종료 작업 진행률: {percentage}%")

        try:
            # 1. 초기 준비 및 그리퍼 열기 (15%)
            publish_progress(15)
            set_digital_output(self.DO_BLUE, 0)
            set_digital_output(self.DO_PURPLE, 1)
            wait(0.8)
            
            # 2. 0번 좌표 이동 및 집기
            movel(posx(P0), v=100, a=100)
            
            # --- [핵심 추가] 최종 보관을 위한 파지 및 센서 검증 ---
            self.get_logger().info("▶️ 완성된 종이 최종 파지 시도...")
            set_digital_output(self.DO_BLUE, 1)
            set_digital_output(self.DO_PURPLE, 0)
            wait(1.5) # 물리적 파지 시간 보장
            
            # 명시적으로 선언된 파라미터 사용!
            val_green = get_digital_input(self.DI_GREEN)
            
            if val_green == 0:
                self.get_logger().error("🚨 [예외 발생] 종이를 잃어버렸거나 파지에 실패했습니다!")
                set_digital_output(self.DO_BLUE, 0)
                set_digital_output(self.DO_PURPLE, 1) # 다시 열기
                goal_handle.abort()
                result.success = False
                result.message = "GRIP_FAIL"
                return result
                
            self.get_logger().info("✅ [정상] 종이 파지 성공! 최종 보관함으로 고속 이동합니다.")
            # ----------------------------------------
            publish_progress(35)
            
            # 3. 메인 이동 시퀀스 (60%)
            movel(posx(P00), v=100, a=100)
            movel(posx(P1), v=100, a=100)
            amovel(posx(P2), v=300, a=400)
            publish_progress(60)

            # 4. 고속 이동 및 복귀 (85%)
            amovel(posx(P3), v=300, a=400)
            movel(posx(P4), v=300, a=400)
            wait(2.0)
            movel(posx(P0), v=100, a=100)
            publish_progress(85)
            
            # 5. 그리퍼 열기 (내려놓기) 및 홈 복귀 (100%)
            set_digital_output(self.DO_BLUE, 0)
            set_digital_output(self.DO_PURPLE, 1)
            wait(0.8)
            movej(posj(0, 0, 90, 0, 90, 0), v=100, a=100)
            
            publish_progress(100)

            # 성공 응답 전송
            goal_handle.succeed()
            result.success = True
            result.message = "최종 종료 작업 완료"
            self.get_logger().info("✅ 모든 시퀀스 종료")
            return result

        except Exception as e:
            self.get_logger().error(f"❌ 작업 실패: {e}")
            result.success = False
            result.message = str(e)
            goal_handle.abort() 
            return result

def main(args=None):
    rclpy.init(args=args)
    server = FinishTaskActionServer()

    # 두산 로봇 설정 연결
    DR_init.__dsr__node = server
    DR_init.__dsr__id = "dsr01"
    DR_init.__dsr__model = "m0609"

    # 멀티스레드 실행기 사용
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