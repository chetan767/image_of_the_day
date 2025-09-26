import os
import boto3
from datetime import datetime
from google import genai

dynamodb = boto3.resource('dynamodb')
words_table = dynamodb.Table(os.environ['WORDS_TABLE'])

def generate_image(word):
    client = genai.Client(api_key=os.environ['GEMINI_API_KEY'])
    prompt = f"Create a visually creative and slightly abstract image that subtly hints at the word \"{word}\" without showing it directly. Use metaphorical, symbolic, or thematic elements to suggest the meaning of the word. The image should make the viewer think and infer the word based on visual clues. Avoid text or overly literal depictions. Focus on making the image tricky but guessable."
    response = client.models.generate_content(
        model="gemini-2.5-flash-image-preview",
        contents=[prompt],
    )

    print("Image generation response:", response)
    for part in response.candidates[0].content.parts:
        if part.inline_data is not None:
            return part.inline_data.data
            
    return None

def save_to_s3(image_data, word):
    s3_client = boto3.client('s3')
    BUCKET_NAME = os.environ['S3_BUCKET_NAME']
    today = datetime.now().strftime('%Y-%m-%d')
    key = f"{today}/{word}.png"
    
    s3_client.put_object(
        Bucket=BUCKET_NAME,
        Key=key,
        Body=image_data,
        ContentType='image/png'
    )
    return key


def store_daily_word(word, s3_key):
    today = datetime.now().strftime('%Y-%m-%d')
    words_table.put_item(
        Item={
            'date': today,
            'word': word,
            's3_key': s3_key,
            'timestamp': datetime.now().isoformat()
        }
    )