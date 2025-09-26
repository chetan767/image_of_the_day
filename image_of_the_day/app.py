import json
import os
from services.image_service import generate_image, save_to_s3, store_daily_word
from services.guess_service import handle_guess, check_daily_status


def lambda_handler(event, context):
    path = event.get('rawPath', '')
    
    if path == '/generatequiz':
        return handle_quiz(event)
    elif path == '/guess':
        return handle_guess(event)
    elif path == '/status':
        return check_daily_status(event)
    
    return {
        "statusCode": 200,
        "body": json.dumps({"message": "hello world"})
    }


def handle_quiz(event):
    try:
        # Check API key authentication
        headers = event.get('headers', {})
        api_key = headers.get('x-api-key') or headers.get('X-API-Key')
        
        if api_key != os.environ.get('ADMIN_API_KEY'):
            return {
                "statusCode": 401,
                "body": json.dumps({"error": "Unauthorized"})
            }
        
        body = json.loads(event.get('body', '{}'))
        word = body.get('word')
        
        if not word:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Missing 'word' in request body"})
            }
        
        # Generate image
        image_data = generate_image(word)
        
        if not image_data:
            return {
                "statusCode": 500,
                "body": json.dumps({"error": "Failed to generate image"})
            }
        
        # Save to S3
        s3_key = save_to_s3(image_data, word)
        
        # Store in words table
        store_daily_word(word, s3_key)
        
        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": f"Image generated for word: {word}",
                "s3_key": s3_key
            })
        }
        
    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }