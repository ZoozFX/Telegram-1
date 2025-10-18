MESSAGES = {
    "start_en": "Welcome! Send me anything and I'll save it.",
    "start_ar": "مرحبًا! أرسل أي شيء وسأقوم بحفظه.",
    "saved_en": "Saved your input 👍",
    "saved_ar": "تم حفظ مدخلك 👍",
    "ask_lang_en": "Choose language: /setlang en or /setlang ar",
    "ask_lang_ar": "اختر اللغة: /setlang en أو /setlang ar",
}

def t(key, lang="en"):
    if lang == "ar":
        return MESSAGES.get(f"{key}_ar", MESSAGES.get(f"{key}_en", ""))
    return MESSAGES.get(f"{key}_en", "")
