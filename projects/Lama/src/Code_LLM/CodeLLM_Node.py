import os
import time
from datetime import datetime
import re
import json
import asyncio
import subprocess
import websockets
import ollama
import firebase_admin
from firebase_admin import credentials, firestore
from concurrent.futures import ProcessPoolExecutor

import integrated_scan_module as integrated_scan_module # 💡 1번에서 만든 스캔 모듈 임포트

import gesture_control_module # 💡 1번에서 만든 제스처 모듈 임포트
# ============================================================
# 1. 기본 설정 및 경량 시스템 프롬프트 (qwen_coder 로직 완벽 유지)
# ============================================================ 
MODEL_NAME = "qwen2.5-coder:7b"

SAVE_DIR = os.path.expanduser("~/cobot_generated")
LATEST_FILENAME = "latest_generated_robot_task.py"

SYSTEM_PROMPT = """
You generate only the body of perform_task() for a Doosan M0609 robot.

Rules:
1. Output only Python statements. No markdown. No explanations.
2. Do not output imports.
3. Do not define functions.
4. Do not output def perform_task().
5. Use only these commands:
   - movej([j1, j2, j3, j4, j5, j6], vel=VELOCITY, acc=ACC)
   - movel(posx([x, y, z, rx, ry, rz]), vel=VELOCITY, acc=ACC)
   - open_gripper()
   - close_gripper()
   - wait(seconds)
6. For movej, use the raw joint list directly.
7. For movel, always wrap the coordinate with posx([x, y, z, rx, ry, rz]).
8. For movel, use the provided roll, pitch, yaw values as rx, ry, rz.
9. Do not use fixed orientation values such as 150, 179, 150 unless they are explicitly provided in the user step.
10. Do not use set_digital_output.
11. Use normal spaces only. Do not use non-breaking spaces.
12. Return only the robot motion statements in the same order as the user steps.
13. Never write ```python or ```.
14. Never output movel([x, y, z], vel=VELOCITY, acc=ACC); always use movel(posx([...]), vel=VELOCITY, acc=ACC).

For action=movej, you MUST include vel=VELOCITY and acc=ACC.
Never output movej(target) without velocity and acceleration.

For action=movel, if the target is [x, y, z, rx, ry, rz], output exactly:
movel(posx([x, y, z, rx, ry, rz]), vel=VELOCITY, acc=ACC)

For action=movel, if the target provides roll, pitch, yaw separately, use them as rx, ry, rz:
movel(posx([x, y, z, roll, pitch, yaw]), vel=VELOCITY, acc=ACC)

For action=open_gripper, output exactly:
open_gripper()

For action=close_gripper, output exactly:
close_gripper()
"""

ROBOT_CODE_TEMPLATE = """import rclpy
import DR_init
import time
from pick_and_place_text.onrobot import RG

ROBOT_ID = "dsr01"
ROBOT_MODEL = "m0609"
ROBOT_TOOL = "Tool Weight"
ROBOT_TCP = "GripperDA_v1"

VELOCITY = 40
ACC = 60

GRIPPER_NAME = "rg2"
TOOLCHANGER_IP = "192.168.1.1"
TOOLCHANGER_PORT = "502"
gripper = RG(GRIPPER_NAME, TOOLCHANGER_IP, TOOLCHANGER_PORT)

DR_init.__dsr__id = ROBOT_ID
DR_init.__dsr__model = ROBOT_MODEL

def initialize_robot():
    from DSR_ROBOT2 import set_tool, set_tcp, get_tool, get_tcp, ROBOT_MODE_MANUAL, ROBOT_MODE_AUTONOMOUS
    from DSR_ROBOT2 import get_robot_mode, set_robot_mode

    set_robot_mode(ROBOT_MODE_MANUAL)
    set_tool(ROBOT_TOOL)
    set_tcp(ROBOT_TCP)

    set_robot_mode(ROBOT_MODE_AUTONOMOUS)
    time.sleep(2.0)

def open_gripper():
    gripper.open_gripper()
    time.sleep(1.5)

def close_gripper():
    gripper.close_gripper()
    time.sleep(1.5)

def perform_task():
    from DSR_ROBOT2 import posx, movej, movel, wait

{task_body}

def main(args=None):
    rclpy.init(args=args)
    node = rclpy.create_node("move_basic", namespace=ROBOT_ID)

    DR_init.__dsr__node = node

    try:
        initialize_robot()
        perform_task()

    except KeyboardInterrupt:
        node.get_logger().info("Node interrupted by user. Shutting down...")
    except Exception as e:
        node.get_logger().error(f"An unexpected error occurred: {{e}}")
    finally:
        if rclpy.ok():
            rclpy.shutdown()

if __name__ == "__main__":
    main()
"""

# ============================================================
# 2. Firebase 업로더 클래스
# ============================================================
class FirebaseLogUploader:
    def __init__(self):
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(current_dir))
        key_path = '/home/rokey/Downloads/CodeLLM/rokey2-e9270-firebase-adminsdk-fbsvc-c831c80eb6.json'
        
        if not os.path.exists(key_path):
            key_path = os.path.join(project_root, "resource", "rokey2-e9270-firebase-adminsdk-fbsvc-c831c80eb6.json")
            
        try:
            cred = credentials.Certificate(key_path)
            if not firebase_admin._apps: 
                firebase_admin.initialize_app(cred)
            self.db = firestore.client()
            print('✅ Firebase 연결 성공')
        except Exception as e:
            print(f'❌ Firebase 실패: {e}')
            self.db = None

    def upload_log(self, prompt, code):
        if not self.db: return
        try:
            self.db.collection('CoderLLM_Logs').add({
                'timestamp': datetime.now(), 
                'prompt': prompt, 
                'data_code': code
            })
            print("☁️ 파이어베이스 업로드 완료!")
        except Exception as e:
            print(f"❌ 파이어베이스 에러: {e}")

uploader = FirebaseLogUploader()

# ============================================================
# 3. 로봇 코드 생성 (qwen_coder 핵심 로직 100% 보존)
# ============================================================
def call_ollama_chat(task_sequence: str) -> str:
    response = ollama.chat(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT.replace("\u00a0", " ")},
            {"role": "user", "content": task_sequence.strip()},
        ],
        options={"temperature": 0.0, "top_p": 1.0, "repeat_penalty": 1.05, "num_predict": 1024},
    )
    return response["message"]["content"]

def clean_task_body(text: str) -> str:
    body = text.strip()
    body = body.replace("\u00a0", " ")

    # markdown fence 전체 제거
    body = body.replace("```python", "")
    body = body.replace("```", "")
    body = body.strip()

    # 혹시 모델이 전체 코드 일부를 출력했으면 perform_task 내부만 추출
    if "def perform_task" in body:
        lines = body.splitlines()
        extracted = []
        inside = False

        for line in lines:
            stripped = line.strip()

            if stripped.startswith("def perform_task"):
                inside = True
                continue

            if inside:
                if stripped.startswith("def ") or stripped.startswith("if __name__"):
                    break
                extracted.append(line)

        body = "\n".join(extracted).strip()

    cleaned_lines = []

    for line in body.splitlines():
        stripped = line.strip()

        if not stripped:
            cleaned_lines.append("")
            continue

        if stripped.startswith("import "):
            continue
        if stripped.startswith("from "):
            continue
        if stripped.startswith("def "):
            continue
        if stripped.startswith("class "):
            continue
        if stripped.startswith("if __name__"):
            continue

        cleaned_lines.append(stripped)

    return "\n".join(cleaned_lines).strip()

def indent_task_body(task_body: str) -> str:
    task_body = task_body.replace("\u00a0", " ")
    if not task_body.strip(): return "    pass"
    indented_lines = []
    for line in task_body.splitlines():
        stripped = line.strip()
        if stripped:
            indented_lines.append("    " + stripped)
        else:
            indented_lines.append("")
    return "\n".join(indented_lines)


def fix_task_body_common_errors(task_body: str) -> str:
    fixed_lines = []

    for line in task_body.splitlines():
        stripped = line.strip()

        if not stripped:
            fixed_lines.append("")
            continue

        # markdown 코드블록 제거
        if stripped in ["```", "```python"]:
            continue

        # gripper 함수 호출 보정
        if stripped == "open_gripper":
            fixed_lines.append("open_gripper()")
            continue

        if stripped == "close_gripper":
            fixed_lines.append("close_gripper()")
            continue

        if stripped == "open_gripper(node)":
            fixed_lines.append("open_gripper()")
            continue

        if stripped == "close_gripper(node)":
            fixed_lines.append("close_gripper()")
            continue

        # movej([...]) 에 vel, acc가 없으면 자동 추가
        if stripped.startswith("movej(") and "vel=" not in stripped and "acc=" not in stripped:
            if stripped.endswith(")"):
                inner = stripped[len("movej("):-1]
                fixed_lines.append(f"movej({inner}, vel=VELOCITY, acc=ACC)")
                continue

        # movel([x, y, z], vel=..., acc=...) 를 posx로 보정
        match_movel_raw = re.match(
            r"^movel\(\s*\[([^\]]+)\]\s*,\s*vel\s*=\s*VELOCITY\s*,\s*acc\s*=\s*ACC\s*\)$",
            stripped,
        )

        if match_movel_raw:
            values_text = match_movel_raw.group(1)
            values = [v.strip() for v in values_text.split(",")]

            if len(values) == 3:
                x, y, z = values
                fixed_lines.append(
                    f"movel(posx([{x}, {y}, {z}, 150, 179, 150]), vel=VELOCITY, acc=ACC)"
                )
                continue

            if len(values) == 6:
                fixed_lines.append(
                    f"movel(posx([{', '.join(values)}]), vel=VELOCITY, acc=ACC)"
                )
                continue

        # movel(posx([x, y, z]), ...) 처럼 posx 안이 3개면 6개로 보정
        match_movel_posx = re.match(
            r"^movel\(\s*posx\(\s*\[([^\]]+)\]\s*\)\s*,\s*vel\s*=\s*VELOCITY\s*,\s*acc\s*=\s*ACC\s*\)$",
            stripped,
        )

        if match_movel_posx:
            values_text = match_movel_posx.group(1)
            values = [v.strip() for v in values_text.split(",")]

            if len(values) == 3:
                x, y, z = values
                fixed_lines.append(
                    f"movel(posx([{x}, {y}, {z}, 150, 179, 150]), vel=VELOCITY, acc=ACC)"
                )
                continue

            if len(values) == 6:
                fixed_lines.append(stripped)
                continue

        fixed_lines.append(stripped)

    return "\n".join(fixed_lines)


def assemble_full_code(task_body: str) -> str:
    cleaned_body = clean_task_body(task_body)
    fixed_body = fix_task_body_common_errors(cleaned_body)
    indented_body = indent_task_body(fixed_body)

    full_code = ROBOT_CODE_TEMPLATE.format(task_body=indented_body)
    full_code = full_code.replace("\u00a0", " ")
    return full_code.strip() + "\n"


def save_code(code: str) -> tuple[str, str]:
    os.makedirs(SAVE_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    timestamp_path = os.path.join(SAVE_DIR, f"generated_doosan_task_{timestamp}.py")
    latest_path = os.path.join(SAVE_DIR, LATEST_FILENAME)

    with open(timestamp_path, "w", encoding="utf-8") as f: f.write(code)
    with open(latest_path, "w", encoding="utf-8") as f: f.write(code)
    return timestamp_path, latest_path

def generate_robot_code(task_sequence: str) -> tuple[str, str, float]:
    print(f"--- [DEBUG] Received task_input: {task_sequence} ---")
    
    print(f"🚀 [{MODEL_NAME}] 모델이 perform_task 내부 코드를 생성 중입니다...")
    start_time = time.perf_counter()
    raw_task_body = call_ollama_chat(task_sequence)
    end_time = time.perf_counter()

    cleaned_task_body = clean_task_body(raw_task_body)
    fixed_task_body = fix_task_body_common_errors(cleaned_task_body)
    final_code = assemble_full_code(fixed_task_body)
    elapsed_time = end_time - start_time

    return final_code, fixed_task_body, elapsed_time

# ============================================================
# 4. 로봇 코드 실제 구동 로직
# ============================================================
def execute_robot_script(code_string):
    with open("temp_robot_run.py", "w", encoding="utf-8") as f:
        f.write(code_string)
        
    print("🤖 [4060] 승인된 최종 제어 코드 스크립트 실행 중...")
    result = subprocess.run(["python3", "temp_robot_run.py"], capture_output=True, text=True)
    
    if result.returncode == 0:
        return "SUCCESS", "✅ 실제 로봇 하드웨어 구동 작업 완료!"
    else:
        return "FAIL", f"🚨 제어 런타임 에러 발생: {result.stderr}"

# ============================================================
# 5. 웹소켓 양방향 비동기 서버 엔진
# ============================================================
async def run_scan_safely():
    loop = asyncio.get_running_loop()
    # 프로세스 풀을 이용해 ROS2 충돌 없이 독립적으로 함수를 실행하고 결과(Dict)만 받아옵니다.
    with ProcessPoolExecutor(max_workers=1) as pool:
        result_dict = await loop.run_in_executor(pool, integrated_scan_module.run_scan_module)
        return result_dict
    
async def run_gesture_safely():
    loop = asyncio.get_running_loop()
    with ProcessPoolExecutor(max_workers=1) as pool:
        result_dict = await loop.run_in_executor(pool, gesture_control_module.run_gesture_module)
        return result_dict

# ============================================================
# 5. 웹소켓 양방향 비동기 서버 엔진
# ============================================================
async def handle_pipeline(websocket):
    print(f"📡 5060 메인 컨트롤러 연결 수립 완료")
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                
                # 5060이 보낸 JSON에서 정보 추출
                action = data.get("action", "")
                msg_type = data.get("prompt", "")

                # ====================================================
                # 🤖 분기 1. Bringup (스캔 및 좌표 추출 요청)
                # ====================================================
                if action == "generate" and  msg_type == "[Bring-1]" :
                    target_obj = data.get('target', '전체')
                    print(f"👀 [Bringup 명령 수신] 타겟: {target_obj} 스캔 시작...")
                    
                    
                    try:
                        # 💡 CLI 명령어 대신, 위에서 만든 파이썬 모듈 함수를 직접 호출!
                        result_payload = await run_scan_safely()
                        
                        if result_payload:
                            await websocket.send(json.dumps({
                                "status": "scan_result",
                                "message": "스캔 완료!",
                                "data": result_payload
                            }))
                            print("📤 정제된 스캔 좌표를 5060으로 전송 완료!")
                        else:
                            await websocket.send(json.dumps({
                                "status": "error",
                                "message": "스캔된 객체가 없습니다."
                            }))
                            print("⚠️ 스캔 결과가 비어있습니다.")
                            
                    except Exception as e:
                        await websocket.send(json.dumps({
                            "status": "error",
                            "message": f"스캔 중 시스템 에러 발생: {str(e)}"
                        }))
                        print(f"❌ 스캔 모듈 실행 에러: {e}")

                # ====================================================
                # 🤖 분기 1.5. Bringup-2 (제스처 제어 가동)
                # ====================================================
                elif action == "generate" and msg_type == "[Bring-2]":
                    print("✋ [Bring-2] 제스처 모드 가동!")
                    await websocket.send(json.dumps({
                        "status": "info", 
                        "message": "제스처 제어 모드를 시작합니다. 손이 5초 이상 안 보이면 자동 종료됩니다."
                    }))
                    
                    try:
                        # 💡 모듈 인스턴스를 프로세스 풀에서 안전하게 실행!
                        result = await run_gesture_safely()
                        
                        print("🛑 제스처 모드 자동 종료 및 5060 보고 완료.")
                        await websocket.send(json.dumps({
                            "status": "result",
                            "exec_status": result.get("status", "SUCCESS"),
                            "message": "제스처 제어가 완료되어 원래 워크플로우로 복귀합니다."
                        }))
                        
                    except Exception as e:
                        print(f"❌ 제스처 모드 실행 에러: {e}")
                        await websocket.send(json.dumps({"status": "error", "message": str(e)}))
                # ====================================================
                # 📝 분기 2. Task (코드 생성 요청 - 기존 로직 100% 유지)
                # ====================================================
                elif action == "generate":
                    print(f"📝 [Task 명령 수신] 프롬프트를 바탕으로 코드를 작성합니다.")
                    
                    task_input = data.get('prompt') or data.get('task_input')
                    if not task_input:
                        task_input = json.dumps(data, ensure_ascii=False)
                    
                    task_input = str(task_input)
                    
                    generated_code, task_body, elapsed_time = generate_robot_code(task_input)
                    print(f"✅ 코드 생성 완료 ({elapsed_time:.3f}초)")
                    
                    save_code(generated_code)
                    uploader.upload_log(task_input, generated_code)
                    
                    response_payload = {"status": "draft", "code": generated_code}
                    await websocket.send(json.dumps(response_payload))
                    print("📤 생성된 코드를 5060으로 송신했습니다.")

                # ====================================================
                # 🚀 분기 3. Execute (승인된 코드 구동 요청 - 기존 로직 유지)
                # ====================================================
                elif action == "execute":
                    approved_code = data.get("code")
                    status, msg = execute_robot_script(approved_code)
                    
                    response_payload = {"status": "result", "exec_status": status, "message": msg}
                    await websocket.send(json.dumps(response_payload))
                    print(f"📤 작업 구동 결과 리포트: {status}")

                else:
                    print(f"⚠️ 알 수 없는 action 수신: {action}")

            except json.JSONDecodeError:
                print("🚨 [ERROR] 전달받은 메시지가 정상적인 JSON 포맷이 아닙니다.")
                
    except websockets.exceptions.ConnectionClosed:
        print("🛑 5060 메인 컨트롤러 연결 정상 종료")
    except Exception as e:
        print(f"🛑 예상치 못한 에러 발생: {e}")

async def main():
    print("🤖 4060 Coder-Node 웹소켓 통합 관제 서버 가동 (포트: 8888)...")
    try:
        async with websockets.serve(handle_pipeline, "0.0.0.0", 8888):
            await asyncio.get_running_loop().create_future()
    except asyncio.CancelledError:
        pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 안전하게 종료되었습니다.")