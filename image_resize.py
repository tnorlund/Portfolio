import cv2

factor =1.5

# opencv read an image and resize it
image = cv2.imread('pic/Rec.png')
image_size_kb = image.size / 1024
# image_size = image.shape
print(f"Image   size: {image_size_kb} KB")
# store image size
image_size = image.shape
# halve it
image = cv2.resize(image, (int(image_size[1] // factor), int(image_size[0] // factor)))


# calculate the size of the image written to the disk in KB
image_size_kb = image.size / 1024
# image_size = image.shape
print(f"Image resize: {image_size_kb} KB")

# write the image
cv2.imwrite('resize/Resized_Rec.png', image)
