# AI & Robotics Developer Portfolio

인공지능(AI)과 로봇 제어(Robotics) 분야의 다양한 프로젝트를 통합 관리하고 소개하는 포트폴리오 저장소입니다. 로봇 팔 제어, 자율주행 모바일 로봇(AMR) 경로 계획, 컴퓨터 비전 객체 인식 및 LLM 기반의 에이전트 시스템까지, 하드웨어와 AI 소프트웨어를 통합하는 역량을 포괄적으로 담고 있습니다.

---

## 📂 저장소 프로젝트 구성

이 포트폴리오 저장소는 아래와 같이 4개의 핵심 프로젝트로 구성되어 있습니다. 각 프로젝트 폴더 내부의 `README.md` 파일에서 각 시스템의 상세 아키텍처 및 흐름도(Flowchart)를 확인하실 수 있습니다.

### 1. [AI Collaborative Robot Assistant - LAMA (춘식아 해줘)](projects/Lama/)
* **개요**: 사용자 자연어 명령을 분석하는 Llama 3(Task LLM)와 이를 수행할 제어 코드를 생성하는 Qwen 2.5-Coder(Code LLM)를 분리한 3단계 분업 에이전트 아키텍처입니다. 비전 좌표 보정 및 예지보전 시스템이 통합되었습니다.
* **주요 기술**: Llama 3, Qwen 2.5-Coder, ROS2, MediaPipe, RealSense, 1D-CNN + LSTM, Python, Flask, Streamlit.

### 2. [Braille Recognition & AMR Control](projects/Braille/)
* **개요**: OAK-D 카메라와 YOLOv8-seg 기술을 사용해 실시간으로 점자 블록을 탐지하고 자율주행 모바일 로봇(AMR)을 주행 및 모니터링하는 시스템입니다.
* **주요 기술**: YOLOv8-seg, OAK-D, Python, SQLite, Flask, ROS2.

### 3. [Museum Security Guard (박살팀)](projects/FindThief/)
* **개요**: 박물관 내 다중 카메라 채널 입력 스트림을 실시간 분석하여 비인가 구역 침입자 및 이상 행동을 실시간으로 자동 감지하고 위치를 추적하는 보안 시스템입니다.
* **주요 기술**: PyTorch, Object Detection, Python, ROS2, OpenCV.

### 4. [NVIDIA Isaac Sim Robot Workspace](projects/Isaac/)
* **개요**: 물리 엔진 기반 시뮬레이터 상에 로봇 매니퓰레이터 및 공장 환경을 모델링하고, 강화학습 및 가상 검증을 수행할 수 있도록 구축된 가상 환경 워크스페이스입니다.
* **주요 기술**: NVIDIA Isaac Sim, Isaac Gym, PhysX, Python, ROS2.
