"""Health check tools for MCP server."""

from core.client_manager import get_client_manager


def get_health_status() -> dict:
    """Get health status of all services."""
    try:
        manager = get_client_manager()

        status = {
            "initialized": True,
            "config_loaded": bool(getattr(manager, "_pulumi_config", {})),
            "services": {},
        }

        # Test DynamoDB
        try:
            dynamo_client = manager.dynamo
            response = dynamo_client._client.describe_table(
                TableName=manager.config.dynamo_table
            )
            status["services"]["dynamo"] = {
                "status": "healthy",
                "table": manager.config.dynamo_table,
                "item_count": response["Table"]["ItemCount"],
            }
        except Exception as e:
            status["services"]["dynamo"] = {"status": "error", "error": str(e)}

        # Test Pinecone
        try:
            pinecone_index = manager.pinecone
            stats = pinecone_index.describe_index_stats()
            status["services"]["pinecone"] = {
                "status": "healthy",
                "index": manager.config.pinecone_index_name,
                "vector_count": stats.total_vector_count,
            }
        except Exception as e:
            status["services"]["pinecone"] = {
                "status": "error",
                "error": str(e),
            }

        return status

    except Exception as e:
        return {
            "initialized": False,
            "config_loaded": False,
            "services": {"error": str(e)},
        }


def test_connection() -> dict:
    """Test connection to DynamoDB and Pinecone."""
    return get_health_status()