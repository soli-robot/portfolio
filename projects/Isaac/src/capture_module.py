import os
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from cv_bridge import CvBridge
import cv2
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SAVE_DIR = os.path.join(BASE_DIR, "test_image")

class StereoAllCameraCaptureNode(Node):
    def __init__(self):
        super().__init__('stereo_all_camera_capture_node')
        self.bridge = CvBridge()
        os.makedirs(SAVE_DIR, exist_ok=True)
        
        self.camera_topics = {
            'front_camera': '/front_stereo_camera/left/image_raw',
            'back_camera': '/back_stereo_camera/left/image_raw',
            'left_camera': '/left_stereo_camera/left/image_raw',
            'right_camera': '/right_stereo_camera/left/image_raw'
        }
        
        self.captured_flags = {cam: False for cam in self.camera_topics.keys()}
        self.subscribers = []
        
        self.get_logger().info("📸 [Vision] 4방향 스테레오 캡처 준비 완료...")

        for cam_name, topic in self.camera_topics.items():
            sub = self.create_subscription(
                Image, topic, 
                lambda msg, name=cam_name: self.image_callback(msg, name), 10
            )
            self.subscribers.append(sub)

    def image_callback(self, msg, cam_name):
        if self.captured_flags[cam_name]: return

        try:
            cv_image = self.bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')
            if cv_image.shape[1] != 640 or cv_image.shape[0] != 480:
                cv_image = cv2.resize(cv_image, (640, 480), interpolation=cv2.INTER_LINEAR)
            
            save_path = os.path.join(SAVE_DIR, f"{cam_name}_captured.jpg")
            cv2.imwrite(save_path, cv_image)
            
            self.get_logger().info(f"🟢 [{cam_name}] 640x480 저장 완료! ➔ {save_path}")
            self.captured_flags[cam_name] = True
        except Exception as e:
            self.get_logger().error(f"❌ [{cam_name}] 시각 데이터 변환 실패: {e}")

    def all_captured(self):
        return all(self.captured_flags.values())

def capture_all_images():
    """
    4방향 카메라를 캡처하고 저장된 파일 경로들을 딕셔너리로 반환합니다.
    """
    if not rclpy.ok(): rclpy.init()

    node = StereoAllCameraCaptureNode()
    timeout_sec = 10.0 
    start_time = time.time()
    
    saved_files = {} # 대뇌로 보낼 경로 저장용 딕셔너리
    
    try:
        while rclpy.ok() and not node.all_captured():
            rclpy.spin_once(node, timeout_sec=0.1)
            if time.time() - start_time > timeout_sec:
                print(f"⚠️ [경고] 타임아웃 발생.")
                break
    except KeyboardInterrupt:
        print("\n🛑 캡처 임무 비상 중단.")
    finally:
        if node.all_captured():
            print("🏁 [Vision] 4방향 캡처 성공!")
            for cam in node.camera_topics.keys():
                saved_files[cam] = os.path.join(SAVE_DIR, f"{cam}_captured.jpg")
                
        node.destroy_node()
        # 여러 번 호출될 수 있으므로 rclpy.shutdown()은 생략합니다.
        
    return saved_files

if __name__ == "__main__":
    result = capture_all_images()
    print("캡처 결과:", result)