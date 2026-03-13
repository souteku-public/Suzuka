"""
補間精度の比較: IDW補間 vs 解析的手法

合成データを使い、「真の Homography」に対する誤差を計測する。
CG描画のずれがどの程度になるか定量的に示す。
"""

import numpy as np
import cv2

from dual_camera_calibration.homography_calibrator import HomographyCalibrator
from dual_camera_calibration.analytic_calibrator import AnalyticHomographyCalibrator


# ============================================================
# 真のカメラパラメータ（Ground Truth）
# ============================================================

# RGBカメラの内部パラメータ（真値）
K2_TRUE = np.array([
    [1200.0, 0, 960.0],
    [0, 1200.0, 540.0],
    [0, 0, 1],
])

# 物体認識カメラの内部パラメータ（真値）
K1_TRUE = np.array([
    [400.0, 0, 320.0],
    [0, 400.0, 240.0],
    [0, 0, 1],
])

# 基準位置でのカメラ間の相対回転（真値: わずかに傾いている）
R_BASE_TRUE = np.array([
    [0.9998, 0.0, 0.0175],
    [0.0, 1.0, 0.0],
    [-0.0175, 0.0, 0.9998],
])


def rotation_x(deg: float) -> np.ndarray:
    r = np.radians(deg)
    return np.array([
        [1, 0, 0],
        [0, np.cos(r), -np.sin(r)],
        [0, np.sin(r), np.cos(r)],
    ])


def rotation_y(deg: float) -> np.ndarray:
    r = np.radians(deg)
    return np.array([
        [np.cos(r), 0, np.sin(r)],
        [0, 1, 0],
        [-np.sin(r), 0, np.cos(r)],
    ])


def true_homography(pan_deg: float, tilt_deg: float) -> np.ndarray:
    """真の Homography を計算する（Ground Truth）"""
    R = rotation_y(pan_deg) @ rotation_x(tilt_deg) @ R_BASE_TRUE
    H = K2_TRUE @ R @ np.linalg.inv(K1_TRUE)
    H /= H[2, 2]
    return H


def generate_points(H: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Homography から4点の対応を生成する"""
    pts_cam1 = np.array([
        [100, 80],
        [540, 80],
        [540, 400],
        [100, 400],
    ], dtype=np.float64)

    pts_cam1_cv = pts_cam1.reshape(-1, 1, 2)
    pts_cam2 = cv2.perspectiveTransform(pts_cam1_cv, H).reshape(-1, 2)

    return pts_cam1, pts_cam2


def measure_error(H_predicted: np.ndarray, H_true: np.ndarray) -> dict:
    """
    2つの Homography の誤差をピクセル単位で計測する

    物体認識カメラの画像全体にテストポイントを配置し、
    RGBカメラ上での予測位置と真値の差を計算する。
    """
    # テスト点: 画像全体に均等配置
    test_points = []
    for x in range(0, 641, 64):
        for y in range(0, 481, 48):
            test_points.append([x, y])
    test_points = np.array(test_points, dtype=np.float64)

    pts_cv = test_points.reshape(-1, 1, 2)
    projected_predicted = cv2.perspectiveTransform(pts_cv, H_predicted).reshape(-1, 2)
    projected_true = cv2.perspectiveTransform(pts_cv, H_true).reshape(-1, 2)

    errors = np.linalg.norm(projected_predicted - projected_true, axis=1)

    return {
        "mean_px": float(errors.mean()),
        "max_px": float(errors.max()),
        "median_px": float(np.median(errors)),
        "std_px": float(errors.std()),
    }


def main():
    print("=" * 70)
    print("補間精度の比較: IDW補間 vs 解析的手法")
    print("=" * 70)

    # ============================================================
    # キャリブレーションデータの生成
    # ============================================================
    calib_angles = [
        (0, 0),    # 基準
        (0, 5),    # tilt +5°
        (0, -5),   # tilt -5°
    ]

    # --- IDW 補間キャリブレーター ---
    idw_cal = HomographyCalibrator()
    for pan, tilt in calib_angles:
        H = true_homography(pan, tilt)
        pts1, pts2 = generate_points(H)
        idw_cal.add_calibration(pan, tilt, pts1, pts2)

    # --- 解析的キャリブレーター ---
    analytic_cal = AnalyticHomographyCalibrator()
    for pan, tilt in calib_angles:
        H = true_homography(pan, tilt)
        pts1, pts2 = generate_points(H)
        analytic_cal.add_calibration(pan, tilt, pts1, pts2)

    print("\n解析的キャリブレーション実行:")
    analytic_cal.calibrate(base_index=0, image_size_cam2=(1920, 1080))

    # ============================================================
    # 各角度での誤差を計測
    # ============================================================
    test_angles = [
        (0, 0),     # キャリブレーション済み角度
        (0, 1),     # 中間角度
        (0, 2),
        (0, 2.5),   # キャリブレーション点の間
        (0, 3),
        (0, 4),
        (0, -1),
        (0, -2.5),
        (0, -3),
        (0, -4),
        (0, 7),     # キャリブレーション範囲外（外挿）
        (0, -7),
    ]

    print(f"\n{'角度':>10s} | {'IDW平均誤差':>12s} | {'IDW最大誤差':>12s} | {'解析的平均誤差':>14s} | {'解析的最大誤差':>14s}")
    print("-" * 75)

    for pan, tilt in test_angles:
        H_true = true_homography(pan, tilt)

        # IDW
        H_idw = idw_cal.get_homography(pan, tilt)
        err_idw = measure_error(H_idw, H_true)

        # 解析的
        H_analytic = analytic_cal.get_homography(pan, tilt)
        err_analytic = measure_error(H_analytic, H_true)

        label = f"tilt={tilt:+.1f}°"
        if (pan, tilt) in calib_angles:
            label += " *"  # キャリブレーション済み
        elif abs(tilt) > 5:
            label += " !"  # 範囲外

        print(
            f"{label:>12s} | "
            f"{err_idw['mean_px']:>10.2f}px | "
            f"{err_idw['max_px']:>10.2f}px | "
            f"{err_analytic['mean_px']:>12.4f}px | "
            f"{err_analytic['max_px']:>12.4f}px"
        )

    # ============================================================
    # CG 描画への影響の解説
    # ============================================================
    print("\n" + "=" * 70)
    print("結果の解説")
    print("=" * 70)
    print("""
* 印 = キャリブレーション済み角度（誤差なし or ごく小さい）
! 印 = キャリブレーション範囲外（外挿）

■ IDW 補間の問題点:
  - Homography の行列要素を線形加重平均しているため、
    射影変換の非線形性を正しく扱えない
  - キャリブレーション点から離れるほど誤差が増大
  - 外挿時に誤差が急激に増加する

■ 解析的手法の利点:
  - 雲台の回転を 3D 回転行列として正しくモデル化
  - K₂（RGBカメラの焦点距離）を推定することで、
    任意角度の Homography を数学的に正確に計算
  - キャリブレーション範囲外（外挿）でも精度が維持される

■ CG 描画への影響:
  - 1px の誤差 → 1920x1080 の画面で約 0.05% のずれ
  - 5px の誤差 → 肉眼で認識可能なずれ（BBox の位置がずれる）
  - 10px以上    → CG合成として品質上問題がある
""")


if __name__ == "__main__":
    main()
