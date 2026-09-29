from PIL import Image, ImageDraw, ImageFilter
import math

SS = 4  # supersampling

def lin_grad(size, c1, c2, angle=135):
    w, h = size
    g = Image.new("RGBA", size)
    px = g.load()
    a = math.radians(angle)
    dx, dy = math.cos(a), math.sin(a)
    # project onto direction, normalise to 0..1
    vals = [x * dx + y * dy for x in (0, w) for y in (0, h)]
    lo, hi = min(vals), max(vals)
    for y in range(h):
        for x in range(w):
            t = ((x * dx + y * dy) - lo) / (hi - lo)
            px[x, y] = tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(4))
    return g

def mark(size):
    """The blue winged-H mark on a transparent canvas of `size` px (content fills ~the box)."""
    S = size * SS
    mask = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(mask)
    w = S * 0.15                     # pillar width
    top, bot = S * 0.20, S * 0.80
    lx, rx = S * 0.30, S * 0.70 - w
    r = w * 0.5
    d.rounded_rectangle([lx, top, lx + w, bot], radius=r, fill=255)
    d.rounded_rectangle([rx, top, rx + w, bot], radius=r, fill=255)
    d.rounded_rectangle([lx, S * 0.455, rx + w, S * 0.455 + w * 0.72], radius=w * 0.36, fill=255)
    body = lin_grad((S, S), (70, 146, 132, 255), (30, 86, 78, 255), 120)
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(body, (0, 0), mask)

    # swept wing behind the left pillar: three broad, curved feathers fanning up-left
    wing = Image.new("L", (S, S), 0)
    wd = ImageDraw.Draw(wing)
    def feather(bx, by, length, width, ang, bend):
        a = math.radians(ang)
        ux, uy = math.cos(a), math.sin(a)          # axis
        nx, ny = -uy, ux                           # normal
        left, right = [], []
        steps = 40
        for k in range(steps + 1):
            t = k / steps
            curve = bend * S * math.sin(math.pi * t) * 0.5   # gentle arc
            cx = bx + ux * length * t + nx * curve
            cy = by + uy * length * t + ny * curve
            hw = width * ((1 - t) ** 0.7) * min(1.0, t * 7 + 0.35)
            left.append((cx + nx * hw, cy + ny * hw))
            right.append((cx - nx * hw, cy - ny * hw))
        wd.polygon(left + right[::-1], fill=255)
    px = lx + w * 0.62
    feather(px, S * 0.47, S * 0.40, S * 0.062, 196, -0.06)
    feather(px, S * 0.41, S * 0.34, S * 0.056, 208, -0.06)
    feather(px, S * 0.35, S * 0.27, S * 0.050, 220, -0.06)
    wing_col = lin_grad((S, S), (190, 222, 212, 255), (112, 170, 156, 255), 20)
    wlayer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    wlayer.paste(wing_col, (0, 0), wing)
    out = Image.alpha_composite(wlayer, out)
    shifted = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    shifted.alpha_composite(out, (int(S * 0.045), 0))
    return shifted.resize((size, size), Image.LANCZOS)

def background(size, shape):
    S = size * SS
    bg = lin_grad((S, S), (253, 252, 249, 255), (226, 238, 233, 255), 45)
    mask = Image.new("L", (S, S), 0)
    md = ImageDraw.Draw(mask)
    if shape == "round":
        md.ellipse([0, 0, S - 1, S - 1], fill=255)
    elif shape == "square":
        md.rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.23), fill=255)
    else:
        md.rectangle([0, 0, S, S], fill=255)
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(bg, (0, 0), mask)
    # hairline edge so the light icon stays visible on white launchers
    edge = Image.new("L", (S, S), 0)
    ed = ImageDraw.Draw(edge)
    if shape == "round":
        ed.ellipse([0, 0, S - 1, S - 1], outline=255, width=max(2, S // 110))
    elif shape == "square":
        ed.rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.23), outline=255, width=max(2, S // 110))
    if shape != "full":
        line = Image.new("RGBA", (S, S), (214, 226, 220, 255))
        out.paste(line, (0, 0), edge)
    return out.resize((size, size), Image.LANCZOS)

def legacy(size, shape):
    img = background(size, shape)
    m = mark(int(size * 0.80))
    off = (size - m.width) // 2
    img.alpha_composite(m, (off, off))
    return img

def foreground(size):
    """Adaptive-icon foreground: 108dp canvas, mark inside the 66dp safe area."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    m = mark(int(size * 66 / 108))
    off = (size - m.width) // 2
    img.alpha_composite(m, (off, off))
    return img

if __name__ == "__main__":
    import os, sys
    res = sys.argv[1]
    dens = dict(mdpi=1, hdpi=1.5, xhdpi=2, xxhdpi=3, xxxhdpi=4)
    for name, f in dens.items():
        d = os.path.join(res, f"mipmap-{name}")
        os.makedirs(d, exist_ok=True)
        legacy(int(48 * f), "square").save(os.path.join(d, "ic_launcher.png"))
        legacy(int(48 * f), "round").save(os.path.join(d, "ic_launcher_round.png"))
        foreground(int(108 * f)).save(os.path.join(d, "ic_launcher_foreground.png"))
        background(int(108 * f), "full").save(os.path.join(d, "ic_launcher_bg.png"))
    legacy(512, "square").save("/root/scratch/icon/preview-square.png")  # previews
    legacy(512, "round").save("/root/scratch/icon/preview-round.png")
