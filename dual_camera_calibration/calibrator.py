"""
ステレオカメラキャリブレーションモジュール

2台の解像度が異なるカメラ（物体認識用・RGBカメラ）間の
内部パラメータと外部パラメータを算出する。
"""

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass
class CalibrationResult:
    """キャリブレーション結果を保持するデータクラス"""

    # カメラ1（物体認識用）の内部パラメータ
    camera_matrix_1: np.ndarray
    dist_coeffs_1: np.ndarray

    # カメラ2（RGB）の内部パラメータ
    camera_matrix_2: np.ndarray
    dist_coeffs_2: np.ndarray

    # カメラ間の外部パラメータ
    R: np.ndarray  # 回転行列 (3x3)
    t: np.ndarray  # 並進ベクトル (3x1)

    # 再投影誤差
    reprojection_error: float

    def save(self, path: str) -> None:
        """キャリブレーション結果をJSONファイルに保存する"""
        data = {
            "camera_matrix_1": self.camera_matrix_1.tolist(),
            "dist_coeffs_1": self.dist_coeffs_1.tolist(),
            "camera_matrix_2": self.camera_matrix_2.tolist(),
            "dist_coeffs_2": self.dist_coeffs_2.tolist(),
            "R": self.R.tolist(),
            "t": self.t.tolist(),
            "reprojection_error": self.reprojection_error,
        }
        Path(path).write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, path: str) -> "CalibrationResult":
        """JSONファイルからキャリブレーション結果を読み込む"""
        data = json.loads(Path(path).read_text())
        return cls(
            camera_matrix_1=np.array(data["camera_matrix_1"]),
            dist_coeffs_1=np.array(data["dist_coeffs_1"]),
            camera_matrix_2=np.array(data["camera_matrix_2"]),
            dist_coeffs_2=np.array(data["dist_coeffs_2"]),
            R=np.array(data["R"]),
            t=np.array(data["t"]),
            reprojection_error=data["reprojection_error"],
        )


class StereoCalibrator:
    """
    2台のカメラのステレオキャリブレーションを行うクラス

    使い方:
        1. チェッカーボードを両カメラで同時に撮影した画像ペアを複数枚用意
        2. calibrate() でキャリブレーション実行
        3. 結果の R, t がカメラ間の相対位置関係を表す

    Parameters
    ----------
    checkerboard_size : tuple[int, int]
        チェッカーボードの内部コーナー数 (列, 行)
    square_size_mm : float
        チェッカーボードの1マスの実寸（mm）
    """

    def __init__(
        self,
        checkerboard_size: tuple[int, int] = (9, 6),
        square_size_mm: float = 25.0,
    ):
        self.checkerboard_size = checkerboard_size
        self.square_size_mm = square_size_mm

        # 3Dオブジェクトポイント（チェッカーボード上の点の実座標）
        self.objp = np.zeros(
            (checkerboard_size[0] * checkerboard_size[1], 3), dtype=np.float32
        )
        self.objp[:, :2] = (
            np.mgrid[0 : checkerboard_size[0], 0 : checkerboard_size[1]]
            .T.reshape(-1, 2)
            .astype(np.float32)
            * square_size_mm
        )

    def calibrate(
        self,
        images_cam1: list[np.ndarray],
        images_cam2: list[np.ndarray],
    ) -> CalibrationResult:
        """
        ステレオキャリブレーションを実行する

        Parameters
        ----------
        images_cam1 : list[np.ndarray]
            カメラ1（物体認識用）で撮影した画像のリスト
        images_cam2 : list[np.ndarray]
            カメラ2（RGB）で撮影した画像のリスト
            ※ images_cam1 と同時刻に撮影されたペアであること

        Returns
        -------
        CalibrationResult
            キャリブレーション結果
        """
        if len(images_cam1) != len(images_cam2):
            raise ValueError("両カメラの画像数が一致しません")

        obj_points = []  # 3D座標
        img_points_1 = []  # カメラ1の2D座標
        img_points_2 = []  # カメラ2の2D座標

        for i, (img1, img2) in enumerate(zip(images_cam1, images_cam2)):
            gray1 = self._to_gray(img1)
            gray2 = self._to_gray(img2)

            found1, corners1 = cv2.findChessboardCorners(
                gray1, self.checkerboard_size, None
            )
            found2, corners2 = cv2.findChessboardCorners(
                gray2, self.checkerboard_size, None
            )

            if not found1 or not found2:
                print(f"画像ペア {i} でチェッカーボードを検出できませんでした。スキップします。")
                continue

            # サブピクセル精度でコーナー位置を補正
            criteria = (
                cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER,
                30,
                0.001,
            )
            corners1 = cv2.cornerSubPix(gray1, corners1, (11, 11), (-1, -1), criteria)
            corners2 = cv2.cornerSubPix(gray2, corners2, (11, 11), (-1, -1), criteria)

            obj_points.append(self.objp)
            img_points_1.append(corners1)
            img_points_2.append(corners2)

        if len(obj_points) < 3:
            raise ValueError(
                f"有効な画像ペアが {len(obj_points)} 組しかありません。"
                "最低3組以上のペアが必要です。"
            )

        img_size_1 = (images_cam1[0].shape[1], images_cam1[0].shape[0])
        img_size_2 = (images_cam2[0].shape[1], images_cam2[0].shape[0])

        # 各カメラの個別キャリブレーション
        ret1, mtx1, dist1, _, _ = cv2.calibrateCamera(
            obj_points, img_points_1, img_size_1, None, None
        )
        ret2, mtx2, dist2, _, _ = cv2.calibrateCamera(
            obj_points, img_points_2, img_size_2, None, None
        )

        # ステレオキャリブレーション
        flags = cv2.CALIB_FIX_INTRINSIC
        criteria = (
            cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER,
            100,
            1e-6,
        )
        ret, mtx1, dist1, mtx2, dist2, R, t, _, _ = cv2.stereoCalibrate(
            obj_points,
            img_points_1,
            img_points_2,
            mtx1,
            dist1,
            mtx2,
            dist2,
            img_size_1,
            criteria=criteria,
            flags=flags,
        )

        print(f"ステレオキャリブレーション完了 (再投影誤差: {ret:.4f})")

        return CalibrationResult(
            camera_matrix_1=mtx1,
            dist_coeffs_1=dist1,
            camera_matrix_2=mtx2,
            dist_coeffs_2=dist2,
            R=R,
            t=t,
            reprojection_error=ret,
        )

    @staticmethod
    def _to_gray(img: np.ndarray) -> np.ndarray:
        if len(img.shape) == 2:
            return img
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
