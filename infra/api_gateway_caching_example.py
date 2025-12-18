# Example of how to add caching to specific routes in your API Gateway

# For count endpoints that don't change often (5 minute cache)
route_label_validation_count = aws.apigatewayv2.Route(
    "label_validation_count_route",
    api_id=api.id,
    route_key="GET /label_validation_count",
    target=integration_label_validation_count.id.apply(lambda id: f"integrations/{id}"),
    # Note: HTTP APIs (v2) don't support per-route caching
    # You'd need to switch to REST API (v1) for caching
)

# Alternative: REST API with caching
rest_api = aws.apigateway.RestApi(
    "my-rest-api",
    # ... other config
)

# Method with caching enabled
label_count_method = aws.apigateway.Method(
    "label_count_method",
    rest_api_id=rest_api.id,
    resource_id=label_count_resource.id,
    http_method="GET",
    authorization="NONE",
    # Enable caching for this method
    request_parameters={
        "method.request.querystring.label": False,  # Cache by label parameter
    },
)

# Method response with cache configuration
method_response = aws.apigateway.MethodResponse(
    "label_count_response",
    rest_api_id=rest_api.id,
    resource_id=label_count_resource.id,
    http_method=label_count_method.http_method,
    status_code="200",
    response_parameters={
        "method.response.header.Cache-Control": True,
    },
)

# Stage with cache cluster
stage = aws.apigateway.Stage(
    "prod",
    rest_api_id=rest_api.id,
    deployment_id=deployment.id,
    cache_cluster_enabled=True,
    cache_cluster_size="0.5",  # 0.5 GB - smallest size
    # Cache TTL is set per method
)


# In your Lambda, return cache headers
def handler(event, context):
    return {
        "statusCode": 200,
        "headers": {
            "Cache-Control": "max-age=300",  # 5 minutes
        },
        "body": json.dumps(counts),
    }
