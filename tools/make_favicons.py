# tools/make_favicons.py
# Генератор фавиконок из одного PNG (нужен Pillow)
# Использование:
#   python tools/make_favicons.py static/img/favicon-master.png static/img/ 0.22
# Последний параметр (0.22) — степень скругления углов (0..0.5), можно не указывать.

import sys, os
from PIL import Image, ImageOps, ImageDraw

def prepare_square(img, size):
    """Центр-кроп до квадрата и ресайз."""
    return ImageOps.fit(img, (size, size), method=Image.LANCZOS)

def round_corners(img, radius_ratio=0.2):
    """Скругляет углы у RGBA-изображения. radius_ratio ~0..0.5."""
    w, h = img.size
    r = int(min(w, h) * float(radius_ratio))
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, w, h), radius=r, fill=255)
    img = img.convert("RGBA")
    img.putalpha(mask)
    return img

def save_png(img, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path, format="PNG", optimize=True)

def main(in_path, out_dir, radius_ratio=0.2):
    base = Image.open(in_path).convert("RGBA")

    # PNG 16 и 32 (для браузера)
    fav16 = round_corners(prepare_square(base, 16), radius_ratio)
    save_png(fav16, os.path.join(out_dir, "favicon-16.png"))

    fav32 = round_corners(prepare_square(base, 32), radius_ratio)
    save_png(fav32, os.path.join(out_dir, "favicon-32.png"))

    # Apple Touch Icon 180x180 (для iOS ярлыка)
    apple = round_corners(prepare_square(base, 180), radius_ratio)
    save_png(apple, os.path.join(out_dir, "apple-touch-icon.png"))

    # ICO с несколькими размерами (16 и 32)
    ico_base = round_corners(prepare_square(base, 256), radius_ratio)
    ico_path = os.path.join(out_dir, "favicon.ico")
    ico_base.save(ico_path, format="ICO", sizes=[(16,16), (32,32)])

    print("Готово! Созданы файлы:",
          "favicon-16.png, favicon-32.png, apple-touch-icon.png, favicon.ico")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python tools/make_favicons.py <input_png> <output_dir> [corner_radius]")
        print("Example: python tools/make_favicons.py static/img/favicon-master.png static/img/ 0.24")
        sys.exit(1)
    in_path = sys.argv[1]
    out_dir = sys.argv[2]
    radius_ratio = float(sys.argv[3]) if len(sys.argv) >= 4 else 0.2
    main(in_path, out_dir, radius_ratio)

