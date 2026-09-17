#!/usr/bin/env python3
"""Reference-following color match (DWG workflow) — standalone Python port.

Faithful port of color-match-dwg.html algorithm (reference-following color
match in Lab, per tonal zone covariance transfer + luma quantile mapping),
used to generate a .cube 3D LUT that maps a source image look onto a
reference image look.

Modes:
  display : LUT maps display-space (sRGB/Rec.709-like) RGB in -> matched RGB out
            (equals the tool's web preview transform). Suitable for applying
            directly to a clip whose display appearance matches the source image.
  dwg     : LUT maps DaVinci Wide Gamut / DaVinci Intermediate encoded values
            in -> matched DWG/DI out (as the original tool's exported cube).

Usage:
  color_match.py --reference REF.png --source SRC.png --out-cube OUT.cube
                 [--size 33|65] [--mode display|dwg] [--preview PREV.png]
                 [--strength 0.82] [--luma-strength 0.7]
                 [--shadow-weight 0.82] [--mid-weight 1.0] [--highlight-weight 0.76]
                 [--no-skin-protect] [--no-sat-protect] [--no-contrast-protect]
"""
import argparse, json, math, struct, subprocess, sys

# ---------------------------------------------------------------- color science
def clamp(v, lo=0.0, hi=1.0):
    return lo if v < lo else (hi if v > hi else v)

def lerp(a, b, t):
    return a + (b - a) * t

def srgb_to_linear(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

def linear_to_srgb(c, quantize=False):
    c = clamp(c)
    v = c * 12.92 if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055
    v = clamp(v)
    return round(v * 255) if quantize else v

def srgb_to_xyz(r, g, b):
    r, g, b = srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b)
    return (r * 0.4124564 + g * 0.3575761 + b * 0.1804375,
            r * 0.2126729 + g * 0.7151522 + b * 0.0721750,
            r * 0.0193339 + g * 0.1191920 + b * 0.9503041)

def xyz_to_srgb(x, y, z, quantize=False):
    r = x * 3.2404542 + y * -1.5371385 + z * -0.4985314
    g = x * -0.9692660 + y * 1.8760108 + z * 0.0415560
    b = x * 0.0556434 + y * -0.2040259 + z * 1.0572252
    return (linear_to_srgb(r, quantize), linear_to_srgb(g, quantize),
            linear_to_srgb(b, quantize))

def xy_to_xyz(x, y):
    return (x / y, 1.0, (1 - x - y) / y)

def xyz_to_lab(x, y, z):
    white = (0.95047, 1.0, 1.08883)
    def f(t):
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116
    fx, fy, fz = f(x / white[0]), f(y / white[1]), f(z / white[2])
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))

def lab_to_xyz(l, a, b):
    white = (0.95047, 1.0, 1.08883)
    fy = (l + 16) / 116
    fx = a / 500 + fy
    fz = fy - b / 200
    def inv(t):
        t3 = t * t * t
        return t3 if t3 > 0.008856 else (t - 16 / 116) / 7.787
    return (inv(fx) * white[0], inv(fy) * white[1], inv(fz) * white[2])

def rgb_to_lab(r, g, b):
    return xyz_to_lab(*srgb_to_xyz(r, g, b))

def lab_to_rgb(l, a, b, quantize=False):
    return xyz_to_srgb(*lab_to_xyz(l, a, b), quantize)

def rgb_to_hsl(r, g, b):
    r, g, b = r / 255.0, g / 255.0, b / 255.0
    mx, mn = max(r, g, b), min(r, g, b)
    l = (mx + mn) / 2
    h = s = 0.0
    if mx != mn:
        d = mx - mn
        s = d / (2 - mx - mn) if l > 0.5 else d / (mx + mn)
        if mx == r:
            h = (g - b) / d + (6 if g < b else 0)
        elif mx == g:
            h = (b - r) / d + 2
        else:
            h = (r - g) / d + 4
        h /= 6
    return (h * 360, s, l)

def is_skin_like(r, g, b):
    h, s, l = rgb_to_hsl(r, g, b)
    return (8 <= h <= 52 and 0.12 <= s <= 0.72 and 0.18 <= l <= 0.84
            and r > b * 0.86)

# ------------------------------------------------------------------ matrix ops
def mat_mul(a, b):
    return [[sum(a[r][k] * b[k][c] for k in range(3)) for c in range(3)]
            for r in range(3)]

def transpose3(m):
    return [[m[r][c] for r in range(3)] for c in range(3)]

def diag3(v):
    return [[v[0], 0, 0], [0, v[1], 0], [0, 0, v[2]]]

def add_diagonal(m, amount):
    return [[m[r][c] + (amount if r == c else 0) for c in range(3)]
            for r in range(3)]

def eigen_symmetric3(inp):
    a = [row[:] for row in inp]
    v = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    for _ in range(18):
        p, q = 0, 1
        mx = abs(a[0][1])
        if abs(a[0][2]) > mx:
            p, q, mx = 0, 2, abs(a[0][2])
        if abs(a[1][2]) > mx:
            p, q, mx = 1, 2, abs(a[1][2])
        if mx < 1e-10:
            break
        theta = (a[q][q] - a[p][p]) / (2 * a[p][q])
        t = math.copysign(1.0, theta or 1) / (abs(theta) + math.sqrt(theta * theta + 1))
        c = 1 / math.sqrt(t * t + 1)
        s = t * c
        app, aqq, apq = a[p][p], a[q][q], a[p][q]
        a[p][p] = c * c * app - 2 * s * c * apq + s * s * aqq
        a[q][q] = s * s * app + 2 * s * c * apq + c * c * aqq
        a[p][q] = a[q][p] = 0
        for k in range(3):
            if k in (p, q):
                continue
            akp, akq = a[k][p], a[k][q]
            a[k][p] = a[p][k] = c * akp - s * akq
            a[k][q] = a[q][k] = s * akp + c * akq
        for k in range(3):
            vkp, vkq = v[k][p], v[k][q]
            v[k][p] = c * vkp - s * vkq
            v[k][q] = s * vkp + c * vkq
    return [a[0][0], a[1][1], a[2][2]], v

def symmetric_power(m, power):
    eig_v, eig_vec = eigen_symmetric3(add_diagonal(m, 1e-4))
    values = diag3([max(1e-4, x) ** power for x in eig_v])
    return mat_mul(mat_mul(eig_vec, values), transpose3(eig_vec))

def invert3(m):
    a, b, c = m[0]; d, e, f = m[1]; g, h, i = m[2]
    A = e * i - f * h; B = c * h - b * i; C = b * f - c * e
    D = f * g - d * i; E = a * i - c * g; F = c * d - a * f
    G = d * h - e * g; H = b * g - a * h; I = a * e - b * d
    det = a * A + b * D + c * G
    return [[A / det, B / det, C / det],
            [D / det, E / det, F / det],
            [G / det, H / det, I / det]]

def mul_mv(m, v):
    return [m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
            m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
            m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2]]

# ---------------------------------------------------------------- DWG (optional)
DWG_RGB_TO_XYZ = [[0.70062239, 0.14877482, 0.10105872],
                  [0.27411851, 0.87363190, -0.14775041],
                  [-0.09896291, -0.13789533, 1.32591599]]
DWG_XYZ_TO_RGB = [[1.51667204, -0.28147805, -0.14696363],
                  [-0.46491710, 1.25142378, 0.17488461],
                  [0.06484905, 0.10913934, 0.76141462]]
DI_A, DI_B, DI_C, DI_M = 0.0075, 7.0, 0.07329248, 10.44426855
DI_LIN_CUT, DI_LOG_CUT = 0.00262409, 0.02740668

def di_encode(linear):
    return linear * DI_M if linear <= DI_LIN_CUT else (math.log2(linear + DI_A) + DI_B) * DI_C

def di_decode(encoded):
    return encoded / DI_M if encoded <= DI_LOG_CUT else math.pow(2, encoded / DI_C - DI_B) - DI_A

# ------------------------------------------------------------------ statistics
def make_stats():
    return [{"count": 0, "sum": [0.0, 0.0, 0.0],
             "cross": [[0.0] * 3 for _ in range(3)], "lValues": []} for _ in range(3)]

def add_sample(stats, bin_, lab):
    s = stats[bin_]
    s["count"] += 1
    for i in range(3):
        s["sum"][i] += lab[i]
        for j in range(3):
            s["cross"][i][j] += lab[i] * lab[j]
    s["lValues"].append(lab[0])

def quantiles(values, count=33):
    if not values:
        return [i / (count - 1) * 100 for i in range(count)]
    sorted_v = sorted(values)
    out = []
    for i in range(count):
        p = (i / (count - 1)) * (len(sorted_v) - 1)
        lo = int(math.floor(p))
        hi = int(math.ceil(p))
        out.append(lerp(sorted_v[lo], sorted_v[hi], p - lo))
    return out

def finalize_stats(stats):
    out = []
    for s in stats:
        count = max(1, s["count"])
        mean = [v / count for v in s["sum"]]
        cov = [[0.0] * 3 for _ in range(3)]
        for i in range(3):
            for j in range(3):
                cov[i][j] = s["cross"][i][j] / count - mean[i] * mean[j]
        out.append({
            "count": s["count"], "mean": mean, "cov": cov,
            "std": [math.sqrt(max(1e-4, cov[0][0])),
                    math.sqrt(max(1e-4, cov[1][1])),
                    math.sqrt(max(1e-4, cov[2][2]))],
            "q": quantiles(s["lValues"]),
        })
    return out

def analyze_image(rgb, stride=6):
    stats = make_stats()
    all_l = []
    n = len(rgb) // 3
    for i in range(0, n * 3, 3 * stride):
        r, g, b = rgb[i], rgb[i + 1], rgb[i + 2]
        lab = rgb_to_lab(r, g, b)
        bin_ = 0 if lab[0] < 34 else (2 if lab[0] > 68 else 1)
        add_sample(stats, bin_, lab)
        all_l.append(lab[0])
    return {"zones": finalize_stats(stats), "lumaQ": quantiles(all_l, 65)}

def build_model(ref_data, src_data):
    ref_a = analyze_image(ref_data)
    src_a = analyze_image(src_data)
    reference = ref_a["zones"]
    source = src_a["zones"]
    ref_fb = max(reference, key=lambda x: x["count"])
    src_fb = max(source, key=lambda x: x["count"])
    reference = [x if x["count"] >= 5 else ref_fb for x in reference]
    source = [x if x["count"] >= 5 else src_fb for x in source]
    transforms = []
    for i in range(3):
        ref_sqrt = symmetric_power(reference[i]["cov"], 0.5)
        src_inv_sqrt = symmetric_power(source[i]["cov"], -0.5)
        transforms.append(mat_mul(ref_sqrt, src_inv_sqrt))
    return {"transforms": transforms, "reference": reference, "source": source,
            "luma": {"source": src_a["lumaQ"], "reference": ref_a["lumaQ"]}}

def map_quantile(value, src_q, ref_q):
    if not src_q or not ref_q or len(src_q) < 2:
        return value
    if value <= src_q[0]:
        return ref_q[0] + (value - src_q[0])
    last = len(src_q) - 1
    if value >= src_q[last]:
        return ref_q[last] + (value - src_q[last])
    for i in range(last):
        if src_q[i] <= value <= src_q[i + 1]:
            span = max(1e-4, src_q[i + 1] - src_q[i])
            t = (value - src_q[i]) / span
            return lerp(ref_q[i], ref_q[i + 1], t)
    return value

def tonal_weights(l):
    x = l / 100.0
    shadow = clamp((0.55 - x) / 0.45)
    highlight = clamp((x - 0.45) / 0.45)
    mid = clamp(1 - abs(x - 0.5) / 0.42)
    total = shadow + mid + highlight or 1.0
    return (shadow / total, mid / total, highlight / total)

def tone_curve_luma(l, model, opts):
    global_target = map_quantile(l, model["luma"]["source"], model["luma"]["reference"])
    zone = 0 if l < 34 else (2 if l > 68 else 1)
    zone_target = map_quantile(l, model["source"][zone]["q"], model["reference"][zone]["q"])
    target = lerp(global_target, zone_target, 0.18)
    if opts["contrast_protect"]:
        max_delta = 8 if (l < 5 or l > 98) else 18 if (l < 15 or l > 90) else 22 if (l < 28 or l > 78) else 26
        target = l + clamp(target - l, -max_delta, max_delta)
    else:
        target = l + clamp(target - l, -38, 38)
    return clamp(target, 0, 100)

def transform_lab(lab, model, opts, protect_rgb=None, l_max=100.0):
    tw = tonal_weights(lab[0])
    target = [tone_curve_luma(lab[0], model, opts), 0.0, 0.0]
    weight_sum = 0.0
    for bin_ in range(3):
        amount = tw[bin_] * opts["weights"][bin_]
        if amount <= 0:
            continue
        src, ref = model["source"][bin_], model["reference"][bin_]
        centered = [0.0, lab[1] - src["mean"][1], lab[2] - src["mean"][2]]
        colored = mul_mv(model["transforms"][bin_], centered)
        cov_target = [ref["mean"][0] + colored[0], ref["mean"][1] + colored[1],
                      ref["mean"][2] + colored[2]]
        channel_target = [0.0, 0.0, 0.0]
        for c in (1, 2):
            ratio = ref["std"][c] / max(1.0, src["std"][c])
            safe_ratio = clamp(ratio, 0.62, 1.48)
            channel_target[c] = ref["mean"][c] + (lab[c] - src["mean"][c]) * safe_ratio
        cov_target[1] = lerp(channel_target[1], cov_target[1], 0.58)
        cov_target[2] = lerp(channel_target[2], cov_target[2], 0.58)
        target[1] += cov_target[1] * amount
        target[2] += cov_target[2] * amount
        weight_sum += amount
    if weight_sum > 0:
        target[1] /= weight_sum
        target[2] /= weight_sum
    else:
        target[1], target[2] = lab[1], lab[2]

    local_strength = opts["strength"]
    if opts["skin_protect"] and protect_rgb and is_skin_like(*protect_rgb):
        local_strength *= 0.38
    mixed = [lerp(lab[0], target[0], opts["luma_strength"]),
             lerp(lab[1], target[1], local_strength),
             lerp(lab[2], target[2], local_strength)]
    if opts["sat_protect"]:
        chroma0 = math.hypot(lab[1], lab[2])
        chroma1 = math.hypot(mixed[1], mixed[2])
        max_chroma = chroma0 * 1.38 + 9
        if chroma1 > max_chroma:
            k = max_chroma / chroma1
            mixed[1] *= k
            mixed[2] *= k
    return (clamp(mixed[0], 0, l_max), clamp(mixed[1], -128, 128),
            clamp(mixed[2], -128, 128))

def transform_rgb(r, g, b, model, opts):
    lab = rgb_to_lab(r, g, b)
    mixed = transform_lab(lab, model, opts, (r, g, b))
    out = lab_to_rgb(mixed[0], mixed[1], mixed[2])
    return (clamp(out[0] * 255, 0, 255), clamp(out[1] * 255, 0, 255),
            clamp(out[2] * 255, 0, 255))

def transform_dwg(r, g, b, model, opts):
    linear_dwg = (di_decode(r), di_decode(g), di_decode(b))
    xyz = mul_mv(DWG_RGB_TO_XYZ, list(linear_dwg))
    lab = xyz_to_lab(*xyz)
    protect = xyz_to_srgb(*xyz)
    mixed = transform_lab(lab, model, opts, protect, 220)
    out_xyz = lab_to_xyz(*mixed)
    out_dwg = mul_mv(DWG_XYZ_TO_RGB, list(out_xyz))
    return [clamp(di_encode(max(0.0, v))) for v in out_dwg]

# ------------------------------------------------------------------ image io
def load_rgb(path, max_side=1280):
    """Decode image via ffmpeg to raw rgb24 (PPM), downscaled to max_side."""
    probe = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", path,
                            "-vf", "scale=%d:-2" % max_side, "-frames:v", "1",
                            "-f", "image2", "-pix_fmt", "rgb24", "/tmp/_cm_ppm.ppm"],
                           capture_output=True)
    if probe.returncode != 0:
        raise RuntimeError(f"ffmpeg decode failed: {probe.stderr.decode(errors='replace')}")
    with open("/tmp/_cm_ppm.ppm", "rb") as f:
        head = f.readline().strip()          # P6
        dims = f.readline().strip()          # W H
        f.readline()                          # maxval
        data = f.read()
    w, h = (int(x) for x in dims.split())
    return list(data), w, h

def write_png(rgb_bytes, w, h, path):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo",
                    "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-i", "-", path],
                   input=rgb_bytes, check=True)

# ------------------------------------------------------------------ main
def build_opts(args):
    return {
        "strength": args.strength,
        "luma_strength": args.luma_strength,
        "weights": [args.shadow_weight, args.mid_weight, args.highlight_weight],
        "skin_protect": not args.no_skin_protect,
        "sat_protect": not args.no_sat_protect,
        "contrast_protect": not args.no_contrast_protect,
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reference", required=True)
    ap.add_argument("--source", required=True)
    ap.add_argument("--out-cube", required=True)
    ap.add_argument("--size", type=int, default=33, choices=[17, 33, 65])
    ap.add_argument("--mode", default="display", choices=["display", "dwg"])
    ap.add_argument("--preview", default=None)
    ap.add_argument("--strength", type=float, default=0.82)
    ap.add_argument("--luma-strength", type=float, default=0.7)
    ap.add_argument("--shadow-weight", type=float, default=0.82)
    ap.add_argument("--mid-weight", type=float, default=1.0)
    ap.add_argument("--highlight-weight", type=float, default=0.76)
    ap.add_argument("--no-skin-protect", action="store_true")
    ap.add_argument("--no-sat-protect", action="store_true")
    ap.add_argument("--no-contrast-protect", action="store_true")
    args = ap.parse_args()

    ref_rgb, rw, rh = load_rgb(args.reference)
    src_rgb, sw, sh = load_rgb(args.source)
    opts = build_opts(args)
    model = build_model(ref_rgb, src_rgb)

    size = args.size
    transform = transform_dwg if args.mode == "dwg" else transform_rgb
    if args.mode == "dwg":
        header = [
            'TITLE "Color Match DWG-DWG"',
            "# 输入: DaVinci Wide Gamut / DaVinci Intermediate 编码值",
            "# 输出: DaVinci Wide Gamut / DaVinci Intermediate 编码值",
            "# LUT 内部: DI 解码 -> 线性 DWG -> Lab 匹配 -> 线性 DWG -> DI 编码",
        ]
    else:
        header = [
            'TITLE "Color Match Rec709-display"',
            "# 输入: Rec.709/sRGB 显示 RGB (0-1)",
            "# 输出: 匹配参考后的显示 RGB (0-1)",
            "# 算法: 参考图色彩匹配 (Lab 分区协方差迁移 + 亮度分位数映射)",
        ]
    lines = header + [f"LUT_3D_SIZE {size}",
                      "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0"]
    for b in range(size):
        for g in range(size):
            for r in range(size):
                v = r / (size - 1)
                if args.mode == "dwg":
                    out = transform(v, g / (size - 1), b / (size - 1), model, opts)
                else:
                    out = transform_rgb(v * 255, (g / (size - 1)) * 255,
                                        (b / (size - 1)) * 255, model, opts)
                lines.append(f"{clamp(out[0] / 255 if args.mode != 'dwg' else out[0]):.6f} "
                             f"{clamp(out[1] / 255 if args.mode != 'dwg' else out[1]):.6f} "
                             f"{clamp(out[2] / 255 if args.mode != 'dwg' else out[2]):.6f}")
    with open(args.out_cube, "w") as f:
        f.write("\n".join(lines) + "\n")

    if args.preview:
        # apply to the downscaled source and write matched preview
        n = len(src_rgb) // 3
        out = bytearray(n * 3)
        for i in range(n):
            r, g, b = transform_rgb(src_rgb[i * 3], src_rgb[i * 3 + 1],
                                    src_rgb[i * 3 + 2], model, opts)
            out[i * 3] = int(round(clamp(r, 0, 255)))
            out[i * 3 + 1] = int(round(clamp(g, 0, 255)))
            out[i * 3 + 2] = int(round(clamp(b, 0, 255)))
        write_png(bytes(out), sw, sh, args.preview)

    print(json.dumps({"ok": True, "cube": args.out_cube, "size": size,
                      "mode": args.mode, "model": {
                          "ref_zones": [z["count"] for z in model["reference"]],
                          "src_zones": [z["count"] for z in model["source"]],
                      }}, ensure_ascii=False))

if __name__ == "__main__":
    main()
