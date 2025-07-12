"""
Shadow testing system for ground-truth validation.

This module implements shadow testing to validate the effectiveness of the agent-based
decision engine by running a percentage of receipts through full GPT processing
even when the agent decides SKIP or BATCH.
"""

import logging
import random
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class ShadowTestMode(Enum):
    """Shadow testing modes."""
    DISABLED = "disabled"
    SKIP_VALIDATION = "skip_validation"      # Shadow test SKIP decisions only
    BATCH_VALIDATION = "batch_validation"    # Shadow test BATCH decisions only
    FULL_VALIDATION = "full_validation"      # Shadow test all decisions


@dataclass
class ShadowTestResult:
    """Results from shadow testing validation."""
    receipt_id: str
    image_id: str
    merchant_name: Optional[str]
    
    # Original agent decision
    agent_decision: str
    agent_labels: Dict[str, str]
    agent_confidence: Dict[str, float]
    
    # Shadow GPT results
    shadow_labels: Dict[str, str]
    shadow_confidence: Dict[str, float]
    shadow_cost_usd: float
    shadow_tokens: int
    
    # Validation metrics
    false_skip_rate: float          # How often agent SKIPs when GPT finds more
    missed_essential_fields: List[str]
    missed_secondary_fields: List[str]
    precision_score: float          # Agent labels accuracy vs GPT
    recall_score: float            # Agent labels completeness vs GPT
    
    # Quality assessment
    validation_passed: bool
    validation_issues: List[str]
    
    # Timing
    timestamp: datetime


class ShadowTestManager:
    """Manages shadow testing for agent decision validation."""
    
    def __init__(
        self,
        mode: ShadowTestMode = ShadowTestMode.SKIP_VALIDATION,
        sampling_rate: float = 0.05,  # 5% default sampling
        essential_fields: Optional[Set[str]] = None,
        false_skip_threshold: float = 0.005  # 0.5% false skip threshold
    ):
        """
        Initialize shadow test manager.
        
        Args:
            mode: Shadow testing mode
            sampling_rate: Percentage of receipts to shadow test (0.0-1.0)
            essential_fields: Set of essential field names for validation
            false_skip_threshold: Maximum acceptable false skip rate
        """
        self.mode = mode
        self.sampling_rate = sampling_rate
        self.essential_fields = essential_fields or {
            "MERCHANT_NAME", "DATE", "GRAND_TOTAL"
        }
        self.false_skip_threshold = false_skip_threshold
        
        # Validation results storage
        self.validation_results: List[ShadowTestResult] = []
        
        # Statistics tracking
        self.stats = {
            "total_receipts": 0,
            "shadow_tests_run": 0,
            "false_skips_detected": 0,
            "validation_failures": 0,
            "total_shadow_cost": 0.0,
            "cost_savings_validated": 0.0
        }
    
    def should_shadow_test(
        self, 
        agent_decision: str, 
        receipt_id: str
    ) -> bool:
        """
        Determine if this receipt should be shadow tested.
        
        Args:
            agent_decision: The agent's decision (SKIP/BATCH/REQUIRED)
            receipt_id: Receipt identifier for deterministic sampling
            
        Returns:
            True if should shadow test, False otherwise
        """
        if self.mode == ShadowTestMode.DISABLED:
            return False
        
        # Check if decision type should be tested
        if (self.mode == ShadowTestMode.SKIP_VALIDATION and 
            agent_decision != "SKIP"):
            return False
        
        if (self.mode == ShadowTestMode.BATCH_VALIDATION and 
            agent_decision != "BATCH"):
            return False
        
        # Use deterministic sampling based on receipt_id
        # This ensures consistent behavior across multiple runs
        random.seed(hash(receipt_id) % 2**32)
        should_test = random.random() < self.sampling_rate
        random.seed()  # Reset to system random
        
        return should_test
    
    def run_shadow_test(
        self,
        receipt_id: str,
        image_id: str,
        words: List[Dict],
        agent_decision: str,
        agent_labels: Dict[str, str],
        agent_confidence: Optional[Dict[str, float]] = None,
        merchant_name: Optional[str] = None,
        gpt_processor=None
    ) -> Optional[ShadowTestResult]:
        """
        Run shadow test by processing receipt through full GPT.
        
        Args:
            receipt_id: Receipt identifier
            image_id: Image identifier
            words: OCR words from receipt
            agent_decision: Agent's original decision
            agent_labels: Labels found by agent/patterns
            agent_confidence: Confidence scores for agent labels
            merchant_name: Merchant name if known
            gpt_processor: GPT processor for shadow testing
            
        Returns:
            ShadowTestResult if test was run, None otherwise
        """
        if not self.should_shadow_test(agent_decision, receipt_id):
            return None
        
        if gpt_processor is None:
            logger.warning(f"Shadow test requested but no GPT processor available for {receipt_id}")
            return None
        
        self.stats["total_receipts"] += 1
        
        try:
            # Run full GPT processing
            shadow_start = datetime.now()
            shadow_result = gpt_processor.process_receipt(
                words=words,
                context={"shadow_test": True, "original_decision": agent_decision}
            )
            
            shadow_labels = shadow_result.get("labels", {})
            shadow_tokens = shadow_result.get("tokens_used", 0)
            shadow_cost = shadow_result.get("estimated_cost", 0.0)
            
            # Calculate validation metrics
            validation_result = self._validate_agent_decision(
                receipt_id=receipt_id,
                agent_decision=agent_decision,
                agent_labels=agent_labels,
                shadow_labels=shadow_labels,
                agent_confidence=agent_confidence or {},
                shadow_cost=shadow_cost,
                shadow_tokens=shadow_tokens,
                merchant_name=merchant_name
            )
            
            self.stats["shadow_tests_run"] += 1
            self.stats["total_shadow_cost"] += shadow_cost
            
            if validation_result.false_skip_rate > self.false_skip_threshold:
                self.stats["false_skips_detected"] += 1
            
            if not validation_result.validation_passed:
                self.stats["validation_failures"] += 1
            
            self.validation_results.append(validation_result)
            
            logger.info(
                f"Shadow test completed for {receipt_id}: "
                f"decision={agent_decision}, "
                f"false_skip_rate={validation_result.false_skip_rate:.3f}, "
                f"validation_passed={validation_result.validation_passed}"
            )
            
            return validation_result
            
        except Exception as e:
            logger.error(f"Shadow test failed for {receipt_id}: {e}")
            return None
    
    def _validate_agent_decision(
        self,
        receipt_id: str,
        agent_decision: str,
        agent_labels: Dict[str, str],
        shadow_labels: Dict[str, str],
        agent_confidence: Dict[str, float],
        shadow_cost: float,
        shadow_tokens: int,
        merchant_name: Optional[str] = None
    ) -> ShadowTestResult:
        """
        Validate agent decision against shadow GPT results.
        
        Returns:
            ShadowTestResult with validation metrics
        """
        # Calculate field-level precision and recall
        agent_fields = set(agent_labels.keys())
        shadow_fields = set(shadow_labels.keys())
        
        # True positives: fields found by both
        true_positives = agent_fields & shadow_fields
        
        # False positives: fields found by agent but not GPT
        false_positives = agent_fields - shadow_fields
        
        # False negatives: fields found by GPT but not agent
        false_negatives = shadow_fields - agent_fields
        
        # Calculate precision and recall
        precision = len(true_positives) / len(agent_fields) if agent_fields else 1.0
        recall = len(true_positives) / len(shadow_fields) if shadow_fields else 1.0
        
        # Check for missed essential fields
        missed_essential = []
        missed_secondary = []
        
        for field in false_negatives:
            if field in self.essential_fields:
                missed_essential.append(field)
            else:
                missed_secondary.append(field)
        
        # Calculate false skip rate for SKIP decisions
        false_skip_rate = 0.0
        if agent_decision == "SKIP":
            # False skip if GPT found essential fields that agent missed
            if missed_essential:
                false_skip_rate = len(missed_essential) / len(self.essential_fields)
        
        # Determine if validation passed
        validation_issues = []
        validation_passed = True
        
        if false_skip_rate > self.false_skip_threshold:
            validation_issues.append(
                f"False skip rate {false_skip_rate:.3f} exceeds threshold {self.false_skip_threshold}"
            )
            validation_passed = False
        
        if missed_essential:
            validation_issues.append(f"Missed essential fields: {missed_essential}")
            validation_passed = False
        
        if precision < 0.8:  # 80% precision threshold
            validation_issues.append(f"Low precision: {precision:.3f}")
            validation_passed = False
        
        return ShadowTestResult(
            receipt_id=receipt_id,
            image_id="",  # Will be populated by caller
            merchant_name=merchant_name,
            agent_decision=agent_decision,
            agent_labels=agent_labels,
            agent_confidence=agent_confidence,
            shadow_labels=shadow_labels,
            shadow_confidence={},  # GPT doesn't provide confidence scores
            shadow_cost_usd=shadow_cost,
            shadow_tokens=shadow_tokens,
            false_skip_rate=false_skip_rate,
            missed_essential_fields=missed_essential,
            missed_secondary_fields=missed_secondary,
            precision_score=precision,
            recall_score=recall,
            validation_passed=validation_passed,
            validation_issues=validation_issues,
            timestamp=datetime.now()
        )
    
    def get_validation_summary(self) -> Dict[str, float]:
        """
        Get summary of validation results.
        
        Returns:
            Dictionary with validation statistics
        """
        if not self.validation_results:
            return {}
        
        # Aggregate statistics
        total_tests = len(self.validation_results)
        passed_tests = sum(1 for r in self.validation_results if r.validation_passed)
        
        avg_precision = sum(r.precision_score for r in self.validation_results) / total_tests
        avg_recall = sum(r.recall_score for r in self.validation_results) / total_tests
        avg_false_skip_rate = sum(r.false_skip_rate for r in self.validation_results) / total_tests
        
        false_skips = sum(1 for r in self.validation_results if r.false_skip_rate > self.false_skip_threshold)
        
        return {
            "total_shadow_tests": total_tests,
            "validation_pass_rate": passed_tests / total_tests,
            "average_precision": avg_precision,
            "average_recall": avg_recall,
            "average_false_skip_rate": avg_false_skip_rate,
            "false_skips_detected": false_skips,
            "false_skip_rate": false_skips / total_tests if total_tests > 0 else 0.0,
            "total_shadow_cost": self.stats["total_shadow_cost"],
            "cost_per_shadow_test": self.stats["total_shadow_cost"] / total_tests if total_tests > 0 else 0.0,
            **self.stats
        }
    
    def generate_validation_report(self) -> str:
        """
        Generate human-readable validation report.
        
        Returns:
            Formatted validation report
        """
        summary = self.get_validation_summary()
        
        if not summary:
            return "No shadow test results available."
        
        report = f"""
Shadow Testing Validation Report
================================

Test Configuration:
- Mode: {self.mode.value}
- Sampling Rate: {self.sampling_rate:.1%}
- False Skip Threshold: {self.false_skip_threshold:.1%}

Results Summary:
- Total Shadow Tests: {summary['total_shadow_tests']}
- Validation Pass Rate: {summary['validation_pass_rate']:.1%}
- Average Precision: {summary['average_precision']:.3f}
- Average Recall: {summary['average_recall']:.3f}
- False Skip Rate: {summary['false_skip_rate']:.3f}

Cost Analysis:
- Total Shadow Cost: ${summary['total_shadow_cost']:.4f}
- Cost per Shadow Test: ${summary['cost_per_shadow_test']:.4f}

Quality Metrics:
- False Skips Detected: {summary['false_skips_detected']}
- Average False Skip Rate: {summary['average_false_skip_rate']:.3f}
- Validation Failures: {summary['validation_failures']}

Recommendations:
"""
        
        # Add recommendations based on results
        if summary['false_skip_rate'] > self.false_skip_threshold:
            report += f"⚠️  False skip rate ({summary['false_skip_rate']:.3f}) exceeds threshold ({self.false_skip_threshold:.3f})\n"
            report += "   Consider adjusting agent decision thresholds or enhancing pattern detection\n"
        
        if summary['average_precision'] < 0.8:
            report += f"⚠️  Low average precision ({summary['average_precision']:.3f})\n"
            report += "   Review pattern detection accuracy and false positive rates\n"
        
        if summary['average_recall'] < 0.8:
            report += f"⚠️  Low average recall ({summary['average_recall']:.3f})\n"
            report += "   Consider enhancing pattern coverage or adjusting SKIP thresholds\n"
        
        if summary['validation_pass_rate'] >= 0.95:
            report += "✅ Validation performance is excellent\n"
        elif summary['validation_pass_rate'] >= 0.85:
            report += "✅ Validation performance is good\n"
        else:
            report += "⚠️  Low validation pass rate - review agent decision logic\n"
        
        return report