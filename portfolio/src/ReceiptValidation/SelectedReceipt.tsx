import React, { useCallback, useEffect, useRef, useState } from "react";
import { ReceiptDetail, ReceiptWord, ReceiptWordTag } from "../interfaces";
import ReceiptBoundingBox from "../ReceiptBoundingBox";
import TagGroup from './TagGroup';
import { fetchReceiptDetail, postReceiptWordTag } from "../api";
import TagMenu from './TagMenu';

// Types
interface GroupedWords {
  words: ReceiptWord[];
  tag: ReceiptWordTag[];
}

interface SelectedReceiptProps {
  selectedReceipt: string | null;
  receiptDetails: { [key: string]: ReceiptDetail };
  cdn_base_url: string;
  onReceiptUpdate: (receiptId: string, newDetails: ReceiptDetail) => void;
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
  validationHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '1rem',
    width: '100%',
  },
  validationTitle: {
    margin: 0,
    fontSize: '1.5rem',
    fontWeight: 'bold',
  },
  refreshButton: {
    background: 'none',
    border: 'none',
    cursor: 'pointer',
    padding: '0.5rem',
    borderRadius: '50%',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    color: 'var(--text-color)',
    transition: 'all 0.2s ease',
  },
};

// Component
const SelectedReceipt: React.FC<SelectedReceiptProps> = ({
  selectedReceipt,
  receiptDetails,
  cdn_base_url,
  onReceiptUpdate,
}) => {
  const [selectedWord, setSelectedWord] = useState<ReceiptWord | null>(null);
  const [openTagMenu, setOpenTagMenu] = useState<{
    groupIndex: number;
    wordIndex: number;
  } | null>(null);
  const [addingTagType, setAddingTagType] = useState<string | null>(null);
  const [selectedWords, setSelectedWords] = useState<ReceiptWord[]>([]);
  const [matchingTag, setMatchingTag] = useState<ReceiptWordTag | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [menuPosition, setMenuPosition] = useState<{ x: number; y: number } | null>(null);

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
    const tags = detail.word_tags.filter((tag) => 
      tag.tag.trim().toLowerCase() === tagType.toLowerCase()
    );

    const taggedWords = detail.words.filter(word => 
      tags.some(tag => 
        tag.word_id === word.word_id &&
        tag.line_id === word.line_id &&
        tag.receipt_id === word.receipt_id &&
        tag.image_id === word.image_id
      )
    );

    const sortedWords = [...taggedWords].sort((a, b) => {
      if (Math.abs(a.bounding_box.y - b.bounding_box.y) < 10) {
        return a.bounding_box.x - b.bounding_box.x;
      }
      return a.bounding_box.y - b.bounding_box.y;
    });

    return {
      words: sortedWords,
      tag: tags
    };
  };

  const handleBoundingBoxClick = (word: ReceiptWord) => {
    if (addingTagType) {
      // console.log('Adding tag:', addingTagType, 'to word:', word);
      setSelectedWord(word);
      setAddingTagType(null);
    }
  };

  const handleSelectionComplete = (words: ReceiptWord[]) => {
    if (addingTagType && words.length > 0) {
      // console.log('Adding tag:', addingTagType, 'to words:', words);
      setSelectedWords(words);
      setAddingTagType(null);
    }
  };

  // Add this new effect to clear selection mode if touch ends outside
  useEffect(() => {
    const handleTouchEnd = () => {
      if (addingTagType) {
        setAddingTagType(null);
      }
    };

    document.addEventListener('touchend', handleTouchEnd);
    return () => {
      document.removeEventListener('touchend', handleTouchEnd);
    };
  }, [addingTagType]);

  const handleRefresh = async () => {
    try {
      if (!selectedReceipt) return;
      const [imageId, receiptId] = selectedReceipt.split('#');
      const details = await fetchReceiptDetail(imageId, parseInt(receiptId));
      if (details) {
        onReceiptUpdate(selectedReceipt, {
          receipt: details.receipt,
          words: details.words,
          word_tags: details.tags
        });
      }
    } catch (error) {
      console.error('Failed to refresh receipt details:', error);
    }
  };

  const handleWordTagClick = (word: ReceiptWord, event: { clientX: number; clientY: number }) => {
    setSelectedWord(word);
    
    if (!selectedReceipt || !receiptDetails[selectedReceipt]) return;

    const existingTags = receiptDetails[selectedReceipt].word_tags.filter((tag: ReceiptWordTag) => 
        tag.word_id === word.word_id &&
        tag.line_id === word.line_id &&
        tag.receipt_id === word.receipt_id &&
        tag.image_id === word.image_id
    );
    
    setMatchingTag(existingTags[0] || null);
    setMenuPosition({ x: event.clientX, y: event.clientY });

    setOpenTagMenu({
        groupIndex: -1,
        wordIndex: -1
    });
  };

  const handleTagUpdate = (updatedTag: ReceiptWordTag) => {
    if (!selectedReceipt) return;
    
    const currentReceipt = receiptDetails[selectedReceipt];
    const updatedReceipt = {
      ...currentReceipt,
      words: currentReceipt.words.map(word => {
        if (word.image_id === updatedTag.image_id && 
            word.line_id === updatedTag.line_id && 
            word.word_id === updatedTag.word_id) {
          // Ensure we have a unique set of tags
          const otherTags = word.tags.filter(t => t !== updatedTag.tag);
          return {
            ...word,
            tags: Array.from(new Set([...otherTags, updatedTag.tag]))
          };
        }
        return word;
      }),
      // Remove any existing tags for this word and add the new one
      word_tags: [
        ...currentReceipt.word_tags.filter(tag => 
          !(tag.image_id === updatedTag.image_id && 
            tag.line_id === updatedTag.line_id && 
            tag.word_id === updatedTag.word_id)
        ),
        updatedTag
      ]
    };

    onReceiptUpdate(selectedReceipt, updatedReceipt);
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
              tags={group.tag}
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
              onUpdateTag={handleTagUpdate}
              onRefresh={handleRefresh}
            />
          );
        })}
      </div>
    );
  };

  return (
    <div style={styles.container}>
      <div style={styles.validationHeader}>
        <h1 style={styles.validationTitle}>Receipt Validation</h1>
        <button 
          style={styles.refreshButton}
          onClick={handleRefresh}
          title="Refresh"
        >
          <svg 
            xmlns="http://www.w3.org/2000/svg" 
            width="24" 
            height="24" 
            viewBox="0 0 24 24" 
            fill="none" 
            stroke="currentColor" 
            strokeWidth="2" 
            strokeLinecap="round" 
            strokeLinejoin="round"
          >
            <path d="M21.5 2v6h-6M2.5 22v-6h6M2 11.5a10 10 0 0 1 18.8-4.3M22 12.5a10 10 0 0 1-18.8 4.3"/>
          </svg>
        </button>
      </div>

      <div style={styles.mainContainer}>
        <div style={styles.leftPanel}>
          {selectedReceipt && receiptDetails[selectedReceipt] ? (
            <>
              <ReceiptBoundingBox
                detail={receiptDetails[selectedReceipt]}
                width={450}
                isSelected={true}
                cdn_base_url={cdn_base_url}
                highlightedWords={selectedWord ? [selectedWord] : selectedWords}
                onClick={addingTagType ? handleBoundingBoxClick : undefined}
                onSelectionComplete={addingTagType ? handleSelectionComplete : undefined}
                isAddingTag={!!addingTagType}
                addingTagType={addingTagType || undefined}
                onWordTagClick={!addingTagType ? handleWordTagClick : undefined}
              />
              {openTagMenu && openTagMenu.groupIndex === -1 && menuPosition && !addingTagType && (
                <TagMenu
                  menuRef={menuRef}
                  style={{
                    position: 'fixed',
                    left: menuPosition.x,
                    top: menuPosition.y,
                    zIndex: 1000
                  }}
                  onSelect={async (newTag) => {
                    if (!selectedWord) return;

                    try {
                        if (selectedWord.tags.length === 0) {
                            const payload = {
                                selected_tag: {
                                    image_id: selectedWord.image_id,
                                    receipt_id: selectedWord.receipt_id,
                                    line_id: selectedWord.line_id,
                                    word_id: selectedWord.word_id,
                                    tag: newTag,
                                    timestamp_added: new Date().toISOString(),
                                    validated: null,
                                    timestamp_validated: null,
                                    gpt_confidence: null,
                                    flag: null,
                                    revised_tag: null,
                                    human_validated: null,
                                    timestamp_human_validated: null
                                } as ReceiptWordTag,
                                selected_word: selectedWord,
                                action: "add_tag" as const,
                                new_tag: newTag
                            };
                            console.log('API Payload for word with no tags:', payload);
                            const response = await postReceiptWordTag(payload);
                            handleTagUpdate(response.updated.receipt_word_tag);
                        } else if (matchingTag) {
                            const payload = {
                                selected_tag: matchingTag,
                                selected_word: selectedWord,
                                action: "change_tag" as const,
                                new_tag: newTag
                            };
                            console.log('API Payload for word with existing tags:', payload);
                            const response = await postReceiptWordTag(payload);
                            handleTagUpdate(response.updated.receipt_word_tag);
                        }
                    } catch (error) {
                        console.error('Failed to update tag:', error);
                    }
                    
                    setOpenTagMenu(null);
                    setMenuPosition(null);
                  }}
                />
              )}
            </>
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
