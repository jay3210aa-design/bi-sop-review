from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, date

import requests
from openai import OpenAI


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--slot", type=int, choices=[1, 2], required=True, help="1=09:00, 2=15:00")
    return p.parse_args()


args = parse_args()
today = date.today().isoformat()

# 用 slot 區分每天兩次
run_key = f"{today}_S{args.slot}"
slot_label = "Morning" if args.slot == 1 else "Afternoon"

print("run_key =", run_key)


# ===== 路徑設定 =====
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


# ===== OpenAI 產生內容（回 JSON）=====
def generate_daily_english_ai(run_key: str, slot_label: str) -> dict:
    """
    用 OpenAI Responses API 產生每日英文內容，回傳 dict:
    { "en": str, "zh": str, "words": [str, ...] }
    """
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("缺少 OPENAI_API_KEY 環境變數")

    client = OpenAI(api_key=api_key)

    # 讓兩次內容更不一樣：上午偏「新句」，下午偏「複習/更短/更實用」
    extra_rule = (
        "上午（Morning）：產生一個新的日常/職場好用句。\n"
        "下午（Afternoon）：產生一個偏複習/提醒用的句子，句子更短更口語，可與上午不同情境。\n"
    )

    prompt = (
        f"你是一個英文教練。今天的執行識別是 {run_key}（{slot_label}）。\n"
        f"{extra_rule}\n"
        "請產生一則英文學習內容。\n"
        "句子情境必須來自『生活或日常對話』，例如：朋友聊天、家庭、購物、餐廳、通勤、興趣、休閒、手機或網路使用等。\n"
        "可以偶爾包含職場，但不要連續幾天都是工作情境。\n"
        "避免旅遊制式句（例如 Where is the station）。\n"
        "請讓主題在以下情境中自然輪替：\n"
        "生活聊天、天氣、吃飯、便利商店、運動、追劇、3C產品、家庭、週末活動、人際互動。\n"

    

        "要求：\n"
        "1) 英文句子自然口語、不要太長\n"
        "2) 繁體中文翻譯\n"
        "3) 2~4 個重點單字/片語（格式：word: 解釋）\n"
        "4) 僅回覆 JSON，不要加任何多餘文字\n\n"
        "JSON 格式：\n"
        "{\n"
        '  "en": "英文句子",\n'
        '  "zh": "繁體中文翻譯",\n'
        '  "words": ["word: 解釋", "phrase: 解釋"]\n'
        "}\n"
    )

    resp = client.responses.create(
        model="gpt-4.1-mini",
        input=prompt,
    )

    text = resp.output_text.strip()

    # 有時會不小心包 ```json ... ```，簡單去掉
    if text.startswith("```"):
        text = text.strip("`")
        text = text.replace("json", "", 1).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"AI 回傳不是合法 JSON：{e}\n原始回覆：\n{text}")

    en = str(data.get("en", "")).strip()
    zh = str(data.get("zh", "")).strip()
    words = data.get("words", [])
    if not isinstance(words, list):
        words = []

    if not en or not zh:
        raise ValueError(f"AI 回傳格式不完整：\n{text}")

    return {"en": en, "zh": zh, "words": [str(w).strip() for w in words if str(w).strip()]}


# ===== LINE 推播 =====
def send_line_message(message: str) -> None:
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    user_id = os.environ.get("LINE_USER_ID", "").strip()

    if not token or not user_id:
        raise RuntimeError("缺少 LINE_CHANNEL_ACCESS_TOKEN 或 LINE_USER_ID 環境變數")

    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {"to": user_id, "messages": [{"type": "text", "text": message}]}

    r = requests.post(url, headers=headers, json=payload, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"LINE 推播失敗：HTTP {r.status_code} - {r.text}")


# ===== 寫入 logs =====
def write_log(filename_key: str, data: dict, slot_label: str) -> str:
    ensure_dir(LOG_DIR)
    path = os.path.join(LOG_DIR, f"{filename_key}.md")

    en = data["en"]
    zh = data["zh"]
    words = data.get("words", [])

    lines: list[str] = []
    lines.append(f"# English Buddy - {filename_key} ({slot_label})\n\n")
    lines.append("## 今日一句\n")
    lines.append(f"- EN: **{en}**\n")
    lines.append(f"- ZH: {zh}\n\n")
    lines.append("## 重點單字\n")
    if words:
        for w in words:
            lines.append(f"- {w}\n")
    else:
        lines.append("-（本日未提供重點單字）\n")
    lines.append("\n---\n")
    lines.append("✅ 建議朗讀 3 次：慢 → 快 → 自然\n")

    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)

    return path


def main() -> None:
    ensure_dir(LOG_DIR)

    # 每天兩次：用 run_key 當檔名 key
    log_path = os.path.join(LOG_DIR, f"{run_key}.md")

    # 防洗版：同一 slot 已跑過就不再生成/推播
    if os.path.exists(log_path):
        print("本次 slot 今天已經產生過內容（logs 已存在），不再呼叫 AI / 不再推播。")
        print(f"📄 {log_path}")
        return

    # 1) AI 生成
    data = generate_daily_english_ai(run_key=run_key, slot_label=slot_label)

    # 2) 寫 logs
    saved = write_log(run_key, data, slot_label)
    print(f"EnglishBuddy running... {run_key}")
    print(f"EN: {data['en']}")
    print(f"\n✅ 已把今日內容存到：{saved}\n")

    # 3) LINE 推播（推播失敗不影響 logs）
    msg = f"EnglishBuddy｜{today}｜{slot_label}\nEN: {data['en']}\nZH: {data['zh']}"
    if data.get("words"):
        msg += "\n\nWords:\n" + "\n".join(f"- {w}" for w in data["words"])

    try:
        send_line_message(msg)
        print("✅ LINE 推播已送出")
    except Exception as e:
        print(f"⚠️ LINE 推播失敗（不影響 logs）：{e}")


if __name__ == "__main__":
    main()
