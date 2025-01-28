import json

def parse_json_file(json_path):
    # Read the JSON file
    with open(json_path, 'r') as file:
        data = json.load(file)

    # Prepare a list to hold the results
    results = []

    # Iterate over each top-level key in the JSON
    for key, details in data.items():
        # Extract the value and the word_centroids if present
        if "value" and "word_centroids" in details:
            value = details.get('value')
            word_centroids = details.get('word_centroids', [])

        # Count the number of word_centroids
            num_word_centroids = len(word_centroids)

        # Add the extracted data to our results list
            results.append({
                'key': key,
                'value': value,
                'word_centroids': word_centroids,
                'num_word_centroids': num_word_centroids,
                'line_item': False    
            })
        
        if isinstance(details, list):
            for item in details:
                if "value" and "word_centroids" in item:
                    value = item.get('value')
                    word_centroids = item.get('word_centroids', [])

                    # Count the number of word_centroids
                    num_word_centroids = len(word_centroids)

                    # Add the extracted data to our results list
                    results.append({
                        'key': key,
                        'value': value,
                        'word_centroids': word_centroids,
                        'num_word_centroids': num_word_centroids,
                        'line_item': True
                    })

    return results

if __name__ == "__main__":
    json_file = "data_labeling_grok_response_00001.json"  # Replace with your JSON file path
    parsed_data = parse_json_file(json_file)

    # Print the parsed data
    for item in parsed_data:
        print("Key:", item['key'])
        print("Value:", item['value'])
        print("Word Centroids:", item['word_centroids'])
        print("Number of Word Centroids:", item['num_word_centroids'])
        print("-" * 40)