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
            "parts": [{"text": f"You are helping me guess the word '{actual_word}'. Rate my guesses on a scale of 1-100 and provide helpful feedback. Return only JSON with 'score' and 'message' fields."}]
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


def store_conversation(user_id, session_id, user_word, actual_word, score, message):
    conversation_id = str(uuid.uuid4())
    conversations_table.put_item(
        Item={
            'user_id': user_id,
            'conversation_id': conversation_id,
            'session_id': session_id,
            'timestamp': datetime.now().isoformat(),
            'user_word': user_word,
            'actual_word': actual_word,
            'score': score,
            'message': message
        }
    )
    
    # Store success if score is 100
    if score == 100:
        store_daily_success(user_id, actual_word)


def store_daily_success(user_id, word):
    today = datetime.now().strftime('%Y-%m-%d')
    success_table.put_item(
        Item={
            'user_id': user_id,
            'date': today,
            'word': word,
            'timestamp': datetime.now().isoformat()
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
        
        has_guessed_correctly = 'Item' in response
        
        # Get today's word and image
        word, s3_key = get_todays_word()
        image_url = f"https://{os.environ['S3_BUCKET_NAME']}.s3.amazonaws.com/{s3_key}" if s3_key else None
        
        return {
            "statusCode": 200,
            "body": json.dumps({
                "has_guessed_correctly": has_guessed_correctly,
                "date": today,
                "image_url": image_url
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

        # Check exact match first
        if user_word.lower() == actual_word.lower():
            score, message = 100, "Correct! You guessed the word!"
            guessed = True
        else:
            previous_messages = get_previous_messages(user_id, session_id)
            print(f"Previous messages count: {len(previous_messages)}")
            print(f"Previous messages: {previous_messages}")
            score, message = check_word_match(user_word, actual_word, previous_messages)
            guessed = False

        store_conversation(user_id, session_id, user_word, actual_word, score, message)

        return {
            "statusCode": 200,
            "body": json.dumps({
                "score": score,
                "message": message,
                "guessed": guessed
            }, cls=DecimalEncoder)
        }

    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}, cls=DecimalEncoder)
        }
