import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime
import os

class FirebaseStoreLogUploader:
    def __init__(self):
        # 1. Firebase 인증 및 초기화
        # 현재 실행 중인 파일(example.py)의 절대 경로 폴더(C:\rokey\javis)를 자동으로 가져옵니다.
        current_dir = os.path.dirname(os.path.abspath(__file__))

        # 안전하게 폴더 경로를 이어 붙입니다.
        key_path = os.path.join(current_dir, "resource", "rokey2-e9270-firebase-adminsdk-fbsvc-c831c80eb6.json")

        
        try:
            cred = credentials.Certificate(key_path)
            if not firebase_admin._apps:
                firebase_admin.initialize_app(cred)
            self.db = firestore.client()
            print('✅ Firebase Admin SDK 초기화 성공!')
        except Exception as e:
            print(f'❌ Firebase 초기화 실패: {e}')
            self.db = None

    def upload_log(self, prompt, data_code, error_message=None):
        """
        데이터 코드와 에러 메시지를 파이어베이스에 업로드합니다.
        """
        if self.db is None:
            print("데이터베이스가 초기화되지 않았습니다.")
            return

        # 2. 파이어베이스에 올릴 데이터 구조 딕셔너리 생성
        # 파이어베이스 Admin SDK는 파이썬의 datetime 객체를 Firestore의 타임스탬프로 자동 변환합니다.
        log_data = {
            'timestamp': datetime.now(),
            'prompt': prompt,
            'data_code': data_code,
            'is_error': bool(error_message), # 에러 메시지가 있으면 True, 없으면 False
        }

        # 에러 메시지가 있을 경우에만 데이터에 추가
        if error_message:
            log_data['error_message'] = error_message

        try:
            # 3. 'system_logs' 컬렉션에 새 문서(자동 ID)로 데이터 추가
            update_time, doc_ref = self.db.collection('CoderLLM_Logs').add(log_data)
            
            if error_message:
                print(f"🚨 에러 로그 업로드 완료 [문서 ID: {doc_ref.id}] - 코드: {data_code}")
            else:
                print(f"📊 정상 데이터 업로드 완료 [문서 ID: {doc_ref.id}] - 코드: {data_code}")
                
        except Exception as e:
            print(f"❌ 데이터 업로드 중 파이어베이스 통신 에러 발생: {e}")

if __name__ == '__main__':
    # 업로더 인스턴스 생성
    uploader = FirebaseStoreLogUploader()
    
    print("\n--- 📝 테스트 업로드 시작 ---")

    # 상황 1: 정상적인 데이터 코드 업로드 (에러 없음)
    uploader.upload_log(prompt='파이썬 헬로우 코드를 작성해주는 코드', data_code='print("Hello, World!")')
    
    # 상황 2: 경고 수준의 데이터 코드 업로드
    uploader.upload_log(prompt=' 헬로드 작성하는 코드', data_code='#include <stdio.h> int main() { printf("Hello, World!\n");return 0;}', error_message='오류코드')