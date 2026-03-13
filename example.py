"""
デュアルカメラキャリブレーション＆移動検出の使用例

このスクリプトは以下の流れを示します:
  1. 初回キャリブレーション（チェッカーボード画像を使用）
  2. キャリブレーション結果の保存
  3. カメラ移動後の再キャリブレーション
  4. 移動量の検出
  5. 物体認識結果のRGBカメラへのマッピング
"""

import glob

import cv2
import numpy as np

from dual_camera_calibration import CameraMovementDetector, StereoCalibrator
from dual_camera_calibration.calibrator import CalibrationResult
from dual_camera_calibration.movement_detector import compute_projection_mapping


def main():
    # ============================================================
    # ステップ1: 初回キャリブレーション
    # ============================================================
    print("=== ステップ1: 初回キャリブレーション ===")

    calibrator = StereoCalibrator(
        checkerboard_size=(9, 6),  # チェッカーボードの内部コーナー数
        square_size_mm=25.0,  # 1マス 25mm
    )

    # チェッカーボード画像の読み込み
    # ※実際の使用時は同時刻に撮影した画像ペアを用意してください
    images_cam1 = [cv2.imread(f) for f in sorted(glob.glob("images/cam1_*.png"))]
    images_cam2 = [cv2.imread(f) for f in sorted(glob.glob("images/cam2_*.png"))]

    if not images_cam1 or not images_cam2:
        print("画像が見つかりません。デモ用のダミーデータで実行します。")
        demo_with_synthetic_data()
        return

    baseline = calibrator.calibrate(images_cam1, images_cam2)
    baseline.save("calibration_baseline.json")
    print(f"再投影誤差: {baseline.reprojection_error:.4f}")
    print(f"カメラ間の距離: {np.linalg.norm(baseline.t):.1f}mm")

    # ============================================================
    # ステップ2: カメラ移動後に再キャリブレーション
    # ============================================================
    print("\n=== ステップ2: 再キャリブレーション ===")

    images_cam1_new = [cv2.imread(f) for f in sorted(glob.glob("images/cam1_new_*.png"))]
    images_cam2_new = [cv2.imread(f) for f in sorted(glob.glob("images/cam2_new_*.png"))]

    current = calibrator.calibrate(images_cam1_new, images_cam2_new)

    # ============================================================
    # ステップ3: 移動量の検出
    # ============================================================
    print("\n=== ステップ3: 移動量の検出 ===")

    detector = CameraMovementDetector(baseline)
    movement = detector.detect_movement(current)
    print(movement)

    if detector.is_moved(current, translation_threshold_mm=5.0, rotation_threshold_deg=1.0):
        print("\n⚠ カメラの移動を検出しました。再キャリブレーションを推奨します。")

    # ============================================================
    # ステップ4: 物体認識結果をRGBカメラにマッピング
    # ============================================================
    print("\n=== ステップ4: 座標マッピング ===")

    # 物体認識カメラで検出したバウンディングボックス（例）
    detection_bbox = {
        "x": 320,
        "y": 240,
        "width": 100,
        "height": 80,
        "label": "person",
        "depth_mm": 2000.0,  # 推定距離 2m
    }

    # 中心点をRGBカメラ座標に変換
    center_cam1 = np.array([
        detection_bbox["x"] + detection_bbox["width"] / 2,
        detection_bbox["y"] + detection_bbox["height"] / 2,
    ])

    center_cam2 = compute_projection_mapping(
        current,  # 最新のキャリブレーション結果を使用
        center_cam1,
        detection_bbox["depth_mm"],
    )

    print(f"物体認識カメラ上の検出位置: ({center_cam1[0]:.0f}, {center_cam1[1]:.0f})")
    print(f"RGBカメラ上のマッピング位置: ({center_cam2[0]:.0f}, {center_cam2[1]:.0f})")


def demo_with_synthetic_data():
    """画像がない場合のデモ: 合成データで移動検出を実演"""
    print("\n--- 合成データによるデモ ---\n")

    # ダミーの初回キャリブレーション結果
    baseline = CalibrationResult(
        camera_matrix_1=np.array([
            [500.0, 0.0, 320.0],
            [0.0, 500.0, 240.0],
            [0.0, 0.0, 1.0],
        ]),
        dist_coeffs_1=np.zeros(5),
        camera_matrix_2=np.array([
            [800.0, 0.0, 960.0],
            [0.0, 800.0, 540.0],
            [0.0, 0.0, 1.0],
        ]),
        dist_coeffs_2=np.zeros(5),
        R=np.eye(3),  # カメラは平行に設置
        t=np.array([[100.0], [0.0], [0.0]]),  # 100mm 横にずれている
        reprojection_error=0.5,
    )

    # カメラが少し動いた後のキャリブレーション結果をシミュレーション
    # 5度 Y軸回りに回転 + 10mm 上方向に移動
    angle_rad = np.radians(5.0)
    R_moved = np.array([
        [np.cos(angle_rad), 0, np.sin(angle_rad)],
        [0, 1, 0],
        [-np.sin(angle_rad), 0, np.cos(angle_rad)],
    ])

    current = CalibrationResult(
        camera_matrix_1=baseline.camera_matrix_1,
        dist_coeffs_1=baseline.dist_coeffs_1,
        camera_matrix_2=baseline.camera_matrix_2,
        dist_coeffs_2=baseline.dist_coeffs_2,
        R=R_moved,
        t=np.array([[100.0], [10.0], [0.0]]),  # 10mm上方向に移動
        reprojection_error=0.6,
    )

    # 移動量の検出
    detector = CameraMovementDetector(baseline)
    movement = detector.detect_movement(current)
    print(movement)

    moved = detector.is_moved(current)
    print(f"\nカメラ移動検出: {'あり' if moved else 'なし'}")

    # 座標マッピングのデモ
    print("\n--- 座標マッピングのデモ ---")
    point_cam1 = np.array([320.0, 240.0])  # カメラ1の中心付近
    depth = 2000.0  # 2m先

    point_cam2 = compute_projection_mapping(current, point_cam1, depth)
    print(f"物体認識カメラ: ({point_cam1[0]:.0f}, {point_cam1[1]:.0f})")
    print(f"RGBカメラ:     ({point_cam2[0]:.0f}, {point_cam2[1]:.0f})")

    # キャリブレーション結果の保存/読み込みデモ
    baseline.save("calibration_baseline.json")
    loaded = CalibrationResult.load("calibration_baseline.json")
    print(f"\n保存/読み込みテスト: R一致={np.allclose(baseline.R, loaded.R)}")


if __name__ == "__main__":
    main()
