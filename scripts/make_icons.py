from pathlib import Path
from PIL import Image, ImageDraw


for size in (192, 512):
    image = Image.new('RGB', (size, size), '#152e38')
    draw = ImageDraw.Draw(image)
    inset = int(size * .13)
    draw.rounded_rectangle((inset, inset, size - inset, size - inset),
                           radius=int(size * .17), fill='#55d3c0')
    color = '#15313a'
    stroke = int(size * .09)
    left = int(size * .34)
    right = int(size * .66)
    top = int(size * .30)
    bottom = int(size * .70)
    draw.line((left, top, left, bottom), fill=color, width=stroke)
    draw.line((right, top, right, bottom), fill=color, width=stroke)
    draw.line((left, size // 2, right, size // 2), fill=color, width=stroke)
    image.save(Path('public') / f'icon-{size}.png')
