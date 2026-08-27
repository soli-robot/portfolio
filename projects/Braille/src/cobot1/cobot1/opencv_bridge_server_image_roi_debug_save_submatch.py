#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Browser camera -> OpenCV braille validation bridge server
- POST /validate/image
- Saves debug images every request:
  debug_received_images/received_original.png
  debug_received_images/received_roi.png
  debug_received_images/received_flipped.png
  and timestamped copies
- Uses the uploaded validation code module directly, without opening webcam windows.
"""

import base64
import importlib.util
import json
import os
import re
import traceback
from argparse import Namespace
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from flask import Flask, jsonify, request
from flask_cors import CORS

BASE_DIR = Path(__file__).resolve().parent
DEBUG_DIR = BASE_DIR / "debug_received_images"
DEBUG_DIR.mkdir(exist_ok=True)

VALIDATION_FILE_CANDIDATES = [
    "dot_validation_final_generalized_cells_v3_slopefix_v2_guidefix.py",
    "dot_validation_final_generalized_cells_v3_slopefix.py",
    "dot_validation_final_generalized_cells_v3_slopefix (1).py",
    "붙여넣은 텍스트 (1).txt",
]

app = Flask(__name__)
CORS(app)


def find_validation_file() -> Path:
    for name in VALIDATION_FILE_CANDIDATES:
        p = BASE_DIR / name
        if p.exists():
            return p
    raise RuntimeError(
        "검증 코드 파일을 못 찾았습니다. 브릿지 서버와 같은 폴더에 "
        "dot_validation_final_generalized_cells_v3_slopefix.py 또는 "
        "dot_validation_final_generalized_cells_v3_slopefix_v2_guidefix.py 를 두세요."
    )


def load_validation_module():
    path = find_validation_file()
    spec = importlib.util.spec_from_file_location("freshdot_validation_module", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, path


def decode_data_url_image(value: str) -> np.ndarray:
    if not value:
        raise ValueError("image 데이터가 비어 있습니다.")

    text = str(value)
    if "," in text and text.strip().startswith("data:"):
        text = text.split(",", 1)[1]

    raw = base64.b64decode(text)
    arr = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("이미지 디코딩에 실패했습니다.")
    return img


def clamp_int(value, low, high):
    return max(low, min(high, int(round(float(value)))))


def crop_roi_if_present(img: np.ndarray, payload: dict) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """
    If payload has roi, crop it from original image.
    If not, assume img is already ROI.
    Accepted roi keys:
      {x,y,width,height}, {x,y,w,h}, {left,top,width,height}
    """
    h, w = img.shape[:2]
    roi = payload.get("roi") or payload.get("roiBox") or payload.get("crop")
    if not isinstance(roi, dict):
        return img.copy(), (0, 0, w, h)

    x = roi.get("x", roi.get("left", 0))
    y = roi.get("y", roi.get("top", 0))
    rw = roi.get("width", roi.get("w", w))
    rh = roi.get("height", roi.get("h", h))

    x0 = clamp_int(x, 0, w - 1)
    y0 = clamp_int(y, 0, h - 1)
    x1 = clamp_int(float(x) + float(rw), x0 + 1, w)
    y1 = clamp_int(float(y) + float(rh), y0 + 1, h)

    return img[y0:y1, x0:x1].copy(), (x0, y0, x1 - x0, y1 - y0)


def save_debug_images(original: np.ndarray, roi: np.ndarray) -> dict:
    flipped = cv2.flip(roi, 1)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")

    paths = {
        "original_latest": DEBUG_DIR / "received_original.png",
        "roi_latest": DEBUG_DIR / "received_roi.png",
        "flipped_latest": DEBUG_DIR / "received_flipped.png",
        "original_timestamped": DEBUG_DIR / f"{ts}_original.png",
        "roi_timestamped": DEBUG_DIR / f"{ts}_roi.png",
        "flipped_timestamped": DEBUG_DIR / f"{ts}_flipped.png",
    }

    cv2.imwrite(str(paths["original_latest"]), original)
    cv2.imwrite(str(paths["roi_latest"]), roi)
    cv2.imwrite(str(paths["flipped_latest"]), flipped)
    cv2.imwrite(str(paths["original_timestamped"]), original)
    cv2.imwrite(str(paths["roi_timestamped"]), roi)
    cv2.imwrite(str(paths["flipped_timestamped"]), flipped)

    return {k: str(v) for k, v in paths.items()}


def parse_expected_cells(value):
    if value is None:
        return None
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    raw = str(value).strip()
    if not raw:
        return None
    raw = raw.replace("[", "").replace("]", "").replace("'", "").replace('"', "")
    raw = raw.replace(",", " ")
    cells = [v.strip() for v in raw.split() if v.strip()]
    return cells or None


def make_default_args(payload: dict) -> Namespace:
    module, _ = load_validation_module()
    return Namespace(
        threshold=float(payload.get("threshold", module.DEFAULT_THRESHOLD)),
        percentile=float(payload.get("percentile", module.DEFAULT_PERCENTILE)),
        std_scale=float(payload.get("std_scale", payload.get("stdScale", module.DEFAULT_STD_SCALE))),
        peak_sigma=float(payload.get("peak_sigma", payload.get("peakSigma", module.DEFAULT_PEAK_SIGMA))),
        peak_percentile=float(payload.get("peak_percentile", payload.get("peakPercentile", module.DEFAULT_PEAK_PERCENTILE))),
        peak_kernel=int(payload.get("peak_kernel", payload.get("peakKernel", module.DEFAULT_PEAK_KERNEL))),
        dot_radius=int(payload.get("dot_radius", payload.get("dotRadius", module.DEFAULT_DOT_RADIUS))),
        merge_scale=float(payload.get("merge_scale", payload.get("mergeScale", module.DEFAULT_MERGE_SCALE))),
        min_merge_dist=float(payload.get("min_merge_dist", payload.get("minMergeDist", module.DEFAULT_MIN_MERGE_DIST))),
        close_iter=int(payload.get("close_iter", payload.get("closeIter", module.DEFAULT_CLOSE_ITER))),
        median_ksize=int(payload.get("median_ksize", payload.get("medianKsize", 3))),
        line_mode=str(payload.get("line_mode", payload.get("lineMode", "auto"))),
        expected_lines=int(payload.get("expected_lines", payload.get("expectedLines", 0))),
        use_base_binary_for_lines=bool(payload.get("use_base_binary_for_lines", payload.get("useBaseBinaryForLines", module.DEFAULT_USE_BASE_BINARY_FOR_LINES))),
        line_pad_scale=float(payload.get("line_pad_scale", payload.get("linePadScale", 1.2))),
        row_slope_max=float(payload.get("row_slope_max", payload.get("rowSlopeMax", module.DEFAULT_ROW_SLOPE_MAX))),
        expected_cells=payload.get("expected_cells", payload.get("expectedCells", None)),
    )



def find_contiguous_subsequence(haystack, needle):
    if not needle or not haystack or len(needle) > len(haystack):
        return -1
    for i in range(0, len(haystack) - len(needle) + 1):
        if haystack[i:i + len(needle)] == needle:
            return i
    return -1


def apply_subsequence_match_if_needed(result: dict):
    """
    웹 카메라 ROI에는 손가락 그림자/카드 로고/빛 반사 때문에
    앞뒤에 가짜 점이 추가로 잡힐 수 있다.
    기존 검증은 actual_cells == expected_cells만 허용해서,
    실제 문구 점형이 가운데에 정확히 들어 있어도 불일치가 났다.
    그래서 웹 검증에서는 expected_cells가 actual_cells 안에 연속으로 들어 있으면 일치 처리한다.
    """
    if result.get("match"):
        return result

    judge = result.get("judge") or {}
    expected = judge.get("expected_cells") or []
    actual = judge.get("actual_cells") or []

    start = find_contiguous_subsequence(actual, expected)
    if start < 0:
        return result

    judge = dict(judge)
    judge["is_match"] = True
    judge["reason"] = f"라벨 기준 점형이 인식 점형 안에 포함됨; 앞뒤 추가 점형 제외 후 일치, 위치 {start + 1}~{start + len(expected)}"
    judge["matched_cells"] = expected
    judge["ignored_prefix_cells"] = actual[:start]
    judge["ignored_suffix_cells"] = actual[start + len(expected):]

    summary = dict(result.get("summary") or {})
    summary["result"] = "일치"
    summary["judgement"] = judge["reason"]
    summary["matched_cells"] = expected
    summary["ignored_prefix_cells"] = judge["ignored_prefix_cells"]
    summary["ignored_suffix_cells"] = judge["ignored_suffix_cells"]

    result = dict(result)
    result["returncode"] = 0
    result["match"] = True
    result["result"] = "일치"
    result["judge"] = judge
    result["summary"] = summary

    # output도 팝업 요약 파서가 바로 읽을 수 있게 다시 구성
    lines = []
    for line in str(result.get("output", "")).splitlines():
        if line.startswith("결과 :"):
            lines.append("결과 : 일치")
        elif line.startswith("판정 :"):
            lines.append(f"판정 : {judge['reason']}")
        else:
            lines.append(line)
    if lines:
        result["output"] = "\n".join(lines)

    return result

def analyze_roi_image(label: str, roi_img: np.ndarray, payload: dict):
    module, validation_path = load_validation_module()
    args = make_default_args(payload)

    module.MEDIAN_KSIZE = int(args.median_ksize)
    module.ROW_SLOPE_MAX = float(args.row_slope_max)

    base_gray, base_enhanced, base_binary, base_threshold = module.preprocess_gray_for_dots(
        roi_img,
        args.threshold,
        close_iter=args.close_iter,
        percentile=args.percentile,
        std_scale=args.std_scale,
        peak_sigma=args.peak_sigma,
        peak_percentile=args.peak_percentile,
        peak_kernel=args.peak_kernel,
        dot_radius=args.dot_radius,
    )

    base_points = module.detect_dots(
        base_binary,
        roi_img.shape,
        merge_scale=args.merge_scale,
        min_merge_dist=args.min_merge_dist,
    )

    line_boxes, row_clusters = module.split_points_to_line_boxes_by_gap(
        base_points,
        roi_img.shape,
        line_mode=args.line_mode,
        expected_lines=args.expected_lines,
        line_pad_scale=args.line_pad_scale,
    )

    line_results = []
    decoded_lines = []

    for line_index, (x, y, w, h) in enumerate(line_boxes, start=1):
        line_roi = roi_img[y:y + h, x:x + w].copy()
        base_gray_crop = base_gray[y:y + h, x:x + w].copy()
        base_enhanced_crop = base_enhanced[y:y + h, x:x + w].copy()
        base_binary_crop = base_binary[y:y + h, x:x + w].copy()

        if args.use_base_binary_for_lines:
            line_data = module.analyze_line_from_base(
                line_roi,
                base_gray_crop,
                base_enhanced_crop,
                base_binary_crop,
                args,
            )
        else:
            line_data = module.analyze_line_roi(line_roi, args)

        line_data["line_index"] = line_index
        line_data["line_box"] = (x, y, w, h)
        line_results.append(line_data)
        decoded_lines.append(line_data["decoded_text"])

    decoded_text = decoded_lines[0] if len(decoded_lines) == 1 else "\n".join(decoded_lines)

    expected_cells_override = parse_expected_cells(payload.get("expected_cells", payload.get("expectedCells", None)))
    judge = module.judge_match_by_cells(label, line_results, expected_cells_override=expected_cells_override)
    is_match = bool(judge["is_match"])
    result = "일치" if is_match else "불일치"

    if len(decoded_lines) == 1:
        interpretation = decoded_text
    else:
        interpretation = " / ".join(f"{i + 1}줄: {t}" for i, t in enumerate(decoded_lines))

    output_lines = [
        f"의도 : {label}",
        f"해석 : {interpretation}",
        f"결과 : {result}",
        f"판정 : {judge['reason']}",
        f"라벨 기준 점형 : {judge['expected_cells']}",
        f"인식 점형       : {judge['actual_cells']}",
        f"검증 코드 파일 : {validation_path.name}",
        f"base 검출 점 개수 : {len(base_points)}",
        f"감지된 점자 줄 수 : {len(line_boxes)}",
        f"line boxes : {line_boxes}",
    ]

    summary = {
        "intent": label,
        "decoded": interpretation,
        "result": result,
        "judgement": judge["reason"],
        "expected_cells": judge["expected_cells"],
        "actual_cells": judge["actual_cells"],
        "base_dot_count": len(base_points),
        "line_count": len(line_boxes),
    }

    result_payload = {
        "returncode": 0 if is_match else 1,
        "match": is_match,
        "output": "\n".join(output_lines),
        "summary": summary,
        "decoded": decoded_text,
        "result": result,
        "judge": judge,
    }

    return apply_subsequence_match_if_needed(result_payload)


def make_error_response(label: str, message: str, detail: str = ""):
    output = "\n".join([
        f"의도 : {label}",
        "해석 : 검증 실패",
        "결과 : 불일치",
        f"판정 : 브릿지 서버 오류 - {message}",
        detail.strip()[:2000] if detail else "",
    ]).strip()
    return jsonify({
        "returncode": 2,
        "match": False,
        "output": output,
        "summary": {
            "intent": label,
            "decoded": "검증 실패",
            "result": "불일치",
            "judgement": f"브릿지 서버 오류 - {message}",
        },
    }), 500


@app.get("/health")
def health():
    try:
        validation_path = find_validation_file()
        return jsonify({"ok": True, "validation_file": validation_path.name})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/validate/image")
def validate_image():
    payload = request.get_json(silent=True) or {}
    label = str(payload.get("label") or payload.get("text") or payload.get("originalText") or "").strip()
    if not label:
        label = "검증 문구"

    try:
        image_value = (
            payload.get("originalImage")
            or payload.get("image")
            or payload.get("imageData")
            or payload.get("image_data")
            or payload.get("dataUrl")
            or payload.get("data_url")
            or payload.get("roiImage")
            or payload.get("capture")
        )
        original = decode_data_url_image(image_value)
        roi_img, roi_box = crop_roi_if_present(original, payload)
        saved_files = save_debug_images(original, roi_img)

        # 방향 자동 판정:
        # 웹캠/뒷면 점자/브라우저 미리보기 방향이 꼬이는 걸 막기 위해
        # ROI 원본과 좌우반전 ROI를 둘 다 검증하고, 일치한 쪽을 자동 선택한다.
        orientation_mode = str(payload.get("orientation_mode", payload.get("orientationMode", "auto"))).lower()

        if orientation_mode == "auto":
            normal_result = analyze_roi_image(label, roi_img, payload)
            flipped_result = analyze_roi_image(label, cv2.flip(roi_img, 1), payload)

            if flipped_result.get("match") and not normal_result.get("match"):
                result = flipped_result
                chosen_orientation = "flipped"
            else:
                result = normal_result
                chosen_orientation = "normal"

            # 둘 다 실패하면 검출 점 개수가 더 많은 쪽을 보여준다.
            if not normal_result.get("match") and not flipped_result.get("match"):
                normal_points = int(normal_result.get("summary", {}).get("base_dot_count", 0))
                flipped_points = int(flipped_result.get("summary", {}).get("base_dot_count", 0))
                if flipped_points > normal_points:
                    result = flipped_result
                    chosen_orientation = "flipped"

            result["orientation_mode"] = "auto"
            result["chosen_orientation"] = chosen_orientation
            result["normal_match"] = bool(normal_result.get("match"))
            result["flipped_match"] = bool(flipped_result.get("match"))
        else:
            flip_for_validation = bool(payload.get("flip_for_validation", payload.get("flipForValidation", False)))
            validation_img = cv2.flip(roi_img, 1) if flip_for_validation else roi_img
            result = analyze_roi_image(label, validation_img, payload)
            result["orientation_mode"] = "manual"
            result["chosen_orientation"] = "flipped" if flip_for_validation else "normal"
            result["flip_for_validation"] = flip_for_validation

        result["saved_files"] = saved_files
        result["roi_box"] = roi_box
        return jsonify(result)

    except Exception as e:
        detail = traceback.format_exc()
        print(detail, flush=True)
        return make_error_response(label, str(e), detail)


if __name__ == "__main__":
    print("[FreshDot] 브릿지 서버 시작")
    print(f"[FreshDot] 폴더: {BASE_DIR}")
    print(f"[FreshDot] 디버그 이미지 저장 폴더: {DEBUG_DIR}")
    try:
        print(f"[FreshDot] 검증 코드: {find_validation_file().name}")
    except Exception as e:
        print(f"[FreshDot] 검증 코드 확인 실패: {e}")
    app.run(host="127.0.0.1", port=5050, debug=False)
