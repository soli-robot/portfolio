import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from rclpy.qos import QoSProfile, QoSHistoryPolicy
from rclpy.executors import MultiThreadedExecutor       # 💡 추가됨: 멀티스레드 처리용
from rclpy.callback_groups import ReentrantCallbackGroup # 💡 추가됨: 콜백 교착상태 방지용
from nav2_msgs.action import NavigateToPose
from action_msgs.msg import GoalStatus                  # 💡 추가됨: 명확한 상태 코드 확인
from sensor_msgs.msg import Imu, JointState, LaserScan
from geometry_msgs.msg import PoseWithCovarianceStamped
from std_srvs.srv import Empty 
import math
import torch
import threading
import time
import ast
import cv2
import numpy as np
import os

# ==========================================================
# 🚨 환경 설정
# ==========================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
POLICY_MODEL_PATH = "/home/rokey/Downloads/final_code_2/move.pt"

MAP_IMAGE_PATH = "/home/rokey/Downloads/final_code_2/warehouse.png"
MAP_RESOLUTION = 0.05  
MAP_ORIGIN_X = -13.125   
MAP_ORIGIN_Y = -18.975

ACTION_SCALE = 0.5 
FOLDED_POSE = [3.14, -1.57, 1.57, 0.0, 1.57, 0.0] 
HOMING_POSE = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

def euler_to_quaternion(yaw):
    qx, qy, qz, qw = 0.0, 0.0, math.sin(yaw / 2.0), math.cos(yaw / 2.0)
    return qx, qy, qz, qw

class Nav2GoalSender(Node):
    def __init__(self):
        super().__init__('nav2_goal_sender')
        self.standing_count = 0
        self.state = 'IDLE'
        
        self.folded_command_sent = False 
        self.target_x, self.target_y, self.target_yaw = 0.0, 0.0, 0.0
        
        self.homing_done = True 
        self.homing_wait_count = 0 
        
        self.action_done = False
        self.action_result = False
        
        # 💡 [핵심] Action Client가 블로킹되지 않도록 전용 콜백 그룹 할당
        self.cb_group = ReentrantCallbackGroup()
        self.action_client = ActionClient(
            self, 
            NavigateToPose, 
            'navigate_to_pose', 
            callback_group=self.cb_group
        )
        self.goal_handle = None

        self.imu_sub = self.create_subscription(Imu, '/chassis/imu', self.imu_callback, 10)
        self.joint_sub = self.create_subscription(JointState, '/joint_states', self.joint_callback, 10)
        self.scan_sub = self.create_subscription(LaserScan, '/scan', self.scan_callback, 10)
        
        self.initial_pose_pub = self.create_publisher(PoseWithCovarianceStamped, '/initialpose', 10)
        
        qos_profile = QoSProfile(history=QoSHistoryPolicy.KEEP_LAST, depth=1)
        self.joint_cmd_pub = self.create_publisher(JointState, '/joint_commands', qos_profile)

        self.get_logger().info(f'RL 모델 로딩 중... 경로: {POLICY_MODEL_PATH}')
        try:
            self.rl_policy = torch.jit.load(POLICY_MODEL_PATH)
            self.rl_policy.eval()
            self.get_logger().info('✅ 모델 로딩 성공!')
        except Exception as e:
            self.get_logger().error(f'❌ 모델 로딩 실패: {e}')
            self.rl_policy = None

        self.proj_gravity = [0.0, 0.0, -1.0] 
        self.current_obs_pos = []
        self.current_obs_vel = []
        
        self.obs_joint_names = [
            'joint_caster_base', 'joint_wheel_left', 'joint_wheel_right',
            'joint_1', 'joint_swing_left', 'joint_swing_right',
            'joint_2', 'joint_caster_left', 'joint_caster_right',
            'joint_3', 'joint_4', 'joint_5', 'joint_6'
        ]
        
        self.target_joint_names = [
            'joint_1', 'joint_2', 'joint_3', 
            'joint_4', 'joint_5', 'joint_6'
        ]
        
        self.last_action = [0.0] * 6 
        self.rl_timer = self.create_timer(0.02, self.rl_control_loop)

    def imu_callback(self, msg):
        q = msg.orientation
        gx = 2.0 * (q.x * q.z - q.w * q.y)
        gy = 2.0 * (q.y * q.z + q.w * q.x)
        gz = 1.0 - 2.0 * (q.x * q.x + q.y * q.y)
        self.proj_gravity = [-gx, -gy, -gz]

        if self.state == 'NAVIGATING' and self.proj_gravity[2] > -0.5:
            self.get_logger().warn('🚨 넘어짐 감지! 주행 중단 및 0점 정렬 시작.')
            self.state = 'RECOVERING'
            self.homing_done = False 
            self.homing_wait_count = 0 
            self.last_action = [0.0] * 6 
            if self.goal_handle is not None:
                self.goal_handle.cancel_goal_async()

    def joint_callback(self, msg):
        try:
            obs_pos = []
            obs_vel = []
            for name in self.obs_joint_names:
                idx = msg.name.index(name)
                obs_pos.append(msg.position[idx])
                obs_vel.append(msg.velocity[idx])
            self.current_obs_pos = obs_pos
            self.current_obs_vel = obs_vel
        except ValueError:
            pass 

    def scan_callback(self, msg):
        if self.state == 'OPENCV_MATCHING':
            self.get_logger().info('🔍 [자율 인지] 라이다 데이터를 맵과 매칭하여 현재 위치를 탐색합니다...')
            self.state = 'MATCHING_IN_PROGRESS' 
            threading.Thread(target=self.perform_opencv_matching, args=(msg,), daemon=True).start()

    def perform_opencv_matching(self, scan_msg):
        try:
            world_map = cv2.imread(MAP_IMAGE_PATH, cv2.IMREAD_GRAYSCALE)
            if world_map is None:
                self.get_logger().error(f"❌ 맵 이미지를 찾을 수 없습니다: {MAP_IMAGE_PATH}")
                self.resume_navigation_directly()
                return

            _, world_map_thresh = cv2.threshold(world_map, 127, 255, cv2.THRESH_BINARY_INV)

            max_range = 8.0 
            pixel_size = int((max_range * 2) / MAP_RESOLUTION)
            center_p = pixel_size // 2
            local_img = np.zeros((pixel_size, pixel_size), dtype=np.uint8)

            angles = np.linspace(scan_msg.angle_min, scan_msg.angle_max, len(scan_msg.ranges))
            for i, r in enumerate(scan_msg.ranges):
                if scan_msg.range_min < r < max_range and not math.isinf(r) and not math.isnan(r):
                    x = r * math.cos(angles[i])
                    y = r * math.sin(angles[i])
                    
                    px = center_p + int(x / MAP_RESOLUTION)
                    py = center_p - int(y / MAP_RESOLUTION) 
                    
                    if 0 <= px < pixel_size and 0 <= py < pixel_size:
                        cv2.circle(local_img, (px, py), 2, 255, -1)

            _, mask = cv2.threshold(local_img, 10, 255, cv2.THRESH_BINARY)

            best_val = -1
            best_loc = (0, 0)
            best_angle_deg = 0

            self.get_logger().info('🔄 [OpenCV] 로봇이 스스로 위치를 계산 중입니다... (약 1~2초 소요)')
            
            for deg in range(0, 360, 5):
                M = cv2.getRotationMatrix2D((center_p, center_p), deg, 1.0)
                rotated_template = cv2.warpAffine(local_img, M, (pixel_size, pixel_size))
                rotated_mask = cv2.warpAffine(mask, M, (pixel_size, pixel_size))

                res = cv2.matchTemplate(world_map_thresh, rotated_template, cv2.TM_CCORR_NORMED, mask=rotated_mask)
                _, max_val, _, max_loc = cv2.minMaxLoc(res)

                if max_val > best_val:
                    best_val = max_val
                    best_loc = max_loc
                    best_angle_deg = deg

            global_center_px = best_loc[0] + center_p
            global_center_py = best_loc[1] + center_p
            map_h = world_map.shape[0]
            
            real_x = MAP_ORIGIN_X + (global_center_px * MAP_RESOLUTION)
            real_y = MAP_ORIGIN_Y + ((map_h - global_center_py) * MAP_RESOLUTION)
            real_yaw = math.radians(best_angle_deg)

            self.get_logger().info(f"🎯 [자율 복구 성공!] 좌표 확정: X={real_x:.2f}, Y={real_y:.2f}, Yaw={best_angle_deg}도")
            self.inject_self_pose(real_x, real_y, real_yaw)

        except Exception as e:
            self.get_logger().error(f"❌ OpenCV 연산 실패: {e}")
            self.resume_navigation_directly()

    def inject_self_pose(self, x, y, yaw):
        msg = PoseWithCovarianceStamped()
        msg.header.frame_id = 'map'
        msg.header.stamp = self.get_clock().now().to_msg()
        
        msg.pose.pose.position.x = float(x)
        msg.pose.pose.position.y = float(y)
        msg.pose.pose.position.z = 0.0
        qx, qy, qz, qw = euler_to_quaternion(yaw)
        msg.pose.pose.orientation.x = qx
        msg.pose.pose.orientation.y = qy
        msg.pose.pose.orientation.z = qz
        msg.pose.pose.orientation.w = qw
        
        msg.pose.covariance[0] = 0.01
        msg.pose.covariance[7] = 0.01
        msg.pose.covariance[35] = 0.01
        
        self.initial_pose_pub.publish(msg)
        time.sleep(1.0) 
        self.resume_navigation_directly()

    def resume_navigation_directly(self):
        self.get_logger().info('🔄 복구 완료: 기존 목표지로 다시 출발합니다.')
        self.state = 'IDLE'
        self.send_goal(self.target_x, self.target_y, self.target_yaw)

    def rl_control_loop(self):
        if self.state == 'IDLE':
            return

        if self.state in ['NAVIGATING', 'OPENCV_MATCHING', 'MATCHING_IN_PROGRESS']:
            if not self.folded_command_sent:
                cmd_msg = JointState()
                cmd_msg.header.stamp = self.get_clock().now().to_msg()
                cmd_msg.name = self.target_joint_names
                cmd_msg.position = FOLDED_POSE
                self.joint_cmd_pub.publish(cmd_msg)
                
                self.folded_command_sent = True 
            return

        if self.state == 'RECOVERING':
            if self.rl_policy is None: return

            if not self.homing_done:
                cmd_msg = JointState()
                cmd_msg.header.stamp = self.get_clock().now().to_msg()
                cmd_msg.name = self.target_joint_names
                cmd_msg.position = HOMING_POSE
                self.joint_cmd_pub.publish(cmd_msg)
                
                self.homing_wait_count += 1
                if self.homing_wait_count < 100:
                    return 
                
                self.get_logger().info('✅ 0점 정렬 완료! RL 기립 정책을 가동합니다.')
                self.homing_done = True
                self.last_action = [0.0] * 6
                return

            is_standing = self.proj_gravity[2] < -0.95
            if is_standing: self.standing_count += 1
            else: self.standing_count = 0
                
            if self.standing_count > 50:
                self.get_logger().info('✅ 기립 성공! 주행을 멈추고 맵 매칭을 실시합니다.')
                self.state = 'OPENCV_MATCHING' 
                self.last_action = [0.0] * 6
                self.standing_count = 0
                return

            if not self.current_obs_pos: return

            obs_list = self.proj_gravity + [c - 0.0 for c in self.current_obs_pos] + self.current_obs_vel + self.last_action
                
            try:
                obs_tensor = torch.tensor([obs_list], dtype=torch.float32)
                with torch.no_grad():
                    action_tensor = self.rl_policy(obs_tensor)
                
                raw_action = action_tensor[0].tolist()[:6]
                self.last_action = raw_action
                scaled_action = [a * ACTION_SCALE for a in raw_action]

                cmd_msg = JointState()
                cmd_msg.header.stamp = self.get_clock().now().to_msg()
                cmd_msg.name = self.target_joint_names
                cmd_msg.position = scaled_action
                self.joint_cmd_pub.publish(cmd_msg)
                
            except Exception as e:
                self.get_logger().error(f"🧠 RL 에러 발생: {e}")

    def send_goal(self, x, y, yaw):
        self.action_done = False
        self.action_result = False
        self.target_x, self.target_y, self.target_yaw = x, y, yaw
        
        self.get_logger().info(f'Nav2 목적지 전송 중... (X:{x}, Y:{y}, Yaw:{yaw})')
        
        goal_msg = NavigateToPose.Goal()
        goal_msg.pose.header.frame_id = 'map'
        goal_msg.pose.header.stamp = self.get_clock().now().to_msg()
        goal_msg.pose.pose.position.x = float(x)
        goal_msg.pose.pose.position.y = float(y)
        goal_msg.pose.pose.position.z = 0.0
        qx, qy, qz, qw = euler_to_quaternion(yaw)
        goal_msg.pose.pose.orientation.x = qx
        goal_msg.pose.pose.orientation.y = qy
        goal_msg.pose.pose.orientation.z = qz
        goal_msg.pose.pose.orientation.w = qw
        
        self.state = 'NAVIGATING'
        self.folded_command_sent = False 
        
        self.send_goal_future = self.action_client.send_goal_async(goal_msg)
        self.send_goal_future.add_done_callback(self.goal_response_callback)

    def goal_response_callback(self, future):
        # 💡 [보강] 에러 발생 시 무시되지 않도록 try-except 래핑
        try:
            self.goal_handle = future.result()
            if not self.goal_handle.accepted: 
                self.get_logger().warn('🚨 Nav2: 목표가 거부되었습니다.')
                self.action_result = False
                self.action_done = True
                self.state = 'IDLE' 
                return
            
            self.get_logger().info('🚀 Nav2: 이동 시작 (목표 수락됨).')
            self._get_result_future = self.goal_handle.get_result_async()
            self._get_result_future.add_done_callback(self.get_result_callback)
        except Exception as e:
            self.get_logger().error(f"Goal 응답 처리 실패: {e}")
            self.action_result = False
            self.action_done = True
            self.state = 'IDLE'

    def get_result_callback(self, future):
        if self.state in ['RECOVERING', 'OPENCV_MATCHING', 'MATCHING_IN_PROGRESS']: 
            self.get_logger().info('⚠️ 복구 모드 중 네비게이션 취소 응답 수신 (무시함).')
            return
            
        # 💡 [보강] 결과를 받아올 때 블로킹되거나 터지는 현상 완벽 방지
        try:
            result = future.result()
            status = result.status
            if status == GoalStatus.STATUS_SUCCEEDED:  # 성공 코드는 4
                self.get_logger().info('✅ Nav2: 목적지 완벽히 도착 완료!')
                self.action_result = True
            else:
                self.get_logger().error(f'⚠️ Nav2: 이동 실패 (Status Code: {status})')
                self.action_result = False
        except Exception as e:
            self.get_logger().error(f"결과 콜백 처리 실패: {e}")
            self.action_result = False
        finally:
            self.state = 'IDLE'
            self.action_done = True # 무조건 True를 보장하여 루프를 풀어줌


# =========================================================================
# 🌐 [모듈화 영역]
# =========================================================================
_ros_thread = None
_nav_node = None

def _spin_ros_node():
    global _nav_node
    if not rclpy.ok():
        rclpy.init()
    _nav_node = Nav2GoalSender()
    
    # 💡 [핵심 해결책] 타이머와 액션 콜백이 서로를 막지 않도록 멀티스레드 이그제큐터 사용
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(_nav_node)
    
    try:
        executor.spin()
    except Exception:
        pass
    finally:
        if _nav_node:
            _nav_node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

def init_module():
    global _ros_thread
    if _ros_thread is None:
        print("🤖 [Move Module] ROS2 Nav2 백그라운드 노드를 시작합니다...")
        _ros_thread = threading.Thread(target=_spin_ros_node, daemon=True)
        _ros_thread.start()
        time.sleep(2.0) 

init_module()

def navigate_to(coordinate_str, timeout_sec=120):
    global _nav_node
    if _nav_node is None:
        return False

    try:
        coords = ast.literal_eval(coordinate_str)
        target_x, target_y, target_yaw = float(coords[0]), float(coords[1]), float(coords[2])
    except Exception:
        return False
        
    print(f"🚙 [Move Module] 명령 전송 -> X:{target_x}, Y:{target_y}, Yaw:{target_yaw}")
    
    _nav_node.action_done = False
    _nav_node.action_result = False
    _nav_node.send_goal(target_x, target_y, target_yaw)
    
    start_time = time.time()
    
    # 💡 이제 멀티스레드와 튼튼한 콜백 덕분에 도착하는 즉시 action_done이 True가 됩니다.
    while not _nav_node.action_done:
        if time.time() - start_time > timeout_sec:
            print(f"⏰ [Move Module] 타임아웃 발생! ({timeout_sec}초 경과).")
            if _nav_node.goal_handle:
                _nav_node.goal_handle.cancel_goal_async()
            _nav_node.state = 'IDLE'
            return False
        time.sleep(0.1)
        
    return _nav_node.action_result