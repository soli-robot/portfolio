#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import rclpy
from rclpy.node import Node
from rclpy.action import ActionServer
from rclpy.callback_groups import ReentrantCallbackGroup
from cobot_interface.action import BraillePunch 
import DR_init
import time
from rclpy.executors import MultiThreadedExecutor

class GrabPaperActionServer(Node):
    def __init__(self):
        super().__init__('grab_paper_server', namespace="dsr01")
        
        self.REF_FRAME = 0 
        self.VEL_L = 250.0
        self.ACC_L = 250.0

        # [센서 및 그리퍼 핀 설정 추가]
        self.DO_BLUE = 1
        self.DO_PURPLE = 2
        self.DI_GREEN = 2

        self._callback_group = ReentrantCallbackGroup()

        self._action_server = ActionServer(
            self,
            BraillePunch,
            'grab_paper',
            execute_callback=self.execute_callback,
            callback_group=self._callback_group
        )
        
        self.get_logger().info("📦 [STEP 1] 종이 집기 액션 서버 가동 시작")

    def execute_callback(self, goal_handle):
        # 전역 공간(main)에서 이미 DSR_ROBOT2가 완벽하게 초기화되었으므로
        # 여기서는 편하게 함수들만 불러와서 쓰면 됩니다!
        from DSR_ROBOT2 import (
            posx, movel, wait, set_tool, set_tcp, movej, posj,
            set_robot_mode, set_digital_output, get_digital_input, change_operation_speed,
            ROBOT_MODE_MANUAL, ROBOT_MODE_AUTONOMOUS
        )

        self.get_logger().info("🚀 [STEP 1] 시퀀스 실행 시작...")
        result = BraillePunch.Result()

        try:
            # --- [A] 로봇 초기화 (10%) ---
            self.publish_progress(goal_handle, 10.0)
            set_robot_mode(ROBOT_MODE_MANUAL)
            set_tool("Tool Weight")
            set_tcp("GripperDA_v1")
            change_operation_speed(70)
            set_robot_mode(ROBOT_MODE_AUTONOMOUS)
            wait(1.0)

            # --- [B] 좌표 매핑 (송근님의 영혼이 담긴 좌표, 절대 보존!) ---
            p1 = posx([474.160, -234.200, 98.440, 156.41, 179.11, 155.92])
            p2 = posx([474.160, -234.200, 282.700, 156.41, 179.11, 155.92])
            p3_h = posx([360.310, 9.750, 49.080 + 150.0, 175.62, 177.62, 175.10]) 
            p3_s = posx([360.310, 9.750, 49.080, 175.62, 177.62, 175.10])
            p4 = posx([525.250, -41.250, 85.810, 170.36, 177.35, 170.85])
            p5 = posx([523.660, -48.740, 93.060, 170.36, 176.40, 171.16])
            p6 = posx([526.440, -33.470, 83.190, 74.30, -164.67, 73.28])
            p7 = posx([521.17, -70.79, 83.63, 15.86, -176.43, 18.93])
            p8 = posx([521.17, -70.79, 120.63, 15.86, -176.43, 18.93])
            p9 = posx([474.160, -234.200, 97.840, 156.41, 179.11, 155.92])
            p10 = posx([474.160, -234.200, 282.700, 156.41, 179.11, 155.92])

            # --- [C] 이동 시퀀스 ---
            # 그리퍼 초기화 (열기)
            # home = posj(0,0,90,0,90,0)
            # movej(home , v=self.VEL_L, a=self.ACC_L, ref=self.REF_FRAME)
            set_digital_output(self.DO_BLUE, 0); set_digital_output(self.DO_PURPLE, 1); wait(0.5)
            movel(p1, v=self.VEL_L, a=self.ACC_L, ref=self.REF_FRAME)
            
            # --- [핵심 추가] 그리퍼 닫기 및 파지 센서 검증 ---
            self.get_logger().info("▶️ 종이 파지 시도...")
            set_digital_output(self.DO_BLUE, 1); set_digital_output(self.DO_PURPLE, 0)
            wait(1.5) # 물리적으로 종이를 잡을 시간 부여
            
            val_green = get_digital_input(self.DI_GREEN)
            
            if val_green == 1:
                self.get_logger().info("✅ [정상] 초록색 핀(2번) 신호 감지: 종이 파지 성공!")
            else:
                self.get_logger().error("🚨 [예외 발생] 종이를 놓쳤거나 허공입니다. 작업을 중단합니다.")
                set_digital_output(self.DO_BLUE, 0); set_digital_output(self.DO_PURPLE, 1) # 다시 열기
                goal_handle.abort()
                result.success = False
                result.message = "GRIP_FAIL"
                return result
            # ----------------------------------------------------

            movel(p2, v=self.VEL_L, a=self.ACC_L, ref=self.REF_FRAME)
            self.publish_progress(goal_handle, 25.0)

            movel(p3_h, v=self.VEL_L, a=self.ACC_L, ref=self.REF_FRAME)
            movel(p3_s, v=self.VEL_L, a=self.ACC_L, ref=self.REF_FRAME)
            movel(p3_h, v=100.0, a=100.0, ref=self.REF_FRAME)
            self.publish_progress(goal_handle, 45.0)

            movel(p4, v=150.0, a=150.0, ref=self.REF_FRAME)
            movel(p5, v=50.0, a=50.0, ref=self.REF_FRAME)
            movel(p6, v=50.0, a=50.0, ref=self.REF_FRAME)
            self.publish_progress(goal_handle, 65.0)

            movel(p7, v=self.VEL_L, a=self.ACC_L, ref=self.REF_FRAME)
            movel(p8, v=self.VEL_L, a=self.ACC_L, ref=self.REF_FRAME)
            movel(p9, v=self.VEL_L, a=self.ACC_L, ref=self.REF_FRAME)
            self.publish_progress(goal_handle, 85.0)
            
            # 마지막 위치에서 종이 놓기 전 다시 초기화 (열기)
            set_digital_output(self.DO_BLUE, 0); set_digital_output(self.DO_PURPLE, 1); wait(0.5)
            movel(p10, v=self.VEL_L, a=self.ACC_L, ref=self.REF_FRAME)
            
            # --- [D] 성공 보고 (100%) ---
            self.publish_progress(goal_handle, 100.0)
            self.get_logger().info("✅ [STEP 1] 모든 동작 완료. 결과를 반환합니다.")
            
            goal_handle.succeed() 
            result.success = True
            result.message = "종이 집기 시퀀스 완료"
            return result

        except Exception as e:
            self.get_logger().error(f"❌ 시퀀스 오류 발생: {e}")
            if goal_handle.is_active:
                goal_handle.abort()
            result.success = False
            result.message = str(e)
            return result

    def publish_progress(self, goal_handle, val):
        feedback = BraillePunch.Feedback()
        feedback.progress = float(val)
        goal_handle.publish_feedback(feedback)
        self.get_logger().info(f"📊 진행도: {val}%")

def main(args=None):
    rclpy.init(args=args)
    server = GrabPaperActionServer()

    # [핵심 변경 사항] 여기서 노드를 확실히 꽂아주고 첫 임포트를 실행합니다!
    DR_init.__dsr__node = server
    DR_init.__dsr__id = "dsr01"
    DR_init.__dsr__model = "m0609"
    
    # 파일럿(DSR_ROBOT2) 탑승 완료! 여기서 안전하게 초기화됩니다.
    import DSR_ROBOT2 

    executor = MultiThreadedExecutor(num_threads=8)
    executor.add_node(server)

    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        server.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()