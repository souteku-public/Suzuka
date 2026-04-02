# Suzuka プロジェクト

## プロジェクト概要

野球場のライブ映像制作向けデュアルカメラキャリブレーション＆映像解析システム。
TouchDesigner を中心としたリアルタイム映像パイプラインで使用される。

**リポジトリ構成:**
```
Suzuka/
├── dual_camera_calibration/       # コア Python ライブラリ
│   ├── calibrator.py              # ステレオカメラキャリブレーション
│   ├── homography_calibrator.py   # Pan/Tilt 雲台 Homography（IDW補間）
│   ├── analytic_calibrator.py     # 解析的 Homography 推定（高精度）
│   ├── movement_detector.py       # カメラ移動検出
│   ├── dmp_parser.py              # Windows ミニダンプ(.dmp) パーサー
│   └── __init__.py
├── docs/
│   ├── theory.md                  # 解析的 Homography 推定の数学的理論
│   └── index.html                 # GitHub Pages 用ブラウザ版 DMP 解析ツール
├── example.py                     # デュアルカメラキャリブレーション例
├── example_accuracy_comparison.py # IDW vs 解析的手法の精度比較
├── example_dmp_parser.py          # DMP パーサー使用例
├── example_pantilt.py             # Pan/Tilt 雲台キャリブレーション例
├── minidump_analyzer.html         # ブラウザ版ミニダンプ解析ツール（スタンドアロン）
└── requirements.txt               # numpy, opencv-python, opencv-contrib-python
```

---

## デュアルカメラシステム

### 物理構成
- **cam1（認識カメラ）**: 物体検出用。雲台に載って pan/tilt 回転する
- **cam2（RGBカメラ）**: 映像出力用。三脚に固定
- cam1 で検出した座標を cam2 の画像座標にマッピングする

### キャリブレーション手法
1. **IDW 補間** (`HomographyCalibrator`): 複数角度の Homography を逆距離加重で補間。誤差 10-200px
2. **解析的手法** (`AnalyticHomographyCalibrator`): 回転行列理論に基づく。誤差 0.01-0.08px
   - `H(pan, tilt) = K₂ · R_y(Δpan) · R_x(Δtilt) · K₂⁻¹ · H_base`
   - カメラ内部パラメータ K₂ を複数キャリブレーションから推定
   - IDW より桁違いに高精度

---

## Minidump Analyzer（DMP 解析ツール）

### 開発経緯
TouchDesigner が本番中にクラッシュ/フリーズする問題の原因特定のために開発。
WinDbg なしでブラウザ上で .dmp ファイルを解析できるツール。

### 対応フォーマット
- **MDMP** (Microsoft Minidump): 標準的なミニダンプ
- **PAGEDUMP** (32-bit Full Dump): フルクラッシュダンプ
- **PAGEDU64** (64-bit Full Dump): 64bit フルクラッシュダンプ
- 10GB 超の大容量ダンプも段階的読み込みで対応

### 機能
- ヘッダー情報、システム情報、例外詳細の解析
- BugCheck/BSOD 情報（カーネルダンプ用）
- モジュール一覧（ベースアドレス、サイズ付き）
- スレッド情報
- TouchDesigner 関連モジュールの自動検出
- JSON エクスポート

### デプロイ
- GitHub Pages: `docs/index.html` として公開
- スタンドアロン HTML (`minidump_analyzer.html`) でもローカル使用可能

---

## TouchDesigner クラッシュ分析結果

### 解析した DMP ファイル
TouchDesigner 実行中のクラッシュダンプを解析し、以下を特定:

### 主な発見
- **クラッシュモジュール**: TouchDesigner 関連の DLL やドライバーが原因
- **例外タイプ**: ACCESS_VIOLATION、STACK_OVERFLOW 等
- **Alienware m18 R2 固有の問題**:
  - GPU ドライバー（NVIDIA）との相互作用
  - 高負荷時のメモリアクセス違反
  - サーマルスロットリングによるタイミング問題の可能性

### フリーズ原因の分析
- GPU リソースの競合（NDI 送受信 + TouchDesigner レンダリング同時実行）
- ドライバーレベルでのデッドロック
- メモリ断片化による長時間運用での不安定化

---

## NDI カクつき問題

### 問題概要
NDI（Network Device Interface）を使った映像伝送で、映像がカクつく（フレーム落ち）問題が発生。

### システム構成
野球場のプロダクション環境で、複数カメラの NDI ストリームを集約・スイッチングする構成。

### 原因分析
- **ネットワーク帯域**: NDI は 1 ストリームあたり約 120-150Mbps（1080p60）。複数ストリーム同時伝送で帯域逼迫
- **スイッチ性能**: ギガビットスイッチのバックプレーン帯域が複数 NDI ストリームに不足
- **PC 処理能力**: デコード/エンコード処理の CPU/GPU 負荷
- **ネットワーク設定**: ジャンボフレーム、QoS 未設定による遅延

### 対策
- 10GbE ネットワークへのアップグレード
- NDI|HX（低帯域版）の使用検討
- ネットワークスイッチの QoS 設定
- 専用 VLAN によるトラフィック分離
- マルチキャスト設定の最適化

---

## 野球場プロダクションシステム構成

### カメラ配置
- 認識カメラ（cam1）: 雲台搭載、物体追従用
- RGB カメラ（cam2）: 固定、高品質映像出力用
- 複数カメラによるマルチアングル撮影

### PC 構成
- **メインPC**: Alienware m18 R2（TouchDesigner 実行）
  - NVIDIA GPU（リアルタイムレンダリング）
  - 高負荷時の熱問題に注意
- NDI 送受信用 PC

### ネットワーク構成
- NDI ストリーム伝送用ネットワーク
- ギガビット/10GbE スイッチ
- カメラ - PC - スイッチャー間の配線

---

## 開発メモ

### ブランチ構成
- `master`: メインブランチ
- `claude/analyze-touchdesigner-dmp-*`: TouchDesigner DMP 解析関連の開発ブランチ
- `claude/dual-camera-calibration-*`: デュアルカメラキャリブレーション開発ブランチ

### 技術スタック
- **Python**: numpy, OpenCV（カメラキャリブレーション）
- **HTML/JavaScript**: ブラウザ版 DMP 解析ツール（サーバー不要）
- **TouchDesigner**: リアルタイム映像制作環境
- **NDI**: IP ベース映像伝送プロトコル

### コーディング規約
- ドキュメント・コメントは日本語
- 数学的導出は docs/theory.md に詳細記載
- JSON ベースのキャリブレーションデータ保存
