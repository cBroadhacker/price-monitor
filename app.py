import os
import time
import threading
import requests
import smtplib
from email.mime.text import MIMEText
from flask import Flask, request, render_template_string, redirect
from bs4 import BeautifulSoup
import uuid
from urllib.parse import urlparse
import re

app = Flask(__name__)

# Email-to-SMS settings
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
SMS_TO = os.getenv("SMS_TO")

products = {}

HTML = """
<h2>Add Product</h2>
<form method="post" action="/add">
Product Name: <input name="name" required><br>
URL: <input name="url" required><br>
Target Price: <input name="price" required><br>
<button type="submit">Add</button>
</form>
<hr>
<h3>Tracked Products (up to 100)</h3>
<table border="1" cellpadding="5">
<tr><th>Store</th><th>Product Name</th><th>Current Price</th><th>Target Price</th><th>Notifications</th><th>Actions</th></tr>
{% for pid, p in products.items() %}
<tr>
<td>{{p['store']}}</td>
<td>{{p['name']}}</td>
<td>${{p['current_price'] if p['current_price'] else 'N/A'}}</td>
<td>${{p['target']}}</td>
<td>
<form method="post" action="/toggle/{{pid}}" style="display:inline">
<input type="checkbox" name="notify" onchange="this.form.submit()" {% if p['notifications_on'] %}checked{% endif %}>
Notify
</form>
</td>
<td><a href="/remove/{{pid}}">Remove</a> | <a href="/edit/{{pid}}">Edit</a></td>
</tr>
{% endfor %}
</table>
"""

EDIT_HTML = """
<h2>Edit Product</h2>
<form method="post">
Product Name: <input name="name" value="{{p['name']}}" required><br>
URL: <input name="url" value="{{p['url']}}" required><br>
Target Price: <input name="price" value="{{p['target']}}" required><br>
<button type="submit">Update</button>
</form>
"""

def send_sms(message):
    msg = MIMEText(message)
    msg['Subject'] = "Price Alert!"
    msg['From'] = EMAIL_ADDRESS
    msg['To'] = SMS_TO
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
        server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        server.sendmail(EMAIL_ADDRESS, SMS_TO, msg.as_string())

def get_store(url):
    hostname = urlparse(url).hostname
    if "amazon.com" in hostname:
        return "Amazon"
    elif "walmart.com" in hostname:
        return "Walmart"
    elif "target.com" in hostname:
        return "Target"
    elif "bestbuy.com" in hostname:
        return "Best Buy"
    elif "microcenter.com" in hostname:
        return "Micro Center"
    else:
        return "Unknown"

# Robust price parsers
def get_price_amazon(soup):
    tag = soup.select_one("#priceblock_ourprice, #priceblock_dealprice")
    return float(tag.text.replace("$","").replace(",","")) if tag else None

def get_price_walmart(soup):
    tag = soup.select_one("span[class*='price-characteristic']")
    return float(tag.text) if tag else None

def get_price_target(soup):
    tag = soup.select_one("span[data-test='product-price']")
    return float(tag.text.replace("$","").replace(",","")) if tag else None

def get_price_bestbuy(soup):
    # Hero price
    tag = soup.select_one("div.priceView-hero-price span")
    if tag and "$" in tag.text:
        return float(tag.text.strip().replace("$","").replace(",",""))
    # Alternate price span
    tag_alt = soup.select_one("span.price")
    if tag_alt and "$" in tag_alt.text:
        return float(tag_alt.text.strip().replace("$","").replace(",",""))
    # Fallback
    for span in soup.find_all("span"):
        if "$" in span.text:
            try:
                return float(span.text.strip().replace("$","").replace(",",""))
            except:
                continue
    return None

def get_price_microcenter(soup):
    tag = soup.select_one("span[id='pricing']")
    if tag:
        match = re.search(r"\d+(\.\d+)?", tag.text.replace(",",""))
        if match:
            return float(match.group())
    for span in soup.find_all("span"):
        if "$" in span.text:
            try:
                match = re.search(r"\d+(\.\d+)?", span.text.replace(",",""))
                if match:
                    return float(match.group())
            except:
                continue
    return None

def extract_price(url, soup):
    store = get_store(url)
    if store == "Amazon":
        return get_price_amazon(soup)
    elif store == "Walmart":
        return get_price_walmart(soup)
    elif store == "Target":
        return get_price_target(soup)
    elif store == "Best Buy":
        return get_price_bestbuy(soup)
    elif store == "Micro Center":
        return get_price_microcenter(soup)
    return None

@app.route("/", methods=["GET"])
def home():
    return render_template_string(HTML, products=products)

@app.route("/add", methods=["POST"])
def add_product():
    if len(products) >= 100:
        return "Max 100 products reached", 400
    url = request.form["url"]
    target = float(request.form["price"])
    name = request.form["name"]
    pid = str(uuid.uuid4())
    store = get_store(url)
    products[pid] = {
        "url": url,
        "store": store,
        "name": name,
        "target": target,
        "last_alert": 0,
        "current_price": None,
        "notifications_on": True
    }
    return redirect("/")

@app.route("/remove/<pid>", methods=["GET"])
def remove_product(pid):
    if pid in products:
        del products[pid]
    return redirect("/")

@app.route("/edit/<pid>", methods=["GET", "POST"])
def edit_product(pid):
    if pid not in products:
        return redirect("/")
    if request.method == "POST":
        products[pid]["name"] = request.form["name"]
        products[pid]["url"] = request.form["url"]
        products[pid]["target"] = float(request.form["price"])
        products[pid]["store"] = get_store(request.form["url"])
        return redirect("/")
    return render_template_string(EDIT_HTML, p=products[pid])

@app.route("/toggle/<pid>", methods=["POST"])
def toggle_notifications(pid):
    if pid in products:
        products[pid]["notifications_on"] = "notify" in request.form
    return redirect("/")

def check_price(product):
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(product["url"], headers=headers, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        price = extract_price(product["url"], soup)
        if price is None:
            return
        product["current_price"] = price
        if price <= product["target"] and product["notifications_on"] and time.time() - product["last_alert"] > 86400:
            send_sms(f"Deal Alert! ${price}\n{product['url']}")
            product["last_alert"] = time.time()
    except:
        pass

def monitor():
    while True:
        for product in list(products.values()):
            check_price(product)
            time.sleep(30)

threading.Thread(target=monitor, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
