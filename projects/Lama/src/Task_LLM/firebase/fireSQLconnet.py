import requests
import json
import os
from google.oauth2 import service_account
from google.auth.transport.requests import Request

class FirebaseSQLconnetTaskUploader:
    def __init__(self):
        """
        클래스 생성 시 1회만 실행되며, 인증 정보를 초기화하고 엔드포인트를 설정합니다.
        """
        # 1. 서비스 계정 JSON 키 경로 동적 설정 (상위 폴더 탐색 구조로 변경)
        # =====================================================================
        # 현재 파일 위치: .../src/FireBaseDB/fireSQLconnet.py
        current_file_path = os.path.abspath(__file__)
        
        # 1단계 위: .../src/FireBaseDB
        firebase_db_dir = os.path.dirname(current_file_path) 
        
        # 2단계 위: .../src
        src_dir = os.path.dirname(firebase_db_dir)
        
        # 3단계 위: .../Jarvis_LLM_integration (최상위 프로젝트 폴더)
        project_root_dir = os.path.dirname(src_dir)

        # 최상위 폴더 아래의 resource 폴더로 경로 결합
        self.key_path = os.path.join(project_root_dir, "doosan_agent/resource", "rokey2-e9270-firebase-adminsdk-fbsvc-c831c80eb6.json")

        # 팩트 체크: 파일 존재 여부 검증
        if not os.path.exists(self.key_path):
            raise FileNotFoundError(
                f"❌ 서비스 계정 키 파일을 찾을 수 없습니다.\n"
                f"확인된 경로: {self.key_path}\n"
                f"resource 폴더 안에 JSON 파일명이 정확한지 확인해 주세요."
            )

        # 서비스 계정 자격 증명(Credentials) 로드
        self.credentials = service_account.Credentials.from_service_account_file(
            self.key_path,
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        
        print("🔑 [인증 준비] 구글 클라우드 보안 인증 객체가 성공적으로 생성되었습니다.")

        # =====================================================================
        # 2. Firebase SQL Connect 엔드포인트 세팅
        # =====================================================================
        self.LOCATION = "asia-northeast3"
        self.PROJECT_ID = "rokey2-e9270"
        self.CONNECTOR_NAME = "example"

        self.url = f"https://firebasedataconnect.googleapis.com/v1beta/projects/{self.PROJECT_ID}/locations/{self.LOCATION}/services/{self.PROJECT_ID}-service/connectors/{self.CONNECTOR_NAME}:executeMutation"

    def upload_log(self, voice_command, yolo_vision_data, llama_raw_output, target_node="4060_notebook_node"):
        """
        로봇의 동적 데이터를 인자로 받아 클라우드 PostgreSQL에 업로드합니다.
        """
        # 구글 API 인증에 사용할 Bearer Access Token 갱신 (만료 대비 안전장치)
        if not self.credentials.valid:
            self.credentials.refresh(Request())

        # =====================================================================
        # 3. 로봇 동적 데이터 구성
        # =====================================================================
        payload = {
            "operationName": "InsertRobotLog",
            "variables": {
                "voiceCommand": voice_command,
                "yoloVisionData": json.dumps(yolo_vision_data),  # 리스트/딕셔너리를 JSON 문자열로 변환
                "llamaRawOutput": llama_raw_output,
                "targetNode": target_node
            }
        }

        headers = {
            "Authorization": f"Bearer {self.credentials.token}",
            "Content-Type": "application/json"
        }

        # =====================================================================
        # 4. 클라우드 PostgreSQL 적재 테스트 실행
        # =====================================================================
        print("🚀 클라우드 PostgreSQL 데이터베이스로 실시간 로봇 로그 전송 중...")
        
        try:
            # 타임아웃(timeout)을 명시하여 로봇 제어 루프가 무한 대기(Blocking)하는 것을 방지
            response = requests.post(self.url, json=payload, headers=headers, timeout=5)

            if response.status_code == 200:
                print("✅ [성공] 데이터가 클라우드 PostgreSQL에 에러 없이 완벽하게 적재되었습니다!")
                print("서버 응답:", response.json())
                return True
            else:
                print(f"❌ [실패] HTTP 상태 코드: {response.status_code}")
                print("에러 상세 내용:", response.text)
                return False
                
        except requests.exceptions.RequestException as e:
            print(f"🚨 [통신 에러] PostgreSQL 서버와 통신 중 문제가 발생했습니다: {e}")
            return False

if __name__ == '__main__':
    # 테스트 실행부: 인스턴스 생성 및 업로드 메서드 호출
    print("\n--- 📝 파이어베이스 테스트 업로드 시작 ---")
    
    # 1. 인스턴스 생성
    uploader = FirebaseSQLconnetTaskUploader()
    
    # 2. 테스트용 더미 데이터
    test_yolo_results = [
        {"label": "blue_gear", "x": 300.5, "y": 100.2, "z": 50.0},
        {"label": "basket", "x": 400.0, "y": 300.0, "z": 100.0}
    ]
    test_command = "저기 파란색 기어 좀 바구니에 조립해줄래?"
    test_llama_out = "Step 1. 기어 좌표 [300.5,100.2,50.0] 이동 -> Step 2. 그리퍼 조작"
    
    # 3. 데이터 업로드 실행
    uploader.upload_log(
        voice_command=test_command,
        yolo_vision_data=test_yolo_results,
        llama_raw_output=test_llama_out
    )