import cv2
import base64

factor = 10

image_file = "pic/Rec.png"

print(f"Scaling image by: {1/factor}")

# opencv read an image and resize it
image = cv2.imread(image_file)
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
cv2.imwrite("resize/Resized_Rec.png", image)


# convert the image to a base64-encoded string and write it to a text file in the same directory
with open("resize/encoded_image.txt", "w") as encoded_file:
    encoded_file.write(base64.b64encode(image).decode("utf-8"))
