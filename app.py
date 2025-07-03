from flask import Flask, request
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
import time, os, requests

app = Flask(__name__)
BOT_TOKEN = os.getenv("BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("CHAT_ID")

@app.route("/generate", methods=["POST"])
def gen():
    data = request.json or {}
    prompt = data.get("prompt", "").strip()
    chat_id = data.get("chat_id", TELEGRAM_CHAT_ID)
    if not prompt or not BOT_TOKEN:
        return {"error": "prompt/BOT_TOKEN missing"}, 400

    start = time.time()
    opts = Options()
    opts.add_argument("--headless")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    driver = webdriver.Chrome(ChromeDriverManager().install(), options=opts)

    try:
        url = f"https://www.pollitions.ai/?prompt={prompt.replace(' ','%20')}"
        driver.get(url)
        time.sleep(12)
        path = "/tmp/out.png"
        driver.save_screenshot(path)
        elapsed = round(time.time() - start, 2)
        caption = f"🖼 Image ready in {elapsed}s\nPrompt: {prompt}"

        with open(path, "rb") as f:
            resp = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                data={"chat_id": chat_id, "caption": caption},
                files={"photo": f}
            )
        return {"telegram": resp.json()}, 200
    finally:
        driver.quit()

@app.route("/health")
def health():
    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)