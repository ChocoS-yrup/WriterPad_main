import cv2
import numpy as np
from PIL import Image

in_path = r"C:\Users\xiix1\.gemini\antigravity-ide\brain\a9123b41-21bd-4943-bb07-0b695c620cca\final_icon_book_orange_1783515278289.png"
out_png = r"D:\안티그래비티\scratch\작가님 힘내세요\app_icon_processed.png"
out_ico = r"D:\안티그래비티\scratch\작가님 힘내세요\app_icon.ico"

# Read image with PIL to handle unicode path
pil_in = Image.open(in_path).convert('RGBA')
img_rgba = np.array(pil_in)
img = cv2.cvtColor(img_rgba, cv2.COLOR_RGBA2BGRA)

# 1. Flood fill from corners to find outer white background
h, w = img.shape[:2]
mask = np.zeros((h + 2, w + 2), np.uint8)

# Flood fill on BGR image because floodFill doesn't support BGRA
bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
for pt in [(0,0), (w-1,0), (0,h-1), (w-1,h-1)]:
    cv2.floodFill(bgr, mask, pt, (255, 255, 255), (10, 10, 10), (10, 10, 10), flags=4 | (255 << 8))

# At this point, the outer white area should be fully transparent (alpha=0).
# We also have the outer mask in 'mask'. The outer mask has 255 for filled areas.
outer_mask = mask[1:h+1, 1:w+1]

# Set alpha to 0 for the outer mask
img[outer_mask == 255, 3] = 0

# 2. Find the inner white logo
# Convert to grayscale to easily find white pixels
gray = cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
# Inner logo: pixels > 200 and NOT in outer_mask
inner_mask = cv2.bitwise_and(cv2.threshold(gray, 240, 255, cv2.THRESH_BINARY)[1], cv2.bitwise_not(outer_mask))

# 3. Dilation to thicken the white lines by ~2px
# Dilation expands the white areas. Since it's a high-res image (likely 1024x1024), 2px might not be enough visually.
# 2px in 1024x1024 is tiny. Let's use a dynamic kernel size based on width. Say, 5x5 or 7x7. 
# Wait, user said "2px 정도 굵게 해줘". Let's assume they mean 2px in a standard icon size (like 256x256), 
# so for a 1024x1024 image, that's roughly 8px. Let's use a 7x7 kernel.
kernel = np.ones((7, 7), np.uint8)
dilated_inner = cv2.dilate(inner_mask, kernel, iterations=1)

# Now we need to blend the dilated inner logo back into the image.
# We turn any pixel that is white in 'dilated_inner' into pure white (255,255,255,255)
img[dilated_inner > 0] = (255, 255, 255, 255)

# Convert back to PIL Image (OpenCV uses BGRA, PIL uses RGBA)
img_rgba = cv2.cvtColor(img, cv2.COLOR_BGRA2RGBA)
pil_img = Image.fromarray(img_rgba)
pil_img.save(out_png)

# Convert to ICO
pil_img = Image.open(out_png)
pil_img.save(out_ico, format='ICO', sizes=[(256, 256), (128, 128), (64, 64), (32, 32), (16, 16)])

print("Processed PNG and generated ICO successfully.")
