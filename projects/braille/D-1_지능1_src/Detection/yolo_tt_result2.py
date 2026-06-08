#!/usr/bin/env python3

import time
import json
import requests
import threading
import queue
import cv2
import numpy as np

import rclpy
from rclpy.node import Node
from rclpy.executors import MultiThreadedExecutor
from rclpy.duration import Duration
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy, DurabilityPolicy
from rclpy.time import Time

from sensor_msgs.msg import Image as ROSImage, CameraInfo, CompressedImage
from geometry_msgs.msg import PointStamped
from std_msgs.msg import Bool, String, Int32
from cv_bridge import CvBridge
from tf2_ros import Buffer, TransformListener
from tf2_geometry_msgs.tf2_geometry_msgs import do_transform_point

from ultralytics import YOLO
from message_filters import Subscriber, ApproximateTimeSynchronizer


SERVER = "http://192.168.108.60:5000"

# ros2 topic pub --once /api/security_status std_msgs/msg/Bool "{data: true}" 욜로 탐지 시작 코드 
# ros2 topic pub /robot2/arrive_position std_msgs/msg/Int32 "{data: 1}" 지점 도착해서 확인하는 코드 pot
# ros2 topic pub /robot2/arrive_position std_msgs/msg/Int32 "{data: 2}" 지점 도착해서 확인하는 코드 ball
# ros2 topic pub --once /robot8/catch_thief_8 std_msgs/msg/Bool "{data: true}" 2번 좌표 계산 중단 코드
# ros2 topic pub --once /robot8/catch_thief_8 std_msgs/msg/Bool "{data: false}" 2번 좌표 계산 재시직
 


class IntegratedYOLONode(Node):
    def __init__(self, robot_ns='/robot2'):
        super().__init__(
            'integrated_yolo_node',
            namespace=robot_ns.strip('/'),
        )

        self.get_logger().info(f"🔥 통합 YOLO 노드 시작 (namespace={robot_ns})")

        self.bridge = CvBridge()
        self.lock = threading.Lock()
        self.main_group = ReentrantCallbackGroup()
        self.robot_name = robot_ns.strip('/')
        self.is_shutting_down = False

        # =====================================================
        # 상태 변수 / 모드
        # =====================================================
        self.trigger = True
        self.detecting = False
        self.arrive_position_triggered = None

        self.catch_thief_8 = False
        self.catch_thief_2_sent = False

        self.missing_sent = {
            1: False,
            2: False
        }

        self.detected_accum = set()

        self.K_rgb = None
        self.K_depth = None
        self.rgb_frame_id = None
        self.depth_frame_id = None

        self.last_detect_time = 0.0
        self.detect_interval = 0.1

        self.last_valid_pose = None
        self.last_valid_pose_time = 0.0
        self.pose_hold_time = 2.0
        self.last_publish_time = 0.0

        self.session = requests.Session()
        self.turtle_items = self.load_items_from_server("turtle_items")
        self.TARGET_OBJECTS = {item["name"] for item in self.turtle_items}
        self.get_logger().info(f"📦 유물 리스트: {self.TARGET_OBJECTS}")

        self.TARGET = [
            [self.find_item_name("C001", "pig")],
            [self.find_item_name("C002", "ball")]
        ]
        self.get_logger().info(f"🎯 TARGET: {self.TARGET}")

        self.server_url = f"{SERVER}/upload"
        self.turtle_log_url = f"{SERVER}/api/turtlebot_log"

        self.upload_queue = queue.Queue(maxsize=4)
        self.worker_thread = threading.Thread(target=self.upload_worker, daemon=True)
        self.worker_thread.start()

        self.model = YOLO('/home/rokey/rokey_ws/src/final/final/best.pt')
        self.model.to('cpu')
        self.get_logger().info(f"✅ YOLO 로드 완료 (CPU): {self.model.names}")

        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)

        self.video_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=5,
            durability=DurabilityPolicy.VOLATILE
        )

        self.pub_image = self.create_publisher(ROSImage, 'processed_image', 10)
        self.pub_bool = self.create_publisher(Bool, 'art_all', 10)
        self.pub_str = self.create_publisher(String, 'art_all_str', 10)

        self.catch_thief_2_pub = self.create_publisher(Bool, 'catch_thief_2', 10)
        self.thief_position_pub = self.create_publisher(PointStamped, 'thief_position', 10)
        self.rgb_pub = self.create_publisher(ROSImage, 'rgb_processed', 1)

        self.rgb_info_sub = self.create_subscription(
            CameraInfo,
            'oakd/rgb/camera_info',
            self.rgb_camera_info_callback,
            10
        )

        self.depth_info_sub = self.create_subscription(
            CameraInfo,
            'oakd/stereo/camera_info',
            self.depth_camera_info_callback,
            10
        )

        self.security_sub = self.create_subscription(
            Bool,
            '/api/security_status',
            self.security_status_callback,
            10
        )

        self.catch_thief_8_sub = self.create_subscription(
            Bool,
            '/robot8/catch_thief_8',
            self.catch_thief_8_callback,
            10
        )

        self.trigger2_sub = self.create_subscription(
            Int32,
            'arrive_position',
            self.trigger2_callback,
            10
        )

        self.rgb_sub = Subscriber(
            self,
            CompressedImage,
            'oakd/rgb/image_raw/compressed',
            qos_profile=self.video_qos,
            callback_group=self.main_group
        )

        self.depth_sub = Subscriber(
            self,
            ROSImage,
            'oakd/stereo/image_raw',
            qos_profile=self.video_qos,
            callback_group=self.main_group
        )

        self.ts = ApproximateTimeSynchronizer(
            [self.rgb_sub, self.depth_sub],
            queue_size=15,
            slop=2.0
        )
        self.ts.registerCallback(self.synced_callback)

        self.get_logger().info("🚀 노드 초기화 완료 및 동기화 대기 중...")

    def safe_info(self, msg: str):
        if not self.is_shutting_down and rclpy.ok():
            self.get_logger().info(msg)

    def safe_warn(self, msg: str):
        if not self.is_shutting_down and rclpy.ok():
            self.get_logger().warn(msg)

    def safe_error(self, msg: str):
        if not self.is_shutting_down and rclpy.ok():
            self.get_logger().error(msg)

    def safe_publish(self, publisher, msg):
        if self.is_shutting_down or not rclpy.ok():
            return
        try:
            publisher.publish(msg)
        except Exception as e:
            self.safe_warn(f"publish 실패: {e}")

    def load_items_from_server(self, table_name):
        fallback = [
            {"id": "C001", "name": "pig"},
            {"id": "C002", "name": "ball"},
        ]

        try:
            url = f"{SERVER}/items/{table_name}"
            res = self.session.get(url, timeout=2)
            res.raise_for_status()
            data = res.json()

            parsed = []

            if isinstance(data, dict):
                items = data.get("items", [])
            elif isinstance(data, list):
                items = data
            else:
                self.safe_warn(f"DB 응답 형식 이상: {type(data)}")
                return fallback

            for item in items:
                if not isinstance(item, dict):
                    continue
                item_id = item.get("id")
                item_name = item.get("name")
                if item_id and item_name:
                    parsed.append({"id": item_id, "name": item_name})

            return parsed if parsed else fallback

        except Exception as e:
            self.safe_warn(f"DB 읽기 실패 → fallback 사용: {e}")
            return fallback

    def find_item_name(self, item_id, default_name):
        for item in self.turtle_items:
            if item["id"] == item_id:
                return item["name"]
        return default_name

    def security_status_callback(self, msg: Bool):
        if msg.data and not self.detecting:
            self.safe_info("🔒 감지 시작")
            self.detecting = True
            self.trigger = True
            self.arrive_position_triggered = None
            self.detected_accum.clear()
            self.missing_sent = {1: False, 2: False}
            self.catch_thief_2_sent = False
            self.last_valid_pose = None
            self.last_valid_pose_time = 0.0

        elif (not msg.data) and self.detecting:
            self.safe_warn("🔓 감지 중지")
            self.detecting = False
            self.trigger = True
            self.catch_thief_2_sent = False

    def catch_thief_8_callback(self, msg: Bool):
        self.catch_thief_8 = msg.data

        if self.catch_thief_8:
            self.safe_warn("⛔ robot8이 먼저 catch_thief_8=True → 우리 쪽 도둑 좌표 계산 중단")
        else:
            self.safe_info("✅ catch_thief_8=False → 우리 쪽 도둑 좌표 계산 가능")
            self.catch_thief_2_sent = False

    def rgb_camera_info_callback(self, msg: CameraInfo):
        with self.lock:
            if self.K_rgb is None:
                self.K_rgb = np.array(msg.k).reshape(3, 3)
                self.rgb_frame_id = msg.header.frame_id
                self.safe_info(f"📸 RGB Camera Info 로드 완료 | frame={self.rgb_frame_id}")

                if self.rgb_info_sub is not None:
                    self.destroy_subscription(self.rgb_info_sub)
                    self.rgb_info_sub = None
                    self.safe_info("🛑 RGB CameraInfo 구독 해제")

    def depth_camera_info_callback(self, msg: CameraInfo):
        with self.lock:
            if self.K_depth is None:
                self.K_depth = np.array(msg.k).reshape(3, 3)
                self.depth_frame_id = msg.header.frame_id
                self.safe_info(f"📸 Depth Camera Info 로드 완료 | frame={self.depth_frame_id}")

                if self.depth_info_sub is not None:
                    self.destroy_subscription(self.depth_info_sub)
                    self.depth_info_sub = None
                    self.safe_info("🛑 Depth CameraInfo 구독 해제")

    def trigger2_callback(self, msg: Int32):
        if msg.data in [1, 2]:
            self.arrive_position_triggered = msg.data
            self.safe_info(f"📍 arrive_position={msg.data} 수신 → TARGET[{msg.data - 1}] 비교 예약")
        else:
            self.safe_warn(f"⚠️ 잘못된 trigger 값: {msg.data}")

    def synced_callback(self, rgb_msg, depth_msg):
        if self.is_shutting_down:
            return

        if not self.detecting:
            return

        now = time.time()
        if now - self.last_detect_time < self.detect_interval:
            return
        self.last_detect_time = now

        try:
            np_arr = np.frombuffer(rgb_msg.data, np.uint8)
            rgb_image = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

            if rgb_image is None:
                self.safe_error("RGB 디코딩 실패")
                return

            depth_image = self.bridge.imgmsg_to_cv2(
                depth_msg,
                desired_encoding='passthrough'
            )
            frame_id = depth_msg.header.frame_id

        except Exception as e:
            self.safe_error(f"이미지 변환 실패: {e}")
            return

        self.process_integrated_logic(
            rgb_image=rgb_image,
            depth_image=depth_image,
            frame_id=frame_id,
            depth_stamp_msg=depth_msg.header.stamp
        )

    def process_integrated_logic(self, rgb_image, depth_image, frame_id, depth_stamp_msg):
        if self.is_shutting_down:
            return

        if self.K_rgb is None and self.K_depth is None:
            self.safe_warn("CameraInfo 대기 중...")
            return

        try:
            results = self.model.predict(
                rgb_image,
                conf=0.3,
                verbose=False,
                device='cpu'
            )
        except Exception as e:
            self.safe_error(f"YOLO 추론 실패: {e}")
            return

        try:
            vis = rgb_image.copy()
            best_thief = None
            best_conf = -1.0

            for r in results:
                if r.boxes is None:
                    continue

                for box in r.boxes:
                    label = self.model.names[int(box.cls[0])]
                    conf = float(box.conf[0])

                    if label == 'thief' and conf > best_conf:
                        best_conf = conf
                        best_thief = box
                        self.trigger = False

            if self.trigger:
                detected_artifacts = []

                for r in results:
                    if r.boxes is None:
                        continue

                    for box in r.boxes:
                        cls = int(box.cls[0])
                        label = self.model.names[cls]
                        conf = float(box.conf[0])
                        x1, y1, x2, y2 = map(int, box.xyxy[0])

                        if label in self.TARGET_OBJECTS and conf > 0.5:
                            detected_artifacts.append(label)

                            cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 255, 0), 2)
                            cv2.putText(
                                vis,
                                f"{label}:{conf:.2f}",
                                (x1, y1 - 10),
                                cv2.FONT_HERSHEY_SIMPLEX,
                                0.6,
                                (0, 255, 0),
                                2
                            )

                self.detected_accum.update(detected_artifacts)

                if self.arrive_position_triggered is not None:
                    target_idx = self.arrive_position_triggered - 1

                    if not (0 <= target_idx < len(self.TARGET)):
                        self.arrive_position_triggered = None
                        self.detected_accum.clear()
                        return

                    target_name = self.TARGET[target_idx][0]
                    missing_items = [] if target_name in self.detected_accum else [target_name]

                    self.safe_publish(self.pub_bool, Bool(data=(len(missing_items) == 0)))

                    if missing_items:
                        self.safe_publish(
                            self.pub_str,
                            String(data=json.dumps(missing_items, ensure_ascii=False))
                        )

                        if not self.missing_sent.get(self.arrive_position_triggered, False):
                            threading.Thread(
                                target=self.send_missing_log,
                                args=(missing_items,),
                                daemon=True
                            ).start()
                            self.missing_sent[self.arrive_position_triggered] = True


                            self.safe_warn(f"🚨 목표 유물 미검출 → 즉시 도둑 탐지 모드 전환: {missing_items}")
                            self.trigger = False
                            self.arrive_position_triggered = None
                            self.detected_accum.clear()
                            return
                    else:
                        self.missing_sent[self.arrive_position_triggered] = False

                    self.arrive_position_triggered = None
                    self.detected_accum.clear()

            else:
                if best_thief is not None:
                    c_x, c_y, _, _ = best_thief.xywh[0].tolist()
                    center_point = (int(c_x), int(c_y))
                    x1, y1, x2, y2 = map(int, best_thief.xyxy[0].tolist())

                    self.safe_info(f"🕵️ 도둑 bbox 감지 | conf={best_conf:.2f}, center={center_point}")

                    cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 0, 255), 2)
                    cv2.circle(vis, center_point, 5, (0, 255, 255), -1)
                    cv2.putText(
                        vis,
                        f"thief {best_conf:.2f}",
                        (x1, max(y1 - 10, 20)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.7,
                        (0, 0, 255),
                        2
                    )

                    if self.catch_thief_8:
                        self.safe_warn("⛔ catch_thief_8=True 상태라 도둑 좌표 계산 생략")
                    else:
                        self.safe_info("📍 도둑 좌표 계산 시작")
                        pt_map = self.compute_thief_pose(
                            depth=depth_image,
                            center_point=center_point,
                            frame_id=frame_id,
                            depth_stamp_msg=depth_stamp_msg
                        )

                        if pt_map is not None:
                            if not self.catch_thief_2_sent:
                                self.safe_publish(self.catch_thief_2_pub, Bool(data=True))
                                self.catch_thief_2_sent = True
                                self.safe_info("✅ catch_thief_2=True 최초 발행")

                            self.last_valid_pose = pt_map
                            self.last_valid_pose_time = time.time()
                            self.safe_publish(self.thief_position_pub, pt_map)

                            if time.time() - self.last_publish_time > 0.2:
                                self.safe_info(
                                    f"📍 도둑 좌표: X={pt_map.point.x:.2f}, "
                                    f"Y={pt_map.point.y:.2f}, Z={pt_map.point.z:.2f}"
                                )
                                self.last_publish_time = time.time()
                        else:
                            self.safe_warn("⚠️ 도둑 bbox는 감지됐지만 map 좌표 계산 실패")
                            self.republish_last_pose("좌표 계산 실패")
                else:
                    self.republish_last_pose("도둑 미검출")

            try:
                ros_img = self.bridge.cv2_to_imgmsg(vis, encoding='bgr8')
                self.safe_publish(self.pub_image, ros_img)
                self.safe_publish(self.rgb_pub, ros_img)
            except Exception as e:
                self.safe_warn(f"이미지 publish 실패: {e}")

            self.enqueue_frame(vis, f"{self.robot_name}_cam2")

        except Exception as e:
            self.safe_error(f"process_integrated_logic 실패: {e}")

    def compute_thief_pose(self, depth, center_point, frame_id, depth_stamp_msg):
        try:
            x, y = center_point
            h, w = depth.shape[:2]

            if not (0 <= x < w and 0 <= y < h):
                self.safe_warn(f"center out of depth bounds | center=({x},{y}) depth_size=({w},{h})")
                return None

            patch = depth[
                max(0, y - 4):min(h, y + 5),
                max(0, x - 4):min(w, x + 5)
            ]

            valid = patch[(patch > 200) & (patch < 5000)]

            if valid.size == 0:
                self.safe_warn("유효 depth 없음")
                return None

            z = float(np.median(valid)) / 1000.0

            if not (0.2 < z < 5.0):
                self.safe_warn(f"z 범위 오류: {z:.3f} m")
                return None

            K_to_use = self.K_depth if self.K_depth is not None else self.K_rgb
            fx, fy = K_to_use[0, 0], K_to_use[1, 1]
            cx, cy = K_to_use[0, 2], K_to_use[1, 2]

            pt_camera = PointStamped()
            pt_camera.header.frame_id = frame_id
            pt_camera.header.stamp = depth_stamp_msg
            pt_camera.point.x = (x - cx) * z / fx
            pt_camera.point.y = (y - cy) * z / fy
            pt_camera.point.z = z

            try:
                tf_time = Time.from_msg(depth_stamp_msg)
                transform = self.tf_buffer.lookup_transform(
                    'map',
                    frame_id,
                    tf_time,
                    timeout=Duration(seconds=1.5)
                )
                return do_transform_point(pt_camera, transform)

            except Exception as e_exact:
                self.safe_warn(f"TF exact lookup 실패: {e_exact}")

            try:
                transform = self.tf_buffer.lookup_transform(
                    'map',
                    frame_id,
                    Time(),
                    timeout=Duration(seconds=1.5)
                )
                pt_camera.header.stamp = Time().to_msg()
                return do_transform_point(pt_camera, transform)

            except Exception as e_latest:
                self.safe_error(f"TF latest lookup도 실패: {e_latest}")
                return None

        except Exception as e:
            self.safe_error(f"compute_thief_pose 실패: {e}")
            return None

    def republish_last_pose(self, reason: str):
        now = time.time()

        if self.last_valid_pose is not None and now - self.last_valid_pose_time < self.pose_hold_time:
            self.safe_publish(self.thief_position_pub, self.last_valid_pose)
        else:
            self.safe_warn(f"{reason} → 재전송할 유효 좌표 없음")

    def enqueue_frame(self, frame, cam_id):
        try:
            if self.upload_queue.full():
                try:
                    _ = self.upload_queue.get_nowait()
                    self.upload_queue.task_done()
                except queue.Empty:
                    pass

            self.upload_queue.put_nowait((cam_id, frame.copy()))

        except Exception as e:
            self.safe_warn(f"enqueue_frame 실패: {e}")

    def upload_worker(self):
        while True:
            if self.is_shutting_down:
                break

            try:
                cam_id, frame = self.upload_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            try:
                _, buffer = cv2.imencode(
                    '.jpg',
                    frame,
                    [int(cv2.IMWRITE_JPEG_QUALITY), 70]
                )

                response = self.session.post(
                    self.server_url,
                    files={"file": ("frame.jpg", buffer.tobytes(), "image/jpeg")},
                    data={"cam_id": cam_id},
                    timeout=2
                )
                response.raise_for_status()

            except Exception as e:
                self.safe_warn(f"upload_worker 실패: {e}")

            finally:
                self.upload_queue.task_done()

    def send_missing_log(self, payload):
        try:
            response = self.session.post(
                self.turtle_log_url,
                json=payload,
                timeout=1
            )
            response.raise_for_status()
            self.safe_warn(f"📝 missing 로그 전송 완료: {payload}")
        except Exception as e:
            self.safe_warn(f"send_missing_log 실패: {e}")

    def destroy_node(self):
        self.is_shutting_down = True

        try:
            cv2.destroyAllWindows()
        except Exception:
            pass

        super().destroy_node()


def main():
    rclpy.init()

    node = IntegratedYOLONode(robot_ns='/robot2')
    executor = MultiThreadedExecutor()
    executor.add_node(node)

    try:
        executor.spin()

    except KeyboardInterrupt:
        pass

    finally:
        try:
            executor.shutdown()
        except Exception:
            pass

        try:
            node.destroy_node()
        except Exception:
            pass

        try:
            if rclpy.ok():
                rclpy.shutdown()
        except Exception:
            pass


if __name__ == '__main__':
    main()