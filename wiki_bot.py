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

# Kullanıcı ajanı Vikipedi API ilkeleri gereği zorunludur
HEADERS = {
    "User-Agent": "BlueskyUnusualWikiBot/1.0 (https://bsky.app/; contact@example.com)"
}

def get_posted_titles():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def save_posted_title(title):
    with open(STATE_FILE, "a", encoding="utf-8") as f:
        f.write(f"{title}\n")

def get_unusual_article_candidates():
    """Wikipedia:Unusual_articles sayfasındaki tüm madde bağlantılarını çeker."""
    url = "https://en.wikipedia.org/w/api.php"
    params = {
        "action": "parse",
        "page": "Wikipedia:Unusual_articles",
        "prop": "links",
        "format": "json"
    }
    resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
    if resp.status_code != 200:
        return []
    
    data = resp.json()
    links = data.get("parse", {}).get("links", [])
    
    # Sadece ana madde alanındaki (ns: 0) bağlantıları filtrele
    articles = [link["*"] for link in links if link.get("ns") == 0 and "exists" in link]
    return articles

def fetch_article_summary(title):
    """MediaWiki REST API üzerinden maddenin özetini ve varsa görselini çeker."""
    safe_title = title.replace(" ", "_")
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{safe_title}"
    
    resp = requests.get(url, headers=HEADERS, timeout=15)
    if resp.status_code != 200:
        return None
    return resp.json()

def optimize_image(img_bytes):
    """Görseli Bluesky'ın 1 MB blob limitine göre optimize eder."""
    try:
        img = Image.open(BytesIO(img_bytes))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
        
        buffer = BytesIO()
        img.save(buffer, format="JPEG", quality=85, optimize=True)
        return buffer.getvalue()
    except Exception:
        return img_bytes

def build_post_text(title, description, extract, article_url):
    """300 karakter sınırına uyacak şekilde zengin metin oluşturur."""
    builder = client_utils.TextBuilder()
    
    header = f"📖 {title}\n"
    builder.text(header)
    
    # Varsa kısa açıklama, yoksa özetten kırpılmış kısım
    body = description if description else extract
    # Kalan karakter alanını hesapla (link ve etiket payı bırak)
    max_body_len = 300 - len(header) - 50 
    if len(body) > max_body_len:
        body = body[:max_body_len - 3].rstrip() + "..."
        
    builder.text(f"{body}\n\n")
    builder.link("Vikipedi Maddesi", article_url)
    builder.text(" ")
    builder.tag("#Vikipedi", "Vikipedi")
    builder.text(" ")
    builder.tag("#İlginçBilgiler", "İlginçBilgiler")
    
    return builder

def main():
    if not BSKY_HANDLE or not BSKY_APP_PASSWORD:
        print("Kimlik bilgileri eksik.")
        sys.exit(1)

    posted = get_posted_titles()
    candidates = get_unusual_article_candidates()
    
    if not candidates:
        print("Madde listesi alınamadı.")
        return

    # Daha önce paylaşılmamış maddeleri seç
    unposted = [t for t in candidates if t not in posted]
    if not unposted:
        print("Tüm arşiv paylaşıldı!")
        return

    random.shuffle(unposted)
    
    # Uygun ve özeti dolu bir madde bulana kadar dene
    selected_data = None
    for candidate_title in unposted[:10]:
        data = fetch_article_summary(candidate_title)
        if data and data.get("type") == "standard" and data.get("extract"):
            selected_data = data
            break

    if not selected_data:
        print("Paylaşılacak uygun madde detayı alınamadı.")
        return

    title = selected_data.get("title")
    description = selected_data.get("description", "")
    extract = selected_data.get("extract", "")
    page_url = selected_data.get("content_urls", {}).get("desktop", {}).get("page", "")
    thumbnail_url = selected_data.get("thumbnail", {}).get("source")

    # Bluesky oturumu aç
    client = Client()
    client.login(BSKY_HANDLE, BSKY_APP_PASSWORD)

    rich_text = build_post_text(title, description, extract, page_url)
    
    # Varsa görseli yükle
    image_bytes = None
    if thumbnail_url:
        try:
            img_resp = requests.get(thumbnail_url, headers=HEADERS, timeout=15)
            if img_resp.status_code == 200:
                image_bytes = optimize_image(img_resp.content)
        except Exception as e:
            print(f"Görsel indirilemedi: {e}")

    try:
        if image_bytes:
            client.send_image(
                text=rich_text,
                image=image_bytes,
                image_alt=f"{title} Wikipedia görseli"
            )
        else:
            client.send_post(text=rich_text)
            
        print(f"Başarıyla paylaşıldı: {title}")
        save_posted_title(title)
        
    except Exception as e:
        print(f"Bluesky paylaşım hatası: {e}")

if __name__ == "__main__":
    main()
