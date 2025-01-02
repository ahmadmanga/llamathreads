import os
import logging
import requests
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv
from pymongo import MongoClient
from beem import Hive
from beem.account import Account

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants with default values
DEFAULT_API_NODE = 'https://api.hive.blog'
HIVE_API_NODE = os.getenv('HIVE_API_NODE', DEFAULT_API_NODE)
MONGO_URI = os.getenv('MONGO_URI')
DATABASE_NAME = 'subscriptions'
SUBSCRIBERS_COLLECTION = 'subscribers'
FREETRIAL_COLLECTION = 'freetrial'
BUYERS_COLLECTION = 'buyers'
PROCESSED_TRANSFERS_COLLECTION = 'processed_transfers'
CREATOR_SUB_ACC = os.getenv('CREATOR_SUB_ACC')  # Fetch CREATOR_SUB_ACC from .env
ACCOUNT = os.getenv('ACCOUNT')  # Fetch ACCOUNT from .env
ACTIVE_KEY = os.getenv('ACTIVE_KEY')  # Fetch ACTIVE_KEY from .env
MIN_HBD = float(os.getenv('MIN_HBD', 0.20))
MAX_HBD = float(os.getenv('MAX_HBD', 1.00))
MIN_HIVE = float(os.getenv('MIN_HIVE', 0.50))
MAX_HIVE = float(os.getenv('MAX_HIVE', 2.00))

# Initialize MongoDB client
client = MongoClient(MONGO_URI)
db = client[DATABASE_NAME]
subscribers_collection = db[SUBSCRIBERS_COLLECTION]
freetrial_collection = db[FREETRIAL_COLLECTION]
buyers_collection = db[BUYERS_COLLECTION]
processed_transfers_collection = db[PROCESSED_TRANSFERS_COLLECTION]

# Initialize Hive client with active key
hive = Hive(nodes=[HIVE_API_NODE], keys=[ACTIVE_KEY])
account = Account(ACCOUNT, blockchain_instance=hive)

def is_valid_api_node(api_node):
    try:
        # Test the API node by sending a simple request
        payload = {
            "jsonrpc": "2.0",
            "method": "condenser_api.get_account_count",
            "params": [],
            "id": 1
        }
        response = requests.post(api_node, json=payload, timeout=5)
        response.raise_for_status()
        return True
    except requests.RequestException as e:
        logger.warning(f'API node {api_node} is not responsive: {e}')
        return False

# Function to fetch account history
def fetch_account_history(account_name, start=-1, limit=1000):
    payload = {
        "jsonrpc": "2.0",
        "method": "condenser_api.get_account_history",
        "params": [account_name, start, limit],
        "id": 1
    }
    try:
        response = requests.post(HIVE_API_NODE, json=payload)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f'Error fetching account history from {HIVE_API_NODE}: {e}')
        return None

# Function to process and filter transfers
def process_transfers(data, subscription_payment_account, creator_sub_acc):
    if data is None or 'result' not in data:
        logger.error('Unexpected API response format')
        return [], []
    valid_transfers = []
    invalid_transfers = []
    current_time = datetime.utcnow()
    thirty_one_days_ago = current_time - timedelta(days=31)
    for operation in data['result']:
        op_details = operation[1]
        if op_details['op'][0] == 'transfer':
            transfer = op_details['op'][1]
            transfer_time = datetime.strptime(op_details['timestamp'], '%Y-%m-%dT%H:%M:%S')
            if (transfer['to'] == subscription_payment_account and 
                (transfer['amount'].endswith(' HIVE') or transfer['amount'].endswith(' HBD')) and 
                transfer['memo'].lower() == f'subscribe:{creator_sub_acc}'):
                if transfer_time >= thirty_one_days_ago:
                    valid_transfers.append({
                        'username': transfer['from'],
                        'timestamp': transfer_time
                    })
                else:
                    invalid_transfers.append({
                        'username': transfer['from'],
                        'timestamp': transfer_time,
                        'days_off': (current_time - transfer_time).days
                    })
    return valid_transfers, invalid_transfers

# Function to update subscribers in MongoDB
def update_subscribers(valid_transfers):
    current_time = datetime.utcnow()
    thirty_one_days_ago = current_time - timedelta(days=31)
    # Remove old subscribers
    subscribers_collection.delete_many({'timestamp': {'$lt': thirty_one_days_ago}})
    # Update or insert valid subscribers
    for transfer in valid_transfers:
        subscriber_data = {
            'username': transfer['username'],
            'timestamp': transfer['timestamp']
        }
        subscribers_collection.update_one(
            {'username': transfer['username']},
            {'$set': subscriber_data},
            upsert=True
        )

# Function to get the list of subscribers
def subscribers_list(subscription_payment_account, creator_sub_acc):
    global HIVE_API_NODE  # Declare HIVE_API_NODE as global to modify it
    # Check if the specified API node is responsive
    if not is_valid_api_node(HIVE_API_NODE):
        logger.warning(f'Using default API node {DEFAULT_API_NODE} as {HIVE_API_NODE} is not responsive.')
        HIVE_API_NODE = DEFAULT_API_NODE
    # Fetch account history for the subscription payment account
    data = fetch_account_history(subscription_payment_account)
    # Process and filter transfers
    valid_transfers, invalid_transfers = process_transfers(data, subscription_payment_account, creator_sub_acc)
    # Log invalid subscriptions
    for invalid_transfer in invalid_transfers:
        logger.info(f"Invalid subscription for user {invalid_transfer['username']} - Off by {invalid_transfer['days_off']} days")
    # Update subscribers in MongoDB
    update_subscribers(valid_transfers)
    # Fetch all active subscribers
    subscribers = list(subscribers_collection.find({}, {'username': 1, '_id': 0}))
    free_trials = list(freetrial_collection.find({}, {'username': 1, '_id': 0}))
    # Combine subscribers and free trials
    all_users = [subscriber['username'] for subscriber in subscribers] + [free_trial['username'] for free_trial in free_trials]
    # Remove duplicates
    all_users = list(set(all_users))
    # Log the total number of users with valid subscriptions or free trial
    logger.info(f'Total users with valid subscriptions or free trial: {len(all_users)}')
    # Return the combined list of usernames
    return all_users

# Function to add buyers
def add_buyers():
    current_time = datetime.utcnow()
    one_day_ago = current_time - timedelta(days=1)
    twenty_five_hours_ago = current_time - timedelta(hours=25)
    limit = 1000
    start = -1
    valid_buyers = []
    # Remove old processed transfers
    processed_transfers_collection.delete_many({'timestamp': {'$lt': twenty_five_hours_ago}})
    while True:
        data = fetch_account_history(ACCOUNT, start, limit)
        if data is None or 'result' not in data:
            logger.error('Unexpected API response format')
            break
        for operation in data['result']:
            op_details = operation[1]
            if op_details['op'][0] == 'transfer':
                transfer = op_details['op'][1]
                transfer_time = datetime.strptime(op_details['timestamp'], '%Y-%m-%dT%H:%M:%S')
                amount_value = float(transfer['amount'].split()[0])
                amount_currency = transfer['amount'].split()[1]
                tx_id = op_details['trx_id']
                # Check if the transfer is already processed
                if processed_transfers_collection.find_one({'tx_id': tx_id}):
                    logger.info(f"Transfer {tx_id} already processed. Skipping.")
                    continue
                if transfer['to'] == ACCOUNT:
                    if amount_currency == 'HBD':
                        if amount_value < MIN_HBD or amount_value > MAX_HBD:
                            send_transfer(transfer['from'], amount_value, amount_currency, f"Returning {transfer['amount']} as it is not within the threshold of {MIN_HBD}~{MAX_HBD} HBD.")
                        elif MIN_HBD <= amount_value <= MAX_HBD:
                            valid_buyers.append({'username': transfer['from'], 'timestamp': transfer_time})
                            send_transfer(transfer['from'], 0.001, 'HIVE', f"Congrats! You're now subscribed for 1 day to {ACCOUNT}'s services!")
                            remaining_hours = 24 - ((current_time - transfer_time).total_seconds() / 3600)
                            logger.info(f"{transfer['from']} has {remaining_hours:.2f} hours remaining in their subscription.")
                    elif amount_currency == 'HIVE':
                        if amount_value < MIN_HIVE or amount_value > MAX_HIVE:
                            send_transfer(transfer['from'], amount_value, amount_currency, f"Returning {transfer['amount']} as it is not within the threshold of {MIN_HIVE}~{MAX_HIVE} HIVE.")
                        elif MIN_HIVE <= amount_value <= MAX_HIVE:
                            valid_buyers.append({'username': transfer['from'], 'timestamp': transfer_time})
                            send_transfer(transfer['from'], 0.001, 'HIVE', f"Congrats! You're now subscribed for 1 day to {ACCOUNT}'s services!")
                            remaining_hours = 24 - ((current_time - transfer_time).total_seconds() / 3600)
                            logger.info(f"{transfer['from']} has {remaining_hours:.2f} hours remaining in their subscription.")
                    # Mark the transfer as processed
                    processed_transfers_collection.insert_one({
                        'tx_id': tx_id,
                        'timestamp': transfer_time
                    })
        # Check if the oldest transaction is within the last 24 hours
        if data['result']:
            oldest_transaction_time = datetime.strptime(data['result'][0][1]['timestamp'], '%Y-%m-%dT%H:%M:%S')
            if oldest_transaction_time >= one_day_ago:
                start = data['result'][0][0]
                continue
        break
    # Remove old buyers
    buyers_collection.delete_many({'timestamp': {'$lt': one_day_ago}})
    # Add new valid buyers
    for buyer in valid_buyers:
        buyers_collection.update_one(
            {'username': buyer['username']},
            {'$set': {'timestamp': buyer['timestamp']}},
            upsert=True
        )
    # Notify users whose subscription has ended
    old_buyers = buyers_collection.find({'timestamp': {'$lt': one_day_ago}})
    for old_buyer in old_buyers:
        send_transfer(old_buyer['username'], 0.001, 'HIVE', f"Your 24 hours subscription has ended. Thanks for using `{ACCOUNT}`")
    # Fetch all active buyers
    active_buyers = buyers_collection.find({}, {'username': 1, '_id': 0})
    all_buyers = [buyer['username'] for buyer in active_buyers]
    # Log the total number of active buyers
    logger.info(f'Total active buyers: {len(all_buyers)}')
    return all_buyers

# Function to send a transfer
def send_transfer(to_account, amount, asset, memo):
    try:
        # Log detailed transfer information
        logger.info(f"Attempting to transfer {amount} {asset} to {to_account} with memo: {memo}")
        
        # Perform the transfer
        account.transfer(to_account, amount, asset, memo)
        
        # Log successful transfer
        logger.info(f"Transfer of {amount} {asset} to {to_account} successful with memo: {memo}")
        
        # Add a 5-second delay after successful transfer
        time.sleep(3)
    except Exception as e:
        logger.error(f"Error sending transfer of {amount} {asset} to {to_account} with memo: {memo}. Error: {e}")

# Example usage
def list_all_users():
    SUBSCRIPTION_PAYMENT_ACCOUNT = 'leosubscriptions'
    subscribers = subscribers_list(SUBSCRIPTION_PAYMENT_ACCOUNT, CREATOR_SUB_ACC)
    buyers = add_buyers()
    all_users = list(set(subscribers + buyers))
    print(all_users)
    return all_users
