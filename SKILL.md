---
name: reference-color-match
description: 参考图仿色工具（Lab 分区色彩匹配，DWG 工作流）。当用户要求"按某张参考图/电影截图/剧照给当前画面、镜头或素材仿色、调色匹配、色彩匹配、生成调色 LUT、把某风格套到素材上"，或提供参考图+素材图片要求生成 .cube 3D LUT 时使用。输入为参考图与源画面两张图片，输出为 33/65 点 .cube LUT（display 或 DWG/DI 两种输入空间）与匹配预览图。执行规则：默认必须通过达芬奇 MCP 将 LUT 直接套入达芬奇当前镜头；仅当用户明确表示不连接/不安装达芬奇 MCP 时，才改为交付仿色后画面结果与 LUT 文件。
---

# 参考图仿色 → .cube LUT

把"源画面"的色彩分布匹配到"参考图"（如电影截图、风格剧照），输出可直接套用的 3D LUT。

## 执行规则（分享版默认行为，优先于其他章节）

1. **默认走达芬奇 MCP 并直接衔接进镜头**：本技能默认通过达芬奇 MCP 执行，完整链路为：导出达芬奇当前帧 → 生成 LUT → `RefreshLUTList` → `SetLUT` 套入当前镜头 → 导出应用后帧验收。不得擅自跳过达芬奇衔接、只交付文件。
2. **未连接 MCP 时先提醒**：执行前先检查达芬奇 MCP 是否可用（如 `get_resolve_status`、桥接 `/health` 等）。不可用时，主动提醒用户"请连接/启用达芬奇 MCP"（说明需要打开达芬奇并启动 MCP 桥接），等待用户完成连接后再继续；不得静默降级为纯文件交付。
3. **降级交付的唯一条件**：仅当用户明确表态不连接达芬奇 MCP（如"不安装 MCP""不用达芬奇""只要图片和 LUT 文件"）时，才跳过达芬奇衔接，交付仿色后的画面结果（匹配预览图/对比图）与 .cube LUT 文件。除此之外一律默认衔接进达芬奇镜头。

## 依赖

- `python3`（标准库，无需 numpy/PIL）
- `ffmpeg`（图片解码、LUT 应用验证、波形/矢量图生成）

## 快速开始

```bash
python3 scripts/color_match.py \
  --reference 参考图.png --source 源画面.png \
  --out-cube out.cube --size 33 --mode display \
  --preview matched_preview.png
```

默认参数（已按用户偏好调整）：`--strength 0.82 --luma-strength 0.7 --shadow-weight 0.82 --mid-weight 1.0 --highlight-weight 0.76`，皮肤/饱和/对比保护全开（用 `--no-skin-protect` 等关闭）。其中**亮度匹配权重默认 70%**（原工具出厂 100%）。

## 模式选择

| 模式 | 输入/输出空间 | 适用 |
|---|---|---|
| `display`（默认） | Rec.709/sRGB 显示值 | 直接套在节点上，等于工具网页预览的变换 |
| `dwg` | DaVinci Wide Gamut / DaVinci Intermediate 编码值 | DWG 调色工作流，LUT 放在 CST/DRT 输出转换之前 |

`--size` 支持 17/33/65（33 与工具默认一致；33 生成约 8 秒）。

## 标准工作流

1. **准备输入**：参考图 = 想要的目标风格；源图 = 当前画面的干净帧（达芬奇导出 PNG，需先复位节点避免残留调色）。
2. **生成 LUT 与预览**：按上面命令执行，同时得到 `.cube` 与匹配预览图。
3. **数值验收**：用 `scripts/imgstats.py` 对 参考图 / 源图 / 匹配结果 三张图输出 mean、低中高三区、饱和度，确认方向正确（亮度、色偏、饱和向参考靠拢）。
4. **LUT 忠实度验证**：用 ffmpeg 独立应用一遍，结果应接近算法预览（33 点误差 <1）：

   ```bash
   ffmpeg -i 源图.png -vf "lut3d=out.cube" -frames:v 1 lut_check.png
   ```
5. **视像验收**：生成三组波形/矢量图对比：
   ```bash
   ffmpeg -i img.png -vf "waveform=mode=column:filter=lowpass" wave.png
   ffmpeg -i img.png -vf "vectorscope=mode=color" vec.png
   ```
6. **套入达芬奇（默认必经步骤，需达芬奇 MCP；降级条件见"执行规则"）**：
   - 先确认达芬奇 MCP 可用（`get_resolve_status` 或桥接 `/health`）；不可用则按"执行规则"第 2 条提醒用户连接。
   - 拷贝 LUT 到达芬奇 LUT 目录（如 `/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT/MCP/`）
   - `project.RefreshLUTList()` → 当前片段节点 `graph.SetLUT(1, "MCP/文件名.cube")`；**SetLUT 路径必须与 `list_luts` 返回的相对路径一致（带子目录前缀）**，否则 SetLUT 返回 false 且不生效。
   - **导出时序**：节点改动后 sleep ≈2s，移动播放头后 sleep ≥1.5s，再 `ExportCurrentFrameAsStill`，否则拿到旧帧（SetCDL 语义与此工具无关，勿混用）。
   - 导出应用后帧，用 `scripts/imgstats.py` 与算法预览对比验收（应与预览数值一致）。

## 算法（移植自 color-match-dwg.html）

- sRGB→线性→XYZ(D65)→Lab(D50)
- 按 L\* 分三影调区（<34 / 34–68 / >68）统计均值、协方差、L\* 分位数；样本不足（<5）的区回退到样本最多的区
- 每区变换矩阵 = `refCov^0.5 · srcCov^-0.5`（Jacobi 特征分解求矩阵幂）
- 亮度：全局 65 分位 + 区内 33 分位映射按 0.18 混合，delta 限幅（对比保护开启时分 8/18/22/26 级）
- 色度：协方差迁移与通道 std 比例（限 0.62–1.48）按 0.58 混合，再按三区权重加权
- 保护：皮肤（HSL h8–52, s0.12–0.72, l0.18–0.84, r>b×0.86 → 强度×0.38）、饱和上限（1.38×+9）、对比限幅

## 注意事项

- 源图必须与达芬奇画面同帧（同时间码导出），否则匹配基准失真。
- `display` 模式是显示空间映射：素材若为 LOG 且未还原，先套还原 LUT 再做仿色，或用 `dwg` 模式匹配 DWG 工作流。
- 强度默认 0.82 + 饱和保护，结果不会完全等于参考图饱和度（防止溢出）；需要更接近可调高 `--strength`、关闭 `--no-sat-protect`。
- 波形/矢量图仅用于目视验收，量化验收以 imgstats 数值为准。
- 达芬奇 MCP 接入方式不限（官方 ResolveMCP、本地桥接等），执行规则一致：未连接先提醒，用户明确拒绝后才降级交付画面结果与 LUT 文件。
- SetLUT 失败时先查 `list_luts` 确认实际相对路径（如 `MCP/xxx.cube`），再重试；不要反复用错误路径。
