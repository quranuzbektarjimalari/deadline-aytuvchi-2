import nest_asyncio
import asyncio
import requests
import pytz
import json
import os

from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    filters
)

from datetime import datetime, time

nest_asyncio.apply()

# =========================================
# ⚙️ SOZLAMALAR
# =========================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

# LMS LOGIN
LMS_USER = os.getenv("LMS_USER")
LMS_PASS = os.getenv("LMS_PASS")

GLOBAL_EXECUTOR = ThreadPoolExecutor(max_workers=10)

TASHKENT_TZ = pytz.timezone("Asia/Tashkent")

USERS_FILE = "users.json"

SUBJECT_LINKS = {
    "833-28-uz": "Din sotsiologiyasi",
    "834-28-uz": "Din psixologiyasi",
    "835-28-uz": "Tafsir matnlari",
    "836-28-uz": "Tasavvufiy tafsirlar",
    "837-28-uz": "Hanafiylikdagi aqidaviy matnlar sharhlari",
    "838-28-uz": "Qur'on navhi"
}


# =========================================
# 👥 USERS STORAGE
# =========================================

def load_users():

    if not os.path.exists(USERS_FILE):
        return []

    try:
        with open(USERS_FILE, "r") as f:
            return json.load(f)
    except:
        return []


def save_user(chat_id):

    users = load_users()

    if chat_id not in users:

        users.append(chat_id)

        with open(USERS_FILE, "w") as f:
            json.dump(users, f)


# =========================================
# 🔐 LMS LOGIN
# =========================================

def login_to_lms(username, password):

    try:

        session = requests.Session()

        login_url = "https://lms.iiau.uz/auth/login"

        # CSRF TOKEN
        resp = session.get(login_url, timeout=10)

        soup = BeautifulSoup(resp.text, "html.parser")

        token_tag = soup.find("input", {"name": "_token"})

        token = token_tag["value"] if token_tag else ""

        payload = {
            "_token": token,
            "login": username,
            "password": password
        }

        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": login_url
        }

        r = session.post(
            login_url,
            data=payload,
            headers=headers,
            timeout=10
        )

        if (
            "logout" in r.text
            or "Chiqish" in r.text
            or "/auth/logout" in r.text
        ):
            return session, "OK", None

        else:
            return None, None, "Login yoki parol noto‘g‘ri."

    except Exception as e:
        return None, None, str(e)


# =========================================
# 📚 FAN NOMINI ANIQLASH
# =========================================

def extract_subject_fast(soup):

    try:

        back_link = None

        for a in soup.find_all("a", href=True):

            if "Orqaga" in a.get_text(strip=True):
                back_link = a
                break

        if back_link:

            href = back_link["href"]

            for key, name in SUBJECT_LINKS.items():

                if key in href:
                    return name

        return "❓ Fani aniqlanmadi"

    except:
        return "❓ Fani aniqlanmadi"


# =========================================
# ⚡ URL MAVJUDLIGINI TEKSHIRISH
# =========================================

def fast_check_exists(session, url, retries=3):

    for attempt in range(retries):

        try:

            r = session.head(url, timeout=7)

            if r.status_code == 200:
                return True

            r = session.get(url, timeout=7)

            if r.status_code == 200:
                return True

        except:
            if attempt == retries - 1:
                return False

    return False


# =========================================
# 📘 TEST TEKSHIRISH
# =========================================

def check_test(session, url, retries=3):

    for attempt in range(retries):

        try:

            if not fast_check_exists(session, url):
                return None

            r = session.get(url, timeout=10)

            r.raise_for_status()

            soup = BeautifulSoup(r.text, "html.parser")

            title_tag = soup.find("h3", class_="page-title")

            title = (
                title_tag.get_text(strip=True)
                if title_tag
                else "Noma’lum test"
            )

            strong = soup.find(
                "strong",
                string=lambda s: s and "Tugallanish vaqti" in s
            )

            deadline = "-"

            if strong:

                span = strong.find_next(
                    "span",
                    class_="text-primary"
                )

                if span:
                    deadline = span.get_text(strip=True)

            subject = extract_subject_fast(soup)

            return (title, subject, deadline, url)

        except:

            if attempt == retries - 1:
                return None


# =========================================
# 📕 TOPSHIRIQ TEKSHIRISH
# =========================================

def check_assignment(session, url, retries=3):

    for attempt in range(retries):

        try:

            if not fast_check_exists(session, url):
                return None

            r = session.get(url, timeout=10)

            r.raise_for_status()

            soup = BeautifulSoup(r.text, "html.parser")

            title = "Noma’lum topshiriq"

            for p in soup.find_all("p", class_="header-title"):

                span = p.find("span")

                if span and "Topshiriq nomi" in span.get_text(strip=True):

                    title = (
                        p.get_text(" ", strip=True)
                        .replace("Topshiriq nomi:", "")
                        .strip()
                    )

            deadline = "-"

            for p in soup.find_all("p", class_="header-title"):

                span = p.find("span")

                if span and "Topshiriq muddati" in span.get_text(strip=True):

                    deadline = (
                        p.get_text(" ", strip=True)
                        .replace("Topshiriq muddati", "")
                        .strip()
                    )

            subject = extract_subject_fast(soup)

            return (title, subject, deadline, url)

        except:

            if attempt == retries - 1:
                return None


# =========================================
# 📅 BUGUNGI SANAMI?
# =========================================

def is_today(deadline_str):

    try:

        s = (
            deadline_str
            .strip()
            .replace(".", "-")
            .replace("–", "-")
            .replace("—", "-")
        )

        formats = [
            "%d-%m-%Y %H:%M:%S",
            "%d-%m-%Y %H:%M",
            "%d.%m.%Y %H:%M:%S",
            "%d.%m.%Y %H:%M"
        ]

        for fmt in formats:

            try:

                dt = datetime.strptime(s, fmt)

                return (
                    dt.date()
                    ==
                    datetime.now(TASHKENT_TZ).date()
                )

            except:
                continue

        return False

    except:
        return False


# =========================================
# 📘 BUGUNGI TESTLAR
# =========================================

def find_today_tests(session, start_id=1110, end_id=1410):

    base_url = (
        "https://lms.iiau.uz/"
        "student/my-course/calendar/resource/test/"
    )

    results = []

    urls = [
        f"{base_url}{i}"
        for i in range(start_id, end_id + 1)
    ]

    futures = [
        GLOBAL_EXECUTOR.submit(check_test, session, url)
        for url in urls
    ]

    for fut in as_completed(futures):

        res = fut.result()

        if res and is_today(res[2]):
            results.append(res)

    return results


# =========================================
# 📕 BUGUNGI TOPSHIRIQLAR
# =========================================

def find_today_assignments(session, start_id=6500, end_id=6800):

    base_url = (
        "https://lms.iiau.uz/"
        "student/my-course/calendar/resource/activity/standard-"
    )

    results = []

    urls = [
        f"{base_url}{i}"
        for i in range(start_id, end_id + 1)
    ]

    futures = [
        GLOBAL_EXECUTOR.submit(check_assignment, session, url)
        for url in urls
    ]

    for fut in as_completed(futures):

        res = fut.result()

        if res and is_today(res[2]):
            results.append(res)

    return results


# =========================================
# 📝 XABAR YASASH
# =========================================

def build_message(tests, assignments):

    now = datetime.now(TASHKENT_TZ)

    weekdays_uz = [
        "Dushanba",
        "Seshanba",
        "Chorshanba",
        "Payshanba",
        "Juma",
        "Shanba",
        "Yakshanba"
    ]

    bugungi_sana = now.strftime("%d-%m-%Y")

    bugungi_kun = weekdays_uz[now.weekday()]

    if not tests and not assignments:

        return (
            f"✅ Bugun tugaydigan test yoki topshiriq yo‘q!\n"
            f"({bugungi_sana}, {bugungi_kun})"
        )

    msg = (
        "⚠️⏳ *DIQQAT DEADLINE*\n\n"
        "*Bugun quyidagi vazifalar tugaydi:*\n\n"
    )

    if tests:

        for title, subject, deadline, link in tests:

            msg += (
                f"📘 *Test:* *{title}*\n"
                f"🕒 Tugash: {deadline}\n"
                f"👉 {subject}\n"
                f"[Ko‘rish]({link})\n\n"
            )

    if assignments:

        for title, subject, deadline, link in assignments:

            msg += (
                f"📕 *Topshiriq:* *{title}*\n"
                f"🕒 Tugash: {deadline}\n"
                f"👉 {subject}\n"
                f"[Ko‘rish]({link})\n\n"
            )

    return msg


# =========================================
# 📤 DEADLINE YUBORISH
# =========================================

async def send_today_deadlines(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    chat = update.effective_chat

    temp_msg = await context.bot.send_message(
        chat_id=chat.id,
        text="🙋‍♂️ Bugungi deadlinelar tekshirilmoqda..."
    )

    session, _, err = login_to_lms(LMS_USER, LMS_PASS)

    if not session:

        await context.bot.send_message(
            chat_id=chat.id,
            text=f"❌ LMS ga kirishda xato:\n{err}"
        )

        return

    tests = find_today_tests(session)

    assignments = find_today_assignments(session)

    await temp_msg.delete()

    msg = build_message(tests, assignments)

    await context.bot.send_message(
        chat_id=chat.id,
        text=msg,
        parse_mode="Markdown",
        disable_web_page_preview=True
    )


# =========================================
# ⏰ HAR KUNI AVTOMATIK YUBORISH
# =========================================

async def auto_send_deadlines(
    context: ContextTypes.DEFAULT_TYPE
):

    users = load_users()

    if not users:
        return

    print("⏰ Avtomatik deadline yuborish boshlandi...")

    session, _, err = login_to_lms(LMS_USER, LMS_PASS)

    if not session:

        print("❌ LMS LOGIN ERROR:", err)

        return

    tests = find_today_tests(session)

    assignments = find_today_assignments(session)

    msg = build_message(tests, assignments)

    for chat_id in users:

        try:

            await context.bot.send_message(
                chat_id=chat_id,
                text=msg,
                parse_mode="Markdown",
                disable_web_page_preview=True
            )

            print(f"✅ Yuborildi: {chat_id}")

        except Exception as e:

            print(f"❌ {chat_id} ga yuborilmadi:", e)


# =========================================
# 🚀 START
# =========================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    chat_id = update.effective_chat.id

    save_user(chat_id)

    await update.message.reply_text(
        "✅ Siz botga muvaffaqiyatli ulandingiz.\n\n"
        "⏰ Endi har kuni sizga soat 08:00 da "
        "joriy kun deadlinelari yuboriladi.\n\n"
        "📌 Qo‘lda tekshirish uchun: /bugun"
    )


# =========================================
# 👥 USERS COUNT
# =========================================

async def users_count(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    users = load_users()

    count = len(users)

    await update.message.reply_text(
        f"👥 Bot foydalanuvchilari soni: {count} ta"
    )

# =========================================
# 🤖 MAIN
# =========================================

async def main():

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # /start
    app.add_handler(CommandHandler("start", start))
    # /users
    app.add_handler(CommandHandler("users", users_count))
    # /bugun
    app.add_handler(
        CommandHandler(
            "bugun",
            send_today_deadlines,
            filters.ChatType.PRIVATE
            |
            filters.ChatType.GROUPS
        )
    )

    # ⏰ HAR KUNI 08:00
    app.job_queue.run_daily(
        auto_send_deadlines,
        time=time(
            hour=11,
            minute=10,
            tzinfo=TASHKENT_TZ
        )
    )
    
        
    print("✅ Bot ishga tushdi.")

    await app.run_polling()


# =========================================
# ▶️ START
# =========================================

if __name__ == "__main__":
    asyncio.run(main())
