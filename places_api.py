import requests
from typing import Dict, Optional
import re

class PlacesAPI:
    """A client for interacting with the Google Places API."""
    
    BASE_URL = "https://maps.googleapis.com/maps/api/place"
    
    def __init__(self, api_key: str):
        """Initialize the Places API client.
        
        Args:
            api_key (str): Your Google Places API key
        """
        self.api_key = api_key
        print(f"Initializing Places API with key: {api_key[:6]}...")  # Only show first 6 chars for security
        
    def search_by_phone(self, phone_number: str) -> Optional[Dict]:
        """Search for a place using a phone number.
        
        Args:
            phone_number (str): The phone number to search for
            
        Returns:
            Optional[Dict]: The place details if found, None otherwise
        """
        # Remove any non-numeric characters from phone number
        phone_number = ''.join(filter(str.isdigit, phone_number))
        
        # Build the URL for the Places API findPlaceFromText endpoint
        url = f"{self.BASE_URL}/findplacefromtext/json"
        
        params = {
            "input": phone_number,
            "inputtype": "phonenumber",
            "fields": "formatted_address,name,place_id,types,business_status",
            "key": self.api_key
        }
        
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            if data["status"] == "OK" and data["candidates"]:
                # Get more details using the place_id
                place_id = data["candidates"][0]["place_id"]
                return self.get_place_details(place_id)
            
            print(f"Phone search response: {data}")  # Debug logging
            return None
            
        except requests.exceptions.RequestException as e:
            print(f"Error searching by phone: {e}")
            return None
            
    def autocomplete_address(self, input_text: str) -> Optional[Dict]:
        """Get address predictions using the Places Autocomplete API.
        
        Args:
            input_text (str): The partial address text
            
        Returns:
            Optional[Dict]: Address predictions if found, None otherwise
        """
        url = f"{self.BASE_URL}/autocomplete/json"
        
        params = {
            "input": input_text,
            "types": "address",  # Focus on address predictions
            "key": self.api_key
        }
        
        try:
            print(f"\nMaking Autocomplete request to: {url}")
            print(f"With params: {params}")
            response = requests.get(url, params=params)
            print(f"Response status code: {response.status_code}")
            response.raise_for_status()
            data = response.json()
            print(f"Autocomplete response: {data}")
            
            if data["status"] == "OK" and data["predictions"]:
                # Return the first (most relevant) prediction
                prediction = data["predictions"][0]
                print(f"\nFound address suggestion: {prediction['description']}")
                return prediction
            return None
            
        except requests.exceptions.RequestException as e:
            print(f"Error in autocomplete: {e}")
            return None

    def search_by_address(self, address: str, receipt_words: list = None) -> Optional[Dict]:
        """Search for a place using an address.
        
        Args:
            address (str): The address to search for
            receipt_words (list, optional): List of words from the receipt to help identify the business
            
        Returns:
            Optional[Dict]: The place details if found, None otherwise
        """
        # First try to get a complete address suggestion
        # Extract the street address part (assuming it starts with numbers)
        street_address = None
        match = re.match(r'\d+[^,]*', address)
        if match:
            street_address = match.group(0).strip()
            print(f"\nTrying autocomplete with street address: {street_address}")
            completion = self.autocomplete_address(street_address)
            if completion:
                address = completion['description']
                print(f"Using completed address: {address}")
        
        url = f"{self.BASE_URL}/findplacefromtext/json"
        
        # Include geometry to get location coordinates
        params = {
            "input": address,
            "inputtype": "textquery",
            "fields": "formatted_address,name,place_id,types,geometry",
            "key": self.api_key
        }
        
        try:
            print(f"\nMaking Places API request to: {url}")
            print(f"With params: {params}")
            response = requests.get(url, params=params)
            print(f"Response status code: {response.status_code}")
            response.raise_for_status()
            data = response.json()
            print(f"Response data: {data}")
            
            if data["status"] == "OK" and data["candidates"]:
                place = data["candidates"][0]
                
                # If we only got a subpremise (address) result, try searching nearby
                if place["types"] == ["subpremise"] and "geometry" in place:
                    print("\nOnly found address location, searching for businesses nearby...")
                    lat = place["geometry"]["location"]["lat"]
                    lng = place["geometry"]["location"]["lng"]
                    
                    # Search nearby with receipt words to help identify the business
                    nearby_result = self.search_nearby(lat, lng, radius=100, receipt_words=receipt_words)
                    if nearby_result:
                        return nearby_result
                
                # Get full details for the place
                return self.get_place_details(place["place_id"])
            return None
            
        except requests.exceptions.RequestException as e:
            print(f"Error searching by address: {e}")
            return None
    
    def search_nearby(self, lat: float, lng: float, keyword: str = None, radius: int = 100, receipt_words: list = None) -> Optional[Dict]:
        """Search for places near a specific location.
        
        Args:
            lat (float): Latitude
            lng (float): Longitude
            keyword (str, optional): Keyword to search for
            radius (int, optional): Search radius in meters
            receipt_words (list, optional): List of words from the receipt to match against business names
            
        Returns:
            Optional[Dict]: The most relevant place if found, None otherwise
        """
        url = f"{self.BASE_URL}/nearbysearch/json"
        
        params = {
            "location": f"{lat},{lng}",
            "radius": radius,
            "type": "grocery_or_supermarket",  # Focus on grocery stores
            "key": self.api_key
        }
        
        if keyword:
            params["keyword"] = keyword
        
        try:
            print(f"\nSearching nearby locations: {url}")
            print(f"With params: {params}")
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            print(f"Nearby search response: {data}")
            
            if data["status"] == "OK" and data["results"]:
                # If we have receipt words, try to find the best matching business
                if receipt_words:
                    print("\nComparing nearby businesses with receipt text...")
                    best_match = None
                    highest_score = 0
                    
                    for place in data["results"]:
                        score = self._compare_with_receipt(place["name"], receipt_words)
                        print(f"Business: {place['name']}, Match score: {score}")
                        if score > highest_score:
                            highest_score = score
                            best_match = place
                    
                    if best_match:
                        print(f"\nBest matching business: {best_match['name']} (score: {highest_score})")
                        return self.get_place_details(best_match["place_id"])
                
                # If no receipt words or no match found, return the first result
                return self.get_place_details(data["results"][0]["place_id"])
            return None
            
        except requests.exceptions.RequestException as e:
            print(f"Error in nearby search: {e}")
            return None

    def _compare_with_receipt(self, business_name: str, receipt_words: list) -> float:
        """Compare a business name with words from the receipt to find matches.
        
        Args:
            business_name (str): Name of the business to compare
            receipt_words (list): List of words from the receipt, in order from top to bottom
            
        Returns:
            float: Score between 0 and 1 indicating how well the business name matches
        """
        # Convert everything to lowercase for comparison
        business_words = set(business_name.lower().split())
        receipt_words = [word.lower() for word in receipt_words]
        
        # Calculate weighted matches based on word position
        total_score = 0
        max_possible_score = 0
        
        for business_word in business_words:
            max_possible_score += 1  # Each word can contribute max of 1 to the score
            
            # Check each receipt word for a match
            for i, receipt_word in enumerate(receipt_words):
                if business_word == receipt_word:
                    # Words in first 10 positions get higher weight
                    position_weight = 1.0 if i < 10 else 0.5
                    total_score += position_weight
                    break  # Only count first occurrence of the word
        
        # Return normalized score
        return total_score / max_possible_score if max_possible_score > 0 else 0

    def get_place_details(self, place_id: str) -> Optional[Dict]:
        """Get detailed information about a place using its place_id.
        
        Args:
            place_id (str): The Google Places ID
            
        Returns:
            Optional[Dict]: Detailed place information if found, None otherwise
        """
        url = f"{self.BASE_URL}/details/json"
        
        # Enhanced fields to get more business information
        fields = [
            "name", "formatted_address", "formatted_phone_number",
            "international_phone_number", "website", "rating",
            "price_level", "business_status", "types",
            "opening_hours", "geometry", "vicinity",
            "plus_code", "user_ratings_total"
        ]
        
        params = {
            "place_id": place_id,
            "fields": ",".join(fields),
            "key": self.api_key
        }
        
        try:
            print("\nGetting detailed place information...")
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            if data["status"] == "OK":
                result = data["result"]
                print(f"\nFound business details:")
                print(f"Name: {result.get('name', 'N/A')}")
                print(f"Type: {', '.join(result.get('types', ['N/A']))}")
                print(f"Phone: {result.get('formatted_phone_number', 'N/A')}")
                print(f"Website: {result.get('website', 'N/A')}")
                return result
            return None
            
        except requests.exceptions.RequestException as e:
            print(f"Error getting place details: {e}")
            return None

# Example usage:
if __name__ == "__main__":
    # Replace with your API key
    api_key = "YOUR_API_KEY"
    places_api = PlacesAPI(api_key)
    
    # Example phone number search
    result = places_api.search_by_phone("+1-234-567-8900")
    if result:
        print("Found by phone:", result)
    
    # Example address search
    result = places_api.search_by_address("123 Main St, Anytown, USA")
    if result:
        print("Found by address:", result) 