import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from rclpy.executors import MultiThreadedExecutor
from rclpy.callback_groups import ReentrantCallbackGroup
import firebase_admin
from firebase_admin import credentials, db
import os
import threading
import time
import subprocess
from ament_index_python.packages import get_package_share_directory
from cobot_interface.action import BraillePunch
from dsr_msgs2.srv import SetRobotControl, GetRobotState  
import DR_init
import signal
import subprocess

class PureController(Node):
    def __init__(self):
        super().__init__('braille_sequential_controller', namespace="dsr01")
        
        self.group = ReentrantCallbackGroup()
        
        self._client_1 = ActionClient(self, BraillePunch, 'grab_paper', callback_group=self.group)
        self._client_2 = ActionClient(self, BraillePunch, 'punch_braille', callback_group=self.group)
        self._client_3 = ActionClient(self, BraillePunch, 'finish_task', callback_group=self.group)
        self._client_4 = ActionClient(self, BraillePunch, 'stamp', callback_group=self.group)
        self._client_5 = ActionClient(self, BraillePunch, 'end_task', callback_group=self.group)
        
        self.is_processing = False
        self.is_recovering = False  
        self.trigger_requested = False
        self.sequence_text = ""
        self.step = 1
        
        self.active_goal_handle = None 
        self.current_result_future = None
        self.step_names = {1: "grab", 2: "punch_braille", 3: "finish_task", 4: "stamp", 5: "end_task"}

        self.robot_ip = "192.168.1.100" 
        self.is_connected = True
        
        self.current_error_type = "NORMAL"
        self.latest_state_code = 1
        
        self.state_cli = self.create_client(GetRobotState, '/dsr01/system/get_robot_state', callback_group=self.group)
        self.control_cli = self.create_client(SetRobotControl, '/dsr01/system/set_robot_control', callback_group=self.group)
        
        self.init_firebase()

        self.create_timer(0.1, self.monitor_trigger, callback_group=self.group)
        self.create_timer(0.5, self.safety_monitor_callback, callback_group=self.group)
        
        threading.Thread(target=self.network_watchdog_loop, daemon=True).start()

    # ==========================================
    # 🧹 로봇 뇌(버퍼) & 액션 서버 강제 초기화
    # ==========================================
    def flush_and_stop_robot(self):
        try:
            from DSR_ROBOT2 import drl_script_stop, DR_QSTOP_STO, stop
            drl_script_stop(DR_QSTOP_STO)
            stop(1) 
        except Exception:
            pass

        if self.active_goal_handle is not None:
            try:
                self.active_goal_handle.cancel_goal_async()
            except Exception:
                pass
            self.active_goal_handle = None

    # ==========================================
    # 🔄 완벽한 이중 복구(Two-Step Recovery) 시퀀스
    # ==========================================
    def reset_robot_sequence(self):
        if self.is_recovering: return
        self.is_recovering = True
        self.get_logger().info("🔄 [RESET] 완전한 재부팅 및 복구 시퀀스 가동...")

        try:
            self.flush_and_stop_robot()
            time.sleep(1.0)

            # 1. [공통] 에러 해제 (RESET: 2번) - 노란불/빨간불 모두 우선 알람을 끕니다.
            if self.current_error_type != "NORMAL":
                self.get_logger().info(f"🛠️ 알람 해제 신호 전송 시작... (현재 에러: {self.current_error_type})")
                req = SetRobotControl.Request()
                req.robot_control = 2
                future = self.control_cli.call_async(req)
                while not future.done(): time.sleep(0.1)
                time.sleep(2.0) 

                # 2. [비상정지 전용] 꺼진 모터 전원(SERVO ON: 3번)을 다시 켜기!
                if self.current_error_type == "EMERGENCY_STOP":
                    self.get_logger().info("🔌 비상정지 복구: 모터 전원(Servo) 다시 켜는 중...")
                    req_servo = SetRobotControl.Request()
                    req_servo.robot_control = 3
                    future_servo = self.control_cli.call_async(req_servo)
                    while not future_servo.done(): time.sleep(0.1)
                    time.sleep(3.0) # 모터 전원이 완전히 인가될 때까지 넉넉히 대기
            
            # 3. 로봇 상태 검증 및 타임아웃 방지
            retry_count = 0
            while self.latest_state_code != 1 and retry_count < 20:
                time.sleep(0.2)
                retry_count += 1
                
            if self.latest_state_code == 6:
                raise Exception("🚨 물리적 비상정지 버튼(버섯 모양)이 아직 눌려 있습니다! 시계방향으로 돌려 해제한 뒤 다시 복구해주세요.")
            
            # 4. 로봇 하드웨어 초기화 및 그리퍼 열기
            from DSR_ROBOT2 import set_robot_mode, set_tool, set_tcp, movej, posj, ROBOT_MODE_MANUAL, ROBOT_MODE_AUTONOMOUS, set_digital_output
            set_robot_mode(ROBOT_MODE_MANUAL); time.sleep(0.5)
            set_tool("Tool Weight"); set_tcp("GripperDA_v1"); set_robot_mode(ROBOT_MODE_AUTONOMOUS); time.sleep(0.5)
            
            set_digital_output(1, 0); set_digital_output(2, 1); time.sleep(1.0)

            # 5. 홈 위치로 완전 복귀
            self.get_logger().info("🏠 홈 위치(HOME)로 완전 복귀 중...")
            movej(posj(0, 0, 90, 0, 90, 0), v=40, a=40)
            
            # 6. 컨트롤러 및 웹 상태 완벽 초기화
            self.step = 1
            self.is_processing = False
            self.trigger_requested = False 
            self.current_error_type = "NORMAL"
            self.active_goal_handle = None
            
            self.braille_ref.update({"resetRobot": False, "error": "", "fitsCard": False})
            self.send_safety_status_to_web(True, "NORMAL", "로봇이 완벽하게 초기화되었습니다. 새로운 명령을 기다립니다.")
            self.get_logger().info("🟢 완전 복구 완료! STEP 1부터 다시 시작할 준비가 되었습니다.")

        except Exception as e:
            self.get_logger().error(f"❌ 복구 중 치명적 오류: {e}")
            self.send_custom_error_to_web(f"복구 실패: {e}")
        finally:
            self.is_recovering = False
            self.hard_restart_ros2_nodes()

    def hard_restart_ros2_nodes(self):
        """
        [수정됨] 로봇 제어 코어(ros2_control_node)와 Rviz가 죽는 현상 해결.
        명확하게 'cobot1' 패키지의 실행 파일 경로만 타겟팅하여 로봇 하드웨어 드라이버를 보호합니다.
        """
        self.get_logger().error("♻️ [시스템 리셋] 공정 노드 정밀 타격 및 재시작 시퀀스 가동...")

        user_home = os.path.expanduser("~")
        workspace = os.path.join(user_home, "cobot_ws")
        script_path = "/tmp/restart_cobot.sh"
        
        # 1. 실행할 쉘 스크립트 내용 작성
        script_content = f"""#!/bin/bash
    source {user_home}/.bashrc

    echo "🛑 [1/3] 기존 공정(액션 서버) 프로세스 안전 종료 중..."
    # [핵심] "cobot_ws/install" 같은 넓은 범위 대신, 우리가 만든 파이썬 노드들이 
    # 위치하는 정확한 폴더인 "cobot1/lib/cobot1"만 타겟팅합니다.
    # 이렇게 하면 dsr_controller2(로봇 뇌)나 rviz2는 절대 영향을 받지 않습니다.
    pkill -SIGINT -f "cobot1/lib/cobot1"
    sleep 3

    echo "🧹 [2/3] 잔류 좀비 노드 완벽 청소 중..."
    # 3초 뒤에도 안 죽은 녀석들만 강제 종료
    pkill -9 -f "cobot1/lib/cobot1"

    # 통신 데몬 리셋 (Action Server 중복 에러 방지용)
    ros2 daemon stop
    sleep 1
    ros2 daemon start

    echo "🚀 [3/3] ROS2 환경 활성화 및 공정 재실행..."
    source /opt/ros/humble/setup.bash
    source {workspace}/install/setup.bash
    export PYTHONPATH=$PYTHONPATH:{workspace}/install/dsr_msgs2/lib/python3.10/site-packages
    export PYTHONPATH=$PYTHONPATH:{workspace}/install/dsr_control2/lib/python3.10/site-packages

    echo "🏁 새로운 액션 서버들을 시작합니다!"
    ros2 launch cobot1 total2.launch.py

    exit 0
    """

        # 2. 스크립트 파일 생성 및 권한 부여
        try:
            with open(script_path, "w") as f:
                f.write(script_content)
            os.chmod(script_path, 0o755)
        except Exception as e:
            self.get_logger().error(f"스크립트 생성 실패: {e}")
            return

        # 3. Terminator 실행 (독립 세션)
        terminal_cmd = [
            "terminator", 
            "-x", 
            f"bash -i -c '{script_path}'"
        ]

        try:
            subprocess.Popen(
                terminal_cmd, 
                start_new_session=True, 
                stdout=subprocess.DEVNULL, 
                stderr=subprocess.DEVNULL
            )
            self.get_logger().warn("✅ 새 Terminator 창으로 제어권 이양 완료. 액션 서버만 재시작합니다.")
        except Exception as e:
            self.get_logger().error(f"터미널 실행 실패: {e}")

        # 4. 퇴장 대기
        time.sleep(1)
        os._exit(0)
    # ==========================================
    # 🛡️ 안전 감시 (노란불 / 빨간불)
    # ==========================================
    def safety_monitor_callback(self):
        if not self.is_connected: return 
        if not self.state_cli.wait_for_service(timeout_sec=0.1): return
        req = GetRobotState.Request()
        future = self.state_cli.call_async(req)
        future.add_done_callback(lambda fut: self.set_latest_state(fut))

    def set_latest_state(self, future):
        try:
            response = future.result()
            self.latest_state_code = response.robot_state
            
            # [핵심] 복구 중에는 파라미터 업데이트만 하고 에러 트리거는 무시! (타임아웃 지연 해결)
            if self.is_recovering: return 
            
            if self.latest_state_code == 5 and self.current_error_type != "SAFE_STOP":
                self.trigger_safety_exception("SAFE_STOP", "⚠️ 충돌 감지! 로봇이 정지했습니다.")
            elif self.latest_state_code == 6 and self.current_error_type != "EMERGENCY_STOP":
                self.trigger_safety_exception("EMERGENCY_STOP", "🚨 비상정지! 스위치를 확인하세요.")
        except: pass

    def trigger_safety_exception(self, error_type, message):
        if self.is_recovering: return
        self.current_error_type = error_type
        threading.Thread(target=self.send_safety_status_to_web, args=(False, error_type, message), daemon=True).start()
        threading.Thread(target=self.send_custom_error_to_web, args=(message,), daemon=True).start()
        if self.is_processing: self.abort_sequence(error_type)

    # ==========================================
    # 🔥 Firebase 리스너 및 든든한 로그
    # ==========================================
    def init_firebase(self):
        try:
            if not firebase_admin._apps:
                pkg_dir = get_package_share_directory('cobot1')
                key_path = "/home/soli/dotdotdirara-d938d-firebase-adminsdk-fbsvc-36dc312ac7.json"

                cred = credentials.Certificate(key_path)
                firebase_admin.initialize_app(cred, {'databaseURL': "https://dotdotdirara-d938d-default-rtdb.asia-southeast1.firebasedatabase.app"})
            
            self.braille_ref = db.reference('/brailleBusinessCardsLatest')
            self.feedback_ref = db.reference('/feedback') 
            self.safe_status_ref = db.reference('/robotStatus/is_safe')
            
            self.braille_ref.update({"resetRobot": False, "fitsCard": False}) 
            self.send_safety_status_to_web(True, "NORMAL", "로봇 제어기가 정상 대기 중입니다.")
            
            self.braille_ref.listen(self.firebase_callback)
            self.get_logger().info("🔥 Firebase 연동 및 리스너 가동 완료")
        except Exception as e:
            self.get_logger().error(f"❌ Firebase 연결 실패: {e}")

    def firebase_callback(self, event):
        data = event.data
        if not data: return
        
        # 1. 복구(Reset) 신호 처리
        is_reset = False
        if isinstance(data, dict): is_reset = data.get('resetRobot', False)
        elif event.path == '/resetRobot': is_reset = bool(data)

        if is_reset and not self.is_recovering:
            threading.Thread(target=self.reset_robot_sequence, daemon=True).start()
            return

        # 2. 시작(Start) 신호 처리 및 검증 로그 출력
        trigger = False
        text = ""
        if isinstance(data, dict):
            trigger = data.get('fitsCard', False)
            self.braille_ref.update({"fitsCard": False})
            text = data.get('brailleText', "")
        elif event.path == '/fitsCard': trigger = bool(data)
            
        if trigger:
            self.get_logger().info(f"📩 웹 신호 감지 (데이터: {text})")
            if self.is_processing:
                self.get_logger().warn("⚠️ 이미 작업이 진행 중이어서 신호를 무시합니다.")
            elif self.current_error_type != "NORMAL":
                self.get_logger().warn(f"⚠️ 현재 로봇이 에러 상태({self.current_error_type})이므로 신호를 무시합니다.")
            elif self.is_recovering:
                self.get_logger().warn("⚠️ 복구가 덜 끝났습니다! 잠시 후 다시 눌러주세요.")
            else:
                self.get_logger().info("🚀 시작 신호 정상 수락! 작업을 개시합니다.")
                self.sequence_text = text
                self.trigger_requested = True 

    # ==========================================
    # 🏃 메인 실행 시퀀스
    # ==========================================
    def monitor_trigger(self):
        if self.trigger_requested and not self.is_processing and not self.is_recovering:
            self.trigger_requested = False
            self.is_processing = True
            self.step = 1
            threading.Thread(target=self.reset_all_feedback, daemon=True).start()
            self.execute_current_step()

    def reset_all_feedback(self):
        try:
            self.braille_ref.update({"error": ""}) 
            self.feedback_ref.set({name: "0.0%" for name in self.step_names.values()})
        except: pass

    def execute_current_step(self):
        if not self.is_connected or self.current_error_type != "NORMAL" or self.is_recovering: return
        clients = {1: self._client_1, 2: self._client_2, 3: self._client_3, 4: self._client_4, 5: self._client_5}
        if self.step not in clients:
            self.finish_all()
            return
        
        self.get_logger().info(f"▶️ [STEP {self.step}] 액션 서버로 목표 전송 중...")
        client = clients[self.step]
        if not client.wait_for_server(timeout_sec=5.0):
            self.abort_sequence("타임아웃")
            return
            
        goal_msg = BraillePunch.Goal()
        if self.step in [1, 2]: goal_msg.text = self.sequence_text
        
        self.current_goal_future = client.send_goal_async(goal_msg, feedback_callback=self.action_feedback_callback)
        self.current_goal_future.add_done_callback(self.goal_response_callback)

    def action_feedback_callback(self, feedback_msg):
        if self.current_error_type != "NORMAL": return 
        progress = feedback_msg.feedback.progress
        threading.Thread(target=self.upload_feedback_to_firebase, args=(self.step, progress), daemon=True).start()

    def upload_feedback_to_firebase(self, step_num, progress):
        try:
            step_label = self.step_names.get(step_num, "unknown")
            self.feedback_ref.child(step_label).set(f"{round(float(progress), 1)}%")
        except: pass 

    def goal_response_callback(self, future):
        goal_handle = future.result()
        if not goal_handle.accepted:
            self.abort_sequence("거부됨")
            return
            
        self.active_goal_handle = goal_handle
        self.current_result_future = goal_handle.get_result_async()
        self.current_result_future.add_done_callback(self.get_result_callback)

    def get_result_callback(self, future):
        self.active_goal_handle = None
        result = future.result().result
        if result.success:
            threading.Thread(target=self.upload_feedback_to_firebase, args=(self.step, 100.0), daemon=True).start()
            self.step += 1
            self.execute_current_step()
        else:
            if result.message == "GRIP_FAIL":
                msg = f"STEP {self.step} 파지 실패"
                threading.Thread(target=self.send_custom_error_to_web, args=(msg,), daemon=True).start()
            self.abort_sequence("실패")
            self.send_safety_status_to_web(False, "EMERGENCY_STOP", "파지를 실패했습니다. 원 위치로 이동해 주세요.")

    # ==========================================
    # 🌐 네트워크 및 유틸리티
    # ==========================================
    def network_watchdog_loop(self):
        while rclpy.ok():
            try:
                res = subprocess.run(['ping', '-c', '1', '-W', '0.5', self.robot_ip], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if res.returncode == 0: self.is_connected = True
                else:
                    if self.is_connected:
                        self.is_connected = False
                        self.trigger_disconnect_error()
            except: pass
            time.sleep(0.5)

    def trigger_disconnect_error(self):
        threading.Thread(target=self.send_custom_error_to_web, args=("네트워크 단절",), daemon=True).start()
        if self.is_processing: self.abort_sequence("네트워크 단절")
        self.send_safety_status_to_web(False, "EMERGENCY_STOP", "네트워크가 단절되었습니다.")

    def send_custom_error_to_web(self, error_msg):
        try:
            self.braille_ref.update({"fitsCard": False, "error": error_msg})
            self.feedback_ref.set({name: "Error" for name in self.step_names.values()})
        except: pass

    def send_safety_status_to_web(self, status, error_type, message):
        try: self.safe_status_ref.set({"status": status, "type": error_type, "message": message})
        except: pass

    def finish_all(self):
        self.is_processing = False
        self.braille_ref.update({"fitsCard": False})
        self.get_logger().info("🏁 모든 공정 완료!")

    def abort_sequence(self, reason):
        self.flush_and_stop_robot()
        self.is_processing = False
        self.braille_ref.update({"fitsCard": False})
        self.get_logger().error(f"🛑 시퀀스 중단: {reason}")
        self.trigger_requested = False # 추가: 대기 중인 트리거도 삭제
        self.step = 1                 # 추가: 스테이징 번호 초기화
        self.active_goal_handle = None
        self.braille_ref.update({"fitsCard": False})

def main(args=None):
    rclpy.init(args=args)
    node = PureController()
    
    DR_init.__dsr__node = node
    DR_init.__dsr__id = "dsr01"
    DR_init.__dsr__model = "m0609"

    executor = MultiThreadedExecutor(num_threads=8)
    executor.add_node(node)

    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == "__main__":
    main()