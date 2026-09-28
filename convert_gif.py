import imageio
from PIL import Image

input_webm = "/config/.gemini/antigravity/brain/e2828c5e-f93d-4512-8f19-da4ec1ccad4b/pulsecoach_demo.webm"
output_gif = "/config/Desktop/Session1/pulsecoach-ai/demo.gif"

print(f"Reading video from {input_webm}...")
reader = imageio.get_reader(input_webm)
meta = reader.get_meta_data()
fps = meta.get('fps', 25)

step = max(1, int(fps / 8))
frames = []

for i, frame in enumerate(reader):
    if i % step == 0:
        img = Image.fromarray(frame)
        w, h = img.size
        new_w = 800
        new_h = int(h * (new_w / w))
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        frames.append(img)

reader.close()

print(f"Extracted {len(frames)} frames. Saving GIF to {output_gif}...")

if frames:
    frames[0].save(
        output_gif,
        save_all=True,
        append_images=frames[1:],
        duration=int(1000 / 8),
        loop=0,
        optimize=True
    )
    print(f"✓ GIF saved successfully at {output_gif}!")
