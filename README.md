# Object Inspector: LINE Bot Image Inspection

Send a photo to a LINE bot. The bot checks whether a reference label is in the photo, draws a box around it, logs the result to MongoDB, and replies with the answer.

Built with Python, FastAPI, OpenCV, MongoDB Atlas and the LINE Messaging API. This is a portfolio project, built to learn how to put image analysis behind a chat interface.

![Pipeline](pipeline_diagram.png)

## How it works

1. A user sends a photo to the LINE bot.
2. LINE calls the FastAPI webhook (`webhook.py`), which checks the LINE signature and downloads the image.
3. OpenCV finds ORB keypoints in the reference label and in the photo, and matches them (Lowe ratio test, 0.75).
4. RANSAC fits a homography to the matches. The matches that agree with it are the "agreeing points".
5. The label counts as found when there are **at least 15 agreeing points and at least 50% of the good matches agree**.
6. A copy of the photo with the detected box drawn is saved, the result (time, found, agreeing points, matches, image file) is stored in MongoDB, and the bot replies in LINE.

This is classic computer vision (feature matching), not machine learning.

## Results

| Test | Result |
|---|---|
| Label rotated 90° | Found. 246 of 266 matches agree |
| Second photo | Found. 206 of 222 matches agree |
| Unrelated labelled object | Rejected. 5 of 18 |

<p>
<img src="line_match.jpg" width="260" alt="LINE reply">
<img src="green_box_result.jpg" width="330" alt="Detected box">
</p>

Each result is logged in MongoDB Atlas:

![MongoDB records](mongo_records.png)

## What went wrong, and what I changed

- **Keyboard photo:** a high match count but a wrong box. The repeating keys match each other, so the count alone is not enough. I added the drawn box so the result can be checked by eye, and the 50% agreement rule.
- **Oversized box:** the first template included the table around the label. I cropped the template to the label only (`template_card.jpg`).
- **Still fails:** small, distant or glossy objects. The label needs to fill roughly a quarter of the frame.

## Run it locally

Requires Python 3.12 (3.14 tried to build OpenCV from source on my Mac).

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file (never commit it):

```
LINE_CHANNEL_ACCESS_TOKEN=...
LINE_CHANNEL_SECRET=...
MONGODB_URI=...
```

Start the server and expose it with ngrok:

```bash
uvicorn webhook:app --port 8000
ngrok http 8000
```

Set the ngrok URL plus `/webhook` as the webhook URL in the LINE Developers console.

## Deploy (run 24/7)

The repo includes a `Dockerfile` and a `render.yaml`, so the bot can run on [Render](https://render.com) without your laptop.

1. Push this repo to GitHub.
2. In Render, choose **New > Blueprint** and select this repo. Render reads `render.yaml`.
3. Enter `LINE_CHANNEL_ACCESS_TOKEN`, `LINE_CHANNEL_SECRET` and `MONGODB_URI` when asked.
4. After the deploy, open `https://<your-service>.onrender.com/`. It should show `{"status":"ok"}`.
5. In the LINE Developers console, set the webhook URL to `https://<your-service>.onrender.com/webhook`, press **Verify**, and turn on **Use webhook**.
6. In MongoDB Atlas, go to **Network Access** and allow `0.0.0.0/0`, or the server cannot reach the database.

Notes:
- `render.yaml` uses Render's **free** plan. A free service sleeps after 15 minutes without traffic, and the first photo after that can take close to a minute. To keep it awake, create a free monitor at [UptimeRobot](https://uptimerobot.com) (type HTTP(s), URL `https://<your-service>.onrender.com/`, every 5 minutes). The free plan includes 750 hours a month, which covers one service running all month.
- The disk is wiped on every redeploy, so the box images in `results/` do not last. The records in MongoDB do.
- The `Dockerfile` also works on other hosts that run containers, such as Google Cloud Run, Railway or Fly.io.

## Files

| File | Purpose |
|---|---|
| `webhook.py` | FastAPI webhook, LINE reply, matching rule, MongoDB logging |
| `match.py` | Standalone ORB + RANSAC matching on two images |
| `keypoints.py` | Draws keypoints, used to see what the matcher sees |
| `template_card.jpg` | Reference label |
| `Dockerfile`, `render.yaml` | Cloud deployment |

## Limits and next steps

- Next: read the label text with OCR and pass it to an LLM.
- Next: store the box images in cloud storage so they survive redeploys.
