import json
import re
import logging
import sys

# Setup logging for context_helper
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', stream=sys.stdout)
logger = logging.getLogger('context_helper')

def find_context_keywords(messages, keywords_file='helper_keywords.json'):
    """Find and prioritize context keywords in messages.
    
    Args:
        messages (list): List of message dictionaries.
        keywords_file (str): Path to JSON file with keywords.
        
    Returns:
        list: Prioritized context messages.
    """
    # Load keywords from JSON file
    try:
        with open(keywords_file, 'r') as file:
            data = json.load(file)
            keywords = data['keywords']
    except FileNotFoundError:
        logger.warning(f"File {keywords_file} not found. Treating it as an empty file without keywords.")
        return []

    # Prepare a dictionary to hold context messages by priority
    context_messages = {'HIGH': [], 'MID': [], 'LOW': []}
    seen_messages = set()
    found_keywords = set()

    # Combine all messages into a single text for searching
    combined_messages = ' '.join(msg['content'] for msg in messages)

    # Compile a regex pattern for case-insensitive, whole-word matching
    # Allow special characters like @ and # in keywords
    pattern = re.compile(r'\b(' + '|'.join(re.escape(keyword) for item in keywords for keyword in item['keywords']) + r')\b', re.IGNORECASE)

    # Find matches
    matches = pattern.findall(combined_messages)

    # Log found keywords
    for match in matches:
        found_keywords.add(match.lower())
    if found_keywords:
        logger.info(f"Found keywords: {', '.join(found_keywords)}")

    # Add context messages based on matches
    for keyword_item in keywords:
        if any(keyword.lower() in found_keywords for keyword in keyword_item['keywords']):
            message = keyword_item['message']
            if message not in seen_messages:
                print(message)
                seen_messages.add(message)
                context_messages[keyword_item['priority']].append({"role": get_role_from_priority(keyword_item['priority']), "content": message})

    # Return the context messages, ordered by priority
    return context_messages['HIGH'] + context_messages['MID'] + context_messages['LOW']

def get_role_from_priority(priority):
    if priority == 'HIGH':
        return 'system'
    elif priority == 'MID':
        return 'important_context'
    elif priority == 'LOW':
        return 'low_priority_context'
    else:
        return 'user'
