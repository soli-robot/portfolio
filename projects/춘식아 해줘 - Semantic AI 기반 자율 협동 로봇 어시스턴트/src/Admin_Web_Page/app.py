from flask import Flask, jsonify, render_template
from flask_cors import CORS
import torch
import torch.nn as nn
import os
import threading
import time
from datetime import datetime
from collections import deque
from pymodbus.client import ModbusTcpClient
import pandas as pd
import numpy as np
import requests

import firebase_admin
from firebase_admin import credentials, firestore
from google.oauth2 import service_account
from google.auth.transport.requests import Request

app = Flask(__name__)
CORS(app)

# ────────────────────────────────────────────────────────
# ☁️ [클라우드 연동 레이어] Firebase Firestore & SQL Data Connect
# ────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KEY_PATH = os.path.join(BASE_DIR, "resource", "rokey2-e9270-firebase-adminsdk-fbsvc-c831c80eb6.json")

# 1. Firestore 초기화 (1시간 요약 통계 저장용)
try:
    if not firebase_admin._apps:
        cred = credentials.Certificate(KEY_PATH)
        firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("🔥 [Firebase] Firestore 데이터베이스 연동 성공!")
except Exception as e:
    db = None
    print(f"⚠️ [Firebase] Firestore 초기화 실패: {e}")

# 2. Firebase SQL Data Connect 클래스 (텔레메트리 데이터용)
class M0609SQLDataConnectUploader:
    def __init__(self):
        self.credentials = service_account.Credentials.from_service_account_file(
            KEY_PATH, 
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        self.url = "https://firebasedataconnect.googleapis.com/v1beta/projects/rokey2-e9270/locations/asia-northeast3/services/rokey2-e9270-service/connectors/example:executeMutation"

    def upload_hourly_summary(self, variables):
        if not self.credentials.valid:
            self.credentials.refresh(Request())
        
        payload = {"operationName": "InsertM0609Telemetry", "variables": variables}
        headers = {"Authorization": f"Bearer {self.credentials.token}", "Content-Type": "application/json"}
        
        try:
            res = requests.post(self.url, json=payload, headers=headers, timeout=5)
            if res.status_code == 200:
                print("🔥 [Firebase SQL] 1시간 요약 데이터 클라우드 적재 성공!")
                return True
            else:
                print(f"❌ [Firebase SQL 응답 에러]: {res.text}")
                return False
        except Exception as e:
            print(f"🚨 [Firebase SQL 통신 예외]: {e}")
            return False

firebase_uploader = M0609SQLDataConnectUploader()

# ────────────────────────────────────────────────────────
# 🧱 [AI 레이어] 1D-CNN + LSTM 하이브리드 예지보전 모델
# ────────────────────────────────────────────────────────
class RobotPredictiveModel(nn.Module):
    def __init__(self, input_dim=12, sequence_length=30):
        super(RobotPredictiveModel, self).__init__()
        self.conv1d = nn.Conv1d(in_channels=input_dim, out_channels=32, kernel_size=3, padding=1)
        self.relu = nn.ReLU()
        self.lstm = nn.LSTM(input_size=32, hidden_size=64, num_layers=2, batch_first=True)
        
        self.fc_classifier = nn.Sequential(
            nn.Linear(64, 16),
            nn.ReLU(),
            nn.Linear(16, 1),
            nn.Sigmoid()
        )
        self.fc_regressor = nn.Sequential(
            nn.Linear(64, 16),
            nn.ReLU(),
            nn.Linear(16, 1)
        )

    def forward(self, x):
        x = x.transpose(1, 2)
        x = self.relu(self.conv1d(x))
        x = x.transpose(1, 2)
        lstm_out, _ = self.lstm(x)
        last_hidden = lstm_out[:, -1, :]
        return self.fc_classifier(last_hidden), self.fc_regressor(last_hidden)

# AI 모델 로드
MODEL_PATH = os.path.join(BASE_DIR, "resource", "1DCNNLSTM_hybrid_predictive_model.pth")
ai_model = RobotPredictiveModel(input_dim=12, sequence_length=30)
if os.path.exists(MODEL_PATH):
    ai_model.load_state_dict(torch.load(MODEL_PATH, map_location=torch.device('cpu')))
    ai_model.eval()

# ────────────────────────────────────────────────────────
# 📡 [인프라 레이어] 실시간 Modbus TCP 로깅 및 스캔 엔진
# ────────────────────────────────────────────────────────
ROBOT_IP = "192.168.1.100"   
GRIPPER_IP = "192.168.1.1"   
MODBUS_PORT = 502
telemetry_history = deque(maxlen=30)

latest_robot_telemetry = {
    "version": "0.0.0",
    "robotState": "INITIALIZING",
    "servoOnRobot": False,
    "emergencyStopped": False,
    "safetyStopped": False,
    "jointPosition": [0.0] * 6,
    "jointVelocity": [0.0] * 6,
    "jointMotorCurrent": [0.0] * 6,
    "jointMotorTemperature": [0.0] * 6,
    "jointTorque": [0.0] * 6,
    "taskPosition": [0.0, 0.0, 0.0],
    "taskOrientation": [0.0, 0.0, 0.0],
    "taskVelocity": [0.0, 0.0, 0.0],
    "taskExternalForce": [0.0, 0.0, 0.0],
    "robot_modbus_status": "disconnected",
    "gripper_modbus_status": "disconnected"
}

def to_signed_16bit(val):
    return val - 65536 if val > 32767 else val

# CSV 로깅 설정
LOG_DIR = os.path.join(BASE_DIR, "resource", "modbus_logs")
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

modbus_log_buffer = []
last_flush_time = time.time()

def flush_buffer_to_csv():
    global modbus_log_buffer, last_flush_time
    if not modbus_log_buffer:
        last_flush_time = time.time()
        return
        
    try:
        current_timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_name = f"{current_timestamp_str}_modbus_raw.csv"
        file_path = os.path.join(LOG_DIR, file_name)
        
        # 1. 로컬 디스크에 CSV 덤프
        df = pd.DataFrame(modbus_log_buffer)
        df.to_csv(file_path, index=False, encoding='utf-8-sig')
        print(f"💾 [Modbus 가드] 주기적 로그 백업 완료 -> {file_path}")

        # 2. 클라우드 요약 데이터 연산 및 업로드
        if not df.empty:
            import ast
            def parse_arr(x):
                try: return ast.literal_eval(x)
                except: return [0.0] * 6

            j3_temps = [parse_arr(x)[2] for x in df['joint_temperature'] if len(parse_arr(x)) > 2]
            j3_torques = [parse_arr(x)[2] for x in df['joint_torque'] if len(parse_arr(x)) > 2]
            
            error_count = int(df['emergency_stopped'].sum())
            avg_j3_temp = float(np.mean(j3_temps)) if j3_temps else 0.0
            max_j3_torque = float(np.max(j3_torques)) if j3_torques else 0.0
            robot_status_val = str(df.iloc[-1]['robot_state'])

            # [A] Firestore 요약 테이블 적재 (프론트엔드 좌측 테이블용)
            if db is not None:
                firestore_summary = {
                    "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    "robot_status": robot_status_val,
                    "error_count": error_count,
                    "avg_j3_temperature": avg_j3_temp,
                    "max_j3_torque": max_j3_torque,
                    "ai_anomaly_loss": 0.0398
                }
                db.collection(u'robot_hourly_stats').document(current_timestamp_str).set(firestore_summary)

            # [B] SQL Data Connect 적재 (프론트엔드 우측 테이블용)
            sql_variables = {
                "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                "robotId": "m0609_hourly_summary",
                "version": "1.0.0",
                "robotState": robot_status_val,
                "servoOnRobot": True,
                "emergencyStopped": bool(df['emergency_stopped'].any()),
                "safetyStopped": bool(df['safety_stopped'].any()),
                "directTeachButtonPressed": False,
                "powerButtonPressed": True,
                "jointPosition": [0.0] * 6,
                "jointVelocity": [0.0] * 6,
                "jointMotorCurrent": [0.0] * 6,
                "jointMotorTemperature": [0.0, 0.0, avg_j3_temp, 0.0, 0.0, 0.0],
                "jointTorque": [0.0, 0.0, max_j3_torque, 0.0, 0.0, 0.0],
                "taskPosition": [0.0] * 3,
                "taskOrientation": [0.0] * 3,
                "taskVelocity": [0.0] * 3,
                "taskAngularVelocity": [0.0] * 3,
                "toolOffsetLength": [0.0] * 3,
                "toolOffsetDegree": [0.0] * 3,
                "taskExternalForce": [0.0] * 3,
                "taskExternalMoment": [0.0] * 3
            }
            firebase_uploader.upload_hourly_summary(sql_variables)

    except Exception as e:
        print(f"🚨 [데이터 덤프 에러] CSV/클라우드 업로드 실패: {e}")
    finally:
        modbus_log_buffer.clear()
        last_flush_time = time.time()

# Modbus 백그라운드 스레드
def modbus_background_scan_loop():
    global latest_robot_telemetry, modbus_log_buffer, last_flush_time
    
    robot_state_map = {
        0: "BACKDRIVE HOLD", 1: "BACKDRIVE RELEASE", 2: "BACKDRIVE RELEASE by COCKPIT",
        3: "SAFE OFF", 4: "INITIALIZING", 5: "INTERRUPTED", 6: "EMERGENCY STOP",
        7: "AUTO MEASURE", 8: "RECOVERY STANDBY", 9: "RECOVERY JOGGING",
        10: "RECOVERY HANDGUIDING", 11: "MANUAL STANDBY", 12: "MANUAL JOGGING",
        13: "MANUAL HANDGUIDING", 14: "HIGH PRIORITY RUNNING", 15: "STANDALONE STANDBY",
        16: "STANDALONE RUNNING", 17: "COLLABORATIVE STANDBY", 18: "COLLABORATIVE RUNNING",
        19: "HANDGUIDING CONTROL STANDBY"
    }
    
    while True:
        client_robot = ModbusTcpClient(ROBOT_IP, port=MODBUS_PORT)
        if client_robot.connect():
            latest_robot_telemetry["robot_modbus_status"] = "connected"
            try:
                res_j = client_robot.read_holding_registers(address=256, count=125, slave=255)
                res_t = client_robot.read_holding_registers(address=400, count=36, slave=255)
                
                if not res_j.isError() and not res_t.isError():
                    sj = [to_signed_16bit(x) for x in res_j.registers]
                    st = [to_signed_16bit(x) for x in res_t.registers]
                    
                    j_curr = [float(sj[34+i]) for i in range(6)]
                    j_temp = [float(sj[44+i]) for i in range(6)]
                    
                    latest_robot_telemetry.update({
                        "version": f"{sj[0]}.{sj[1]}.{sj[2]}", 
                        "robotState": robot_state_map.get(sj[3], f"STATE_CODE({sj[3]})"),
                        "servoOnRobot": bool(sj[4]),
                        "emergencyStopped": bool(sj[5]),
                        "safetyStopped": bool(sj[6]),
                        "jointPosition": [sj[14+i] / 10.0 for i in range(6)],
                        "jointVelocity": [sj[24+i] / 10.0 for i in range(6)],
                        "jointMotorCurrent": j_curr,
                        "jointMotorTemperature": j_temp,
                        "jointTorque": [sj[54+i] / 10.0 for i in range(6)],
                        "taskPosition": [st[0]/10.0, st[1]/10.0, st[2]/10.0],
                        "taskOrientation": [st[3]/10.0, st[4]/10.0, st[5]/10.0],
                        "taskVelocity": [st[10]/10.0, st[11]/10.0, st[12]/10.0],
                        "taskExternalForce": [float(st[30]), float(st[31]), float(st[32])]
                    })
                    
                    telemetry_history.append(j_curr + j_temp)
                    
                    current_record = {
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"),
                        "robot_state": latest_robot_telemetry["robotState"],
                        "servo_on": latest_robot_telemetry["servoOnRobot"],
                        "emergency_stopped": latest_robot_telemetry["emergencyStopped"],
                        "safety_stopped": latest_robot_telemetry["safetyStopped"],
                        "joint_position": str(latest_robot_telemetry["jointPosition"]),
                        "joint_velocity": str(latest_robot_telemetry["jointVelocity"]),
                        "joint_current": str(latest_robot_telemetry["jointMotorCurrent"]),
                        "joint_temperature": str(latest_robot_telemetry["jointMotorTemperature"]),
                        "joint_torque": str(latest_robot_telemetry["jointTorque"]),
                        "task_position": str(latest_robot_telemetry["taskPosition"]),
                        "task_orientation": str(latest_robot_telemetry["taskOrientation"])
                    }
                    modbus_log_buffer.append(current_record)
            except Exception as e:
                latest_robot_telemetry["robot_modbus_status"] = "disconnected"
            finally:
                client_robot.close()
        else:
            latest_robot_telemetry["robot_modbus_status"] = "disconnected"

        client_gripper = ModbusTcpClient(GRIPPER_IP, port=MODBUS_PORT)
        if client_gripper.connect():
            latest_robot_telemetry["gripper_modbus_status"] = "connected"
            client_gripper.close()
        else:
            latest_robot_telemetry["gripper_modbus_status"] = "disconnected"

        # (테스트용) 주기 도달 시 플러시 진행 - 실사용 시 3600(1시간)으로 변경
        if time.time() - last_flush_time >= 3600: 
            flush_buffer_to_csv()

        time.sleep(1.0)

scan_thread = threading.Thread(target=modbus_background_scan_loop, daemon=True)
scan_thread.start()

# ────────────────────────────────────────────────────────
# 📡 3. REST API 라우터 (대시보드 화면 및 데이터 엔드포인트)
# ────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/predictive-maintenance', methods=['GET'])
def get_integrated_dashboard_data():
    """실시간 모니터링 탭을 위한 AI 및 Modbus 텔레메트리 통합 전송"""
    try:
        inferred_prob = 0.0398
        if len(telemetry_history) == 30:
            input_tensor = torch.tensor([list(telemetry_history)], dtype=torch.float32)
            with torch.no_grad():
                raw_prob, _ = ai_model(input_tensor)
                inferred_prob = float(raw_prob.item())

        calculated_health_j3 = max(10, min(100, int(100 - (inferred_prob * 100))))

        joint_rul_data = []
        components_map = ["베어링", "감속기", "감속기", "베어링", "감속기", "베어링"]
        health_baselines = [92, 88, calculated_health_j3, 95, 78, 97]
        rul_baselines = [2340, 1980, 3560, 3120, 890, 3560]

        for i in range(6):
            joint_rul_data.append({
                "joint": f"J{i+1}",
                "angle": latest_robot_telemetry["jointPosition"][i],
                "velocity": latest_robot_telemetry["jointVelocity"][i],
                "torque": latest_robot_telemetry["jointTorque"][i],
                "current": latest_robot_telemetry["jointMotorCurrent"][i],       
                "temperature": latest_robot_telemetry["jointMotorTemperature"][i], 
                "health": health_baselines[i],
                "remainingHours": rul_baselines[i],
                "component": components_map[i],
                "status": "warning" if health_baselines[i] < 85 else "good"
            })

        task_space_data = {
            "coordinate": latest_robot_telemetry["taskPosition"],
            "orientation": latest_robot_telemetry["taskOrientation"],
            "linearVelocity": latest_robot_telemetry["taskVelocity"],
            "externalForce": latest_robot_telemetry["taskExternalForce"]
        }

        historical_risk_trend = [
            {"time": "00:00", "risk": 2}, {"time": "04:00", "risk": 3},
            {"time": "08:00", "risk": 5}, {"time": "12:00", "risk": 8},
            {"time": "16:00", "risk": int(12 + (inferred_prob * 15))}, 
            {"time": "20:00", "risk": 6}, {"time": "23:59", "risk": 3}
        ]

        connected_count = 1 if latest_robot_telemetry["robot_modbus_status"] == "connected" else 0
        if latest_robot_telemetry["gripper_modbus_status"] == "connected": connected_count += 1

        ai_diagnosis = {
            "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "severity": "warning" if calculated_health_j3 < 85 else "good",
            "summary": "M0609 및 onRobot 핵심 노드 패킷 통합 스캔 완료",
            "details": f"엔진 진단 결과: J3 관절 열화 확률 {inferred_prob*100:.1f}%\n물리적 레지스터 기반 시스템 펌웨어 버전 트래킹 완료.",
            "recommendedAction": "요주의 점검" if calculated_health_j3 < 85 else "정상 가동"
        }

        return jsonify({
            "status": "success",
            "kpiMetrics": {
                "online_devices": f"{connected_count} / 2",
                "robot_state": latest_robot_telemetry["robotState"],
                "robot_version": f"v{latest_robot_telemetry['version']}", 
                "master_active": "ACTIVE" if connected_count > 0 else "OFFLINE"
            },
            "infrastructureNodes": [
                {"name": "M0609 #1", "ip": ROBOT_IP, "status": latest_robot_telemetry["robot_modbus_status"], "signal": 98},
                {"name": "onRobot #1", "ip": GRIPPER_IP, "status": latest_robot_telemetry["gripper_modbus_status"], "signal": 95}
            ],
            "jointRULData": joint_rul_data,
            "taskSpaceData": task_space_data,
            "historicalRiskTrend": historical_risk_trend,
            "aiDiagnosis": ai_diagnosis
        }), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ────────────────────────────────────────────────────────
# 📡 4. 파이어스토어 & SQL Data Connect 로그 조회 API
# ────────────────────────────────────────────────────────
@app.route('/api/logs', methods=['GET'])
def get_cloud_logs():
    """클라우드 로그 조회 탭을 위한 통합 데이터 서빙 (TaskLog 포함)"""
    try:
        firestore_logs = []
        sql_logs = []
        coder_llm_logs = []
        task_logs = [] # ✨ 신규: M0609TaskLog 데이터를 담을 리스트
        
        # 1. Firestore 1시간 요약 로그 조회
        if db is not None:
            try:
                docs = db.collection(u'robot_hourly_stats').order_by(u'timestamp', direction=firestore.Query.DESCENDING).limit(10).stream()
                for doc in docs:
                    firestore_logs.append(doc.to_dict())
            except Exception as e:
                print(f"⚠️ [Firestore Hourly Stats Read Error] {e}")

            # Firestore CoderLLM_Logs 조회
            try:
                coder_docs = db.collection(u'CoderLLM_Logs').limit(10).stream()
                for doc in coder_docs:
                    data = doc.to_dict()
                    data['id'] = doc.id
                    coder_llm_logs.append(data)
            except Exception as e:
                print(f"⚠️ [Firestore CoderLLM_Logs Read Error] {e}")

        # 2. SQL Data Connect 텔레메트리 (에지 로컬 캐시 사용)
        # 2. SQL Data Connect 텔레메트리 (에지 로컬 캐시 사용)
        try:
            import ast
            recent_buffer = list(modbus_log_buffer)[-10:]
            for record in reversed(recent_buffer):
                # 기존 태스크 공간 좌표 파싱
                task_pos_str = record.get("task_position", "[0.0, 0.0, 0.0]")
                try: task_pos = ast.literal_eval(task_pos_str)
                except: task_pos = [0.0, 0.0, 0.0]

                # ✨ [신규 추가] 조인트 공간 값, 전류, 온도 데이터 역직렬화 파싱
                joint_pos_str = record.get("joint_position", "[0.0] * 6")
                joint_curr_str = record.get("joint_current", "[0.0] * 6")
                joint_temp_str = record.get("joint_temperature", "[0.0] * 6")
                
                try: joint_pos = ast.literal_eval(joint_pos_str)
                except: joint_pos = [0.0] * 6
                try: joint_curr = ast.literal_eval(joint_curr_str)
                except: joint_curr = [0.0] * 6
                try: joint_temp = ast.literal_eval(joint_temp_str)
                except: joint_temp = [0.0] * 6

                sql_logs.append({
                    "timestamp": str(record.get("timestamp", "")).split('.')[0],
                    "version": latest_robot_telemetry.get("version", "1.0.0"),
                    "robotState": record.get("robot_state", "UNKNOWN"),
                    "servoOnRobot": record.get("servo_on", False),
                    "emergencyStopped": record.get("emergency_stopped", False),
                    "taskPosition": task_pos,
                    "jointPosition": joint_pos,       # ✨ 추가
                    "jointCurrent": joint_curr,       # ✨ 추가
                    "jointTemperature": joint_temp     # ✨ 추가
                })
        except Exception as e:
            print(f"⚠️ [SQL Cache Read Error] {e}")

        # 3. ✨ 신규: SQL Data Connect - M0609TaskLog 조회
        try:
            if not firebase_uploader.credentials.valid:
                firebase_uploader.credentials.refresh(Request())
            
            # 💡 주의: 파이어베이스 GraphQL 스키마에 GetRecentTaskLogs 쿼리가 정의되어 있어야 합니다.
            payload_tasks = {
                "operationName": "GetRecentTaskLogs", 
                "variables": {"limit": 10}
            }
            headers = {
                "Authorization": f"Bearer {firebase_uploader.credentials.token}", 
                "Content-Type": "application/json"
            }
            
            res_tasks = requests.post(firebase_uploader.url.replace("executeMutation", "executeQuery"), json=payload_tasks, headers=headers, timeout=5)
            if res_tasks.status_code == 200:
                # GraphQL 응답에서 m0609TaskLogs 배열 추출 (스키마 정의에 따라 이름이 다를 수 있음)
                task_logs = res_tasks.json().get("data", {}).get("m0609TaskLogs", [])
            else:
                print(f"⚠️ [SQL TaskLog Read Error] {res_tasks.text}")
        except Exception as e:
            print(f"⚠️ [SQL TaskLog Exception] {e}")

        return jsonify({
            "status": "success",
            "firestoreLogs": firestore_logs,
            "sqlLogs": sql_logs,
            "coderLlmLogs": coder_llm_logs,
            "taskLogs": task_logs # ✨ 반환 데이터에 추가
        }), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# app.py 파일의 가장 밑바닥에 이 코드가 있어야 합니다!
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)