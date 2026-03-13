"""
カメラ移動検出モジュール

初回キャリブレーション時の外部パラメータ (R, t) と
再キャリブレーション時の外部パラメータを比較して、
カメラの移動量（並進・回転）を算出する。
"""

from dataclasses import dataclass

import cv2
import numpy as np

from .calibrator import CalibrationResult, StereoCalibrator


@dataclass
class MovementResult:
    """カメラ移動量の計算結果"""

    # 並進の変化量 (mm)
    translation_delta: np.ndarray  # (3,) [dx, dy, dz]

    # 回転の変化量（ロドリゲスベクトル形式, ラジアン）
    rotation_delta_rvec: np.ndarray  # (3,)

    # 回転の変化量（度）
    rotation_delta_degrees: np.ndarray  # (3,) [rx, ry, rz]

    # 並進の移動距離 (mm)
    translation_distance_mm: float

    # 回転の総変化量（度）
    rotation_angle_degrees: float

    def __str__(self) -> str:
        return (
            "=== カメラ移動検出結果 ===\n"
            f"並進の変化量: dx={self.translation_delta[0]:.2f}mm, "
            f"dy={self.translation_delta[1]:.2f}mm, "
            f"dz={self.translation_delta[2]:.2f}mm\n"
            f"並進の移動距離: {self.translation_distance_mm:.2f}mm\n"
            f"回転の変化量: rx={self.rotation_delta_degrees[0]:.3f}°, "
            f"ry={self.rotation_delta_degrees[1]:.3f}°, "
            f"rz={self.rotation_delta_degrees[2]:.3f}°\n"
            f"回転の総変化量: {self.rotation_angle_degrees:.3f}°"
        )


class CameraMovementDetector:
    """
    カメラの物理的な移動量を検出するクラス

    初回キャリブレーション結果と再キャリブレーション結果を比較し、
    カメラがどれだけ動いたかを計算する。

    使い方:
        1. 初回キャリブレーション結果を baseline として設定
        2. カメラが動いた後に再キャリブレーションを実行
        3. detect_movement() で移動量を計算
    """

    def __init__(self, baseline: CalibrationResult):
        """
        Parameters
        ----------
        baseline : CalibrationResult
            初回キャリブレーション時の結果（基準）
        """
        self.baseline = baseline

    def detect_movement(self, current: CalibrationResult) -> MovementResult:
        """
        基準と現在のキャリブレーション結果を比較して移動量を算出する

        Parameters
        ----------
        current : CalibrationResult
            再キャリブレーション後の結果

        Returns
        -------
        MovementResult
            移動量の計算結果
        """
        # 回転の差分: R_delta = R_current @ R_baseline^T
        # これは「基準からどれだけ回転したか」を表す
        R_delta = current.R @ self.baseline.R.T

        # ロドリゲスベクトルに変換（回転軸 * 回転角）
        rvec_delta, _ = cv2.Rodrigues(R_delta)
        rvec_delta = rvec_delta.flatten()

        # 回転角度（度）
        angle_rad = np.linalg.norm(rvec_delta)
        angle_deg = np.degrees(angle_rad)

        # 各軸の回転量を度に変換
        rotation_degrees = np.degrees(rvec_delta)

        # 並進の差分
        t_delta = current.t.flatten() - self.baseline.t.flatten()
        distance = float(np.linalg.norm(t_delta))

        return MovementResult(
            translation_delta=t_delta,
            rotation_delta_rvec=rvec_delta,
            rotation_delta_degrees=rotation_degrees,
            translation_distance_mm=distance,
            rotation_angle_degrees=angle_deg,
        )

    def is_moved(
        self,
        current: CalibrationResult,
        translation_threshold_mm: float = 5.0,
        rotation_threshold_deg: float = 1.0,
    ) -> bool:
        """
        カメラが移動したかどうかを判定する

        Parameters
        ----------
        current : CalibrationResult
            再キャリブレーション後の結果
        translation_threshold_mm : float
            並進のしきい値（mm）。これ以上動いていたら True
        rotation_threshold_deg : float
            回転のしきい値（度）。これ以上回転していたら True

        Returns
        -------
        bool
            移動が検出された場合 True
        """
        result = self.detect_movement(current)
        return (
            result.translation_distance_mm > translation_threshold_mm
            or result.rotation_angle_degrees > rotation_threshold_deg
        )


def compute_projection_mapping(
    calib: CalibrationResult,
    point_in_cam1: np.ndarray,
    depth_mm: float,
) -> np.ndarray:
    """
    カメラ1上の点をカメラ2の画像座標に変換する

    物体認識カメラで検出した座標を、RGBカメラの画像上に
    マッピングするために使用する。

    Parameters
    ----------
    calib : CalibrationResult
        キャリブレーション結果
    point_in_cam1 : np.ndarray
        カメラ1上のピクセル座標 (u, v)
    depth_mm : float
        対象物体までの推定距離（mm）

    Returns
    -------
    np.ndarray
        カメラ2上のピクセル座標 (u, v)
    """
    # カメラ1のピクセル座標 → カメラ1の正規化座標
    u, v = point_in_cam1
    K1_inv = np.linalg.inv(calib.camera_matrix_1)
    p_normalized = K1_inv @ np.array([u, v, 1.0])

    # カメラ1座標系での3D点（推定深度を使用）
    p_cam1 = p_normalized * depth_mm

    # カメラ1 → カメラ2 への座標変換
    p_cam2 = calib.R @ p_cam1 + calib.t.flatten()

    # カメラ2の画像座標に投影
    p_projected = calib.camera_matrix_2 @ p_cam2
    p_projected = p_projected / p_projected[2]

    return p_projected[:2]
