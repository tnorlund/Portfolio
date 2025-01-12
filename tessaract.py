import pytesseract
from PIL import Image
import os

# Get the path to the images directory
images_dir = os.path.join(os.path.dirname(__file__), "pic")

# Get a list of all image files in the directory
image_files = [
    os.path.join(images_dir, f)
    for f in os.listdir(images_dir)
    if f.endswith((".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".gif"))
]

for image_file in image_files:
    print("Processing image:", image_file)
    image = Image.open(image_file)

    # Use Tesseract to do OCR on the image
    text = pytesseract.image_to_string(image)

    # Split the text into words and print each word
    words = text.split()
    for word in words:
        print(word)
