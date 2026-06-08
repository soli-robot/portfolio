# 🤖 NVIDIA Isaac Sim 로봇 제어 시뮬레이션 환경 (Isaac Sim Workspace)

NVIDIA Omniverse 기반의 가상 환경 엔진인 Isaac Sim과 Isaac Gym을 연동하여, 로봇 매니퓰레이터 및 공장 자동화 하드웨어를 실제 기기에 배포하기 전 고도로 정밀한 가상 환경에서 학습 및 테스트할 수 있도록 시뮬레이션 환경을 다각화하여 구축한 워크스페이스입니다.

## 🎯 핵심 기능
1. **정밀 물리 엔진 기반 시뮬레이션 (NVIDIA PhysX & Omniverse)**:
   - 로봇의 관절 각도, 기어 마찰력, 자재 무게 등 현실의 물리적 특성을 시뮬레이션상에 동일하게 모델링합니다.
   - USD(Universal Scene Description) 표준을 기반으로 고정밀 3D 공장 레이아웃 및 환경 에셋을 통합 제어합니다.
2. **강화학습 환경 연동 (Isaac Gym)**:
   - 로봇 팔의 Pick & Place 작업 최적화 및 최적 경로 학습을 위해 GPU 기반 병렬 물리 시뮬레이션 학습 파이프라인을 연동합니다.
3. **ROS2/DRL 통신 브릿지 제공**:
   - 가상 로봇 센서 데이터를 ROS2 토픽으로 발행하고, 외부 ROS2 노드 혹은 Doosan Robotics DRL 제어 스크립트의 토크 및 속도 명령을 수신하여 로봇의 움직임을 재현합니다.

## 📊 시스템 흐름도 (Flowchart)

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

## 🛠️ 개발 환경 및 기술 스택
- **Simulation Engine**: NVIDIA Isaac Sim (Omniverse API)
- **RL Framework**: NVIDIA Isaac Gym (PyTorch PPO Algorithm)
- **Middleware**: ROS2 (Jazzy / Humble Bridge)
- **Languages & APIs**: Python 3.10+, USD (Universal Scene Description), Omniverse Kit Extension SDK
