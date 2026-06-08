# 🤖 NVIDIA Isaac Sim 로봇 제어 시뮬레이션 환경 (Isaac Sim Workspace)

NVIDIA Omniverse 기반의 가상 환경 엔진인 Isaac Sim과 Isaac Gym을 연동하여, 로봇 매니퓰레이터 및 공장 자동화 하드웨어를 실제 기기에 배포하기 전 고도로 정밀한 가상 환경에서 학습 및 테스트할 수 있도록 시뮬레이션 환경을 다각화하여 구축한 워크스페이스입니다.

---

## 📊 1. 시스템 설계 및 플로우 차트 (System Design & Flow Chart)

### 시스템 개요
본 시뮬레이션 시스템은 크게 세 단계로 나누어 동작합니다:
1. **에셋 및 로봇 임포트 (Assets Import)**: 로봇의 CAD 또는 URDF 도면을 USD 포맷으로 변환하고 물리 속성(질량, 관성, 조인트 한계치 등)을 리깅하여 가상 스테이지 상에 배치합니다.
2. **물리 및 센서 시뮬레이션 (Simulation Loop)**: Omniverse PhysX 엔진을 기반으로 실시간 물리 연산을 수행하고, 스테레오 카메라, LiDAR, Joint State 센서 등의 가상 피드백을 실시간 수집합니다.
3. **제어 및 학습 브릿지 (Control Bridge)**: 가상 센서 데이터를 ROS2 브릿지(Action Graph) 또는 Tensor API(Isaac Gym)를 통해 받아 구동 모터(Joint Controller)에 토크 및 속도 입력을 인가하는 루프를 형성합니다.

### 시스템 흐름도 (Mermaid)

```mermaid
flowchart TD
    subgraph Assets_Import [1. 에셋 및 로봇 임포트]
        URDF[로봇 모델 URDF/XACRO] -->|URDF Importer| USD[USD 파일 변환 및 리깅]
        Factory[3D 가상 공장 환경 USD] --> Stage[Isaac Sim Stage 월드 구성]
        USD --> Stage
    end

    subgraph Simulation_Loop [2. 물리 및 센서 시뮬레이션]
        Stage -->|PhysX 엔진 적용| Simulation[물리 연산 루프 수행]
        Simulation -->|가상 센서 피드백| SensorData[Joint State / RGB-D / LiDAR 데이터]
    end

    subgraph Control_Bridge [3. 제어 및 학습 브릿지]
        SensorData -->|ROS2 Bridge / Action Graph| ROS2[ROS2 Topic / Services]
        SensorData -->|Tensor API| IsaacGym[Isaac Gym 강화학습 (PPO)]
        
        ROS2 -->|속도/토크 제어 명령 수신| Control[로봇 Joint Controller]
        IsaacGym -->|행동 에이전트 Action Policy| Control
        Control -->|물리 엔진에 입력값 적용| Simulation
    end
```

---

## 💻 2. 운영체제 환경 (Operating System Environment)

- **OS**: Ubuntu 22.04 LTS (NVIDIA 드라이버 및 Isaac Sim 런타임 최적 지원 운영체제)
- **Middleware**: ROS2 Humble (Bridge 및 Action Graph 통신용)
- **Python**: Isaac Sim 내장 Python Interpreter (Python 3.10 기반)

---

## 🛠️ 3. 사용한 장비 목록 (Hardware Equipment List)

- **GPU Workstation (시뮬레이션 연산용)**:
  - CPU: AMD Ryzen 9 7900X 혹은 Intel Core i9 13th Gen 이상
  - GPU: NVIDIA GeForce RTX 4080 / 4090 (VRAM 16GB 이상 필수, RT Core 탑재 필수)
  - RAM: 64GB DDR5
  - Storage: 1TB NVMe SSD

---

## 📦 4. 의존성 (Dependencies)

Isaac Sim 연동 및 테스트를 위해 구성해야 하는 파이썬 필수 의존성은 다음과 같으며, `requirements.txt`에 명시되어 있습니다.

- **강화학습 및 딥러닝**: `torch>=2.0.0`, `gym>=0.21.0`
- **수치 연산 및 과학 라이브러리**: `numpy>=1.24.0`, `scipy>=1.8.0`
- **Isaac Sim 내장 모듈 (설치 디렉토리 기본 제공)**:
  - `omni.isaac.core`, `omni.isaac.gym`, `omni.kit.app` 등

> [!NOTE]
> 자세한 내용은 [requirements.txt](requirements.txt) 파일을 참고해 주세요.

---

## 🚀 5. 간단한 실행 순서 (Execution Sequence & Launch Script)

NVIDIA Isaac Sim 환경에서 로봇 모델링 및 주행 모듈을 기동하는 스크립트 실행 절차입니다.

### Step 1. ROS2 Humble 환경 및 Omniverse 환경 소싱
```bash
# ROS2 환경 소싱
source /opt/ros/humble/setup.bash

# Isaac Sim 패스 환경 변수 등록
export ISAAC_SIM_PATH=~/.local/share/ov/pkg/isaac-sim-2023.1.1
```

### Step 2. Isaac Sim 시뮬레이터 구동 및 USD 맵 로드
GUI 모드로 시뮬레이터를 켜고 공장 월드 및 로봇 모델을 활성화합니다.
```bash
cd $ISAAC_SIM_PATH

# Isaac Sim GUI 실행 및 백그라운드 서버 구동
./isaac-sim.sh
```
*Omniverse Launcher를 통해서도 실행 가능하며, `projects/isaac/scene.usd` 파일을 스테이지에 로드합니다.*

### Step 3. 제어 스크립트 또는 강화학습 에이전트 실행
Isaac Sim의 전용 Python 인터프리터를 사용하여 가상 로봇에 제어 신호를 보내거나, 강화학습 루프를 시작합니다.
```bash
# ROS2 통신 노드 기반 로봇 제어 스크립트 실행
$ISAAC_SIM_PATH/python.sh robot_control_loop.py

# Isaac Gym 강화학습 (PPO) 에이전트 훈련 시작
$ISAAC_SIM_PATH/python.sh train_rl_agent.py --task DoosanPickAndPlace
```
*정상적으로 구동되면 Isaac Sim 화면상의 로봇 팔이 파란색 물체를 감지하여 지정된 트레이로 이송하는 모션이 시작됩니다.*
