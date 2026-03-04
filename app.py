import os
import time
import threading
import requests
import smtplib
from email.mime.text import MIMEText
from flask import Flask, request, render_template_string, redirect
from bs4 import BeautifulSoup

app = Flask(__name__)

# Email-to-SMS settings
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")      # your Gmail
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")    # Gmail app password
SMS_TO = os.getenv("SMS_TO")                    # e.g., 5551234567@vtext.com

products = []

HTML = """
<h2>Add Product</h2>
<form method="post">
URL: <input name="url"><br>
Target Price: <input name="price"><br>
<button type="submit">Add</button>
</form>
<hr>
<h3>Tracked Products</h3>
{% for p in products %}
<p>{{p['url']}} - Target: ${{p['target']}}</p>
{% endfor %}
"""

@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        products.append({
            "url": request.form["url"],
            "target": float(request.form["price"]),
            "last_alert": 0
        })
        return redirect("/")
    return render_template_string(HTML, products=products)

def send_sms(message):
    msg = MIMEText(message)
    msg['Subject'] = "Price Alert!"
    msg['From'] = EMAIL_ADDRESS
    msg['To'] = SMS_TO

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
        server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        server.sendmail(EMAIL_ADDRESS, SMS_TO, msg.as_string())

def check_price(product):
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(product["url"], headers=headers)
    soup = BeautifulSoup(r.text, "html.parser")

    price_tag = soup.find("span")
    if not price_tag:
        return

    try:
        price = float(price_tag.text.replace("$","").replace(",",""))
    except:
        return

    # 24-hour cooldown
    if price <= product["target"] and time.time() - product["last_alert"] > 86400:
        message = f"Deal Alert! ${price}\n{product['url']}"
        send_sms(message)
        product["last_alert"] = time.time()

def monitor():
    while True:
        for product in products:
            check_price(product)
            time.sleep(30)  # staggered checks
threading.Thread(target=monitor, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
