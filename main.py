import os
import logging
from flask import Flask, request, abort
from dotenv import load_dotenv
import datetime # 日付/時刻を扱うために追加

# LINE Bot SDK v3 のインポート
from linebot.v3.webhook import WebhookHandler
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest
from linebot.v3.messaging import TextMessage as LineReplyTextMessage
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.exceptions import InvalidSignatureError

# 署名検証のためのライブラリをインポート
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
CHANNEL_ACCESS_TOKEN = os.getenv('CHANNEL_ACCESS_TOKEN')
CHANNEL_SECRET = os.getenv('CHANNEL_SECRET')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

# 環境変数が設定されているか確認
if not CHANNEL_ACCESS_TOKEN:
    logging.critical("CHANNEL_ACCESS_TOKEN is not set in environment variables.")
    raise ValueError("CHANNEL_ACCESS_TOKEN is not set. Please set it in Render Environment Variables.")
if not CHANNEL_SECRET:
    logging.critical("CHANNEL_SECRET is not set in environment variables.")
    raise ValueError("CHANNEL_SECRET is not set. Please set it in Render Environment Variables.")
if not GEMINI_API_KEY:
    logging.critical("GEMINI_API_KEY is not set in environment variables.")
    raise ValueError("GEMINI_API_KEY is not set. Please set it in Render Environment Variables.")

# LINE Messaging API v3 の設定
try:
    configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
    line_bot_api = MessagingApi(ApiClient(configuration))
    handler = WebhookHandler(CHANNEL_SECRET)
    logging.info("LINE Bot SDK configured successfully.")
except Exception as e:
    logging.critical(f"Failed to configure LINE Bot SDK: {e}. Please check CHANNEL_ACCESS_TOKEN and CHANNEL_SECRET.")
    raise Exception(f"LINE Bot SDK configuration failed: {e}")

# Gemini API の設定
try:
    genai.configure(api_key=GEMINI_API_KEY)
    # ユーザーが指定したモデル名を使用。利用可能性に応じて他のモデルも検討してください。
    # 例: 'gemini-1.5-flash', 'gemini-1.5-pro' など
    gemini_model = genai.GenerativeModel(
        'gemini-2.5-flash-lite-preview-06-17', # ユーザー指定のモデル名
        safety_settings={
            # 心理カウンセリングの性質上、有害コンテンツのブロック閾値を調整します。
            # ただし、BLOCK_NONEは非常に緩いため、コンテンツポリシーと照らし合わせて慎重に検討が必要です。
            # 一般的にはBLOCK_LOW_AND_ABOVEやBLOCK_MEDIUM_AND_ABOVEを推奨します。
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        }
    )
    logging.info("Gemini API configured successfully using 'gemini-2.5-flash-lite-preview-06-17' model.")
except Exception as e:
    logging.critical(f"Failed to configure Gemini API: {e}. Please check GEMINI_API_KEY and 'google-generativeai' library version in requirements.txt. Also ensure 'gemini-2.5-flash-lite-preview-06-17' model is available for your API Key/Region.")
    raise Exception(f"Gemini API configuration failed: {e}")

# --- カウンセリング関連の設定 ---
MAX_GEMINI_REQUESTS_PER_DAY = 20  # 1ユーザーあたり1日20回まで (無料枠考慮)

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
# 本番環境ではデータベース（Firestoreなど）を使用することを強く推奨します。
# 構造: {user_id: {'history': [{'role': 'user', 'parts': [{text: '...'}]}, ...], 'request_count': int, 'last_request_date': date}}
user_sessions = {}

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    
    if not signature:
        app.logger.error("X-Line-Signature header is missing.")
        abort(400)

    app.logger.info("Received Webhook Request:")
    app.logger.info("  Request body (truncated to 500 chars): " + body[:500]) 
    app.logger.info(f"  X-Line-Signature: {signature}")

    # --- 署名検証のデバッグログ ---
    try:
        secret_bytes = CHANNEL_SECRET.encode('utf-8')
        body_bytes = body.encode('utf-8')
        hash_value = hmac.new(secret_bytes, body_bytes, hashlib.sha256).digest()
        calculated_signature = base64.b64encode(hash_value).decode('utf-8')
        
        app.logger.info(f"  Calculated signature (manual): {calculated_signature}")
        app.logger.info(f"  Channel Secret used for manual calc (first 5 chars): {CHANNEL_SECRET[:5]}...")

        if calculated_signature != signature:
            app.logger.error("!!! Manual Signature MISMATCH detected !!!")
            app.logger.error(f"    Calculated: {calculated_signature}")
            app.logger.error(f"    Received:    {signature}")
            abort(400) 
        else:
            app.logger.info("  Manual signature check: Signatures match! Proceeding to SDK handler.")

    except Exception as e:
        app.logger.error(f"Error during manual signature calculation for debug: {e}", exc_info=True)
        pass

    # --- LINE Bot SDKによる署名検証とイベント処理 ---
    try:
        handler.handle(body, signature)
        app.logger.info("Webhook handled successfully by SDK.")
    except InvalidSignatureError:
        app.logger.error("!!! SDK detected Invalid signature !!!")
        app.logger.error("  This typically means CHANNEL_SECRET in Render does not match LINE Developers.")
        app.logger.error(f"  Body (truncated for error log): {body[:200]}...")
        app.logger.error(f"  Signature sent to SDK: {signature}")
        app.logger.error(f"  Channel Secret configured for SDK (first 5 chars): {CHANNEL_SECRET[:5]}...")
        abort(400)
    except Exception as e:
        logging.critical(f"Unhandled error during webhook processing by SDK: {e}", exc_info=True)
        abort(500)

    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_id = event.source.user_id # ユーザーIDを取得
    user_message = event.message.text
    app.logger.info(f"Received text message from user_id: '{user_id}', message: '{user_message}' (Reply Token: {event.reply_token})")

    response_text = "申し訳ありません、現在メッセージを処理できません。しばらくしてからもう一度お試しください。"

    # ユーザーセッションの初期化または取得
    current_date = datetime.date.today()
    if user_id not in user_sessions:
        # 初めてのユーザー、またはセッションがリセットされた場合
        user_sessions[user_id] = {
            'history': [], # 会話履歴は空で開始
            'request_count': 0,
            'last_request_date': current_date
        }
        app.logger.info(f"Initialized new session for user_id: {user_id}")
        # 初回メッセージを送信
        response_text = INITIAL_MESSAGE
        try:
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[LineReplyTextMessage(text=response_text)]
                )
            )
            app.logger.info(f"Sent initial message to new user {user_id}.")
        except Exception as e:
            logging.error(f"Error sending initial reply to LINE for new user: {e}", exc_info=True)
        return 'OK' # 初回メッセージ送信後はここで処理を終了

    # 日付が変わったらリクエスト数をリセット
    if user_sessions[user_id]['last_request_date'] != current_date:
        user_sessions[user_id]['request_count'] = 0
        user_sessions[user_id]['last_request_date'] = current_date
        app.logger.info(f"Reset request count for user {user_id} as date changed.")

    # Gemini API利用回数制限のチェック
    if user_sessions[user_id]['request_count'] >= MAX_GEMINI_REQUESTS_PER_DAY:
        response_text = GEMINI_LIMIT_MESSAGE
        app.logger.warning(f"User {user_id} exceeded daily Gemini request limit ({MAX_GEMINI_REQUESTS_PER_DAY}).")
        try:
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[LineReplyTextMessage(text=response_text)]
                )
            )
            app.logger.info(f"Sent limit message to LINE for user {user_id}.")
        except Exception as e:
            logging.error(f"Error sending limit reply to LINE: {e}", exc_info=True)
        return 'OK'

    # 会話履歴を準備
    # システムプロンプトを履歴の先頭に設定
    # Geminiモデルのhistoryは `role` と `parts` を持つオブジェクトのリストで構成されます。
    # `parts` は `text` プロパティを持つオブジェクトのリストです。
    
    # システムプロンプトを固定で追加
    chat_history_for_gemini = [{'role': 'user', 'parts': [{'text': COUNSELING_SYSTEM_PROMPT}]}]
    chat_history_for_gemini.append({'role': 'model', 'parts': [{'text': "はい、承知いたしました。こころコンパスとして、心を込めてお話をお伺いします。"}]}) # システムプロンプトへのAIの承諾応答

    # MAX_CONTEXT_TURNS に基づいて過去の会話履歴を追加
    # user_sessions['history'] は [['user', 'メッセージ'], ['model', 'メッセージ'], ...] の形式で保存されていると仮定
    # 最新の会話からMAX_CONTEXT_TURNS分だけ取得
    
    # Gemini APIのhistoryフォーマットに合わせて変換し、追加
    # user_sessions['history'] には、システムプロンプトとAIの承諾応答は含まれないため、MAX_CONTEXT_TURNSはユーザーとAIの実際のやり取りのターン数を指す
    start_index = max(0, len(user_sessions[user_id]['history']) - MAX_CONTEXT_TURNS * 2) # 最新のNターンを取得
    
    for role, text_content in user_sessions[user_id]['history'][start_index:]:
        chat_history_for_gemini.append({'role': role, 'parts': [{'text': text_content}]})

    # 現在のユーザーメッセージを追加
    chat_history_for_gemini.append({'role': 'user', 'parts': [{'text': user_message}]})


    try:
        # Geminiとのチャットセッションを開始
        # `generate_content` ではなく `start_chat` を使用して、会話履歴を渡す
        convo = gemini_model.start_chat(history=chat_history_for_gemini)
        gemini_response = convo.send_message(user_message) # 履歴を渡しているので、ここには現在のメッセージのみを渡す

        if gemini_response and hasattr(gemini_response, 'text'):
            response_text = gemini_response.text
        elif isinstance(gemini_response, list) and gemini_response and hasattr(gemini_response[0], 'text'):
            response_text = gemini_response[0].text
        else:
            logging.warning(f"Unexpected Gemini response format or no text content: {gemini_response}")
            response_text = "Geminiからの応答形式が予期せぬものでした。"

        app.logger.info(f"Gemini generated response: '{response_text}'")

        # 会話履歴を更新
        user_sessions[user_id]['history'].append(['user', user_message])
        user_sessions[user_id]['history'].append(['model', response_text])
        
        # リクエスト数をインクリメント
        user_sessions[user_id]['request_count'] += 1
        user_sessions[user_id]['last_request_date'] = current_date # リクエスト日を更新

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
