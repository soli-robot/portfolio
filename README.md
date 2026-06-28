# 👨‍💻 송종진 (Song Jongjin) | AI & Robotics Software Engineer

> **"하드웨어의 한계를 소프트웨어 지능으로 극복하는 로보틱스 엔지니어"**

인공지능(AI) 모델과 하드웨어 제어 로직을 결합하여, 불확실성이 높은 실제 환경에서도 안정적으로 구동하는 자율주행 및 협동 로봇 시스템을 설계합니다. LLM/VLM 기반의 에이전트 시스템(Agentic System) 통합부터 ROS2 Navigation2 자율주행 최적화, 다중 로봇 간 교착(Deadlock) 방지 알고리즘 설계까지 풀스택 로보틱스 엔지니어링 역량을 보유하고 있습니다.

[![Tech Stack](https://skillicons.dev/icons?i=python,ros,ubuntu,cpp,pytorch,flask,docker,github)](https://skillicons.dev)

---

## 🎯 핵심 역량 (Core Competencies)

- **Autonomous Navigation & Control**: ROS2 Nav2 기반 자율주행(AMR) 파라미터 최적화, 협동로봇(Cobot) 순차 액션 및 파지 센서 제어, 다중 로봇(Multi-Robot) 충돌 방지 및 포위 기동 알고리즘 설계
- **AI Agent & LLM Integration**: VLA(Vision-Language-Action) 아키텍처 구축, DeepSeek/Qwen/Llama3 기반 작업 지시 파이프라인 최적화, 분산 노드 WebSocket 통신 구현
- **Sensor Fusion & Error Recovery**: OAK-D 뎁스 카메라 노이즈 필터링(Median/EMA), IMU 데이터 활용 전도 감지 및 기립 자세 복구, LiDAR 스캔 - 전역 맵 간 OpenCV 매칭을 통한 자이로 오차 극복
- **System Integration & Backend**: Flask 기반 로봇 관제 HMI 서버 구축, SQLite/Firebase 데이터 로깅 및 예지보전 대시보드 연동

---

## 🚀 포트폴리오 프로젝트 (Portfolio Projects)

본 저장소는 시스템의 아키텍처부터 하위 노드 제어까지 직접 구현한 4가지 핵심 프로젝트를 포함하고 있습니다. 각 제목을 클릭하면 상세한 시스템 파이프라인과 트러블슈팅(문제 해결) 기록을 확인하실 수 있습니다.

### 1. 🤖 [춘식아 해줘 - Semantic AI 기반 자율 협동 로봇 어시스턴트](projects/춘식아 해줘 - Semantic AI 기반 자율 협동 로봇 어시스턴트/)
**"단순 반복을 넘어, 자연어 명령을 스스로 기획하고 코드로 변환하는 Local AI 파트너"**
- **역할 및 기여**: 코더 LLM(Qwen 2.5-Coder) 프롬프트 최적화, 파이썬 기반 로봇 제어 API 템플릿 설계
- **기술 스택**: Llama 3, Qwen 2.5-Coder, ROS2, Python, Flask, Modbus TCP
- **주요 성과**: 1만 자 이상의 프롬프트를 2천 자 수준으로 경량화하여 **코드 생성 Latency를 60초에서 10초로 대폭 단축(83% 개선)**.

### 2. 🪐 [춘식이 화성가즈아 - 디지털 트윈 기반 로봇 자동화 시뮬레이션 시스템](projects/춘식이 화성가즈아 - 디지털 트윈 기반 로봇 자동화 시뮬레이션 시스템/)
**"Florence-2 / DeepSeek-R1 분산형 VLA 기반 로봇 에이전트 및 자가 복구 생존 시스템"**
- **역할 및 기여**: ROS2 Nav2 자율주행 최적화, IMU 기반 전도 감지 및 기립 제어, OpenCV LiDAR 매칭 자가 복구 루틴 개발
- **기술 스택**: NVIDIA Isaac Sim, DeepSeek-R1, Florence-2, ROS2 Nav2, OpenCV, PyTorch
- **주요 성과**: 화성 험지에서 모바일 로봇 전복 시, IMU로 감지 후 로봇 팔 반동으로 스스로 기립. 직후 라이다 스캔과 전역 맵 매칭을 통해 **자이로 위치 오차를 강제 보정하여 자율주행 복구율 100% 달성**.

### 3. 🖨️ [FreshDot - 협동로봇 기반 점자 명함 제작 시스템](projects/FreshDot - 협동로봇 기반 점자 명함 제작 시스템/)
**"두산 협동로봇(m0609)과 비전 검증을 융합한 점자 명함 자동화 플랫폼"**
- **역할 및 기여**: ROS 2 액션 클라이언트/서버 로직 통합 제어, OpenCV 기반 점자 윤곽선(Contour) 검출 및 타각 품질 검증 서버 구축
- **기술 스택**: Doosan Cobot(m0609), ROS 2, OpenCV, Flask, Firebase
- **주요 성과**: 종이 미끄러짐으로 인한 타각 오류를 방지하고자 파지 센서 상태 머신(State Machine)을 도입해 동작 신뢰성 확보. 브라우저에서 전송된 명함 사진을 OpenCV로 분석해 좌우 반전 및 ROI를 자동 교정하여 **최종 점자 검증 정확도를 95% 이상으로 대폭 상향**.

### 4. 🚨 [Museum is Alive - 박물관 보안 관제 및 로봇 다중 추적 시스템](projects/Museum is Alive - 박물관 보안 관제 및 로봇 다중 추적 시스템/)
**"다중 CCTV 침입자 탐지 및 2대의 TurtleBot4 협동 자율 포위망 시스템"**
- **역할 및 기여**: YOLO 객체 탐지, OAK-D 뎁스 좌표 -> 글로벌 Map TF 변환, 다중 로봇 교착 상태 방지 인터락 설계
- **기술 스택**: YOLOv8, ROS2 Nav2, Flask, OpenCV, TurtleBot4
- **주요 성과**: 타임스탬프 비동기화 및 뎁스 노이즈를 처리하는 TF 변환 로직 최적화. 2대의 추적 로봇이 좁은 복도에서 교착(Deadlock)되지 않도록 상호 배타적 선점 및 `0.8m` 타겟 대기 거리를 적용해 **성공적인 양방향 포위 기동 구현**.

---

## 📬 Contact & Links
- **GitHub**: [github.com/soli-robot](https://github.com/soli-robot)

> *“각 프로젝트 폴더 내부의 `README.md`에서 시스템의 한계점과 안정성을 코드 레벨에서 치열하게 고민하고 해결해낸 **상세한 트러블슈팅(Troubleshooting)** 기록을 직접 확인하실 수 있습니다.”*
