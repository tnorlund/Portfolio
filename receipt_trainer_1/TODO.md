# Receipt Trainer ToDo List

## Model Training Pipeline

- [x] Implement basic ReceiptTrainer class
- [x] Add support for SROIE dataset
- [x] Add DynamoDB metrics logging
- [x] Add model checkpointing
- [ ] Support for multiple model architectures
- [ ] Hyperparameter optimization

## Infrastructure

- [x] Implement JobQueue class for SQS integration
- [x] Create worker process for SQS job handling
- [x] Implement EC2 spot instance interruption handling
- [x] Add EFS integration for shared storage
- [x] Create instance registry with DynamoDB
- [x] Add command-line interface for job submission and worker management
- [x] Create example EC2 user-data script for spot instances
- [ ] Add CloudWatch metric integration
- [x] Implement auto-scaling based on queue depth

## Documentation

- [x] Add README with usage instructions
- [x] Document SQS/EFS/EC2 integration
- [ ] Create architecture diagram
- [ ] Add API documentation

## Testing

- [ ] Add unit tests for JobQueue
- [ ] Add unit tests for worker process
- [ ] Add integration tests for SQS/EFS integration
- [ ] Add infrastructure tests

## Future Enhancements

- [ ] Support for distributed training across multiple instances (model parallelism)
- [ ] Add support for Amazon SageMaker integration
- [ ] Create web interface for job monitoring
- [ ] Add notification system for job status updates
