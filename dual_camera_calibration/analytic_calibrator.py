"""
解析的 Homography 推定モジュール

雲台の回転角度が既知であることを利用して、
基準角度の Homography から任意角度の Homography を
数学的に正確に算出する。

IDW 補間（行列要素の加重平均）と異なり、射影変換群の
構造を正しく扱うため、理論上の補間誤差がゼロになる。

理論:
  雲台に搭載された2台のカメラの関係は以下で表される:

    p₂ = K₂ · (R_rel + t·nᵀ/d) · K₁⁻¹ · p₁

  ここで:
    K₁, K₂ : 各カメラの内部パラメータ行列
    R_rel   : カメラ間の相対回転
    t       : カメラ間の並進ベクトル
    n, d    : 対象平面の法線と距離

  雲台が角度 Δθ だけ回転すると:
    - 両カメラが同じ雲台に載っている場合 → R_rel は変わらない
    - 片方だけが動く場合 → R_rel に雲台回転 R_pan_tilt(Δθ) が合成される

  遠方の被写体（t·nᵀ/d ≈ 0）の場合、Homography は純粋な回転で近似でき:

    H(θ) ≈ K₂ · R_pan_tilt(Δθ) · K₂⁻¹ · H(θ₀)

  となる。K₂ が未知でも、複数角度のキャリブレーションから推定できる。
"""

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


def _rotation_x(angle_deg: float) -> np.ndarray:
    """X軸周りの回転行列（tilt）"""
    r = np.radians(angle_deg)
    return np.array([
        [1, 0, 0],
        [0, np.cos(r), -np.sin(r)],
        [0, np.sin(r), np.cos(r)],
    ])


def _rotation_y(angle_deg: float) -> np.ndarray:
    """Y軸周りの回転行列（pan）"""
    r = np.radians(angle_deg)
    return np.array([
        [np.cos(r), 0, np.sin(r)],
        [0, 1, 0],
        [-np.sin(r), 0, np.cos(r)],
    ])


@dataclass
class AnalyticCalibrationResult:
    """解析的キャリブレーションの結果"""

    H_base: np.ndarray        # 基準角度での Homography (3x3)
    K2: np.ndarray            # RGBカメラの推定内部パラメータ (3x3)
    K2_inv: np.ndarray        # K2 の逆行列
    base_pan_deg: float       # 基準 pan 角度
    base_tilt_deg: float      # 基準 tilt 角度
    reprojection_error: float # 再投影誤差（ピクセル）


class AnalyticHomographyCalibrator:
    """
    解析的に任意角度の Homography を算出するキャリブレーター

    原理:
      雲台の回転は3D空間での剛体回転。RGBカメラの内部パラメータ K₂ が
      わかれば、任意の雲台角度に対する Homography を解析的に計算できる。

      H(pan, tilt) = K₂ · R_y(Δpan) · R_x(Δtilt) · K₂⁻¹ · H_base

    キャリブレーション手順:
      1. 基準角度で4点キャリブレーション → H_base を取得
      2. 別の角度でも4点キャリブレーション → K₂ を推定
      3. 以降は角度だけで H を解析的に算出（追加キャリブレーション不要）
    """

    def __init__(self):
        self._calibrations: list[dict] = []
        self._result: AnalyticCalibrationResult | None = None

    def add_calibration(
        self,
        pan_deg: float,
        tilt_deg: float,
        points_cam1: np.ndarray,
        points_cam2: np.ndarray,
    ) -> None:
        """
        キャリブレーションデータを追加する

        最低2角度（基準 + 別角度1つ）が必要。
        3角度以上あると K₂ の推定精度が向上する。
        """
        pts1 = np.array(points_cam1, dtype=np.float64).reshape(4, 2)
        pts2 = np.array(points_cam2, dtype=np.float64).reshape(4, 2)

        H, _ = cv2.findHomography(pts1, pts2)
        if H is None:
            raise ValueError("Homography を計算できませんでした。")

        self._calibrations.append({
            "pan_deg": pan_deg,
            "tilt_deg": tilt_deg,
            "H": H,
            "points_cam1": pts1,
            "points_cam2": pts2,
        })

    def calibrate(
        self,
        base_index: int = 0,
        image_size_cam2: tuple[int, int] = (1920, 1080),
    ) -> AnalyticCalibrationResult:
        """
        追加済みのキャリブレーションデータから K₂ を推定する

        Parameters
        ----------
        base_index : int
            基準として使うキャリブレーションのインデックス（通常0）
        image_size_cam2 : tuple[int, int]
            RGBカメラの画像サイズ (width, height)

        Returns
        -------
        AnalyticCalibrationResult
        """
        if len(self._calibrations) < 2:
            raise ValueError(
                "最低2角度のキャリブレーションが必要です。"
                f"現在 {len(self._calibrations)} 角度。"
            )

        base = self._calibrations[base_index]
        H_base = base["H"]
        base_pan = base["pan_deg"]
        base_tilt = base["tilt_deg"]

        # K₂ を最適化で推定する
        # 初期値: 画像サイズから妥当な焦点距離を仮定
        w, h = image_size_cam2
        fx_init = max(w, h)  # 一般的な初期推定

        best_K2 = None
        best_error = float("inf")

        # 焦点距離を探索（粗い探索 → 細かい探索）
        for fx_coarse in np.linspace(fx_init * 0.3, fx_init * 2.0, 50):
            K2, error = self._optimize_K2(
                fx_coarse, w, h, H_base, base_pan, base_tilt
            )
            if error < best_error:
                best_error = error
                best_K2 = K2

        # 細かい探索
        fx_center = best_K2[0, 0]
        for fx_fine in np.linspace(fx_center * 0.9, fx_center * 1.1, 100):
            K2, error = self._optimize_K2(
                fx_fine, w, h, H_base, base_pan, base_tilt
            )
            if error < best_error:
                best_error = error
                best_K2 = K2

        self._result = AnalyticCalibrationResult(
            H_base=H_base,
            K2=best_K2,
            K2_inv=np.linalg.inv(best_K2),
            base_pan_deg=base_pan,
            base_tilt_deg=base_tilt,
            reprojection_error=best_error,
        )

        print(f"K₂ 推定完了:")
        print(f"  焦点距離: fx={best_K2[0,0]:.1f}, fy={best_K2[1,1]:.1f}")
        print(f"  主点:     cx={best_K2[0,2]:.1f}, cy={best_K2[1,2]:.1f}")
        print(f"  再投影誤差: {best_error:.2f} px")

        return self._result

    def _optimize_K2(
        self,
        fx: float,
        w: int,
        h: int,
        H_base: np.ndarray,
        base_pan: float,
        base_tilt: float,
    ) -> tuple[np.ndarray, float]:
        """
        指定した焦点距離での K₂ を構成し、再投影誤差を計算する
        """
        K2 = np.array([
            [fx, 0, w / 2.0],
            [0, fx, h / 2.0],
            [0, 0, 1],
        ])
        K2_inv = np.linalg.inv(K2)

        total_error = 0.0
        n_points = 0

        for cal in self._calibrations:
            if (
                abs(cal["pan_deg"] - base_pan) < 1e-6
                and abs(cal["tilt_deg"] - base_tilt) < 1e-6
            ):
                continue

            d_pan = cal["pan_deg"] - base_pan
            d_tilt = cal["tilt_deg"] - base_tilt

            # 解析的に Homography を計算
            R = _rotation_y(d_pan) @ _rotation_x(d_tilt)
            H_predicted = K2 @ R @ K2_inv @ H_base
            H_predicted /= H_predicted[2, 2]

            # 実測の Homography との比較（対応点で再投影誤差を計算）
            pts1 = cal["points_cam1"].reshape(-1, 1, 2).astype(np.float64)
            pts2_actual = cal["points_cam2"]

            pts2_predicted = cv2.perspectiveTransform(pts1, H_predicted).reshape(-1, 2)

            errors = np.linalg.norm(pts2_predicted - pts2_actual, axis=1)
            total_error += errors.sum()
            n_points += len(errors)

        avg_error = total_error / max(n_points, 1)
        return K2, avg_error

    def get_homography(self, pan_deg: float, tilt_deg: float) -> np.ndarray:
        """
        任意の雲台角度に対する Homography を解析的に算出する

        Parameters
        ----------
        pan_deg : float
            現在の雲台 pan 角度（度）
        tilt_deg : float
            現在の雲台 tilt 角度（度）

        Returns
        -------
        np.ndarray
            3x3 Homography 行列
        """
        if self._result is None:
            raise ValueError("先に calibrate() を実行してください。")

        r = self._result
        d_pan = pan_deg - r.base_pan_deg
        d_tilt = tilt_deg - r.base_tilt_deg

        # 角度差がゼロならベースをそのまま返す
        if abs(d_pan) < 1e-9 and abs(d_tilt) < 1e-9:
            return r.H_base.copy()

        # H(θ) = K₂ · R_y(Δpan) · R_x(Δtilt) · K₂⁻¹ · H_base
        R = _rotation_y(d_pan) @ _rotation_x(d_tilt)
        H = r.K2 @ R @ r.K2_inv @ r.H_base
        H /= H[2, 2]

        return H

    def transform_point(
        self,
        pan_deg: float,
        tilt_deg: float,
        point_cam1: np.ndarray,
    ) -> np.ndarray:
        """カメラ1のピクセル座標をカメラ2のピクセル座標に変換する"""
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
        """カメラ1上のBBoxをカメラ2上に変換する"""
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

    def save(self, path: str) -> None:
        """キャリブレーション結果を保存する"""
        if self._result is None:
            raise ValueError("先に calibrate() を実行してください。")

        r = self._result
        data = {
            "H_base": r.H_base.tolist(),
            "K2": r.K2.tolist(),
            "base_pan_deg": r.base_pan_deg,
            "base_tilt_deg": r.base_tilt_deg,
            "reprojection_error": r.reprojection_error,
            "calibrations": [
                {
                    "pan_deg": c["pan_deg"],
                    "tilt_deg": c["tilt_deg"],
                    "H": c["H"].tolist(),
                    "points_cam1": c["points_cam1"].tolist(),
                    "points_cam2": c["points_cam2"].tolist(),
                }
                for c in self._calibrations
            ],
        }
        Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False))

    @classmethod
    def load(cls, path: str) -> "AnalyticHomographyCalibrator":
        """保存したキャリブレーション結果を読み込む"""
        data = json.loads(Path(path).read_text())
        calibrator = cls()

        for c in data["calibrations"]:
            calibrator._calibrations.append({
                "pan_deg": c["pan_deg"],
                "tilt_deg": c["tilt_deg"],
                "H": np.array(c["H"]),
                "points_cam1": np.array(c["points_cam1"], dtype=np.float64),
                "points_cam2": np.array(c["points_cam2"], dtype=np.float64),
            })

        K2 = np.array(data["K2"])
        calibrator._result = AnalyticCalibrationResult(
            H_base=np.array(data["H_base"]),
            K2=K2,
            K2_inv=np.linalg.inv(K2),
            base_pan_deg=data["base_pan_deg"],
            base_tilt_deg=data["base_tilt_deg"],
            reprojection_error=data["reprojection_error"],
        )

        return calibrator
