import cv2

# opencv read an image and resize it
image = cv2.imread('pic/Rec.png')
image_size_kb = image.size / 1024
# image_size = image.shape
print(f"Image   size: {image_size_kb} KB")
# store image size
image_size = image.shape
# halve it
image = cv2.resize(image, (image_size[1] // 3, image_size[0] // 3))


# calculate the size of the image written to the disk in KB
image_size_kb = image.size / 1024
# image_size = image.shape
print(f"Image resize: {image_size_kb} KB")

# write the image
cv2.imwrite('resize/Resized_Rec.png', image)
