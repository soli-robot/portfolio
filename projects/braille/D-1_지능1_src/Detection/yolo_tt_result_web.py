#!/usr/bin/env python3

# ==========================================================
# 🔥 기본 라이브러리
# ==========================================================
import time
import json
import requests
import threading
import queue
import cv2

import rclpy
from rclpy.node import Node
from cv_bridge import CvBridge 
from sensor_msgs.msg import Image
from std_msgs.msg import Bool, String

from ultralytics import YOLO

# ==========================================================
# 🔥 서버 설정
# ==========================================================
SERVER = "http://192.168.108.60:5000"

# 👉 YOLO ON/OFF 상태 변수 (전역)
detecting = False


# ==========================================================
# 🔥 서버에서 감지 상태 가져오는 쓰레드 함수
# ==========================================================
def check_security_status():
    global detecting

    while True:
        try:
            res = requests.get(f"{SERVER}/api/security_status")
            data = res.json()

            if data["security_active"] and not detecting:
                print("🔒 감지 시작")
                detecting = True

            elif not data["security_active"] and detecting:
                print("🔓 감지 중지")
                detecting = False

        except Exception as e:
            print("❌ 서버 연결 실패:", e)

        time.sleep(2)


# ==========================================================
# 🔥 YOLO ROS2 노드
# ==========================================================
class YOLOWebcamArtifact(Node):

    def __init__(self, cam1_index, cam2_index):
        super().__init__('yolo_webcam_artifact')

        # ==================================================
        # 🔥 DB에서 유물 리스트 가져오기
        # ==================================================
        self.TARGET_OBJECTS = self.load_items_from_server("web_items")
        self.get_logger().info(f"📦 유물 리스트: {self.TARGET_OBJECTS}")

        # 👉 DB 갱신 시간
        self.last_db_time = time.time()

        # ==================================================
        # 🔥 상태 변수
        # ==================================================
        self.detected_accum = set()   # 1초 동안 누적된 탐지 결과
        self.prev_missing = set()
        self.last_check_time = time.time()

        self.thief_detected_prev = False

        # ==================================================
        # 🔥 서버 주소
        # ==================================================
        self.server_url = f"{SERVER}/upload"
        self.list_url = f"{SERVER}/api/update_detected"

        # ==================================================
        # 🔥 YOLO 모델
        # ==================================================
        self.model = YOLO('/home/parkjieon/Downloads/best.pt')

        # ==================================================
        # 🔥 카메라
        # ==================================================
        self.cap1 = self.init_camera(cam1_index)
        self.cap2 = self.init_camera(cam2_index)

        # ==================================================
        # 🔥 ROS Publisher
        # ==================================================
        self.bridge = CvBridge()

        # 영상
        self.pub_image1 = self.create_publisher(Image, 'processed_image_cam1', 10)
        self.pub_image2 = self.create_publisher(Image, 'processed_image_cam2', 10)

        # 유물 상태
        self.pub_bool = self.create_publisher(Bool, 'art_all', 10)  # 모든 유물 존재 여부
        self.pub_str = self.create_publisher(String, 'art_all_lis', 10)  # 유물 리스트

        # 도둑
        self.pub_thief = self.create_publisher(Bool, 'thief_detected', 10)

        # 👉 추가된 토픽들
        self.pub_detected = self.create_publisher(String, 'detected_objects', 10)  # 현재 탐지된 객체

        # ==================================================
        # 🔥 업로드 쓰레드
        # ==================================================
        self.session = requests.Session()
        self.upload_queue = queue.Queue(maxsize=4)

        self.worker_thread = threading.Thread(
            target=self.upload_worker,
            daemon=True
        )
        self.worker_thread.start()


        # ==================================================
        # 🔥 메인 루프 (1초마다 실행)
        # ==================================================
        self.timer = self.create_timer(1, self.process_frame)

        self.get_logger().info("🔥 YOLO Node Started")

    # ==========================================================
    # 🔹 DB에서 유물 리스트 가져오기
    # ==========================================================
    def load_items_from_server(self, table_name):
        try:
            url = f"{SERVER}/items/{table_name}"
            res = requests.get(url, timeout=2)
            data = res.json()
            return set(data["items"])
        except:
            return {'hand', 'frog', 'bubble', 'flower', 'heart'}

    # ==========================================================
    # 🔹 카메라 초기화
    # ==========================================================
    def init_camera(self, index):
        cap = cv2.VideoCapture(index)

        if not cap.isOpened():
            raise RuntimeError(f"Camera {index} error")

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        return cap

    # ==========================================================
    # 🔹 프레임 읽기
    # ==========================================================
    def read_frames(self):
        ret1, frame1 = self.cap1.read()
        ret2, frame2 = self.cap2.read()

        if not ret1 or not ret2:
            return None, None

        return frame1, frame2

    # ==========================================================
    # 🔹 YOLO 탐지
    # ==========================================================
    def detect_objects(self, frame):
        detected = []
        detected_all = []

        results = self.model(frame, imgsz=640, conf=0.5, verbose=False)

        for r in results:
            for box in r.boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                cls = int(box.cls[0])
                label = self.model.names[cls]

                detected_all.append(label)

                if label in self.TARGET_OBJECTS:
                    detected.append(label)

                # 바운딩 박스 그리기
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                cv2.putText(frame, label, (x1, y1 - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

        return list(set(detected)), list(set(detected_all)), frame

    # ==========================================================
    # 🔹 이미지 업로드 쓰레드
    # ==========================================================
    def upload_worker(self):
        while rclpy.ok():
            try:
                cam_id, frame = self.upload_queue.get(timeout=1.0)

                _, buffer = cv2.imencode('.jpg', frame)

                self.session.post(
                    self.server_url,
                    files={"file": buffer.tobytes()},
                    data={"cam_id": cam_id},
                    timeout=2
                )

                self.upload_queue.task_done()

            except:
                continue

    # ==========================================================
    # 🔥 메인 루프
    # ==========================================================
    def process_frame(self):
        global detecting

        if not detecting:
            return

        frame1, frame2 = self.read_frames()
        if frame1 is None:
            return

        # ==================================================
        # 🔥 YOLO 탐지
        # ==================================================
        d1, all1, frame1 = self.detect_objects(frame1)
        d2, all2, frame2 = self.detect_objects(frame2)

        detected_total = list(set(d1 + d2))
        detected_all = list(set(all1 + all2))

        current_time = time.time()

        if self.upload_queue.qsize() <= 2:
            self.upload_queue.put(("cam1", frame1))
            self.upload_queue.put(("cam2", frame2))

        # ==================================================
        # 🔥 DB 주기 갱신 (5초)
        # ==================================================
        if current_time - self.last_db_time >= 5:
            self.TARGET_OBJECTS = self.load_items_from_server("web_items")
            self.get_logger().info(f"🔄 DB 갱신: {self.TARGET_OBJECTS}")
            self.last_db_time = current_time

        # ==================================================
        # 🔥 도둑 탐지
        # ==================================================
        if 'thief' in detected_all and not self.thief_detected_prev:
            # 👉 ROS로만 전송 (서버 전송 없음)
            self.pub_thief.publish(Bool(data=True))
            self.thief_detected_prev = True
        elif 'thief' not in detected_all:
            self.thief_detected_prev = False

        # ==================================================
        # 🔥 유물 존재 체크 (1초 기준)
        # ==================================================
        self.detected_accum.update(detected_total)

        if current_time - self.last_check_time >= 1:

            # 👉 현재 없는 유물
            missing = set(obj for obj in self.TARGET_OBJECTS if obj not in self.detected_accum)

            # 👉 전체 상태 기준으로 서버 전송
            if missing:
                self.get_logger().warn(f"🚨 유물 없음: {list(missing)}")

                requests.post(
                    self.list_url,
                    json={"missing": list(missing)},
                    timeout=1
                )

            # 👉 모든 유물 존재 여부 publish
            all_exist = len(missing) == 0
            self.pub_bool.publish(Bool(data=all_exist))

            # 👉 detected 리스트 publish
            self.pub_detected.publish(
                String(data=json.dumps(detected_total))
            )

            # 초기화
            self.detected_accum.clear()
            self.last_check_time = current_time

        # ==================================================
        # 🔹 ROS 이미지 publish
        # ==================================================
        self.pub_image1.publish(self.bridge.cv2_to_imgmsg(frame1, "bgr8"))
        self.pub_image2.publish(self.bridge.cv2_to_imgmsg(frame2, "bgr8"))

        # ==================================================
        # 🔹 화면 출력
        # ==================================================
        cv2.imshow("cam1", frame1)
        cv2.imshow("cam2", frame2)

        if cv2.waitKey(1) == ord('q'):
            rclpy.shutdown()

    # ==========================================================
    # 🔹 종료 처리
    # ==========================================================
    def destroy_node(self):
        self.cap1.release()
        self.cap2.release()
        cv2.destroyAllWindows()
        super().destroy_node()


# ==========================================================
# 🔥 실행
# ==========================================================
def main():
    rclpy.init()

    # 👉 서버 상태 확인 쓰레드
    thread = threading.Thread(target=check_security_status, daemon=True)
    thread.start()

    node = YOLOWebcamArtifact(cam1_index=4, cam2_index=2)

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass

    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()