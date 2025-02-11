import React, { useCallback, useEffect, useRef, useState } from "react";
import { ReceiptDetail, ReceiptWord, ReceiptWordTag } from "../interfaces";
import ReceiptBoundingBox from "../ReceiptBoundingBox";
import AddressGroup from "./AddressGroup";
import TagGroup from './TagGroup';

// Types
interface WordWithTag extends ReceiptWord {
  tag?: ReceiptWordTag;
}

interface GroupedWords {
  words: ReceiptWord[];
  tag: ReceiptWordTag;
}

interface SelectedReceiptProps {
  selectedReceipt: string | null;
  receiptDetails: { [key: string]: ReceiptDetail };
  cdn_base_url: string;
}

// Constants
const SELECTABLE_TAGS = [
  "store_name",
  "date",
  "time",
  "phone_number",
  "address",
  "line_item_name",
  "line_item_price",
  "total_amount",
  "taxes",
] as const;

// Styles
const styles = {
  container: {
    display: "flex" as const,
    flexDirection: "column" as const,
    width: "100%",
  },
  mainContainer: {
    display: "flex",
    position: "relative" as const,
  },
  leftPanel: {
    width: "50%",
    display: "flex",
    justifyContent: "center",
    alignItems: "center",
  },
  rightPanel: {
    width: "50%",
    position: "absolute" as const,
    right: 0,
    top: 0,
    bottom: 0,
    overflowY: "auto" as const,
  },
  rightPanelContent: {
    padding: "20px",
    display: "flex",
    flexDirection: "column" as const,
    gap: "8px",
  },
  header: {
    display: "flex",
    justifyContent: "space-between",
    marginBottom: "0.5rem",
    alignItems: "center",
  },
  headerTitle: {
    fontSize: "2rem",
    color: "white",
    fontWeight: "bold",
  },
  addButton: (isAddingTag: boolean) => ({
    width: "32px",
    height: "32px",
    borderRadius: "50%",
    backgroundColor: isAddingTag ? "var(--background-color)" : "var(--text-color)",
    border: isAddingTag ? "2px solid var(--text-color)" : "none",
    color: isAddingTag ? "var(--text-color)" : "var(--background-color)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    cursor: "pointer",
    fontSize: "1.5rem",
    lineHeight: "1",
    paddingBottom: "3px",
    fontWeight: "bold",
  }),
};

// Component
const SelectedReceipt: React.FC<SelectedReceiptProps> = ({
  selectedReceipt,
  receiptDetails,
  cdn_base_url,
}) => {
  const [selectedWord, setSelectedWord] = useState<ReceiptWord | null>(null);
  const [openTagMenu, setOpenTagMenu] = useState<{
    groupIndex: number;
    wordIndex: number;
  } | null>(null);
  const [addingTagType, setAddingTagType] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const onWordSelect = useCallback((word: ReceiptWord) => {
    setSelectedWord(word);
  }, []);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setOpenTagMenu(null);
      }
    };

    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const getTagGroups = (detail: ReceiptDetail, tagType: string): GroupedWords => {
    // Get all tags of the specified type
    const tags = detail.word_tags.filter((tag) => {
      return tag.tag.trim().toLowerCase() === tagType.toLowerCase();
    });

    // Get all words that have this tag
    const taggedWords = detail.words.filter(word => 
      tags.some(tag => 
        tag.word_id === word.word_id &&
        tag.line_id === word.line_id &&
        tag.receipt_id === word.receipt_id &&
        tag.image_id === word.image_id
      )
    );

    // Sort words by their position (top to bottom, left to right)
    const sortedWords = [...taggedWords].sort((a, b) => {
      if (Math.abs(a.bounding_box.y - b.bounding_box.y) < 10) {
        return a.bounding_box.x - b.bounding_box.x;
      }
      return a.bounding_box.y - b.bounding_box.y;
    });

    // Return all words with their tag
    return {
      words: sortedWords,
      tag: tags[0] // Use the first tag since they're all the same type
    };
  };

  const renderStars = (confidence: number | null) => {
    if (confidence === null) return null;
    const stars = '★'.repeat(confidence) + '☆'.repeat(5 - confidence);
    return (
      <span style={{ 
        color: 'var(--text-color)', // Amber-400 for gold stars
        marginLeft: '8px',
        fontSize: '0.875rem'
      }}>
        {stars}
      </span>
    );
  };

  const renderHumanValidation = (validated: boolean | null) => {
    if (validated === null) {
      return (
        <span style={{ 
          width: '16px',
          height: '16px',
          borderRadius: '4px',
          border: '2px solid var(--text-color)',
          display: 'inline-block'
        }} />
      );
    }
    return (
      <span style={{ 
        color: validated ? '#4ade80' : '#ef4444',
        fontSize: '1rem'
      }}>
        {validated ? '✓' : '✗'}
      </span>
    );
  };

  const handleBoundingBoxClick = (word: ReceiptWord) => {
    if (addingTagType) {
      console.log('Adding tag:', addingTagType, 'to word:', word);
      setSelectedWord(word);
      setAddingTagType(null);
    }
  };

  const renderRightPanel = () => {
    if (!selectedReceipt || !receiptDetails[selectedReceipt]) return null;

    let groupIndex = 0;

    return (
      <div style={styles.rightPanelContent}>
        {SELECTABLE_TAGS.map(tagType => {
          const group = getTagGroups(receiptDetails[selectedReceipt], tagType);
          if (group.words.length === 0) return null;

          return (
            <TagGroup
              key={tagType}
              words={group.words}
              tag={group.tag}
              tagType={tagType}
              selectedWord={selectedWord}
              onWordSelect={onWordSelect}
              groupIndex={groupIndex++}
              openTagMenu={openTagMenu}
              setOpenTagMenu={setOpenTagMenu}
              menuRef={menuRef}
              isAddingTag={addingTagType === tagType}
              onAddTagClick={() => {
                setAddingTagType(addingTagType === tagType ? null : tagType);
              }}
            />
          );
        })}
      </div>
    );
  };

  return (
    <div style={styles.container}>
      <h1 className="text-2xl font-bold p-4">Receipt Validation</h1>

      <div style={styles.mainContainer}>
        <div style={styles.leftPanel}>
          {selectedReceipt && receiptDetails[selectedReceipt] ? (
            <ReceiptBoundingBox
              detail={receiptDetails[selectedReceipt]}
              width={450}
              isSelected={true}
              cdn_base_url={cdn_base_url}
              highlightedWords={selectedWord ? [selectedWord] : []}
              onClick={addingTagType ? handleBoundingBoxClick : undefined}
              isAddingTag={!!addingTagType}
            />
          ) : (
            <div>Select a receipt</div>
          )}
        </div>

        <div style={styles.rightPanel}>{renderRightPanel()}</div>
      </div>
    </div>
  );
};

export default SelectedReceipt;
