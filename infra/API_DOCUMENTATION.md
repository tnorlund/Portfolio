# API Documentation

This document provides a comprehensive overview of all API endpoints available in the Portfolio project.

## Base URLs

- **Production**: `https://api.tylernorlund.com`
- **Development**: `https://dev-api.tylernorlund.com`

## Authentication

Currently, all endpoints are public and do not require authentication.

## Endpoints

### Health Check
```
GET /health_check
```
Returns the health status of the API.

**Response:**
```json
{
  "status": "ok",
  "timestamp": "2024-12-25T12:00:00Z"
}
```

### AI Usage Metrics
```
GET /ai_usage
```
Returns AI service usage metrics and costs.

**Query Parameters:**
- `start_date` (string, optional): Start date in YYYY-MM-DD format. Default: 30 days ago
- `end_date` (string, optional): End date in YYYY-MM-DD format. Default: today
- `service` (string, optional): Filter by service (openai, anthropic, google_places)
- `operation` (string, optional): Filter by operation type (completion, embedding, code_review)
- `aggregation` (string, optional): Comma-separated aggregation levels (day, service, model, operation). Default: "day,service,model,operation"

**Example Request:**
```bash
curl "https://dev-api.tylernorlund.com/ai_usage?start_date=2024-12-01&service=openai&aggregation=day,model"
```

**Response:**
```json
{
  "summary": {
    "total_cost_usd": 45.67,
    "total_tokens": 125000,
    "total_api_calls": 234,
    "average_cost_per_call": 0.195,
    "date_range": {
      "start": "2024-12-01",
      "end": "2024-12-25"
    }
  },
  "aggregations": {
    "by_day": {
      "2024-12-01": {
        "cost_usd": 5.23,
        "tokens": 15000,
        "api_calls": 25
      }
    },
    "by_model": {
      "gpt-3.5-turbo": {
        "cost_usd": 30.12,
        "tokens": 100000,
        "api_calls": 180
      }
    }
  }
}
```

### Receipt Processing
```
GET /receipts
```
Returns a list of processed receipts.

**Query Parameters:**
- `limit` (integer, optional): Number of results to return. Default: 20, Max: 100
- `offset` (integer, optional): Pagination offset. Default: 0

### Process Receipt
```
GET /process
```
Processes a new receipt image.

**Query Parameters:**
- `image_url` (string, required): URL of the receipt image to process

### Image Count
```
GET /image_count
```
Returns the total count of processed images.

### Random Image Details
```
GET /random_image_details
```
Returns details of a random processed image.

### Label Validation Count
```
GET /label_validation_count
```
Returns validation statistics for receipt labels.

### Merchant Counts
```
GET /merchant_counts
```
Returns merchant frequency statistics.

### Receipt Count
```
GET /receipt_count
```
Returns the total count of processed receipts.

### Images
```
GET /images
```
Returns a list of processed images.

**Query Parameters:**
- `limit` (integer, optional): Number of results to return
- `offset` (integer, optional): Pagination offset

## Error Responses

All endpoints return consistent error responses:

```json
{
  "error": "Error message",
  "statusCode": 400
}
```

Common HTTP status codes:
- `200`: Success
- `400`: Bad Request (invalid parameters)
- `404`: Not Found
- `500`: Internal Server Error

## Rate Limiting

The API implements rate limiting with:
- Burst limit: 10,000 requests
- Sustained rate: 20,000 requests per second

## CORS

The API supports CORS for the following origins:
- `http://localhost:3000`
- `https://tylernorlund.com`
- `https://www.tylernorlund.com`
- `https://dev.tylernorlund.com`

## Infrastructure

The API is built using:
- AWS API Gateway (HTTP API)
- AWS Lambda functions (Python 3.12, ARM64)
- DynamoDB for data storage
- CloudWatch for logging and monitoring

For infrastructure details, see [infra/README.md](README.md).
