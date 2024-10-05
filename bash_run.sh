#!/bin/bash

# Directory containing images
IMAGE_DIR="pic/"

# List images in the directory
echo "Listing images in $IMAGE_DIR:"
ls "$IMAGE_DIR"*.{jpg,jpeg,png,gif} 2>/dev/null

Iterate over each image and use tesseract to write the data to the /out directory
for image in "$IMAGE_DIR"*.{jpg,jpeg,png,gif}; do
    if [[ -f "$image" ]]; then
        echo "Processing $image"
        output_file="out/$(basename "$image" | sed 's/\.[^.]*$//')"
        echo "Writing to $output_file"
        tesseract "$image" "$output_file" -c tessedit_create_hocr=0 makebox
    fi
done
