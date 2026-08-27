#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dot_validation_final_generalized_cells_v3_slopefix_v2_guidefix.py

요청 반영 버전:
- 점들을 먼저 찾는다.
- x좌표 기준으로 "세로 점 기둥(column)"을 만든다.
- column 사이 간격이 좁으면 같은 점자 칸, 간격이 넓으면 다른 점자 칸으로 나눈다.
- 이렇게 얻은 칸별 점형을 한글 점자로 해석한다.
- 실행 argument 라벨과 비교한다.

실행:
    python dot_validation_final_generalized_cells_v3_slopefix_v2_guidefix.py "안녕" --show --verbose

점이 너무 많으면:
    python dot_validation_final_generalized_cells_v3_slopefix_v2_guidefix.py "안녕" --threshold 1.3 --show --verbose

점이 너무 적으면:
    python dot_validation_final_generalized_cells_v3_slopefix_v2_guidefix.py "안녕" --threshold 0.8 --show --verbose

주의:
- 이 코드는 라벨을 해석에 끼워 넣지 않는다.
- 라벨은 마지막 비교용이다.
- 단, 라벨 기준 점형은 verbose 디버그에만 출력된다.
"""

import argparse
import json
import math
import sys
from datetime import datetime

import cv2
import numpy as np


# ============================================================
# 점 검출 민감도 기본값
# ============================================================
# 점들이 서로 뭉쳐서 한 덩어리로 잡히면:
#   --merge-scale 값을 낮춘다. 예: 0.025, 0.020
#   --close-iter 0 으로 morphology close를 끈다.
#
# 점 하나가 여러 조각으로 너무 쪼개지면:
#   --merge-scale 값을 올린다. 예: 0.045, 0.055
#   --close-iter 1 로 둔다.
DEFAULT_MERGE_SCALE = 0.028
DEFAULT_MIN_MERGE_DIST = 2.2
DEFAULT_CLOSE_ITER = 0
MEDIAN_KSIZE = 3

# Enhanced -> Binary 단계 threshold 설정
# 값이 클수록 확실한 점만 흰색으로 남는다.
DEFAULT_THRESHOLD = 1.35
DEFAULT_PERCENTILE = 98.7
DEFAULT_STD_SCALE = 1.35

# 붙어 있는 6점이 하나의 큰 blob으로 잡히는 문제 방지용
# enhanced에서 넓게 밝아진 영역을 제거하고, 작은 local peak만 남긴다.
DEFAULT_PEAK_SIGMA = 3.0
DEFAULT_PEAK_PERCENTILE = 97.8
DEFAULT_PEAK_KERNEL = 5
DEFAULT_DOT_RADIUS = 2

# 여러 줄 분석 시, 줄별 ROI를 다시 threshold하면 약한 점이 사라질 수 있다.
# 기본값은 전체 ROI에서 만든 base binary를 잘라서 사용한다.
DEFAULT_USE_BASE_BINARY_FOR_LINES = True

# row slope 제한
# 가이드라인을 맞춰 촬영한다는 전제에서는 큰 기울기 보정이 오히려 오판을 만든다.
# 기본값 0.06: 아주 약한 기울기만 허용
DEFAULT_ROW_SLOPE_MAX = 0.06
ROW_SLOPE_MAX = DEFAULT_ROW_SLOPE_MAX

# ============================================================
# 1. 한글 점자표
# ============================================================
INITIAL_TO_BRAILLE = {
    "ㄱ": "4", "ㄴ": "14", "ㄷ": "24", "ㄹ": "5", "ㅁ": "15", "ㅂ": "45",
    "ㅅ": "6", "ㅈ": "46", "ㅊ": "56", "ㅋ": "124", "ㅌ": "125", "ㅍ": "145", "ㅎ": "245",
}

VOWEL_TO_BRAILLE = {
    "ㅏ": "126", "ㅑ": "345", "ㅓ": "234", "ㅕ": "156", "ㅗ": "136", "ㅛ": "346",
    "ㅜ": "134", "ㅠ": "146", "ㅡ": "246", "ㅣ": "135", "ㅐ": "1235", "ㅔ": "1345",
    "ㅖ": "34", "ㅘ": "1236", "ㅝ": "1234", "ㅚ": "13456", "ㅢ": "2456",
}

FINAL_TO_BRAILLE = {
    "ㄱ": "1", "ㄴ": "25", "ㄷ": "35", "ㄹ": "2", "ㅁ": "26", "ㅂ": "12",
    "ㅅ": "3", "ㅇ": "2356", "ㅈ": "13", "ㅊ": "23", "ㅋ": "123",
    "ㅌ": "236", "ㅍ": "1234", "ㅎ": "356",
}

BRAILLE_TO_INITIAL = {v: k for k, v in INITIAL_TO_BRAILLE.items()}
BRAILLE_TO_VOWEL = {v: k for k, v in VOWEL_TO_BRAILLE.items()}
BRAILLE_TO_FINAL = {v: k for k, v in FINAL_TO_BRAILLE.items()}

# ============================================================
# 한글 점자 약자/약어 테이블
# ============================================================
# 약자: 한 점자 칸이 완성 음절처럼 읽히는 경우
# - 가/나/다/마/바/사/자/카/타/파/하
# - 억/언/얼/연/열/영/옥/온/옹/운/울/은/을/인
#
# 주의:
# 14는 초성 ㄴ이기도 하고 약자 "나"이기도 하다.
# 24는 초성 ㄷ이기도 하고 약자 "다"이기도 하다.
# 그래서 단순 치환하지 않고 beam search 후보로 같이 넣는다.
SYLLABLE_CONTRACTIONS = {
    "1246": "가",
    "14": "나",
    "24": "다",
    "15": "마",
    "45": "바",
    "123": "사",
    "46": "자",
    "124": "카",
    "125": "타",
    "145": "파",
    "245": "하",

    "1456": "억",
    "23456": "언",
    "2345": "얼",
    "16": "연",
    "1256": "열",
    "12456": "영",
    "1346": "옥",
    "12356": "온",
    "123456": "옹",
    "1245": "운",
    "12346": "울",
    "1356": "은",
    "2346": "을",
    "12345": "인",
}

# 약자 중 "것"은 두 칸으로 적는다.
# 456 + 234
TWO_CELL_SYLLABLE_CONTRACTIONS = {
    ("456", "234"): "것",
}

# 약어: 특정 단어를 2칸으로 줄여 쓰는 경우
# 그래서 as, 그러나 ac, 그러면 a3, 그러므로 a5, 그런데 an, 그리고 au, 그리하여 a:
# 점 번호로 바꾸면 아래와 같다.
WORD_ABBREVIATIONS = {
    ("1", "234"): "그래서",
    ("1", "14"): "그러나",
    ("1", "25"): "그러면",
    ("1", "26"): "그러므로",
    ("1", "1345"): "그런데",
    ("1", "136"): "그리고",
    ("1", "156"): "그리하여",
}


CHO_LIST = [
    "ㄱ", "ㄲ", "ㄴ", "ㄷ", "ㄸ", "ㄹ", "ㅁ", "ㅂ", "ㅃ", "ㅅ",
    "ㅆ", "ㅇ", "ㅈ", "ㅉ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ"
]
JUNG_LIST = [
    "ㅏ", "ㅐ", "ㅑ", "ㅒ", "ㅓ", "ㅔ", "ㅕ", "ㅖ", "ㅗ", "ㅘ",
    "ㅙ", "ㅚ", "ㅛ", "ㅜ", "ㅝ", "ㅞ", "ㅟ", "ㅠ", "ㅡ", "ㅢ", "ㅣ"
]
JONG_LIST = [
    "", "ㄱ", "ㄲ", "ㄳ", "ㄴ", "ㄵ", "ㄶ", "ㄷ", "ㄹ", "ㄺ",
    "ㄻ", "ㄼ", "ㄽ", "ㄾ", "ㄿ", "ㅀ", "ㅁ", "ㅂ", "ㅄ", "ㅅ",
    "ㅆ", "ㅇ", "ㅈ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ"
]

CHO_INDEX = {c: i for i, c in enumerate(CHO_LIST)}
JUNG_INDEX = {c: i for i, c in enumerate(JUNG_LIST)}
JONG_INDEX = {c: i for i, c in enumerate(JONG_LIST)}


def compose_hangul(cho, jung, jong=""):
    if cho not in CHO_INDEX or jung not in JUNG_INDEX or jong not in JONG_INDEX:
        return None
    code = 0xAC00 + (CHO_INDEX[cho] * 21 + JUNG_INDEX[jung]) * 28 + JONG_INDEX[jong]
    return chr(code)


def decompose_hangul_char(ch):
    code = ord(ch)
    if not (0xAC00 <= code <= 0xD7A3):
        return None
    base = code - 0xAC00
    cho = base // (21 * 28)
    jung = (base % (21 * 28)) // 28
    jong = base % 28
    return CHO_LIST[cho], JUNG_LIST[jung], JONG_LIST[jong]



# ============================================================
# 사용자 프로젝트 기준 expected 점형 오버라이드
# ============================================================
# key는 공백/줄바꿈 제거 후 문자열 기준.
# 예: "명함 주문 제작", "명함\n주문\n제작", "명함주문제작" 모두 "명함주문제작"으로 비교된다.
CUSTOM_EXPECTED_CELLS = {
    # 필요할 때만 예외 문구를 여기에 추가한다.
    # 기본 동작은 아래 label_to_cells()의 일반화된 변환 알고리즘을 사용한다.
}


def normalize_label_key(label):
    return "".join(str(label).split())


def label_to_cells(label, use_custom=True):
    """
    라벨을 프로젝트 기준 점형으로 변환한다.

    이 함수는 특정 단어만 하드코딩하지 않고, 아래 일반 규칙으로 변환한다.

    변환 우선순위:
      1. CUSTOM_EXPECTED_CELLS에 등록된 예외 문구가 있으면 그 점형 사용
      2. 단어 약어 사용
         예: 그래서, 그러나, 그러면, 그러므로, 그런데, 그리고, 그리하여
      3. 한 글자 완성 약자 사용
         예: 가, 나, 다, 마, 바, 사, 자, 카, 타, 파, 하
             억, 언, 얼, 연, 열, 영, 옥, 온, 옹, 운, 울, 은, 을, 인
      4. 초성 + ㅏ + 종성 구조에서 '초성+ㅏ' 약자를 먼저 쓰고 종성을 붙임
         예:
           함 = 하(245) + ㅁ받침(26)
           작 = 자(46) + ㄱ받침(1)
           간 = 가(1246) + ㄴ받침(25)
      5. 초성이 있는 음절에서 'ㅇ+중성+종성' 부분이 약자면
         초성 + 해당 약자로 처리
         예:
           명 = ㅁ(15) + 영(12456)
           문 = ㅁ(15) + 운(1245)
           녕 = ㄴ(14) + 영(12456)
      6. 위 규칙에 없으면 초성/중성/종성으로 기본 분해
    """
    if use_custom:
        key = normalize_label_key(label)
        if key in CUSTOM_EXPECTED_CELLS:
            return list(CUSTOM_EXPECTED_CELLS[key])

    cells = []

    # 단어 약어 역매핑
    word_to_codes = {word: list(codes) for codes, word in WORD_ABBREVIATIONS.items()}

    # 1칸 약자 역매핑
    syllable_to_code = {syllable: code for code, syllable in SYLLABLE_CONTRACTIONS.items()}

    i = 0
    while i < len(label):
        # 1. 단어 약어 우선 매칭
        matched = False
        for word in sorted(word_to_codes.keys(), key=len, reverse=True):
            if label.startswith(word, i):
                cells.extend(word_to_codes[word])
                i += len(word)
                matched = True
                break

        if matched:
            continue

        ch = label[i]
        i += 1

        if ch.isspace():
            continue

        # 2. 특수 2칸 약자: 것 = 456 + 234
        if ch == "것":
            cells.extend(["456", "234"])
            continue

        dec = decompose_hangul_char(ch)
        if dec is None:
            continue

        cho, jung, jong = dec

        # 3. 완성 음절이 1칸 약자에 해당하면 바로 약자 사용
        # 예: 영 -> 12456, 운 -> 1245, 자 -> 46, 하 -> 245
        if ch in syllable_to_code:
            cells.append(syllable_to_code[ch])
            continue

        # 4. 초성 + ㅏ + 받침 구조 일반화
        # 예: 함 = 하(245)+ㅁ(26), 작 = 자(46)+ㄱ(1)
        #     간 = 가(1246)+ㄴ(25), 산 = 사(123)+ㄴ(25)
        if cho != "ㅇ" and jung == "ㅏ" and jong:
            cv_syllable = compose_hangul(cho, "ㅏ", "")
            if cv_syllable in syllable_to_code and jong in FINAL_TO_BRAILLE:
                cells.append(syllable_to_code[cv_syllable])
                cells.append(FINAL_TO_BRAILLE[jong])
                continue

        # 5. 초성 + 모음시작 약자 일반화
        # 예: 명 = ㅁ(15)+영(12456), 문 = ㅁ(15)+운(1245)
        if cho != "ㅇ":
            remainder = compose_hangul("ㅇ", jung, jong)
            if remainder in syllable_to_code:
                if cho in INITIAL_TO_BRAILLE:
                    cells.append(INITIAL_TO_BRAILLE[cho])
                cells.append(syllable_to_code[remainder])
                continue

        # 6. 기본 풀어쓰기
        if cho != "ㅇ" and cho in INITIAL_TO_BRAILLE:
            cells.append(INITIAL_TO_BRAILLE[cho])

        if jung in VOWEL_TO_BRAILLE:
            cells.append(VOWEL_TO_BRAILLE[jung])

        if jong and jong in FINAL_TO_BRAILLE:
            cells.append(FINAL_TO_BRAILLE[jong])

    return cells

def code_to_mask(code):
    mask = 0
    for ch in str(code):
        if ch.isdigit():
            n = int(ch)
            if 1 <= n <= 6:
                mask |= (1 << (n - 1))
    return mask


def mask_to_code(mask):
    return "".join(str(i + 1) for i in range(6) if mask & (1 << i))


def popcount(x):
    return int(bin(int(x)).count("1"))


# ============================================================
# 2. 웹캠 캡쳐 / ROI 선택
# ============================================================

def draw_capture_guide(
    frame,
    guide_lines=3,
    guide_width_ratio=0.78,
    guide_height_ratio=0.72,
):
    """
    웹캠 캡쳐 단계에서 점자 카드/점자 줄을 수평에 가깝게 맞추기 위한 가이드.

    guide_lines:
      0이면 전체 큰 박스만 표시
      1 이상이면 점자 줄 수에 맞춰 가로 레인 표시

    목적:
      - 점자 줄이 카메라 화면에서 기울어지는 것을 줄임
      - row_slope가 튀는 문제를 사전에 방지
      - ROI 선택 전에 카드 위치/수평을 맞추기 쉽게 함
    """
    h, w = frame.shape[:2]
    out = frame.copy()
    overlay = frame.copy()

    guide_lines = int(max(0, guide_lines))

    box_w = int(w * float(guide_width_ratio))
    box_h = int(h * float(guide_height_ratio))

    x0 = (w - box_w) // 2
    y0 = (h - box_h) // 2
    x1 = x0 + box_w
    y1 = y0 + box_h

    # 전체 가이드 박스
    cv2.rectangle(overlay, (x0, y0), (x1, y1), (0, 255, 255), 2)

    # 중앙 수직선/수평선
    cx = (x0 + x1) // 2
    cy = (y0 + y1) // 2
    cv2.line(overlay, (cx, y0), (cx, y1), (255, 255, 0), 1)
    cv2.line(overlay, (x0, cy), (x1, cy), (255, 255, 0), 1)

    if guide_lines > 0:
        lane_h = box_h / float(guide_lines)

        for i in range(guide_lines):
            ly0 = int(round(y0 + i * lane_h))
            ly1 = int(round(y0 + (i + 1) * lane_h))
            lcy = int(round((ly0 + ly1) / 2))

            # 줄별 박스
            cv2.rectangle(overlay, (x0, ly0), (x1, ly1), (0, 255, 255), 1)

            # 해당 줄 중앙 기준선
            cv2.line(overlay, (x0, lcy), (x1, lcy), (0, 255, 0), 1)

            # 점자 한 줄 안의 3행 위치를 대략적으로 표시
            # 실제 점자 1/2/3행이 이 선들과 평행하게 보이면 기울기 오차가 줄어듦.
            row_gap = max(5, int((ly1 - ly0) * 0.20))
            for ry in (lcy - row_gap, lcy, lcy + row_gap):
                if ly0 < ry < ly1:
                    cv2.line(overlay, (x0, ry), (x1, ry), (0, 180, 255), 1)

            cv2.putText(
                overlay,
                f"Line {i + 1}",
                (x0 + 8, ly0 + 24),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (0, 255, 255),
                2,
            )

    # 반투명 합성
    alpha = 0.35
    cv2.addWeighted(overlay, alpha, out, 1.0 - alpha, 0, out)

    # 외곽/중요 선은 한 번 더 진하게
    cv2.rectangle(out, (x0, y0), (x1, y1), (0, 255, 255), 2)

    info1 = "Align braille rows with horizontal guide lines"
    info2 = "SPACE: capture | ESC: quit"

    cv2.putText(out, info1, (20, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2)
    cv2.putText(out, info2, (20, 62), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 0), 2)

    return out


def capture_frame(
    camera_index=0,
    mirror=False,
    guide=True,
    guide_lines=3,
    guide_width_ratio=0.78,
    guide_height_ratio=0.72,
):
    cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        raise RuntimeError("웹캠을 열 수 없습니다.")

    print("[INFO] 웹캠 창에서 SPACE를 누르면 캡쳐, ESC를 누르면 종료합니다.", flush=True)
    if guide:
        print("[INFO] 노란 가이드 박스 안에 점자 영역을 넣고, 점자 줄을 초록/주황 가이드라인과 평행하게 맞추세요.", flush=True)

    frame = None
    while True:
        ret, img = cap.read()
        if not ret:
            continue

        img = cv2.flip(img, 1)
        
           

        if guide:
            view = draw_capture_guide(
                img,
                guide_lines=guide_lines,
                guide_width_ratio=guide_width_ratio,
                guide_height_ratio=guide_height_ratio,
            )
        else:
            view = img.copy()
            cv2.putText(
                view,
                "SPACE: capture | ESC: quit",
                (20, 35),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 255, 0),
                2,
            )

        cv2.imshow("Webcam Capture", view)

        key = cv2.waitKey(1) & 0xFF
        if key == 27:
            break
        if key == 32:
            frame = img.copy()
            break

    cap.release()
    cv2.destroyWindow("Webcam Capture")

    if frame is None:
        raise RuntimeError("캡쳐가 취소되었습니다.")

    return frame


def select_roi_by_mouse(frame):
    win = "Select Braille ROI"
    state = {"dragging": False, "has_box": False, "x0": 0, "y0": 0, "x1": 0, "y1": 0}

    def mouse_callback(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            state["dragging"] = True
            state["has_box"] = True
            state["x0"], state["y0"], state["x1"], state["y1"] = x, y, x, y
        elif event == cv2.EVENT_MOUSEMOVE and state["dragging"]:
            state["x1"], state["y1"] = x, y
        elif event == cv2.EVENT_LBUTTONUP:
            state["dragging"] = False
            state["x1"], state["y1"] = x, y
            state["has_box"] = True

    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(win, mouse_callback)

    print("[INFO] 점자 영역을 드래그한 뒤 SPACE 또는 ENTER를 누르세요.", flush=True)
    print("[INFO] R: 다시 선택 / ESC: 취소", flush=True)

    while True:
        display = frame.copy()
        cv2.putText(
            display,
            "Drag ROI | SPACE/ENTER: confirm | R: reset | ESC: cancel",
            (20, 35),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (0, 255, 0),
            2,
        )

        if state["has_box"]:
            cv2.rectangle(display, (state["x0"], state["y0"]), (state["x1"], state["y1"]), (0, 255, 255), 2)

        cv2.imshow(win, display)
        key = cv2.waitKey(20) & 0xFF

        if key == 27:
            cv2.destroyWindow(win)
            raise RuntimeError("ROI 선택이 취소되었습니다.")
        if key in (ord("r"), ord("R")):
            state["dragging"] = False
            state["has_box"] = False
            print("[INFO] ROI 선택 초기화", flush=True)
            continue
        if key in (13, 10, 32):
            if not state["has_box"]:
                print("[WARN] ROI를 먼저 드래그하세요.", flush=True)
                continue

            x0 = min(state["x0"], state["x1"])
            y0 = min(state["y0"], state["y1"])
            x1 = max(state["x0"], state["x1"])
            y1 = max(state["y0"], state["y1"])
            w = x1 - x0
            h = y1 - y0

            if w < 5 or h < 5:
                print("[WARN] ROI가 너무 작습니다.", flush=True)
                continue

            cv2.destroyWindow(win)
            return frame[y0:y1, x0:x1].copy(), (x0, y0, w, h)


# ============================================================
# 3. gray scale 변환 + 점 검출
# ============================================================
def preprocess_gray_for_dots(roi, threshold_boost=DEFAULT_THRESHOLD, close_iter=DEFAULT_CLOSE_ITER, percentile=DEFAULT_PERCENTILE, std_scale=DEFAULT_STD_SCALE, peak_sigma=DEFAULT_PEAK_SIGMA, peak_percentile=DEFAULT_PEAK_PERCENTILE, peak_kernel=DEFAULT_PEAK_KERNEL, dot_radius=DEFAULT_DOT_RADIUS):
    print("[INFO] gray scale 변환", flush=True)
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

    print("[INFO] 점 후보 강조", flush=True)
    small = cv2.GaussianBlur(gray, (3, 3), 0)
    large = cv2.GaussianBlur(gray, (21, 21), 0)
    diff = cv2.absdiff(small, large)

    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11))
    top = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, k)
    black = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, k)

    enhanced = cv2.addWeighted(diff, 1.0, top, 0.8, 0)
    enhanced = cv2.addWeighted(enhanced, 1.0, black, 0.8, 0)
    enhanced = cv2.normalize(enhanced, None, 0, 255, cv2.NORM_MINMAX)

    # ------------------------------------------------------------
    # 핵심 수정:
    # 기존에는 enhanced 자체를 threshold해서, 6개 점 사이에 생긴 회색/밝은 면적이
    # 한 덩어리 blob으로 잡히는 문제가 있었다.
    #
    # 이제는 enhanced에서 "넓게 밝아진 영역"을 한 번 더 제거한다.
    # broad = 큰 blur
    # peak_map = enhanced - broad
    # 이렇게 하면 큰 덩어리 면적은 사라지고, 각 점의 국소 peak만 남는다.
    # ------------------------------------------------------------
    broad = cv2.GaussianBlur(
        enhanced,
        (0, 0),
        sigmaX=float(peak_sigma),
        sigmaY=float(peak_sigma),
    )
    peak_map = cv2.subtract(enhanced, broad)
    peak_map = cv2.normalize(peak_map, None, 0, 255, cv2.NORM_MINMAX)

    mean = float(np.mean(peak_map))
    std = float(np.std(peak_map))

    # enhanced가 아니라 peak_map 기준으로 threshold
    p = float(np.percentile(peak_map, float(peak_percentile)))
    p2 = float(np.percentile(peak_map, float(percentile)))
    base_th = max(p, p2, mean + float(std_scale) * std)
    th = int(max(15, min(250, base_th * float(threshold_boost))))

    # 단순 threshold mask
    _, rough = cv2.threshold(peak_map, th, 255, cv2.THRESH_BINARY)

    if MEDIAN_KSIZE > 1:
        rough = cv2.medianBlur(rough, MEDIAN_KSIZE)

    # ------------------------------------------------------------
    # local maxima 기반으로 점 중심만 뽑기
    # rough 영역 전체를 쓰지 않고, peak_map의 지역 최대점만 흰색 점으로 찍는다.
    # 붙어 있는 6점이 하나의 넓은 blob이 되어도 내부 local peak가 여러 개면 분리된다.
    # ------------------------------------------------------------
    ksize = int(peak_kernel)
    if ksize < 3:
        ksize = 3
    if ksize % 2 == 0:
        ksize += 1

    local_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ksize, ksize))
    local_max = cv2.dilate(peak_map, local_kernel)

    maxima = np.zeros_like(peak_map, dtype=np.uint8)
    maxima[(peak_map == local_max) & (peak_map >= th) & (rough > 0)] = 255

    # plateau가 생기면 한 peak가 여러 픽셀로 나올 수 있으므로 component 중심으로 다시 정리
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(maxima, connectivity=8)

    binary = np.zeros_like(peak_map, dtype=np.uint8)
    radius = max(1, int(dot_radius))

    for label_idx in range(1, num_labels):
        area = stats[label_idx, cv2.CC_STAT_AREA]
        if area <= 0:
            continue

        cx, cy = centroids[label_idx]
        cv2.circle(binary, (int(round(cx)), int(round(cy))), radius, 255, -1)

    # close는 기본적으로 사용하지 않는다.
    # 점 하나가 여러 조각으로 쪼개지는 경우에만 --close-iter 1을 사용한다.
    if close_iter > 0:
        binary = cv2.morphologyEx(
            binary,
            cv2.MORPH_CLOSE,
            np.ones((2, 2), np.uint8),
            iterations=int(close_iter),
        )

    # 사용자에게 보이는 3 Enhanced 창도 이제 peak_map으로 보여준다.
    # 원래 enhanced보다 "점 중심만 밝은 영상"이므로 뭉침 원인을 확인하기 쉽다.
    return gray, peak_map, binary, th


def merge_close_points(points, merge_dist):
    if not points:
        return []

    used = [False] * len(points)
    merged = []

    for i, p in enumerate(points):
        if used[i]:
            continue

        cluster = [p]
        used[i] = True

        changed = True
        while changed:
            changed = False
            total = sum(q["weight"] for q in cluster)
            cx = sum(q["x"] * q["weight"] for q in cluster) / max(total, 1e-6)
            cy = sum(q["y"] * q["weight"] for q in cluster) / max(total, 1e-6)

            for j, q in enumerate(points):
                if used[j]:
                    continue
                if math.hypot(q["x"] - cx, q["y"] - cy) <= merge_dist:
                    cluster.append(q)
                    used[j] = True
                    changed = True

        total = sum(q["weight"] for q in cluster)
        mx = sum(q["x"] * q["weight"] for q in cluster) / max(total, 1e-6)
        my = sum(q["y"] * q["weight"] for q in cluster) / max(total, 1e-6)

        merged.append({"x": float(mx), "y": float(my), "weight": float(total), "parts": len(cluster)})

    merged.sort(key=lambda d: (d["x"], d["y"]))
    return merged


def detect_dots(binary, roi_shape, merge_scale=DEFAULT_MERGE_SCALE, min_merge_dist=DEFAULT_MIN_MERGE_DIST):
    print("[INFO] 점 검출", flush=True)

    h, w = roi_shape[:2]
    roi_area = float(w * h)

    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    raw = []
    min_area = max(1.0, roi_area * 0.000008)
    max_area = max(50.0, roi_area * 0.025)

    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < min_area or area > max_area:
            continue

        x, y, bw, bh = cv2.boundingRect(cnt)

        if bw > w * 0.25 or bh > h * 0.45:
            continue
        if bw < 1 or bh < 1:
            continue

        aspect = bw / bh if bh else 999.0
        if not (0.16 <= aspect <= 6.5):
            continue

        M = cv2.moments(cnt)
        if M["m00"] == 0:
            cx = x + bw / 2.0
            cy = y + bh / 2.0
        else:
            cx = M["m10"] / M["m00"]
            cy = M["m01"] / M["m00"]

        raw.append({
            "x": float(cx), "y": float(cy), "weight": float(max(area, 1.0)),
            "area": float(area), "bbox": [int(x), int(y), int(bw), int(bh)]
        })

    # 점들이 뭉쳐서 하나로 합쳐지면 merge_scale을 낮춘다.
    # 기존 0.050은 가까운 점들을 과하게 합칠 수 있어서 기본값을 0.028로 낮춤.
    merge_dist = max(float(min_merge_dist), min(w, h) * float(merge_scale))
    return merge_close_points(raw, merge_dist)


# ============================================================
# 4. 간격 기반 칸 묶기
# ============================================================
def kmeans_1d_three(values, init_centers=None, iterations=25):
    values = np.array(values, dtype=np.float32)

    if len(values) == 0:
        return [0.0, 10.0, 20.0], np.zeros(0, dtype=np.int32), 999999.0

    if init_centers is None:
        centers = np.percentile(values, [12, 50, 88]).astype(np.float32)
    else:
        centers = np.array(init_centers, dtype=np.float32)

    for _ in range(iterations):
        labels = np.argmin(np.abs(values[:, None] - centers[None, :]), axis=1)
        new_centers = centers.copy()

        for k in range(3):
            vals = values[labels == k]
            if len(vals) > 0:
                new_centers[k] = vals.mean()

        if np.max(np.abs(new_centers - centers)) < 0.05:
            break

        centers = new_centers

    order = np.argsort(centers)
    sorted_centers = centers[order]
    remap = {old: new for new, old in enumerate(order)}
    sorted_labels = np.array([remap[int(lb)] for lb in labels], dtype=np.int32)

    error = 0.0
    for v, lb in zip(values, sorted_labels):
        error += float((v - sorted_centers[lb]) ** 2)
    error /= max(len(values), 1)

    return [float(c) for c in sorted_centers], sorted_labels, float(error)


def estimate_sloped_rows(points, roi_h):
    """
    한 줄 안의 3개 row를 추정한다.

    수정:
      - ROW_SLOPE_MAX로 slope 탐색 범위를 제한한다.
      - 기본값은 0.06.
      - --row-slope-max 0 을 주면 완전 수평 row만 사용한다.
    """
    global ROW_SLOPE_MAX

    xs = np.array([p["x"] for p in points], dtype=np.float32)
    ys = np.array([p["y"] for p in points], dtype=np.float32)

    if len(points) < 3:
        center = float(np.median(ys)) if len(ys) else roi_h / 2
        pitch = max(roi_h * 0.16, 8.0)
        return {
            "slope": 0.0,
            "centers": [center - pitch, center, center + pitch],
            "row_pitch": float(pitch),
            "reason": "too_few_points",
        }

    def build_model(slope):
        y_corr = ys - slope * xs
        centers, labels, error = kmeans_1d_three(y_corr)

        sep1 = centers[1] - centers[0]
        sep2 = centers[2] - centers[1]
        sep = min(sep1, sep2)

        valid = sep >= max(5.0, roi_h * 0.045)
        balance = min(np.bincount(labels, minlength=3)) / max(len(points), 1)

        # error는 작을수록 좋고, sep/balance는 클수록 좋다.
        # slope가 큰 모델은 penalty를 줘서 과보정을 막는다.
        score = error - 1.5 * sep - 6.0 * balance + 10.0 * abs(slope)

        return {
            "valid": valid,
            "score": float(score),
            "slope": float(slope),
            "centers": [float(c) for c in centers],
            "labels": labels,
            "error": float(error),
            "sep": float(sep),
            "balance": float(balance),
        }

    horizontal = build_model(0.0)
    best = horizontal

    slope_max = max(0.0, float(ROW_SLOPE_MAX))

    if slope_max > 0:
        steps = max(3, int(round(slope_max / 0.01)) * 2 + 1)

        for slope in np.linspace(-slope_max, slope_max, steps):
            model = build_model(float(slope))

            if not model["valid"]:
                continue

            if model["score"] < best["score"]:
                best = model

    # 수평 모델 대비 개선이 아주 작으면 수평 선택
    if best is not horizontal:
        improvement = horizontal["score"] - best["score"]
        if improvement < 0.8:
            best = horizontal
            reason = "prefer_horizontal"
        else:
            reason = "limited_slope"
    else:
        reason = "horizontal"

    pitch = max((best["centers"][2] - best["centers"][0]) / 2.0, roi_h * 0.12, 8.0)

    return {
        "slope": float(best["slope"]),
        "centers": [float(c) for c in best["centers"]],
        "row_pitch": float(pitch),
        "error": float(best["error"]),
        "sep": float(best["sep"]),
        "reason": reason,
    }


def row_center_y_at_x(row_model, row_index, x):
    return row_model["slope"] * x + row_model["centers"][row_index]


def dot_row_number(x, y, row_model):
    y_corr = y - row_model["slope"] * x
    return int(np.argmin([abs(y_corr - c) for c in row_model["centers"]]))


def make_x_columns(points, row_pitch):

    """
    점들을 x좌표 기준 세로 기둥으로 묶는다.
    같은 세로 기둥 안의 점들은 x가 거의 같다.
    """
    if not points:
        return []

    sorted_pts = sorted(points, key=lambda p: p["x"])
    merge_x = max(5.0, row_pitch * 0.42)

    columns = []
    current = [sorted_pts[0]]

    for p in sorted_pts[1:]:
        cx = sum(q["x"] * q["weight"] for q in current) / max(sum(q["weight"] for q in current), 1e-6)
        if abs(p["x"] - cx) <= merge_x:
            current.append(p)
        else:
            columns.append(current)
            current = [p]
    columns.append(current)

    result = []
    for col in columns:
        total = sum(p["weight"] for p in col)
        cx = sum(p["x"] * p["weight"] for p in col) / max(total, 1e-6)
        result.append({"x": float(cx), "points": col})

    result.sort(key=lambda c: c["x"])
    return result


def gap_threshold_from_columns(columns):
    if len(columns) <= 1:
        return 999999.0, []

    gaps = [columns[i + 1]["x"] - columns[i]["x"] for i in range(len(columns) - 1)]

    if len(gaps) == 1:
        return gaps[0] * 1.2, gaps

    sorted_gaps = sorted(gaps)

    # gap들이 작은 그룹/큰 그룹으로 갈라지는 지점을 찾는다.
    best_i = None
    best_ratio = 1.0

    for i in range(len(sorted_gaps) - 1):
        a = max(sorted_gaps[i], 1e-6)
        b = sorted_gaps[i + 1]
        ratio = b / a
        if ratio > best_ratio:
            best_ratio = ratio
            best_i = i

    if best_i is not None and best_ratio >= 1.30:
        threshold = (sorted_gaps[best_i] + sorted_gaps[best_i + 1]) / 2.0
    else:
        threshold = float(np.median(gaps) * 1.20)

    return threshold, gaps


def split_columns_to_cells(columns):
    """
    핵심:
    - column 사이 gap이 threshold보다 작으면 같은 점자 칸
    - threshold보다 크면 다음 점자 칸
    - 한 점자 칸은 최대 2개 column
    """
    if not columns:
        return [], 0.0, []

    threshold, gaps = gap_threshold_from_columns(columns)

    cells = []
    current = [columns[0]]

    for i, gap in enumerate(gaps):
        next_col = columns[i + 1]

        if len(current) < 2 and gap <= threshold:
            current.append(next_col)
        else:
            cells.append(current)
            current = [next_col]

    cells.append(current)

    return cells, threshold, gaps


def mask_from_column_points(col, row_model, is_right_col):
    mask = 0

    for p in col["points"]:
        row = dot_row_number(p["x"], p["y"], row_model)

        if is_right_col:
            dot = row + 4
        else:
            dot = row + 1

        mask |= (1 << (dot - 1))

    return mask


def make_cell_code_options(cells, row_model):

    """
    각 cell의 점형 후보를 만든다.
    - 2 column cell: 왼쪽/오른쪽 확정
    - 1 column cell: 왼쪽 column일 수도, 오른쪽 column일 수도 있으므로 두 후보 생성
    """
    options = []

    for cell in cells:
        if len(cell) >= 2:
            left_col = cell[0]
            right_col = cell[1]
            mask = mask_from_column_points(left_col, row_model, is_right_col=False)
            mask |= mask_from_column_points(right_col, row_model, is_right_col=True)
            options.append([mask_to_code(mask)])
        else:
            only_col = cell[0]
            left_mask = mask_from_column_points(only_col, row_model, is_right_col=False)
            right_mask = mask_from_column_points(only_col, row_model, is_right_col=True)
            cand = []
            if left_mask:
                cand.append(mask_to_code(left_mask))
            if right_mask and right_mask != left_mask:
                cand.append(mask_to_code(right_mask))
            options.append(cand if cand else [""])

    return options


def fit_braille_by_gaps(points, roi_shape):
    print("[INFO] 간격 기반 점자 칸 묶기", flush=True)

    if len(points) < 2:
        return None

    h, _ = roi_shape[:2]
    row_model = estimate_sloped_rows(points, h)
    row_pitch = row_model["row_pitch"]

    columns = make_x_columns(points, row_pitch)
    cells, gap_threshold, gaps = split_columns_to_cells(columns)
    code_options = make_cell_code_options(cells, row_model)

    decoded = decode_code_options(code_options)

    return {
        "row_model": row_model,
        "row_centers": row_model["centers"],
        "row_slope": row_model["slope"],
        "row_pitch": float(row_pitch),
        "columns": columns,
        "cells": cells,
        "gap_threshold": float(gap_threshold),
        "gaps": [float(g) for g in gaps],
        "code_options": code_options,
        "codes": decoded["codes"],
        "decoded": decoded,
    }


# ============================================================
# 5. 점자 칸 -> 한글 해석
# ============================================================
def edit_distance_mask(input_code, target_code):
    mask = code_to_mask(input_code)
    target = code_to_mask(target_code)
    missing = popcount(target & ~mask)
    extra = popcount(mask & ~target)
    return missing, extra, missing + extra


def classify_cell(code):
    """
    한 점자 칸을 초성/중성/종성 후보로 분류한다.
    약자는 여기서 바로 확정하지 않고 decode_code_sequence에서 별도 후보로 추가한다.
    """
    if code == "":
        return [("EMPTY", "", "", -0.3)]

    exact = []
    if code in BRAILLE_TO_INITIAL:
        exact.append(("I", BRAILLE_TO_INITIAL[code], code, 1.8))
    if code in BRAILLE_TO_VOWEL:
        exact.append(("V", BRAILLE_TO_VOWEL[code], code, 1.8))
    if code in BRAILLE_TO_FINAL:
        exact.append(("F", BRAILLE_TO_FINAL[code], code, 1.8))

    if exact:
        return exact

    candidates = []
    for kind, table in (("I", BRAILLE_TO_INITIAL), ("V", BRAILLE_TO_VOWEL), ("F", BRAILLE_TO_FINAL)):
        for target_code, char in table.items():
            missing, extra, dist = edit_distance_mask(code, target_code)
            if dist <= 2:
                score = 1.0 - 0.42 * missing - 0.28 * extra
                if len(target_code) == 1:
                    score -= 0.08
                candidates.append((kind, char, target_code, score))

    candidates.append(("?", code, code, -1.0))
    candidates.sort(key=lambda x: x[3], reverse=True)
    return candidates[:5]


def token_candidates_at(codes, i):
    """
    i번째 cell에서 시작할 수 있는 token 후보를 만든다.
    반환: [(skip_count, token, score), ...]
    token = (kind, char, code)
    kind:
      W = 약어 단어
      S = 약자 음절
      I/V/F = 일반 초성/모음/종성
      ? = 미해석
    """
    result = []

    # 2칸 약어 단어
    if i + 1 < len(codes):
        pair = (codes[i], codes[i + 1])
        if pair in WORD_ABBREVIATIONS:
            result.append((2, ("W", WORD_ABBREVIATIONS[pair], "+".join(pair)), 4.2))

        if pair in TWO_CELL_SYLLABLE_CONTRACTIONS:
            result.append((2, ("S", TWO_CELL_SYLLABLE_CONTRACTIONS[pair], "+".join(pair)), 3.2))

    code = codes[i]

    # 1칸 약자 음절
    # 점형 충돌이 많으므로 일반 I/V/F보다 과도하게 높게 주지 않는다.
    if code in SYLLABLE_CONTRACTIONS:
        result.append((1, ("S", SYLLABLE_CONTRACTIONS[code], code), 1.55))

    # 일반 초성/모음/종성 후보
    for kind, ch, target_code, s in classify_cell(code):
        if kind == "EMPTY":
            result.append((1, ("EMPTY", "", ""), s))
        else:
            result.append((1, (kind, ch, target_code), s))

    result.sort(key=lambda x: x[2], reverse=True)
    return result[:8]


def decode_code_sequence(codes):
    """
    약자/약어를 포함해서 cell code sequence를 해석한다.
    """
    beams = [(0, [], 0.0)]  # index, tokens, score

    while beams:
        active = []
        complete = []

        for idx, tokens, score in beams:
            if idx >= len(codes):
                complete.append((tokens, score))
                continue

            for skip, token, token_score in token_candidates_at(codes, idx):
                kind = token[0]
                if kind == "EMPTY":
                    active.append((idx + skip, tokens, score + token_score))
                else:
                    active.append((idx + skip, tokens + [token], score + token_score))

        if not active:
            final_beams = complete
            break

        active.sort(key=lambda x: x[2], reverse=True)
        beams = active[:120]

        if complete:
            # active와 complete를 함께 관리
            beams.extend([(len(codes), t, s) for t, s in complete])
            beams.sort(key=lambda x: x[2], reverse=True)
            beams = beams[:120]

        # 모든 beam이 끝났으면 종료
        if all(idx >= len(codes) for idx, _, _ in beams):
            final_beams = [(tokens, score) for _, tokens, score in beams]
            break
    else:
        final_beams = []

    results = []
    for tokens, symbol_score in final_beams:
        text, parse, parse_score = assemble_tokens(tokens)
        results.append({
            "text": text if text else "(해석 실패)",
            "parse": parse,
            "score": float(symbol_score + parse_score),
            "tokens": tokens,
        })

    results.sort(key=lambda x: x["score"], reverse=True)

    unique = []
    seen = set()
    for r in results:
        if r["text"] in seen:
            continue
        seen.add(r["text"])
        unique.append(r)
        if len(unique) >= 5:
            break

    return unique


def decode_code_options(code_options):
    """
    single-column cell은 왼쪽/오른쪽 후보가 있으므로 조합 탐색한다.
    """
    beams = [([], 0.0)]

    for options in code_options:
        new_beams = []
        for codes, score in beams:
            for code in options:
                new_beams.append((codes + [code], score))
        beams = new_beams[:160]

    all_results = []
    for codes, _ in beams:
        candidates = decode_code_sequence(codes)
        if candidates:
            best = candidates[0]
            all_results.append({
                "codes": codes,
                "text": best["text"],
                "parse": best["parse"],
                "score": best["score"],
                "candidates": candidates,
            })

    if not all_results:
        return {
            "codes": [],
            "text": "(해석 실패)",
            "parse": [],
            "score": 0.0,
            "candidates": [],
        }

    all_results.sort(key=lambda x: x["score"], reverse=True)
    return all_results[0]


def assemble_tokens(tokens):
    i = 0
    out = []
    parse = []
    score = 0.0

    while i < len(tokens):
        kind, ch, code = tokens[i]

        # 약어 단어
        if kind == "W":
            out.append(ch)
            parse.append(f"약어:{code}->{ch}")
            score += 2.4
            i += 1
            continue

        # 초성 + 약자 음절(억/언/.../영 등)
        # 예: ㄴ + 영 -> 녕
        if kind == "I" and i + 1 < len(tokens) and tokens[i + 1][0] == "S":
            next_syll = tokens[i + 1][1]
            dec = decompose_hangul_char(next_syll)

            if dec is not None:
                next_cho, next_jung, next_jong = dec

                # '영', '억', '언'처럼 초성이 ㅇ인 약자만 앞 초성과 결합
                if next_cho == "ㅇ":
                    syll = compose_hangul(ch, next_jung, next_jong)
                    if syll:
                        out.append(syll)
                        parse.append(f"{ch}+약자:{tokens[i + 1][2]}({next_syll})->{syll}")
                        score += 3.0
                        i += 2
                        continue

        # 약자 음절은 그 자체로 완성 단위
        if kind == "S":
            out.append(ch)
            parse.append(f"약자:{code}->{ch}")
            score += 1.4
            i += 1
            continue

        # 초성 + 모음 + optional 종성
        if kind == "I" and i + 1 < len(tokens) and tokens[i + 1][0] == "V":
            cho = ch
            jung = tokens[i + 1][1]
            jong = ""
            used = 2

            if i + 2 < len(tokens) and tokens[i + 2][0] == "F":
                jong = tokens[i + 2][1]
                used = 3

            syll = compose_hangul(cho, jung, jong)
            if syll:
                out.append(syll)
                parse.append(f"{cho}+{jung}+{jong}->{syll}")
                score += 2.4 if jong else 1.8
                i += used
                continue

        # 모음 + optional 종성: 초성 ㅇ 생략
        if kind == "V":
            jung = ch
            jong = ""
            used = 1

            if i + 1 < len(tokens) and tokens[i + 1][0] == "F":
                jong = tokens[i + 1][1]
                used = 2

            syll = compose_hangul("ㅇ", jung, jong)
            if syll:
                out.append(syll)
                parse.append(f"ㅇ+{jung}+{jong}->{syll}")
                score += 2.3 if jong else 1.6
                i += used
                continue

        out.append(ch)
        parse.append(f"{kind}:{ch}")
        score -= 1.0
        i += 1

    text = "".join(out)
    hangul = sum(1 for ch in text if 0xAC00 <= ord(ch) <= 0xD7A3)
    bare = len(text) - hangul

    score += 0.45 * hangul
    score -= 0.65 * bare

    return text, parse, score


# ============================================================
# 6. 출력 / 디버그
# ============================================================
def draw_result(roi, points, grid, decoded_text):
    out = roi.copy()

    # 원시 점
    for i, p in enumerate(points, start=1):
        x = int(round(p["x"]))
        y = int(round(p["y"]))
        cv2.circle(out, (x, y), 6, (0, 255, 255), 2)
        cv2.putText(out, str(i), (x + 5, y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)

    if grid is not None:
        # row line: 기울기 보정 행 모델 표시
        row_model = grid.get("row_model")
        if row_model is not None:
            x0 = 0
            x1 = roi.shape[1]
            for ri in range(3):
                y0 = int(round(row_center_y_at_x(row_model, ri, x0)))
                y1 = int(round(row_center_y_at_x(row_model, ri, x1)))
                cv2.line(out, (x0, y0), (x1, y1), (255, 255, 0), 1)
        else:
            for ry in grid["row_centers"]:
                cv2.line(out, (0, int(round(ry))), (roi.shape[1], int(round(ry))), (255, 255, 0), 1)

        # column line
        for i, col in enumerate(grid["columns"], start=1):
            x = int(round(col["x"]))
            cv2.line(out, (x, 0), (x, roi.shape[0]), (255, 255, 0), 1)
            cv2.putText(out, f"X{i}", (x + 2, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (255, 255, 0), 1)

        # cell boxes
        for ci, cell in enumerate(grid["cells"], start=1):
            xs = [c["x"] for c in cell]
            x0 = int(round(min(xs) - 8))
            x1 = int(round(max(xs) + 8))
            cv2.rectangle(out, (x0, 2), (x1, roi.shape[0] - 2), (0, 255, 0), 1)
            cv2.putText(out, f"C{ci}", (x0, roi.shape[0] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)

    cv2.putText(out, f"decoded: {decoded_text}", (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 0), 2)
    return out



# ============================================================
# 7. 여러 줄 점자 지원
# ============================================================
def cluster_rows_from_points(points, roi_shape):
    """
    검출된 점들의 y좌표를 기준으로 row cluster를 만든다.
    점자 한 줄은 보통 3개의 row cluster를 가진다.
    """
    h, w = roi_shape[:2]

    if not points:
        return []

    row_merge = max(5.0, h * 0.025)
    sorted_points = sorted(points, key=lambda p: p["y"])

    clusters = []
    current = [sorted_points[0]]

    def mean_y(items):
        total = sum(p.get("weight", 1.0) for p in items)
        return sum(p["y"] * p.get("weight", 1.0) for p in items) / max(total, 1e-6)

    for p in sorted_points[1:]:
        cy = mean_y(current)

        if abs(p["y"] - cy) <= row_merge:
            current.append(p)
        else:
            clusters.append(current)
            current = [p]

    clusters.append(current)

    row_clusters = []
    for c in clusters:
        cy = mean_y(c)
        row_clusters.append({
            "center_y": float(cy),
            "points": c,
            "y_min": float(min(p["y"] for p in c)),
            "y_max": float(max(p["y"] for p in c)),
        })

    row_clusters.sort(key=lambda r: r["center_y"])
    return row_clusters


def make_boxes_from_line_point_groups(line_point_groups, roi_shape, line_pad_scale=1.2):
    """
    line별 point group을 실제 ROI box로 변환한다.
    x는 전체 폭을 쓰고 y만 줄별로 자른다.
    """
    h, w = roi_shape[:2]
    boxes = []

    for group_points in line_point_groups:
        if not group_points:
            continue

        y_min = min(p["y"] for p in group_points)
        y_max = max(p["y"] for p in group_points)

        # 이 줄 안에서 row 간격 추정
        ys = sorted([p["y"] for p in group_points])
        if len(ys) >= 3:
            local_gaps = [ys[i + 1] - ys[i] for i in range(len(ys) - 1)]
            small_gaps = [g for g in local_gaps if g > 1.0]
            row_pitch = float(np.median(small_gaps)) if small_gaps else h * 0.05
        else:
            row_pitch = h * 0.06

        margin = max(8.0, row_pitch * float(line_pad_scale))

        y0 = max(0, int(round(y_min - margin)))
        y1 = min(h, int(round(y_max + margin)))

        if y1 - y0 >= 5:
            boxes.append((0, y0, w, y1 - y0))

    boxes.sort(key=lambda b: b[1])
    return boxes


def split_points_to_line_boxes_by_gap(points, roi_shape, line_mode="auto", expected_lines=0, line_pad_scale=1.2):
    """
    점자 줄 분리 핵심 로직.

    사용자의 기준:
      점자 칸 내부 간격 < 점자 글자 사이 간격 < 줄 간격

    x축에서는 column gap을 보고 칸을 나눴듯이,
    y축에서는 point/row group gap을 보고 줄을 나눈다.

    과정:
    1. 점들을 y좌표 기준으로 정렬
    2. 가까운 y값은 같은 horizontal row group으로 묶음
       예: 한 점자 줄 안의 1행/2행/3행
    3. row group 중심 사이 gap을 계산
    4. gap들이 작은 그룹과 큰 그룹으로 갈라지는 지점을 찾음
    5. 큰 gap = 줄 사이 간격으로 보고 line 분리

    expected_lines가 있으면 마지막에 줄 수를 그 값에 맞춰 보정한다.
    """
    h, w = roi_shape[:2]

    if line_mode == "single":
        return [(0, 0, w, h)], []

    if not points:
        return [(0, 0, w, h)], []

    # ------------------------------------------------------------
    # 1. y축 row group 만들기
    # ------------------------------------------------------------
    sorted_points = sorted(points, key=lambda p: p["y"])

    # 같은 horizontal row 안의 점들은 y가 가깝다.
    # 너무 작게 잡으면 같은 row가 쪼개지고, 너무 크게 잡으면 1/2/3행이 합쳐진다.
    y_merge = max(4.0, h * 0.020)

    row_groups = []
    current = [sorted_points[0]]

    def weighted_mean_y(items):
        total = sum(p.get("weight", 1.0) for p in items)
        return sum(p["y"] * p.get("weight", 1.0) for p in items) / max(total, 1e-6)

    for p in sorted_points[1:]:
        cy = weighted_mean_y(current)
        if abs(p["y"] - cy) <= y_merge:
            current.append(p)
        else:
            row_groups.append(current)
            current = [p]

    row_groups.append(current)

    row_infos = []
    for group in row_groups:
        row_infos.append({
            "center_y": float(weighted_mean_y(group)),
            "points": group,
            "count": len(group),
            "y_min": float(min(p["y"] for p in group)),
            "y_max": float(max(p["y"] for p in group)),
        })

    row_infos.sort(key=lambda r: r["center_y"])

    if len(row_infos) <= 3 and line_mode != "multi":
        return [(0, 0, w, h)], row_infos

    centers = [r["center_y"] for r in row_infos]
    gaps = [centers[i + 1] - centers[i] for i in range(len(centers) - 1)]

    # ------------------------------------------------------------
    # 2. gap 기반 줄 사이 threshold 찾기
    # ------------------------------------------------------------
    if not gaps:
        return [(0, 0, w, h)], row_infos

    sorted_gaps = sorted(gaps)

    best_i = None
    best_ratio = 1.0

    # gap의 가장 큰 비율 점프를 찾는다.
    # 작은 gap들은 같은 점자 줄 내부 row 간격,
    # 큰 gap들은 줄 사이 간격으로 본다.
    for i in range(len(sorted_gaps) - 1):
        a = max(sorted_gaps[i], 1e-6)
        b = sorted_gaps[i + 1]
        ratio = b / a
        if ratio > best_ratio:
            best_ratio = ratio
            best_i = i

    if best_i is not None and best_ratio >= 1.25:
        line_gap_threshold = (sorted_gaps[best_i] + sorted_gaps[best_i + 1]) / 2.0
    else:
        # 명확한 gap jump가 없으면 "내부 row gap의 약 1.8배" 이상을 줄 간격으로 본다.
        # 사용자의 전제: 점자 글자 사이 간격보다 줄 간격이 더 크다.
        line_gap_threshold = float(np.median(gaps) * 1.80)

    # ------------------------------------------------------------
    # 3. threshold보다 큰 y gap에서 줄 분리
    # ------------------------------------------------------------
    line_row_groups = []
    current_rows = [row_infos[0]]

    for i, gap in enumerate(gaps):
        next_row = row_infos[i + 1]

        if gap > line_gap_threshold:
            line_row_groups.append(current_rows)
            current_rows = [next_row]
        else:
            current_rows.append(next_row)

    line_row_groups.append(current_rows)

    # row group -> point group
    line_point_groups = []
    for rows in line_row_groups:
        pts = []
        for r in rows:
            pts.extend(r["points"])
        line_point_groups.append(pts)

    # ------------------------------------------------------------
    # 4. expected_lines가 있으면 줄 수 보정
    # ------------------------------------------------------------
    # 자동 gap이 가끔 3줄을 2줄/4줄로 잡으면, 사용자가 준 expected_lines로
    # y순서 기준 균등 분할해서 안정화한다.
    if expected_lines and expected_lines > 0 and len(line_point_groups) != expected_lines:
        all_points_sorted = sorted(points, key=lambda p: p["y"])
        split_groups = [list(g) for g in np.array_split(all_points_sorted, int(expected_lines)) if len(g) > 0]
        line_point_groups = split_groups

    boxes = make_boxes_from_line_point_groups(line_point_groups, roi_shape, line_pad_scale=line_pad_scale)

    if line_mode == "auto" and len(boxes) <= 1:
        return [(0, 0, w, h)], row_infos

    return boxes if boxes else [(0, 0, w, h)], row_infos



def analyze_line_roi(line_roi, args):
    """
    한 줄 ROI에 대해 v6 기존 방식 그대로 분석.
    """
    gray, enhanced, binary, threshold = preprocess_gray_for_dots(
        line_roi,
        args.threshold,
        close_iter=args.close_iter,
        percentile=args.percentile,
        std_scale=args.std_scale,
        peak_sigma=args.peak_sigma,
        peak_percentile=args.peak_percentile,
        peak_kernel=args.peak_kernel,
        dot_radius=args.dot_radius,
    )

    points = detect_dots(
        binary,
        line_roi.shape,
        merge_scale=args.merge_scale,
        min_merge_dist=args.min_merge_dist,
    )

    grid = fit_braille_by_gaps(points, line_roi.shape)

    if grid is None:
        decoded_text = "(해석 실패)"
        cells = []
        decoded = {"text": decoded_text, "parse": [], "score": 0.0, "candidates": []}
    else:
        cells = grid["codes"]
        decoded = grid["decoded"]
        decoded_text = decoded["text"]

    result_img = draw_result(line_roi, points, grid, decoded_text)

    return {
        "gray": gray,
        "enhanced": enhanced,
        "binary": binary,
        "threshold": threshold,
        "points": points,
        "grid": grid,
        "cells": cells,
        "decoded": decoded,
        "decoded_text": decoded_text,
        "result_img": result_img,
    }



def analyze_line_from_base(line_roi, base_gray_crop, base_enhanced_crop, base_binary_crop, args):
    """
    여러 줄 분석에서 점이 사라지는 문제 방지용.

    기존:
      line_roi를 다시 preprocess -> 줄마다 threshold가 새로 계산됨
      => 1번 라인처럼 상대적으로 약한 점이 line별 threshold에서 사라질 수 있음

    변경:
      전체 ROI에서 이미 만든 base_binary를 line box 기준으로 crop해서 사용
      => line split에 사용된 점 후보와 line 분석에 사용되는 점 후보가 일관됨
    """
    gray = base_gray_crop.copy()
    enhanced = base_enhanced_crop.copy()
    binary = base_binary_crop.copy()

    points = detect_dots(
        binary,
        line_roi.shape,
        merge_scale=args.merge_scale,
        min_merge_dist=args.min_merge_dist,
    )

    grid = fit_braille_by_gaps(points, line_roi.shape)

    if grid is None:
        decoded_text = "(해석 실패)"
        cells = []
        decoded = {"text": decoded_text, "parse": [], "score": 0.0, "candidates": []}
    else:
        cells = grid["codes"]
        decoded = grid["decoded"]
        decoded_text = decoded["text"]

    result_img = draw_result(line_roi, points, grid, decoded_text)

    return {
        "gray": gray,
        "enhanced": enhanced,
        "binary": binary,
        "threshold": -1,
        "points": points,
        "grid": grid,
        "cells": cells,
        "decoded": decoded,
        "decoded_text": decoded_text,
        "result_img": result_img,
    }


def draw_line_boxes(roi, line_boxes):
    out = roi.copy()

    for i, (x, y, w, h) in enumerate(line_boxes, start=1):
        cv2.rectangle(out, (x, y), (x + w, y + h), (0, 255, 255), 2)
        cv2.putText(
            out,
            f"Line {i}",
            (x + 6, max(20, y + 20)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 255, 255),
            2,
        )

    return out


def stack_result_images(line_results):
    imgs = [r["result_img"] for r in line_results if r.get("result_img") is not None]

    if not imgs:
        return None

    max_w = max(img.shape[1] for img in imgs)
    stacked = []

    for idx, img in enumerate(imgs, start=1):
        h, w = img.shape[:2]
        canvas = np.zeros((h + 30, max_w, 3), dtype=np.uint8)
        canvas[:] = 255
        canvas[30:30 + h, 0:w] = img
        cv2.putText(canvas, f"Line {idx}", (8, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)
        stacked.append(canvas)

    return cv2.vconcat(stacked)


def normalize_compare_text(text):
    return text.replace(" ", "").replace("\t", "").replace("\r", "").replace("\n", "")


def is_hangul_syllable(ch):
    return 0xAC00 <= ord(ch) <= 0xD7A3


def merge_standalone_final_jamo(text):
    """
    후보군 기반 비교용 보정.

    예:
      '주무ㄴ' -> '주문'

    규칙:
      앞 글자가 완성형 한글 음절이고,
      앞 음절에 받침이 없고,
      현재 글자가 종성으로 쓸 수 있는 자모이면
      현재 자모를 앞 음절의 받침으로 합친다.
    """
    result = []

    for ch in text:
        if (
            result
            and ch in JONG_INDEX
            and ch != ""
            and is_hangul_syllable(result[-1])
        ):
            prev = result[-1]
            base = ord(prev) - 0xAC00

            cho_idx = base // (21 * 28)
            jung_idx = (base % (21 * 28)) // 28
            jong_idx = base % 28

            # 앞 음절에 받침이 없을 때만 결합
            if jong_idx == 0:
                result[-1] = chr(
                    0xAC00
                    + (cho_idx * 21 + jung_idx) * 28
                    + JONG_INDEX[ch]
                )
                continue

        result.append(ch)

    return "".join(result)


def normalize_for_match(text):
    """
    라벨/후보 비교용 정규화.
    공백/줄바꿈 제거 후, 단독 종성 자모를 앞 음절에 결합한다.
    """
    return merge_standalone_final_jamo(normalize_compare_text(str(text)))


def unique_keep_order(items):
    unique = []
    seen = set()

    for item in items:
        if item is None:
            continue

        item = str(item)

        if item not in seen:
            seen.add(item)
            unique.append(item)

    return unique


def get_line_candidate_texts(line_data, max_candidates=8):
    """
    한 줄의 1등 해석과 후보군을 모은다.
    """
    texts = []

    decoded_text = line_data.get("decoded_text", "")
    if decoded_text:
        texts.append(decoded_text)

    decoded = line_data.get("decoded", {})
    for cand in decoded.get("candidates", []):
        t = cand.get("text", "")
        if t:
            texts.append(t)

    return unique_keep_order(texts)[:max_candidates]


def collect_candidate_texts(decoded_text, decoded_lines, line_results, max_per_line=8, max_total=300):
    """
    전체 해석 후보군을 만든다.

    단일 줄:
      해당 줄의 후보군 그대로 사용

    여러 줄:
      각 줄 후보군의 조합을 만든다.
      예: 1줄 후보 x 2줄 후보 x 3줄 후보
      비교 시 normalize_for_match가 줄바꿈을 제거하므로,
      joined 후보가 라벨과 의미상 일치하는지 확인 가능하다.
    """
    candidates = []

    if decoded_text:
        candidates.append(decoded_text)

    if decoded_lines:
        candidates.append("\n".join(decoded_lines))
        candidates.append("".join(decoded_lines))

    per_line = [get_line_candidate_texts(line, max_candidates=max_per_line) for line in line_results]

    if not per_line:
        return unique_keep_order(candidates)[:max_total]

    # 줄별 후보 조합 생성
    combos = [""]

    for line_cands in per_line:
        if not line_cands:
            line_cands = [""]

        new_combos = []
        for prefix in combos:
            for cand in line_cands:
                if prefix:
                    new_combos.append(prefix + "\n" + cand)
                    new_combos.append(prefix + cand)
                else:
                    new_combos.append(cand)

                if len(new_combos) >= max_total:
                    break

            if len(new_combos) >= max_total:
                break

        combos = new_combos

        if len(combos) >= max_total:
            break

    candidates.extend(combos)
    return unique_keep_order(candidates)[:max_total]


def flatten_actual_cells(line_results):
    """
    줄별 인식 점형을 한 리스트로 합친다.
    예:
      Line1 ['15','12456','245','26']
      Line2 ['46','134','15','1245']
      -> ['15','12456','245','26','46','134','15','1245']
    """
    cells = []
    for line in line_results:
        cells.extend(line.get("cells", []))
    return cells


def generate_code_option_sequences_from_line_results(line_results, max_total=1000):
    """
    code_options에 여러 후보가 있는 경우를 고려해서 가능한 점형 시퀀스를 만든다.

    예:
      code_options = [['13', '46'], ['1345'], ['13', '46'], ['1', '4']]
    이면 '46'을 선택한 경우도, '13'을 선택한 경우도 후보 점형으로 유지한다.

    이것은 의미 해석 후보가 아니라, 순수 점형 후보 조합이다.
    """
    all_options = []

    for line in line_results:
        grid = line.get("grid")
        if grid is not None and grid.get("code_options"):
            all_options.extend(grid["code_options"])
        else:
            # grid가 없으면 확정 cells라도 사용
            for cell in line.get("cells", []):
                all_options.append([cell])

    sequences = [[]]

    for options in all_options:
        if not options:
            options = [""]

        new_sequences = []
        for seq in sequences:
            for code in options:
                new_sequences.append(seq + [code])
                if len(new_sequences) >= max_total:
                    break

            if len(new_sequences) >= max_total:
                break

        sequences = new_sequences

        if len(sequences) >= max_total:
            break

    return sequences


def judge_match_by_cells(label, line_results, expected_cells_override=None):
    """
    의미 해석을 쓰지 않고 점형만 비교한다.

    일치 기준:
      1. 라벨 기준 점형과 실제 인식 점형이 정확히 같으면 일치
      2. 실제 점형에 ambiguous option이 있으면 가능한 점형 조합 중
         라벨 기준 점형과 같은 조합이 존재할 때 일치

    예:
      라벨 '명함 주문 제작'
      라벨 기준 점형:
        ['15','12456','245','126','26','46','134','15','1245','46','1345','46','126','1']

      실제 인식 점형이 위와 같으면 일치.
      해석 결과 문자열이 '명하ㅁ 자우문 자에자ㄱ'처럼 이상해도
      점형이 맞으면 일치.
    """
    if expected_cells_override:
        expected_cells = list(expected_cells_override)
    else:
        expected_cells = label_to_cells(label)

    actual_cells = flatten_actual_cells(line_results)

    if actual_cells == expected_cells:
        return {
            "is_match": True,
            "reason": "점형 정확 일치",
            "expected_cells": expected_cells,
            "actual_cells": actual_cells,
            "matched_cells": actual_cells,
            "cell_candidate_count": 1,
        }

    candidate_sequences = generate_code_option_sequences_from_line_results(line_results)

    for seq in candidate_sequences:
        if seq == expected_cells:
            return {
                "is_match": True,
                "reason": "점형 후보군 기준 일치",
                "expected_cells": expected_cells,
                "actual_cells": actual_cells,
                "matched_cells": seq,
                "cell_candidate_count": len(candidate_sequences),
            }

    return {
        "is_match": False,
        "reason": "라벨 기준 점형과 인식 점형 불일치",
        "expected_cells": expected_cells,
        "actual_cells": actual_cells,
        "matched_cells": None,
        "cell_candidate_count": len(candidate_sequences),
    }




def parse_expected_cells_arg(value):
    """
    --expected-cells 옵션 파싱.
    허용 예:
      --expected-cells "15,12456,245,26"
      --expected-cells "15 12456 245 26"
    """
    if value is None:
        return None

    raw = str(value).strip()
    if not raw:
        return None

    raw = raw.replace("[", "").replace("]", "").replace("'", "").replace('"', "")
    raw = raw.replace(",", " ")
    cells = [v.strip() for v in raw.split() if v.strip()]
    return cells if cells else None


def validate(label, args):
    global MEDIAN_KSIZE, ROW_SLOPE_MAX
    MEDIAN_KSIZE = int(args.median_ksize)
    ROW_SLOPE_MAX = float(args.row_slope_max)
    if MEDIAN_KSIZE not in (1, 3, 5):
        print("[WARN] --median-ksize는 1, 3, 5 중 하나를 권장합니다. 기본 3으로 보정합니다.", flush=True)
        MEDIAN_KSIZE = 3

    # 캡쳐 가이드 줄 수 결정
    # 우선순위:
    #   1. --capture-guide-lines N을 직접 지정하면 N 사용
    #   2. 아니면 --expected-lines N 사용
    #   3. 둘 다 없으면 기본 3줄
    if args.capture_guide_lines > 0:
        guide_lines = args.capture_guide_lines
    elif args.expected_lines > 0:
        guide_lines = args.expected_lines
    else:
        guide_lines = 3

    frame = capture_frame(
        args.camera,
        args.mirror,
        guide=not args.no_capture_guide,
        guide_lines=guide_lines,
        guide_width_ratio=args.guide_width_ratio,
        guide_height_ratio=args.guide_height_ratio,
    )
    roi, roi_box = select_roi_by_mouse(frame)

    print(f"[INFO] ROI: {roi_box}", flush=True)

    # 전체 ROI에서 한 번 점 후보를 얻고, 그 y분포로 여러 줄을 나눈다.
    base_gray, base_enhanced, base_binary, base_threshold = preprocess_gray_for_dots(
        roi,
        args.threshold,
        close_iter=args.close_iter,
        percentile=args.percentile,
        std_scale=args.std_scale,
        peak_sigma=args.peak_sigma,
        peak_percentile=args.peak_percentile,
        peak_kernel=args.peak_kernel,
        dot_radius=args.dot_radius,
    )
    base_points = detect_dots(
        base_binary,
        roi.shape,
        merge_scale=args.merge_scale,
        min_merge_dist=args.min_merge_dist,
    )

    line_boxes, row_clusters = split_points_to_line_boxes_by_gap(
        base_points,
        roi.shape,
        line_mode=args.line_mode,
        expected_lines=args.expected_lines,
        line_pad_scale=args.line_pad_scale,
    )

    print(f"[INFO] 감지된 점자 줄 수: {len(line_boxes)}", flush=True)

    print("[INFO] 한글 점자 해석", flush=True)

    line_results = []
    decoded_lines = []

    for line_index, (x, y, w, h) in enumerate(line_boxes, start=1):
        line_roi = roi[y:y + h, x:x + w].copy()

        if args.use_base_binary_for_lines:
            base_gray_crop = base_gray[y:y + h, x:x + w].copy()
            base_enhanced_crop = base_enhanced[y:y + h, x:x + w].copy()
            base_binary_crop = base_binary[y:y + h, x:x + w].copy()
            line_data = analyze_line_from_base(
                line_roi,
                base_gray_crop,
                base_enhanced_crop,
                base_binary_crop,
                args,
            )
        else:
            line_data = analyze_line_roi(line_roi, args)

        line_data["line_index"] = line_index
        line_data["line_box"] = (x, y, w, h)
        line_results.append(line_data)
        decoded_lines.append(line_data["decoded_text"])

    if len(decoded_lines) == 1:
        decoded_text = decoded_lines[0]
    else:
        decoded_text = "\n".join(decoded_lines)

    # ------------------------------------------------------------
    # 점형 기반 판정
    # ------------------------------------------------------------
    # 의미 해석 결과는 참고만 한다.
    # 최종 판정은 "라벨을 약자/약어 기준으로 변환한 점형"과
    # "영상에서 인식한 점형"이 일치하는지로만 판단한다.
    expected_cells_override = parse_expected_cells_arg(args.expected_cells)

    judge = judge_match_by_cells(
        label=label,
        line_results=line_results,
        expected_cells_override=expected_cells_override,
    )

    is_match = judge["is_match"]
    result = "일치" if is_match else "불일치"

    print(f"의도 : {label}")

    if len(decoded_lines) == 1:
        print(f"해석 : {decoded_text}")
    else:
        print("해석 :")
        for i, line_text in enumerate(decoded_lines, start=1):
            print(f"  {i}줄: {line_text}")
        print(f"  합침: {normalize_compare_text(decoded_text)}")

    print(f"결과 : {result}")
    print(f"판정 : {judge['reason']}")
    print(f"라벨 기준 점형 : {judge['expected_cells']}")
    print(f"인식 점형       : {judge['actual_cells']}")

    if judge.get("matched_cells") is not None and judge["matched_cells"] != judge["actual_cells"]:
        print(f"일치 점형 후보 : {judge['matched_cells']}")

    if args.verbose:
        print()
        print("----- 디버그 정보 -----")
        print(f"전체 ROI: {roi_box}")
        print(f"line_mode: {args.line_mode}")
        print(f"expected_lines: {args.expected_lines}")
        print(f"use_base_binary_for_lines: {args.use_base_binary_for_lines}")
        print(f"line_pad_scale: {args.line_pad_scale}")
        print(f"row_slope_max: {args.row_slope_max}")
        print(f"expected_cells_override: {parse_expected_cells_arg(args.expected_cells)}")
        print(f"capture_guide: {not args.no_capture_guide}")
        print(f"capture_guide_lines: {guide_lines}")
        print(f"guide_width_ratio: {args.guide_width_ratio}")
        print(f"guide_height_ratio: {args.guide_height_ratio}")
        print(f"base threshold: {base_threshold}")
        print(f"base 검출 점 개수: {len(base_points)}")
        print(f"row cluster 개수: {len(row_clusters)}")
        print(f"line boxes: {line_boxes}")
        print(f"라벨 기준 점형 참고: {label_to_cells(label)}")
        print(f"threshold_boost: {args.threshold}")
        print(f"percentile: {args.percentile}")
        print(f"std_scale: {args.std_scale}")
        print(f"peak_sigma: {args.peak_sigma}")
        print(f"peak_percentile: {args.peak_percentile}")
        print(f"peak_kernel: {args.peak_kernel}")
        print(f"dot_radius: {args.dot_radius}")
        print(f"merge_scale: {args.merge_scale}")
        print(f"min_merge_dist: {args.min_merge_dist}")
        print(f"close_iter: {args.close_iter}")
        print(f"median_ksize: {MEDIAN_KSIZE}")
        print(f"cell_candidate_count: {judge['cell_candidate_count']}")
        print(f"match_reason: {judge['reason']}")
        print(f"expected_cells: {judge['expected_cells']}")
        print(f"actual_cells: {judge['actual_cells']}")
        print(f"matched_cells: {judge['matched_cells']}")

        for line in line_results:
            grid = line["grid"]
            decoded = line["decoded"]

            print()
            print(f"[Line {line['line_index']}]")
            print(f"line_box: {line['line_box']}")
            print(f"threshold: {line['threshold']}")
            print(f"검출 점 개수: {len(line['points'])}")
            print(f"검출 점 좌표: {[(int(round(p['x'])), int(round(p['y']))) for p in line['points']]}")
            print(f"인식 점형: {line['cells']}")

            if grid is not None:
                print(f"x column 개수: {len(grid['columns'])}")
                print(f"x column 좌표: {[round(c['x'], 1) for c in grid['columns']]}")
                print(f"x column gaps: {[round(g, 1) for g in grid['gaps']]}")
                print(f"same-cell gap threshold: {grid['gap_threshold']:.1f}")
                print(f"row slope: {grid.get('row_slope', 0.0):.3f}")
                if grid.get("row_model") is not None:
                    print(f"row model reason: {grid['row_model'].get('reason', '')}")
                    print(f"row model sep: {grid['row_model'].get('sep', 0.0):.2f}")
                print(f"row centers corrected: {[round(v, 1) for v in grid.get('row_centers', [])]}")
                print(f"cell 개수: {len(grid['cells'])}")
                print(f"cell code options: {grid['code_options']}")

            print(f"해석 점수: {decoded['score']:.2f}")
            print(f"음절 분해: {' / '.join(decoded['parse'])}")

            if decoded["candidates"]:
                print("해석 후보:")
                for i, cand in enumerate(decoded["candidates"], start=1):
                    print(f"  {i}. {cand['text']} | score={cand['score']:.2f} | parse={' / '.join(cand['parse'])}")

        print("----------------------")

    line_box_img = draw_line_boxes(roi, line_boxes)
    stacked_result_img = stack_result_images(line_results)

    if args.show:
        cv2.imshow("1 ROI with Line Boxes", line_box_img)
        cv2.imshow("2 Base Enhanced", base_enhanced)
        cv2.imshow("3 Base Binary", base_binary)

        if stacked_result_img is not None:
            cv2.imshow("4 Multi-line Result", stacked_result_img)

        # 줄별 창도 같이 보여줌
        for line in line_results:
            cv2.imshow(f"Line {line['line_index']} Binary", line["binary"])
            cv2.imshow(f"Line {line['line_index']} Result", line["result_img"])

        print("[INFO] 아무 키나 누르면 창을 닫습니다.", flush=True)
        cv2.waitKey(0)
        cv2.destroyAllWindows()

    if args.save:
        prefix = args.output_prefix or f"dot_validation_final_generalized_cells_v3_slopefix_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        cv2.imwrite(f"{prefix}_frame.png", frame)
        cv2.imwrite(f"{prefix}_roi.png", roi)
        cv2.imwrite(f"{prefix}_line_boxes.png", line_box_img)
        cv2.imwrite(f"{prefix}_base_enhanced.png", base_enhanced)
        cv2.imwrite(f"{prefix}_base_binary.png", base_binary)

        if stacked_result_img is not None:
            cv2.imwrite(f"{prefix}_result.png", stacked_result_img)

        for line in line_results:
            idx = line["line_index"]
            x, y, w, h = line["line_box"]
            line_roi = roi[y:y + h, x:x + w].copy()
            cv2.imwrite(f"{prefix}_line{idx}_roi.png", line_roi)
            cv2.imwrite(f"{prefix}_line{idx}_binary.png", line["binary"])
            cv2.imwrite(f"{prefix}_line{idx}_result.png", line["result_img"])

        payload = {
            "label": label,
            "decoded": decoded_text,
            "decoded_lines": decoded_lines,
            "result": result,
            "match": is_match,
            "match_reason": judge["reason"],
            "expected_cells": judge["expected_cells"],
            "actual_cells": judge["actual_cells"],
            "matched_cells": judge["matched_cells"],
            "cell_candidate_count": judge["cell_candidate_count"],
            "roi": roi_box,
            "line_mode": args.line_mode,
            "line_boxes": line_boxes,
            "base_threshold": base_threshold,
            "base_dot_count": len(base_points),
            "label_reference_cells": label_to_cells(label),
            "lines": [
                {
                    "line_index": line["line_index"],
                    "line_box": line["line_box"],
                    "threshold": line["threshold"],
                    "dot_count": len(line["points"]),
                    "dots": line["points"],
                    "actual_cells": line["cells"],
                    "decoded": line["decoded_text"],
                    "decoded_detail": line["decoded"],
                    "grid": None if line["grid"] is None else {
                        "gap_threshold": line["grid"]["gap_threshold"],
                        "gaps": line["grid"]["gaps"],
                        "codes": line["grid"]["codes"],
                        "code_options": line["grid"]["code_options"],
                        "decoded": line["grid"]["decoded"],
                    },
                }
                for line in line_results
            ],
        }

        with open(f"{prefix}_result.json", "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        print(f"[INFO] 저장 완료: {prefix}_*.png / {prefix}_result.json", flush=True)

    return 0 if is_match else 1


def main():
    parser = argparse.ArgumentParser(description="OpenCV 한글 점자 검증 코드 - gap clustering + generalized expected cells + capture guide + cell match + slope limit")
    parser.add_argument("label", type=str, help='의도한 한글 라벨. 예: "안녕"')
    parser.add_argument("--expected-cells", type=str, default=None, help='라벨 변환 대신 직접 expected 점형 입력. 예: "15,12456,245,26"')
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--mirror", action="store_true")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help="Enhanced->Binary threshold 계수. 높이면 확실한 점만 흰색으로 남음")
    parser.add_argument("--percentile", type=float, default=DEFAULT_PERCENTILE, help="Enhanced 상위 percentile 기준. 높이면 더 확실한 점만 남음. 예: 98.0~99.3")
    parser.add_argument("--std-scale", type=float, default=DEFAULT_STD_SCALE, help="mean + std_scale*std 기준. 높이면 더 확실한 점만 남음")
    parser.add_argument("--peak-sigma", type=float, default=DEFAULT_PEAK_SIGMA, help="넓게 밝아진 blob 제거용 blur sigma. 높이면 큰 덩어리 제거가 강해짐")
    parser.add_argument("--peak-percentile", type=float, default=DEFAULT_PEAK_PERCENTILE, help="local peak threshold percentile. 높이면 확실한 peak만 남음")
    parser.add_argument("--peak-kernel", type=int, default=DEFAULT_PEAK_KERNEL, help="local maxima 탐색 kernel. 3,5,7 권장")
    parser.add_argument("--dot-radius", type=int, default=DEFAULT_DOT_RADIUS, help="검출 peak를 binary에 그릴 반지름. 1~2 권장")
    parser.add_argument("--merge-scale", type=float, default=DEFAULT_MERGE_SCALE, help="가까운 점 병합 거리 비율. 낮추면 붙은 점을 덜 뭉침. 예: 0.020~0.035")
    parser.add_argument("--min-merge-dist", type=float, default=DEFAULT_MIN_MERGE_DIST, help="최소 병합 거리(px). 낮추면 붙은 점을 덜 뭉침. 예: 1.5~3.0")
    parser.add_argument("--close-iter", type=int, default=DEFAULT_CLOSE_ITER, help="morphology close 반복 횟수. 0이면 점끼리 덜 붙음, 1이면 찢어진 점을 병합")
    parser.add_argument("--median-ksize", type=int, default=3, help="medianBlur 커널. 1이면 끔, 3이면 기본 노이즈 제거")
    parser.add_argument("--line-mode", choices=["single", "auto", "multi"], default="auto", help="single=한 줄만, auto=자동 줄 분리, multi=무조건 줄 분리")
    parser.add_argument("--expected-lines", type=int, default=0, help="점자 줄 수를 알고 있으면 지정. 예: 3줄이면 --expected-lines 3")
    parser.add_argument("--use-base-binary-for-lines", action=argparse.BooleanOptionalAction, default=DEFAULT_USE_BASE_BINARY_FOR_LINES, help="여러 줄 분석 시 전체 ROI binary를 잘라서 사용. 약한 점 소실 방지")
    parser.add_argument("--line-pad-scale", type=float, default=1.2, help="줄 box y padding 배율. 줄 가장자리 점이 잘리면 1.5~2.0으로 증가")
    parser.add_argument("--row-slope-max", type=float, default=DEFAULT_ROW_SLOPE_MAX, help="하늘색 row line 기울기 최대값. 0이면 수평 고정, 기본 0.06")

    # 웹캠 캡쳐 가이드라인 옵션
    parser.add_argument("--no-capture-guide", action="store_true", help="웹캠 캡쳐 화면의 수평 가이드라인을 끔")
    parser.add_argument("--capture-guide-lines", type=int, default=0, help="캡쳐 화면에 표시할 점자 줄 가이드 수. 0이면 --expected-lines 값을 자동 사용")
    parser.add_argument("--guide-width-ratio", type=float, default=0.78, help="가이드 박스 너비 비율")
    parser.add_argument("--guide-height-ratio", type=float, default=0.72, help="가이드 박스 높이 비율")

    parser.add_argument("--show", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--output-prefix", type=str, default=None)

    args = parser.parse_args()

    try:
        return validate(args.label, args)
    except Exception as e:
        print(f"[ERROR] {e}", flush=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
