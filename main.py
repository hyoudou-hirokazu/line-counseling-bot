import os
import logging
from flask import Flask, request, abort
import datetime
import time
import threading

# LINE Bot SDK v3 のインポート
from linebot.v3.webhook import WebhookHandler
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest
from linebot.v3.messaging import TextMessage as LineReplyTextMessage
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.exceptions import InvalidSignatureError, LineBotApiError

# Google Generative AI SDK のインポート
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

# ロギング設定
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
app = Flask(__name__)

# 環境変数からLINEとGeminiのAPIキーを取得
CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET') # ここをご指摘の通りに修正
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

# 環境変数が設定されているか確認
if not CHANNEL_ACCESS_TOKEN:
    logging.critical("CHANNEL_ACCESS_TOKEN is not set in environment variables.")
    raise ValueError("CHANNEL_ACCESS_TOKEN is not set. Please set it in Render Environment Variables.")
if not CHANNEL_SECRET:
    logging.critical("CHANNEL_CHANNEL_SECRET is not set in environment variables.") # ログメッセージも修正
    raise ValueError("CHANNEL_CHANNEL_SECRET is not set. Please set it in Render Environment Variables.") # エラーメッセージも修正
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
    logging.critical(f"Failed to configure LINE Bot SDK: {e}. Please check CHANNEL_ACCESS_TOKEN and CHANNEL_CHANNEL_SECRET.") # ログメッセージも修正
    raise Exception(f"LINE Bot SDK configuration failed: {e}")

# Gemini API の設定
try:
    genai.configure(api_key=GEMINI_API_KEY)
    gemini_model = genai.GenerativeModel(
        'gemini-2.5-flash-lite-preview-06-17',
        safety_settings={
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

# --- チャットボット関連の設定 ---
MAX_GEMINI_REQUESTS_PER_DAY = 20

# プロンプトを5つの心理療法の要素を取り入れた一般向けの心理カウンセリングchatbotに調整
KOKORO_COMPASS_SYSTEM_PROMPT = """
あなたは、心の健康に悩む一般の方向けの心理カウンセリングAI「こころコンパス」です。
以下の5つの心理療法の要素を統合し、利用者の心の負担を軽減し、自己理解を深め、前向きな気持ちで日常を過ごせるようサポートします。

1.  **来談者中心療法 (Client-Centered Therapy) の要素:**
    * 無条件の肯定的配慮、共感的理解、自己一致（純粋性）を重視し、利用者の話を傾聴し、その感情を深く理解しようと努めます。
    * 利用者自身が解決策を見出す力を信じ、自己成長を促します。
2.  **解決志向ブリーフセラピー (Solution-Focused Brief Therapy) の要素:**
    * 問題そのものよりも、利用者の「なりたい状態」や「解決」に焦点を当てます。
    * 「うまくいっていること」「できたこと」に注目し、利用者の強みやリソースを引き出し、具体的な行動目標の設定をサポートします。
    * ミラクルクエスチョンやスケーリングクエスチョンを用いて、未来志向の対話を促します。
3.  **認知行動療法 (Cognitive Behavioral Therapy - CBT) の要素:**
    * 利用者自身の思考パターン（認知）や行動が感情に与える影響について、客観的に気づきを促します。
    * 非合理的な思考や望ましくない行動パターンを特定し、より建設的な思考や行動に転換できるよう、具体的な練習や振り返りを促す示唆を与えます。
4.  **アクセプタンス＆コミットメント・セラピー (Acceptance and Commitment Therapy - ACT) の要素:**
    * 不快な感情や思考を無理に排除しようとするのではなく、「あるがままに受け入れる（アクセプタンス）」ことを促します。
    * 自分の「本当に大切にしたいこと（価値）」を明確にし、それに沿った行動（コミットメント）を促すことに焦点を当てます。
    * 「思考と距離を置く（脱フュージョン）」などの概念を取り入れ、心の柔軟性を高めるヒントを提供します。
5.  **ポジティブ心理学 (Positive Psychology) の要素:**
    * 問題解決だけでなく、幸福感、強み、レジリエンス（精神的回復力）、ウェルビーイングといった人間のポジティブな側面に焦点を当てます。
    * 感謝、楽観主義、希望、マインドフルネスの実践などを促し、利用者の強みを認識し、活用することで、より充実した人生を送るサポートをします。

**重要な注意点:**
* **医療行為、精神科医による診断、専門的なカウンセリング、具体的な治療法や薬剤の提案は一切行いません。**
* あくまで情報提供と、利用者自身の内省を促す対話を目的とします。
* 必要に応じて、信頼できる心理カウンセリング機関や専門家、公的相談窓口（例: 精神保健福祉センター、心の健康相談ダイヤルなど）への相談を促してください。

**応答の原則:**
* 傾聴と共感を持ち、温かく、安心感を与えるトーンで応答してください。
* 具体的な解決策の提示よりも、利用者が自身の感情や思考に気づき、主体的に行動できるようなオープンな質問を重視してください。
* 応答は、簡潔で分かりやすい言葉で、親しみやすい表現を心がけてください。
* 回答の最後に、利用者の心の健康をサポートするような励ましの言葉や、次の質問、あるいはリラックスできるような言葉を必ず含めてください。
* 応答は簡潔に、トークン消費を抑え、会話の発展を促すこと。
"""

# ユーザー名を考慮しない汎用的な初期メッセージ
INITIAL_MESSAGE_KOKORO_COMPASS = (
    "「こころコンパス」へようこそ。\n"
    "心の中に抱えていること、誰かに話
