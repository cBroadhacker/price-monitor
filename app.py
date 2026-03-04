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
import sqlite3
import re

app = Flask(__name__)

# --- Email-to-SMS settings (set these as Render environment variables) ---
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
SMS_TO = os.getenv("SMS_TO")

# --- Database ---
DB_FILE = "products.db"
products = {}  # key=UUID, value=dict

# --- HTML templates ---
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

# --- Database functions ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id TEXT PRIMARY KEY,
            name TEXT,
            url TEXT,
            store TEXT,
            target REAL,
            last_alert REAL,
            current_price REAL,
            notifications_on INTEGER
        )
    ''')
    conn.commit()
    conn.close()

def load_products():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT * FROM products")
    rows = c.fetchall()
    for row in rows:
        pid, name, url, store, target, last_alert, current_price, notifications_on = row
        products[pid] = {
            "name": name,
            "url": url,
            "store": store,
            "target": target,
            "last_alert": last_alert,
            "current_price": current_price,
            "notifications_on": bool(notifications_on)
        }
    conn.close()

def save_product_to_db(pid):
    p = products[pid]
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        INSERT OR REPLACE INTO products (id,name,url,store,target,last_alert,current_price,notifications_on)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (pid, p['name'], p['url'], p['store'], p['target'], p['last_alert'], p['current_price'], int(p['notifications_on'])))
    conn.commit()
    conn.close()

def delete_product_from_db(pid):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM products WHERE id=?", (pid,))
    conn.commit()
    conn.close()

# --- Email/SMS ---
def send_sms(message):
    if not EMAIL_ADDRESS or not EMAIL_PASSWORD or not SMS_TO:
        print("Email/SMS not configured!")
        return
    msg = MIMEText(message)
    msg['Subject'] = "Price Alert!"
    msg['From'] = EMAIL_ADDRESS
    msg['To'] = SMS_TO
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.sendmail(EMAIL_ADDRESS, SMS_TO, msg.as_string())
    except Exception as e:
        print("Failed to send SMS:", e)

# --- Store detection ---
def get_store(url):
    hostname = urlparse(url).hostname or ""
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

# --- Price parsers ---
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
    tag = soup.select_one("div.priceView-hero-price span")
    if tag and "$" in tag.text:
        return float(tag.text.strip().replace("$","").replace(",",""))
    tag_alt = soup.select_one("span.price")
    if tag_alt and "$" in tag_alt.text:
        return float(tag_alt.text.strip().replace("$","").replace(",",""))
    for span in soup.find_all("span"):
        if "$" in span.text:
            try:
                return float(span.text.strip().replace("$","").replace(",",""))
            except: continue
    return None

def get_price_microcenter(soup):
    tag = soup.select_one("span[id='pricing']")
    if tag:
        match = re.search(r"\d+(\.\d+)?", tag.text.replace(",",""))
        if match: return float(match.group())
    for span in soup.find_all("span"):
        if "$" in span.text:
            try:
                match = re.search(r"\d+(\.\d+)?", span.text.replace(",",""))
                if match: return float(match.group())
            except: continue
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

# --- Flask routes ---
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
    save_product_to_db(pid)
    return redirect("/")

@app.route("/remove/<pid>", methods=["GET"])
def remove_product(pid):
    if pid in products:
        del products[pid]
        delete_product_from_db(pid)
    return redirect("/")

@app.route("/edit/<pid>", methods=["GET","POST"])
def edit_product(pid):
    if pid not in products:
        return redirect("/")
    if request.method == "POST":
        products[pid]["name"] = request.form["name"]
        products[pid]["url"] = request.form["url"]
        products[pid]["target"] = float(request.form["price"])
        products[pid]["store"] = get_store(request.form["url"])
        save_product_to_db(pid)
        return redirect("/")
    return render_template_string(EDIT_HTML, p=products[pid])

@app.route("/toggle/<pid>", methods=["POST"])
def toggle_notifications(pid):
    if pid in products:
        products[pid]["notifications_on"] = "notify" in request.form
        save_product_to_db(pid)
    return redirect("/")

# --- Price monitoring ---
def check_price(pid, product):
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(product["url"], headers=headers, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        price = extract_price(product["url"], soup)
        if price is None:
            return
        product["current_price"] = price
        save_product_to_db(pid)
        if price <= product["target"] and product["notifications_on"] and time.time() - product["last_alert"] > 86400:
            send_sms(f"Deal Alert! ${price}\n{product['url']}")
            product["last_alert"] = time.time()
            save_product_to_db(pid)
    except Exception as e:
        print("Price check failed:", e)

def monitor():
    while True:
        for pid, product in list(products.items()):
            check_price(pid, product)
            time.sleep(30)  # staggered
        time.sleep(570)  # remaining time to ~10 minutes

# --- Startup ---
init_db()
load_products()
threading.Thread(target=monitor, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
