import os
import logging
import threading
import asyncio
import time
import queue
import datetime
import requests
import re
import base64
import hashlib
import json
from flask import Flask
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, ConversationHandler, ContextTypes, filters
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5

# ============ 日志 ============
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============ 今日语录 ============
def fetch_quote():
    url = "https://v.api.aa1.cn/api/api-wenan-aiqing/index.php?type=json"
    try:
        r = requests.get(url, timeout=6)
        j = r.json()
        if isinstance(j, dict):
            for key in ("text","content","msg"):
                if j.get(key):
                    return f"🎵 今日语录：{j[key]}"
        if isinstance(j, list) and j and isinstance(j[0], dict):
            for key in ("content","text","msg"):
                if j[0].get(key):
                    return f"🎵 今日语录：{j[0][key]}"
        return "🎵 今日语录获取失败"
    except:
        return "🎵 今日语录获取失败"

# ============ 京东核验 API ============
class Batch2YS:
    def __init__(self):
        self.RsaPubKeyStr = (
            "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCWmc4bKr/RQloO3SBk0PMdNTgxWwKwJNStiZXYX41bCFfgGI5P4tKNsxkv2JKjQpmXkchOiUT2/"
            "hQB6dOtDaKuvfbWRpSoEDNyTVZdavQ9Ubrh3gU0WojRyiN4ytEDOUW8G2Y59UIPZJhItUllkEwT5JlbIofLD3Aq3OZCI0VbUQIDAQAB"
        )
        key_der = base64.b64decode(self.RsaPubKeyStr)
        self.public_key = RSA.importKey(key_der)
        self.cipher = PKCS1_v1_5.new(self.public_key)

    def encrypt_rsa(self, text):
        return base64.b64encode(self.cipher.encrypt(text.encode("utf-8"))).decode("utf-8")

    def compute_md5(self, text):
        return hashlib.md5(text.encode("utf-8")).hexdigest()

    def send_request(self, realName, idCard):
        sign_md5 = self.compute_md5(idCard)
        enc_text = self.encrypt_rsa(idCard)
        payload = {
            "certNo": enc_text,
            "certNoEncrypt": "",
            "certNoMd5": sign_md5,
            "certType": "1",
            "name": realName,
            "nameEncrypt": ""
        }
        headers = {
            "cache-control": "no-cache",
            "jdaz-h5-ver": "bd72ddcd&2025-07-10 10:25:50",
            "jdaz-host": "m.jdallianz.com",
            "jdaz-referer": "https://m.jdallianz.com/ins-temp-m/detail",
            "origin": "https://m.jdallianz.com",
            "xxx-jdaz-app": "pc",
            "Content-Type": "application/json; charset=utf-8"
        }
        url = "https://m.jdallianz.com/c/api/360/tools/realName/verify"
        try:
            response = requests.post(url, data=json.dumps(payload), headers=headers, timeout=5)
            response.raise_for_status()
            return response.text
        except Exception as e:
            logger.error(f"核验请求异常: {e}")
            return None

    def verify_id(self, realName, idCard):
        ret_string = self.send_request(realName, idCard)
        if not ret_string:
            return False
        try:
            result_json = json.loads(ret_string)
            return result_json.get("code") == "0000"
        except:
            return False

# ============ 工具 ============
def extract_chinese(response_json):
    if not response_json:
        return ""
    parts = []
    if "msg" in response_json:
        parts.append(response_json["msg"])
    if "data" in response_json and isinstance(response_json, dict):
        for v in response_json["data"].values():
            if v:
                parts.append(str(v))
    chinese = []
    for p in parts:
        seq = re.findall(r"[\u4e00-\u9fff]+", str(p))
        if seq:
            chinese.append("".join(seq))
    return " ".join(chinese)

def calculate_check_code(id17):
    weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    codes = "10X98765432"
    return codes[sum(int(a) * b for a, b in zip(id17, weights)) % 11]

def generate_all_valid_ids(partial_id, gender):
    base = partial_id[:14]
    valid_ids = []
    for seq in range(0, 1000):
        s = f"{seq:03d}"
        last = int(s[2])
        if (gender == "男" and last % 2 == 1) or (gender == "女" and last % 2 == 0) or (gender == "未知"):
            id17 = base + s
            valid_ids.append(id17 + calculate_check_code(id17))
    return valid_ids

# ============ 多线程 Worker ============
class WorkerThread(threading.Thread):
    def __init__(self, task_q, result_q, name):
        super().__init__(daemon=True)
        self.task_q, self.result_q, self.name = task_q, result_q, name
        self.verifier = Batch2YS()
    def run(self):
        while True:
            id_card = self.task_q.get()
            if id_card is None:
                break
            if not self.result_q.empty():
                break
            if self.verifier.verify_id(self.name, id_card):
                try:
                    resp = requests.get(f"https://zj.v.api.aa1.cn/api/sfz/?sfz={id_card}", timeout=5)
                    resp_json = resp.json() if resp.status_code == 200 else None
                except:
                    resp_json = None
                chinese_text = extract_chinese(resp_json)
                self.result_q.put((id_card, chinese_text))
            self.task_q.task_done()

# ============ 授权系统 ============
authorized_users = {"yeguanzhu"}
usage_records = {}

def is_authorized(username): return username in authorized_users

def check_usage_limit(username, command):
    today = datetime.date.today()
    if is_authorized(username):
        return True
    rec = usage_records.get(username, {})
    if rec.get(command) == today:
        return False
    usage_records.setdefault(username, {})[command] = today
    return True

# ============ Telegram 对话状态 ============
BQ_ID, BQ_GENDER, BQ_NAME = range(3)

# ============ /bq 核验线程 ============
def do_bq_verification(chat_id, partial_id, gender, name, thread_count, bot, loop):
    start = time.time()
    all_ids = generate_all_valid_ids(partial_id, gender)
    task_q, result_q = queue.Queue(), queue.Queue()
    total = len(all_ids)
    for i in all_ids: task_q.put(i)
    threads = [WorkerThread(task_q, result_q, name) for _ in range(thread_count)]
    for t in threads: t.start()

    stop_flag = threading.Event()

    # 进度提示线程
    def progress_reporter():
        while not stop_flag.is_set():
            done = total - task_q.qsize()
            msg = f"🐿️ 正在核验中...\n已完成 {done}/{total}"
            asyncio.run_coroutine_threadsafe(
                bot.send_message(chat_id=chat_id, text=msg),
                loop
            )
            time.sleep(3)

    prog_thread = threading.Thread(target=progress_reporter, daemon=True)
    prog_thread.start()

    success = None
    end_time = time.time() + 30
    while time.time() < end_time:
        try:
            sid, chinese = result_q.get(timeout=1)
            success = (sid, chinese)
            break
        except queue.Empty:
            continue

    stop_flag.set()
    prog_thread.join(timeout=1)
    for _ in threads: task_q.put(None)
    for t in threads: t.join(timeout=1)

    elapsed = time.time() - start
    if success:
        sid, chinese = success
        msg = f"🐿️ 核验成功 🐿️\n🧑 姓名：{name}\n🪪 身份证：{sid}\n⏱️ 用时：{elapsed:.2f}秒\n🐥 接口响应：{chinese}"
    else:
        msg = f"🐿️ 核验失败 🐿️\n🧑 姓名：{name}\n🪪 身份证：{partial_id}\n⏱️ 用时：{elapsed:.2f}秒\n🐥 接口响应：未找到有效身份证"

    asyncio.run_coroutine_threadsafe(bot.send_message(chat_id=chat_id, text=msg), loop)

# ============ /bq 流程 ============
async def bq_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    username = (u.username or "").strip("@")
    if not username:
        await update.message.reply_text("请先设置 Telegram 用户名")
        return ConversationHandler.END
    if not check_usage_limit(username, "bq"):
        await update.message.reply_text("未授权用户每天只能用一次，请联系 @yeguanzhu 授权")
        return ConversationHandler.END
    context.user_data.clear()
    await update.message.reply_text("请输入14位身份证号：", reply_markup=ReplyKeyboardRemove())
    return BQ_ID

async def bq_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or len(text) != 14:
        await update.message.reply_text("身份证号应为14位数字")
        return BQ_ID
    context.user_data["partial_id"] = text
    await update.message.reply_text("请选择性别：", reply_markup=ReplyKeyboardMarkup([["男","女","未知"]], one_time_keyboard=True))
    return BQ_GENDER

async def bq_gender(update: Update, context: ContextTypes.DEFAULT_TYPE):
    g = update.message.text.strip()
    if g not in ["男","女","未知"]:
        await update.message.reply_text("请选择有效性别")
        return BQ_GENDER
    context.user_data["gender"] = g
    await update.message.reply_text("请输入姓名：")
    return BQ_NAME

async def bq_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("姓名不能为空")
        return BQ_NAME
    context.user_data["name"] = name
    await update.message.reply_text("开始核验（固定 20 线程），请稍候...")
    loop = asyncio.get_event_loop()
    threading.Thread(
        target=do_bq_verification,
        args=(update.effective_chat.id,
              context.user_data["partial_id"],
              context.user_data["gender"],
              context.user_data["name"],
              20,
              context.bot,
              loop)
    ).start()
    return ConversationHandler.END

# ============ /start ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quote = fetch_quote()
    await update.message.reply_text(quote)
    await asyncio.sleep(3)
    await update.message.reply_text(
        "🐿️ 身份证核验机器人 🐿️\n"
        "可用指令：\n"
        "/bq   输入14位身份证 → 自动补齐 → 多线程核验\n"
        "/sq   超管授权指定用户\n"
        "/help 显示帮助信息"
    )

# ============ /sq 授权 ============
async def sq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    username = (u.username or "").strip("@")
    if not is_authorized(username):
        await update.message.reply_text("只有超级管理员可以授权")
        return
    if not context.args:
        await update.message.reply_text("用法：/sq 用户名")
        return
    target = context.args[0].strip("@")
    if not target:
        await update.message.reply_text("用户名不能为空")
        return
    authorized_users.add(target)
    await update.message.reply_text(f"已成功授权用户：{target}")

# ============ Flask 保活 ============
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running 🐿️", 200

def run_flask():
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)

# ============ 启动机器人 ============
def run_telegram():
    TOKEN = os.environ.get("BOT_TOKEN")
    if not TOKEN:
        raise RuntimeError("请在 Glitch 的 .env 设置 BOT_TOKEN")
    application = Application.builder().token(TOKEN).build()
    bq_conv = ConversationHandler(
        entry_points=[CommandHandler("bq", bq_start)],
        states={
            BQ_ID:[MessageHandler(filters.TEXT & ~filters.COMMAND, bq_id)],
            BQ_GENDER:[MessageHandler(filters.TEXT & ~filters.COMMAND, bq_gender)],
            BQ_NAME:[MessageHandler(filters.TEXT & ~filters.COMMAND, bq_name)],
        },
        fallbacks=[]
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("sq", sq))
    application.add_handler(bq_conv)
    application.run_polling()

# ============ 主入口 ============
if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    run_telegram()
