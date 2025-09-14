# ========== imghdr 兼容补丁 ==========
import sys
import types

if "imghdr" not in sys.modules:
    fake_imghdr = types.ModuleType("imghdr")
    fake_imghdr.what = lambda file, h=None: None
    sys.modules["imghdr"] = fake_imghdr
# ====================================

import re
import requests
import base64
import hashlib
import json
import time
import threading
from queue import Queue
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5
from telegram.ext import Updater, CommandHandler
import os
from flask import Flask, request
from threading import Thread


# ===================== 保活服务 =====================
app = Flask('')

@app.route('/')
def home():
    return "🤖 Bot is alive! 访问 /keepalive 获取保活说明"

@app.route('/keepalive')
def keepalive_page():
    host = request.host
    url = f"https://{host}/keepalive"
    return f"""
    <html>
    <head><title>保活说明</title></head>
    <body style="font-family:Arial; text-align:center; margin-top:50px;">
        <h2>🚀 保活设置</h2>
        <p>请复制此页面的网址，粘贴到 <a href="https://uptimerobot.com" target="_blank">UptimeRobot</a> 监控服务</p>
        <p>监控类型：HTTP(s)</p>
        <p>监控地址：<b>{url}</b></p>
        <p>建议监控间隔：5分钟</p>
        <hr>
        <p style="color:gray;">本页面用于保持 Replit Bot 长期在线</p>
    </body>
    </html>
    """

def run():
    app.run(host="0.0.0.0", port=8080)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()
# ==============================


# ===================== 每日语录 =====================
def fetch_quote():
    url = "https://v.api.aa1.cn/api/api-wenan-aiqing/index.php?type=json"
    try:
        r = requests.get(url, timeout=6)
        text = r.text.strip()
        try:
            j = json.loads(text)
        except Exception:
            return "🎵 今日语录获取失败"

        if isinstance(j, dict):
            for key in ("text", "content", "msg"):
                val = j.get(key)
                if val:
                    return f"🎵 今日语录：{val}"

        if isinstance(j, list) and j and isinstance(j[0], dict):
            for key in ("content", "text", "msg"):
                val = j[0].get(key)
                if val:
                    return f"🎵 今日语录：{val}"

        return "🎵 今日语录获取失败"
    except Exception:
        return "🎵 今日语录获取失败"


# ===================== 工具函数 =====================
def calculate_check_code(id_17):
    weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    check_codes = "10X98765432"
    total = sum(int(a) * b for a, b in zip(id_17, weights))
    return check_codes[total % 11]


def generate_all_valid_ids(partial_id, gender):
    base = partial_id[:14]
    valid_ids = []
    for seq in range(0, 1000):
        seq_str = f"{seq:03d}"
        last_digit = int(seq_str[2])
        if (gender == '男' and last_digit % 2 == 1) or \
           (gender == '女' and last_digit % 2 == 0) or \
           gender == '未知':
            full_17 = base + seq_str
            check_code = calculate_check_code(full_17)
            full_id = full_17 + check_code
            valid_ids.append(full_id)
    return valid_ids


# ===================== 公安核验封装类 =====================
class Batch2YS:
    def __init__(self):
        self.RsaPubKeyStr = "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCWmc4bKr/RQloO3SBk0PMdNTgxWwKwJNStiZXYX41bCFfgGI5P4tKNsxkv2JKjQpmXkchOiUT2/hQB6dOtDaKuvfbWRpSoEDNyTVZdavQ9Ubrh3gU0WojRyiN4ytEDOUW8G2Y59UIPZJhItUllkEwT5JlbIofLD3Aq3OZCI0VbUQIDAQAB"
        key_der = base64.b64decode(self.RsaPubKeyStr)
        self.public_key = RSA.importKey(key_der)
        self.cipher = PKCS1_v1_5.new(self.public_key)

    def encrypt_rsa(self, text):
        text_bytes = text.encode("utf-8")
        encrypted_bytes = self.cipher.encrypt(text_bytes)
        return base64.b64encode(encrypted_bytes).decode("utf-8")

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
            session = requests.Session()
            response = session.post(url, data=json.dumps(payload), headers=headers, timeout=5)
            response.raise_for_status()
            return response.text
        except Exception:
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


def query_id_info(id_card):
    try:
        url = f"https://zj.v.api.aa1.cn/api/sfz/?sfz={id_card}"
        resp = requests.get(url, timeout=5)
        return resp.json()
    except Exception as e:
        return {"error": str(e)}


# ===================== 多线程 Worker =====================
class WorkerThread(threading.Thread):
    def __init__(self, task_queue, real_name, result_queue):
        super().__init__()
        self.task_queue = task_queue
        self.real_name = real_name
        self.result_queue = result_queue
        self.verifier = Batch2YS()
        self.daemon = True

    def run(self):
        while True:
            id_card = self.task_queue.get()
            if id_card is None:
                self.task_queue.task_done()
                break
            if self.result_queue.empty():
                success = self.verifier.verify_id(self.real_name, id_card)
                if success:
                    self.result_queue.put(id_card)
            self.task_queue.task_done()


# ===================== 授权管理 =====================
OWNER_USERNAME = "yeguanzhu"
AUTHORIZED_USERS = set()

def check_auth(update):
    user = update.effective_user
    if not user:
        return False
    if user.username == OWNER_USERNAME:
        return True
    return user.username in AUTHORIZED_USERS

def require_auth(func):
    def wrapper(update, context, *args, **kwargs):
        if not check_auth(update):
            update.message.reply_text("⚠️ 未授权用户，禁止使用！")
            return
        return func(update, context, *args, **kwargs)
    return wrapper

def sq_cmd(update, context):
    user = update.effective_user
    if user.username != OWNER_USERNAME:
        update.message.reply_text("⚠️ 只有主人才能授权！")
        return
    if not context.args:
        update.message.reply_text("用法: /sq <用户名>")
        return
    target_username = context.args[0].lstrip("@")
    AUTHORIZED_USERS.add(target_username)
    update.message.reply_text(f"✅ 已授权 @{target_username} 使用机器人")

def unauth_cmd(update, context):
    user = update.effective_user
    if user.username != OWNER_USERNAME:
        update.message.reply_text("⚠️ 只有主人才能取消授权！")
        return
    if not context.args:
        update.message.reply_text("用法: /unauth <用户名>")
        return
    target_username = context.args[0].lstrip("@")
    if target_username in AUTHORIZED_USERS:
        AUTHORIZED_USERS.remove(target_username)
        update.message.reply_text(f"❌ 已移除 @{target_username} 的授权")
    else:
        update.message.reply_text(f"ℹ️ 用户 @{target_username} 不在授权列表中")


# ===================== Telegram Bot =====================
TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    raise ValueError("❌ 未检测到 BOT_TOKEN，请在 Replit Secrets 设置 BOT_TOKEN")


@require_auth
def start(update, context):
    quote = fetch_quote()
    msg = (
        "👋 欢迎使用【身份证核验机器人】\n\n"
        f"{quote}\n\n"
        "📌 指令说明：\n"
        "🔄 /bq <14位号码> <姓名> <性别> - 补齐并批量核验身份证 (20线程, 显示耗时+进度条)\n"
        "✅ /hy <18位号码> <姓名> - 直接核验身份证 (单次, 不显示耗时)\n"
        "🛡️ /sq <用户名> - 主人授权用户\n"
        "🛑 /unauth <用户名> - 主人移除授权\n"
        "ℹ️ /help - 查看帮助\n"
    )
    update.message.reply_text(msg)


@require_auth
def help_cmd(update, context):
    update.message.reply_text(
        "📖 使用说明：\n"
        "➡️ /bq 41010219900101 张三 男\n"
        "➡️ /hy 41010219900101003X 张三\n"
        "➡️ /sq @username (主人专用授权)\n"
        "➡️ /unauth @username (主人专用移除授权)\n"
    )


@require_auth
def bq_cmd(update, context):
    if len(context.args) < 3:
        update.message.reply_text("⚠️ 格式错误，请使用：/bq <14位号码> <姓名> <性别>")
        return

    partial_id, name, gender = context.args[0], context.args[1], context.args[2]
    all_ids = generate_all_valid_ids(partial_id, gender)
    total_ids = len(all_ids)

    task_queue = Queue()
    result_queue = Queue()
    for id_num in all_ids:
        task_queue.put(id_num)

    # 固定 20 线程
    threads = []
    for _ in range(20):
        t = WorkerThread(task_queue, name, result_queue)
        t.start()
        threads.append(t)

    start_time = time.time()
    progress_msg = update.message.reply_text(
        f"🔄 开始补齐并核验，共 {total_ids} 个候选号码...\n⏳ 进度: 0%"
    )

    last_update = time.time()

    while not task_queue.empty():
        time.sleep(0.2)
        remaining = task_queue.qsize()
        checked = total_ids - remaining
        percent = int((checked / total_ids) * 100)

        if time.time() - last_update > 1:
            bar_len = 20
            filled = int(bar_len * percent / 100)
            bar = "█" * filled + "-" * (bar_len - filled)
            try:
                progress_msg.edit_text(
                    f"🔄 补齐核验中...\n"
                    f"[{bar}] {percent}%\n"
                    f"✅ 已核验: {checked}/{total_ids}"
                )
            except Exception:
                pass
            last_update = time.time()

        if not result_queue.empty():
            break

    task_queue.join()
    for _ in range(20):
        task_queue.put(None)
    for t in threads:
        t.join()

    elapsed = time.time() - start_time

    if not result_queue.empty():
        success_id = result_queue.get()
        info = query_id_info(success_id)
        progress_msg.edit_text(
            f"✅ 核验成功！\n"
            f"👤 姓名: {name}\n"
            f"🆔 身份证: {success_id}\n"
            f"⏱️ 补齐耗时: {elapsed:.1f} 秒\n\n"
            f"📄 信息:\n{json.dumps(info, ensure_ascii=False, indent=2)}"
        )
    else:
        progress_msg.edit_text(
            f"❌ 所有组合核验失败\n⏱️ 总耗时: {elapsed:.1f} 秒"
        )


@require_auth
def hy_cmd(update, context):
    if len(context.args) < 2:
        update.message.reply_text("⚠️ 格式错误，请使用：/hy <18位号码> <姓名>")
        return

    id_card, name = context.args[0], context.args[1]
    verifier = Batch2YS()
    update.message.reply_text(f"🔍 正在核验 {id_card} ...")

    if verifier.verify_id(name, id_card):
        info = query_id_info(id_card)
        update.message.reply_text(
            f"✅ 核验成功！\n"
            f"👤 姓名: {name}\n"
            f"🆔 身份证: {id_card}\n\n"
            f"📄 信息:\n{json.dumps(info, ensure_ascii=False, indent=2)}"
        )
    else:
        update.message.reply_text("❌ 核验失败")


# ===================== 主函数 =====================
def main():
    print("🤖 Bot 启动中...")
    print(fetch_quote())

    keep_alive()   # 启动保活

    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("sq", sq_cmd))
    dp.add_handler(CommandHandler("unauth", unauth_cmd))
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help_cmd))
    dp.add_handler(CommandHandler("bq", bq_cmd))
    dp.add_handler(CommandHandler("hy", hy_cmd))

    print("🚀 Bot 已上线，等待指令...")
    updater.start_polling()
    updater.idle()


if __name__ == "__main__":
    main()