import os
import sys
import random
import requests
from io import BytesIO
from PIL import Image
from atproto import Client, client_utils

BSKY_HANDLE = os.environ.get("BSKY_HANDLE")
BSKY_APP_PASSWORD = os.environ.get("BSKY_APP_PASSWORD")
STATE_FILE = "posted_articles.txt"

HEADERS = {
    "User-Agent": "BlueskyUnusualWikiBot/2.0 (contact@example.com)"
}

def get_posted_titles():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def save_posted_title(title):
    with open(STATE_FILE, "a", encoding="utf-8") as f:
        f.write(f"{title}\n")

def get_unusual_articles():
    url = "https://en.wikipedia.org/w/api.php"
    params = {
        "action": "parse",
        "page": "Wikipedia:Unusual_articles",
        "prop": "links",
        "format": "json"
    }
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
        links = resp.json().get("parse", {}).get("links", [])
        return [l["*"] for l in links if l.get("ns") == 0 and "exists" in l]
    except Exception:
        return []

def fetch_summary(title):
    safe_title = title.replace(" ", "_")
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{safe_title}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None

def optimize_image(img_bytes):
    try:
        img = Image.open(BytesIO(img_bytes))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        # Kaliteyi koruyarak en boy oranını 1600px ile sınırla
        img.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
        
        buffer = BytesIO()
        img.save(buffer, format="JPEG", quality=85, optimize=True)
        return buffer.getvalue()
    except Exception:
        return img_bytes

def smart_truncate_text(text, max_len):
    """Metni kelime veya cümle sonundan bölerek maksimum uzunluğa ulaştırır."""
    if len(text) <= max_len:
        return text

    truncated = text[:max_len - 1]
    
    # Mümkünse en yakın cümle sonundan (. ! ?) kes
    last_dot = max(truncated.rfind('. '), truncated.rfind('! '), truncated.rfind('? '))
    if last_dot > max_len * 0.65:
        return truncated[:last_dot + 1]

    # Cümle sonu yoksa son boşluktan kes
    last_space = truncated.rfind(' ')
    if last_space > 0:
        truncated = truncated[:last_space]
        
    return truncated.rstrip() + "…"

def build_dense_post(title, extract, page_url):
    """300 grafem sınırını maksimum bilgiyle dolduran zengin metin mimarı."""
    builder = client_utils.TextBuilder()

    header = f"📌 {title.upper()}\n\n"
    footer_plain = "\n\n📖 Maddenin Tamamı • #Vikipedi #Sıradışı"
    
    # 300 sınırdan sabit alanları ve güvenlik payını (5 karakter) düş
    budget = 295 - len(header) - len(footer_plain)
    body = smart_truncate_text(extract, budget)

    # 1. Başlık
    builder.text(header)
    # 2. Yoğun Bilgi Gövdesi
    builder.text(body)
    # 3. Zengin Altbilgi
    builder.text("\n\n")
    builder.link("📖 Maddenin Tamamı", page_url)
    builder.text(" • ")
    builder.tag("#Vikipedi", "Vikipedi")
    builder.text(" ")
    builder.tag("#Sıradışı", "Sıradışı")

    return builder

def main():
    if not BSKY_HANDLE or not BSKY_APP_PASSWORD:
        sys.exit(1)

    posted = get_posted_titles()
    candidates = get_unusual_articles()
    unposted = [c for c in candidates if c not in posted]
    random.shuffle(unposted)

    target_data = None
    for cand in unposted[:15]:
        data = fetch_summary(cand)
        # Sadece açıklaması olan ve standart maddeleri kabul et
        if data and data.get("type") == "standard" and data.get("extract"):
            target_data = data
            break

    if not target_data:
        print("Uygun içerikli madde bulunamadı.")
        return

    title = target_data.get("title")
    extract = target_data.get("extract", "").strip()
    page_url = target_data.get("content_urls", {}).get("desktop", {}).get("page", "")
    
    # Varsa yüksek çözünürlüklü görseli, yoksa thumbnail'ı seç
    img_url = (
        target_data.get("originalimage", {}).get("source") or 
        target_data.get("thumbnail", {}).get("source")
    )

    image_bytes = None
    if img_url:
        try:
            r = requests.get(img_url, headers=HEADERS, timeout=20)
            if r.status_code == 200:
                image_bytes = optimize_image(r.content)
        except Exception as e:
            print(f"Görsel alınamadı: {e}")

    client = Client()
    client.login(BSKY_HANDLE, BSKY_APP_PASSWORD)

    rich_text = build_dense_post(title, extract, page_url)

    try:
        if image_bytes:
            client.send_image(
                text=rich_text,
                image=image_bytes,
                image_alt=f"{title} konulu Vikipedi arşiv görseli"
            )
        else:
            client.send_post(text=rich_text)

        print(f"Paylaşıldı: {title}")
        save_posted_title(title)
    except Exception as e:
        print(f"Paylaşım başarısız: {e}")

if __name__ == "__main__":
    main()
