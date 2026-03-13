"""
雲台 + 4点対応によるデュアルカメラキャリブレーションの使用例

前提:
  - 物体認識カメラ: 640x480
  - RGBカメラ:      1920x1080
  - 雲台で上下 ±5度 の範囲で動かす
  - 各角度で同じ物体の4点を両カメラで指定してキャリブレーション
"""

import numpy as np

from dual_camera_calibration import HomographyCalibrator


def main():
    calibrator = HomographyCalibrator()

    # ============================================================
    # ステップ1: 複数角度でキャリブレーション
    #
    # 実際の運用では、各角度で同じ物体の4点を
    # 両方のカメラ画像上でクリックして取得する。
    # ここではデモ用のダミーデータを使用。
    # ============================================================
    print("=== ステップ1: 複数角度でのキャリブレーション ===\n")

    # --- 基準位置 (pan=0, tilt=0) ---
    # 物体認識カメラ上の4点
    pts_cam1_base = np.array([
        [100, 80],
        [540, 80],
        [540, 400],
        [100, 400],
    ], dtype=np.float32)

    # RGBカメラ上の対応する4点（解像度が高いのでピクセル値も大きい）
    pts_cam2_base = np.array([
        [280, 160],
        [1640, 160],
        [1640, 920],
        [280, 920],
    ], dtype=np.float32)

    calibrator.add_calibration(
        pan_deg=0, tilt_deg=0,
        points_cam1=pts_cam1_base,
        points_cam2=pts_cam2_base,
    )
    print("  (pan=0, tilt=0) キャリブレーション追加")

    # --- tilt = +5度（上向き） ---
    # 雲台を上に5度傾けると、画像内の物体は下方向にずれる
    pts_cam1_up5 = pts_cam1_base + np.array([0, 30], dtype=np.float32)
    pts_cam2_up5 = pts_cam2_base + np.array([0, 70], dtype=np.float32)

    calibrator.add_calibration(
        pan_deg=0, tilt_deg=5,
        points_cam1=pts_cam1_up5,
        points_cam2=pts_cam2_up5,
    )
    print("  (pan=0, tilt=+5) キャリブレーション追加")

    # --- tilt = -5度（下向き） ---
    pts_cam1_dn5 = pts_cam1_base - np.array([0, 30], dtype=np.float32)
    pts_cam2_dn5 = pts_cam2_base - np.array([0, 70], dtype=np.float32)

    calibrator.add_calibration(
        pan_deg=0, tilt_deg=-5,
        points_cam1=pts_cam1_dn5,
        points_cam2=pts_cam2_dn5,
    )
    print("  (pan=0, tilt=-5) キャリブレーション追加")

    # --- tilt = +2度（中間角度、補間精度の検証用） ---
    pts_cam1_up2 = pts_cam1_base + np.array([0, 12], dtype=np.float32)
    pts_cam2_up2 = pts_cam2_base + np.array([0, 28], dtype=np.float32)

    calibrator.add_calibration(
        pan_deg=0, tilt_deg=2,
        points_cam1=pts_cam1_up2,
        points_cam2=pts_cam2_up2,
    )
    print("  (pan=0, tilt=+2) キャリブレーション追加")

    print(f"\n合計 {len(calibrator.entries)} 角度でキャリブレーション完了\n")

    # ============================================================
    # ステップ2: 任意の角度での座標変換
    # ============================================================
    print("=== ステップ2: 座標変換 ===\n")

    # 物体認識カメラで人物を検出したとする
    detection = {
        "label": "person",
        "bbox": (200, 150, 120, 200),  # (x, y, w, h)
        "confidence": 0.95,
    }

    # 現在の雲台角度
    current_pan = 0.0
    current_tilt = 3.0  # キャリブレーション済みの角度の間（補間される）

    # バウンディングボックスをRGBカメラ座標に変換
    bbox_rgb = calibrator.transform_bbox(
        current_pan, current_tilt,
        detection["bbox"],
    )
    print(f"現在の雲台角度: pan={current_pan}°, tilt={current_tilt}°")
    print(f"物体認識カメラ上の検出: {detection['bbox']}")
    print(f"RGBカメラ上にマッピング: ({bbox_rgb[0]:.1f}, {bbox_rgb[1]:.1f}, {bbox_rgb[2]:.1f}, {bbox_rgb[3]:.1f})")

    # 中心点の変換
    center = np.array([
        detection["bbox"][0] + detection["bbox"][2] / 2,
        detection["bbox"][1] + detection["bbox"][3] / 2,
    ])
    center_rgb = calibrator.transform_point(current_pan, current_tilt, center)
    print(f"\n物体認識カメラ中心: ({center[0]:.0f}, {center[1]:.0f})")
    print(f"RGBカメラ中心:     ({center_rgb[0]:.1f}, {center_rgb[1]:.1f})")

    # ============================================================
    # ステップ3: 雲台の移動量の分析
    # ============================================================
    print("\n=== ステップ3: 雲台移動の影響分析 ===\n")

    movement = calibrator.estimate_movement(
        pan_before=0, tilt_before=0,
        pan_after=0, tilt_after=5,
    )
    print(f"雲台の移動: tilt {movement['tilt_delta']}°")
    print(f"RGBカメラ上の平均ピクセルシフト: {movement['avg_pixel_shift']:.1f}px")

    # ============================================================
    # ステップ4: キャリブレーションデータの保存/読み込み
    # ============================================================
    print("\n=== ステップ4: データ保存/読み込み ===\n")

    calibrator.save("pantilt_calibration.json")
    loaded = HomographyCalibrator.load("pantilt_calibration.json")
    print(f"保存完了: {len(loaded.entries)} エントリ")

    # 読み込んだデータで同じ変換ができることを確認
    center_rgb_loaded = loaded.transform_point(current_pan, current_tilt, center)
    print(f"読み込み後の変換結果: ({center_rgb_loaded[0]:.1f}, {center_rgb_loaded[1]:.1f})")
    print(f"一致確認: {np.allclose(center_rgb, center_rgb_loaded)}")


if __name__ == "__main__":
    main()
