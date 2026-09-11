import os
import sys
import time
import random
import re
import json
import base64
import html
import urllib.parse
import requests
from io import BytesIO
from PIL import Image
from atproto import Client, client_utils

# İngilizce Hesap (Birincil)
BSKY_HANDLE_EN = os.environ.get("BSKY_HANDLE_EN") or os.environ.get("BSKY_HANDLE")
BSKY_APP_PASSWORD_EN = os.environ.get("BSKY_APP_PASSWORD_EN") or os.environ.get("BSKY_APP_PASSWORD")

# Türkçe Hesap (İkincil)
BSKY_HANDLE_TR = os.environ.get("BSKY_TR_HANDLE")
BSKY_APP_PASSWORD_TR = os.environ.get("BSKY_TR_APP_PASSWORD")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = os.environ.get("GITHUB_REPOSITORY")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
STATE_FILE = "posted_articles.txt"

HEADERS = {
    "User-Agent": "BlueskyUnusualWikiBot/6.2 (https://bsky.app/; dual-language curated bot)"
}

GITHUB_API_HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json"
}

TOTAL_BLUESKY_BUDGET = 300
MAX_BLOB_IMAGE_SIZE = 950_000
FALLBACK_EMOJIS = ["📜", "🧐", "💡", "🔍", "✨", "🛸", "🧩"]

UNUSUAL_SOURCES = [
    {
        "lang": "en",
        "domain": "en.wikipedia.org",
        "page": "Wikipedia:Unusual_articles"
    },
    {
        "lang": "de",
        "domain": "de.wikipedia.org",
        "page": "Wikipedia:Kuriositätenkabinett"
    },
    {
        "lang": "es",
        "domain": "es.wikipedia.org",
        "page": "Wikipedia:Artículos_peculiares"
    },
    {
        "lang": "fr",
        "domain": "fr.wikipedia.org",
        "page": "Wikipédia:Articles_insolites"
    }
]

def record_error_and_exit(error_message):
    print(f"\n[KRİTİK ARIZA] {error_message}")
    try:
        with open("error_summary.txt", "w", encoding="utf-8") as f:
            f.write(error_message)
    except Exception as e:
        print(f"Hata özeti yazılamadı: {e}")
    sys.exit(1)

def clean_url(raw_url):
    if not raw_url:
        return raw_url
    match = re.search(r'https?://[^\s)\]"\']+', str(raw_url))
    return match.group(0) if match else raw_url

def get_posted_titles():
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return set()

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{STATE_FILE}"
    try:
        resp = requests.get(url, headers=GITHUB_API_HEADERS, params={"ref": GITHUB_BRANCH}, timeout=15)
        if resp.status_code == 200:
            content_b64 = resp.json().get("content", "")
            text = base64.b64decode(content_b64).decode("utf-8")
            return set(line.strip() for line in text.splitlines() if line.strip())
    except Exception as e:
        print(f"Kayıt dosyası okunurken hata: {e}")
    return set()

def is_already_posted(cand, posted_lower_set):
    raw_title_lower = cand["title"].lower()
    prefixed_key_lower = f"{cand['lang']}:{cand['title']}".lower()
    return (raw_title_lower in posted_lower_set or prefixed_key_lower in posted_lower_set)

def save_posted_title(record_key, max_retries=5):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{STATE_FILE}"

    for attempt in range(1, max_retries + 1):
        current_text = ""
        sha = None

        resp = requests.get(url, headers=GITHUB_API_HEADERS, params={"ref": GITHUB_BRANCH}, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            sha = data["sha"]
            current_text = base64.b64decode(data["content"]).decode("utf-8")

        if record_key in set(line.strip() for line in current_text.splitlines() if line.strip()):
            return

        new_text = current_text
        if new_text and not new_text.endswith("\n"):
            new_text += "\n"
        new_text += f"{record_key}\n"

        payload = {
            "message": "chore: update posted wiki archive [skip ci]",
            "content": base64.b64encode(new_text.encode("utf-8")).decode("utf-8"),
            "branch": GITHUB_BRANCH,
        }
        if sha:
            payload["sha"] = sha

        put_resp = requests.put(url, headers=GITHUB_API_HEADERS, json=payload, timeout=15)
        if put_resp.status_code in (200, 201):
            print(f"'{record_key}' başarıyla arşive işlendi.")
            return
        elif put_resp.status_code in (409, 422):
            time.sleep(1.5)
            continue
        else:
            return

def extract_candidates_from_source(source):
    domain = source["domain"]
    page = source["page"]
    lang = source["lang"]

    api_url = clean_url(f"https://{domain}/w/api.php")
    params = {
        "action": "parse",
        "page": page,
        "prop": "text",
        "redirects": 1,
        "format": "json"
    }

    try:
        resp = requests.get(api_url, params=params, headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            return []

        html_text = resp.json().get("parse", {}).get("text", {}).get("*", "")
        li_blocks = re.findall(r'<li\b[^>]*>(.*?)</li>', html_text, flags=re.DOTALL | re.IGNORECASE)

        skip_prefixes = (
            "wikipedia:", "wikipédia:", "file:", "fichier:", "datei:", "archivo:",
            "help:", "aide:", "hilfe:", "ayuda:", "category:", "catégorie:",
            "kategorie:", "categoría:", "special:", "spezial:", "spécial:", "especial:",
            "talk:", "diskussion:", "discussion:", "discusión:", "template:", "modèle:",
            "vorlage:", "plantilla:", "portal:", "user:", "utilisateur:", "benutzer:", "usuario:",
            "mediawiki:"
        )

        candidates = []
        for li in li_blocks:
            links = re.findall(r'<a\s+[^>]*href=["\']/wiki/([^"#?:]+)["\'][^>]*>(.*?)</a>', li, flags=re.DOTALL | re.IGNORECASE)
            if not links:
                continue

            target_title = None
            for raw_slug, _ in links:
                decoded = urllib.parse.unquote(raw_slug).replace('_', ' ').strip()
                d_lower = decoded.lower()

                if any(d_lower.startswith(p) for p in skip_prefixes):
                    continue
                if d_lower.startswith(("list of", "liste de", "liste von", "lista de", "chronologie", "liste des")):
                    continue

                target_title = decoded
                break

            if not target_title:
                continue

            clean_note = re.sub(r'<[^>]+>', ' ', li)
            clean_note = re.sub(r'\s+', ' ', clean_note).strip()
            clean_note = html.unescape(clean_note)

            if len(clean_note) < 25:
                continue

            candidates.append({
                "lang": lang,
                "domain": domain,
                "title": target_title,
                "curation_note": clean_note
            })

        print(f"[{domain}] Listeden {len(candidates)} adet sıra dışı madde ayıklandı.")
        return candidates
    except Exception as e:
        print(f"Kaynak okuma hatası ({domain}): {e}")
        return []

def fetch_summary(domain, title):
    safe_title = urllib.parse.quote(title.replace(" ", "_"), safe="")
    url = clean_url(f"https://{domain}/api/rest_v1/page/summary/{safe_title}")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None

def resolve_image_url(target_data):
    orig_url = target_data.get("originalimage", {}).get("source") or ""
    thumb_url = target_data.get("thumbnail", {}).get("source") or ""

    def is_svg(url):
        return url.lower().endswith(".svg") or ".svg/" in url.lower()

    if orig_url and not is_svg(orig_url):
        return orig_url

    if thumb_url and not is_svg(thumb_url):
        return thumb_url

    return None

def get_turkish_wiki_page(domain, title):
    safe_title = urllib.parse.quote(title.replace(" ", "_"), safe="")
    lang_code = domain.split(".")[0]

    if lang_code == "tr":
        return f"https://tr.wikipedia.org/wiki/{safe_title}", title

    api_url = clean_url(f"https://{domain}/w/api.php")
    params = {
        "action": "query",
        "titles": title,
        "prop": "langlinks",
        "lllang": "tr",
        "format": "json"
    }

    try:
        resp = requests.get(api_url, params=params, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            pages = resp.json().get("query", {}).get("pages", {})
            for page_id, page_info in pages.items():
                langlinks = page_info.get("langlinks", [])
                if langlinks:
                    tr_title = langlinks[0].get("*")
                    if tr_title:
                        safe_tr = urllib.parse.quote(tr_title.replace(" ", "_"), safe="")
                        print(f"Türkçe karşılığı bulundu: {tr_title}")
                        return f"https://tr.wikipedia.org/wiki/{safe_tr}", tr_title
    except Exception as e:
        print(f"Türkçe dil bağlantısı sorgulama hatası: {e}")

    return None, None

def fetch_image_caption(domain, title, img_url):
    if not img_url:
        return None
    try:
        raw_filename = img_url.split('/')[-1]
        if re.match(r'^\d+px-', raw_filename):
            raw_filename = re.sub(r'^\d+px-', '', raw_filename)
        target_filename = urllib.parse.unquote(raw_filename).replace(' ', '_').lower()

        safe_title = urllib.parse.quote(title.replace(" ", "_"), safe="")
        media_url = clean_url(f"https://{domain}/api/rest_v1/page/media-list/{safe_title}")

        resp = requests.get(media_url, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            items = resp.json().get("items", [])
            for item in items:
                item_file = item.get("title", "").replace("File:", "").replace(" ", "_").lower()
                if item_file and (item_file == target_filename or target_filename in item_file):
                    caption = item.get("caption", {}).get("text", "").strip()
                    if caption:
                        clean_caption = re.sub(r'<[^>]+>', '', caption)
                        return re.sub(r'\s+', ' ', clean_caption).strip()

            if items and items[0].get("caption", {}).get("text"):
                clean_first = re.sub(r'<[^>]+>', '', items[0]["caption"]["text"]).strip()
                if clean_first:
                    return clean_first
    except Exception as e:
        print(f"Görsel açıklaması alınamadı: {e}")
    return None

def optimize_image(img_bytes):
    try:
        img = Image.open(BytesIO(img_bytes))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        max_dim = 1600
        img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

        for quality in (85, 75, 65, 50, 35):
            buffer = BytesIO()
            img.save(buffer, format="JPEG", quality=quality, optimize=True)
            data = buffer.getvalue()
            if len(data) <= MAX_BLOB_IMAGE_SIZE:
                return data

        while len(data) > MAX_BLOB_IMAGE_SIZE and max_dim > 600:
            max_dim -= 300
            img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
            buffer = BytesIO()
            img.save(buffer, format="JPEG", quality=50, optimize=True)
            data = buffer.getvalue()

        return data if len(data) <= MAX_BLOB_IMAGE_SIZE else None
    except Exception as e:
        print(f"Görsel optimize etme hatası: {e}")
        return None

def fit_complete_sentences(text, max_len):
    raw_sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    collected = []
    current_len = 0

    for s in raw_sentences:
        s = s.strip()
        if not s:
            continue
        added_len = len(s) if not collected else len(s) + 1
        if current_len + added_len <= max_len:
            collected.append(s)
            current_len += added_len
        else:
            break

    if collected:
        return " ".join(collected)

    first = raw_sentences[0]
    truncated = first[:max_len - 1]
    last_space = truncated.rfind(' ')
    return (truncated[:last_space] if last_space > 0 else truncated).rstrip() + "..."

def validate_internal_punctuation(text, max_internal=2):
    if not text:
        return True
    sentences = [s.strip() for s in re.split(r'[.!?]+', text) if s.strip()]
    for s in sentences:
        internal_marks = re.findall(r'[,;:\-—–()[\]]', s)
        if len(internal_marks) > max_internal:
            return False
    return True

def is_valid_turkish(text):
    if not text:
        return False
    if any(c in "çğıöşüÇĞİÖŞÜ" for c in text):
        return True
    tr_stopwords = {
        "ve", "bir", "bu", "da", "de", "için", "ile", "gibi", "çok", "ama",
        "ancak", "sonra", "kadar", "olan", "diye", "yok", "var", "oldu",
        "etti", "yaptı", "çıktı", "geldi", "gitti", "başladı", "tarihinde"
    }
    words = set(re.findall(r'\b[a-zA-ZçğıöşüÇĞİÖŞÜ]+\b', text.lower()))
    return len(words.intersection(tr_stopwords)) >= 2

def parse_json_safely(raw_str):
    """LLM çıktısından JSON nesnesini hatasız ayıklar."""
    if not raw_str or not isinstance(raw_str, str):
        return None

    clean = raw_str.strip()
    clean = re.sub(r'^```(?:json)?\s*', '', clean, flags=re.IGNORECASE)
    clean = re.sub(r'\s*```$', '', clean)
    clean = clean.strip()

    # 1. Doğrudan deneme (strict=False kontrol karakterlerini tolere eder)
    try:
        return json.loads(clean, strict=False)
    except Exception:
        pass

    # 2. İlk { ile son } arasını ayıkla
    start = clean.find('{')
    end = clean.rfind('}')
    if start != -1 and end != -1 and end > start:
        snippet = clean[start:end+1]
        try:
            return json.loads(snippet, strict=False)
        except Exception:
            # Trailing comma (sondaki fazla virgül) temizliği
            fixed = re.sub(r',\s*([}\]])', r'\1', snippet)
            try:
                return json.loads(fixed, strict=False)
            except Exception:
                pass
    return None

def request_gemini(prompt):
    if not GEMINI_API_KEY:
        print("[Gemini] API anahtarı (GEMINI_API_KEY) tanımlı değil! Atlanıyor.")
        return None

    url = clean_url(f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}")
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "maxOutputTokens": 1200,
            "temperature": 0.85,
            "thinkingConfig": {
                "thinkingBudget": 0
            }
        }
    }

    try:
        resp = requests.post(url, json=payload, timeout=20)
        if resp.status_code == 200:
            candidates = resp.json().get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                for part in parts:
                    if "text" in part and part["text"].strip():
                        parsed = parse_json_safely(part["text"])
                        if parsed:
                            return parsed
            print(f"[Gemini] Yanıt alındı ancak beklenen JSON çözülemedi.")
        else:
            print(f"[Gemini] HTTP {resp.status_code} Hatası: {resp.text[:300]}")
    except Exception as e:
        print(f"[Gemini] Bağlantı/Zaman aşımı hatası: {e}")
    return None

def request_deepseek(prompt):
    if not DEEPSEEK_API_KEY:
        print("[DeepSeek-V3] API anahtarı (DEEPSEEK_API_KEY) tanımlı değil! Atlanıyor.")
        return None

    url = clean_url("https://api.deepseek.com/chat/completions")
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    # "response_format": {"type": "json_object"} boş string ("") dönme hatasına yol açtığı için kaldırıldı.
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {
                "role": "system",
                "content": "You are a specialized curator bot. You MUST ALWAYS output ONLY a single valid raw JSON object. Do not include markdown formatting, code block fences, or any commentary."
            },
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.75,
        "max_tokens": 1200
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=25)
        if resp.status_code == 200:
            choices = resp.json().get("choices", [])
            if choices:
                message = choices[0].get("message", {})
                content = message.get("content", "") or message.get("reasoning_content", "") or ""
                if content.strip():
                    parsed = parse_json_safely(content)
                    if parsed:
                        return parsed
                    print(f"[DeepSeek-V3] JSON çözülemedi. Ham içerik: {repr(content[:250])}")
                else:
                    print(f"[DeepSeek-V3] Model boş içerik döndürdü. Detay: {choices[0]}")
            else:
                print(f"[DeepSeek-V3] choices dizisi boş döndü.")
        else:
            print(f"[DeepSeek-V3] HTTP {resp.status_code} Hatası: {resp.text[:300]}")
    except Exception as e:
        print(f"[DeepSeek-V3] Bağlantı hatası: {e}")
    return None

def validate_candidate_output(data, budget_en, budget_tr, target_min_en, target_min_tr):
    if not data:
        return False, None, None, None, None, "Yanıt boş veya parse edilemedi"

    emoji = data.get("emoji", "").strip() or random.choice(FALLBACK_EMOJIS)
    n_en = data.get("narrative_en", "").strip()
    n_tr = data.get("narrative_tr", "").strip()
    alt_tr = data.get("alt_tr", "").strip() if data.get("alt_tr") else None

    if not n_en or not n_tr:
        return False, None, None, None, None, "Metin alanları eksik (narrative_en veya narrative_tr boş)"

    if len(n_en) > budget_en:
        n_en = fit_complete_sentences(n_en, budget_en)
    if len(n_tr) > budget_tr:
        n_tr = fit_complete_sentences(n_tr, budget_tr)

    if not is_valid_turkish(n_tr):
        return False, None, None, None, None, "Türkçe metin doğrulaması başarısız (İngilizce saptandı)"

    if not validate_internal_punctuation(n_tr, max_internal=2):
        return False, None, None, None, None, "Türkçe cümlede 2'den fazla iç noktalama işareti var"

    if len(n_en) < target_min_en or len(n_tr) < target_min_tr:
        return False, None, None, None, None, f"Bütçe yetersiz (EN: {len(n_en)}/{target_min_en}, TR: {len(n_tr)}/{target_min_tr})"

    return True, emoji, n_en, n_tr, alt_tr, "Kusursuz"

def generate_dual_language_posts(cand, extract, caption, budget_en, budget_tr):
    # Gerçekçi ve dolgun hedef bütçe (gereksiz reddedilmeleri engeller)
    target_min_en = max(190, budget_en - 55)
    target_min_tr = max(190, budget_tr - 55)

    caption_info = f"Original Image Caption: {caption}\n" if caption else ""

    base_prompt = (
        "You are the curator of a popular Bluesky feed dedicated to reality's strangest oddities.\n"
        f"This subject is officially listed on Wikipedia's curated unusual articles list ({cand['domain']}).\n\n"
        f"Article Title: {cand['title']}\n"
        f"Curator Note (WHY IT IS UNUSUAL): {cand['curation_note']}\n"
        f"Article Extract ({cand['lang'].upper()} Wikipedia): {extract}\n"
        f"{caption_info}\n"
        "GOAL:\n"
        "1. Craft a compelling 2 to 3-sentence micro-narrative in ENGLISH ('narrative_en') that hooks the reader with the sheer bizarre irony of this story.\n"
        "2. Craft a TURKISH version ('narrative_tr') of the same story. CRITICAL LANGUAGE RULE: 'narrative_tr' MUST be written 100% in natural, fluent, native TURKISH (TÜRKÇE). Under NO circumstances write English in narrative_tr! Never use aorist tense (-r, -ar, -er, -maz, -mez; 'yapılır', 'bilinir'); use past (-dı/-miş) or present continuous (-ıyor).\n"
        "3. If an Original Image Caption was provided above, localize it into a short, natural Turkish image description for 'alt_tr' (max 150 chars). If no caption was provided, set 'alt_tr' to null.\n\n"
        "TONE & STYLE (CRITICAL):\n"
        "- Write with an intriguing, curious narrative voice with a subtle touch of dry, intelligent mischief (playful curiosity without being disrespectful or silly).\n"
        "- Do NOT write a dry textbook summary. Avoid formal encyclopedic passive phrasing (e.g. 'It is known as...', 'This article describes...').\n"
        "- Focus on the concrete paradox: the specific odd rule, historical accident, absurd number, or improbable turn of events.\n"
        "- No cheesy clickbait hooks like 'Imagine this', 'Picture this', 'Meet the', 'What if', or 'You won't believe'. Dive straight into the bizarre action or fact.\n"
        "- Avoid excessive punctuation: do NOT use more than 2 mid-sentence punctuation marks (commas/dashes) in a single sentence; split into shorter sentences if needed.\n\n"
        "LENGTH REQUIREMENTS (STRICT):\n"
        f"- Target Range: narrative_en MUST be between {target_min_en} and {budget_en} characters; narrative_tr MUST be between {target_min_tr} and {budget_tr} characters. Fill the available budget with vivid details!\n"
        f"- Hard Limit: Under NO condition exceed {budget_en} characters for EN and {budget_tr} characters for TR.\n"
        "- End on a finished, grammatically complete sentence (punctuated with . ! or ?).\n"
        "- Do not repeat or start with the article title.\n"
        "- No hashtags, no markdown formatting.\n"
        "- Select ONE matching emoji.\n"
        "- Return strictly a single JSON: {\"emoji\": \"...\", \"narrative_en\": \"...\", \"narrative_tr\": \"...\", \"alt_tr\": \"...\"}."
    )

    last_valid_fallback = None

    print(f"\n--- AI Üretim Süreci Başlıyor ---")
    print(f"API Durumu: GEMINI={'Tanımlı' if GEMINI_API_KEY else 'YOK'}, DEEPSEEK={'Tanımlı' if DEEPSEEK_API_KEY else 'YOK'}")

    for attempt in range(1, 4):
        prompt = base_prompt
        if attempt > 1:
            prompt += (
                "\n\nCRITICAL RETRY NOTICE: Either 'narrative_tr' was NOT written in Turkish, or a sentence had excess punctuation, "
                "or text was too short. You MUST write 'narrative_tr' purely in TURKISH, fill the character budget, and avoid aorist tense."
            )

        # 1. ÖNCELİK: GEMINI
        print(f"\n[Deneme {attempt}/3] [1. Öncelik: Gemini 2.5 Flash] çağrılıyor...")
        data_gemini = request_gemini(prompt)
        ok, emoji, n_en, n_tr, alt_tr, reason = validate_candidate_output(
            data_gemini, budget_en, budget_tr, target_min_en, target_min_tr
        )

        if ok:
            print(f"===> Başarılı! Metin GEMINI tarafından üretildi (EN: {len(n_en)} kr, TR: {len(n_tr)} kr).")
            return emoji, n_en, n_tr, alt_tr
        else:
            print(f"[Gemini] Çıktı uygun bulunmadı ({reason}).")
            if data_gemini and is_valid_turkish(data_gemini.get("narrative_tr", "")):
                last_valid_fallback = (
                    data_gemini.get("emoji") or random.choice(FALLBACK_EMOJIS),
                    data_gemini.get("narrative_en", ""),
                    data_gemini.get("narrative_tr", ""),
                    data_gemini.get("alt_tr", ""),
                    "Gemini (Kısmi Bütçe)"
                )

        # 2. ÖNCELİK: DEEPSEEK-V3
        print(f"[Deneme {attempt}/3] [2. Öncelik: DeepSeek-V3] devreye giriyor...")
        data_deepseek = request_deepseek(prompt)
        ok, emoji, n_en, n_tr, alt_tr, reason = validate_candidate_output(
            data_deepseek, budget_en, budget_tr, target_min_en, target_min_tr
        )

        if ok:
            print(f"===> Başarılı! Metin DEEPSEEK-V3 tarafından üretildi (EN: {len(n_en)} kr, TR: {len(n_tr)} kr).")
            return emoji, n_en, n_tr, alt_tr
        else:
            print(f"[DeepSeek-V3] Çıktı uygun bulunmadı ({reason}).")
            if data_deepseek and is_valid_turkish(data_deepseek.get("narrative_tr", "")):
                last_valid_fallback = (
                    data_deepseek.get("emoji") or random.choice(FALLBACK_EMOJIS),
                    data_deepseek.get("narrative_en", ""),
                    data_deepseek.get("narrative_tr", ""),
                    data_deepseek.get("alt_tr", ""),
                    "DeepSeek-V3 (Kısmi Bütçe)"
                )

    if last_valid_fallback:
        em, en_cand, tr_cand, alt_cand, provider = last_valid_fallback
        print(f"\n===> Tam bütçeye ulaşılamadı fakat geçerli Türkçe metin kurtarıldı [{provider}] (EN: {len(en_cand)} kr, TR: {len(tr_cand)} kr).")
        return em, fit_complete_sentences(en_cand, budget_en), fit_complete_sentences(tr_cand, budget_tr), alt_cand

    print("\n[UYARI] Hem Gemini hem DeepSeek-V3 başarısız oldu. Çift dilli AI metni üretilemedi!")
    return random.choice(FALLBACK_EMOJIS), fit_complete_sentences(extract, budget_en), None, None

def build_post(display_title, narrative, emoji, page_url):
    builder = client_utils.TextBuilder()
    builder.text(f"{emoji} ")
    builder.link(display_title.upper(), page_url)
    builder.text("\n\n")
    builder.text(narrative)
    return builder

def send_with_retry(client, rich_text, image_bytes=None, image_alt=None, langs=None, max_retries=3, delay=3):
    for attempt in range(1, max_retries + 1):
        try:
            if image_bytes:
                return client.send_image(
                    text=rich_text,
                    image=image_bytes,
                    image_alt=image_alt,
                    langs=langs
                )
            else:
                return client.send_post(
                    text=rich_text,
                    langs=langs
                )
        except Exception as e:
            print(f"Bluesky gönderim hatası (Deneme {attempt}/{max_retries}): {e}")
            if attempt < max_retries:
                time.sleep(delay)
            else:
                raise e

def main():
    if not BSKY_HANDLE_EN or not BSKY_APP_PASSWORD_EN:
        record_error_and_exit("İngilizce Bluesky hesap bilgileri (BSKY_HANDLE / BSKY_APP_PASSWORD) eksik veya tanımlanmamış.")

    posted = get_posted_titles()
    posted_lower = {line.lower() for line in posted}

    en_source = next(s for s in UNUSUAL_SOURCES if s["lang"] == "en")
    other_sources = [s for s in UNUSUAL_SOURCES if s["lang"] != "en"]

    chosen_candidate = None
    target_data = None

    # 1. ÖNCELİK: ENWIKI
    print("Öncelik kontrolü: ENWIKI taranıyor...")
    en_candidates = extract_candidates_from_source(en_source)
    en_unposted = [c for c in en_candidates if not is_already_posted(c, posted_lower)]
    print(f"[en.wikipedia.org] Henüz paylaşılmamış ENWIKI aday sayısı: {len(en_unposted)}")

    if en_unposted:
        random.shuffle(en_unposted)
        for cand in en_unposted[:50]:
            data = fetch_summary(cand["domain"], cand["title"])
            if data and data.get("type") == "standard" and data.get("extract"):
                chosen_candidate = cand
                target_data = data
                print(f"ENWIKI'den Seçilen Madde: {cand['title']}")
                break

    # 2. ÖNCELİK: ENWIKI BİTERSE DİĞERLERİ
    if not chosen_candidate:
        print("ENWIKI tükendi veya erişilemedi, diğer diller rastgele taranıyor...")
        random.shuffle(other_sources)
        for src in other_sources:
            candidates = extract_candidates_from_source(src)
            unposted = [c for c in candidates if not is_already_posted(c, posted_lower)]
            random.shuffle(unposted)
            for cand in unposted[:40]:
                data = fetch_summary(cand["domain"], cand["title"])
                if data and data.get("type") == "standard" and data.get("extract"):
                    chosen_candidate = cand
                    target_data = data
                    print(f"Yedek Dilden Seçilen Madde: {cand['title']} ({cand['domain']})")
                    break
            if chosen_candidate:
                break

    if not chosen_candidate or not target_data:
        record_error_and_exit("Vikipedi sıra dışı madde listelerinden (EN, DE, ES, FR) paylaşılacak uygun içerik bulunamadı.")

    title_en = chosen_candidate["title"]
    domain = chosen_candidate["domain"]
    extract = target_data.get("extract", "").strip()

    page_url_en = target_data.get("content_urls", {}).get("desktop", {}).get("page", "")

    tr_url, tr_title = get_turkish_wiki_page(domain, title_en)
    if tr_url and tr_title:
        page_url_tr = tr_url
        title_tr = tr_title
    else:
        page_url_tr = page_url_en
        title_tr = title_en

    img_url = resolve_image_url(target_data)
    image_bytes = None
    caption = None
    alt_text_en = f"{title_en} Wikipedia image"
    alt_text_tr = f"{title_tr} Vikipedi görseli"

    if img_url:
        try:
            caption = fetch_image_caption(domain, title_en, img_url)
            if caption:
                alt_text_en = f"{title_en}: {caption}"[:495]

            r = requests.get(clean_url(img_url), headers=HEADERS, timeout=20)
            if r.status_code == 200:
                image_bytes = optimize_image(r.content)
                if image_bytes:
                    print(f"Görsel optimize edildi ({len(image_bytes)} bytes, Blob < 950KB garantisi sağlandı).")
                else:
                    print("Görsel 950 KB sınırına indirilemedi, metin modunda devam edilecek.")
        except Exception as e:
            print(f"Görsel indirilemedi: {e}")

    header_len_en = len(title_en) + 5
    budget_en = TOTAL_BLUESKY_BUDGET - header_len_en - 2

    header_len_tr = len(title_tr) + 5
    budget_tr = TOTAL_BLUESKY_BUDGET - header_len_tr - 2

    emoji, narrative_en, narrative_tr, alt_tr = generate_dual_language_posts(
        chosen_candidate, extract, caption, budget_en, budget_tr
    )

    if alt_tr:
        alt_text_tr = f"{title_tr}: {alt_tr}"[:495]
    elif caption:
        alt_text_tr = f"{title_tr}: {caption}"[:495]

    post_en = build_post(title_en, narrative_en, emoji, page_url_en)

    # 1. HESAP: İNGİLİZCE PAYLAŞIM
    try:
        client_en = Client()
        client_en.login(BSKY_HANDLE_EN, BSKY_APP_PASSWORD_EN)
        send_with_retry(
            client=client_en,
            rich_text=post_en,
            image_bytes=image_bytes,
            image_alt=alt_text_en,
            langs=["en"]
        )
        print(f"[EN Hesap] Başarıyla paylaşıldı: {title_en}")
    except Exception as e:
        record_error_and_exit(f"İngilizce hesap ({BSKY_HANDLE_EN}) Bluesky paylaşımı başarısız oldu: {e}")

    # 2. HESAP: TÜRKÇE PAYLAŞIM
    if BSKY_HANDLE_TR and BSKY_APP_PASSWORD_TR:
        if not narrative_tr or not is_valid_turkish(narrative_tr):
            record_error_and_exit("Türkçe hesap için geçerli bir Türkçe metin üretilemedi; yabancı dilde paylaşım engellendi.")
        else:
            try:
                post_tr = build_post(title_tr, narrative_tr, emoji, page_url_tr)
                client_tr = Client()
                client_tr.login(BSKY_HANDLE_TR, BSKY_APP_PASSWORD_TR)
                send_with_retry(
                    client=client_tr,
                    rich_text=post_tr,
                    image_bytes=image_bytes,
                    image_alt=alt_text_tr,
                    langs=["tr"]
                )
                print(f"[TR Hesap] Başarıyla paylaşıldı: {title_tr}")
            except Exception as e:
                record_error_and_exit(f"Türkçe hesap ({BSKY_HANDLE_TR}) Bluesky paylaşımı başarısız oldu: {e}")
    else:
        print("Türkçe hesap kimlik bilgileri tanımlı değil, sadece İngilizce paylaşıldı.")

    save_posted_title(f"{chosen_candidate['lang']}:{title_en}")

if __name__ == "__main__":
    main()
