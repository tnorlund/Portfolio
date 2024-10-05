import base64

# Step 1: Read and encode the PNG image
png_image_path = "/Users/tnorlund/GitHub/example/resize/Resized_Rec.png"
with open(png_image_path, "rb") as image_file:
    encoded_string = base64.b64encode(image_file.read()).decode('utf-8')

# Step 2: Create an SVG file with the base64-encoded PNG
svg_content = f'''<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200">
    <image href="data:image/png;base64,{encoded_string}" width="200" height="200"/>
</svg>'''

# Step 3: Save the SVG content to a file
svg_file_path = "output_image.svg"
with open(svg_file_path, "w") as svg_file:
    svg_file.write(svg_content)

print(f"SVG file created: {svg_file_path}")

