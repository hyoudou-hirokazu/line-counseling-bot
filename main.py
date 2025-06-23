import os
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import google.generativeai as genai

# Flaskアプリケーションの初期化
app = Flask(__name__)

# 環境変数からLINE Botの情報を取得
# Renderなどのサービスでは、環境変数は自動的に読み込まれます
CHANNEL_SECRET = os.getenv('CHANNEL_SECRET', None)
CHANNEL_ACCESS_TOKEN = os.getenv('CHANNEL_ACCESS_TOKEN', None)
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', None)

# 環境変数が設定されているか確認
if CHANNEL_SECRET is None:
    print('Specify CHANNEL_SECRET as environment variable.')
    exit(1)
if CHANNEL_ACCESS_TOKEN is None:
    print('Specify CHANNEL_ACCESS_TOKEN as environment variable.')
    exit(1)
if GEMINI_API_KEY is None:
    print('Specify GEMINI_API_KEY as environment variable.')
    exit(1)

# LINE Bot APIとWebhookハンドラーの初期化
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# Gemini APIの設定
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-pro') # 使用するGeminiモデルを指定

# WebhookからのPOSTリクエストを処理するエンドポイント
@app.route("/callback", methods=['POST'])
def callback():
    # リクエストヘッダーから署名検証のための署名を取得
    signature = request.headers['X-Line-Signature']

    # リクエストボディをテキストで取得
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    try:
        # Webhookハンドラーでリクエストを処理
        handler.handle(body, signature)
    except InvalidSignatureError:
        # 署名検証に失敗した場合、エラーを返す
        abort(400)
    return 'OK'

# メッセージイベント（テキストメッセージ）の処理
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_message = event.message.text
    
    # Geminiに送信するプロンプトを整形
    # ここに心理カウンセラーとしてのプロンプトを追加
    gemini_prompt = f"あなたはプロの心理カウンセラーです。ユーザーの悩みに寄り添い、共感し、具体的なアドバイスではなく、自己解決を促すような質問をしてください。ただし、短い返答で、専門用語は避けてください。ユーザー: {user_message}"

    try:
        # Gemini APIを呼び出して応答を生成
        response = model.generate_content(gemini_prompt)
        # 生成されたテキストを取得
        gemini_response_text = response.text
        
        # LINEでユーザーに応答を返信
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=gemini_response_text)
        )
    except Exception as e:
        # エラーが発生した場合の処理
        app.logger.error(f"Gemini API Error or Reply Error: {e}")
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="ごめんなさい、現在応答できません。もう一度お試しください。")
        )

# アプリケーションの実行
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080)) # RenderはPORT環境変数を使用
    app.run(host="0.0.0.0", port=port)