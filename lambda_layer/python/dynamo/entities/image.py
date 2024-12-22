class Image:
    def __init__(self, id: int, width: int, height: int):
        """Constructs a new Image object for DynamoDB

        Args:
            id (int): Number identifying the image
            width (int): The width of the image in pixels
            height (int): The height of the image in pixels

        Raises:
            ValueError: When the ID is not a positive integer
        """
        # Ensure the ID is a positive integer
        if id <= 0:
            raise ValueError("id must be a positive integer")
        self.id = id
        self.width = width
        self.height = height
