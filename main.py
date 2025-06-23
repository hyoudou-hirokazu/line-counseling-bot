import os
import logging
from flask import Flask, request, abort
from dotenv import load_dotenv

# LINE Bot SDK v3 のインポート
# 各クラスを具体的なパスから明示的にインポートすることで、将来のSDK変更に強くする
from linebot.v3.webhook import WebhookHandler
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage as LineReplyTextMessage
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.exceptions import InvalidSignatureError

# 署名検証のためのライブラリをインポート
import hmac
import hashlib
import base64

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
            HarmCategory.HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
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
    app.logger.info("X-Line-Signature: " + signature)

    # Bot側で計算した署名とLINEから送られてきた署名を比較するためのデバッグログ
    # ※このブロックは、SDKがなぜエラーを出すのかを特定するために一時的に追加しています。
    # 署名が一致しているにも関わらずエラーが出る場合は、SDKの内部挙動を疑う必要があります。
    calculated_signature = ""
    try:
        secret_bytes = CHANNEL_SECRET.encode('utf-8')
        body_bytes = body.encode('utf-8')
        hash_value = hmac.new(secret_bytes, body_bytes, hashlib.sha256).digest()
        calculated_signature = base64.b64encode(hash_value).decode('utf-8')
        
        app.logger.info(f"Calculated signature (manual): {calculated_signature}")
        app.logger.info(f"Received signature (from header): {signature}") # ログ名をより明確に

        # ここで直接比較し、もし不一致ならSDKのエラー発生前にabort
        if calculated_signature != signature:
            app.logger.error("!!! Manual Signature MISMATCH detected !!!")
            app.logger.error(f"  Calculated: {calculated_signature}")
            app.logger.error(f"  Received:   {signature}")
            app.logger.error(f"  Channel Secret used for manual calc: {CHANNEL_SECRET}")
            abort(400) # 明示的に400を返す
        else:
            app.logger.info("Manual signature check: Signatures match! Proceeding to SDK handler.")

    except Exception as e:
        app.logger.error(f"Error during manual signature calculation for debug: {e}", exc_info=True)
        # 手動計算でエラーが発生しても、SDKの処理は試みる
        pass

    try:
        # LINE Bot SDKのハンドラーを使って署名を検証し、イベントを処理
        # ここで InvalidSignatureError が出る場合、SDKの内部、または環境固有の問題の可能性が高い
        handler.handle(body, signature)
        app.logger.info("Webhook handled successfully by SDK.") # SDKが正常処理した場合のログ
    except InvalidSignatureError:
        app.logger.error("!!! SDK detected Invalid signature !!!")
        app.logger.error("  Please check your channel access token/channel secret in LINE Developers and Render.")
        app.logger.error(f"  Body (truncated for error log): {body[:200]}...")
        app.logger.error(f"  Signature sent to SDK: {signature}")
        app.logger.error(f"  Channel Secret configured for SDK: {CHANNEL_SECRET}")
        abort(400) # 署名エラーの場合は400を返す
    except Exception as e:
        app.logger.error(f"Error handling webhook with SDK: {e}", exc_info=True)
        abort(500) # その他のエラーの場合は500を返す

    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
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
