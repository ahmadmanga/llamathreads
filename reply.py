import os
import sys
from datetime import datetime, timedelta
from beem import Hive
from beem.comment import Comment
from beem.exceptions import MissingKeyError
from dotenv import load_dotenv
import logging
import requests
import json
import re

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', stream=sys.stdout)
logger = logging.getLogger()

# Load environment variables
load_dotenv()

# Get environment variables
ACCOUNT = os.getenv('ACCOUNT')
POSTING_KEY = os.getenv('POSTING_KEY')
API_KEY = os.getenv('API_KEY')

# Constants
BASE_URL = "https://nano-gpt.com/api"
headers = {
    "x-api-key": API_KEY,
    "Content-Type": "application/json"
}

# Corrected regex pattern for URLs
URL_REGEX = re.compile(r'https://inleo.io/threads/(?:view/)?(\w+)/([-.\w]+)(?:\?[^?]+)?')

def talk_to_gpt(prompt, system_prompt=None, model="llama-3.3-70b", messages=[], max_retries=3, timeout=90):
    if system_prompt is None:
        system_prompt = """You are a general purpose chatbot on a social media website called inleo.io. 
Each of your messages should be less than 950 characters. 
* You'll use your knowledge as an expert on any topic you'll be asked about. 
* You talk in a light-hearted friendly way, as you look at the topic from multiple sides. 
* Opinions should be mentioned as such, and facts should be reinforced with a source or a reference. 
* You'll be replying to fellow users on inleo.io. 
* The chain of previous messages will be provided as context. (Example: post by @{author}: MESSAGE) 
* What a user says in the prompt should have more weight compared to the context. 
* All of your responses should be formatted in a beautiful, easy-to-read markdown format. 
* All of your responses should fit in 950 characters. Average response length between 600 and 800 characters. 
* Try to cram as much valuable information as possible without exceeding the character limit."""
    
    messages.insert(0, {"role": "system", "content": system_prompt})
    
    for attempt in range(1, max_retries + 1):
        try:
            data = {
                "prompt": prompt,
                "model": model,
                "messages": messages
            }
            response = requests.post(f"{BASE_URL}/talk-to-gpt", headers=headers, json=data, timeout=timeout)
            if response.status_code == 200:
                response_text = response.text.strip()
                # Split the response to separate the text and NanoGPT info parts
                parts = response_text.split('<NanoGPT>')
                if len(parts) > 1:
                    # Extract the text response (everything before <NanoGPT>)
                    text_response = parts[0].strip()
                    logger.info(f"Extracted text response from response. Attempt {attempt}.")
                    if len(text_response) <= 1100:
                        return text_response
                    else:
                        logger.warning(f"Response too long (attempt {attempt}): {len(text_response)} characters.")
                else:
                    logger.info(f"No <NanoGPT> delimiter found. Returning raw response. Attempt {attempt}.")
                    if len(response_text) <= 1100:
                        return response_text
                    else:
                        logger.warning(f"Response too long (attempt {attempt}): {len(response_text)} characters.")
            else:
                logger.error(f"Error {response.status_code}: {response.text}. Attempt {attempt}.")
        except requests.RequestException as e:
            if isinstance(e, requests.Timeout):
                logger.warning(f"Request timed out after {timeout} seconds. Attempt {attempt}.")
            else:
                logger.error(f"An error occurred: {e}. Attempt {attempt}.")
    
    logger.error("All attempts failed to get a valid response.")
    return None

def post_reply(parent_comment, reply_text):
    # Replace "@llamathreads" with "`llamathreads`" to prevent tagging
    reply_text = reply_text.replace('@llamathreads', '`llamathreads`')
    hive = Hive(node='https://api.hive.blog', keys=[POSTING_KEY])
    try:
        # Generate a unique permlink for your comment and convert it to lowercase
        permlink = f"re-{parent_comment['author']}-{parent_comment['permlink']}-{datetime.utcnow().strftime('%Y%m%dT%H%M%S%f')}"
        permlink = permlink.lower()
        result = hive.post(
            title="",  # Leave empty for a comment
            body=reply_text,
            author=ACCOUNT,
            permlink=permlink,
            reply_identifier=f"{parent_comment['author']}/{parent_comment['permlink']}",
            json_metadata={"app": "leothreads/0.3"}  # Use Leothreads interface for posting to the blockchain
        )
        logger.info(f"Reply posted successfully: {result}")
    except MissingKeyError:
        logger.error("Missing posting key. Please check your POSTING_KEY in the .env file.")
    except Exception as e:
        logger.error(f"An error occurred while posting the reply: {e}")
        logger.debug(str(e))

def fetch_referenced_comments(message_body, referencing_author):
    # Use the corrected regex pattern
    references = URL_REGEX.findall(message_body)
    referenced_messages = []
    for referenced_author, permlink in references:
        try:
            referenced_comment = Comment(f"@{referenced_author}/{permlink}")
            referenced_body = referenced_comment.get('body', '')
            prefixed_body = f"@{referencing_author} referenced a post by @{referenced_author}:\n{referenced_body}"
            referenced_messages.append({"role": "user", "content": prefixed_body})
            logger.info(f"Referenced message added for @{referenced_author}/{permlink} by @{referencing_author}")
        except MissingKeyError:
            logger.error(f"Missing posting key. Please check your POSTING_KEY in the .env file.")
        except Exception as e:
            logger.error(f"Error fetching referenced comment @{referenced_author}/{permlink} by @{referencing_author}: {e}")
    return referenced_messages

def fetch_comment_chain(comment, blacklist=['leothreads']):
    messages = []
    current_comment = comment
    while current_comment:
        author = current_comment.get('author', '')
        permlink = current_comment.get('permlink', '')
        body = current_comment.get('body', '')
        if author in blacklist:
            logger.info(f"Skipped blacklisted user: @{author}/{permlink}")
            break
        role = "assistant" if author.lower() == "llamathreads" else f"user_{author}"
        # Preface the body with the author's username and a line break
        prefixed_body = f"post by @{author}:\n{body}" if role != "assistant" else f"{body}"
        message = {"role": role, "content": prefixed_body}
        messages.append(message)
        logger.info(f"Added a message: @{author}/{permlink}")
        
        # Fetch referenced comments
        referenced_messages = fetch_referenced_comments(body, author)
        messages.extend(referenced_messages)
        
        # Check if the current comment is a blog post (no parent author or parent permlink)
        parent_author = current_comment.get('parent_author', '')
        parent_permlink = current_comment.get('parent_permlink', '')
        if not parent_author or not parent_permlink:
            logger.info(f"No parent author or parent permlink found for @{author}/{permlink}. Stopping chain trace.")
            break
        try:
            # Fetch the parent comment
            parent_comment = Comment(f"@{parent_author}/{parent_permlink}")
            # Safely retrieve values from parent_comment
            parent_author = parent_comment.get('author', '')
            parent_permlink = parent_comment.get('permlink', '')
            parent_body = parent_comment.get('body', '')
            parent_parent_author = parent_comment.get('parent_author', '')
            parent_parent_permlink = parent_comment.get('parent_permalink', '')
            current_comment = {
                'author': parent_author,
                'permlink': parent_permlink,
                'body': parent_body,
                'parent_author': parent_parent_author,
                'parent_permlink': parent_parent_permlink
            }
        except MissingKeyError:
            logger.error("Missing posting key. Please check your POSTING_KEY in the .env file.")
        except Exception as e:
            logger.error(f"Error fetching parent comment @{parent_author}/{parent_permlink}: {e}")
            break
    # Reverse the messages to maintain historical order
    messages.reverse()
    # Trim messages to ensure the combined length is within 11000 characters
    while True:
        total_length = sum(len(msg['content']) for msg in messages)
        if total_length <= 11000:
            break
        # Remove the oldest message
        removed_message = messages.pop(0)
        logger.info(f"Removed oldest message: @{removed_message['content'].split(':', 1)[0]}")
        logger.info(f"New total length: {total_length} -> {sum(len(msg['content']) for msg in messages)}")
    return messages
