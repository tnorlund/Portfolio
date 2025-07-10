# Project Specifications

This directory contains active technical specifications and implementation plans for the receipt processing system.

## 📊 Active Development Roadmap

| Specification | Status | Priority | GitHub Issue | Effort |
|---------------|--------|----------|--------------|---------|
| **🟡 Line Item Processing Enhancement** | 🟡 Next | High | TBD | 2-4 weeks |
| **DynamoDB Entity Consolidation** | 🔴 Planned | Medium | TBD | 2-3 weeks |
| **Agentic Refactor** | 📋 Specified | Low | TBD | 12-16 weeks |

## ✅ Recently Completed

| Specification | Completion Date | PRs |
|---------------|-----------------|-----|
| **AI Usage Tracking (Phases 1-4)** | July 1, 2025 | #131, #132, #133, #145, #148 |
| **Google Places API Tracking** | July 3, 2025 | #158 |
| **Receipt Upload Reformatting** | July 3, 2025 | #160 |

## 🎯 Current Focus

### **🟡 Line Item Processing Enhancement** - PRIORITY
**Pattern Matching + Pinecone Approach (No GPT)**
- Current state: 60-70% accuracy with broken GPT stub
- Target: 90-95% accuracy without GPT dependency
- Location: `receipt_label/data/gpt.py:43` (stub implementation)

### **Implementation Plan**
- **Week 1**: Remove GPT stub, enhance pattern matching (60-70% → 80-85%)
- **Week 2**: Add Pinecone integration for edge cases (80-85% → 85-90%)
- **Week 3**: Store-specific learning and complex patterns (85-90% → 90-95%)
- **Week 4**: Financial validation with thumbs up/down determination

### **DynamoDB Optimization**
- Consolidate 7 analysis entities → 3 entities (60% reduction)
- Phase 1: Merge validation hierarchy
- Phase 2: Implement embedding-based validation

## 📁 Directory Structure

```
spec/
├── line-item-enhancement/      # 🟡 ACTIVE: Line item processing improvement
│   ├── README.md              # 4-week enhancement plan (pattern matching + Pinecone)
│   ├── financial-validation.md # Comprehensive financial validation specification
│   └── currency-analysis-entity.md # DynamoDB schema design for currency analysis
├── technical-analysis/        # System architecture documentation
│   ├── dynamo-entities.md     # DynamoDB entity reference
│   ├── dynamodb-entity-consolidation.md # Entity consolidation plan with Pinecone
│   ├── client-refactor.md     # Client management patterns
│   └── labeler-refactor.md    # ReceiptLabeler architecture
├── agentic-refactor/          # 4-phase architecture transformation [FUTURE]
│   ├── week1-retrieval.md     # RAG-based intelligent labeling
│   ├── week2-tools.md         # Tool registry and standardization
│   ├── week3-orchestration.md # Agent orchestration patterns
│   └── week4-streaming.md     # Real-time streaming and observability
├── archive/                   # Completed work (reference only)
│   ├── completed-2024/        # 2024 completed specifications
│   ├── completed-2025/        # 2025 completed specifications
│   │   ├── ai-usage-tracking/ # ✅ COMPLETED (Phases 1-4)
│   │   │   ├── implementation.md
│   │   │   └── testing-project.md
│   │   └── README.md          # Summary of 2025 completions
│   └── conflicting-plans/     # Deprecated approaches
└── README.md                  # This file (active roadmap)
```

## 📋 Key Active Specifications

### **🟡 Line Item Processing Enhancement** - READY TO START
**4-Week Enhancement Plan**
- **Immediate Action**: Fix broken GPT stub at `receipt_label/data/gpt.py:43`
- **Pattern Matching**: Add support for quantity formats like "2 @ $5.99"
- **Pinecone Integration**: Retrieval-augmented processing for edge cases
- **Financial Validation**: Comprehensive thumbs up/down determination
- **DynamoDB Optimization**: Consolidate entities during implementation
- See: `line-item-enhancement/README.md`, `line-item-enhancement/financial-validation.md`, and `technical-analysis/dynamodb-entity-consolidation.md`

### **DynamoDB Entity Consolidation** 🔴 PLANNED
**2-Phase Consolidation Plan**
- **Phase 1**: Consolidate validation entities (40% reduction)
- **Phase 2**: Implement embedding-based validation (60% reduction)
- **Benefits**: Simplified queries, reduced storage, better performance
- See: `technical-analysis/dynamodb-entity-consolidation.md`

### **Agentic Architecture Refactor** 📋 SPECIFIED
**Long-term Vision (12-16 weeks)**
- RAG-based intelligent labeling
- Standardized tool registry
- Real-time streaming capabilities
- Advanced observability
- See: `agentic-refactor/` directory

## 🛠 Implementation Guidelines

### **Getting Started with Line Item Enhancement**
```bash
# Create implementation branch
git checkout -b feat/line-item-enhancement-week1

# Key files to modify:
# 1. receipt_label/receipt_label/data/gpt.py (remove stub)
# 2. receipt_label/receipt_label/processors/line_item_processor.py (enhance patterns)
# 3. Add tests for new pattern matching capabilities
```

### **Code Quality Standards**
- ✅ Type hints for all public APIs
- ✅ Comprehensive docstrings
- ✅ 90%+ test coverage
- ✅ Performance benchmarks
- ✅ Error handling and logging

### **Testing Requirements**
- **Unit tests**: Individual pattern matching functions
- **Integration tests**: End-to-end line item extraction
- **Performance tests**: Measure accuracy improvements
- **Validation tests**: Financial validation logic

## 📈 Success Metrics

### **Line Item Processing**
- **Baseline**: 60-70% accuracy (current)
- **Week 1 Target**: 80-85% accuracy
- **Week 2 Target**: 85-90% accuracy
- **Week 3 Target**: 90-95% accuracy
- **Final Target**: 95%+ with financial validation

### **DynamoDB Optimization**
- **Entity Reduction**: 7 → 3 entities (60% reduction)
- **Query Performance**: 50% faster validation
- **Storage Costs**: 40-60% reduction

## 🔧 Tools and Patterns

### **Development Tools**
- **Git worktrees**: Parallel development
- **pytest**: Comprehensive testing framework
- **mypy**: Static type checking
- **GitHub Actions**: CI/CD pipeline

### **Architecture Patterns**
- **Pattern Matching**: Enhanced regex and spatial analysis
- **Retrieval Augmented Generation**: Pinecone for edge cases
- **Entity Consolidation**: Simplified DynamoDB schema
- **Financial Validation**: Comprehensive accuracy checking

## 📚 Additional Resources

- [Receipt Label Package](../receipt_label/README.md)
- [Receipt Dynamo Package](../receipt_dynamo/README.md)
- [Claude AI Workflow Guidelines](../CLAUDE.md)
- [Project README](../README.md)

---

## 🚀 Quick Start

To begin implementing line item enhancement:

```bash
# Week 1: Fix GPT stub and enhance patterns
git checkout -b feat/line-item-enhancement-week1
cd receipt_label

# Find the broken stub
grep -n "stub implementation" receipt_label/data/gpt.py

# Run existing tests to establish baseline
pytest tests/

# Start implementation...
```

**Last Updated**: 2025-07-10
**Next Review**: After Week 1 implementation complete
