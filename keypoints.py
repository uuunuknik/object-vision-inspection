import cv2

img = cv2.imread("template.jpg")
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

orb = cv2.ORB_create(nfeatures=2000)
kp = orb.detect(gray, None)
print("keypoints found:", len(kp))

out = cv2.drawKeypoints(img, kp, None, color=(0, 255, 0),
                        flags=cv2.DRAW_MATCHES_FLAGS_DRAW_RICH_KEYPOINTS)
cv2.imwrite("keypoints_template.jpg", out)