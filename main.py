import os
import logging
from flask import Flask, request, abort
from dotenv import load_dotenv

# LINE Bot SDK v3 のインポート
# 各クラスを具体的なパスから明示的にインポートすることで、将来のSDK変更に強くする
from linebot.v3.webhook import WebhookHandler # WebhookHandlerのインポートパス
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage as LineReplyTextMessage # Reply用のTextMessageにエイリアス
from linebot.v3.webhooks import MessageEvent, TextMessageContent # ここを修正しました！MessageEventとTextMessageContentをインポート
from linebot.v3.exceptions import InvalidSignatureError

import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

# ロギング設定
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
app = Flask(__name__)

# .envファイルから環境変数を読み込む（Renderでは不要だが、ローカル実行時のために残しておく）
load_dotenv()

# 環境変数からLINEとGeminiのAPIキーを取得
CHANNEL_ACCESS_TOKEN = os.getenv('CHANNEL_ACCESS_TOKEN')
CHANNEL_SECRET = os.getenv('CHANNEL_SECRET')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

# 環境変数が設定されているか確認
if not CHANNEL_ACCESS_TOKEN:
    logging.error("CHANNEL_ACCESS_TOKEN is not set.")
    raise ValueError("CHANNEL_ACCESS_TOKEN is not set. Please set it in Render Environment Variables.")
if not CHANNEL_SECRET:
    logging.error("CHANNEL_SECRET is not set.")
    raise ValueError("CHANNEL_SECRET is not set. Please set it in Render Environment Variables.")
if not GEMINI_API_KEY:
    logging.error("GEMINI_API_KEY is not set.")
    raise ValueError("GEMINI_API_KEY is not set. Please set it in Render Environment Variables.")

# LINE Messaging API v3 の設定
configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
line_bot_api = MessagingApi(ApiClient(configuration))
handler = WebhookHandler(CHANNEL_SECRET)

# Gemini API の設定
try:
    genai.configure(api_key=GEMINI_API_KEY)
    gemini_model = genai.GenerativeModel(
        'gemini-pro',
        safety_settings={
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        }
    )
    logging.info("Gemini API configured successfully.")
except Exception as e:
    logging.critical(f"Failed to configure Gemini API: {e}. Please check GEMINI_API_KEY.")
    raise Exception(f"Gemini API configuration failed: {e}")


@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        app.logger.error("Invalid signature. Check your channel access token/channel secret in LINE Developers and Render.")
        abort(400)
    except Exception as e:
        app.logger.error(f"Error handling webhook: {e}", exc_info=True)
        abort(500)

    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent) # ここを TextMessageContent に変更しました！
def handle_message(event):
    # event.message は TextMessageContent のインスタンスであると想定
    user_message = event.message.text
    app.logger.info(f"Received message from user: '{user_message}' (Reply Token: {event.reply_token})")

    response_text = "申し訳ありません、現在メッセージを処理できません。しばらくしてからもう一度お試しください。"

    try:
        gemini_response = gemini_model.generate_content(user_message)

        if hasattr(gemini_response, 'text'):
            response_text = gemini_response.text
        elif isinstance(gemini_response, list) and gemini_response:
            if hasattr(gemini_response[0], 'text'):
                response_text = gemini_response[0].text
            else:
                logging.warning(f"Gemini response is a list but first element has no 'text' attribute: {gemini_response}")
        else:
            logging.warning(f"Unexpected Gemini response format: {gemini_response}")
            response_text = "Geminiからの応答形式が予期せぬものでした。"

        app.logger.info(f"Gemini response: '{response_text}'")

    except Exception as e:
        logging.error(f"Error interacting with Gemini API: {e}", exc_info=True)
        response_text = "Geminiとの通信中にエラーが発生しました。時間を置いてお試しください。"

    finally:
        try:
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[LineReplyTextMessage(text=response_text)]
                )
            )
            app.logger.info("Reply sent to LINE successfully.")
        except Exception as e:
            logging.error(f"Error replying to LINE: {e}", exc_info=True)

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
