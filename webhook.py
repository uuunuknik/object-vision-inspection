import os
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from linebot import LineBotApi, WebhookParser
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, ImageMessage, TextSendMessage
import cv2
import numpy as np

load_dotenv()

from datetime import datetime
os.makedirs("results", exist_ok=True)

import certifi
from pymongo import MongoClient

mongo = MongoClient(
    os.getenv("MONGODB_URI"),
    tlsCAFile=certifi.where(),
    serverSelectionTimeoutMS=5000,
)
collection = mongo["inspector"]["results"]


def save_result(found, inliers, matches, image_file):
    try:
        collection.insert_one({
            "time": datetime.now(),
            "found": found,
            "agreeing_points": inliers,
            "matches": matches,
            "image_file": image_file,
        })
    except Exception as e:
        print("MongoDB save failed:", e)

CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

app = FastAPI()
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
parser = WebhookParser(CHANNEL_SECRET)

template = cv2.imread("template_card.jpg")
template = cv2.resize(template, None, fx=500 / template.shape[1], fy=500 / template.shape[1])


def check_match(image_bytes: bytes) -> str:
    arr = np.frombuffer(image_bytes, np.uint8)
    scene = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if scene is None:
        return "Could not read that image, please try again."

    if scene.shape[1] > 1500:
        scale = 1500 / scene.shape[1]
        scene = cv2.resize(scene, None, fx=scale, fy=scale)

    orb = cv2.ORB_create(nfeatures=2000)
    kp1, des1 = orb.detectAndCompute(cv2.cvtColor(template, cv2.COLOR_BGR2GRAY), None)
    kp2, des2 = orb.detectAndCompute(cv2.cvtColor(scene, cv2.COLOR_BGR2GRAY), None)
    if des1 is None or des2 is None:
        return "No features found in the image."

    bf = cv2.BFMatcher(cv2.NORM_HAMMING)
    matches = bf.knnMatch(des1, des2, k=2)
    good = [p[0] for p in matches if len(p) == 2 and p[0].distance < 0.75 * p[1].distance]

    if len(good) < 10:
        save_result(False, 0, len(good), None)
        return f"No match found\nMatches: {len(good)} (too few)"

    src_pts = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst_pts = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    H, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
    if H is None or mask is None:
        return "No match found\nCould not find a consistent position."

    inliers = int(mask.sum())
    found = inliers >= 15 and inliers / len(good) >= 0.5

    # draw the green box on the photo and save it
    if found:
        h, w = template.shape[:2]
        corners = np.float32([[0, 0], [0, h], [w, h], [w, 0]]).reshape(-1, 1, 2)
        projected = cv2.perspectiveTransform(corners, H)
        cv2.polylines(scene, [np.int32(projected)], True, (0, 255, 0), 4)

    status = "found" if found else "not_found"
    filename = datetime.now().strftime("%Y%m%d_%H%M%S") + f"_{status}.jpg"
    cv2.imwrite(os.path.join("results", filename), scene)
    save_result(found, inliers, len(good), filename)

    if found:
        return f"Match found!\nAgreeing points: {inliers} of {len(good)} matches"
    return f"No confident match\nAgreeing points: {inliers} of {len(good)} matches"


@app.post("/webhook")
async def webhook(request: Request):
    body = await request.body()
    signature = request.headers.get("X-Line-Signature", "")
    try:
        events = parser.parse(body.decode(), signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    for event in events:
        if isinstance(event, MessageEvent) and isinstance(event.message, ImageMessage):
            content = line_bot_api.get_message_content(event.message.id)
            image_bytes = b"".join(chunk for chunk in content.iter_content())
            result_text = check_match(image_bytes)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=result_text))
    return "OK"