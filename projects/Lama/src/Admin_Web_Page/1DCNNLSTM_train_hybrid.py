import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
import numpy as np
import os
import pandas as pd
import ast

# PostgreSQL 배열 스트링 파싱 함수
def parse_pg_array(array_str):
    if pd.isna(array_str) or not isinstance(array_str, str):
        return []
    try:
        return ast.literal_eval(array_str.replace('{', '[').replace('}', ']'))
    except:
        return []

# 1. 실제 로컬 CSV 데이터를 파이토치 시퀀스로 굽는 커스텀 데이터셋 클래스
class RobotSequenceDataset(Dataset):
    def __init__(self, file_path, seq_length=30):
        self.seq_length = seq_length
        
        # 파일 확인 및 로드
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"❌ 데이터 파일을 찾을 수 없습니다: {file_path}")
            
        column_names = [
            'id', 'db_timestamp', 'emergency_stopped', 'safety_stopped', 
            'joint_motor_current', 'joint_motor_temperature', 'joint_torque', 
            'joint_velocity', 'joint_position', 'direct_teach_pressed', 
            'robot_device_id', 'robot_state', 'collision_detected', 'servo_on_robot', 
            'task_external_force', 'task_external_moment', 'task_velocity', 
            'task_orientation', 'task_position', 'user_custom_data', 'created_at', 
            'reserved_field_1', 'reserved_field_2', 'firmware_version'
        ]
        
        df = pd.read_csv(file_path, names=column_names, header=None if pd.read_csv(file_path, nrows=1).columns[0] == 'ba621164-0fbc-4312-b058-24e414b68993' else 0)
        
        # 배열 컬럼 전처리
        df['joint_motor_current'] = df['joint_motor_current'].apply(parse_pg_array)
        df['joint_motor_temperature'] = df['joint_motor_temperature'].apply(parse_pg_array)
        
        # 한국 시간 기준 시계열 정렬
        df['created_at'] = pd.to_datetime(df['created_at'])
        df = df.sort_values(by='created_at').reset_index(drop=True)
        
        # 6개 관절 전류와 온도를 추출하여 모델에 넣을 (8202, 12) 차원의 2차원 넘파이 행렬 구축
        features = []
        for i in range(len(df)):
            curr = df['joint_motor_current'].iloc[i]
            temp = df['joint_motor_temperature'].iloc[i]
            # 예외 처리: 데이터가 비어있을 경우 0으로 패딩
            if len(curr) < 6: curr = [0.0]*6
            if len(temp) < 6: temp = [0.0]*6
            features.append(curr + temp)
            
        self.data_matrix = np.array(features, dtype=np.float32)
        
        # Z-Score 데이터 스케일 정규화
        self.mean = np.mean(self.data_matrix, axis=0)
        self.std = np.std(self.data_matrix, axis=0) + 1e-5
        self.data_matrix = (self.data_matrix - self.mean) / self.std
        
        # 공식 매핑을 위한 정답 레이블 세팅
        total_len = len(df)
        self.labels_failure = np.zeros((total_len, 1), dtype=np.float32)
        self.labels_rul = np.zeros((total_len, 1), dtype=np.float32)
        
        for i in range(total_len):
            # 가동 로그 후반부로 갈수록 장비의 잔여 수명이 줄어들도록 선형 매핑 (RUL 수식 정석)
            self.labels_rul[i, 0] = float(total_len - i) 
            # 특정 시점 간격마다 주기적인 위험 징후 플래그(1) 부여
            if i % 250 == 0:
                self.labels_failure[i, 0] = 1.0

    def __len__(self):
        return len(self.data_matrix) - self.seq_length

    def __getitem__(self, idx):
        # Sliding Window 30개 시퀀스 추출
        x_seq = self.data_matrix[idx : idx + self.seq_length]
        
        # 시퀀스의 마지막 시점 기준 정답(Target) 매핑
        y_fail = self.labels_failure[idx + self.seq_length]
        y_rul = self.labels_rul[idx + self.seq_length]
        
        return torch.tensor(x_seq), torch.tensor(y_fail), torch.tensor(y_rul)


# 2. 1D-CNN + LSTM 하이브리드 모델 정의
class RobotPredictiveModel(nn.Module):
    def __init__(self, input_dim, sequence_length):
        super(RobotPredictiveModel, self).__init__()
        # 타임스탬프 축을 따라 로컬 변동 특징 추출
        self.conv1d = nn.Conv1d(in_channels=input_dim, out_channels=32, kernel_size=3, padding=1)
        self.relu = nn.ReLU()
        # 시간 경과에 따른 시계열 흐름 및 의존성 학습
        self.lstm = nn.LSTM(input_size=32, hidden_size=64, num_layers=2, batch_first=True)
        
        # 고장 확률 분류 탑 (공식 1 대응)
        self.fc_classifier = nn.Sequential(
            nn.Linear(64, 16),
            nn.ReLU(),
            nn.Linear(16, 1),
            nn.Sigmoid()
        )
        # 잔여 수명(RUL) 회귀 탑 (공식 2 대응)
        self.fc_regressor = nn.Sequential(
            nn.Linear(64, 16),
            nn.ReLU(),
            nn.Linear(16, 1)
        )

    def forward(self, x):
        x = x.transpose(1, 2) # (Batch, input_dim, seq_len)
        x = self.relu(self.conv1d(x))
        x = x.transpose(1, 2) # (Batch, seq_len, channels)
        lstm_out, _ = self.lstm(x)
        last_hidden = lstm_out[:, -1, :]
        
        return self.fc_classifier(last_hidden), self.fc_regressor(last_hidden)


# 3. 메인 실행 파이프라인
def run_hybrid_training():
    print("🚀 [AI 하이브리드] 실제 로컬 CSV 연동 학습 파이프라인 시작...")
    
    # 디렉토리 경로 자동 추적
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    csv_file_path = os.path.join(base_dir, "resource", "Cloud_SQL_Export_2026-05-26 (06_48_03).csv")
    
    sequence_length = 30
    input_dim = 12 # 6개 전류 + 6개 온도
    
    # 커스텀 데이터셋 및 데이터로더 연결
    dataset = RobotSequenceDataset(csv_file_path, seq_length=sequence_length)
    dataloader = DataLoader(dataset, batch_size=16, shuffle=True)
    
    model = RobotPredictiveModel(input_dim, sequence_length)
    optimizer = optim.Adam(model.parameters(), lr=0.001)
    
    criterion_bce = nn.BCELoss() # 시계열 분류 손실 공식
    criterion_mse = nn.MSELoss() # 잔여 수명(RUL) 회귀 손실 공식
    
    epochs = 20  # 전체 데이터셋 반복 학습 횟수
    print(f"\n🏋️ 총 {epochs} 에포크 동안 신경망 본격 최적화 학습을 시작합니다...")
    
    for epoch in range(epochs):
        model.train()
        epoch_loss_clf = 0.0
        epoch_loss_reg = 0.0
        batch_count = 0
        
        for batch_x, batch_y_fail, batch_y_rul in dataloader:
            optimizer.zero_grad()
            
            # 1D-CNN + LSTM 예측 순방향 전파
            pred_prob, pred_rul = model(batch_x)
            
            # 손실 계산
            loss_classification = criterion_bce(pred_prob, batch_y_fail)
            loss_regression = criterion_mse(pred_rul, batch_y_rul)
            
            # 수식 스케일 밸런싱 결합 (MSE가 너무 크므로 1e-6 가중치 반영)
            total_loss = loss_classification + (loss_regression * 0.000001)
            
            # 역전파 및 가중치 업데이트
            total_loss.backward()
            optimizer.step()
            
            epoch_loss_clf += loss_classification.item()
            epoch_loss_reg += loss_regression.item()
            batch_count += 1
            
        # 매 에포크마다의 평균 손실률 출력 추적
        avg_clf = epoch_loss_clf / batch_count
        avg_reg = epoch_loss_reg / batch_count
        print(f"🍏 [Epoch {epoch+1:02d}/{epochs}] 분류 Loss: {avg_clf:.4f} | RUL MSE: {avg_reg:.2f}")

    print("\n🎉 1D-CNN + LSTM 하이브리드 예지 보전 모델 학습이 최종 완료되었습니다!")
    
    # 학습 완료된 가중치 파일 물리적 보관
    model_save_path = os.path.join(base_dir, "resource", "1DCNNLSTM_hybrid_predictive_model.pth")
    torch.save(model.state_dict(), model_save_path)
    print(f"💾 모델 가중치가 {model_save_path} 파일로 안전하게 저장되었습니다.")

if __name__ == "__main__":
    run_hybrid_training()

# import torch
# import torch.nn as nn
# import torch.optim as optim
# from torch.utils.data import Dataset, DataLoader
# import numpy as np
# import os
# import pandas as pd
# import ast

# # PostgreSQL 배열 스트링 파싱 함수
# def parse_pg_array(array_str):
#     if pd.isna(array_str) or not isinstance(array_str, str):
#         return []
#     try:
#         return ast.literal_eval(array_str.replace('{', '[').replace('}', ']'))
#     except:
#         return []

# # 1. 실제 로컬 CSV 데이터를 파이토치 시퀀스로 굽는 커스텀 데이터셋 클래스
# class RobotSequenceDataset(Dataset):
#     def __init__(self, file_path, seq_length=30):
#         self.seq_length = seq_length
        
#         # 파일 확인 및 로드
#         if not os.path.exists(file_path):
#             raise FileNotFoundError(f"❌ 데이터 파일을 찾을 수 없습니다: {file_path}")
            
#         column_names = [
#             'id', 'db_timestamp', 'emergency_stopped', 'safety_stopped', 
#             'joint_motor_current', 'joint_motor_temperature', 'joint_torque', 
#             'joint_velocity', 'joint_position', 'direct_teach_pressed', 
#             'robot_device_id', 'robot_state', 'collision_detected', 'servo_on_robot', 
#             'task_external_force', 'task_external_moment', 'task_velocity', 
#             'task_orientation', 'task_position', 'user_custom_data', 'created_at', 
#             'reserved_field_1', 'reserved_field_2', 'firmware_version'
#         ]
        
#         df = pd.read_csv(file_path, names=column_names, header=None if pd.read_csv(file_path, nrows=1).columns[0] == 'ba621164-0fbc-4312-b058-24e414b68993' else 0)
        
#         # 배열 컬럼 전처리
#         df['joint_motor_current'] = df['joint_motor_current'].apply(parse_pg_array)
#         df['joint_motor_temperature'] = df['joint_motor_temperature'].apply(parse_pg_array)
        
#         # 한국 시간 기준 시계열 정렬
#         df['created_at'] = pd.to_datetime(df['created_at'])
#         df = df.sort_values(by='created_at').reset_index(drop=True)
        
#         # 6개 관절 전류와 온도를 추출하여 모델에 넣을 (8202, 12) 차원의 2차원 넘파이 행렬 구축
#         features = []
#         for i in range(len(df)):
#             curr = df['joint_motor_current'].iloc[i]
#             temp = df['joint_motor_temperature'].iloc[i]
#             # 예외 처리: 데이터가 비어있을 경우 0으로 패딩
#             if len(curr) < 6: curr = [0.0]*6
#             if len(temp) < 6: temp = [0.0]*6
#             features.append(curr + temp)
            
#         self.data_matrix = np.array(features, dtype=np.float32)
        
#         # Z-Score 데이터 스케일 정규화
#         self.mean = np.mean(self.data_matrix, axis=0)
#         self.std = np.std(self.data_matrix, axis=0) + 1e-5
#         self.data_matrix = (self.data_matrix - self.mean) / self.std
        
#         # 공식 매핑을 위한 가상 정답 레이블 세팅 (실전 연구 목적용 아키텍처 결합)
#         total_len = len(df)
#         self.labels_failure = np.zeros((total_len, 1), dtype=np.float32)
#         self.labels_rul = np.zeros((total_len, 1), dtype=np.float32)
        
#         for i in range(total_len):
#             # 예시: 가동 로그 후반부로 갈수록 장비의 잔여 수명이 줄어들도록 선형 매핑 (RUL 정석)
#             self.labels_rul[i, 0] = float(total_len - i) 
#             # 예시: 특정 열화 임계치를 일시적으로 넘은 기록이 있다면 고장 위험 플래그(1) 부여
#             if i % 250 == 0:
#                 self.labels_failure[i, 0] = 1.0

#     def __len__(self):
#         return len(self.data_matrix) - self.seq_length

#     def __getitem__(self, idx):
#         # Sliding Window 30개 시퀀스 추출
#         x_seq = self.data_matrix[idx : idx + self.seq_length]
        
#         # 시퀀스의 마지막 시점 기준 정답(Target) 매핑
#         y_fail = self.labels_failure[idx + self.seq_length]
#         y_rul = self.labels_rul[idx + self.seq_length]
        
#         return torch.tensor(x_seq), torch.tensor(y_fail), torch.tensor(y_rul)

# # 2. 1D-CNN + LSTM 하이브리드 모델 정의
# class RobotPredictiveModel(nn.Module):
#     def __init__(self, input_dim, sequence_length):
#         super(RobotPredictiveModel, self).__init__()
#         self.conv1d = nn.Conv1d(in_channels=input_dim, out_channels=32, kernel_size=3, padding=1)
#         self.relu = nn.ReLU()
#         self.lstm = nn.LSTM(input_size=32, hidden_size=64, num_layers=2, batch_first=True)
        
#         # 공식 1 (분류) 출력 탑
#         self.fc_classifier = nn.Sequential(
#             nn.Linear(64, 16),
#             nn.ReLU(),
#             nn.Linear(16, 1),
#             nn.Sigmoid()
#         )
#         # 공식 2 (회귀 - RUL) 출력 탑
#         self.fc_regressor = nn.Sequential(
#             nn.Linear(64, 16),
#             nn.ReLU(),
#             nn.Linear(16, 1)
#         )

#     def forward(self, x):
#         x = x.transpose(1, 2) # (Batch, input_dim, seq_len)
#         x = self.relu(self.conv1d(x))
#         x = x.transpose(1, 2) # (Batch, seq_len, channels)
#         lstm_out, _ = self.lstm(x)
#         last_hidden = lstm_out[:, -1, :]
        
#         return self.fc_classifier(last_hidden), self.fc_regressor(last_hidden)

# # 3. 메인 실행 파이프라인
# def run_hybrid_training():
#     print("🚀 [AI 하이브리드] 실제 로컬 CSV 연동 학습 파이프라인 시작...")
    
#     # 디렉토리 경로 자동 추적
#     base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
#     csv_file_path = os.path.join(base_dir, "resource", "Cloud_SQL_Export_2026-05-26 (06_48_03).csv")
    
#     sequence_length = 30
#     input_dim = 12 # 6개 전류 + 6개 온도
    
#     # 커스텀 데이터셋 및 데이터로더 연결
#     dataset = RobotSequenceDataset(csv_file_path, seq_length=sequence_length)
#     dataloader = DataLoader(dataset, batch_size=16, shuffle=True)
    
#     model = RobotPredictiveModel(input_dim, sequence_length)
#     optimizer = optim.Adam(model.parameters(), lr=0.001)
    
#     criterion_bce = nn.BCELoss()
#     criterion_mse = nn.MSELoss()
    
#     # 배치 대입 학습 피드포워드 테스트 (실제 첫 번째 배치 가동)
#     model.train()
#     for batch_x, batch_y_fail, batch_y_rul in dataloader:
#         optimizer.zero_grad()
        
#         # 가상이 아닌 '진짜 데이터' 대입
#         pred_prob, pred_rul = model(batch_x)
        
#         loss_classification = criterion_bce(pred_prob, batch_y_fail)
#         loss_regression = criterion_mse(pred_rul, batch_y_rul)
        
#         # 수식 결합 및 가중치 업데이트 역전파
#         total_loss = loss_classification + (loss_regression * 0.000001)
#         total_loss.backward()
#         optimizer.step()
        
#         print("\n✅ [실전 데이터 피딩] 1D-CNN + LSTM 1개 배치 학습 완료")
#         print(f"📉 [공식 1 실전] Binary Cross-Entropy Loss: {loss_classification.item():.4f}")
#         print(f"🧮 [공식 2 실전] RUL Regression MSE Loss: {loss_regression.item():.2f}")
#         print(f"🧬 [종합 실전 최적화] Combined Total Loss: {total_loss.item():.4f}")
#         break # 아키텍처 매핑 확인을 위해 1개 배치만 수행 후 정지

# if __name__ == "__main__":
#     run_hybrid_training()