# 軌跡描画パイプライン

## 全体フロー

```
イベントカメラ → 軌跡点検出 → 座標変換 → 角度フィルタ → 軌跡接続
→ 線形補間 → 移動平均平滑化 → 再補間 → 描画 → Spout出力
```

---

## Step 1: 軌跡点の検出

**参照**: `locus_window.py:368-380`

`e_finder.handle(e_frame)` がイベントカメラの二値化フレームから軌跡点を抽出し、結果を2つに分離して返す。

| 出力 | 内容 |
|------|------|
| `front_locus` | 上昇部分の点列 |
| `backs_locus` | 下降部分の点列 |

---

## Step 2: 座標変換 — `trans_locus`

**参照**: `locus_window.py:538-545`

イベントカメラ座標系からRGBカメラ座標系へ変換する。イベントカメラとRGBカメラは解像度・位置が異なるため、スケーリングとオフセットで座標を合わせる。

各点 `(x_event, y_event)` に対して：

```
x_rgb = x_event × resize_rate + trans_range[0]
y_rgb = y_event × resize_rate + trans_range[1]
```

| パラメータ | 意味 |
|-----------|------|
| `resize_rate` | イベントカメラ → RGBカメラの解像度比（スケール係数） |
| `trans_range[0]` | RGBフレーム上のX方向オフセット（左上原点） |
| `trans_range[1]` | RGBフレーム上のY方向オフセット |

---

## Step 3: 角度フィルタ — `calc_angle`

**参照**: `locus_window.py:386-394`, `points_proc_utils.py:12-26`

ショットポイントから軌跡が正しい方向に飛んでいるかを角度で判定する。閾値未満であれば誤検出として棄却しリセット。

`calc_angle(p1, p2, p3)` は `p2` を頂点として2ベクトル `p1→p2`, `p3→p2` が成す角度（度）を返す。

```
v₁ = p1 - p2
v₂ = p3 - p2

cos θ = (v₁ · v₂) / (|v₁| × |v₂|)

angle = arccos(cos θ) × 180 / π
```

**判定ロジック**:

```python
angle_from_shot = calc_angle(shot_point, locus[0], locus[-1])
angle_from_find = calc_angle(finder.locus[-1], locus[0], locus[-1])

if max(angle_from_shot, angle_from_find) < threshold:
    # → 棄却・リセット
```

---

## Step 4: 軌跡の接続

**参照**: `locus_window.py:405-413`

RGBカメラ側の finder 軌跡（`first_front`）を先頭に結合し、前面の最後の点を背面の先頭に接続することで途切れのない1本の軌跡にする。

```python
rgb_front_locus = first_front + rgb_front_locus
rgb_backs_locus = rgb_front_locus[-1:] + rgb_backs_locus

# 全座標を整数化
rgb_front_locus = [(int(x), int(y)) for each point]
rgb_backs_locus = [(int(x), int(y)) for each point]
```

---

## Step 5: 線形補間（稠密化） — `correct_line`（1回目）

**参照**: `locus_window.py:416`, `points_proc_utils.py:68-91`

隣接する2点間の距離が1pxを超える場合に中間点を挿入し、全点が1px以内で連続する稠密な点列にする。次の移動平均を正しく機能させるための前処理。

2点 `P₁(x₁, y₁)` と `P₂(x₂, y₂)` に対して：

```
dx = x₂ - x₁
dy = y₂ - y₁
N  = max(|dx|, |dy|)

if N > 1:
    for k = 1 to N-1:
        t = k / N
        insert(int(dx × t + x₁), int(dy × t + y₁))
```

**例**: `P₁(10, 20)` → `P₂(14, 22)` の場合

```
N = max(4, 2) = 4
k=1: (11, 20)
k=2: (12, 21)
k=3: (13, 21)

→ 最終列: (10,20), (11,20), (12,21), (13,21), (14,22)
```

---

## Step 6: 移動平均による平滑化 — `correct_num_list`

**参照**: `locus_window.py:417-421`, `points_proc_utils.py:43-54`

X座標列・Y座標列をそれぞれ独立に移動平均フィルタで平滑化する。検出ノイズによるギザギザを取り除き、なめらかな曲線にする。**前面軌跡のみに適用**。

ウィンドウ幅 `W = neighbor_count × 2 + 1`（奇数に正規化）、中心位置 `i = ⌊W/2⌋ + 1` として：

```
for n = 0, 1, 2, ...:
    list に nums[n] を追加
    if listの長さ ≥ W:
        list[n - i] = (1/W) × Σ_{k=0}^{W-1} list[n - k]
```

**呼び出し例**:

```python
xs = [x for x, y in rgb_front_locus]
ys = [y for x, y in rgb_front_locus]

xs_smooth = correct_num_list(xs, ave_num=neighbor_count*2+1)
ys_smooth = correct_num_list(ys, ave_num=neighbor_count*2+1)

rgb_front_locus = [(x, int(y)) for x, y in zip(xs_smooth, ys_smooth)]
```

**例**: `W=5`, `i=3` のとき、`list[7]` を処理する際：

```
list[7-3] = list[4] = (list[3] + list[4] + list[5] + list[6] + list[7]) / 5
```

---

## Step 7: 再補間（稠密化） — `correct_line`（2回目）

**参照**: `locus_window.py:423-424`

Step 6 の移動平均で座標が小数→整数丸めされた結果、再び隣接点間が1pxを超える箇所が生じ得る。Step 5 と同じ `correct_line` を再適用し、描画用に点密度を1px以内に戻す。

```python
rgb_front_locus = correct_line(rgb_front_locus)   # 前面
rgb_backs_locus = correct_line(rgb_backs_locus)    # 背面
```

> 背面はStep 6の移動平均をかけていないが、Step 4 の接続で座標間隔が開いている可能性があるため、同様に補間する。

---

## Step 8: 描画 — `LocusDrawer.draw_locus_front` / `draw_locus_back`

**参照**: `locus_drawer.py:26-47`

各点 `(x, y)` に対して水平線を描画する。`last_size`（半幅）が点ごとに徐々に減少することで、軌跡の始点は太く末端は細いテーパー（先細り）効果を実現する。

**各点の描画**:

```python
line_start = (x - last_size, y)
line_end   = (x + last_size, y)

cv2.line(canvas, line_start, line_end, color, thickness=1)

last_size = max(last_size - decay, min_size)
```

各点で描かれる水平線の全幅は `2 × last_size` px。

| パラメータ | 前面 (front) | 背面 (back) |
|-----------|-------------|------------|
| 初期半幅 `max_size` | 10 px | front描画後の値を継続 |
| 最小半幅 `min_size` | 5 px | 5 px |
| 減衰率 `decay` | 0.001 px/点 | 0.0005 px/点 |
| リセット | 毎フレームreset | resetなし |

> 前面の減衰は背面の2倍速いため、前面が速く細くなり背面は長く太さを維持する。

---

## Step 9: Spout出力 — `SpoutSender.send`

**参照**: `locus_drawer.py:53-55`

描画した canvas (1920x1080) を SpoutGL 経由で外部アプリケーション（TouchDesigner等）にテクスチャとして送信する。

```
BGR → RGB → RGBA 変換
sendImage(rgba_bytes, 1920, 1080)
```

> 前面と背面は別々の Spout チャンネルとして送信され、受信側で合成される。

---

## まとめ図

```
[イベントカメラ二値化フレーム]
        │
        ▼ Step1: 軌跡点検出
        │         → front_locus, backs_locus
        │
        ▼ Step2: trans_locus — 座標変換
        │         x' = x × rate + offset_x
        │         y' = y × rate + offset_y
        │
        ▼ Step3: calc_angle — 角度フィルタ
        │         cos θ = (v₁·v₂) / (|v₁||v₂|)
        │         angle < threshold → 棄却
        │
        ▼ Step4: 軌跡接続
        │         front = finder軌跡 + front
        │         back  = front末尾 + back
        │
        ▼ Step5: correct_line — 線形補間 (1回目)
        │         隣接 > 1px → 中間点挿入
        │
        ▼ Step6: correct_num_list — 移動平均 (前面のみ)
        │         x̄[n-i] = (1/W) Σ x[n-k]   (X,Y各軸)
        │
        ▼ Step7: correct_line — 線形補間 (2回目)
        │         平滑化後の隙間を再稠密化
        │
        ▼ Step8: draw_locus — 水平線描画 + テーパー
        │         幅 = 2 × max(last_size - decay×k, min_size)
        │
        ▼ Step9: Spout送信 (BGR→RGBA, 1920×1080)
```
