# 🤖 NVIDIA Isaac Sim 로봇 제어 시뮬레이션 환경 (Isaac Sim Workspace)

본 저장소는 NVIDIA Isaac Sim 및 Isaac Gym을 연동하여 로봇 매니퓰레이터 및 자율주행 모바일 로봇(AMR)의 가상 물리 시뮬레이션 환경을 구축한 작업 공간입니다.

## 📌 프로젝트 소개
현장 로봇을 도입하기 전, 시뮬레이션 환경 상에서 물리 엔진(PhysX)을 활용해 안전성과 오작동 테스트를 사전에 검증할 수 있습니다. 

## 🛠️ 개발 스택
- **Simulation**: NVIDIA Isaac Sim / Isaac Gym
- **Physics Engine**: PhysX (NVIDIA)
- **Framework**: ROS2
- **Language**: Python (Omniverse Kit API, Gym RL wrapper)

## 📂 파일 및 폴더 배치 안내
* 이 디렉터리는 현재 로컬 개발용 시뮬레이션 워크스페이스입니다. 
* 시뮬레이션에 필요한 USD(Universal Scene Description) 파일 및 로봇 메쉬 파일(URDF, OBJ), 강화학습 학습 스크립트(`.py`) 등을 해당 위치에 복사해서 관리하시면 됩니다.
