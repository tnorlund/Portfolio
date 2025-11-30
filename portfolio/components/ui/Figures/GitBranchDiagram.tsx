import React from "react";
import styles from "./GitBranchDiagram.module.css";

interface GitBranchDiagramProps {
  /** Optional prop for future enhancements */
  animated?: boolean;
}

const GitBranchDiagram: React.FC<GitBranchDiagramProps> = ({ animated = false }) => {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "center",
        marginTop: "1em",
        marginBottom: "1em",
      }}
    >
      <div>
        {/* Vertical layout (mobile) */}
        <div className={styles["mobile-only"]}>
          <div>
            <svg height="400" width="300" viewBox="0 0 300 400">
              <defs>
                <style>
                  {`
                    .git-branch-line {
                      fill: none;
                      stroke: var(--text-color);
                      stroke-miterlimit: 10;
                      stroke-width: 7px;
                    }
                  `}
                </style>
              </defs>

              {/* Git branch lines */}
              <line
                className="git-branch-line"
                x1="150"
                y1="112.5"
                x2="150"
                y2="200"
              />
              <path
                className="git-branch-line"
                d="M150,25v87.5"
              />
              <path
                className="git-branch-line"
                d="M237.81,112.5s0-87.5-87.81-87.5"
              />
              <line
                className="git-branch-line"
                x1="237.81"
                y1="200"
                x2="237.81"
                y2="112.5"
              />
              <path
                className="git-branch-line"
                d="M237.81,200s0,87.5-87.81,87.5"
              />
              <line
                className="git-branch-line"
                x1="150"
                y1="199.73"
                x2="150"
                y2="287.23"
              />
              <path
                className="git-branch-line"
                d="M63.59,375s0-87.5,87.81-87.5"
              />

              {/* Git branch nodes (commits) - rendered after lines so they appear on top */}
              {/* All main branch commits - blue */}
              <circle fill="var(--color-blue)" cx="150" cy="25" r="25" />
              <circle fill="var(--color-blue)" cx="150" cy="112.5" r="25" />
              <circle fill="var(--color-blue)" cx="150" cy="200" r="25" />
              <circle fill="var(--color-blue)" cx="150" cy="287.5" r="25" />
              {/* Side branch commits - red */}
              <circle fill="var(--color-red)" cx="237.81" cy="112.5" r="25" />
              <circle fill="var(--color-red)" cx="237.81" cy="200" r="25" />
              {/* Other commit - green */}
              <circle fill="var(--color-green)" cx="62.19" cy="375" r="25" />
            </svg>
          </div>
        </div>

        {/* Horizontal layout (desktop) */}
        <div className={styles["desktop-only"]}>
          <div>
            <svg height="250" width="400" viewBox="0 0 400 250">
              <defs>
                <style>
                  {`
                    .git-branch-line {
                      fill: none;
                      stroke: var(--text-color);
                      stroke-miterlimit: 10;
                      stroke-width: 7px;
                    }
                  `}
                </style>
              </defs>

              {/* Git branch lines */}
              <line
                className="git-branch-line"
                x1="112.5"
                y1="125"
                x2="200"
                y2="125"
              />
              <path
                className="git-branch-line"
                d="M25,125h87.5"
              />
              <path
                className="git-branch-line"
                d="M112.5,37.19s-87.5,0-87.5,87.81"
              />
              <line
                className="git-branch-line"
                x1="200"
                y1="37.19"
                x2="112.5"
                y2="37.19"
              />
              <path
                className="git-branch-line"
                d="M200,37.19s87.5,0,87.5,87.81"
              />
              <line
                className="git-branch-line"
                x1="199.73"
                y1="125"
                x2="287.23"
                y2="125"
              />
              <path
                className="git-branch-line"
                d="M375,211.41s-87.5,0-87.5-87.81"
              />

              {/* Git branch nodes (commits) - rendered after lines so they appear on top */}
              {/* All main branch commits - blue */}
              <circle fill="var(--color-blue)" cx="25" cy="125" r="25" />
              <circle fill="var(--color-blue)" cx="112.5" cy="125" r="25" />
              <circle fill="var(--color-blue)" cx="200" cy="125" r="25" />
              <circle fill="var(--color-blue)" cx="287.5" cy="125" r="25" />
              {/* Side branch commits - red */}
              <circle fill="var(--color-red)" cx="112.5" cy="37.19" r="25" />
              <circle fill="var(--color-red)" cx="200" cy="37.19" r="25" />
              {/* Other commit - green */}
              <circle fill="var(--color-green)" cx="375" cy="212.81" r="25" />
            </svg>
          </div>
        </div>

        {/* Color legend */}
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            columnGap: "24px",
            marginTop: "1rem",
            marginBottom: "1rem",
            justifyContent: "center",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <div
              style={{
                width: "16px",
                height: "16px",
                borderRadius: "50%",
                backgroundColor: "var(--color-blue)",
              }}
            />
            <span>Main</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <div
              style={{
                width: "16px",
                height: "16px",
                borderRadius: "50%",
                backgroundColor: "var(--color-red)",
              }}
            />
            <span>Bug Fix</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <div
              style={{
                width: "16px",
                height: "16px",
                borderRadius: "50%",
                backgroundColor: "var(--color-green)",
              }}
            />
            <span>Feature</span>
          </div>
        </div>
      </div>
    </div>
  );
};

export default GitBranchDiagram;
