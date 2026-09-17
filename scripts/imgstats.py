#!/usr/bin/env python3
"""Compute display-space color stats for an image via ffmpeg raw RGB dump.
Outputs JSON: mean RGB, region RGB means (low/mid/high by luma), saturation.
"""
import json, struct, subprocess, sys

def load_rgb(path, width=256):
    cmd = ["ffmpeg", "-v", "error", "-i", path,
           "-vf", f"scale={width}:-2", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0 or not p.stdout:
        raise RuntimeError(f"ffmpeg failed on {path}: {p.stderr.decode(errors='replace')[:300]}")
    data = p.stdout
    n = len(data) // 3
    px = struct.unpack(f"{n*3}B", data)
    r = list(px[0::3]); g = list(px[1::3]); b = list(px[2::3])
    return r, g, b

def stats(path, width=256):
    r, g, b = load_rgb(path, width)
    n = len(r)
    luma = [int(0.299 * r[i] + 0.587 * g[i] + 0.114 * b[i]) for i in range(n)]
    mr, mg, mb = sum(r) / n, sum(g) / n, sum(b) / n
    sat = sum(max(r[i], g[i], b[i]) - min(r[i], g[i], b[i]) for i in range(n)) / n
    order = sorted(range(n), key=lambda i: luma[i])

    def region(fr, to):
        seg = order[int(n * fr):int(n * to)]
        if not seg:
            return [0.0, 0.0, 0.0]
        rr = sum(r[i] for i in seg) / len(seg)
        gg = sum(g[i] for i in seg) / len(seg)
        bb = sum(b[i] for i in seg) / len(seg)
        return [round(rr, 1), round(gg, 1), round(bb, 1)]

    return {
        "mean_rgb": [round(mr, 1), round(mg, 1), round(mb, 1)],
        "low_rgb": region(0.0, 0.10),
        "mid_rgb": region(0.45, 0.55),
        "high_rgb": region(0.90, 1.0),
        "sat": round(sat, 1),
        "pixels": n,
    }

if __name__ == "__main__":
    print(json.dumps(stats(sys.argv[1]), ensure_ascii=False))
