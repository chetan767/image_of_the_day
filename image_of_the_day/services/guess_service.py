import os
import boto3
import json
import uuid
from datetime import datetime
from decimal import Decimal
from google import genai
from langchain_core.output_parsers import JsonOutputParser

dynamodb = boto3.resource('dynamodb')
words_table = dynamodb.Table(os.environ['WORDS_TABLE'])
conversations_table = dynamodb.Table(os.environ.get('CONVERSATIONS_TABLE', 'conversations-v2'))
success_table = dynamodb.Table(os.environ.get('SUCCESS_TABLE', 'daily-success'))

MAX_GUESSES = 5

class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return int(obj) if obj % 1 == 0 else float(obj)
        return super().default(obj)


def get_todays_word():
    today = datetime.now().strftime('%Y-%m-%d')
    response = words_table.get_item(Key={'date': today})
    item = response.get('Item', {})
    return item.get('word'), item.get('s3_key')


def get_previous_messages(user_id, session_id):
    response = conversations_table.query(
        IndexName='session-index',
        KeyConditionExpression='session_id = :sid',
        FilterExpression='user_id = :uid',
        ExpressionAttributeValues={
            ':sid': session_id,
            ':uid': user_id
        },
        ScanIndexForward=True
    )
    return response.get('Items', [])


def check_word_match(user_word, actual_word, previous_messages):
    print(f"check_word_match: user_word={user_word}, actual_word={actual_word}")
    try:
        # Build conversation history
        history = []

        # Add system context
        history.append({
            "role": "user",
            "parts": [{"text": f"You are a game master. The secret word is '{actual_word}'. Your task is to evaluate a user's guess. Provide a score from 1-100 on how close their guess is, and a creative hint in the 'message' field. **Do not reveal the secret word '{actual_word}' or direct synonyms in your response.** Your entire response must be only a single JSON object with 'score' (integer) and 'message' (string) keys."}]
        })

        history.append({
            "role": "model",
            "parts": [{"text": "I'll help you guess the word! Send me your guesses and I'll rate them and give you feedback."}]
        })

        # Add previous conversation
        for i, msg in enumerate(previous_messages):
            history.append({
                "role": "user",
                "parts": [{"text": f"My guess: {msg['user_word']}"}]
            })
            history.append({
                "role": "model",
                "parts": [{"text": json.dumps({"score": msg['score'], "message": msg['message']}, cls=DecimalEncoder)}]
            })

        print(f"Final history: {(history)} messages")

        client = genai.Client(api_key=os.environ['GEMINI_API_KEY'])
        chat = client.chats.create(model="gemini-2.0-flash", history=history)

        response = chat.send_message(f"My guess: {user_word}")
        print("Chat response:", response.text)
        parser = JsonOutputParser(schema={"score": int, "message": str})
        result = parser.parse(response.text)
        print("Parsed result:", result)
        return result.get('score', 0), result.get('message', 'No feedback available')
    except Exception as e:
        print(f"Error processing guess: {e}")
        return 0, "Unable to process guess"


def store_conversation(user_id, session_id, user_word, actual_word, score, message, date):
    conversation_id = str(uuid.uuid4())
    conversations_table.put_item(
        Item={
            'user_id': user_id,
            'conversation_id': conversation_id,
            'session_id': session_id,
            'date': date,
            'timestamp': datetime.now().isoformat(),
            'user_word': user_word,
            'actual_word': actual_word,
            'score': score,
            'message': message
        }
    )


def get_guess_count_for_today(user_id):
    today = datetime.now().strftime('%Y-%m-%d')
    response = conversations_table.query(
        IndexName='user-date-index',
        KeyConditionExpression='user_id = :uid AND #d = :d',
        ExpressionAttributeNames={'#d': 'date'},
        ExpressionAttributeValues={':uid': user_id, ':d': today},
        Select='COUNT'
    )
    return response['Count']

def get_guesses_for_today(user_id):
    today = datetime.now().strftime('%Y-%m-%d')
    response = conversations_table.query(
        IndexName='user-date-index',
        KeyConditionExpression='user_id = :uid AND #d = :d',
        ExpressionAttributeNames={'#d': 'date'},
        ExpressionAttributeValues={':uid': user_id, ':d': today}
    )
    items = response.get('Items', [])
    items.sort(key=lambda x: x.get('timestamp', ''))
    return items

def store_daily_success(user_id, word, guessed):
    today = datetime.now().strftime('%Y-%m-%d')
    success_table.put_item(
        Item={
            'user_id': user_id,
            'date': today,
            'word': word,
            'timestamp': datetime.now().isoformat(),
            'guessed': guessed,
        }
    )


def check_daily_status(event):
    try:
        body = json.loads(event.get('body', '{}'))
        user_id = body.get('user_id', 'anonymous')
        today = datetime.now().strftime('%Y-%m-%d')
        
        response = success_table.get_item(
            Key={
                'user_id': user_id,
                'date': today
            }
        )
        item = response.get('Item')
        previous_guesses = get_guesses_for_today(user_id)
        guess_count = len(previous_guesses)
        has_guessed_correctly = False
        if item:
            has_guessed_correctly = item.get('guessed', False)

        out_of_guesses = guess_count >= MAX_GUESSES or has_guessed_correctly
        # Get today's word and image
        word, s3_key = get_todays_word()
        image_url = f"https://{os.environ['S3_BUCKET_NAME']}.s3.amazonaws.com/{s3_key}" if s3_key else None

        return {
            "statusCode": 200,
            "body": json.dumps({
                "has_guessed_correctly": has_guessed_correctly,
                "date": today,
                "image_url": image_url,
                "guess_count": guess_count,
                "out_of_guesses": out_of_guesses,
                "previous_guesses": previous_guesses
            }, cls=DecimalEncoder)
        }
        
    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }


def handle_guess(event):
    try:
        body = json.loads(event.get('body', '{}'))
        user_word = body.get('user_word')
        user_id = body.get('user_id', 'anonymous')
        session_id = body.get('session_id', 'default')

        # Check current guess status from DB
        today = datetime.now().strftime('%Y-%m-%d')
        status_response = success_table.get_item(Key={'user_id': user_id, 'date': today})
        status_item = status_response.get('Item')

        guess_count = get_guess_count_for_today(user_id)
        has_guessed_correctly = status_item.get('guessed', False) if status_item else False

        if has_guessed_correctly:
            return {"statusCode": 403, "body": json.dumps({"error": "You have already guessed the word correctly today."})}

        if guess_count >= MAX_GUESSES :
            return {"statusCode": 403, "body": json.dumps({"error": f"You have reached the maximum of {MAX_GUESSES} guesses for today."})}


        if not user_word:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Missing 'user_word' in request body"}, cls=DecimalEncoder)
            }

        actual_word, _ = get_todays_word()
        print(f"Today's word: {actual_word}")
        if not actual_word:
            return {
                "statusCode": 404,
                "body": json.dumps({"error": "No word found for today"}, cls=DecimalEncoder)
            }

        guessed = False
        # Check exact match first
        if user_word.lower() == actual_word.lower():
            score, message = 100, "Correct! You guessed the word!"
            guessed = True
        else:
            previous_messages = get_previous_messages(user_id, session_id)
            score, message = check_word_match(user_word, actual_word, previous_messages)
            if score == 100:
                guessed = True
                message = "Correct! You guessed the word!"

        store_conversation(user_id, session_id, user_word, actual_word, score, message, today)
        store_daily_success(user_id, actual_word, guessed=guessed)

        return {
            "statusCode": 200,
            "body": json.dumps({
                "score": score,
                "message": message,
                "guessed": guessed,
                "out_of_guesses": (guess_count + 1) >= MAX_GUESSES or guessed
            }, cls=DecimalEncoder)
        }

    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}, cls=DecimalEncoder)
        }
