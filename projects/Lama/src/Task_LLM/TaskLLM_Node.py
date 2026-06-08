import sys
import os
import ctypes


# =====================================================================
# 🔇 2. ALSA/JACK 로그 영구 차단 (C-level 에러 핸들러 덮어쓰기)
# =====================================================================
# 파이썬 경고 무시로는 안 되므로, 리눅스 오디오(ALSA)의 에러 출력 함수 자체를 백지로 만듭니다.
ERROR_HANDLER_FUNC = ctypes.CFUNCTYPE(None, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p)
def py_error_handler(filename, line, function, err, fmt):
    pass  # 아무것도 출력하지 않음
c_error_handler = ERROR_HANDLER_FUNC(py_error_handler)

try:
    asound = ctypes.cdll.LoadLibrary('libasound.so.2') # Ubuntu 표준
    asound.snd_lib_error_set_handler(c_error_handler)
except OSError:
    try:
        asound = ctypes.cdll.LoadLibrary('libasound.so')
        asound.snd_lib_error_set_handler(c_error_handler)
    except OSError:
        pass

os.environ['AUDIODRIVER'] = 'alsa'
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"

# --- 기존 경로 설정 ---
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.dirname(current_dir)
if src_dir not in sys.path:
    sys.path.append(src_dir)

import json
import re
import streamlit as st
import ollama
import websocket  # pip install websocket-client
import asyncio
import threading
import websockets # yolo 수신용

import speech_recognition as sr
from faster_whisper import WhisperModel

import time # time 모듈이 상단에 없다면 추가해 주세요.
import tempfile
import difflib

# 💡 모델을 매번 불러오지 않도록 캐싱 처리
@st.cache_resource
def load_whisper_model():
    print("⏳ [5060] Whisper 모델 로딩 중... (최초 1회만)")
    # VRAM 확보를 위해 device="cpu" 로 설정
    return WhisperModel("small", device="cpu", compute_type="int8")

def run_single_stt():
    recognizer = sr.Recognizer()
    
    # 1. 마이크 열고 딱 1번만 듣기
    with sr.Microphone() as source:
        # 주변 소음 적응 (0.5초)
        recognizer.adjust_for_ambient_noise(source, duration=0.5)
        # 최대 5초 대기, 말하기 시작하면 최대 5초까지만 녹음
        audio = recognizer.listen(source, timeout=5, phrase_time_limit=5)
        
    # 2. 녹음된 음성을 임시 파일로 저장
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
        tmp_file.write(audio.get_wav_data())
        tmp_filename = tmp_file.name
        
    # 3. Whisper로 텍스트 변환
    model = load_whisper_model()
    segments, _ = model.transcribe(tmp_filename, beam_size=5, language="ko")
    text = "".join([segment.text for segment in segments]).strip()
    
    # 4. 임시 파일 삭제 후 텍스트 반환
    os.remove(tmp_filename)
    return text


# PostgreSQL 인프라 연동 컴포넌트 임포트
try:
    from FireBaseDB.fireSQLconnet import FirebaseSQLconnetTaskUploader
except ImportError:
    st.warning("⚠️ DB 모듈을 찾을 수 없습니다. 테스트 모드로 진행합니다.")

# =====================================================================
# 🌐 [스레드 안전] yolo 감각 4060 PC에서 들어오는 실시간 전역 버퍼 클래스
# =====================================================================
class SensoryDataBuffer:
    def __init__(self):
        self.command = "대기 중... (4060 PC의 음성 명령을 기다립니다)"
        self.yolo = {}
        self.lock = threading.Lock()  # 데이터 동시 접근 방지용 안전장치

    def update(self, command, yolo):
        with self.lock:
            if command:
                self.command = command
            if yolo:
                self.yolo = yolo

    def get_data(self):
        with self.lock:
            return self.command, self.yolo

# 전역 객체 생성 (Streamlit의 상태와 무관하게 백그라운드에서 항상 접근 가능)
if 'global_buffer' not in st.session_state:
    st.session_state['global_buffer'] = SensoryDataBuffer()

global_sensor_buffer = st.session_state['global_buffer']





# =====================================================================
# 🚀 4060으로부터 데이터를 수신하는 백그라운드 웹소켓 서버
# =====================================================================
def run_ws_server_for_1650():
    async def handler(websocket):
        print("📡 [5060] 4060 감각 PC 연결 수립 완료!")
        try:
            async for message in websocket:
                data = json.loads(message)
                print(f"📥 [5060] 1650으로부터 데이터 수신: {data}")
                
                # 💡 [핵심 변경] 실제 들어오는 키(transformed_positions)를 잡아냅니다.
                cmd = data.get("command")
                # 만약 'yolo' 키로 들어오면 yolo를, 아니면 'transformed_positions'를 사용
                yolo = data.get("transformed_positions") or data.get("yolo") 
                
                global_sensor_buffer.update(cmd, yolo)
                    
        except websockets.exceptions.ConnectionClosed:
            print("🛑 [5060] 1650 감각 PC 연결 종료")

    async def main_server():
        async with websockets.serve(handler, "0.0.0.0", 8888):
            await asyncio.Future()

    asyncio.run(main_server())

@st.cache_resource
def start_background_server():
    print("🤖 [5060] 1650 수신용 백그라운드 서버 시작 (포트: 8888)")
    thread = threading.Thread(target=run_ws_server_for_1650, daemon=True)
    thread.start()
    return thread

class DualAgentControlCenter:
    def __init__(self):
        self.WSS_4060_URL = "ws://192.168.0.190:8888"

        # 1. Macro Prompt: 전체 작업의 큰 흐름(Sub-tasks) 자율 기획
        self.macro_prompt = """
        너는 두산 로봇 제어 프로젝트의 '총괄 디렉터'야.
        사용자의 음성 명령과 현재 YOLO 데이터를 분석하여 전체 작업(Sub-task)들을 순서대로 기획해.
        
        [사용 가능한 사내 제작 모듈 (Bringup 리스트)]
        - [Bring-1] (YOLO_Vision): 카메라를 켜고 특정 객체의 실시간 좌표를 계속 추적함
        - [Bring-2] (Gesture_Controll_Module): 사용자의 제스처를 인식하고 로봇을 제어함(욜로에서 객체가 탐지되지 않을때 사용)
    
        [가장 중요한 핵심 기획 로직: 선행 조건 판단 및 연속 작업 절대 규칙]
        1. 현재 주어진 YOLO 데이터에 사용자가 언급한 물체의 좌표가 없다면, [Task] 작업을 지시하기 전에 반드시 [Bring-1] 작업을 먼저 기획해.
        2. 여러 물체를 조작하거나 연속적인 작업을 수행할 때, 절대 모든 [Bring-1]을 1, 2단계 초반에 몰아서 배치하지 마. (Batching 금지)
        3. 모든 [Task]의 '바로 직전 스텝'에는 무조건 해당 타겟 물체를 스캔하는 [Bring-1]이 1:1로 짝지어져야 해. (예외 없음)
        4. 물리적 상태 변화 인지: 로봇이 물체를 한 번 조작([Task])하고 나면 그 물체의 이전 좌표는 무효화된다. 따라서 같은 물체를 다른 곳으로 다시 옮겨야 할 경우, **반드시 [Bring-1]을 다시 기획하여 변동된 위치를 새롭게 스캔한 후 다음 [Task]를 수행해야 해.**

        (올바른 다중/연속 작업 기획 흐름 예시: A 스캔 ➔ A 조작 ➔ B 스캔 ➔ B 조작 ➔ A 다시 스캔 ➔ A 2차 조작)

        [가장 중요한 규칙: 영어 강제 번역 (Language Rule)]
        사용자가 한국어로 명령하더라도(예: "하얀 수건 세탁기에 넣어"), JSON 배열의 'target'과 'destination' 값은 **반드시 영어로 번역해서(예: "white_towel", "washing_machine") 적어야 해!**
        카메라(YOLO)가 객체를 영어로만 인식하기 때문에 한국어가 들어가면 매칭이 실패해.
        
        [작업 타입 분류 규칙]
        - 모듈을 쓸 때는 'type'에 "[Bring-1]" 처럼 해당 태그만 단순하게 적어.
        - 로봇 물리 동작은 'type'에 "[Task]"를 적고 세부 action을 적어.
        
        [절대 규칙 - 무조건 지킬 것]
        1. 인사말, 설명 등 어떠한 자연어 텍스트도 절대 출력하지 마.
        2. 오직 아래 양식의 순수 JSON 배열(Array)만 딱 출력해.
        3. 'type' 필드의 값은 반드시 큰따옴표로 묶인 문자열이어야 해.
        
        [출력 양식 예시 JSON Array (사용자 명령: "하얀 수건 세탁기에 넣어줘"일 때)]
        [
            {"step": 1, "type": "[Bring-1]", "target": "white_towel"},
            {"step": 2, "type": "[Task]", "action": "pick_and_place", "target": "white_towel", "destination": "washing_machine"}
        ]
        """
        
        # 2. Micro Prompt
        self.micro_prompt = """
        You are the 'Detailed Motion Task Planner' for a Doosan M0609 robot.
        Your role is to convert the [Current Step Information] from the Director into a fixed-format detailed motion sequence that the Coder LLM can directly convert into robot control code.

        [Absolute Rules]
        1. Do not output explanations, greetings, analysis, comments, or any extra text.
        2. Output only in the exact format specified below.
        3. Never calculate, modify, infer, or invent coordinate values.
        4. Copy the provided coordinate values exactly as given.
        5. The target of every movel action must contain exactly 6 values: [x, y, z, rx, ry, rz].
        6. If any required coordinate is missing, empty, or [0, 0, 0], output only:
        ERROR: MISSING_COORDINATE
        7. For a pick_and_place task, always follow the exact 12-step sequence below.
        8. Do not add or remove any steps outside the required 12 steps.

        ====================================================
        [Case A: Bring Task]

        If the current step type is [Bring-1], output only the following single line:

        [Bring-1]

        ====================================================
        [Case B: pick_and_place Task]

        The input will provide the following coord values:
        - Task
        - Object
        - Target
        - Target_Offset
        - Destination
        - Destination_Offset

        For pick_and_place, always generate exactly the following 12 steps:

        1. Initialize: movej to [0, 0, 90, 0, 90, 0]
        2. Prepare: open_gripper
        3. Approach: movel to Target_Offset
        4. Descend: movel to Target
        5. Grasp: close_gripper
        6. Stabilize: wait for 0.5s
        7. Lift: movel to Target_Offset
        8. Transfer: movel to Destination_Offset
        9. Descend: movel to Destination
        10. Release: open_gripper
        11. Stabilize: wait for 0.5s
        12. Lift/Retract: movel to Destination_Offset

        [Required Output Format]

        Task: pick_and_place
        Object: <Object value>
        Steps:
        1. action=movej, target=[0, 0, 90, 0, 90, 0]
        2. action=open_gripper
        3. action=movel, target=<Target_Offset>
        4. action=movel, target=<Target>
        5. action=close_gripper
        6. action=wait, duration=0.5
        7. action=movel, target=<Target_Offset>
        8. action=movel, target=<Destination_Offset>
        9. action=movel, target=<Destination>
        10. action=open_gripper
        11. action=wait, duration=0.5
        12. action=movel, target=<Destination_Offset>

        [Example Input]

        Task: pick_and_place
        Object: white_towel
        Target: [417.7, -62.3, 96.2, 176.8, -145.0, -92.0]
        Target_Offset: [417.7, -62.3, 216.2, 176.8, -145.0, -92.0]
        Destination: [850.1, 13.7, 133.6, 176.8, -145.0, -92.0]
        Destination_Offset: [850.1, 13.7, 253.6, 176.8, -145.0, -92.0]

        [Example Output]

        Task: pick_and_place
        Object: white_towel
        Steps:
        1. action=movej, target=[0, 0, 90, 0, 90, 0]
        2. action=open_gripper
        3. action=movel, target=[417.7, -62.3, 216.2, 176.8, -145.0, -92.0]
        4. action=movel, target=[417.7, -62.3, 96.2, 176.8, -145.0, -92.0]
        5. action=close_gripper
        6. action=wait, duration=0.5
        7. action=movel, target=[417.7, -62.3, 216.2, 176.8, -145.0, -92.0]
        8. action=movel, target=[850.1, 13.7, 253.6, 176.8, -145.0, -92.0]
        9. action=movel, target=[850.1, 13.7, 133.6, 176.8, -145.0, -92.0]
        10. action=open_gripper
        11. action=wait, duration=0.5
        12. action=movel, target=[850.1, 13.7, 253.6, 176.8, -145.0, -92.0]
        ====================================================
        """
        
        # 3. Critic Prompt: 4060이 짠 코드를 검수
        self.critic_prompt = """
        너는 두산 로봇(m0609) 제어 코드 안전 검수관이야. 
        아래 코드를 읽고 문법 에러나 충돌 위험이 없으면 오직 "PASS"라고만 출력해.
        만약 에러가 예상되거나 허용되지 않은 함수를 썼다면 "FAIL: [구체적인 에러 원인 및 수정 지시]"를 출력해.
        """
        
        self.sql_uploader = None  # self._init_db_connector()

    @staticmethod
    @st.cache_resource
    def _init_db_connector():
        try:
            return FirebaseSQLconnetTaskUploader()
        except Exception as e:
            print(f"🚨 PostgreSQL 커넥터 초기화 실패: {e}")
            return None

    def log_to_sql(self, command_text: str, yolo_data_dict: dict, llama_output_text: str):
        if self.sql_uploader:
            self.sql_uploader.upload_log(
                voice_command=command_text,
                yolo_vision_data=yolo_data_dict,
                llama_raw_output=llama_output_text
            )

    # 👇 [여기에 추가!] 4060과 통신하고 '결과'를 받아오는 만능 함수
    def _send_to_4060(self, payload: dict):
        try:
            ws = websocket.create_connection(self.WSS_4060_URL, timeout=300)
            ws.send(json.dumps(payload))
            
            # 4060이 응답을 줄 때까지 기다렸다가 받음
            response_raw = ws.recv()
            ws.close()
            
            return json.loads(response_raw)
            
        except Exception as e:
            st.error(f"🚨 4060 통신 실패: {e}")
            return None

    # 4060 통신 및 검수 로직 (누락되었던 부분 복구)
    def _communicate_with_4060(self, final_prompt_for_4060):
        max_critic_retries = 3
        final_code = ""

        with st.spinner("📡 4060 코딩 생성 및 5060 감사관 검수 진행 중..."):
            for i in range(max_critic_retries):
                payload = {"action": "generate", "prompt": final_prompt_for_4060}
                response = self._send_to_4060(payload)
                if not response:
                    return None
                draft_code = response.get("code", "")

                # 5060 감사관 검수
                review_res = ollama.chat(model='llama3', messages=[
                    {'role': 'system', 'content': self.critic_prompt},
                    {'role': 'user', 'content': f"검수할 코드:\n{draft_code}"}
                ])
                review = review_res['message']['content']
                
                if "PASS" in review.upper():
                    st.success("✅ 5060 감사관 검수 통과! (무결성 확보)")
                    final_code = draft_code
                    break
                else:
                    st.warning(f"⚠️ 감사관 지적 사항 반영 중 ({i+1}/{max_critic_retries}): {review}")
                    final_prompt_for_4060 += f"\n\n[수정 지시]: {review}"

        return final_code if final_code else None

    def render_ui(self):
        st.title("🤖 ROS2 단계별 자율 제어 센터 (Step-by-Step)")

        start_background_server()

        st.subheader(" YOLO 좌표 수신부")
        
        # 새로고침과 초기화 버튼
        col1, col2, col3 = st.columns([1, 1, 2])
        with col1:
            if st.button("🔄 데이터 새로고침"):
                st.rerun()
        with col2:
            if st.button("🛑 전체 작업 초기화"):
                st.session_state['task_list'] = []
                st.session_state['current_step_idx'] = 0
                st.session_state['current_step_code'] = None
                st.session_state['yolo_data'] = None
                st.rerun()
                
        auto_refresh = st.toggle("🔄 실시간 자동 동기화 켜기 (Auto-Sync)", value=True)

        buf_command, buf_yolo = global_sensor_buffer.get_data()
        
        # ==========================================
        # 💡 [변경됨] 음성 명령 입력 UI (버튼형)
        # ==========================================
        st.divider()
        st.markdown("#### 🎙️ 로봇 음성 제어")
        
        col_mic, col_txt = st.columns([1, 4])
        with col_mic:
            if st.button("🎤 음성 명령 듣기"):
                with st.spinner("듣고 있습니다... 말씀해주세요!"):
                    try:
                        text = run_single_stt()
                        if text:
                            st.session_state['stt_command'] = text
                            st.success("인식 완료!")
                        else:
                            st.warning("목소리가 인식되지 않았습니다.")
                    except sr.WaitTimeoutError:
                        st.error("입력 시간이 초과되었습니다.")
                    except Exception as e:
                        st.error(f"마이크 에러: {e}")
                        
        # 텍스트 박스 기본값을 방금 인식한 STT 텍스트로 채움
        default_cmd = st.session_state.get('stt_command', buf_command)
        user_command = col_txt.text_input("현재 명령:", value=default_cmd)
        
        # ==========================================
        
        current_yolo = st.session_state.get('yolo_data') or buf_yolo
        default_yolo_str = json.dumps(current_yolo, indent=4, ensure_ascii=False) if current_yolo else "{}"
        
        yolo_input_str = st.text_area(
            "📷 현재 카메라 인식 상태:", 
            value=default_yolo_str,
            height=200
        )

        try:
            yolo_status = json.loads(yolo_input_str)
        except json.JSONDecodeError:
            st.error("⚠️ 올바른 JSON 형식이 아닙니다.")
            yolo_status = {}
            
        st.divider()

        # ==========================================
        # 💡 [핵심] 상태 기억(Session State) 변수 초기화
        # ==========================================
        if 'yolo_data' not in st.session_state:
            st.session_state['yolo_data'] = None
        if 'task_list' not in st.session_state:
            st.session_state['task_list'] = []
        if 'current_step_idx' not in st.session_state:
            st.session_state['current_step_idx'] = 0
        if 'current_step_code' not in st.session_state:
            st.session_state['current_step_code'] = None

        # ------------------------------------------
        # 1단계: 전체 작업 기획 (Macro) - 한 번만 실행됨
        # ------------------------------------------
        if not st.session_state['task_list']:
            if st.button("▶️ 1단계: 사용자 의도 분석 및 전체 Task 기획"):
                with st.spinner("🧠 총괄 디렉터가 큰 작업 순서를 기획 중..."):
                    try:
                        macro_res = ollama.chat(model='llama3', messages=[
                            {'role': 'system', 'content': self.macro_prompt},
                            {'role': 'user', 'content': f"명령: {user_command}\nYOLO 데이터: {yolo_status}"}
                        ])
                        
                        macro_output = macro_res['message']['content'].strip()
                        
                        def clean_and_parse(text):
                            # LLM이 값을 큰따옴표 없이 "type": [Task] 로 출력하는 경우 자동 복구
                            text = re.sub(r'"type"\s*:\s*\[([^\]]+)\]', r'"type": "[\1]"', text)
                            # 후행 쉼표 에러 방지
                            text = re.sub(r',\s*]', ']', text)
                            text = re.sub(r',\s*}', '}', text)
                            return json.loads(text)

                        task_list = None
                        
                        # 전략 1: 마크다운 블록(```json ... ```) 우선 추출
                        json_match = re.search(r'```(?:json)?\s*(.*?)\s*```', macro_output, re.DOTALL)
                        if json_match:
                            try:
                                task_list = clean_and_parse(json_match.group(1))
                            except Exception: pass

                        # 전략 2: 괄호 쌍 맞추기 (가장 확실한 방법)
                        if task_list is None:
                            start_idx = macro_output.find('[')
                            while start_idx != -1 and task_list is None:
                                count = 0
                                end_idx = -1
                                for i in range(start_idx, len(macro_output)):
                                    if macro_output[i] == '[':
                                        count += 1
                                    elif macro_output[i] == ']':
                                        count -= 1
                                        if count == 0:
                                            end_idx = i
                                            break
                                if end_idx != -1:
                                    candidate = macro_output[start_idx:end_idx+1]
                                    try:
                                        task_list = clean_and_parse(candidate)
                                    except Exception:
                                        pass
                                start_idx = macro_output.find('[', start_idx + 1)
                        
                        if not task_list or not isinstance(task_list, list):
                            raise ValueError("올바른 JSON 배열 형식을 찾거나 파싱하지 못했습니다.")
                            
                        # 💡 기획된 리스트를 메모리에 저장하고 화면 새로고침
                        st.session_state['task_list'] = task_list
                        st.session_state['current_step_idx'] = 0
                        st.rerun()
                        
                    except Exception as e:
                        st.error(f"🚨 기획 데이터 파싱 에러: {e}")
                        st.warning(f"원본 출력 데이터:\n{macro_output}")
        
        # ------------------------------------------
        # 2단계: 스텝 바이 스텝 (Micro ➔ 4060 ➔ 인간 승인)
        # ------------------------------------------
        else:
            task_list = st.session_state['task_list']
            current_idx = st.session_state['current_step_idx']

            st.success(f"✅ 총 {len(task_list)}개의 세부 작업 기획 완료!")
            st.json(task_list)
            st.divider()

            # 아직 완료되지 않은 스텝이 남아있다면
            if current_idx < len(task_list):
                current_task = task_list[current_idx]
                st.subheader(f"📍 현재 진행 스텝: {current_idx + 1} / {len(task_list)}")
                st.info(f"작업 유형: {current_task.get('type')} | 액션: {current_task.get('action')} | 대상: {current_task.get('target')}")

                if current_task.get('type') == "[Bring-1]":
                    if st.button("👁️ 로봇 스캔 시작"):
                        payload = {"action": "generate", "prompt": "[Bring-1]", "target": current_task.get('target')}
                        result = self._send_to_4060(payload)
                        if result and result.get("status") == "scan_result":
                            # 4060에서 받은 데이터를 yolo 데이터로 갱신
                            st.session_state['yolo_data'] = result.get("data")
                            st.success("✅ 스캔 완료!")
                            st.session_state['current_step_idx'] += 1
                            st.rerun()

                elif current_task.get('type') == "[Bring-2]":
                    if st.button("🖐️ 제스처 제어 시작"):
                        payload = {"action": "generate", "prompt": "[Bring-2]", "target": current_task.get('target')}
                        result = self._send_to_4060(payload)
                        if result and result.get("status") == "scan_result":
                            # 4060에서 받은 데이터를 yolo 데이터로 갱신
                            st.session_state['yolo_data'] = result.get("data")
                            st.success("✅ 제스처 제어 완료!")
                            st.session_state['current_step_idx'] += 1
                            st.rerun()

                elif current_task.get('type') == "[Task]":
                    # 2-1. 해당 스텝에 대한 코드가 아직 안 만들어졌을 때
                    if st.session_state['current_step_code'] is None:
                        if st.button(f"⚙️ 스텝 {current_idx + 1} 모션 플래닝 및 4060 코드 생성"):
                            # =========================================================
                            # 💡 1. 딕셔너리 구조에 맞춘 스마트 매칭 함수
                            # =========================================================
                            def get_smart_coord_key(target_name, yolo_dict):
                                if not target_name or not yolo_dict: return None
                                target_space = target_name.replace('_', ' ')
                                
                                # 1. 완벽 일치 검색
                                if target_name in yolo_dict: return target_name
                                if target_space in yolo_dict: return target_space
                                
                                # 2. 포함 관계 검색 (예: white_towel_1 찾기, offset은 제외)
                                keys_no_offset = [k for k in yolo_dict.keys() if "offset" not in k]
                                for key in keys_no_offset:
                                    if target_name in key or target_space in key:
                                        return key
                                        
                                # 3. 유사도 검색
                                matches = difflib.get_close_matches(target_name, keys_no_offset, n=1, cutoff=0.5)
                                if matches: return matches[0]
                                return None

                            action = current_task.get('action')
                            target = current_task.get('target', '')
                            dest = current_task.get('destination', '')

                            # 💡 2. Target 좌표 파싱 (4060이 보내준 딕셔너리 구조 파싱)
                            target_key = get_smart_coord_key(target, yolo_status)
                            if target_key and isinstance(yolo_status[target_key], dict):
                                d = yolo_status[target_key]
                                target_str = (
                                    f"[{round(d['x'],1)}, {round(d['y'],1)}, {round(d['z'],1)}, "
                                    f"{round(d['rx'],1)}, {round(d['ry'],1)}, {round(d['rz'],1)}]"
                                )
                                
                                # 4060이 보내준 _offset 데이터 바로 활용!
                                offset_key = target_key + "_offset"
                                if offset_key in yolo_status:
                                    od = yolo_status[offset_key]
                                    offset_str = (
                                        f"[{round(od['x'],1)}, {round(od['y'],1)}, {round(od['z'],1)}, "
                                        f"{round(od['rx'],1)}, {round(od['ry'],1)}, {round(od['rz'],1)}]"
                                    )
                                else:
                                    offset_str = (
                                        f"[{round(d['x'],1)}, {round(d['y'],1)}, {round(d['z']+100.0,1)}, "
                                        f"{round(d['rx'],1)}, {round(d['ry'],1)}, {round(d['rz'],1)}]"
                                    )
                            else:
                                target_str = "[0, 0, 0, 0, 0, 0]"
                                offset_str = "[0, 0, 100.0, 0, 0, 0]"

                            # 💡 3. Destination 좌표 파싱
                            dest_key = get_smart_coord_key(dest, yolo_status)
                            if dest_key and isinstance(yolo_status[dest_key], dict):
                                d = yolo_status[dest_key]
                                dest_str = (
                                    f"[{round(d['x'],1)}, {round(d['y'],1)}, {round(d['z'],1)}, "
                                    f"{round(d['rx'],1)}, {round(d['ry'],1)}, {round(d['rz'],1)}]"
                                )
                                
                                offset_key = dest_key + "_offset"
                                if offset_key in yolo_status:
                                    od = yolo_status[offset_key]
                                    dest_offset_str = (
                                        f"[{round(od['x'],1)}, {round(od['y'],1)}, {round(od['z'],1)}, "
                                        f"{round(od['rx'],1)}, {round(od['ry'],1)}, {round(od['rz'],1)}]"
                                    )
                                else:
                                    dest_offset_str = (
                                        f"[{round(d['x'],1)}, {round(d['y'],1)}, {round(d['z']+100.0,1)}, "
                                        f"{round(d['rx'],1)}, {round(d['ry'],1)}, {round(d['rz'],1)}]"
                                    )
                            else:
                                dest_str = "[0, 0, 0, 0, 0, 0]"
                                dest_offset_str = "[0, 0, 100.0, 0, 0, 0]"

                            # GUI 화면에 어떻게 들어가는지 띄워줌
                            st.info(f"🎯 AI 주입 데이터 - 원좌표: {target_str}, 오프셋: {offset_str}")

                            with st.spinner(f"🔍 [Micro] 스텝 {current_idx + 1} 세부 궤적 계산 및 코드 생성 중..."):
                                user_msg = f"""
                                            [현재 스텝 정보]
                                            Task: {action}
                                            Object: {target}
                                            Target: {target_str}
                                            Target_Offset: {offset_str}
                                            Destination: {dest_str}
                                            Destination_Offset: {dest_offset_str}
                                            """
                                micro_res = ollama.chat(model='llama3', messages=[
                                    {'role': 'system', 'content': self.micro_prompt},
                                    {'role': 'user', 'content': user_msg}
                                ])
                                
                                micro_output = micro_res['message']['content'].strip()
                                st.text_area("생성된 단일 모션 스케줄", micro_output, height=150)
                                
                                # 2. 4060에 코드 생성 요청 및 검수
                                final_prompt = f"다음 단일 스텝의 궤적을 파이썬 코드로 변환해라.\n\n{micro_output}"
                                final_code = self._communicate_with_4060(final_prompt)
                                
                                if final_code:
                                    # 💡 생성된 코드를 저장하고 화면 새로고침
                                    st.session_state['current_step_code'] = final_code
                                    st.rerun()
                                else:
                                    st.error("🚨 4060 코드 생성/검수 실패")

                    # 2-2. 코드가 생성되어 인간의 승인을 기다릴 때
                    else:
                        st.success("✅ 5060 검수 완료! 아래 코드를 확인해 주세요.")
                        st.code(st.session_state['current_step_code'], language='python')
                        
                        if st.button(f"▶️ [승인] 실로봇 구동 (스텝 {current_idx + 1} 실행)"):
                            with st.spinner("📡 4060으로 실행 명령 전송 중..."):
                                try:
                                    payload = {"action": "execute", "code": st.session_state['current_step_code']}
                                    response = self._send_to_4060(payload)
                                    
                                    if response:
                                        # 💡 만약 응답에 YOLO 업데이트 데이터가 있다면?
                                        if response.get("status") == "scan_result":
                                            new_yolo = response.get("data")
                                            # 전역 버퍼를 업데이트해서 GUI에 좌표가 바로 뜨게 함
                                            global_sensor_buffer.update(command=None, yolo=new_yolo)
                                            st.success("✅ 4060으로부터 새 좌표를 수신했습니다!")
                                            st.rerun() # 새로고침해서 좌표 업데이트 반영
                                        
                                        elif response.get("exec_status") == "SUCCESS":
                                            st.success(f"스텝 {current_idx + 1} 실행 성공!")
                                            
                                            # 💡 [가장 중요] 성공 시 다음 스텝으로 넘어가기 위해 상태 업데이트
                                            st.session_state['current_step_idx'] += 1
                                            st.session_state['current_step_code'] = None # 코드 초기화
                                            st.rerun() 
                                            
                                        else:
                                            st.error(f"❌ 4060 실행 에러: {response.get('message')}")
                                except Exception as e:
                                    st.error(f"❌ 통신 에러: {e}")

            # 모든 스텝이 완료되었을 때
            else:
                st.balloons()
                st.success("🎉 모든 작업(Sub-tasks)이 성공적으로 완료되었습니다!")
                if st.button("🔄 새로운 명령 받기 (초기화)"):
                    st.session_state['task_list'] = []
                    st.session_state['current_step_idx'] = 0
                    st.session_state['current_step_code'] = None
                    st.session_state['yolo_data'] = None
                    st.rerun()






# 2. 메인 웹 UI 렌더링
app = DualAgentControlCenter()
app.render_ui()