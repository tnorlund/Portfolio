from typing import Dict, List, Optional, Tuple
from datetime import datetime
import re
import logging

logger = logging.getLogger(__name__)

# Common date formats
DATE_FORMATS = [
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%m/%d/%y",
    "%d/%m/%Y",
    "%d/%m/%y",
    "%B %d, %Y",
    "%b %d, %Y",
    "%d %B %Y",
    "%d %b %Y",
    "%Y/%m/%d",
]

# Common time formats
TIME_FORMATS = [
    "%H:%M:%S",
    "%H:%M",
    "%I:%M:%S %p",
    "%I:%M %p",
    "%H:%M:%S %Z",
    "%H:%M %Z",
]

def parse_datetime(
    date_str: str,
    time_str: Optional[str] = None
) -> Optional[datetime]:
    """Parse date and time strings into a datetime object.
    
    Args:
        date_str: Date string to parse
        time_str: Optional time string to parse
        
    Returns:
        Parsed datetime object or None if parsing fails
    """
    try:
        # Try to parse date
        date = None
        for fmt in DATE_FORMATS:
            try:
                date = datetime.strptime(date_str, fmt)
                break
            except ValueError:
                continue
        
        if not date:
            logger.warning(f"Could not parse date string: {date_str}")
            return None
        
        # If no time provided, return just the date
        if not time_str:
            return date
        
        # Try to parse time
        time = None
        for fmt in TIME_FORMATS:
            try:
                time = datetime.strptime(time_str, fmt)
                break
            except ValueError:
                continue
        
        if not time:
            logger.warning(f"Could not parse time string: {time_str}")
            return date
        
        # Combine date and time
        return datetime.combine(date.date(), time.time())
        
    except Exception as e:
        logger.error(f"Error parsing datetime: {str(e)}")
        return None

def extract_datetime(text: str) -> Optional[Dict]:
    """Extract date and time from text.
    
    Args:
        text: Text to extract from
        
    Returns:
        Dict containing extracted date and time or None if not found
    """
    try:
        # Common date patterns
        date_patterns = [
            r"\d{1,2}[-/]\d{1,2}[-/]\d{2,4}",  # MM/DD/YYYY or DD/MM/YYYY
            r"\d{4}[-/]\d{1,2}[-/]\d{1,2}",     # YYYY/MM/DD
            r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}",
            r"\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}",
        ]
        
        # Common time patterns
        time_patterns = [
            r"\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM)?",
            r"\d{1,2}\s*(?:AM|PM)",
        ]
        
        # Find date
        date_match = None
        for pattern in date_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                date_match = match
                break
        
        # Find time
        time_match = None
        for pattern in time_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                time_match = match
                break
        
        if not date_match:
            return None
        
        # Parse date and time
        date_str = date_match.group()
        time_str = time_match.group() if time_match else None
        
        dt = parse_datetime(date_str, time_str)
        if not dt:
            return None
        
        return {
            "value": dt.isoformat(),
            "date_format": date_str,
            "time_format": time_str if time_str else None,
            "confidence": 0.9 if time_match else 0.8,
        }
        
    except Exception as e:
        logger.error(f"Error extracting datetime: {str(e)}")
        return None

def format_datetime(
    dt: datetime,
    include_time: bool = True,
    include_seconds: bool = False
) -> str:
    """Format a datetime object into a string.
    
    Args:
        dt: Datetime object to format
        include_time: Whether to include time in output
        include_seconds: Whether to include seconds in time
        
    Returns:
        Formatted datetime string
    """
    if include_time:
        if include_seconds:
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        return dt.strftime("%Y-%m-%d %H:%M")
    return dt.strftime("%Y-%m-%d")

def is_valid_date(date_str: str) -> bool:
    """Check if a date string is valid.
    
    Args:
        date_str: Date string to validate
        
    Returns:
        True if date is valid, False otherwise
    """
    try:
        # Try each date format
        for fmt in DATE_FORMATS:
            try:
                datetime.strptime(date_str, fmt)
                return True
            except ValueError:
                continue
        return False
    except Exception as e:
        logger.error(f"Error validating date: {str(e)}")
        return False

def is_valid_time(time_str: str) -> bool:
    """Check if a time string is valid.
    
    Args:
        time_str: Time string to validate
        
    Returns:
        True if time is valid, False otherwise
    """
    try:
        # Try each time format
        for fmt in TIME_FORMATS:
            try:
                datetime.strptime(time_str, fmt)
                return True
            except ValueError:
                continue
        return False
    except Exception as e:
        logger.error(f"Error validating time: {str(e)}")
        return False

def get_date_range(
    start_date: datetime,
    end_date: datetime
) -> List[datetime]:
    """Get a list of dates between two dates.
    
    Args:
        start_date: Start date
        end_date: End date
        
    Returns:
        List of dates between start and end
    """
    dates = []
    current = start_date
    
    while current <= end_date:
        dates.append(current)
        current = current.replace(day=current.day + 1)
    
    return dates

def get_time_difference(
    time1: datetime,
    time2: datetime
) -> Tuple[int, int, int]:
    """Calculate the difference between two times.
    
    Args:
        time1: First time
        time2: Second time
        
    Returns:
        Tuple of (hours, minutes, seconds) difference
    """
    diff = time2 - time1
    hours = diff.seconds // 3600
    minutes = (diff.seconds % 3600) // 60
    seconds = diff.seconds % 60
    
    return (hours, minutes, seconds) 