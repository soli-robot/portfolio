import os
import sys
import ollama
import json
import time
import subprocess
import gc
import re
import asyncio
import websockets
import torch
from PIL import Image
from datetime import datetime
from transformers import AutoProcessor, AutoModelForCausalLM

# ==========================================
# 🚨 [중요] 설정 구역 (이곳만 수정하세요)
# ==========================================
BASE_DIR = "/home/rokey/dev_ws/issac_sim/IsaacLab/m0609_lift_practice_19"
PICK_POLICY_PATH = "/home/rokey/Downloads/final_code_2/pick.pt"

# 제어 모듈 임포트
import move_controller_1 
import capture_module  # 4방향 카메라 모듈 임포트
import pick_module     # Isaac Sim pick 전용 정책 제어 모듈 임포트

model_name = 'qwen3-coder:30b'
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# ==========================================
# 🧹 0. 메모리 및 캐시 초기화
# ==========================================
def clear_all_caches(model_to_clear="qwen3-coder:30b"):
    print("\n" + "="*40)
    print("🧹 [🚨 시작 전 메모리 및 캐시 대청소]")
    try:
        ollama.chat(model=model_to_clear, messages=[], keep_alive=0)
    except: 
        pass
    gc.collect()
    if device.type == "cuda":
        torch.cuda.empty_cache()
    print("✅ 메모리 리셋 완료! 깨끗한 상태에서 시작합니다.")
    print("="*40 + "\n")

clear_all_caches(model_to_clear=model_name)

# ==========================================
# 🌐 [중요] 네트워크 주소 설정 
# ==========================================
PC1_IP = "192.168.10.23" 
PC1_VISION_WS_URL = f"ws://{PC1_IP}:8889"


# ==========================================
# 🧠 1. Florence-2 시각(Vision) 모델 로드
# ==========================================
print("▶ Florence-2 모델 로드 중...")
FLORENCE_MODEL_ID = "microsoft/Florence-2-base"
DTYPE = torch.float16 if device.type == "cuda" else torch.float32

processor = AutoProcessor.from_pretrained(FLORENCE_MODEL_ID, trust_remote_code=True)
florence_model = AutoModelForCausalLM.from_pretrained(
    FLORENCE_MODEL_ID,
    trust_remote_code=True,
    attn_implementation="eager",
    torch_dtype=DTYPE,
).to(device).eval()
print(f"➔ Florence-2 연산 디바이스: {device}\n")

# 추론 최적화 로직
def run_florence2(image: Image.Image, task: str) -> str:
    image = image.resize((768, 768))
    inputs = processor(text=task, images=image, return_tensors="pt").to(device, DTYPE)
    with torch.no_grad():
        generated_ids = florence_model.generate(
            input_ids=inputs["input_ids"],
            pixel_values=inputs["pixel_values"],
            max_new_tokens=256, # 토큰 폭주 방지
            num_beams=3,
            use_cache=False,    # 캐시 끄기
        )
    result = processor.batch_decode(generated_ids, skip_special_tokens=False)[0]
    parsed = processor.post_process_generation(result, task=task, image_size=image.size)
    return parsed

# 각 이미지 처리 및 Hermes 요약 엔진 반영
def analyze_images_with_florence(image_paths):
    analyzed_data = []
    for idx, image_path in enumerate(image_paths, start=1):
        filename = os.path.basename(image_path)
        try:
            image = Image.open(image_path).convert("RGB")
            
            # 1. Object Detection 
            od_result = run_florence2(image, "<OD>")
            labels = od_result.get("<OD>", {}).get("labels", [])
            
            # 2. Dense Caption 추출 (<MORE_DETAILED_CAPTION> 적용)
            caption_result = run_florence2(image, "<CAPTION>")
            caption_text = caption_result.get("<CAPTION>", "")
        except Exception as e:
            print(f"❌ [{idx}/{len(image_paths)}] {filename} 처리 실패: {e}")
            continue

        # 컨텍스트 버스트 방지 요약 엔진
        if caption_text:
            view_sections = re.split(r'(\[[a-zA-Z0-9_\-\.]+\])', caption_text)
            summarized_parts = []
            current_tag = ""

            for part in view_sections:
                part = part.strip()
                if not part:
                    continue
                if part.startswith("[") and part.endswith("]"):
                    current_tag = part
                else:
                    sentences = [s.strip() for s in part.split('.') if s.strip()]
                    compact_desc = ". ".join(sentences[:2]) + "." if sentences else part

                    if current_tag:
                        summarized_parts.append(f"{current_tag} {compact_desc}")
                        current_tag = "" 
                    else:
                        summarized_parts.append(compact_desc)

            summary_text = "\n".join(summarized_parts)
        else:
            summary_text = ""

        unique_labels = sorted(list(set(labels)))
        counts = {label: labels.count(label) for label in unique_labels}
        
        output_json = {
            "image_file": filename,
            "objects": {"labels": unique_labels, "counts": counts},
            "caption": summary_text,
        }
        print(f"// [{idx}/{len(image_paths)}] {filename} 분석 완료")
        analyzed_data.append(output_json)
        
    return analyzed_data


# ==========================================
# 🤖 2. Isaac Policy 제어기 인스턴스 생성
# ==========================================
try:
    policy_controller = pick_module.IsaacPolicyController()
except AttributeError:
    print("⚠️ [경고] pick_module 내에 IsaacPolicyController를 찾을 수 없습니다.")
    policy_controller = None


# ==========================================
# 📡 3-A. [송신부] 비전 데이터를 찍어서 대뇌(PC1)로 쏘는 루프
# ==========================================
async def send_vision_data_to_pc1():
    BASE_DIR_PATH = os.path.dirname(os.path.abspath(__file__))
    IMAGE_DIR = os.path.join(BASE_DIR_PATH, "test_image")
    supported_exts = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
    
    while True: 
        print(f"\n📡 [비전 SNED] 대뇌(PC 1) {PC1_VISION_WS_URL} 연결 확인 중...")
        try:
            async with websockets.connect(PC1_VISION_WS_URL, open_timeout=5, ping_interval=None) as ws:
                print("✅ [비전 송신] 대뇌(PC 1)와 연결 성공! 1회 캡처 및 분석을 시작합니다.")
                await asyncio.to_thread(capture_module.capture_all_images)
                
                if not os.path.exists(IMAGE_DIR):
                    print(f"⚠️ [경고] 이미지 폴더가 존재하지 않습니다: {IMAGE_DIR}")
                else:
                    actual_image_paths = sorted([
                        os.path.join(IMAGE_DIR, f)
                        for f in os.listdir(IMAGE_DIR)
                        if os.path.isfile(os.path.join(IMAGE_DIR, f)) and os.path.splitext(f)[1].lower() in supported_exts
                    ])
                    
                    if not actual_image_paths:
                        print(f"⚠️ [비전 송신] {IMAGE_DIR} 에 분석할 이미지가 없습니다.")
                    else:
                        vision_analysis_results = await asyncio.to_thread(analyze_images_with_florence, actual_image_paths)
                        
                        total_counts = {}
                        combined_captions = []
                        for res in vision_analysis_results:
                            for label, count in res["objects"]["counts"].items():
                                total_counts[label] = total_counts.get(label, 0) + count
                            combined_captions.append(f"[{res['image_file']}] {res['caption']}")
                            
                        master_labels = sorted(list(total_counts.keys()))
                        master_caption = "\n".join(combined_captions)

                        # source와 timestamp가 포함된 Hermes 규격 패킷 완성
                        packet = {
                            "objects": { "labels": master_labels, "counts": total_counts },
                            "caption": master_caption,
                            "source": "florence2",
                            "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                        }
                        
                        await ws.send(json.dumps(packet))
                        print("📤 [비전 송신] 분석된 맵/객체 데이터(JSON)를 대뇌(PC 1) UI로 전송 완료!")
                        
                        try:
                            ack = await asyncio.wait_for(ws.recv(), timeout=2.0)
                            print(f"📩 [비전 송신] 대뇌(PC 1) 응답 수신: {ack}\n")
                        except asyncio.TimeoutError:
                            print("⚠️ [비전 송신] 대뇌(PC 1)로부터 ACK 수신 타임아웃 (문제없음)\n")
                
                print("⏸️ [비전 송신] 1회 캡처가 완료되었습니다. 추가 촬영 없이 대뇌의 Task 명령을 대기합니다.")
                while True:
                    await asyncio.sleep(3600)
                
        except (websockets.exceptions.ConnectionClosedError, ConnectionRefusedError, asyncio.TimeoutError):
            print(f"⚠️ [비전 송신] 대뇌(PC 1) 서버 대기 중... ({PC1_IP}:8889)")
            await asyncio.sleep(5)
        except Exception as e:
            print(f"🛑 [비전 송신] 알 수 없는 오류 발생: {e}")
            await asyncio.sleep(5)

# ==========================================
# 📡 3-B. [수신부] 대뇌(PC1)로부터 작업 명령 수신 및 피드백 송신
# ==========================================
async def command_handler(websocket):
    print("\n🔗 [명령 수신] 대뇌(PC 1)의 Hermes Agent 도구가 접속했습니다!")
    try:
        async for message in websocket:
            task_data = json.loads(message)
            
            print(f"\n📥 [명령 수신] 대뇌(PC 1)로부터 수신한 원본 JSON 데이터:")
            print(json.dumps(task_data, ensure_ascii=False, indent=2))
            
            # 💡 [수정] 직접 await 호출 (run_task_pipeline 내부가 이미 비동기 최적화됨)
            feedback_payload = await run_task_pipeline(model_name, task_data)
            
            print(f"📤 [명령 피드백 전송 준비 완료] 상태: {feedback_payload.get('status')}")
            await websocket.send(json.dumps(feedback_payload, ensure_ascii=False))
            print("📤 [피드백 송신] PC 1으로 실행 결과(JSON) 회신 완료!\n")
            
            if feedback_payload["status"] == "success":
                print("✅ [로봇 제어] 작업 시퀀스 정상 종료.")
            else:
                print(f"❌ [로봇 제어] 에러로 인한 중단: {feedback_payload.get('error_message')}")
                
    except websockets.exceptions.ConnectionClosed:
        print("🛑 [명령 수신] 대뇌(PC 1) 연결 종료됨.")

async def command_server():
    print("🤖 [명령 서버] 대뇌 명령 대기 중 (PC 2 포트: 9999)...")
    async with websockets.serve(command_handler, "0.0.0.0", 9999):
        await asyncio.Future()


# ==========================================
# ⚙️ 4. 로봇 제어 파이프라인 (비동기 최적화)
# ==========================================
def generate_plan(model_id, input_json_str):
    SYSTEM_PROMPT = """
You are a universal Robotic Task Planner.
You receive a JSON with a high-level "sequence" of actions. This sequence could be from any domain (e.g., agriculture, logistics, domestic robots).
Your task is to translate this sequence into a single, raw JSON array of executable base robot skills.

CRITICAL INSTRUCTIONS:
1. Output MUST be a valid JSON array starting with '[' and ending with ']'. No markdown, no explanations.
2. Each object in the array MUST contain EXACTLY two keys: "step" (integer, starting from 1) and "skill" (string).
3. The "skill" value MUST be exactly one of these 3 base actions: "move", "pick", "put".

LOGICAL MAPPING GUIDE:
- "move": Navigating, scanning, approaching, walking, driving, or changing physical location.
- "pick": Grasping, cutting, harvesting, lifting, taking, or holding an object.
- "put": Releasing, dropping, placing, storing, or putting down an object.

EXAMPLE:
Input sequence: ["find_box", "grab_box", "place_on_conveyor"]
Output: [{"step": 1, "skill": "move"}, {"step": 2, "skill": "pick"}, {"step": 3, "skill": "put"}]
"""
    print(f"🧠 [{model_id}] LLM 추론 중... (작업 계획 수립)")
    try:
        response = ollama.chat(
            model=model_id,
            messages=[
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': input_json_str}
            ],
            options={"temperature": 0.0, "num_predict": 512}
        )
        raw_output = response['message']['content'].strip()
        match = re.search(r'\[.*\]', raw_output, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        return None
    except Exception as e:
        print(f"❌ 추론 에러: {e}")
        return None

# 💡 [수정] 완전한 비동기(async) 함수로 변경 및 블로킹 방지 처리
async def execute_robot_skill(skill_name, target_info, coordinate=None, robot_state=None):
    if robot_state is None:
        robot_state = {"is_holding_box": False}
        
    print(f"\n  ▶️ [실행 중] 스킬: {skill_name.upper()} (Target: {target_info}) | 좌표: {coordinate}")
    await asyncio.sleep(0.5) # time.sleep(0.5) -> await asyncio.sleep(0.5)
    
    if skill_name == "move":
        if coordinate:
            print(f"    -> 🗺️ AMR 네비게이션 시작 (스레드 격리): {coordinate}")
            # 💡 [핵심] move_controller_1이 블로킹되지 않도록 스레드 풀에서 실행
            success = await asyncio.to_thread(move_controller_1.navigate_to, coordinate)
            
            if not success:
                return False, {
                    "failed_step": "move",
                    "error_code": "navigation_failed",
                    "error_message": f"AMR stopped or failed to reach coordinate {coordinate}",
                    "observed_state": {"target_visible": False, "distance_to_target": "unknown"}
                }
            print("    -> 🏁 목적지 도착 확인 완료!")
            
    elif skill_name == "pick":
        print("    -> ✂️ RL 정책(Policy) 제어 준비: 로봇 초기화(reset) 진행...")
        if policy_controller:
            print("    -> ✂️ 로봇 초기화 완료. RL 정책 가동(run_pick) 시작...")
            await asyncio.sleep(0.5) 
            
            # Isaac 통신 블로킹 방지
            success_start = await asyncio.to_thread(policy_controller.execute_command, "run_pick") 
            
            if success_start:
                print("    -> ⏳ 로봇이 물건을 집을 때까지 대기합니다...")
                await asyncio.sleep(6.0) # 블로킹 해제
                robot_state["is_holding_box"] = True
            else:
                return False, {
                    "failed_step": "suction_grasp",
                    "error_code": "policy_start_error",
                    "error_message": "Policy model failed to grasp object",
                    "observed_state": {"target_visible": True, "gripper_contact": False}
                }
        else:
            return False, {
                "failed_step": "suction_grasp",
                "error_code": "module_missing",
                "error_message": "pick_module/policy_controller not found",
                "observed_state": {}
            }
            
    elif skill_name == "put":
        if not robot_state.get("is_holding_box", False):
            print("    -> ⚠️ [경고] 로봇이 상자를 잡고 있지 않습니다! Put 작업을 취소합니다.")
            return False, {
                "failed_step": "put",
                "error_code": "not_holding_object",
                "error_message": "Robot is not holding any object to place.",
                "observed_state": {"gripper_contact": False}
            }
            
        print("    -> 🧺 Place 정책(Policy) 가동: 로봇을 움직여 상자 내려놓기(run_place) 시작...")
        if policy_controller:
            success_place = await asyncio.to_thread(policy_controller.execute_command, "run_place")
            if not success_place:
                return False, {
                    "failed_step": "run_place",
                    "error_code": "policy_place_error",
                    "error_message": "Failed to run place policy",
                    "observed_state": {}
                }
            
            print("    -> ⏳ 로봇 팔이 -180도 방향으로 회전하며 이동 중입니다 (대기 중)...")
            await asyncio.sleep(8.0) # 블로킹 해제
            
            print("    -> 🛑 목적지 도달! 상자를 바닥에 떨어뜨립니다 (stop).")
            success_stop = await asyncio.to_thread(policy_controller.execute_command, "stop")
            
            robot_state["is_holding_box"] = False
            
            if not success_stop:
                return False, {
                    "failed_step": "place_stop",
                    "error_code": "policy_stop_error",
                    "error_message": "Failed to stop and release object",
                    "observed_state": {}
                }
        else:
            return False, {
                "failed_step": "put",
                "error_code": "module_missing",
                "error_message": "pick_module/policy_controller not found",
                "observed_state": {}
            }
            
    return True, None

# 💡 [수정] execute_robot_skill이 async가 되었으므로 파이프라인도 async로 선언
async def run_task_pipeline(model_id, input_task_dict):
    print(f"\n{'='*50}\n🎯 [로봇 작업 파이프라인 시작]")
    
    task_id = input_task_dict.get("task_id", datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))
    
    input_str = json.dumps(input_task_dict, ensure_ascii=False, indent=2)
    plan = generate_plan(model_id, input_str) # llm 추론은 짧아서 일반 함수로 유지
    
    if not plan: 
        return {
            "type": "execution_feedback",
            "task_id": task_id,
            "status": "failed",
            "failed_step": "planning",
            "error_code": "llm_planning_failed",
            "error_message": "LLM failed to generate a valid sequence",
            "observed_state": {},
            "suggestion": "Check LLM model or input task structure"
        }

    print(f"\n📋 [실행 계획] Code LLM({model_id})이 생성한 행동 순서도:")
    print(json.dumps(plan, ensure_ascii=False, indent=2))

    pickup_pos = input_task_dict.get("amr_pickup_pos", [0.0, 0.0, 0.0])
    drop_pos = input_task_dict.get("amr_drop_pos", [0.0, 0.0, 0.0])
    target = input_task_dict.get("target", "unknown")
    
    completed_sequence = []
    current_robot_state = {"is_holding_box": False}
    
    for action in plan:
        skill = action['skill']
        coord_to_pass = None
        
        if skill == "move":
            if not current_robot_state["is_holding_box"]:
                coord_to_pass = str(pickup_pos)
            else:
                coord_to_pass = str(drop_pos)
            
        # 💡 [수정] await 키워드를 붙여 비동기 대기
        success, error_info = await execute_robot_skill(
            skill, 
            target, 
            coordinate=coord_to_pass, 
            robot_state=current_robot_state 
        )
        
        if success:
            completed_sequence.append(skill)
        else:
            print(f"       ⚠️ [경고] {skill} 단계에서 실패 발생!")
            return {
                "type": "execution_feedback",
                "task_id": task_id,
                "status": "failed",
                "failed_step": error_info.get("failed_step", skill),
                "error_code": error_info.get("error_code", "execution_error"),
                "error_message": error_info.get("error_message", f"Failed during {skill}"),
                "observed_state": error_info.get("observed_state", {}),
                "suggestion": "Check robot state, navigation goals, or policy readiness"
            }
        
    print(f"\n{'='*50}\n🏁 모든 작업 완료!")
    
    return {
        "type": "execution_feedback",
        "task_id": task_id,
        "status": "success",
        "completed_sequence": completed_sequence
    }


# ==========================================
# 🚀 5. 메인 비동기 오케스트레이터
# ==========================================
async def main():
    await asyncio.gather(
        command_server(),
        send_vision_data_to_pc1()
    )

if __name__ == "__main__":
    print("🚀 시스템 초기화 완료. 메인 서버 및 비전 분석을 가동합니다.\n")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 [PC 2] 종료되었습니다.")