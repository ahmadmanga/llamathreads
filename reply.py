import os
import sys
from datetime import datetime, timedelta
from beem import Hive
from beem.comment import Comment
from beem.exceptions import MissingKeyError
from dotenv import load_dotenv
from context_helper import find_context_keywords
import logging
import requests
import json
import re
import time  # Import the time module to use time.sleep()

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
MAX_MESSAGE_LENGTH = 18000
PRUNE_THRESHOLD = 8 * MAX_MESSAGE_LENGTH

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
* Each of your messages should be less than 999 characters. Try to adjust to the sweet spot above 850 characters.
* Use the provided context and your knowledge to solve any question in the prompt. 
* You'll be replying to fellow users on inleo.io.
* You talk in a light-hearted friendly way, as you look at the topic from multiple sides.
* The chain of previous messages will be provided as context. (Example: post by @{author}: MESSAGE)
* The content of some links will be provided to you in as a unique message.
* Context of various levels of importance will be provided to you as messages as well.
* The user prompt should have more weight compared to the context.
* If an answer to a question asked in the prompt is in the messages, it should take priority over your knowledge.
* If links are provided as an important context, make sure to reference the URLs in your responses.
* All of your responses should be formatted in a beautiful, easy-to-read markdown format.
* Always add two line breaks after each paragraph, and after the last bullet point in a section."""
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
        print("waiting for blockchain...")
        time.sleep(3)  # Wait for 3 seconds
        print("continuing...")
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
            # Construct the URL
            referenced_url = f"https://inleo.io/threads/view/{referenced_author}/{permlink}"
            # Preface the body with the URL and the referencing author
            prefixed_body = f"@{referencing_author} shared this {referenced_url} by @{referenced_author}\nLink's content:\n{referenced_body}"
            referenced_messages.append({"role": "user", "content": prefixed_body})
            logger.info(f"Referenced message added for @{referenced_author}/{permlink} by @{referencing_author}")
        except MissingKeyError:
            logger.error(f"Missing posting key. Please check your POSTING_KEY in the .env file.")
        except Exception as e:
            logger.error(f"Error fetching referenced comment @{referenced_author}/{permlink} by @{referencing_author}: {e}")
    return referenced_messages

def fetch_comment_chain(comment, blacklist=['leothreads']) -> list:
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
    
    # Find context keywords and add them to messages
    context_messages = find_context_keywords(messages)
    
    # Add HIGH priority context messages to the start as system messages
    high_priority_messages = [msg for msg in context_messages if msg['role'] == 'system']
    messages.extend(high_priority_messages)
    
    # Add MID priority context messages to the end
    mid_priority_messages = [msg for msg in context_messages if msg['role'] == 'important_context']
    messages.extend(mid_priority_messages)
    
    # Add LOW priority context messages to the end
    low_priority_messages = [msg for msg in context_messages if msg['role'] == 'low_priority_context']
    messages.extend(low_priority_messages)

    # Log the final messages to ensure they are correctly integrated
    logger.debug(f"Final messages: {messages}")

    # Reverse the messages to maintain historical order
    messages.reverse()

    # Prune messages if their total combined length is above the threshold
    while True:
        total_length = sum(len(msg['content']) for msg in messages)
        if total_length <= PRUNE_THRESHOLD:
            break
        # Remove the oldest message
        removed_message = messages.pop(0)
        logger.info(f"Removed oldest message: @{removed_message['content'].split(':', 1)[0]}")
        logger.info(f"New total length: {total_length} -> {sum(len(msg['content']) for msg in messages)}")
    
    # Truncate the last message to fit within MAX_MESSAGE_LENGTH if necessary
    if messages and len(messages[-1]['content']) > MAX_MESSAGE_LENGTH:
        logger.info(f"Truncating the last message to fit within {MAX_MESSAGE_LENGTH} characters.")
        messages[-1]['content'] = messages[-1]['content'][:MAX_MESSAGE_LENGTH]
        logger.info(f"Truncated message length: {len(messages[-1]['content'])}")
    
    return messages