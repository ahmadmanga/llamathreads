import requests
import time
from pymongo import MongoClient
from dotenv import load_dotenv
import os
import re
from beem import Hive
from beem.comment import Comment
from beem.exceptions import ContentDoesNotExistsException

load_dotenv()  # Load MongoDB connection details from environment variables
MONGO_URI = os.getenv('MONGO_URI')
DATABASE_NAME = 'llama_threads'  # New database name
COLLECTION_NAME = 'blocks'  # New collection name

# Initialize MongoDB client
client = MongoClient(MONGO_URI)
db = client[DATABASE_NAME]
collection = db[COLLECTION_NAME]

HIVE_API = [
    'https://api.hive.blog',
    'https://api.deathwing.me',
    'https://api.openhive.network'
]

SLEEP_INTERVAL = 5

def get_latest_block_num():
    """Get the latest block number from the HIVE blockchain."""
    data = {
        "jsonrpc": "2.0",
        "method": "condenser_api.get_dynamic_global_properties",
        "params": [],
        "id": 1
    }
    retries = 10
    api_index = 0
    while retries > 0:
        try:
            response = requests.post(HIVE_API[api_index % len(HIVE_API)], json=data).json()
            result = response.get('result')
            if result:
                return result['head_block_number']
            else:
                print(f"Failed to get latest block number from {HIVE_API[api_index % len(HIVE_API)]}. Retrying...")
                time.sleep(SLEEP_INTERVAL / 10)
                retries -= 1
                api_index += 1  # Switch to the next API on each retry
        except Exception as e:
            print(f"Exception while fetching latest block number from {HIVE_API[api_index % len(HIVE_API)]}: {e}")
            time.sleep(SLEEP_INTERVAL / 10)
            retries -= 1
            api_index += 1  # Switch to the next API on each retry
    print("Max retries exceeded. Aborting.")
    raise Exception("Failed to fetch latest block number after multiple retries.")

def get_block_range(start_block, end_block):
    """Fetch a range of blocks from the HIVE blockchain using block_api.get_block_range."""
    if start_block == end_block:
        print(f"Waiting for more blocks before fetching...")
        time.sleep(SLEEP_INTERVAL)
    data = {
        "jsonrpc": "2.0",
        "method": "block_api.get_block_range",
        "params": {
            "starting_block_num": start_block,
            "count": end_block - start_block + 1
        },
        "id": 1
    }
    retries = 10
    api_index = 0
    while retries > 0:
        try:
            response = requests.post(HIVE_API[api_index % len(HIVE_API)], json=data).json()
            result = response.get('result')
            if result['blocks']:
                print(f"Fetched block range {start_block} to {end_block} from {HIVE_API[api_index % len(HIVE_API)]}")
                return result['blocks']
            else:
                print(f"Failed to fetch block range {start_block} to {end_block} from {HIVE_API[api_index % len(HIVE_API)]}. Retrying...")
                time.sleep(SLEEP_INTERVAL)
                retries -= 1
                api_index += 1  # Switch to the next API on each retry
        except Exception as e:
            print(f"Exception while fetching block range {start_block} to {end_block} from {HIVE_API[api_index % len(HIVE_API)]}: {e}")
            time.sleep(SLEEP_INTERVAL)
            retries -= 1
            api_index += 1  # Switch to the next API on each retry
    print("Max retries exceeded. Aborting.")
    raise Exception("Failed to fetch block data after multiple retries.")

def load_last_block():
    """Load the last processed block number from MongoDB."""
    doc = collection.find_one({'_id': 'last_block'})
    if doc:
        return doc.get('block_num')
    return None

def save_last_block(block_num):
    """Save the last processed block number to MongoDB."""
    collection.update_one(
        {'_id': 'last_block'},
        {'$set': {'block_num': block_num}},
        upsert=True
    )
    print(f"Saved last block: {block_num}")

def listen_for_comments(start_block, end_block):
    """Listen for comments in a range of blocks and process them."""
    blocks = get_block_range(start_block, end_block)
    comments = []
    for block in blocks:
        block_timestamp = block['timestamp']  # Use timestamp directly for transaction
        for transaction in block['transactions']:
            for operation in transaction['operations']:
                if operation['type'] == 'comment_operation':
                    comment_data = operation['value']
                    if comment_data['parent_author'] != '':
                        comment = {
                            'author': comment_data['author'],
                            'permlink': comment_data['permlink'],
                            'parent_author': comment_data['parent_author'],
                            'parent_permlink': comment_data['parent_permlink'],
                            'body': comment_data['body'],
                            'metadata': comment_data["json_metadata"],
                            'block_timestamp': block_timestamp
                        }
                        if is_target_comment(comment):
                            comments.append(comment)
    return comments

def is_target_comment(comment):
    """Check if the comment is a target comment."""
    # Check if '@llamathreads' is mentioned as a full word
    if re.search(r'@\bllamathreads\b', comment['body'], re.IGNORECASE):
        return True
    # Check if the comment is a reply to a comment by 'llamathreads'
    if comment['parent_author'].lower() == 'llamathreads':
        return True
    return False
