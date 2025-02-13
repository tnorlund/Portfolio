import React, { useCallback, useEffect, useRef, useState } from "react";
import { ReceiptDetail, ReceiptWord, ReceiptWordTag } from "../interfaces";
import ReceiptBoundingBox from "./ReceiptBoundingBox";
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
  receiptContainer: {
    position: 'relative' as const,
    minHeight: '100vh', // Ensure container is at least full viewport height
  },
  floatingAddButton: {
    position: 'sticky' as const,
    left: '16px',
    top: '37vh',
    transform: 'translateY(-50%)',
    width: '50px',
    height: '50px',
    borderRadius: '50%',
    backgroundColor: 'var(--background-color)',
    color: 'var(--text-color)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    cursor: 'pointer',
    fontSize: '2rem',
    lineHeight: '32px',
    padding: 0,
    fontWeight: 'bold',
    userSelect: 'none' as const,
    border: '2px solid var(--text-color)',
    zIndex: 10,
  },
  floatingAddButtonText: {
    marginTop: '-4px',
    display: 'block',
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

  // Add new state for floating button menu
  const [showFloatingMenu, setShowFloatingMenu] = useState(false);
  const floatingButtonRef = useRef<HTMLButtonElement>(null);

  const onWordSelect = useCallback((word: ReceiptWord) => {
    setSelectedWord(word);
  }, []);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setOpenTagMenu(null);
        setShowFloatingMenu(false);
      }
    };

    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const getTagGroups = (detail: ReceiptDetail, tagType: string): GroupedWords => {
    console.log(`Getting tag groups for ${tagType}`, detail);
    
    const tags = detail.word_tags.filter((tag) => {
      const matches = tag.tag.trim().toLowerCase() === tagType.toLowerCase();
      console.log(`Checking tag ${tag.tag} against ${tagType}: ${matches}`);
      return matches;
    });

    console.log(`Found ${tags.length} tags for ${tagType}`);

    const taggedWords = detail.words.filter(word => {
      const matches = tags.some(tag => 
        tag.word_id === word.word_id &&
        tag.line_id === word.line_id &&
        tag.receipt_id === word.receipt_id &&
        tag.image_id === word.image_id
      );
      if (matches) {
        console.log(`Found matching word for ${tagType}:`, word);
      }
      return matches;
    });

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

  const handleSelectionComplete = (words: ReceiptWord[], updatedDetail: Partial<ReceiptDetail>) => {
    setSelectedWords(words);
    setAddingTagType(null);
    if (selectedReceipt && receiptDetails[selectedReceipt]) {
      onReceiptUpdate(selectedReceipt, {
        ...receiptDetails[selectedReceipt],
        words: updatedDetail.words || receiptDetails[selectedReceipt].words,
        word_tags: updatedDetail.word_tags || receiptDetails[selectedReceipt].word_tags
      });
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
      
      const [imageId, receiptIdStr] = selectedReceipt.split('_');
      const receiptId = parseInt(receiptIdStr, 10);
      
      if (isNaN(receiptId)) {
        console.error('Invalid receipt ID:', receiptIdStr);
        return;
      }

      console.log('Fetching details for:', { imageId, receiptId });
      const details = await fetchReceiptDetail(imageId, receiptId);
      console.log('Received details:', details);
      
      if (details) {
        const updatedDetails = {
          receipt: details.receipt,
          words: details.words,
          word_tags: details.tags
        };
        console.log('Updating receipt with:', updatedDetails);
        onReceiptUpdate(selectedReceipt, updatedDetails);
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

    const container = document.querySelector('.receipt-container');
    if (!container) return;

    const rect = container.getBoundingClientRect();
    setMenuPosition({ 
      x: event.clientX - rect.left + 10,
      y: event.clientY - rect.top
    });
    setOpenTagMenu({ groupIndex: -1, wordIndex: -1 });
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

  // Update the floating button click handler
  const handleFloatingButtonClick = () => {
    if (addingTagType) {
      // If we're in selection mode, exit it
      setAddingTagType(null);
      return;
    }

    // Otherwise show the tag menu as before
    if (!floatingButtonRef.current) return;
    
    const buttonRect = floatingButtonRef.current.getBoundingClientRect();
    setMenuPosition({
      x: buttonRect.right + 10,
      y: buttonRect.top
    });
    
    setShowFloatingMenu(true);
  };

  const handleTagButtonClick = (tagType: string) => {
    setAddingTagType(current => current === tagType ? null : tagType);
  };

  const renderRightPanel = () => {
    if (!selectedReceipt || !receiptDetails[selectedReceipt]) {
      console.log('No receipt selected or no details available');
      return null;
    }

    console.log('Receipt Details:', receiptDetails[selectedReceipt]);
    console.log('Word Tags:', receiptDetails[selectedReceipt].word_tags);

    let groupIndex = 0;

    return (
      <div style={styles.rightPanelContent}>
        {SELECTABLE_TAGS.map(tagType => {
          const group = getTagGroups(receiptDetails[selectedReceipt], tagType);
          console.log(`Tag group for ${tagType}:`, group);
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
              onAddTagClick={() => handleTagButtonClick(tagType)}
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
              <div className="receipt-container" style={styles.receiptContainer}>
                <button 
                  ref={floatingButtonRef}
                  className="floating-add-button"
                  style={styles.floatingAddButton}
                  onClick={handleFloatingButtonClick}
                >
                  <span style={styles.floatingAddButtonText}>+</span>
                </button>
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
                  onClearSelection={() => {
                    setSelectedWord(null);
                    setSelectedWords([]);
                  }}
                />
                {((openTagMenu && openTagMenu.groupIndex === -1) || showFloatingMenu) && menuPosition && !addingTagType && (
                  <TagMenu
                    menuRef={menuRef}
                    style={{
                      position: showFloatingMenu ? 'fixed' : 'absolute',
                      left: `${menuPosition.x}px`,
                      top: `${menuPosition.y}px`,
                      zIndex: 1000
                    }}
                    onSelect={async (newTag) => {
                      setShowFloatingMenu(false);
                      
                      // Only set addingTagType if we're using the floating menu
                      if (showFloatingMenu) {
                        setAddingTagType(newTag);
                        return;
                      }
                      
                      // Handle individual word tag updates
                      if (selectedWord) {
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
                            const response = await postReceiptWordTag(payload);
                            handleTagUpdate(response.updated.receipt_word_tag);
                          } else if (matchingTag) {
                            const payload = {
                              selected_tag: matchingTag,
                              selected_word: selectedWord,
                              action: "change_tag" as const,
                              new_tag: newTag
                            };
                            const response = await postReceiptWordTag(payload);
                            handleTagUpdate(response.updated.receipt_word_tag);
                          }
                        } catch (error) {
                          console.error('Failed to update tag:', error);
                        }
                      }
                      
                      setOpenTagMenu(null);
                      setMenuPosition(null);
                    }}
                  />
                )}
              </div>
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
