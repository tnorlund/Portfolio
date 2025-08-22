"""CloudWatch custom metrics for ChromaDB compaction operations."""

from typing import Dict, Any, Optional
from datetime import datetime, timezone

try:
    import boto3
    from botocore.exceptions import ClientError
    CLOUDWATCH_AVAILABLE = True
except ImportError:
    CLOUDWATCH_AVAILABLE = False

from .logging import get_operation_logger


class CompactionMetrics:
    """ChromaDB compaction-specific metrics tracking."""

    def __init__(self, namespace: str = "ChromaDB/Compaction"):
        """Initialize metrics client.
        
        Args:
            namespace: CloudWatch namespace for metrics
        """
        self.namespace = namespace
        self.logger = get_operation_logger(__name__)
        self._client = None
        
        if CLOUDWATCH_AVAILABLE:
            try:
                self._client = boto3.client('cloudwatch')
            except Exception as e:
                self.logger.warning("CloudWatch client initialization failed", 
                                  error=str(e))
                self._client = None
    
    def _put_metric_data(self, metric_name: str, value: float, unit: str, 
                        dimensions: Optional[Dict[str, str]] = None):
        """Put metric data to CloudWatch."""
        if not self._client:
            # Log metric locally if CloudWatch unavailable
            self.logger.debug("Metric recorded locally",
                            metric_name=metric_name,
                            value=value,
                            unit=unit,
                            dimensions=dimensions or {})
            return
            
        try:
            metric_data = {
                'MetricName': metric_name,
                'Value': value,
                'Unit': unit,
                'Timestamp': datetime.now(timezone.utc)
            }
            
            if dimensions:
                metric_data['Dimensions'] = [
                    {'Name': k, 'Value': v} for k, v in dimensions.items()
                ]
            
            self._client.put_metric_data(
                Namespace=self.namespace,
                MetricData=[metric_data]
            )
            
        except ClientError as e:
            self.logger.error("Failed to put metric data",
                            metric_name=metric_name,
                            error=str(e))
        except Exception as e:
            self.logger.error("Unexpected error putting metric data", 
                            metric_name=metric_name,
                            error=str(e))

    def count(self, metric_name: str, value: int = 1, 
              dimensions: Optional[Dict[str, str]] = None):
        """Record a count metric.
        
        Args:
            metric_name: Name of the metric
            value: Count value (default: 1)
            dimensions: Additional dimensions for the metric
        """
        self._put_metric_data(metric_name, float(value), "Count", dimensions)

    def gauge(self, metric_name: str, value: float, unit: str = "None",
              dimensions: Optional[Dict[str, str]] = None):
        """Record a gauge metric.
        
        Args:
            metric_name: Name of the metric
            value: Gauge value
            unit: Unit of measurement
            dimensions: Additional dimensions for the metric
        """
        self._put_metric_data(metric_name, value, unit, dimensions)

    def timer(self, metric_name: str, duration_seconds: float,
              dimensions: Optional[Dict[str, str]] = None):
        """Record a timing metric.
        
        Args:
            metric_name: Name of the metric
            duration_seconds: Duration in seconds
            dimensions: Additional dimensions for the metric
        """
        self._put_metric_data(metric_name, duration_seconds, "Seconds", dimensions)

    # ChromaDB Compaction-specific metrics
    
    def track_compaction_operation(self,
                                 collection: str,
                                 operation_type: str,
                                 processed_deltas: int,
                                 processing_time: float,
                                 records_updated: int,
                                 success: bool = True):
        """Track a complete compaction operation.
        
        Args:
            collection: ChromaDB collection name
            operation_type: Type of operation (delta_processing, metadata_update, etc.)
            processed_deltas: Number of deltas processed
            processing_time: Time taken in seconds
            records_updated: Number of records updated
            success: Whether operation succeeded
        """
        base_dimensions = {
            "Collection": collection,
            "OperationType": operation_type
        }
        
        # Core metrics
        self.timer("CompactionProcessingTime", processing_time, base_dimensions)
        self.count("DeltasProcessed", processed_deltas, base_dimensions)
        self.count("RecordsUpdated", records_updated, base_dimensions)
        
        # Success/failure tracking
        result_dimensions = {**base_dimensions, "Success": str(success)}
        self.count("CompactionOperations", 1, result_dimensions)
        
        if success:
            self.count("CompactionOperationsSuccess", 1, base_dimensions)
        else:
            self.count("CompactionOperationsFailure", 1, base_dimensions)

    def track_stream_processing(self,
                              collection: str,
                              event_name: str,
                              records_processed: int,
                              processing_time: float,
                              success: bool = True):
        """Track DynamoDB stream message processing.
        
        Args:
            collection: Target ChromaDB collection
            event_name: DynamoDB event name (INSERT, MODIFY, REMOVE)
            records_processed: Number of stream records processed
            processing_time: Time taken in seconds
            success: Whether processing succeeded
        """
        base_dimensions = {
            "Collection": collection,
            "EventName": event_name
        }
        
        self.timer("StreamProcessingTime", processing_time, base_dimensions)
        self.count("StreamRecordsProcessed", records_processed, base_dimensions)
        
        # Success/failure tracking
        result_dimensions = {**base_dimensions, "Success": str(success)}
        self.count("StreamOperations", 1, result_dimensions)

    def track_s3_operation(self,
                          operation_type: str,
                          collection: str,
                          file_size_bytes: Optional[int] = None,
                          duration_seconds: Optional[float] = None,
                          success: bool = True):
        """Track S3 operations (upload/download snapshots and deltas).
        
        Args:
            operation_type: Type of S3 operation (upload_snapshot, download_snapshot, upload_delta)
            collection: ChromaDB collection name
            file_size_bytes: Size of file transferred
            duration_seconds: Time taken for operation
            success: Whether operation succeeded
        """
        dimensions = {
            "Collection": collection,
            "OperationType": operation_type,
            "Success": str(success)
        }
        
        self.count("S3Operations", 1, dimensions)
        
        if file_size_bytes is not None:
            self.gauge("S3TransferSizeBytes", float(file_size_bytes), "Bytes", dimensions)
            
        if duration_seconds is not None:
            self.timer("S3OperationTime", duration_seconds, dimensions)

    def track_chromadb_operation(self,
                               collection: str,
                               operation_type: str,
                               record_count: int,
                               duration_seconds: float,
                               success: bool = True):
        """Track ChromaDB database operations.
        
        Args:
            collection: ChromaDB collection name
            operation_type: Type of operation (update, query, add, delete)
            record_count: Number of records affected
            duration_seconds: Time taken for operation
            success: Whether operation succeeded
        """
        dimensions = {
            "Collection": collection,
            "OperationType": operation_type,
            "Success": str(success)
        }
        
        self.count("ChromaDBOperations", 1, dimensions)
        self.count("ChromaDBRecords", record_count, dimensions)
        self.timer("ChromaDBOperationTime", duration_seconds, dimensions)

    def track_lambda_execution(self,
                             function_name: str,
                             execution_time: float,
                             memory_used_mb: Optional[float] = None,
                             success: bool = True,
                             error_type: Optional[str] = None):
        """Track Lambda function execution metrics.
        
        Args:
            function_name: Name of the Lambda function
            execution_time: Total execution time in seconds
            memory_used_mb: Memory used in MB (if available)
            success: Whether execution succeeded
            error_type: Type of error if failed
        """
        dimensions = {
            "FunctionName": function_name,
            "Success": str(success)
        }
        
        if error_type:
            dimensions["ErrorType"] = error_type
            
        self.count("LambdaExecutions", 1, dimensions)
        self.timer("LambdaExecutionTime", execution_time, dimensions)
        
        if memory_used_mb is not None:
            self.gauge("LambdaMemoryUsage", memory_used_mb, "Megabytes", dimensions)


# Global metrics instance
metrics = CompactionMetrics()