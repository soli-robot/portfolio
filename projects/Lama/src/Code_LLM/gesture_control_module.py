import os
import sys
import time
import cv2
import math
import numpy as np
from collections import deque
from scipy.spatial.transform import Rotation
import pyrealsense2 as rs
from ultralytics import YOLO

import rclpy
from rclpy.node import Node
import mediapipe as mp

import DR_init
from ament_index_python.packages import get_package_share_directory
from pick_and_place_text.onrobot import RG

# ==========================================================
# 1. 환경 및 로봇 기본 설정
# ==========================================================
ROBOT_ID = "dsr01"
ROBOT_MODEL = "m0609"
VELOCITY, ACC = 100, 100

MAINTAIN_DISTANCE = 500.0
CUBE_CENTER = np.array([400.0, 0.0, 300.0])
SPHERE_RADIUS = 150.0
CUBE_HALF_SIZE = 270.0  

CUBE_MIN = CUBE_CENTER - CUBE_HALF_SIZE
CUBE_MAX = CUBE_CENTER + CUBE_HALF_SIZE

X_MIN, X_MAX = 300.0, 700.0
Y_MIN, Y_MAX = -400.0, 400.0
Z_MIN, Z_MAX = 50.0, 700.0

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
YOLO_WEIGHTS_PATH = os.path.join(BASE_DIR, "source", "towel_yolo26n_seg_v5_best.pt")
HOME_JOINT = [0, 0, 90, -90, 90, 0]

# 🗜️ 그리퍼 세팅 (IP와 포트는 상황에 맞게 유지)
GRIPPER_NAME = "rg2"
TOOLCHANGER_IP = "192.168.1.1"
TOOLCHANGER_PORT = "502"
gripper = RG(GRIPPER_NAME, TOOLCHANGER_IP, TOOLCHANGER_PORT)

class GestureDirectController(Node):
    def __init__(self):
        super().__init__("gesture_direct_control_node")

        self.model = YOLO(YOLO_WEIGHTS_PATH)
        self.object_data = {
            "colored_towel": {"pos": None, "area": 0},
            "container1": {"pos": None, "area": 0}
        }
        
        self.init_robot()
        self.gripper_is_open = True
        
        self.action_lock_time = 0.0
        self.three_finger_count = 0  
        self.is_task_done = False # 💡 작업 완료 플래그
        
        self.virtual_sphere_center = None 
        self.vec_buffer = deque(maxlen=10) 
        self.origin_buffer = deque(maxlen=10) 
        
        self.get_logger().info("Starting RealSense via USB...")
        self.pipeline = rs.pipeline()
        config = rs.config()
        config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)
        config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
        
        profile = self.pipeline.start(config)
        self.align = rs.align(rs.stream.color) 
        
        intr = profile.get_stream(rs.stream.color).as_video_stream_profile().get_intrinsics()
        self.intrinsics = {'fx': intr.fx, 'fy': intr.fy, 'ppx': intr.ppx, 'ppy': intr.ppy}
        
        self.mp_drawing = mp.solutions.drawing_utils
        self.mp_hands = mp.solutions.hands
        self.hands = self.mp_hands.Hands(max_num_hands=1, min_detection_confidence=0.7)
        
        self.gripper2cam_path = '/home/rokey/Downloads/20/realsense_detection/T_gripper2camera.npy'

    def _get_roi_median_depth(self, depth_frame, x_min, x_max, y_min, y_max):
        depth_array = np.asanyarray(depth_frame.get_data())
        h, w = depth_array.shape 
        x_min, x_max = max(0, x_min), min(w, x_max + 1)
        y_min, y_max = max(0, y_min), min(h, y_max + 1)
        
        roi = depth_array[y_min:y_max, x_min:x_max]
        valid_depths = roi[roi > 0] 
        
        if len(valid_depths) > 0:
            return float(np.median(valid_depths)) * 1.0 
        return 0.0

    def _pixel_to_camera_coords(self, x, y, z):
        fx, fy = self.intrinsics['fx'], self.intrinsics['fy']
        ppx, ppy = self.intrinsics['ppx'], self.intrinsics['ppy']
        return ((x - ppx) * z / fx, (y - ppy) * z / fy, z)

    def _count_fingers(self, hand_landmarks):
        finger_count = 0
        def dist(idx1, idx2):
            p1 = hand_landmarks.landmark[idx1]
            p2 = hand_landmarks.landmark[idx2]
            return math.hypot(p1.x - p2.x, p1.y - p2.y)
            
        for tip, pip in zip([8, 12, 16, 20], [6, 10, 14, 18]):
            if dist(tip, 0) > dist(pip, 0):
                finger_count += 1
        if dist(4, 17) > dist(3, 17):
            finger_count += 1
        return finger_count

    def get_robot_pose_matrix(self, x, y, z, rx, ry, rz):
        R = Rotation.from_euler("ZYZ", [rx, ry, rz], degrees=True).as_matrix()
        T = np.eye(4)
        T[:3, :3] = R
        T[:3, 3] = [x, y, z]
        return T

    def transform_to_base(self, camera_coords, robot_pos):
        try: gripper2cam = np.load(self.gripper2cam_path)
        except: gripper2cam = np.eye(4)
        coord = np.append(np.array(camera_coords), 1)
        x, y, z, rx, ry, rz = robot_pos
        base2gripper = self.get_robot_pose_matrix(x, y, z, rx, ry, rz)
        base2cam = base2gripper @ gripper2cam
        return np.dot(base2cam, coord)[:3]

    def init_robot(self):
        try:
            from DSR_ROBOT2 import movej, mwait
            movej(HOME_JOINT, vel=50, acc=50)
            gripper.open_gripper()
            mwait()
        except: pass

    def get_world_pos(self, bbox, depth_frame, w, h):
        x1, y1, x2, y2 = bbox
        cx, cy = int((x1 + x2) / 2), int((y1 + y2) / 2)
        
        orig_x1, orig_x2 = w - 1 - x2, w - 1 - x1
        orig_y1, orig_y2 = h - 1 - y2, h - 1 - y1
        orig_cx, orig_cy = w - 1 - cx, h - 1 - cy
        
        cz = self._get_roi_median_depth(depth_frame, orig_x1, orig_x2, orig_y1, orig_y2)
        if cz <= 0: return None
        
        cam_pos = self._pixel_to_camera_coords(orig_cx, orig_cy, cz)
        from DSR_ROBOT2 import get_current_posx
        return self.transform_to_base(cam_pos, get_current_posx()[0])

    def perform_pick_and_place(self):
        from DSR_ROBOT2 import movej, movel, mwait
        self.get_logger().info("📦 작업 시작: Pick & Place 시퀀스 가동")
        towl = self.object_data.get("colored_towel", {}).get("pos")
        cont = self.object_data.get("container1", {}).get("pos")
        
        if towl is None or cont is None:
            self.get_logger().error("❌ 두 객체의 좌표가 모두 수집되지 않았습니다!")
            return

        # 1. Pick (Towel)
        movej(HOME_JOINT, vel=100, acc=100)
        movel([towl[0]+100, towl[1], towl[2] + 150, 1, 179, 1], vel=100, acc=100)
        gripper.open_gripper()
        movel([towl[0]+100, towl[1], towl[2], 1, 179, 1], vel=50, acc=50)
        gripper.close_gripper()
        mwait()
        movel([towl[0]+100, towl[1], towl[2] + 150, 1, 179, 1], vel=100, acc=100)
        
        # 2. Place (Container)
        movel([cont[0]+30, cont[1]-100, cont[2] + 150, 1, 179, 1], vel=100, acc=100)
        movel([cont[0]+30, cont[1]-150, cont[2]+100, 1, 179, 1], vel=50, acc=50)
        gripper.open_gripper()
        mwait()
        movel([cont[0]+30, cont[1], cont[2] + 150, 1, 179, 1], vel=100, acc=100)
        movej(HOME_JOINT, vel=100, acc=100)
        
        self.get_logger().info("✅ Pick & Place 작업 완료!")
        self.is_task_done = True # 💡 루프 탈출을 위한 플래그 설정

    def follow_hand(self, timeout=30.0):
        from DSR_ROBOT2 import amovel, amovej, get_current_posx, get_current_posj, wait
        
        self.get_logger().info(f"🚀 Ready! Operating with 0.05 sec USB throttle. Timeout: {timeout}s")
        last_action_time = time.time()
        last_hand_seen_time = time.time() # 💡 타임아웃 측정을 위한 변수
        
        try:
            while rclpy.ok():
                if self.is_task_done:
                    return "Pick & Place 작업이 성공적으로 완료되었습니다."
                
                # 타임아웃 검사
                if time.time() - last_hand_seen_time > timeout:
                    self.get_logger().info("🛑 손이 감지되지 않아 제스처 모드를 종료합니다.")
                    return "제스처 모드 자동 종료 (타임아웃)"

                frames = self.pipeline.wait_for_frames()
                current_time = time.time()
                if current_time - last_action_time < 0.05: continue
                last_action_time = current_time

                aligned_frames = self.align.process(frames)
                color_frame = aligned_frames.get_color_frame()
                depth_frame = aligned_frames.get_depth_frame()
                
                if not color_frame or not depth_frame: continue

                frame = np.asanyarray(color_frame.get_data())
                frame = cv2.flip(frame, -1)
                h, w, c = frame.shape  

                # ==========================================================
                # 👁️ YOLO 객체 탐지
                # ==========================================================
                results_yolo = self.model(frame, verbose=False)
                frame = results_yolo[0].plot() 
                
                for r in results_yolo:
                    for box in r.boxes:
                        cls = int(box.cls[0])
                        name = self.model.names[cls]
                        if name in self.object_data:
                            x1, y1, x2, y2 = map(int, box.xyxy[0])
                            area = (x2 - x1) * (y2 - y1)
                            if area > self.object_data[name]["area"]: 
                                pos = self.get_world_pos([x1, y1, x2, y2], depth_frame, w, h)
                                if pos is not None:
                                    self.object_data[name] = {"pos": pos, "area": area}

                # ==========================================================
                # ✋ MediaPipe 제스처 인식
                # ==========================================================
                image_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                results = self.hands.process(image_rgb)

                if results.multi_hand_landmarks:
                    last_hand_seen_time = time.time() # 손 보이면 타임아웃 리셋
                    hand_landmarks = results.multi_hand_landmarks[0]
                    
                    finger_count = self._count_fingers(hand_landmarks)
                    cv2.putText(frame, f'Fingers: {finger_count}', (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)

                    if finger_count != 3: self.three_finger_count = 0

                    if time.time() < self.action_lock_time:
                        cv2.putText(frame, 'Moving... Commands Locked', (10, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                        if len(self.vec_buffer) > 0: 
                            self.vec_buffer.clear()
                            self.origin_buffer.clear()
                        self.mp_drawing.draw_landmarks(frame, hand_landmarks, self.mp_hands.HAND_CONNECTIONS)
                    
                    else:
                        # 🖐️ 손가락 5개: 위치 이동
                        if finger_count == 5:
                            if len(self.vec_buffer) > 0: 
                                self.vec_buffer.clear()
                                self.origin_buffer.clear()
                                
                            if not self.gripper_is_open:
                                gripper.open_gripper()
                                self.gripper_is_open = True
                                
                            target_lm = hand_landmarks.landmark[9]
                            cx, cy = int(target_lm.x * w), int(target_lm.y * h)
                            cx, cy = max(0, min(cx, w - 1)), max(0, min(cy, h - 1))
                            orig_cx, orig_cy = w - 1 - cx, h - 1 - cy
                            
                            cz = self._get_roi_median_depth(depth_frame, orig_cx-5, orig_cx+5, orig_cy-5, orig_cy+5)
                            
                            if cz > 0:
                                hand_camera_coords = self._pixel_to_camera_coords(orig_cx, orig_cy, cz)
                                hand_vec = np.array(hand_camera_coords)
                                distance_to_hand = np.linalg.norm(hand_vec)
                                
                                if distance_to_hand > 0:
                                    direction_vec = hand_vec / distance_to_hand
                                    target_camera_coords = hand_vec - (direction_vec * MAINTAIN_DISTANCE)
                                    robot_posx = get_current_posx()[0]
                                    td_coord = self.transform_to_base(target_camera_coords, robot_posx)

                                    target_pos = [
                                        np.clip(td_coord[0], X_MIN, X_MAX),
                                        np.clip(td_coord[1], Y_MIN, Y_MAX),
                                        np.clip(td_coord[2]+50, Z_MIN, Z_MAX)
                                    ] + robot_posx[3:]
                                    amovel(target_pos, vel=VELOCITY, acc=ACC)

                        # 🖐️ 손가락 4개: 정육면체/구 표면 광선 추적
                        elif finger_count == 4:
                            tip_xs = [int(hand_landmarks.landmark[i].x * w) for i in [8, 12, 16, 20]]
                            tip_ys = [int(hand_landmarks.landmark[i].y * h) for i in [8, 12, 16, 20]]
                            
                            avg_tip_x, avg_tip_y = int(np.mean(tip_xs)), int(np.mean(tip_ys))
                            
                            orig_min_x, orig_max_x = w - 1 - max(tip_xs), w - 1 - min(tip_xs)
                            orig_min_y, orig_max_y = h - 1 - max(tip_ys), h - 1 - min(tip_ys)
                            
                            base_lm = hand_landmarks.landmark[9]
                            orig_base_x, orig_base_y = w - 1 - int(base_lm.x * w), h - 1 - int(base_lm.y * h)
                            
                            z_tip = self._get_roi_median_depth(depth_frame, orig_min_x, orig_max_x, orig_min_y, orig_max_y)
                            z_base = self._get_roi_median_depth(depth_frame, orig_base_x-10, orig_base_x+10, orig_base_y-10, orig_base_y+10)
                            
                            if z_tip > 0 and z_base > 0:
                                orig_avg_tip_x, orig_avg_tip_y = w - 1 - avg_tip_x, h - 1 - avg_tip_y
                                cam_tip = self._pixel_to_camera_coords(orig_avg_tip_x, orig_avg_tip_y, z_tip)
                                cam_base = self._pixel_to_camera_coords(orig_base_x, orig_base_y, z_base)
                                
                                robot_posx = get_current_posx()[0]
                                base_tip_3d = self.transform_to_base(cam_tip, robot_posx)
                                base_base_3d = self.transform_to_base(cam_base, robot_posx)
                                
                                vec_base = base_tip_3d - base_base_3d
                                vec_norm = np.linalg.norm(vec_base)
                                
                                if vec_norm > 0:
                                    self.vec_buffer.append(vec_base / vec_norm)
                                    self.origin_buffer.append(base_base_3d) 
                                    
                                if len(self.vec_buffer) < 10:
                                    pass
                                else:
                                    O = np.mean(self.origin_buffer, axis=0)
                                    V = np.mean(self.vec_buffer, axis=0)
                                    V = V / np.linalg.norm(V)
                                    
                                    candidates = []
                                    if abs(V[2]) > 1e-6:
                                        t_floor = (55.0 - O[2]) / V[2]
                                        P_floor = O + t_floor * V
                                        if t_floor > 0 and CUBE_MIN[0] <= P_floor[0] <= CUBE_MAX[0] and CUBE_MIN[1] <= P_floor[1] <= CUBE_MAX[1]:
                                            candidates.append(P_floor)

                                    L = O - CUBE_CENTER
                                    a, b, c = np.dot(V, V), 2.0 * np.dot(V, L), np.dot(L, L) - SPHERE_RADIUS**2
                                    D = b**2 - 4*a*c
                                    if D >= 0:
                                        t1, t2 = (-b - math.sqrt(D))/(2*a), (-b + math.sqrt(D))/(2*a)
                                        if t1 > 0: candidates.append(O + t1*V)
                                        if t2 > 0: candidates.append(O + t2*V)
                                    
                                    t_mins = (CUBE_MIN - O) / (V + 1e-8)
                                    t_maxs = (CUBE_MAX - O) / (V + 1e-8)
                                    t_n, t_f = np.max(np.minimum(t_mins, t_maxs)), np.min(np.maximum(t_mins, t_maxs))
                                    if t_n <= t_f and t_f > 0:
                                        candidates.append(O + (t_n if t_n > 0 else t_f) * V)

                                    if candidates:
                                        target_point = max(candidates, key=lambda p: p[1])
                                        D_CT = target_point - CUBE_CENTER
                                        dist_CT = np.linalg.norm(D_CT)
                                        U_CT = D_CT / dist_CT if dist_CT > 0 else np.array([1.0, 0.0, 0.0])
                                        if U_CT[0] < 0:
                                            U_CT[0] = 0.0
                                            U_CT = U_CT / np.linalg.norm(U_CT)
                                        robot_target_xyz = CUBE_CENTER + (SPHERE_RADIUS * U_CT)
                                        
                                        look_vec = target_point - robot_target_xyz
                                        Z_axis = (look_vec / np.linalg.norm(look_vec)) if np.linalg.norm(look_vec) > 0 else U_CT
                                        
                                        temp_X = np.array([1, 0, 0] if abs(Z_axis[2]) > 0.99 else [0, 0, 1])
                                        Y_axis = np.cross(Z_axis, temp_X)
                                        Y_axis = Y_axis / np.linalg.norm(Y_axis)
                                        X_axis = np.cross(Y_axis, Z_axis)
                                        
                                        R_mat = np.column_stack((X_axis, Y_axis, Z_axis))
                                        target_euler = Rotation.from_matrix(R_mat).as_euler('ZYZ', degrees=True)
                                        
                                        target_pos = [robot_target_xyz[0], robot_target_xyz[1], robot_target_xyz[2], 
                                                      target_euler[0], target_euler[1], target_euler[2]]
                                        
                                        amovel(target_pos, vel=60, acc=60)
                                        self.action_lock_time = time.time() + 5.0
                                        cv2.putText(frame, 'Executing Raycast...', (10, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 165, 255), 2)

                        # 🖐️ 손가락 3개: Pick and Place 
                        elif finger_count == 3:
                            self.three_finger_count += 1
                            cv2.putText(frame, f'P&P Standby: {self.three_finger_count}/10', (10, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                            
                            if self.three_finger_count >= 10: 
                                self.three_finger_count = 0
                                self.action_lock_time = time.time() + 10.0 
                                self.perform_pick_and_place()

                        else:
                            if len(self.vec_buffer) > 0: 
                                self.vec_buffer.clear()
                                self.origin_buffer.clear()

                            if finger_count == 0:
                                if self.gripper_is_open:
                                    gripper.close_gripper()
                                    self.gripper_is_open = False
                            elif finger_count == 2:
                                amovej(HOME_JOINT, vel=VELOCITY, acc=ACC)
                                wait(1.0)
                            elif finger_count == 1:
                                curr_j = get_current_posj()
                                target_j = list(curr_j)
                                cy = int(hand_landmarks.landmark[9].y * h)
                                target_j[3] -= (240 - cy) * 0.05 
                                amovej(target_j, vel=VELOCITY, acc=ACC)

                        self.mp_drawing.draw_landmarks(frame, hand_landmarks, self.mp_hands.HAND_CONNECTIONS)
                
                else:
                    if len(self.vec_buffer) > 0: 
                        self.vec_buffer.clear()
                        self.origin_buffer.clear()
                    self.three_finger_count = 0 
                
                # 타임아웃을 화면에 표시
                remain_time = max(0, timeout - (time.time() - last_hand_seen_time))
                cv2.putText(frame, f'Timeout: {remain_time:.1f}s', (400, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
                cv2.imshow("Gesture Control", frame)
                if cv2.waitKey(1) & 0xFF == 27: break
        finally:
            self.pipeline.stop()
            cv2.destroyAllWindows()
            return "제스처 루프 강제 종료됨"

# ==========================================================
# 💡 외부에서 안전하게 호출하기 위한 래퍼(Wrapper) 함수
# ==========================================================
def run_gesture_module():
    import DR_init
    DR_init.__dsr__id = ROBOT_ID
    DR_init.__dsr__model = ROBOT_MODEL
    
    # rclpy가 이미 초기화되지 않았다면 초기화
    if not rclpy.ok():
        rclpy.init()
        
    node = rclpy.create_node("gesture_direct_control", namespace=ROBOT_ID)
    DR_init.__dsr__node = node
    
    try:
        controller = GestureDirectController()
        # 30초 동안 손이 안 보이면 타임아웃, 작업 완료 시 즉시 복귀
        result_msg = controller.follow_hand(timeout=30.0)
        return {"status": "SUCCESS", "message": result_msg}
    except Exception as e:
        return {"status": "FAIL", "message": str(e)}
    finally:
        node.destroy_node()
        # 메인 웹소켓 서버를 위해 rclpy.shutdown()은 생략합니다.