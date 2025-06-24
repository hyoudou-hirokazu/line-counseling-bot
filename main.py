import os
import logging
from flask import Flask, request, abort
from dotenv import load_dotenv

# LINE Bot SDK v3 のインポート
# 各クラスを具体的なパスから明示的にインポートすることで、将来のSDK変更に強くする
# TextMessageのインポートパスは、過去のバージョンでの変更履歴を考慮し、
# linebot.v3.messaging と linebot.v3.webhooks.models からのimportを両方試すようにコメントで示唆。
# 現在のコードでは linebot.v3.messaging.TextMessage を使用
from linebot.v3.webhook import WebhookHandler
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest
from linebot.v3.messaging import TextMessage as LineReplyTextMessage # LINEへの返信用テキストメッセージ
from linebot.v3.webhooks import MessageEvent, TextMessageContent # 受信イベントのメッセージコンテンツ

# 署名検証のためのライブラリをインポート
import hmac
import hashlib
import base64

# Google Generative AI SDK のインポート
import google.generativeai as genai
# HarmCategoryの属性名変更に対応するため、HarmCategoryとHarmBlockThresholdを明示的にインポート
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
            # HarmCategoryの属性名が変更された場合に対応 (例: HARASSMENT -> HARM_CATEGORY_HARASSMENT)
            # 現在のSDKバージョンに合わせて修正済み
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        }
    )
    logging.info("Gemini API configured successfully.")
except Exception as e:
    # Gemini APIの設定失敗時はアプリケーションを起動させない
    logging.critical(f"Failed to configure Gemini API: {e}. Please check GEMINI_API_KEY and google-generativeai version.")
    raise Exception(f"Gemini API configuration failed: {e}")


@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    app.logger.info("Request body (truncated to 500 chars): " + body[:500]) # 長いボディは切り詰める
    app.logger.info("X-Line-Signature: " + signature)

    # Bot側で計算した署名とLINEから送られてきた署名を比較するためのデバッグログ
    # Invalid signatureエラーのデバッグ用に残しておく
    try:
        secret_bytes = CHANNEL_SECRET.encode('utf-8')
        body_bytes = body.encode('utf-8')
        hash_value = hmac.new(secret_bytes, body_bytes, hashlib.sha256).digest()
        calculated_signature = base64.b64encode(hash_value).decode('utf-8')
        
        app.logger.info(f"Calculated signature (manual): {calculated_signature}")
        app.logger.info(f"Received signature (from header): {signature}")

        if calculated_signature != signature:
            app.logger.error("!!! Manual Signature MISMATCH detected !!!")
            app.logger.error(f"  Calculated: {calculated_signature}")
            app.logger.error(f"  Received:   {signature}")
            app.logger.error(f"  Channel Secret used for manual calc (first 5 chars): {CHANNEL_SECRET[:5]}...") # シークレット全体はログに出さない
            # 手動計算で不一致が検出された場合は、SDK処理前に終了
            abort(400) 
        else:
            app.logger.info("Manual signature check: Signatures match! Proceeding to SDK handler.")

    except Exception as e:
        app.logger.error(f"Error during manual signature calculation for debug: {e}", exc_info=True)
        # 手動計算でエラーが発生しても、SDKの処理は試みる
        pass

    try:
        # LINE Bot SDKのハンドラーを使って署名を検証し、イベントを処理
        handler.handle(body, signature)
        app.logger.info("Webhook handled successfully by SDK.") # SDKが正常処理した場合のログ
    except InvalidSignatureError:
        app.logger.error("!!! SDK detected Invalid signature !!!")
        app.logger.error("  Please check your channel access token/channel secret in LINE Developers and Render.")
        app.logger.error(f"  Body (truncated for error log): {body[:200]}...")
        app.logger.error(f"  Signature sent to SDK: {signature}")
        app.logger.error(f"  Channel Secret configured for SDK (first 5 chars): {CHANNEL_SECRET[:5]}...")
        abort(400) # 署名エラーの場合は400を返す
    except Exception as e:
        # その他の予期せぬエラー
        logging.critical(f"Unhandled error during webhook processing: {e}", exc_info=True)
        abort(500)

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
            # Geminiがリスト形式で応答を返す可能性も考慮
            if hasattr(gemini_response[0], 'text'):
                response_text = gemini_response[0].text
            else:
                logging.warning(f"Gemini response is a list but first element has no 'text' attribute: {gemini_response}")
        else:
            logging.warning(f"Unexpected Gemini response format: {gemini_response}")
            response_text = "Geminiからの応答形式が予期せぬものでした。"

        app.logger.info(f"Gemini response: '{response_text}'")

    except Exception as e:
        # Gemini APIとの通信エラーをログに記録し、ユーザーにエラーを通知
        logging.error(f"Error interacting with Gemini API: {e}", exc_info=True)
        response_text = "Geminiとの通信中にエラーが発生しました。時間を置いてお試しください。"

    finally:
        # 最終的にLINEに返信する
        try:
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[LineReplyTextMessage(text=response_text)]
                )
            )
            app.logger.info("Reply sent to LINE successfully.")
        except Exception as e:
            # LINEへの返信失敗もログに記録
            logging.error(f"Error replying to LINE: {e}", exc_info=True)

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    # Flaskのデバッグモードは本番環境では無効にするべきだが、
    # RenderはGunicornなどで起動するため、ここでは直接app.runは本番環境では使われない。
    # app.run(host='0.0.0.0', port=port, debug=True) # デバッグ時はTrue
    app.run(host='0.0.0.0', port=port)
