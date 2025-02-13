import React, { useState } from "react";
import { ReceiptWord, ReceiptWordTag } from "../interfaces";
import TagMenu from "./TagMenu";
import { postReceiptWordTag } from "../api";

interface WordItemProps {
  word: ReceiptWord;
  tag: ReceiptWordTag;
  isSelected: boolean;
  onWordClick: () => void;
  onTagClick: () => void;
  openTagMenu: boolean;
  menuRef: React.RefObject<HTMLDivElement>;
  onUpdateTag?: (updatedTag: ReceiptWordTag) => void;
  onRefresh?: () => void;
}

const WordItem: React.FC<WordItemProps> = ({
  word,
  tag,
  isSelected,
  onWordClick,
  onTagClick,
  openTagMenu,
  menuRef,
  onUpdateTag,
  onRefresh,
}) => {
  const [isUpdating, setIsUpdating] = useState(false);
  const tagRef = React.useRef<HTMLSpanElement>(null);

  const renderStars = (confidence: number | null) => {
    if (confidence === null) return null;
    const stars = "★".repeat(confidence) + "☆".repeat(5 - confidence);
    return (
      <span
        style={{
          color: "var(--text-color)",
          marginLeft: "8px",
          fontSize: "0.875rem",
        }}
      >
        {stars}
      </span>
    );
  };

  const renderHumanValidation = (validated: boolean | null) => {
    const commonStyles = {
      display: "flex",
      alignItems: "center",
      gap: "0px",
      height: "24px",
      borderRadius: "999px",
      overflow: "hidden",
      border: `2px solid ${
        validated === null
          ? "var(--text-color)"
          : validated
          ? "var(--color-green)"
          : "var(--color-red)"
      }`,
    };

    const buttonStyles = (isCheck: boolean) => ({
      width: "32px",
      height: "100%",
      cursor: "pointer",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      backgroundColor: validated === null 
        ? "rgba(var(--text-color-rgb), 0.1)"
        : validated === true && isCheck
        ? "var(--color-green)"
        : validated === false && !isCheck
        ? "var(--color-red)"
        : "rgba(var(--text-color-rgb), 0.1)",
      color: validated === null 
        ? "var(--text-color)"
        : (validated === true && isCheck) || (validated === false && !isCheck)
        ? "var(--color-background)"
        : "var(--text-color)",
    });

    return (
      <span style={commonStyles}>
        <span
          style={buttonStyles(true)}
          onClick={(e) => {
            e.stopPropagation();
            console.log("Checkmark clicked - setting human_validated to true");
            if (!isUpdating) updateHumanValidation(true);
          }}
        >
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke={validated === true ? "var(--background-color)" : "var(--text-color)"}
            strokeWidth="5"
          >
            <path d="M20 6L9 17L4 12" />
          </svg>
        </span>
        <span
          style={buttonStyles(false)}
          onClick={(e) => {
            e.stopPropagation();
            console.log("X clicked - setting human_validated to false");
            if (!isUpdating) updateHumanValidation(false);
          }}
        >
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke={validated === false ? "var(--background-color)" : "var(--text-color)"}
            strokeWidth="5"
          >
            <path d="M18 6L6 18M6 6l12 12" />
          </svg>
        </span>
      </span>
    );
  };

  const updateHumanValidation = async (newValue: boolean) => {
    if (isUpdating) return;

    try {
      setIsUpdating(true);

      const response = await postReceiptWordTag({
        selected_tag: tag,
        selected_word: word,
        action: "validate",
        validation_value: newValue,
      });

      if (onUpdateTag) {
        onUpdateTag(response.updated.receipt_word_tag);
      }
    } catch (error) {
      console.error("Failed to update human validation:", error);
    } finally {
      setIsUpdating(false);
    }
  };

  // Determine tag color based on validation status
  const getTagColor = () => {
    if (tag.human_validated === false) {
      return {
        color: "var(--color-red)",
        border: "1px solid var(--color-red)",
        backgroundColor: "rgba(var(--color-red-rgb), 0.1)",
      };
    } else if (tag.human_validated === null) {
      // If human_validated is null, use tag.validated
      return tag.validated
        ? {
            color: "var(--color-green)",
            border: "1px solid var(--color-green)",
            backgroundColor: "rgba(var(--color-green-rgb), 0.1)",
          }
        : {
            color: "var(--color-red)",
            border: "1px solid var(--color-red)",
            backgroundColor: "rgba(var(--color-red-rgb), 0.1)",
          };
    }
    return {
      color: "var(--color-green)",
      border: "1px solid var(--color-green)",
      backgroundColor: "rgba(var(--color-green-rgb), 0.1)",
    };
  };

  return (
    <div
      style={{
        cursor: "pointer",
        padding: "4px",
        borderRadius: "2px",
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        position: "relative",
        outline: isSelected ? "2px solid var(--color-blue)" : "none",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          minWidth: "24px",
          justifyContent: "center",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            opacity: isUpdating ? 0.5 : 1,
            cursor: isUpdating ? "not-allowed" : "pointer",
          }}
        >
          {renderHumanValidation(tag.human_validated)}
        </div>
      </div>

      <div
        style={{
          flex: 1,
          color: "var(--text-color)",
          padding: "0 8px",
          cursor: "pointer",
        }}
        onClick={(e) => {
          e.stopPropagation();
          onWordClick();
        }}
      >
        {word.text}
      </div>

      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "8px",
          position: "relative",
        }}
      >
        {renderStars(tag.gpt_confidence)}
        <span
          ref={tagRef}
          onClick={onTagClick}
          style={{
            ...getTagColor(),
            padding: "2px 8px",
            borderRadius: "999px",
            fontSize: "0.875rem",
            cursor: "pointer",
          }}
        >
          {tag.tag
            .split("_")
            .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
            .join(" ")}
        </span>
        {openTagMenu && (
          <TagMenu
            menuRef={menuRef}
            onSelect={async (newTag) => {
              try {
                console.log("Changing tag:", {
                  selected_tag: tag,
                  selected_word: word,
                  action: "change_tag",
                  new_tag: newTag,
                });

                const response = await postReceiptWordTag({
                  selected_tag: tag,
                  selected_word: word,
                  action: "change_tag",
                  new_tag: newTag,
                });

                console.log("Tag change response:", response);

                if (onUpdateTag) {
                  onUpdateTag(response.updated.receipt_word_tag);
                }
              } catch (error) {
                console.error("Failed to update tag:", error);
              }
              onTagClick();
            }}
          />
        )}
      </div>
    </div>
  );
};

export default WordItem;
