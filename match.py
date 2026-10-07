import cv2
import numpy as np

# ---- load your two photos ----
template = cv2.imread("template.jpg")
scene = cv2.imread("scene.jpg")

# resize scene down if it's a huge phone photo (keeps things fast)
if scene.shape[1] > 1500:
    scale = 1500 / scene.shape[1]
    scene = cv2.resize(scene, None, fx=scale, fy=scale)
    template = cv2.resize(template, None, fx=scale, fy=scale)

# ---- ORB feature matching (handles rotation/scale better than plain matching) ----
orb = cv2.ORB_create(nfeatures=2000)
kp1, des1 = orb.detectAndCompute(cv2.cvtColor(template, cv2.COLOR_BGR2GRAY), None)
kp2, des2 = orb.detectAndCompute(cv2.cvtColor(scene, cv2.COLOR_BGR2GRAY), None)

bf = cv2.BFMatcher(cv2.NORM_HAMMING)
matches = bf.knnMatch(des1, des2, k=2)
good = [m for m, n in matches if m.distance < 0.75 * n.distance]

print(f"Good matches found: {len(good)}")

if len(good) >= 10:
    src_pts = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst_pts = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    H, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
    inliers = int(mask.sum())
    print(f"Inliers: {inliers}/{len(good)}")

    h, w = template.shape[:2]
    corners = np.float32([[0, 0], [0, h], [w, h], [w, 0]]).reshape(-1, 1, 2)
    projected = cv2.perspectiveTransform(corners, H)

    output = scene.copy()
    cv2.polylines(output, [np.int32(projected)], True, (0, 255, 0), 3)
    cv2.imwrite("result.jpg", output)
    print("Saved result.jpg -- open it to see the detected box")
else:
    print("Not enough matches -- try a clearer photo or better-lit object")