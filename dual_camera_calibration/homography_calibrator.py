"""
Homography ベースのデュアルカメラキャリブレーションモジュール

4点対応による簡易キャリブレーション + 雲台の角度情報を組み合わせて、
任意の雲台角度における Homography を推定する。

前提条件:
  - 2台のカメラ（物体認識用・RGB）が雲台に搭載されている
  - 雲台の角度（pan/tilt）は物理パラメータとして取得可能
  - 各角度で両カメラに映る同一物体の4点を指定してキャリブレーション
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np


@dataclass
class HomographyEntry:
    """特定の雲台角度における Homography データ"""

    pan_deg: float   # 水平角度（度）
    tilt_deg: float  # 垂直角度（度）
    H: np.ndarray    # 3x3 Homography 行列
    points_cam1: np.ndarray  # カメラ1の4点 (4, 2)
    points_cam2: np.ndarray  # カメラ2の4点 (4, 2)


class HomographyCalibrator:
    """
    4点対応 + 雲台角度によるキャリブレーション

    使い方:
        1. 基準角度 (0, 0) でキャリブレーション
        2. 上下左右に角度を変えて追加キャリブレーション
        3. 任意の角度の Homography を補間で取得
    """

    def __init__(self):
        self.entries: list[HomographyEntry] = []

    def add_calibration(
        self,
        pan_deg: float,
        tilt_deg: float,
        points_cam1: np.ndarray,
        points_cam2: np.ndarray,
    ) -> HomographyEntry:
        """
        特定の雲台角度でのキャリブレーションを追加する

        Parameters
        ----------
        pan_deg : float
            雲台の水平角度（度）
        tilt_deg : float
            雲台の垂直角度（度）
        points_cam1 : np.ndarray
            カメラ1（物体認識用）上の4点 shape=(4, 2)
        points_cam2 : np.ndarray
            カメラ2（RGB）上の4点 shape=(4, 2)

        Returns
        -------
        HomographyEntry
            追加されたキャリブレーションデータ
        """
        pts1 = np.array(points_cam1, dtype=np.float32).reshape(4, 2)
        pts2 = np.array(points_cam2, dtype=np.float32).reshape(4, 2)

        H, status = cv2.findHomography(pts1, pts2)
        if H is None:
            raise ValueError("Homography を計算できませんでした。4点が不正です。")

        entry = HomographyEntry(
            pan_deg=pan_deg,
            tilt_deg=tilt_deg,
            H=H,
            points_cam1=pts1,
            points_cam2=pts2,
        )
        self.entries.append(entry)
        return entry

    def get_homography(self, pan_deg: float, tilt_deg: float) -> np.ndarray:
        """
        指定した雲台角度における Homography を取得する

        キャリブレーション済みの角度と完全一致すればそのまま返し、
        そうでなければ近傍のキャリブレーションデータから補間する。

        Parameters
        ----------
        pan_deg : float
            雲台の水平角度（度）
        tilt_deg : float
            雲台の垂直角度（度）

        Returns
        -------
        np.ndarray
            3x3 Homography 行列
        """
        if not self.entries:
            raise ValueError("キャリブレーションデータがありません。")

        # 完全一致するエントリがあればそれを返す
        for entry in self.entries:
            if (
                abs(entry.pan_deg - pan_deg) < 1e-6
                and abs(entry.tilt_deg - tilt_deg) < 1e-6
            ):
                return entry.H.copy()

        # 補間で Homography を推定
        return self._interpolate(pan_deg, tilt_deg)

    def _interpolate(self, pan_deg: float, tilt_deg: float) -> np.ndarray:
        """
        近傍のキャリブレーションデータから Homography を補間する

        距離の逆数で重み付き平均を行う（IDW: Inverse Distance Weighting）
        """
        # 各エントリとの角度距離を計算
        distances = []
        for entry in self.entries:
            d = np.sqrt(
                (entry.pan_deg - pan_deg) ** 2
                + (entry.tilt_deg - tilt_deg) ** 2
            )
            distances.append(d)

        distances = np.array(distances)

        # 非常に近いエントリがあればそれを返す
        min_idx = np.argmin(distances)
        if distances[min_idx] < 1e-6:
            return self.entries[min_idx].H.copy()

        # IDW（逆距離加重法）で補間
        weights = 1.0 / distances
        weights /= weights.sum()

        # Homography の各要素を重み付き平均
        H_interpolated = np.zeros((3, 3), dtype=np.float64)
        for w, entry in zip(weights, self.entries):
            H_interpolated += w * entry.H

        # H[2,2] = 1 に正規化
        H_interpolated /= H_interpolated[2, 2]

        return H_interpolated

    def transform_point(
        self,
        pan_deg: float,
        tilt_deg: float,
        point_cam1: np.ndarray,
    ) -> np.ndarray:
        """
        カメラ1のピクセル座標をカメラ2のピクセル座標に変換する

        Parameters
        ----------
        pan_deg : float
            現在の雲台の水平角度（度）
        tilt_deg : float
            現在の雲台の垂直角度（度）
        point_cam1 : np.ndarray
            カメラ1上のピクセル座標 (u, v) or (N, 2)

        Returns
        -------
        np.ndarray
            カメラ2上のピクセル座標
        """
        H = self.get_homography(pan_deg, tilt_deg)
        pts = np.array(point_cam1, dtype=np.float32)

        if pts.ndim == 1:
            pts = pts.reshape(1, 1, 2)
            result = cv2.perspectiveTransform(pts, H)
            return result.reshape(2)
        else:
            pts = pts.reshape(-1, 1, 2)
            result = cv2.perspectiveTransform(pts, H)
            return result.reshape(-1, 2)

    def transform_bbox(
        self,
        pan_deg: float,
        tilt_deg: float,
        bbox_cam1: tuple[float, float, float, float],
    ) -> tuple[float, float, float, float]:
        """
        カメラ1上のバウンディングボックスをカメラ2上に変換する

        Parameters
        ----------
        pan_deg, tilt_deg : float
            現在の雲台角度
        bbox_cam1 : tuple[float, float, float, float]
            (x, y, width, height) カメラ1上のバウンディングボックス

        Returns
        -------
        tuple[float, float, float, float]
            (x, y, width, height) カメラ2上のバウンディングボックス
        """
        x, y, w, h = bbox_cam1
        corners = np.array([
            [x, y],
            [x + w, y],
            [x + w, y + h],
            [x, y + h],
        ], dtype=np.float32)

        transformed = self.transform_point(pan_deg, tilt_deg, corners)

        x_min = transformed[:, 0].min()
        y_min = transformed[:, 1].min()
        x_max = transformed[:, 0].max()
        y_max = transformed[:, 1].max()

        return (float(x_min), float(y_min), float(x_max - x_min), float(y_max - y_min))

    def estimate_movement(
        self,
        pan_before: float,
        tilt_before: float,
        pan_after: float,
        tilt_after: float,
    ) -> dict:
        """
        雲台の角度変化に伴う Homography の変化量を計算する

        Parameters
        ----------
        pan_before, tilt_before : float
            移動前の雲台角度
        pan_after, tilt_after : float
            移動後の雲台角度

        Returns
        -------
        dict
            移動に関する情報
        """
        H_before = self.get_homography(pan_before, tilt_before)
        H_after = self.get_homography(pan_after, tilt_after)

        # Homography の差分
        H_delta = H_after @ np.linalg.inv(H_before)

        # 代表点で移動量を可視化（画像中心付近）
        test_points = np.array([
            [320, 240],
            [0, 0],
            [640, 0],
            [640, 480],
            [0, 480],
        ], dtype=np.float32)

        pts_before = cv2.perspectiveTransform(
            test_points.reshape(-1, 1, 2), H_before
        ).reshape(-1, 2)
        pts_after = cv2.perspectiveTransform(
            test_points.reshape(-1, 1, 2), H_after
        ).reshape(-1, 2)

        pixel_shifts = pts_after - pts_before
        avg_shift = np.mean(np.linalg.norm(pixel_shifts, axis=1))

        return {
            "pan_delta": pan_after - pan_before,
            "tilt_delta": tilt_after - tilt_before,
            "H_delta": H_delta,
            "avg_pixel_shift": float(avg_shift),
            "pixel_shifts": pixel_shifts,
        }

    def save(self, path: str) -> None:
        """キャリブレーションデータを保存する"""
        data = []
        for entry in self.entries:
            data.append({
                "pan_deg": entry.pan_deg,
                "tilt_deg": entry.tilt_deg,
                "H": entry.H.tolist(),
                "points_cam1": entry.points_cam1.tolist(),
                "points_cam2": entry.points_cam2.tolist(),
            })
        Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False))

    @classmethod
    def load(cls, path: str) -> "HomographyCalibrator":
        """保存したキャリブレーションデータを読み込む"""
        data = json.loads(Path(path).read_text())
        calibrator = cls()
        for item in data:
            entry = HomographyEntry(
                pan_deg=item["pan_deg"],
                tilt_deg=item["tilt_deg"],
                H=np.array(item["H"]),
                points_cam1=np.array(item["points_cam1"], dtype=np.float32),
                points_cam2=np.array(item["points_cam2"], dtype=np.float32),
            )
            calibrator.entries.append(entry)
        return calibrator
