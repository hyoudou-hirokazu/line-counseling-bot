import os
from flask import Flask, request, abort
from dotenv import load_dotenv

# --- 変更点ここから ---
from linebot.v3.webhooks import WebhookHandler
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage, WebhookEndpoint
from linebot.v3.exceptions import InvalidSignatureError
# --- 変更点ここまで ---

import google.generativeai as genai

load_dotenv()

app = Flask(__name__)

# 環境変数からLINEとGeminiのAPIキーを取得
CHANNEL_ACCESS_TOKEN = os.getenv('CHANNEL_ACCESS_TOKEN')
CHANNEL_SECRET = os.getenv('CHANNEL_SECRET')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

# 環境変数が設定されているか確認
if not CHANNEL_ACCESS_TOKEN:
    raise ValueError("CHANNEL_ACCESS_TOKEN is not set.")
if not CHANNEL_SECRET:
    raise ValueError("CHANNEL_SECRET is not set.")
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY is not set.")

# --- 変更点ここから ---
# LINE Messaging API の設定
configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
line_bot_api = MessagingApi(ApiClient(configuration))
# --- 変更点ここまで ---

handler = WebhookHandler(CHANNEL_SECRET)

# Gemini API の設定
genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel('gemini-pro')

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        app.logger.error("Invalid signature. Check your channel access token/channel secret.")
        abort(400)
    except Exception as e:
        app.logger.error(f"Error handling webhook: {e}")
        abort(500)

    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_message = event.message.text
    app.logger.info(f"Received message from user: {user_message}")

    try:
        # Geminiにメッセージを送信
        gemini_response = gemini_model.generate_content(user_message)
        response_text = gemini_response.text
        app.logger.info(f"Gemini response: {response_text}")

        # LINEに返信
        # --- 変更点ここから ---
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=response_text)]
            )
        )
        # --- 変更点ここまで ---

    except Exception as e:
        app.logger.error(f"Error generating Gemini response or replying: {e}")
        # エラーが発生した場合もユーザーに何らかの応答をする
        # --- 変更点ここから ---
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text="申し訳ありません、現在メッセージを処理できません。しばらくしてからもう一度お試しください。")]
            )
        )
        # --- 変更点ここまで ---

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
