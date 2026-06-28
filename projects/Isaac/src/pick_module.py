import subprocess
import os

class IsaacPolicyController:
    """
    가상환경을 활성화하고 Isaac Sim TCP 서버로 정책 명령을 보내는 모듈입니다.
    """
    def __init__(self):
        # 1. 가상환경 활성화를 위해 먼저 진입해야 할 작업 디렉토리
        self.work_dir = os.path.expanduser("~/dev_ws/issac_sim/IsaacSim-ros_workspaces-main/humble_ws")
        
        # 2. 가상환경 활성화 명령어
        self.activate_cmd = "source ~/dev_ws/venv/isaaclab/bin/activate"
        
        # 3. 실제 TCP 클라이언트 스크립트 경로 (상대 경로로 수정)
        BASE_DIR = os.path.dirname(os.path.abspath(__file__))
        self.script_path = os.path.join(BASE_DIR, "send_policy_command.py")

    def execute_command(self, cmd_type: str) -> bool:
        """
        가상환경을 로드한 후, 파이썬 스크립트를 실행합니다.
        cmd_type: 'start', 'stop', 'reset', 'status', 'run_pick', 'policy_stop', 'hold_home' 등
        """
        # 💡 policy_stop 추가 (이전 논의에서 상자 흡착 유지용으로 사용하기 위함)
        valid_commands = ["start", "stop", "reset", "status", "run_pick", "run_place", "hold_home"]
        if cmd_type not in valid_commands:
            print(f"⚠️ [Isaac Policy] 지원하지 않는 명령어입니다: {cmd_type}")
            return False

        # 💡 핵심: 작업 폴더로 이동 -> 가상환경 활성화 -> 절대 경로의 파이썬 스크립트 실행
        full_command = f"cd {self.work_dir} && {self.activate_cmd} && python {self.script_path} {cmd_type}"
        
        print(f"🔌 [Isaac Policy] 가상환경 터미널 실행: {cmd_type.upper()}")
        
        try:
            # shell=True 와 executable='/bin/bash' 를 통해 bash 환경에서 실행
            result = subprocess.run(
                full_command, 
                shell=True, 
                executable='/bin/bash', 
                capture_output=True, 
                text=True
            )
            
            if result.returncode == 0:
                print(f"✅ [Isaac Policy] 응답: {result.stdout.strip()}")
                return True
            else:
                print(f"❌ [Isaac Policy] 에러 발생:\n{result.stderr.strip()}")
                return False
                
        except Exception as e:
            print(f"❌ [Isaac Policy] 시스템 에러: {e}")
            return False

# 단독 테스트용
if __name__ == "__main__":
    controller = IsaacPolicyController()
    controller.execute_command("status")