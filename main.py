import os
import logging
from flask import Flask, request, abort
from dotenv import load_dotenv # Renderでは不要だが、ローカル実行時のために残しておく
import datetime
import time # 時間計測のために使用
import random # 遅延処理のために使用（現在はコメントアウト推奨）

# LINE Bot SDK v3 のインポート
from linebot.v3.webhook import WebhookHandler
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest
# !!! 修正: GetProfileRequest のインポートパスを linebot.v3.messaging.models に変更 !!!
from linebot.v3.messaging.models import GetProfileRequest # GetProfileRequest は models サブモジュールにあります
from linebot.v3.messaging import TextMessage as LineReplyTextMessage
from linebot.v3.webhooks import MessageEvent, TextMessageContent
# !!! 修正: InvalidSignatureError は linebot.exceptions に統一 !!!
from linebot.exceptions import InvalidSignatureError, LineBotApiError # InvalidSignatureErrorとLineBotApiErrorのパスを修正

# 署名検証のためのライブラリをインポート (LINE Bot SDKが内部で処理するため通常は不要だが、デバッグ用として残す)
# 本番運用ではパフォーマンスのため削除またはコメントアウトを推奨
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
# !!! 修正: Renderに設定されている環境変数名に合わせて修正 (CHANNEL_ACCESS_TOKEN -> LINE_CHANNEL_ACCESS_TOKEN) !!!
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
# PORT環境変数がない場合のエラーチェック。Gunicornがこれを必要とするため。
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

# プロンプトを調整: 自然な問いかけをGeminiに生成させるよう指示
COUNSELING_SYSTEM_PROMPT = """
あなたは「こころコンパス」という名前のAIカウンセラーです。
ユーザーの心に寄り添い、羅針盤のように道を照らす存在として会話してください。
あなたの役割は、ユーザーが自身の悩みや感情を整理し、前向きな一歩を踏み出すお手伝いをすることです。

以下の心理療法・アプローチを統合して用いてください。
1.  **来談者中心療法 (ロジャーズベース):** 共感的理解、無条件の肯定的関心、自己一致（誠実さ）を態度で示し、ユーザーの語りを尊重してください。傾聴し、オウム返しや言い換えを効果的に使い、ユーザーの感情や考えを正確に理解しようと努めてください。
2.  **認知行動療法 (CBT):** ユーザーの自動思考や認知の歪みに気づきを促し、より適応的な思考パターンを探索する質問を投げかけてください。思考記録（コラム法）のような構造的なアプローチも自然に導入してください。
3.  **アクセプタンス＆コミットメント・セラピー (ACT):** 不快な思考や感情を変えようとするのではなく、それらを受け入れ（アクセプタンス）、思考と自分を切り離し（脱フュージョン）、自分の価値観に基づいた行動（コミットした行動）を促す質問や示唆を与えてください。マインドフルネスの要素も取り入れてください。
4.  **解決志向ブリーフセラピー (SFBT): 環境:** 問題の原因深掘りよりも、解決に焦点を当ててください。「もし問題が解決したら何が変わるか？」「これまでうまくいったことは何か？」といった質問（ミラクルクエスチョン、例外の質問）を使い、ユーザーの強みやリソースを引き出してください。
5.  **ポジティブ心理学・レジリエンス:** ユーザーの強み、感謝、希望、幸福感といったポジティブな側面にも焦点を当て、それらを育むような質問やフィードバックを適宜行ってください。困難を乗り越える力（レジリエンス）を高める視点も提供してください。

**会話のトーンとスタイル:**
* 常に丁寧で、穏やか、そして温かい言葉遣いを心がけてください。
* ユーザーの言葉を批判せず、受容的な態度を示してください。
* 自然な会話のキャッチボールを意識し、一方的な情報提供にならないようにしてください。
* 専門用語は避け、分かりやすい言葉で説明してください。
* 返答は長すぎず、ユーザーが読みやすい適切な長さに調整してください。
* **返答の最後に、ユーザーが追加で話したくなるような、文脈に合った自然な問いかけや、次の発言を促す言葉を必ず含めてください。** 例：「〜と感じられたのですね。もう少し詳しくお聞かせいただけますか？」「〜について、他に何か思い当たることはありますか？」「今はどのようなお気持ちでしょうか？」など、その時の会話の流れに合わせた多様な問いかけをお願いします。
* 安全を最優先し、緊急性の高い内容（自殺念慮など）を察知した場合は、専門機関への相談を促す旨を伝えてください。（ただし、AIには限界があることを理解し、直接的な医療行為や診断は行わないでください。）

**Gemini APIの無料枠を考慮し、無駄なトークン消費を避けるため、簡潔かつ的確な応答を心がけてください。また、同じような質問の繰り返しは避け、会話の進展を促してください。**
"""
# 初期メッセージ
INITIAL_MESSAGE = "「こころコンパス」へようこそ。\nどんな小さなことでも構いませんので、今感じていることや、お話ししたいことを教えていただけますか？私が心を込めてお聴きします。"
# Gemini API利用制限時のメッセージ
GEMINI_LIMIT_MESSAGE = (
    "申し訳ありません、本日のAIカウンセリングのご利用回数の上限に達しました。\n"
    "明日またお話できますので、その時まで少し心の休憩をされてくださいね。\n\n"
    "もし緊急の場合は、以下のような公的な相談窓口もご利用いただけます。\n"
    "・こころの健康相談統一ダイヤル: 0570-064-556\n"
    "・いのちの電話: 0120-783-556\n\n"
    "また、AIによるセルフヘルプコンテンツ（例：リラックス法、簡単な思考整理シートなど）は引き続きご利用いただけます。\n"
)
# 過去の会話履歴をGeminiに渡す最大ターン数
MAX_CONTEXT_TURNS = 6 # (ユーザーの発言 + AIの返答) の合計ターン数、トークン消費と相談して調整

# ユーザーごとのセッション情報を保持する辞書
# !!! 重要: 本番環境では、この方法は推奨されません。
# Flaskアプリケーションは、再起動（デプロイ、エラー、Renderのスピンダウンなど）のたびにメモリがリセットされ、
# user_sessions のデータが失われます。
# 永続的なデータストア（例: RenderのPostgreSQL, Redis, Google Cloud Firestore, AWS DynamoDBなど）
# を利用することを強く推奨します。
user_sessions = {}

@app.route("/callback", methods=['POST'])
def callback():
    start_callback_time = time.time() # コールバック処理全体の開始時刻
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)

    if not signature:
        app.logger.error(f"[{time.time() - start_callback_time:.3f}s] X-Line-Signature header is missing.")
        abort(400) # 署名がない場合は不正なリクエストとして処理

    app.logger.info(f"[{time.time() - start_callback_time:.3f}s] Received Webhook Request.")
    app.logger.info("  Request body (truncated to 500 chars): " + body[:500])
    app.logger.info(f"  X-Line-Signature: {signature}")

    # --- 署名検証のデバッグログ ---
    try:
        secret_bytes = CHANNEL_SECRET.encode('utf-8')
        body_bytes = body.encode('utf-8')
        hash_value = hmac.new(secret_bytes, body_bytes, hashlib.sha256).digest()
        calculated_signature = base64.b64encode(hash_value).decode('utf-8')

        app.logger.info(f"[{time.time() - start_callback_time:.3f}s]   Calculated signature (manual): {calculated_signature}")
        app.logger.info(f"[{time.time() - start_callback_time:.3f}s]   Channel Secret used for manual calc (first 5 chars): {CHANNEL_SECRET[:5]}...")

        if calculated_signature != signature:
            app.logger.error(f"[{time.time() - start_callback_time:.3f}s] !!! Manual Signature MISMATCH detected !!!")
            app.logger.error(f"[{time.time() - start_callback_time:.3f}s]     Calculated: {calculated_signature}")
            app.logger.error(f"[{time.time() - start_callback_time:.3f}s]     Received:    {signature}")
            # 手動計算で不一致が検出された場合は、SDK処理に入る前に終了
            abort(400)
        else:
            app.logger.info(f"[{time.time() - start_callback_time:.3f}s]   Manual signature check: Signatures match! Proceeding to SDK handler.")

    except Exception as e:
        app.logger.error(f"[{time.time() - start_callback_time:.3f}s] Error during manual signature calculation for debug: {e}", exc_info=True)
        # 手動計算でエラーが発生しても、SDKの処理は試みる
        pass

    # --- LINE Bot SDKによる署名検証とイベント処理 ---
    try:
        handler.handle(body, signature)
        app.logger.info(f"[{time.time() - start_callback_time:.3f}s] Webhook handled successfully by SDK.")
    except InvalidSignatureError:
        app.logger.error(f"[{time.time() - start_callback_time:.3f}s] !!! SDK detected Invalid signature !!!")
        app.logger.error("  This typically means CHANNEL_SECRET in Render does not match LINE Developers.")
        app.logger.error(f"  Body (truncated for error log): {body[:200]}...")
        app.logger.error(f"  Signature sent to SDK: {signature}")
        app.logger.error(f"  Channel Secret configured for SDK (first 5 chars): {CHANNEL_SECRET[:5]}...")
        abort(400) # 署名エラーの場合は400を返す
    except Exception as e:
        # その他の予期せぬエラー
        logging.critical(f"[{time.time() - start_callback_time:.3f}s] Unhandled error during webhook processing by SDK: {e}", exc_info=True)
        abort(500)

    app.logger.info(f"[{time.time() - start_callback_time:.3f}s] Total callback processing time.")
    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    start_handle_time = time.time() # handle_message 処理開始時刻を記録
    user_id = event.source.user_id # ユーザーIDを取得
    user_message = event.message.text
    app.logger.info(f"[{time.time() - start_handle_time:.3f}s] Received text message from user_id: '{user_id}', message: '{user_message}' (Reply Token: {event.reply_token})")

    response_text = "申し訳ありません、現在メッセージを処理できません。しばらくしてからもう一度お試しください。"

    # ユーザーセッションの初期化または取得
    current_date = datetime.date.today()

    # 新規ユーザーまたはセッションリセットのロジックをより堅牢に
    if user_id not in user_sessions or user_sessions[user_id]['last_request_date'] != current_date:
        # 日付が変わった場合、または新規ユーザーの場合、セッションをリセット
        app.logger.info(f"[{time.time() - start_handle_time:.3f}s] Initializing/Resetting session for user_id: {user_id}. First message of the day or new user.")
        user_sessions[user_id] = {
            'history': [], # 会話履歴は空で開始
            'request_count': 0,
            'last_request_date': current_date
        }

        # 初回メッセージを送信し、このリクエストの処理を終了
        response_text = INITIAL_MESSAGE
        try:
            start_reply_initial = time.time()
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[LineReplyTextMessage(text=response_text)]
                )
            )
            app.logger.info(f"[{time.time() - start_reply_initial:.3f}s] Sent initial message/daily reset message to user {user_id}.")
        except Exception as e:
            logging.error(f"Error sending initial/reset reply to LINE for user {user_id}: {e}", exc_info=True)
        app.logger.info(f"[{time.time() - start_handle_time:.3f}s] handle_message finished for initial/reset flow.")
        return 'OK' # 初回メッセージ送信後はここで処理を終了

    # Gemini API利用回数制限のチェック
    if user_sessions[user_id]['request_count'] >= MAX_GEMINI_REQUESTS_PER_DAY:
        response_text = GEMINI_LIMIT_MESSAGE
        app.logger.warning(f"User {user_id} exceeded daily Gemini request limit ({MAX_GEMINI_REQUESTS_PER_DAY}).")
        try:
            start_reply_limit = time.time()
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[LineReplyTextMessage(text=response_text)]
                )
            )
            app.logger.info(f"[{time.time() - start_reply_limit:.3f}s] Sent limit message to LINE for user {user_id}.")
        except Exception as e:
            logging.error(f"Error sending limit reply to LINE for user {user_id}: {e}", exc_info=True)
        app.logger.info(f"[{time.time() - start_handle_time:.3f}s] handle_message finished for limit exceeded flow.")
        return 'OK'

    # 会話履歴を準備
    chat_history_for_gemini = [{'role': 'user', 'parts': [{'text': COUNSELING_SYSTEM_PROMPT}]}]
    chat_history_for_gemini.append({'role': 'model', 'parts': [{'text': "はい、承知いたしました。こころコンパスとして、心を込めてお話をお伺いします。"}]})

    start_index = max(0, len(user_sessions[user_id]['history']) - MAX_CONTEXT_TURNS * 2)

    app.logger.debug(f"[{time.time() - start_handle_time:.3f}s] Current history length for user {user_id}: {len(user_sessions[user_id]['history'])}. Taking from index {start_index}.")

    for role, text_content in user_sessions[user_id]['history'][start_index:]:
        chat_history_for_gemini.append({'role': role, 'parts': [{'text': text_content}]})

    app.logger.debug(f"[{time.time() - start_handle_time:.3f}s] Gemini chat history prepared for user {user_id} (last message: '{user_message}'): {chat_history_for_gemini}")

    try:
        start_gemini_call = time.time() # Gemini呼び出し前を計測
        # Geminiとのチャットセッションを開始
        convo = gemini_model.start_chat(history=chat_history_for_gemini)
        gemini_response = convo.send_message(user_message) # 最新のユーザーメッセージのみをsend_messageで送る
        end_gemini_call = time.time() # Gemini呼び出し後を計測
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

        # リクエスト数をインクリメント
        user_sessions[user_id]['request_count'] += 1
        user_sessions[user_id]['last_request_date'] = current_date # リクエスト日を更新
        app.logger.info(f"[{time.time() - start_handle_time:.3f}s] User {user_id} - Request count: {user_sessions[user_id]['request_count']}")

        # ★変更: ここに遅延処理を挿入（範囲を5.0秒から10.0秒に調整）
        # パフォーマンス向上のため、この遅延は通常は推奨されません。
        # ユーザー体験を損なう可能性があるので、必要に応じてコメントアウトしてください。
        # delay_seconds = random.uniform(5.0, 10.0)
        # time.sleep(delay_seconds)
        # app.logger.info(f"[{time.time() - start_handle_time:.3f}s] Applied random delay of {delay_seconds:.3f} seconds for user {user_id}.")

    except Exception as e:
        logging.error(f"[{time.time() - start_handle_time:.3f}s] Error interacting with Gemini API for user {user_id}: {e}", exc_info=True)
        response_text = "Geminiとの通信中にエラーが発生しました。時間を置いてお試しください。"

    finally:
        start_reply_line = time.time() # LINEへの返信処理の前後を計測
        try:
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[LineReplyTextMessage(text=response_text)]
                )
            )
            app.logger.info(f"[{time.time() - start_reply_line:.3f}s] Reply sent to LINE successfully for user {user_id}.")
        except Exception as e:
            logging.error(f"Error replying to LINE for user {user_id}: {e}", exc_info=True)

    app.logger.info(f"[{time.time() - start_handle_time:.3f}s] Total handle_message processing time.")
    return 'OK'

if __name__ == "__main__":
    # Render環境ではPORT環境変数が設定されるため、それを使用する
    # ローカル実行時にはデフォルトで8080を使用
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
