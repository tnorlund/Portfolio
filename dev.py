from PIL import Image
import cv2
import numpy as np

def invert_affine(a, b, c, d, e, f):
    """
    Inverts the 2x3 affine transform:

        [ a  b  c ]
        [ d  e  f ]
        [ 0  0  1 ]

    Returns the 6-tuple (a_inv, b_inv, c_inv, d_inv, e_inv, f_inv)
    for the inverse transform, provided the determinant is not zero.
    """
    det = a * e - b * d
    if abs(det) < 1e-14:
        raise ValueError("Singular transform cannot be inverted.")
    a_inv = e / det
    b_inv = -b / det
    c_inv = (b * f - c * e) / det
    d_inv = -d / det
    e_inv = a / det
    f_inv = (c * d - a * f) / det
    return (a_inv, b_inv, c_inv, d_inv, e_inv, f_inv)

# Download file from CDN
cdn_url = "https://dev.tylernorlund.com/assets/2608fbeb-dd25-4ab8-8034-5795282b6cd6.png"
local_file = "2608fbeb-dd25-4ab8-8034-5795282b6cd6.png"
import requests
r = requests.get(cdn_url)
with open(local_file, "wb") as f:
    f.write(r.content)

bbox = np.array([
    [136.86524540105756, 612.9459893206688],
    [869.5855437615264, 279.14297123098027],
    [2067.888499216709, 2909.499353483462],
    [1335.1682008562402, 3243.3023715731506]
], dtype="float32")

# Optional: Order the points in a consistent order (top-left, top-right, bottom-right, bottom-left)
def order_points(pts):
    # initialize a list of coordinates that will be ordered
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]  # top-left: smallest sum
    rect[2] = pts[np.argmax(s)]  # bottom-right: largest sum

    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]  # top-right: smallest difference
    rect[3] = pts[np.argmax(diff)]  # bottom-left: largest difference
    return rect

# Order the bounding box points (if your points are already in the right order, you can skip this)
rect = order_points(bbox)

# Compute the width and height for the output (destination) rectangle.
# For an affine transform we can define these as the distances between:
# - top-left and top-right (for width) and 
# - top-left and bottom-left (for height)
width = int(np.linalg.norm(rect[0] - rect[1]))
height = int(np.linalg.norm(rect[0] - rect[3]))

# Choose three source points for the affine transform.
# Here we take: top-left, top-right, and bottom-left.
src_tri = np.float32([rect[0], rect[1], rect[3]])

# Define the destination points: we want the region to become an upright rectangle.
dst_tri = np.float32([
    [0, 0],             # top-left maps to (0, 0)
    [width - 1, 0],     # top-right maps to (width, 0)
    [0, height - 1]     # bottom-left maps to (0, height)
])

# Get the affine transformation matrix (2x3) that maps src_tri to dst_tri
M = cv2.getAffineTransform(src_tri, dst_tri)

# print(width, height)

# Load the image
image = cv2.imread(local_file)

# Apply the affine transformation.
# Note: warpAffine uses the size (width, height) of the destination image.
warped = cv2.warpAffine(image, M, (width, height))

# (Optional) Save the result
cv2.imwrite("warped_cv.png", warped)

# Open the image using PIL
image = Image.open(local_file)

# Convert the OpenCV M matrix to a PIL affine transform matrix
a_f = M[0, 0]
b_f = M[0, 1]
c_f = M[0, 2]
d_f = M[1, 0]
e_f = M[1, 1]
f_f = M[1, 2]

a_i, b_i, c_i, d_i, e_i, f_i = invert_affine(a_f, b_f, c_f, d_f, e_f, f_f)

affine_img = image.transform(
    (805, 2890),
    Image.AFFINE,
    (a_i, b_i, c_i, d_i, e_i, f_i),
    fill=1,
    fillcolor=(255, 255, 0),
    # resample=PIL_Image.NEAREST,
)
affine_img.save("warped_pil.png")

# delete the file
import os
os.remove(local_file)
