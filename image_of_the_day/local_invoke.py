from dotenv import load_dotenv
import os
load_dotenv()
from app import lambda_handler
import json

with open("../events/event.json", "r", errors="ignore") as f:
    event = json.load(f)
    context = None
    print("Running local test...")
    response = lambda_handler(event, {})
    print("\n--- Lambda Response ---")
    print(f"Status Code: {response['statusCode']}")
    print(response)
