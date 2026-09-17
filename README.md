# Reference Color Match 参考图仿色

把「参考图/电影截图/剧照」的色彩风格匹配到你的素材上，生成可直接套用的 3D LUT（.cube），并默认通过达芬奇 MCP 直接套入当前镜头，免费使用。

## 安装（完全免费）

在豆包中向豆包说下面这句话即可安装本技能：

```
安装技能 reference-color-match，仓库地址 https://github.com/rick8393/reference-color-match
```

## 使用

安装后，给豆包一张参考图（电影截图、剧照等），并对豆包说：

> 按这张参考图，给我的达芬奇当前画面仿色

豆包会默认通过达芬奇 MCP 把生成的 LUT 直接套进你当前镜头并验收；未连接达芬奇 MCP 时会先提醒你连接；只有你明确表示不连接 MCP 时，才会改为交付仿色结果图和 LUT 文件。

## 文件结构

| 文件 | 说明 |
|---|---|
| `SKILL.md` | 技能定义、执行规则与工作流 |
| `scripts/color_match.py` | 色彩匹配算法：生成 .cube 3D LUT 与匹配预览图 |
| `scripts/imgstats.py` | 图像色彩统计，用于数值验收 |

## 环境要求

- `python3` 与 `ffmpeg`
- 达芬奇（DaVinci Resolve）+ 达芬奇 MCP 桥接（默认执行方式）
