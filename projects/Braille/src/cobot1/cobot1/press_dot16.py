import rclpy
import os
from rclpy.node import Node
from rclpy.action import ActionServer
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from ament_index_python.packages import get_package_share_directory

# 두산 로봇 라이브러리 및 초기화 관련
import DR_init

# Firebase 관련
import firebase_admin
from firebase_admin import credentials, db

# 표준 라이브러리 및 유틸리티
import sys
import math
import time
from dataclasses import dataclass
from typing import Optional

# 사용자 정의 액션 인터페이스
from cobot_interface.action import BraillePunch

@dataclass
class PunchConfig:
    TARGET_FORCE = 300
    Z_PUNCH_VEL = 10.0
    SAFE_VEL = 150.0
    SAFE_ACC = 150.0
    Z_HOVER = 40.5
    Z_LIMIT = 37.0
    DOT_PITCH = 3.0
    CHAR_PITCH = 8.0
    MY_BASE = 101
    BASE_POSE = [-9.20 , 4.04, 43.97, 0.1, 179.9, -0.1]
    TOOL_POSE = [525.38, -233.19, 120.75, 40.64, -117.17, 41.86]
    ANGLE_DEG = 19.51
    ANGLE_RAD = math.radians(ANGLE_DEG)
    FORCE_Y = TARGET_FORCE * math.sin(ANGLE_RAD)
    FORCE_Z = TARGET_FORCE * math.cos(ANGLE_RAD)

class BrailleActionServer(Node):
    def __init__(self):
        super().__init__('braille_action_server', namespace="dsr01")
        
        self.group = ReentrantCallbackGroup()
        
        self.offset_x = 0.0
        self.offset_y = 0.0
        self.latest_braille = ""

        try:
            package_share_dir = get_package_share_directory('cobot1')
            json_path = "/home/soli/dotdotdirara-d938d-firebase-adminsdk-fbsvc-36dc312ac7.json"
            DATABASE_URL = "https://dotdotdirara-d938d-default-rtdb.asia-southeast1.firebasedatabase.app" 

            if not firebase_admin._apps:
                cred = credentials.Certificate(json_path)
                firebase_admin.initialize_app(cred, {'databaseURL': DATABASE_URL})
            
            self.pose_ref = db.reference('/brailleBusinessCardsLatest/brailleBox')
            self.pose_ref.listen(self.pose_listener_callback)
            self.braille_ref = db.reference('/brailleBusinessCardsLatest')
            self.braille_ref.listen(self.braille_listener_callback)
            self.get_logger().info("🔥 Firebase 및 리스너 가동 완료")
            
        except Exception as e:
            self.get_logger().error(f"❌ 초기화 오류: {e}")

        self._action_server = ActionServer(
            self,
            BraillePunch,
            'punch_braille',
            execute_callback=self.execute_callback,
            callback_group=self.group
        )

    def pose_listener_callback(self, event):
        data = event.data
        if isinstance(data, dict):
            self.offset_x = float(data.get('xMm', 0.0)) + 5
            self.offset_y = -float(data.get('yMm', 0.0))
            self.get_logger().info(f"📍 좌표 동기화 -> X: {self.offset_x}, Y: {self.offset_y}")

    def braille_listener_callback(self, event):
        data = event.data
        if isinstance(data, dict):
            self.latest_braille = str(data.get('brailleText', ''))
        elif isinstance(data, str):
            self.latest_braille = data
        if self.latest_braille:
            self.get_logger().info(f"⠃ 점자 데이터 동기화: {self.latest_braille}")

    def get_all_punch_targets(self, braille_str, start_x, start_y):
        targets = []
        p = PunchConfig.DOT_PITCH
        error_y = -0.45
        dot_map = {
            1: (0, -p * 2),  4: (p, -p * 2),
            2: (0, -p),      5: (p, -p),
            3: (0, 0),       6: (p, 0)
        }
        for b_idx, b_char in enumerate(braille_str):
            if b_char == "⠀": continue
            offset = ord(b_char) - 0x2800
            active_dots = [i + 1 for i in range(6) if offset & (1 << i)]
            char_x = start_x + (b_idx * PunchConfig.CHAR_PITCH)
            char_y = start_y + (b_idx * error_y)
            points = [(char_x + dot_map[d][0], char_y + dot_map[d][1]) for d in active_dots]
            targets.append({"char": b_char, "points": points})
            start_y += 1.0
        return targets

    def execute_callback(self, goal_handle):
        import DSR_ROBOT2
        DSR_ROBOT2.g_node = self 
        from DSR_ROBOT2 import set_robot_mode, ROBOT_MODE_MANUAL, ROBOT_MODE_AUTONOMOUS, set_tcp, set_tool, wait

        braille_to_punch = goal_handle.request.text if goal_handle.request.text else self.latest_braille
        if not braille_to_punch:
            self.get_logger().error("❌ 타각 데이터 없음")
            goal_handle.abort()
            return BraillePunch.Result(success=False)

        final_start_x = PunchConfig.BASE_POSE[0] + self.offset_x
        final_start_y = PunchConfig.BASE_POSE[1] + self.offset_y
        punch_targets = self.get_all_punch_targets(braille_to_punch, final_start_x, final_start_y)

        set_robot_mode(ROBOT_MODE_MANUAL); wait(0.5)
        set_tcp("GripperDA_v1"); set_tool("Tool Weight")
        set_robot_mode(ROBOT_MODE_AUTONOMOUS); wait(0.5)

        try:
            self.grab_tool()
            for i, item in enumerate(punch_targets):
                if goal_handle.is_cancel_requested:
                    goal_handle.canceled()
                    self.put_tool()
                    return BraillePunch.Result(success=False)
                self.perform_punch_logic(item)
                feedback_msg = BraillePunch.Feedback()
                feedback_msg.progress = ((i + 1) / len(punch_targets)) * 100.0
                goal_handle.publish_feedback(feedback_msg)
            self.put_tool()
            goal_handle.succeed()
            return BraillePunch.Result(success=True)
        except Exception as e:
            self.get_logger().error(f"❌ 타각 오류: {e}")
            goal_handle.abort()
            return BraillePunch.Result(success=False, message=str(e))

    def grab_tool(self):
        from DSR_ROBOT2 import posx, movel, wait, set_digital_output, get_digital_input
        pose_tool_up = posx(61.04, -117.94, 170.16, 90.08, -158.49, 90.00)
        pose_tool_down = posx(61.54, -145.0, 68.44, 90.00, -158.49, 90.00)
        
        movel(pose_tool_up, v=60.0, a=60.0, ref=101)
        movel(pose_tool_down, v=60.0, a=60.0, ref=101)
        
        # --- [핵심 추가] 툴 파지 및 센서 검증 ---
        self.get_logger().info("▶️ 점자 타각용 드라이버 툴 파지 시도...")
        set_digital_output(1, 1); set_digital_output(2, 0); wait(1.5) # 물리적 파지 시간 1.5초 부여
        
        if get_digital_input(2) == 0:
            self.get_logger().error("🚨 [예외 발생] 거치대에 툴이 없거나 파지에 실패했습니다!")
            set_digital_output(1, 0); set_digital_output(2, 1) # 그리퍼 다시 열기
            raise Exception("GRIP_FAIL") # 컨트롤러로 통일된 에러 메시지(GRIP_FAIL) 전송
            
        self.get_logger().info("✅ [정상] 타각용 툴 파지 성공!")
        # ----------------------------------------

        movel(pose_tool_up, v=60.0, a=60.0, ref=101)

    def put_tool(self):
        from DSR_ROBOT2 import posx, movel, wait, set_digital_output
        pose_tool_down = posx(62.41, -136.9, 90.54, 88.36, -160.57, 88.75)
        pose_tool_up = posx(61.04, -117.94, 155.16, 93.71, -159.97, 91.79)
        movel(pose_tool_up, v=60.0, a=60.0, ref=101)
        movel(pose_tool_down, v=60.0, a=60.0, ref=101)
        set_digital_output(1, 0); set_digital_output(2, 1); wait(0.5)
        movel(pose_tool_up, v=60.0, a=60.0, ref=101)

    def perform_punch_logic(self, item):
        from DSR_ROBOT2 import posx, movel, wait, task_compliance_ctrl, release_compliance_ctrl, set_desired_force, release_force, get_tool_force, get_current_posx, DR_TOOL, DR_FC_MOD_REL
        rx, ry, rz = PunchConfig.BASE_POSE[3:]


        # error_y = 0
        # p_hover = posx(tx, ty, PunchConfig.BASE_POSE[2], rx, ry, rz)
        # movel(p_hover, v=PunchConfig.SAFE_VEL, a=PunchConfig.SAFE_ACC, ref=101)
        for tx, ty in item['points']:
            p_hover = posx(tx, ty, PunchConfig.Z_HOVER, rx, ry, rz)
            movel(p_hover, v=PunchConfig.SAFE_VEL, a=PunchConfig.SAFE_ACC, ref=101)
            task_compliance_ctrl(); wait(0.1)
            set_desired_force([0.0, PunchConfig.FORCE_Y, -PunchConfig.FORCE_Z, 0.0, 0.0, 0.0], dir=[0, 1, 1, 0, 0, 0], mod=DR_FC_MOD_REL)
            while rclpy.ok():
                ext_force = get_tool_force(DR_TOOL)
                if abs(ext_force[2]) >= PunchConfig.TARGET_FORCE: break
                cp, _ = get_current_posx(ref=101)
                if cp[2] <= PunchConfig.Z_LIMIT: break
                wait(0.01)
            release_force(); release_compliance_ctrl()
            movel(p_hover, v=PunchConfig.SAFE_VEL, a=PunchConfig.SAFE_ACC, ref=101); wait(0.1)
            # if error_y <=3.5 :
            #     error_y += 0.55

def main(args=None):
    rclpy.init(args=args)
    server_node = BrailleActionServer()
    DR_init.__dsr__node = server_node
    DR_init.__dsr__id = "dsr01"
    DR_init.__dsr__model = "m0609"
    executor = MultiThreadedExecutor()
    executor.add_node(server_node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        server_node.destroy_node()
        rclpy.shutdown()

if __name__ == "__main__":
    main()