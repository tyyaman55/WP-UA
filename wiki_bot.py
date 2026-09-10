def get_ai_context_emoji(title, extract):
    """Gemini 2.5 Flash kullanarak içerikle en uyumlu tek bir emojiyi belirler."""
    if not GEMINI_API_KEY:
        return random.choice(FALLBACK_EMOJIS)

    prompt = (
        "Given the Wikipedia title and short summary below, return ONLY ONE single emoji "
        "that best captures the essence, humor, absurdity, or subject of the story. "
        "Do NOT write any words, explanations, or quotes. Output ONLY the emoji character itself.\n\n"
        f"Title: {title}\n"
        f"Summary: {extract[:300]}"
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}]
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            result = resp.json()
            emoji_text = result["candidates"][0]["content"]["parts"][0]["text"].strip()
            if emoji_text:
                selected_emoji = emoji_text.split()[0]
                print(f"Yapay zeka (gemini-2.5-flash) tarafından seçilen emoji: {selected_emoji}")
                return selected_emoji
        else:
            print(f"Gemini API yanıt kodu ({resp.status_code}): {resp.text}")
    except Exception as e:
        print(f"Yapay zeka emoji seçim hatası: {e}")

    return random.choice(FALLBACK_EMOJIS)
