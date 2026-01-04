import React from "react";
import { animated, useSpring } from "@react-spring/web";
import { PipelineStage } from "./mockData";
import styles from "./LLMEvaluatorVisualization.module.css";

interface PipelineFlowProps {
  stages: PipelineStage[];
  currentStageIndex: number;
}

const STAGE_ICONS: Record<string, string> = {
  input: "📥",
  currency: "💰",
  metadata: "📋",
  financial: "🧮",
  output: "✅",
};

const PipelineFlow: React.FC<PipelineFlowProps> = ({
  stages,
  currentStageIndex,
}) => {
  return (
    <div className={styles.pipelineContainer}>
      <h4 className={styles.pipelineTitle}>Evaluation Pipeline</h4>
      <div className={styles.pipelineFlow}>
        {stages.map((stage, index) => (
          <React.Fragment key={stage.id}>
            <PipelineStageBox
              stage={stage}
              index={index}
              isActive={index === currentStageIndex}
              isComplete={index < currentStageIndex}
            />
            {index < stages.length - 1 && (
              <div
                className={`${styles.pipelineConnector} ${
                  index < currentStageIndex ? styles.pipelineConnectorActive : ""
                }`}
              />
            )}
          </React.Fragment>
        ))}
      </div>
    </div>
  );
};

interface PipelineStageBoxProps {
  stage: PipelineStage;
  index: number;
  isActive: boolean;
  isComplete: boolean;
}

const PipelineStageBox: React.FC<PipelineStageBoxProps> = ({
  stage,
  index,
  isActive,
  isComplete,
}) => {
  const spring = useSpring({
    from: { opacity: 0, transform: "scale(0.8)" },
    to: { opacity: 1, transform: "scale(1)" },
    delay: index * 150,
    config: { tension: 200, friction: 20 },
  });

  return (
    <animated.div
      className={`${styles.pipelineStage} ${
        isActive ? styles.pipelineStageProcessing : ""
      } ${isComplete ? styles.pipelineStageComplete : ""}`}
      style={spring}
    >
      <span className={styles.pipelineStageIcon}>
        {isComplete ? "✓" : STAGE_ICONS[stage.id] || "⚙️"}
      </span>
      <span className={styles.pipelineStageName}>{stage.name}</span>
    </animated.div>
  );
};

export default PipelineFlow;
