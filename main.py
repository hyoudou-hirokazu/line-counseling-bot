import os
import logging
from flask import Flask, request, abort
from dotenv import load_dotenv
import datetime
import time
import random
import threading # 非同期処理のためにthreadingをインポート

# LINE Bot SDK v3 のインポート
from linebot.v3.webhook import WebhookHandler
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest
from linebot.v3.messaging.models import GetProfileRequest # GetProfileRequest は models サブモジュールにあります
from linebot.v3.messaging import TextMessage as LineReplyTextMessage
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.exceptions import InvalidSignatureError, LineBotApiError

# 署名検証のためのライブラリをインポート (デバッグ用)
import hmac
import hashlib
import base64

# Google Generative AI SDK のインポート
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

# ロギング設定
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
app = Flask(__name__)

# .envファイルから環境変数を読み込む（Renderでは不要だが、ローカル実行時のために残しておく）
load_dotenv()

# 環境変数からLINEとGeminiのAPIキーを取得
CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

# 環境変数が設定されているか確認
if not CHANNEL_ACCESS_TOKEN:
    logging.critical("LINE_CHANNEL_ACCESS_TOKEN is not set in environment variables.")
    raise ValueError("LINE_CHANNEL_ACCESS_TOKEN is not set. Please set it in Render Environment Variables.")
if not CHANNEL_SECRET:
    logging.critical("LINE_CHANNEL_SECRET is not set in environment variables.")
    raise ValueError("LINE_CHANNEL_SECRET is not set. Please set it in Render Environment Variables.")
if not GEMINI_API_KEY:
    logging.critical("GEMINI_API_KEY is not set in environment variables.")
    raise ValueError("GEMINI_API_KEY is not set. Please set it in Render Environment Variables.")
if not os.getenv('PORT'):
    logging.critical("PORT environment variable is not set by Render. This is unexpected for a Web Service.")
    raise ValueError("PORT environment variable is not set. Ensure this is deployed on a platform like Render.")

# LINE Messaging API v3 の設定
try:
    configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
    line_bot_api = MessagingApi(ApiClient(configuration))
    handler = WebhookHandler(CHANNEL_SECRET)
    logging.info("LINE Bot SDK configured successfully.")
except Exception as e:
    logging.critical(f"Failed to configure LINE Bot SDK: {e}. Please check LINE_CHANNEL_ACCESS_TOKEN and LINE_CHANNEL_SECRET.")
    raise Exception(f"LINE Bot SDK configuration failed: {e}")

# Gemini API の設定
try:
    genai.configure(api_key=GEMINI_API_KEY)
    gemini_model = genai.GenerativeModel(
        'gemini-2.5-flash-lite-preview-06-17', # ユーザー指定のモデル名
        safety_settings={
            HarmCategory.HARMS_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARMS_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARMS_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARMS_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        }
    )
    logging.info("Gemini API configured successfully using 'gemini-2.5-flash-lite-preview-06-17' model.")
except Exception as e:
    logging.critical(f"Failed to configure Gemini API: {e}. Please check GEMINI_API_KEY and 'google-generativeai' library version in requirements.txt. Also ensure 'gemini-2.5-flash-lite-preview-06-17' model is available for your API Key/Region.")
    raise Exception(f"Gemini API configuration failed: {e}")

# --- カウンセリング関連の設定 ---
MAX_GEMINI_REQUESTS_PER_DAY = 20    # 1ユーザーあたり1日20回まで (無料枠考慮)

COUNSELING_SYSTEM_PROMPT = """
あなたは「こころコンパス」というAIカウンセラーです。ユーザーの心に寄り添い、羅針盤のように道を照らす存在として会話してください。
ユーザーが悩みや感情を整理し、前向きな一歩を踏み出す手助けがあなたの役割です。

以下の心理療法・アプローチを統合して用いてください:
1.  **来談者中心療法:** 共感的に傾聴し、ユーザーの語りを尊重。オウム返しや言い換えで理解を示す。
2.  **認知行動療法:** 自動思考や認知の歪みに気づきを促し、適応的思考を探索する質問を投げかける。
3.  **アクセプタンス＆コミットメント・セラピー (ACT):** 不快な思考や感情を受け入れ、価値観に基づいた行動を促す。マインドフルネス要素も取り入れる。
4.  **解決志向ブリーフセラピー (SFBT):** 問題の原因より解決に焦点を当て、「もし解決したら何が変わるか？」等の質問でユーザーの強みやリソースを引き出す。
5.  **ポジティブ心理学・レジリエンス:** 強み、感謝、希望に焦点を当て、困難を乗り越える力を高める視点を提供する。

**会話のトーンとスタイル:**
* 丁寧、穏やか、温かい言葉遣い。批判せず受容的な態度。
* 自然な会話を意識し、一方的にならない。専門用語は避け、分かりやすく。
* 返答は簡潔に適切な長さに調整。
* **返答の最後に、ユーザーが追加で話したくなるような、文脈に合った自然な問いかけや次の発言を促す言葉を必ず含めてください。** 例：「〜と感じられたのですね。もう少し詳しくお聞かせいただけますか？」「今はどのようなお気持ちでしょうか？」
* 緊急性の高い内容（自殺念慮など）を察知した場合は、専門機関への相談を促す旨を伝えてください。（AIの限界を理解し、直接的な医療行為や診断は行わないでください。）

Gemini APIのトークン消費を避けるため、簡潔かつ的確な応答を心がけ、同じ質問の繰り返しは避けて会話の進展を促してください。
"""
INITIAL_MESSAGE = "「こころコンパス」へようこそ。\nどんな小さなことでも構いませんので、今感じていることや、お話ししたいことを教えていただけますか？私が心を込めてお聴きします。"
GEMINI_LIMIT_MESSAGE = (
    "申し訳ありません、本日のAIカウンセリングのご利用回数の上限に達しました。\n"
    "明日またお話できますので、その時まで少し心の休憩をされてくださいね。\n\n"
    "もし緊急の場合は、以下のような公的な相談窓口もご利用いただけます。\n"
    "・こころの健康相談統一ダイヤル: 0570-064-556\n"
    "・いのちの電話: 0120-783-556\n\n"
    "また、AIによるセルフヘルプコンテンツ（例：リラックス法、簡単な思考整理シートなど）は引き続きご利用いただけます。\n"
)
MAX_CONTEXT_TURNS = 6 # (ユーザーの発言 + AIの返答) の合計ターン数、トークン消費と相談して調整

user_sessions = {}

# LINEへの返信を非同期で行う関数
def deferred_reply(reply_token, messages_to_send, user_id, start_time):
    try:
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=messages_to_send
            )
        )
        app.logger.info(f"[{time.time() - start_time:.3f}s] Deferred reply sent to LINE successfully for user {user_id}.")
    except Exception as e:
        app.logger.error(f"Error sending deferred reply to LINE for user {user_id}: {e}", exc_info=True)

@app.route("/callback", methods=['POST'])
def callback():
    start_callback_time = time.time()
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)

    if not signature:
        app.logger.error(f"[{time.time() - start_callback_time:.3f}s] X-Line-Signature header is missing.")
        abort(400)

    app.logger.info(f"[{time.time() - start_callback_time:.3f}s] Received Webhook Request.")
    app.logger.info("  Request body (truncated to 500 chars): " + body[:500])
    app.logger.info(f"  X-Line-Signature: {signature}")

    # --- 署名検証のデバッグログ（本番運用では不要であればコメントアウトまたは削除） ---
    try:
        secret_bytes = CHANNEL_SECRET.encode('utf-8')
        body_bytes = body.encode('utf-8')
        hash_value = hmac.new(secret_bytes, body_bytes, hashlib.sha256).digest()
        calculated_signature = base64.b64encode(hash_value).decode('utf-8')
        if calculated_signature != signature:
            app.logger.error(f"[{time.time() - start_callback_time:.3f}s] !!! Manual Signature MISMATCH detected !!!")
            abort(400)
    except Exception as e:
        app.logger.error(f"[{time.time() - start_callback_time:.3f}s] Error during manual signature calculation for debug: {e}", exc_info=True)

    # --- LINE Bot SDKによる署名検証とイベント処理 ---
    try:
        handler.handle(body, signature)
        app.logger.info(f"[{time.time() - start_callback_time:.3f}s] Webhook handled successfully by SDK.")
    except InvalidSignatureError:
        app.logger.error(f"[{time.time() - start_callback_time:.3f}s] !!! SDK detected Invalid signature !!!")
        abort(400)
    except Exception as e:
        logging.critical(f"[{time.time() - start_callback_time:.3f}s] Unhandled error during webhook processing by SDK: {e}", exc_info=True)
        abort(500)

    app.logger.info(f"[{time.time() - start_callback_time:.3f}s] Total callback processing time.")
    return 'OK' # ここで即座にOKを返す

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    start_handle_time = time.time()
    user_id = event.source.user_id
    user_message = event.message.text
    app.logger.info(f"[{time.time() - start_handle_time:.3f}s] handle_message received for user_id: '{user_id}', message: '{user_message}' (Reply Token: {event.reply_token})")

    # 返信用メッセージの初期化
    messages_to_send = []
    
    current_date = datetime.date.today()

    # セッションの初期化または取得
    if user_id not in user_sessions or user_sessions[user_id]['last_request_date'] != current_date:
        app.logger.info(f"[{time.time() - start_handle_time:.3f}s] Initializing/Resetting session for user_id: {user_id}. First message of the day or new user.")
        user_sessions[user_id] = {
            'history': [],
            'request_count': 0,
            'last_request_date': current_date
        }
        response_text = INITIAL_MESSAGE
        messages_to_send.append(LineReplyTextMessage(text=response_text))
        
        # 非同期でLINEに返信
        threading.Thread(target=deferred_reply, args=(event.reply_token, messages_to_send, user_id, start_handle_time)).start()
        app.logger.info(f"[{time.time() - start_handle_time:.3f}s] handle_message finished for initial/reset flow (deferred reply).")
        return 'OK'

    # Gemini API利用回数制限のチェック
    if user_sessions[user_id]['request_count'] >= MAX_GEMINI_REQUESTS_PER_DAY:
        response_text = GEMINI_LIMIT_MESSAGE
        app.logger.warning(f"User {user_id} exceeded daily Gemini request limit ({MAX_GEMINI_REQUESTS_PER_DAY}).")
        messages_to_send.append(LineReplyTextMessage(text=response_text))

        # 非同期でLINEに返信
        threading.Thread(target=deferred_reply, args=(event.reply_token, messages_to_send, user_id, start_handle_time)).start()
        app.logger.info(f"[{time.time() - start_handle_time:.3f}s] handle_message finished for limit exceeded flow (deferred reply).")
        return 'OK'

    # 会話履歴を準備
    chat_history_for_gemini = [{'role': 'user', 'parts': [{'text': COUNSELING_SYSTEM_PROMPT}]}]
    chat_history_for_gemini.append({'role': 'model', 'parts': [{'text': "はい、承知いたしました。こころコンパスとして、心を込めてお話をお伺いします。"}]})

    start_index = max(0, len(user_sessions[user_id]['history']) - MAX_CONTEXT_TURNS * 2)
    app.logger.debug(f"[{time.time() - start_handle_time:.3f}s] Current history length for user {user_id}: {len(user_sessions[user_id]['history'])}. Taking from index {start_index}.")

    for role, text_content in user_sessions[user_id]['history'][start_index:]:
        chat_history_for_gemini.append({'role': role, 'parts': [{'text': text_content}]})

    app.logger.debug(f"[{time.time() - start_handle_time:.3f}s] Gemini chat history prepared for user {user_id} (last message: '{user_message}'): {chat_history_for_gemini}")

    response_text = "申し訳ありません、現在メッセージを処理できません。しばらくしてからもう一度お試しください。" # デフォルトエラーメッセージ

    try:
        start_gemini_call = time.time()
        convo = gemini_model.start_chat(history=chat_history_for_gemini)
        gemini_response = convo.send_message(user_message)
        end_gemini_call = time.time()
        app.logger.info(f"[{end_gemini_call - start_gemini_call:.3f}s] Gemini API call completed for user {user_id}.")

        if gemini_response and hasattr(gemini_response, 'text'):
            response_text = gemini_response.text
        elif isinstance(gemini_response, list) and gemini_response and hasattr(gemini_response[0], 'text'):
            response_text = gemini_response[0].text
        else:
            logging.warning(f"[{time.time() - start_handle_time:.3f}s] Unexpected Gemini response format or no text content: {gemini_response}")
            response_text = "Geminiからの応答形式が予期せぬものでした。"

        app.logger.info(f"[{time.time() - start_handle_time:.3f}s] Gemini generated response for user {user_id}: '{response_text}'")

        # 会話履歴を更新
        user_sessions[user_id]['history'].append(['user', user_message])
        user_sessions[user_id]['history'].append(['model', response_text])
        user_sessions[user_id]['request_count'] += 1
        user_sessions[user_id]['last_request_date'] = current_date
        app.logger.info(f"[{time.time() - start_handle_time:.3f}s] User {user_id} - Request count: {user_sessions[user_id]['request_count']}")

    except Exception as e:
        logging.error(f"[{time.time() - start_handle_time:.3f}s] Error interacting with Gemini API for user {user_id}: {e}", exc_info=True)
        response_text = "Geminiとの通信中にエラーが発生しました。時間を置いてお試しください。"

    finally:
        messages_to_send.append(LineReplyTextMessage(text=response_text))
        # 非同期でLINEに返信
        threading.Thread(target=deferred_reply, args=(event.reply_token, messages_to_send, user_id, start_handle_time)).start()

    app.logger.info(f"[{time.time() - start_handle_time:.3f}s] handle_message finished (deferred reply).")
    return 'OK'

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
